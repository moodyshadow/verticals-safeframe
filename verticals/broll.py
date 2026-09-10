"""Gemini Imagen / local Stable Diffusion b-roll generation + Ken Burns animation."""

import base64
import os
import time
from pathlib import Path

import requests
from PIL import Image

from .config import BROLL_COUNT, VIDEO_WIDTH, VIDEO_HEIGHT, get_gemini_key, run_cmd
from .log import log
from .media_library import asset_path_for
from .retry import with_retry
from .stock_media import (
    fetch_topical_broll,
    fetch_topical_broll_video,
    fetch_last_resort_stock_photo,
    fetch_img2img_reference_photo,
)


def _broll_provider() -> str:
    """Which backend to use for b-roll images: 'local_sd' or 'gemini'."""
    return os.environ.get("BROLL_PROVIDER", "local_sd").lower()


# Both local SD instances this machine can run: the main A1111 webui
# (port 7860) and Forge (port 7861, pinned to the second GPU — see
# G:\AI\Forge\webui-user.bat's --device-id 0). Alternating between them
# per b-roll frame (see _pick_sd_endpoint) actually uses both GPUs across
# one video's b-roll generation instead of leaving one idle the whole run.
_SD_ENDPOINTS = ["http://127.0.0.1:7860", "http://127.0.0.1:7861"]

# Set for the duration of one frame's generation by generate_broll's loop
# (see _pick_sd_endpoint) so every call this module makes during that frame
# — txt2img, img2img, AnimateDiff, the available-scripts check — goes to
# the same chosen instance without threading a url parameter through every
# function signature. None outside of that loop (e.g. one-off scripts,
# _check_sd_webui_reachable) falls through to SD_WEBUI_URL/the default.
_endpoint_override: str | None = None

# Round-robin position, advanced each time _pick_sd_endpoint finds a
# healthy instance to hand out.
_rr_index = 0


def _sd_webui_url() -> str:
    if _endpoint_override:
        return _endpoint_override
    return os.environ.get("SD_WEBUI_URL", "http://127.0.0.1:7860").rstrip("/")


def _endpoint_healthy(url: str) -> bool:
    try:
        r = requests.get(f"{url}/sdapi/v1/sd-models", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _pick_sd_endpoint() -> str:
    """Round-robin across _SD_ENDPOINTS, skipping any that fail a quick
    health check, so one instance being down (or having no model loaded —
    the exact failure that took Forge out for real on 2026-09-05) degrades
    to using the other exclusively instead of failing the whole video.
    Raises only when neither instance is reachable.
    """
    global _rr_index
    if os.environ.get("SD_WEBUI_URL"):
        # An explicit override (used by one-off redo_*.py scripts to force
        # a specific instance) always wins — no rotation.
        return os.environ["SD_WEBUI_URL"].rstrip("/")

    for _ in range(len(_SD_ENDPOINTS)):
        candidate = _SD_ENDPOINTS[_rr_index % len(_SD_ENDPOINTS)]
        _rr_index += 1
        if _endpoint_healthy(candidate):
            return candidate
    raise RuntimeError(
        f"None of the configured SD instances are reachable: {_SD_ENDPOINTS}. "
        "Start at least one before producing."
    )


# RealVisXL_V4.0 (added 2026-09-06) lives in the shared checkpoint
# directory (C:\...\stable-diffusion-webui\models\Stable-diffusion, the
# same one Forge's --ckpt-dir points at) alongside the SD1.5 checkpoints.
# SDXL's motion-module support is too immature to bother with, so it's
# only ever used for still-image generation, never AnimateDiff — every
# sdxl=True call switches whichever endpoint it lands on over to
# RealVisXL, then switches back to the SD1.5 checkpoint before returning,
# so a later AnimateDiff call on that same endpoint isn't left on the
# wrong model. (Forge's /sdapi/* routes have been down since 2026-09-05 —
# _endpoint_healthy already treats it as unreachable and the round-robin
# falls through to the main webui exclusively — so in practice this all
# runs on port 7860 today, but the switch-and-restore works the same on
# either instance once/if Forge's API comes back.)
# Matched by a substring of model_name rather than the full title (which
# embeds a config hash that changes if the checkpoint file is ever
# re-downloaded).
SDXL_CHECKPOINT_HINT = "RealVisXL"
SD15_CHECKPOINT_HINT = "DreamShaper"


def _ensure_checkpoint(url: str, name_hint: str) -> None:
    """Make sure `url`'s currently loaded checkpoint matches name_hint,
    switching it via the API if not. No-ops (skipping the ~10-20s reload)
    when it's already loaded. Best-effort: any failure here just logs and
    falls through to generating with whatever's currently loaded rather
    than failing the whole b-roll frame over a checkpoint query hiccup.
    """
    try:
        r = requests.get(f"{url}/sdapi/v1/options", timeout=10)
        r.raise_for_status()
        current = r.json().get("sd_model_checkpoint", "")
        if name_hint.lower() in current.lower():
            return
        models = requests.get(f"{url}/sdapi/v1/sd-models", timeout=10).json()
        title = next(
            (m["title"] for m in models if name_hint.lower() in m.get("model_name", "").lower()),
            None,
        )
        if not title:
            log(f"No checkpoint matching '{name_hint}' found on {url} — using whatever's loaded.")
            return
        log(f"Switching {url} checkpoint to {title}...")
        requests.post(f"{url}/sdapi/v1/options", json={"sd_model_checkpoint": title}, timeout=120)
    except Exception as e:
        log(f"Checkpoint switch on {url} failed ({e}) — using whatever's loaded.")


_script_availability_cache: dict[str, set[str]] = {}


def _available_scripts() -> set[str]:
    """Which alwayson scripts the target webui actually has loaded —
    cached per webui URL for this process. Forge doesn't ship AnimateDiff
    by default (its Forge-compatible fork failed to load on this specific
    Forge build — a real version incompatibility, see PROJECT_STATUS.md),
    so a request that references "AnimateDiff" in alwayson_scripts —
    even just to explicitly disable it — gets rejected outright with a
    422 "Script not found". Only include a script in a request if it's
    confirmed present.
    """
    url = _sd_webui_url()
    if url not in _script_availability_cache:
        try:
            r = requests.get(f"{url}/sdapi/v1/scripts", timeout=10)
            r.raise_for_status()
            data = r.json()
            names = set(data.get("txt2img", [])) | set(data.get("img2img", []))
            _script_availability_cache[url] = {n.lower() for n in names}
        except Exception as e:
            log(f"Could not query available scripts at {url} ({e}) — "
                f"assuming none of the optional ones are present.")
            _script_availability_cache[url] = set()
    return _script_availability_cache[url]


def _disable_motion_scripts() -> dict:
    """alwayson_scripts payload that disables AnimateDiff/ControlNet where
    they exist, omitting any script the target webui doesn't actually have
    loaded (referencing a missing script name is a hard 422, not a no-op).
    """
    available = _available_scripts()
    payload = {}
    if "animatediff" in available:
        payload["AnimateDiff"] = {"args": [{"enable": False}]}
    if "controlnet" in available:
        payload["ControlNet"] = {"args": [{"enabled": False}]}
    return payload


def _post_sd_generate(url: str, body: dict, timeout: int = 90):
    """POST to the local SD webui's txt2img/img2img endpoint with a short
    fail-fast timeout instead of the naive 600s used before.

    A real single-image generation (no AnimateDiff/ControlNet) finishes in
    5-15s on this hardware — 90s is already generous headroom. This matters
    because of a real, observed failure mode: the AnimateDiff extension has
    intermittently ignored our explicit `enable: False` override (root cause
    not pinned down — looks like a race/stale-state bug inside the
    extension itself, not our request body, which was verified correct via
    an isolated test) and silently run its full 16-frame pipeline instead of
    one still image. That alone isn't fatal, except the runaway job then
    keeps grinding for hours *after* our client gives up, blocking the
    webui for every subsequent request in the whole pipeline run (this is
    what actually caused most of a night's failures, not any timeout
    number). So on a timeout here, best-effort interrupt the stuck job
    server-side before propagating the error, so the next call isn't stuck
    queued behind a multi-hour zombie generation.
    """
    try:
        return requests.post(url, json=body, timeout=timeout)
    except requests.exceptions.ReadTimeout:
        try:
            requests.post(f"{_sd_webui_url()}/sdapi/v1/interrupt", timeout=10)
        except Exception:
            pass
        raise


@with_retry(max_retries=2, base_delay=2.0)
def _generate_image_local_sd(prompt: str, output_path: Path, seed: int = -1, sdxl: bool = False):
    """Generate an image via a local AUTOMATIC1111/Forge webui (--api), $0 cost.

    sdxl=True switches whichever endpoint this frame's round-robin picked
    over to RealVisXL_V4.0 for this call, using SDXL's native ~2:3
    resolution and its recommended low-CFG/Karras sampling instead of the
    SD1.5 defaults, then switches that endpoint back to the SD1.5
    checkpoint before returning — so a later AnimateDiff call in this same
    video that lands on the same endpoint isn't left on the wrong model.
    """
    url_for_switch = _sd_webui_url()
    if sdxl:
        _ensure_checkpoint(url_for_switch, SDXL_CHECKPOINT_HINT)
    try:
        url = f"{_sd_webui_url()}/sdapi/v1/txt2img"
        body = {
            "prompt": prompt,
            # Always include safety terms, not just quality terms — b-roll may
            # still end up naming a real person despite the draft prompt's rule
            # against it (local models don't always follow instructions), and an
            # inappropriate/undignified depiction is worse than a quality flaw.
            "negative_prompt": (
                "blurry, low quality, distorted, watermark, text, logo, nsfw, nudity, "
                "extra limbs, fused limbs, mutated hands, bad anatomy, disfigured, "
                "warped geometry, garbled pattern"
            ),
            "width": 832 if sdxl else 576,
            "height": 1216 if sdxl else 1024,
            "steps": 30 if sdxl else 20,
            "cfg_scale": 5 if sdxl else 7,
            "sampler_name": "DPM++ SDE Karras" if sdxl else "DPM++ 2M",
            "batch_size": 1,
            "seed": seed,
            # AnimateDiff/ControlNet's own UI checkbox default (independent of
            # ui-config.json's saved value) is `enable: True` when the API call
            # omits alwayson_scripts entirely — every plain b-roll still image
            # was silently going through AnimateDiff's 16-frame video pipeline
            # instead, which is why generation was taking 15+ minutes per image
            # once VRAM pressure kicked in. Explicitly disabling both here is
            # required, not optional, even though the webui's own saved UI state
            # already shows them off. (Only for scripts the target webui
            # actually has loaded — see _disable_motion_scripts.)
            "alwayson_scripts": _disable_motion_scripts(),
        }
        r = _post_sd_generate(url, body)
        if r.status_code != 200:
            raise RuntimeError(
                f"Local SD webui {r.status_code}: {r.text[:200]} — is it running "
                f"with --api at {_sd_webui_url()}?"
            )
        data = r.json()
        images = data.get("images") or []
        if not images:
            raise RuntimeError("No image returned by local SD webui")
        output_path.write_bytes(base64.b64decode(images[0]))
    finally:
        if sdxl:
            _ensure_checkpoint(url_for_switch, SD15_CHECKPOINT_HINT)


@with_retry(max_retries=2, base_delay=2.0)
def _generate_image_img2img_local_sd(
    prompt: str, init_image_bytes: bytes, output_path: Path,
    denoising_strength: float = 0.7, seed: int = -1, sdxl: bool = False,
):
    """Generate an image via img2img, using a real stock photo as the
    starting point instead of pure noise — grounds the generation in real
    structure/geometry, which is exactly what pure txt2img gets wrong most
    often (extra limbs, melted keyboard keys, warped crowd seating: every one
    of those was a structural error, not a style error).

    denoising_strength=0.7 is the default for GENERIC stock-photo grounding
    (Pexels/Pixabay stand-ins): high enough that the output doesn't preserve
    that specific stock photo's likeness (a stock model's face, a product's
    exact branding), since reproducing an unrelated stock photo isn't the
    goal — just giving diffusion a real-world starting structure.

    When the reference is a photo the USER themselves supplied and wants
    recognizable (e.g. their own product reference photos), pass a low
    value instead (~0.2-0.3) so the output stays as close as possible to
    what they actually gave you while still being a transformed derivative,
    not a copy. Default to close-match (low denoising) whenever the caller
    is working from a user-provided reference image.
    """
    url_for_switch = _sd_webui_url()
    if sdxl:
        _ensure_checkpoint(url_for_switch, SDXL_CHECKPOINT_HINT)
    try:
        b64 = base64.b64encode(init_image_bytes).decode()
        url = f"{_sd_webui_url()}/sdapi/v1/img2img"
        body = {
            "init_images": [b64],
            "prompt": prompt,
            "negative_prompt": (
                "blurry, low quality, distorted, watermark, text, logo, nsfw, nudity, "
                "extra limbs, fused limbs, mutated hands, bad anatomy, disfigured, "
                "warped geometry, garbled pattern"
            ),
            "denoising_strength": denoising_strength,
            "width": 832 if sdxl else 576,
            "height": 1216 if sdxl else 1024,
            "steps": 30 if sdxl else 25,
            "cfg_scale": 5 if sdxl else 7,
            "sampler_name": "DPM++ SDE Karras" if sdxl else "DPM++ 2M",
            "batch_size": 1,
            "seed": seed,
            # See the matching comment in _generate_image_local_sd — without
            # this, the API call defaults to AnimateDiff's checkbox default
            # (enabled) regardless of the webui's saved UI state, turning a
            # single still-image request into a 16-frame ControlNet-guided video
            # generation that thrashes an 8GB card for 15+ minutes per image.
            "alwayson_scripts": _disable_motion_scripts(),
        }
        r = _post_sd_generate(url, body)
        if r.status_code != 200:
            raise RuntimeError(
                f"Local SD webui img2img {r.status_code}: {r.text[:200]} — is it "
                f"running with --api at {_sd_webui_url()}?"
            )
        data = r.json()
        images = data.get("images") or []
        if not images:
            raise RuntimeError("No image returned by local SD webui (img2img)")
        output_path.write_bytes(base64.b64decode(images[0]))
    finally:
        if sdxl:
            _ensure_checkpoint(url_for_switch, SD15_CHECKPOINT_HINT)


def _vision_qa_frame(image_path: Path) -> tuple[bool, str]:
    """Ask a local vision model (llava:7b, on the CPU-only marketing-helper
    Ollama instance — keeps the GPU free for SD/AnimateDiff) to flag genuine
    AI-generation artifacts in a single frame: extra/malformed limbs, garbled
    text/logos, warped geometry — the exact class of bug caught by hand
    throughout this project (extra legs, melted keyboard keys, warped crowd
    seating).

    Note: llava:7b is a smaller/older vision model — reliable at catching
    blatant garbling (tested: correctly flagged a garbled face+object image),
    less reliable at subtle anatomical errors (tested: missed a partially-
    hidden extra limb that a human caught). Not a silver bullet, but a real
    net positive since it fails OPEN (returns passed=True) on any error,
    unparseable response, or missed artifact — this is a best-effort quality
    gate, not a hard requirement. A miss here is no worse than the
    pre-existing baseline; a false rejection blocking production would be
    worse than either.
    """
    prompt = (
        "You are a quality-control check for AI-generated stock footage used as "
        "b-roll in a video. Look at this image and check ONLY for: extra or "
        "malformed limbs/fingers/faces, distorted or melted objects, garbled or "
        "illegible text/logos where text was clearly intended as a design element, "
        "or warped/nonsensical geometry (e.g. impossible furniture, melted "
        "keyboards, warped rows of seating). Ignore normal AI-art style, minor "
        "blur, or artistic choices — only flag genuine structural errors a viewer "
        "would immediately notice as broken.\n\n"
        "Respond with exactly one line: PASS or FAIL: <short reason>"
    )
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        r = requests.post(
            "http://127.0.0.1:11435/api/generate",
            json={
                "model": "llava:7b", "prompt": prompt,
                "images": [b64], "stream": False,
            },
            timeout=60,
        )
        if r.status_code != 200:
            return True, "vision QA unavailable, skipping"
        text = r.json().get("response", "").strip()
        if text.upper().startswith("FAIL"):
            return False, text
        return True, text
    except Exception as e:
        return True, f"vision QA error, skipping: {e}"


def _extract_frame(video_path: Path, out_path: Path, at_fraction: float = 0.5) -> bool:
    """Grab one representative frame from a video clip for a vision QA check."""
    try:
        duration_r = run_cmd(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture=True,
        )
        duration = float(duration_r.stdout.strip())
    except Exception:
        duration = 1.0
    run_cmd([
        "ffmpeg", "-y", "-ss", str(duration * at_fraction), "-i", str(video_path),
        "-frames:v", "1", "-update", "1", str(out_path), "-loglevel", "quiet",
    ])
    return out_path.exists()


def _animatediff_single_clip(prompt: str, seed: int = -1) -> Path | None:
    """Generate one 16-frame (~2s) AnimateDiff clip and return its path, or
    None on failure. 16 frames is the validated sweet spot on an 8GB GPU —
    pushing a single generation to 24 frames was tested and found to blow
    past comfortable VRAM headroom, degrading into GPU-memory thrashing
    (100% utilization for 15+ minutes with no result) rather than just
    taking proportionally longer. Generating multiple 16-frame clips and
    concatenating them (see _generate_video_local_animatediff) gives more
    real motion length without hitting that cliff.
    """
    import glob
    import time

    if "animatediff" not in _available_scripts():
        log("AnimateDiff not available on this webui — skipping motion clip, "
            "caller falls back to a real still image instead.")
        return None

    url = f"{_sd_webui_url()}/sdapi/v1/txt2img"
    before = time.time()
    body = {
        "prompt": prompt + _MOTION_SUFFIX,
        "negative_prompt": (
            "blurry, low quality, distorted, watermark, text, logo, nsfw, nudity, "
            "extra limbs, fused limbs, mutated hands, bad anatomy, disfigured, "
            "warped geometry, garbled pattern"
        ),
        "width": 384,
        "height": 576,
        "steps": 20,
        "cfg_scale": 7,
        "sampler_name": "DPM++ 2M",
        "batch_size": 1,
        "seed": seed,
        "alwayson_scripts": {
            "AnimateDiff": {
                "args": [{
                    "model": "v3_sd15_mm.ckpt",
                    "enable": True,
                    "video_length": 16,
                    "fps": 8,
                    "loop_number": 0,
                    "closed_loop": "R-P",
                    "batch_size": 16,
                    "stride": 1,
                    "overlap": -1,
                    "format": ["MP4"],
                    "interp": "Off",
                    "interp_x": 10,
                }]
            }
        },
    }
    try:
        r = requests.post(url, json=body, timeout=120)
    except Exception as e:
        log(f"AnimateDiff request failed: {e}")
        return None
    if r.status_code != 200:
        log(f"AnimateDiff {r.status_code}: {r.text[:200]}")
        return None

    # AnimateDiff saves the actual .mp4 to the webui's own outputs folder
    # rather than returning it in the API response — find the file it just
    # wrote (matched by being newer than this request).
    search_root = str(Path.home() / "stable-diffusion-webui" / "outputs" / "txt2img-images" / "AnimateDiff")
    candidates = [
        f for f in glob.glob(f"{search_root}/**/*.mp4", recursive=True)
        if os.path.getmtime(f) >= before
    ]
    if not candidates:
        log("AnimateDiff reported success but no output .mp4 was found")
        return None
    return Path(max(candidates, key=os.path.getmtime))


@with_retry(max_retries=2, base_delay=2.0)
def _generate_video_local_animatediff(prompt: str, output_path: Path, clips: int = 2) -> bool:
    """Generate `clips` distinct AnimateDiff motion clips and concatenate
    them into one longer real-motion clip at output_path, $0 cost — real
    variety instead of one short clip looping to fill duration. Returns
    False if AnimateDiff isn't available at all (missing motion module, out
    of VRAM, etc.) so the caller falls back to a static AI-generated image
    instead of failing the whole production over it.
    """
    import random
    import shutil

    paths = []
    for i in range(clips):
        clip = None
        for attempt in range(3):  # 1 initial try + 2 re-rolls on a failed QA check
            candidate = _animatediff_single_clip(prompt, seed=random.randint(0, 2**31 - 1))
            if candidate is None:
                break  # AnimateDiff itself unavailable — no point re-rolling
            qa_frame = candidate.with_name(f"{candidate.stem}_qa.jpg")
            if _extract_frame(candidate, qa_frame):
                passed, reason = _vision_qa_frame(qa_frame)
                qa_frame.unlink(missing_ok=True)
                if not passed:
                    log(f"Clip {i+1}/{clips} attempt {attempt+1}/3 failed vision QA "
                        f"({reason}) — re-rolling with a new seed...")
                    continue
            clip = candidate
            break
        if clip is None:
            break
        paths.append(clip)

    if not paths:
        log("AnimateDiff unavailable — falling back to static image")
        return False
    if len(paths) == 1:
        shutil.copy(paths[0], output_path)
        return True

    def _esc(p):
        return str(p).replace("'", "'\\''")

    concat_file = output_path.with_suffix(".concat.txt")
    concat_file.write_text(
        "\n".join(f"file '{_esc(p)}'" for p in paths), encoding="utf-8"
    )
    run_cmd([
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", str(output_path), "-y", "-loglevel", "quiet",
    ])
    concat_file.unlink(missing_ok=True)
    return output_path.exists()


@with_retry(max_retries=3, base_delay=2.0)
def _generate_image_gemini(prompt: str, output_path: Path, api_key: str):
    """Generate image via Gemini native image generation (free tier compatible)."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-2.5-flash-image:generateContent"
    )
    body = {
        "contents": [{"parts": [{"text": f"Generate an image: {prompt}"}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    r = requests.post(
        url, json=body, timeout=90,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    if r.status_code != 200:
        try:
            detail = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            detail = r.text[:200]
        hint = ""
        if r.status_code == 403:
            hint = (
                " — check that GEMINI_API_KEY is set in this environment and is "
                "an AI Studio key (https://aistudio.google.com/apikey), not a "
                "Vertex AI / service-account credential"
            )
        raise RuntimeError(f"Gemini API {r.status_code}: {detail}{hint}")
    data = r.json()
    # Extract image from response parts
    for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        if "inlineData" in part:
            img_b64 = part["inlineData"]["data"]
            output_path.write_bytes(base64.b64decode(img_b64))
            return
    raise RuntimeError("No image in Gemini response")


def _generate_video_kling(
    prompt: str, output_path: Path, duration: str = "5", timeout: int = 300
) -> bool:
    """Generate a b-roll video clip via the official Kling AI text-to-video API.

    Kling only makes sense for shots that need real motion (unfolding,
    camera movement) that a static SD frame can't fake — most b-roll should
    still go through the free local generators. Submits the job, polls
    until it succeeds, downloads the resulting clip. Returns False (does
    not raise) on any failure so callers can fall back to another method.
    """
    from .config import get_kling_key

    api_key = get_kling_key()
    if not api_key:
        log("Kling generation skipped — no KLING_API_KEY configured.")
        return False

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    submit = requests.post(
        "https://api.klingai.com/v1/videos/text2video",
        headers=headers,
        json={"model_name": "kling-v1", "prompt": prompt[:2500], "duration": duration},
        timeout=30,
    )
    if submit.status_code != 200:
        log(f"Kling submit failed ({submit.status_code}): {submit.text[:300]}")
        return False
    task_id = submit.json().get("data", {}).get("task_id")
    if not task_id:
        log(f"Kling submit had no task_id: {submit.text[:300]}")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(10)
        poll = requests.get(
            f"https://api.klingai.com/v1/videos/text2video/{task_id}",
            headers=headers, timeout=30,
        )
        if poll.status_code != 200:
            log(f"Kling poll failed ({poll.status_code}): {poll.text[:300]}")
            return False
        data = poll.json().get("data", {})
        status = data.get("task_status")
        if status == "succeed":
            videos = data.get("task_result", {}).get("videos", [])
            if not videos:
                log("Kling task succeeded but returned no video URL.")
                return False
            video_url = videos[0].get("url")
            video_bytes = requests.get(video_url, timeout=60).content
            output_path.write_bytes(video_bytes)
            return True
        if status == "failed":
            log(f"Kling task failed: {data.get('task_status_msg', 'unknown reason')}")
            return False
        # still "submitted" or "processing" — keep polling

    log("Kling generation timed out.")
    return False


def _check_sd_webui_reachable():
    """Fail fast with one clear error if EVERY configured SD instance is
    down, instead of letting every single frame in the loop retry and fail
    slowly on its own. One instance being down is fine — generate_broll's
    per-frame round-robin (_pick_sd_endpoint) just uses the other one for
    the whole video instead of splitting the load."""
    if os.environ.get("SD_WEBUI_URL"):
        if not _endpoint_healthy(_sd_webui_url()):
            raise RuntimeError(
                f"Local Stable Diffusion webui is not reachable at {_sd_webui_url()}. "
                "Start it before producing — a video is never shipped with a plain "
                "color placeholder instead of real b-roll."
            )
        return
    if not any(_endpoint_healthy(u) for u in _SD_ENDPOINTS):
        raise RuntimeError(
            f"None of the configured Stable Diffusion instances are reachable "
            f"({_SD_ENDPOINTS}). Start at least one before producing — a video "
            "is never shipped with a plain color placeholder instead of real b-roll."
        )


# Content categories this model tier (SD1.5 + AnimateDiff) reliably mangles:
# fine repetitive detail (keyboards, seating rows, crowds), legible text/logos,
# and close-up hands. AnimateDiff makes this worse, not better — it extends
# a single frame's flaw across 16 frames instead of it appearing once, so for
# these a static (Ken Burns-panned) image is the more reliable choice, not a
# fallback of last resort.
_ANIMATEDIFF_RISKY_TERMS = (
    "keyboard", "crowd", "audience", "spectator", "stadium seat", "seating",
    "theatre seat", "theater seat", "logo", "bold text", "text overlay",
    # Broadened from the exact phrases "close-up of a hand"/"close-up of
    # hands" — a real miss found in practice: "a gamer's hands playing..."
    # slipped through since it doesn't contain either literal phrase, and
    # produced a visibly malformed/melted hand in the AnimateDiff output.
    # Any mention of hands/fingers is risky regardless of surrounding
    # phrasing, not just that one specific "close-up of ___" construction.
    "hand", "hands", "fingers", "typing",
    # 2026-09-10: subjects that change shape/topology mid-clip (a phone
    # unfolding, a hinge opening) are the other consistent AnimateDiff/LTX
    # failure mode — the motion model wasn't trained on topology changes and
    # reliably warps the geometry (doubled edges, melted joints). A still +
    # Ken Burns pan/zoom has zero artifact risk and reads as motion anyway
    # for a 5-8s b-roll slot, so route these straight there instead of
    # risking a generation.
    "unfold", "unfolding", "fold", "folding", "hinge", "transform",
    "transforming", "morph", "morphing", "opening up",
)


def _is_animatediff_risky(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(term in lowered for term in _ANIMATEDIFF_RISKY_TERMS)


# LTX-Video (2B distilled) via a local ComfyUI instance — added 2026-09-06.
# Runs on the idle second GPU (2060 Super, the same card Forge is
# configured for) as a completely separate process from the SD1.5/SDXL
# webui on 7860, so it doesn't compete for VRAM or checkpoint state with
# anything else in this module. Motion quality is a clear step up from
# AnimateDiff — see the 2026-09-06 comparison — so it's now the first
# choice for a generated motion clip, with AnimateDiff kept as the
# fallback for whenever ComfyUI isn't running or a generation fails.
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
_LTX_CHECKPOINT = "ltxv-2b-0.9.6-distilled-04-25.safetensors"
_LTX_CLIP = "t5xxl_fp8_e4m3fn.safetensors"
_LTX_NEGATIVE = (
    "low quality, worst quality, deformed, distorted, disfigured, motion "
    "smear, motion artifacts, fused fingers, bad anatomy, weird hand, ugly, "
    "static image, blurry, watermark, text, logo, warping, melting, "
    "changing shape, morphing geometry, doubled edges, impossible geometry"
)
# Appended to every motion-clip prompt (LTX + AnimateDiff) — both models are
# far more reliable at camera movement over a stable subject than at the
# subject itself changing shape/pose, so nudging toward the former in the
# prompt itself (on top of routing outright topology-change prompts to a
# still via _is_animatediff_risky) measurably reduces warping.
_MOTION_SUFFIX = ", subtle slow motion, gentle camera movement, stable unchanging subject"


def _comfyui_reachable() -> bool:
    try:
        r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _ltx_workflow(prompt: str, seed: int, length: int) -> dict:
    """API-format graph matching ComfyUI's bundled ltxv_text_to_video
    template (comfyui_workflow_templates_json), swapped to the 2B
    *distilled* checkpoint — far fewer steps needed (8 vs the template's
    30) and no real CFG required, hence cfg=1.0 below.
    """
    return {
        "38": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": _LTX_CLIP, "type": "ltxv", "device": "default",
        }},
        "44": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": _LTX_CHECKPOINT}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt + _MOTION_SUFFIX, "clip": ["38", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": _LTX_NEGATIVE, "clip": ["38", 0]}},
        "70": {"class_type": "EmptyLTXVLatentVideo", "inputs": {
            "width": 768, "height": 512, "length": length, "batch_size": 1,
        }},
        "69": {"class_type": "LTXVConditioning", "inputs": {
            "positive": ["6", 0], "negative": ["7", 0], "frame_rate": 25,
        }},
        "71": {"class_type": "LTXVScheduler", "inputs": {
            "steps": 8, "max_shift": 2.05, "base_shift": 0.95,
            "stretch": True, "terminal": 0.1, "latent": ["70", 0],
        }},
        "73": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "72": {"class_type": "SamplerCustom", "inputs": {
            "model": ["44", 0], "add_noise": True, "noise_seed": seed, "cfg": 1.0,
            "positive": ["69", 0], "negative": ["69", 1],
            "sampler": ["73", 0], "sigmas": ["71", 0], "latent_image": ["70", 0],
        }},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["72", 0], "vae": ["44", 2]}},
        "78": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 24}},
        "79": {"class_type": "SaveVideo", "inputs": {
            "video": ["78", 0], "filename_prefix": "broll_ltx",
            "format": "auto", "codec": "auto",
        }},
    }


def _generate_video_local_ltx(prompt: str, output_path: Path, seed: int = -1, length: int = 161) -> bool:
    """Generate a motion clip via LTX-Video, re-rolling with a new seed up
    to twice if a vision-QA pass on a sampled frame catches warped geometry
    — same quality gate AnimateDiff's path already had, extended here so
    neither generator gets a free pass on a bad clip.
    """
    import random

    for attempt in range(3):  # 1 initial try + 2 re-rolls on a failed QA check
        this_seed = random.randint(0, 2**31 - 1) if seed == -1 else seed
        if not _generate_video_local_ltx_once(prompt, output_path, seed=this_seed, length=length):
            return False  # queue/connection failure — no point re-rolling
        qa_frame = output_path.with_name(f"{output_path.stem}_qa.jpg")
        if _extract_frame(output_path, qa_frame):
            passed, reason = _vision_qa_frame(qa_frame)
            qa_frame.unlink(missing_ok=True)
            if not passed:
                log(f"LTX-Video attempt {attempt+1}/3 failed vision QA ({reason}) — re-rolling with a new seed...")
                seed = -1  # force a fresh seed on retry even if caller passed a fixed one
                continue
        return True
    log("LTX-Video: never passed vision QA after 3 attempts — falling back.")
    return False


def _generate_video_local_ltx_once(prompt: str, output_path: Path, seed: int = -1, length: int = 161) -> bool:
    """Generate a short motion clip via local ComfyUI + LTX-Video.

    length=161 frames @ 25fps ~= 6.4s. broll generation happens before the
    voiceover exists, so the real per-frame slot duration isn't known yet —
    prepare_video_clip() covers that gap by looping whatever clip it's
    given with -stream_loop -1 to fill the actual slot. That's invisible
    for a normal ~10-20s stock clip, but a first attempt at this used the
    much shorter default of 65 frames (~2.6s) against a real ~5s slot and
    the loop was obvious — the same short clip visibly replaying twice in
    one frame (caught in production, 2026-09-06). 161 frames covers
    comfortably up to a ~6.4s slot without ever looping, which covers the
    typical range for BROLL_COUNT=8 over a ~40-60s script; only an
    unusually long slot would still loop, same as it would for a short
    stock clip.
    """
    import random
    import uuid

    if seed == -1:
        seed = random.randint(0, 2**31 - 1)
    client_id = str(uuid.uuid4())
    workflow = _ltx_workflow(prompt, seed, length)

    try:
        r = requests.post(
            f"{COMFYUI_URL}/prompt",
            json={"prompt": workflow, "client_id": client_id}, timeout=15,
        )
        r.raise_for_status()
        prompt_id = r.json()["prompt_id"]
    except Exception as e:
        log(f"LTX-Video: could not queue generation ({e}) — falling back.")
        return False

    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(3)
        try:
            hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10).json()
        except Exception:
            continue
        entry = hist.get(prompt_id)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            log(f"LTX-Video generation error: {status.get('messages')}")
            return False
        if status.get("completed"):
            video_info = None
            for node_out in entry.get("outputs", {}).values():
                if node_out.get("images"):
                    video_info = node_out["images"][0]
                    break
            if not video_info:
                log("LTX-Video: generation completed but no video output found.")
                return False
            try:
                resp = requests.get(
                    f"{COMFYUI_URL}/view",
                    params={
                        "filename": video_info["filename"],
                        "subfolder": video_info.get("subfolder", ""),
                        "type": video_info.get("type", "output"),
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                output_path.write_bytes(resp.content)
                return True
            except Exception as e:
                log(f"LTX-Video: failed to fetch output video ({e}).")
                return False
    log("LTX-Video: generation timed out after 180s.")
    return False


def generate_broll(
    prompts: list, out_dir: Path, use_stock: bool = True,
    exclude_paths: set[str] | None = None,
) -> tuple[list[Path], int, list[str]]:
    """Generate BROLL_COUNT b-roll frames (local Stable Diffusion by default, or Gemini).

    use_stock=False skips the Pexels lookup entirely and generates every
    frame — set this for niches about specific copyrighted media (a
    particular game/movie/show), where no free stock photo can legally
    depict the actual subject and searching only wastes an API call on
    generic, off-topic filler.

    Never produces a plain-color placeholder frame: if a frame can't be
    sourced from real stock footage or real AI generation, this raises and
    the whole production fails loudly instead of silently shipping a
    degraded video. Fix whatever's broken (usually: start SD webui) and
    retry, rather than accepting a video missing real b-roll.

    The third return value is every library asset path actually used for
    this job's b-roll — the caller should persist it on the draft
    (broll.artifacts.used_asset_paths) so cleanup_published.py can call
    media_library.mark_used() on each one once (and only once) this video
    is confirmed to have actually gone live on YouTube; see mark_used()'s
    docstring for why that has to be a separate, later step rather than
    stamping usage here at generation time.
    """
    provider = _broll_provider()
    api_key = get_gemini_key() if provider == "gemini" else None
    count = min(BROLL_COUNT, max(len(prompts), 1))

    if provider == "gemini" and not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set — cannot generate b-roll. Get an AI Studio "
            "key at https://aistudio.google.com/apikey (must be an AI Studio "
            "key; Vertex AI / service-account credentials are rejected with a "
            "403 'unregistered callers' error), or switch BROLL_PROVIDER to local_sd."
        )
    if provider != "gemini":
        _check_sd_webui_reachable()

    frames = []
    # Every asset actually used so far in THIS video — passed to each
    # subsequent fetch as an exclusion so two prompts that both happen to
    # match the same real clip (e.g. "a prominent Apple logo" and "a
    # MacBook with the Apple logo visible") can't show the identical
    # footage twice in one video. Seeded from `exclude_paths` when the
    # caller already has other frames from this same job on disk (e.g.
    # regenerating a single flagged frame after the fact) so the
    # replacement can't just re-pick the same duplicate again.
    used_asset_paths: set[str] = set(exclude_paths or ())

    global _endpoint_override
    for i, prompt in enumerate(prompts[:count]):
        out_path = out_dir / f"broll_{i}.png"

        # Pick this frame's SD instance once, up front — every local-SD call
        # below (txt2img, img2img, AnimateDiff) for this frame goes through
        # _sd_webui_url(), which reads this override. Rotating per-frame
        # rather than once per video is what actually spreads one video's
        # b-roll load across both GPUs instead of pinning the whole run to
        # whichever instance answered first.
        if provider != "gemini":
            _endpoint_override = _pick_sd_endpoint()

        # Prefer real motion — a real, freely-licensed stock video clip —
        # over a still image or an abstract AI-generated scene. Fall back to
        # a stock photo, then AI generation, only when no clip matches.
        video_bytes = fetch_topical_broll_video(prompt, exclude_paths=used_asset_paths) if use_stock else None
        if video_bytes:
            video_path = out_dir / f"broll_{i}.mp4"
            video_path.write_bytes(video_bytes)
            used_asset_paths.add(str(asset_path_for(video_bytes, "video")))
            log(f"Frame {i+1}/{count}: using real stock video (Pexels) for '{prompt.split('.')[0][:60]}'")
            frames.append(video_path)
            continue

        # Prefer real, freely-licensed stock footage that actually matches the
        # topic over an abstract AI-generated scene; only fall back to
        # generation when no relevant stock photo is found.
        stock_bytes = fetch_topical_broll(prompt, exclude_paths=used_asset_paths) if use_stock else None
        if stock_bytes:
            log(f"Frame {i+1}/{count}: using real stock photo (Pexels) for '{prompt.split('.')[0][:60]}'")
            used_asset_paths.add(str(asset_path_for(stock_bytes, "photo")))
            out_path.write_bytes(stock_bytes)
        else:
            # No stock match at all — prefer an AI-generated motion clip
            # over a plain still, so this fallback tier still produces real
            # video rather than a panned static image. LTX-Video (2B
            # distilled, via the local ComfyUI instance on port 8188) is
            # tried first — visibly more coherent motion than AnimateDiff in
            # side-by-side testing (2026-09-06) — with AnimateDiff kept as
            # the fallback for whenever ComfyUI isn't running or a
            # generation fails. Only drops to a still if neither motion
            # generator works out, never as a first choice.
            if provider != "gemini" and not _is_animatediff_risky(prompt):
                video_path = out_dir / f"broll_{i}.mp4"
                if os.environ.get("BROLL_USE_LTX", "true").lower() not in ("0", "false", "no") and _comfyui_reachable():
                    log(f"Frame {i+1}/{count}: no stock match, trying local LTX-Video motion clip...")
                    if _generate_video_local_ltx(prompt, video_path):
                        frames.append(video_path)
                        continue
                    log(f"Frame {i+1}/{count}: LTX-Video failed, falling back to AnimateDiff...")
                log(f"Frame {i+1}/{count}: no stock match, trying local AnimateDiff motion clip...")
                if _generate_video_local_animatediff(prompt, video_path):
                    frames.append(video_path)
                    continue
            elif provider != "gemini":
                log(f"Frame {i+1}/{count}: skipping motion generation (prompt risks an "
                    f"artifact — keyboard/crowd/logo/hands) in favor of a static image...")
            log(f"Frame {i+1}/{count}: generating still via "
                f"{'local Stable Diffusion' if provider != 'gemini' else 'Gemini Imagen'}...")
            if provider == "gemini":
                _generate_image_gemini(prompt, out_path, api_key)
            else:
                import random
                # Ground generation in a real photo when one's findable, even
                # a loosely-matching one — img2img starting from real
                # structure is far less prone to the anatomy/geometry errors
                # pure txt2img (starting from noise) keeps producing. This
                # must be a looser search than the direct-swap one above
                # (fetch_topical_broll): that one already ran against this
                # exact prompt and returned nothing, and re-running the same
                # deterministic search here would always fail identically,
                # making this branch dead code. fetch_img2img_reference_photo
                # is a deliberately weaker bar — safe here specifically
                # because img2img redraws the reference rather than showing
                # it directly.
                reference_bytes = fetch_img2img_reference_photo(prompt) if use_stock else None
                if reference_bytes:
                    log(f"Frame {i+1}/{count}: found a real reference photo — "
                        f"using img2img instead of generating from scratch...")

                # SDXL (RealVisXL on Forge) for still-only generation by
                # default — its motion-module support is too immature to
                # bother with, so this never applies to the AnimateDiff
                # branch above, only this plain-still fallback. Set
                # BROLL_SDXL_STILLS=false to fall back to the SD1.5 still
                # path (e.g. if Forge/RealVisXL isn't available).
                use_sdxl = os.environ.get("BROLL_SDXL_STILLS", "true").lower() not in ("0", "false", "no")

                qa_passed = False
                for attempt in range(3):  # 1 initial try + 2 re-rolls on a failed QA check
                    if reference_bytes:
                        _generate_image_img2img_local_sd(
                            prompt, reference_bytes, out_path, seed=random.randint(0, 2**31 - 1), sdxl=use_sdxl
                        )
                    else:
                        _generate_image_local_sd(prompt, out_path, seed=random.randint(0, 2**31 - 1), sdxl=use_sdxl)
                    qa_passed, reason = _vision_qa_frame(out_path)
                    if qa_passed:
                        break
                    log(f"Frame {i+1}/{count} attempt {attempt+1}/3 failed vision QA "
                        f"({reason}) — re-rolling with a new seed...")
                if not qa_passed:
                    # Real-photo rule: generation never passed QA — a real
                    # stock photo with even a loose keyword match carries zero
                    # anatomy/garbling risk, which beats keeping a
                    # confirmed-flawed AI image.
                    log(f"Frame {i+1}/{count}: generation never passed QA, "
                        f"trying a relaxed real-photo search before giving up...")
                    rescue_bytes = fetch_last_resort_stock_photo(prompt)
                    if rescue_bytes:
                        log(f"Frame {i+1}/{count}: real photo found — using it over the flawed generation.")
                        out_path.write_bytes(rescue_bytes)

        # Resize/crop to 9:16 portrait
        img = Image.open(out_path).convert("RGB")
        target_w, target_h = VIDEO_WIDTH, VIDEO_HEIGHT
        orig_w, orig_h = img.size
        scale = max(target_w / orig_w, target_h / orig_h)
        new_w, new_h = int(orig_w * scale), int(orig_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        img = img.crop((left, top, left + target_w, top + target_h))
        img.save(out_path)
        frames.append(out_path)

    _endpoint_override = None
    return frames, 0, sorted(used_asset_paths)


@with_retry(max_retries=2, base_delay=3.0)
def _clip_duration(clip_path: Path) -> float:
    r = run_cmd(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(clip_path)],
        capture=True,
    )
    return float(r.stdout.strip())


# Applied to every b-roll frame right before it's turned into a timed clip
# — the single choke point every source (stock footage, AI generation, or a
# manually-picked photo/screenshot) passes through, so grading here gives
# the whole video one consistent look instead of each shot carrying its
# source's own exposure/color quirks. Slightly darker (matches every niche's
# "dark moody atmosphere" prompt_suffix), a bit more contrast/saturation so
# flat stock footage reads as punchier, a light sharpen for soft/low-res
# sources, and a gentle vignette to pull focus toward the frame center.
_GRADE_FILTER = (
    "eq=contrast=1.06:brightness=-0.02:saturation=1.08:gamma=0.97,"
    "unsharp=5:5:0.4:5:5:0.0,"
    "vignette=PI/5"
)


def prepare_video_clip(clip_path: Path, out_path: Path, duration: float, punch_in: bool = False):
    """Crop/scale a real stock video clip to portrait and trim/loop to `duration` seconds.

    A clip shorter than the target duration is looped to fill it. A plain
    `-stream_loop` restart is fine for stock b-roll (an ambient, roughly
    static scene) but reads as a jarring jump-cut on a clip with real
    directional motion end-to-end (e.g. a phone unfolding open) — the loop
    snaps from the fully-open end frame straight back to the closed start
    frame. For those, play forward then backward (boomerang/ping-pong)
    before looping, so the restart is seamless instead of a visible glitch.

    `punch_in=True` adds a slow, continuous zoom-in over the clip's
    duration (1.0x to 1.25x) — for a real video clip that's otherwise a
    static, locked-off shot (e.g. an unmoving product close-up), a punch-in
    reads as far less stale than holding the exact same framing the whole
    slot. zoompan works on video input (not just stills) as long as it
    outputs one frame per input frame (d=1), letting its self-referencing
    `zoom+increment` expression persist and increase frame over frame.
    """
    fps = 30
    src_duration = _clip_duration(clip_path)
    loop_source = clip_path
    if src_duration < duration:
        boomerang_path = clip_path.with_name(clip_path.stem + "_boomerang.mp4")
        run_cmd([
            "ffmpeg", "-i", str(clip_path),
            "-filter_complex", "[0:v]split[a][b];[b]reverse[b2];[a][b2]concat=n=2:v=1[out]",
            "-map", "[out]", "-an", "-pix_fmt", "yuv420p",
            str(boomerang_path), "-y", "-loglevel", "quiet",
        ])
        loop_source = boomerang_path
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT
    # AnimateDiff clips come out at a low native frame rate (~8fps). A plain
    # `fps=30` here just duplicates each source frame ~4x, which still plays
    # back as visibly choppy/stuttery — it doesn't create real motion
    # between frames. minterpolate synthesizes genuine in-between frames via
    # motion estimation, so the upsampled result actually looks smooth
    # instead of low-framerate footage stretched to fill a 30fps container.
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1"
    )
    if punch_in:
        total_frames = int(duration * fps)
        zoom_per_frame = 0.25 / max(total_frames, 1)
        vf += (
            f",scale=iw*2:ih*2,zoompan=z='min(zoom+{zoom_per_frame:.6f},1.25)'"
            f":d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
        )
    vf += f",{_GRADE_FILTER}"
    run_cmd([
        "ffmpeg", "-stream_loop", "-1", "-i", str(loop_source),
        "-vf", vf, "-t", str(duration),
        "-an", "-pix_fmt", "yuv420p", str(out_path), "-y", "-loglevel", "quiet",
    ])


def animate_frame(img_path: Path, out_path: Path, duration: float, effect: str = "zoom_in"):
    """Ken Burns animation on a single frame."""
    fps = 30
    frames = int(duration * fps)
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT

    if effect == "zoom_in":
        vf = (
            f"scale={int(w * 1.12)}:{int(h * 1.12)},"
            f"zoompan=z='1.12-0.12*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={w}x{h}:fps={fps}"
        )
    elif effect == "pan_right":
        vf = (
            f"scale={int(w * 1.15)}:{int(h * 1.15)},"
            f"zoompan=z=1.15:x='0.15*iw*on/{frames}':y='ih*0.075'"
            f":d={frames}:s={w}x{h}:fps={fps}"
        )
    else:  # zoom_out
        vf = (
            f"scale={int(w * 1.12)}:{int(h * 1.12)},"
            f"zoompan=z='1.0+0.12*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={w}x{h}:fps={fps}"
        )
    vf += f",{_GRADE_FILTER}"

    run_cmd([
        # -framerate is load-bearing: without it, -loop 1 feeds the image at
        # the image2 demuxer's default ~25fps, so zoompan (which restarts
        # its zoom progress each time it receives a "new" input frame) sees
        # many redundant deliveries of the same static image and keeps
        # resetting near the starting zoom level instead of animating
        # smoothly across the full duration — the frame reads as frozen.
        # One input frame per second means zoompan only ever sees a single
        # source frame and animates its own d={frames} output cleanly.
        "ffmpeg", "-loop", "1", "-framerate", "1", "-i", str(img_path),
        "-vf", vf, "-t", str(duration), "-r", str(fps),
        "-pix_fmt", "yuv420p", str(out_path), "-y", "-loglevel", "quiet",
    ])


def animate_multi_image_frame(
    img_paths: list[Path], out_path: Path, duration: float, tmp_dir: Path, xfade_dur: float = 0.4,
):
    """Ken Burns + crossfade sequence across multiple items, filling one
    b-roll slot's full duration. Each item can be a still image (gets a Ken
    Burns pan) or a real video clip (gets trimmed/cropped to its share of
    the duration instead, since it already has its own motion) — mixed
    freely in the same slot.

    Added because relying more heavily on manually-picked real photos (as
    opposed to one auto-matched stock result) meant a single slot sometimes
    has 2-3 genuinely good, distinct real items available instead of just
    one — showing all of them reads as far more dynamic than picking one
    and discarding the rest, without introducing an AI-generated video's
    artifact risk.
    """
    effects = ["zoom_in", "pan_right", "zoom_out"]
    n = len(img_paths)
    if n == 1:
        item = Path(img_paths[0])
        if item.suffix.lower() == ".mp4":
            prepare_video_clip(item, out_path, duration)
        else:
            animate_frame(item, out_path, duration, effects[0])
        return

    # Equal time share per item, sized so the crossfade overlaps still add
    # up to the full requested duration rather than running short.
    total_xfade = xfade_dur * (n - 1)
    per_image = (duration + total_xfade) / n

    sub_clips = []
    for i, item in enumerate(img_paths):
        item = Path(item)
        sub = tmp_dir / f"multi_sub_{out_path.stem}_{i}.mp4"
        if item.suffix.lower() == ".mp4":
            prepare_video_clip(item, sub, per_image)
        else:
            animate_frame(item, sub, per_image, effects[i % len(effects)])
        sub_clips.append(sub)

    inputs = []
    for c in sub_clips:
        inputs += ["-i", str(c)]

    filt = []
    cur = "0:v"
    cum = per_image
    for i in range(1, n):
        offset = cum - xfade_dur
        label = f"v{i}" if i < n - 1 else "vout"
        filt.append(f"[{cur}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[{label}]")
        cur = label
        cum = cum - xfade_dur + per_image

    run_cmd([
        "ffmpeg", *inputs, "-filter_complex", ";".join(filt),
        "-map", "[vout]", "-t", str(duration),
        "-pix_fmt", "yuv420p", str(out_path), "-y", "-loglevel", "quiet",
    ])

"""Gemini Imagen / local Stable Diffusion b-roll generation + Ken Burns animation."""

import base64
import os
from pathlib import Path

import requests
from PIL import Image

from .config import BROLL_COUNT, VIDEO_WIDTH, VIDEO_HEIGHT, get_gemini_key, run_cmd
from .log import log
from .retry import with_retry
from .stock_media import fetch_topical_broll, fetch_topical_broll_video


def _broll_provider() -> str:
    """Which backend to use for b-roll images: 'local_sd' or 'gemini'."""
    return os.environ.get("BROLL_PROVIDER", "local_sd").lower()


def _sd_webui_url() -> str:
    return os.environ.get("SD_WEBUI_URL", "http://127.0.0.1:7860").rstrip("/")


@with_retry(max_retries=2, base_delay=2.0)
def _generate_image_local_sd(prompt: str, output_path: Path):
    """Generate an image via a local AUTOMATIC1111 webui (--api), $0 cost."""
    url = f"{_sd_webui_url()}/sdapi/v1/txt2img"
    body = {
        "prompt": prompt,
        # Always include safety terms, not just quality terms — b-roll may
        # still end up naming a real person despite the draft prompt's rule
        # against it (local models don't always follow instructions), and an
        # inappropriate/undignified depiction is worse than a quality flaw.
        "negative_prompt": (
            "blurry, low quality, distorted, watermark, text, logo, "
            "nsfw, nudity, sexualized, suggestive, revealing clothing"
        ),
        "width": 576,
        "height": 1024,
        "steps": 20,
        "cfg_scale": 7,
        "sampler_name": "DPM++ 2M",
        "batch_size": 1,
    }
    r = requests.post(url, json=body, timeout=300)
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


def _check_sd_webui_reachable():
    """Fail fast with one clear error if local SD webui is down, instead of
    letting every single frame in the loop retry and fail slowly on its own."""
    try:
        r = requests.get(f"{_sd_webui_url()}/sdapi/v1/sd-models", timeout=5)
        if r.status_code != 200:
            raise RuntimeError(f"Local SD webui returned {r.status_code}")
    except Exception as e:
        raise RuntimeError(
            f"Local Stable Diffusion webui is not reachable at {_sd_webui_url()} ({e}). "
            "Start it before producing — a video is never shipped with a plain "
            "color placeholder instead of real b-roll."
        )


def generate_broll(prompts: list, out_dir: Path, use_stock: bool = True) -> tuple[list[Path], int]:
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

    for i, prompt in enumerate(prompts[:count]):
        out_path = out_dir / f"broll_{i}.png"

        # Prefer real motion — a real, freely-licensed stock video clip —
        # over a still image or an abstract AI-generated scene. Fall back to
        # a stock photo, then AI generation, only when no clip matches.
        video_bytes = fetch_topical_broll_video(prompt) if use_stock else None
        if video_bytes:
            video_path = out_dir / f"broll_{i}.mp4"
            video_path.write_bytes(video_bytes)
            log(f"Frame {i+1}/{count}: using real stock video (Pexels) for '{prompt.split('.')[0][:60]}'")
            frames.append(video_path)
            continue

        # Prefer real, freely-licensed stock footage that actually matches the
        # topic over an abstract AI-generated scene; only fall back to
        # generation when no relevant stock photo is found.
        stock_bytes = fetch_topical_broll(prompt) if use_stock else None
        if stock_bytes:
            log(f"Frame {i+1}/{count}: using real stock photo (Pexels) for '{prompt.split('.')[0][:60]}'")
            out_path.write_bytes(stock_bytes)
        else:
            log(f"Frame {i+1}/{count}: no stock match, generating via "
                f"{'local Stable Diffusion' if provider != 'gemini' else 'Gemini Imagen'}...")
            if provider == "gemini":
                _generate_image_gemini(prompt, out_path, api_key)
            else:
                _generate_image_local_sd(prompt, out_path)

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

    return frames, 0


def prepare_video_clip(clip_path: Path, out_path: Path, duration: float):
    """Crop/scale a real stock video clip to portrait and trim/loop to `duration` seconds.

    `-stream_loop -1` loops the source indefinitely so a clip shorter than
    the target duration still fills it, then `-t` cuts to the exact length —
    correctly handles both too-short and too-long source clips.
    """
    fps = 30
    w, h = VIDEO_WIDTH, VIDEO_HEIGHT
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps}"
    run_cmd([
        "ffmpeg", "-stream_loop", "-1", "-i", str(clip_path),
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

    run_cmd([
        "ffmpeg", "-loop", "1", "-i", str(img_path),
        "-vf", vf, "-t", str(duration), "-r", str(fps),
        "-pix_fmt", "yuv420p", str(out_path), "-y", "-loglevel", "quiet",
    ])

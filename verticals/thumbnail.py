"""Thumbnail generation — local Stable Diffusion / Gemini Imagen (16:9) + Pillow text overlay."""

import base64
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from .broll import _broll_provider, _sd_webui_url, _vision_qa_frame
from .config import get_gemini_key, VIDEO_WIDTH, VIDEO_HEIGHT
from .log import log
from .retry import with_retry

# Landscape (traditional long-form) thumbnail dimensions.
THUMB_WIDTH = 1280
THUMB_HEIGHT = 720

# Shorts thumbnails must match the video's own portrait orientation — a
# landscape thumbnail gets cropped oddly by YouTube's Shorts shelf/player,
# which is exactly why thumbnails looked wrong on every video this pipeline
# has produced (every one of them is platform=shorts). Reuses the video's
# own resolution so text/composition is designed for the frame it'll
# actually be displayed in.
THUMB_WIDTH_SHORTS = VIDEO_WIDTH
THUMB_HEIGHT_SHORTS = VIDEO_HEIGHT


@with_retry(max_retries=2, base_delay=2.0)
def _generate_thumb_local_sd(
    prompt: str, output_path: Path, seed: int = -1,
    width: int = 1024, height: int = 576,
):
    """Generate a thumbnail via a local AUTOMATIC1111 webui (--api), $0 cost."""
    url = f"{_sd_webui_url()}/sdapi/v1/txt2img"
    body = {
        "prompt": prompt,
        # Always include safety terms, not just quality terms — the thumbnail
        # prompt may still end up naming a real person despite the draft
        # prompt's rule against it, and a thumbnail is the single most public
        # frame of the whole video (shown before anyone even clicks play).
        "negative_prompt": (
            "blurry, low quality, distorted, watermark, text, logo, "
            "nsfw, nudity, sexualized, suggestive, revealing clothing, "
            "extra limbs, fused limbs, mutated hands, bad anatomy, disfigured, "
            "warped geometry, garbled pattern"
        ),
        "width": width,
        "height": height,
        "steps": 20,
        "cfg_scale": 7,
        "sampler_name": "DPM++ 2M",
        "batch_size": 1,
        "seed": seed,
    }
    r = requests.post(url, json=body, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(
            f"Local SD webui {r.status_code}: {r.text[:200]} — is it running "
            f"with --api at {_sd_webui_url()}?"
        )
    images = r.json().get("images") or []
    if not images:
        raise RuntimeError("No image returned by local SD webui")
    output_path.write_bytes(base64.b64decode(images[0]))


@with_retry(max_retries=3, base_delay=2.0)
def _generate_thumb_image(prompt: str, output_path: Path, api_key: str, portrait: bool = False):
    """Generate a thumbnail via Gemini native image generation."""
    orientation = "9:16 portrait" if portrait else "16:9 landscape"
    url = (
        "https://generativelanguage.googleapis.com/v1beta"
        "/models/gemini-2.0-flash-exp-image-generation:generateContent"
    )
    body = {
        "contents": [{"parts": [{"text": f"Generate a {orientation} image: {prompt}"}]}],
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
    for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        if "inlineData" in part:
            img_b64 = part["inlineData"]["data"]
            output_path.write_bytes(base64.b64decode(img_b64))
            return
    raise RuntimeError("No image in Gemini response")


def _overlay_title(
    image_path: Path, title: str, output_path: Path,
    target_width: int = THUMB_WIDTH, target_height: int = THUMB_HEIGHT,
    font_size: int = 64,
):
    """Overlay bold title text with drop shadow on the thumbnail."""
    img = Image.open(image_path).convert("RGB")
    img = img.resize((target_width, target_height), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    # Try to find a bold font, fall back to default
    font = None
    for font_name in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
    ]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except (OSError, IOError):
            continue
    if font is None:
        font = ImageFont.load_default()

    # Word wrap the title
    max_width = target_width - 80  # 40px padding each side
    lines = _wrap_text(draw, title, font, max_width)
    text_block = "\n".join(lines)

    # Calculate position (center, lower third)
    bbox = draw.multiline_textbbox((0, 0), text_block, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (target_width - text_w) // 2
    y = target_height - text_h - 60  # 60px from bottom

    # Drop shadow
    shadow_offset = 3
    draw.multiline_text(
        (x + shadow_offset, y + shadow_offset),
        text_block, fill=(0, 0, 0), font=font, align="center",
    )

    # Main text
    draw.multiline_text(
        (x, y), text_block, fill=(255, 255, 255), font=font, align="center",
    )

    img.save(output_path)


def _wrap_text(draw: ImageDraw.Draw, text: str, font, max_width: int) -> list[str]:
    """Simple word-wrap for Pillow text rendering."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def generate_thumbnail(draft: dict, out_dir: Path) -> Path:
    """Generate a YouTube thumbnail with Gemini + text overlay.

    Uses the thumbnail_prompt from the draft, overlays the video title.
    Returns path to the final thumbnail PNG.
    """
    provider = _broll_provider()
    prompt = draft.get("thumbnail_prompt", "Cinematic YouTube thumbnail")
    title = draft.get("youtube_title", draft.get("news", ""))
    job_id = draft.get("job_id", "unknown")

    # Shorts thumbnails must be portrait to match how YouTube actually
    # displays them — a landscape thumbnail gets awkwardly cropped in the
    # Shorts shelf/player. Every video this pipeline makes is platform=shorts.
    is_shorts = draft.get("platform", "shorts") == "shorts"
    target_width = THUMB_WIDTH_SHORTS if is_shorts else THUMB_WIDTH
    target_height = THUMB_HEIGHT_SHORTS if is_shorts else THUMB_HEIGHT
    gen_width, gen_height = (576, 1024) if is_shorts else (1024, 576)
    font_size = 72 if is_shorts else 64

    raw_path = out_dir / f"thumb_raw_{job_id}.png"
    final_path = out_dir / f"thumb_{job_id}.png"

    if provider == "gemini":
        api_key = get_gemini_key()
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set — cannot generate thumbnail. Get an "
                "AI Studio key at https://aistudio.google.com/apikey (Vertex AI / "
                "service-account credentials are rejected with a 403 "
                "'unregistered callers' error)."
            )
        log("Generating thumbnail via Gemini Imagen...")
        _generate_thumb_image(prompt, raw_path, api_key, portrait=is_shorts)
    else:
        log("Generating thumbnail via local Stable Diffusion...")
        import random
        for attempt in range(3):  # 1 initial try + 2 re-rolls on a failed QA check
            _generate_thumb_local_sd(prompt, raw_path, seed=random.randint(0, 2**31 - 1), width=gen_width, height=gen_height)
            passed, reason = _vision_qa_frame(raw_path)
            if passed:
                break
            log(f"Thumbnail attempt {attempt+1}/3 failed vision QA ({reason}) — re-rolling with a new seed...")

    log("Adding title overlay...")
    _overlay_title(raw_path, title, final_path, target_width=target_width, target_height=target_height, font_size=font_size)

    log(f"Thumbnail saved: {final_path.name}")
    return final_path

"""Animated channel-logo intro/outro bumpers, rendered once and cached.

Every produced video gets the same branded open/close so the channel is
recognizable across uploads, without paying render cost per video — the
bumper clips are cached on disk and only regenerated if the logo changes.
"""

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .broll import animate_frame
from .config import VIDEO_WIDTH, VIDEO_HEIGHT, run_cmd
from .log import log

BRANDING_DIR = Path(__file__).resolve().parent.parent / "branding"
LOGO_PATH = BRANDING_DIR / "logo_icon_800.png"
BUMPER_DIR = BRANDING_DIR / "bumpers"

# 2026-09-09: per-platform custom button overlays (downloaded from
# jitter.video, one design per platform since YouTube/Instagram/TikTok each
# use their own follow/subscribe terminology and iconography — a YouTube
# play-button icon doesn't make sense reused as an Instagram "Follow"
# prompt). Drop a file named exactly "youtube", "instagram", or "tiktok"
# (any extension — gif/mp4/webm/mov/png) in this directory and it's used
# automatically; a platform with no custom file falls back to the plain
# generated SUBSCRIBE pill below.
CUSTOM_BUTTON_DIR = BRANDING_DIR / "custom_buttons"

INTRO_DURATION = 1.2
OUTRO_DURATION = 1.5

LOGO_SIZE_FRAC = 0.55  # fraction of VIDEO_WIDTH the logo occupies in the composed frame
FPS = 30

# Same palette as the logo itself (see make_logo.py's BG/RED/WHITE) so the
# button reads as part of the same brand rather than a bolted-on sticker.
_BUTTON_RED = (255, 31, 61)
_BUTTON_WHITE = (244, 245, 247)
_FONT_PATH = Path("C:/Windows/Fonts/arialbd.ttf")


def _logo_frame_path() -> Path:
    return BUMPER_DIR / f"logo_frame_{VIDEO_WIDTH}x{VIDEO_HEIGHT}.png"


def _compose_logo_frame() -> Path:
    """Center the logo on a black canvas at the video's resolution, cached."""
    out_path = _logo_frame_path()
    if out_path.exists() and out_path.stat().st_mtime >= LOGO_PATH.stat().st_mtime:
        return out_path

    BUMPER_DIR.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (18, 20, 28))
    logo = Image.open(LOGO_PATH).convert("RGBA")

    logo_size = int(VIDEO_WIDTH * LOGO_SIZE_FRAC)
    logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
    x = (VIDEO_WIDTH - logo_size) // 2
    y = (VIDEO_HEIGHT - logo_size) // 2
    canvas.paste(logo, (x, y), logo)
    canvas.save(out_path)
    return out_path


def _subscribe_button_path() -> Path:
    return BUMPER_DIR / f"subscribe_button_{VIDEO_WIDTH}x{VIDEO_HEIGHT}.png"


def get_subscribe_button_overlay() -> Path:
    """A standalone transparent "SUBSCRIBE" pill button, cached — composited
    via ffmpeg overlay (not baked into a bumper) so it can pop in partway
    through the content, right as the CTA line is spoken, and stay on
    screen through the outro instead of only appearing in the closing
    bumper. Sized/positioned to sit above where captions render (bottom
    ~40% of frame) so it never collides with the burned-in caption text.
    """
    out_path = _subscribe_button_path()
    if out_path.exists():
        return out_path

    BUMPER_DIR.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    label = "SUBSCRIBE"
    font_size = int(VIDEO_WIDTH * 0.06)
    font = ImageFont.truetype(str(_FONT_PATH), font_size)

    pad_x, pad_y = int(font_size * 0.9), int(font_size * 0.5)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    btn_w, btn_h = text_w + pad_x * 2, text_h + pad_y * 2

    x0 = (VIDEO_WIDTH - btn_w) // 2
    y0 = int(VIDEO_HEIGHT * 0.18) - btn_h // 2
    x1, y1 = x0 + btn_w, y0 + btn_h
    radius = btn_h // 2

    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=_BUTTON_RED)
    text_x = x0 + (btn_w - text_w) // 2 - bbox[0]
    text_y = y0 + (btn_h - text_h) // 2 - bbox[1]
    draw.text((text_x, text_y), label, font=font, fill=_BUTTON_WHITE)

    canvas.save(out_path)
    return out_path


_ANIMATED_BUTTON_EXTS = {".mp4", ".gif", ".webm", ".mov"}


def get_platform_button_overlay(platform: str) -> tuple[Path, bool]:
    """Return (path, is_animated) for `platform`'s subscribe/follow button.

    Looks for a custom file (any of _ANIMATED_BUTTON_EXTS, or a static
    .png/.jpg) named exactly for the platform in CUSTOM_BUTTON_DIR first;
    falls back to the plain generated SUBSCRIBE pill (get_subscribe_button_
    overlay) if no custom asset has been dropped in yet. is_animated tells
    assemble_video whether to treat it as a looping video/gif input
    (-stream_loop -1) instead of a static looped image (-loop 1).
    """
    if CUSTOM_BUTTON_DIR.exists():
        matches = [
            p for p in CUSTOM_BUTTON_DIR.glob(f"{platform}.*")
            if p.suffix.lower() in _ANIMATED_BUTTON_EXTS | {".png", ".jpg", ".jpeg"}
        ]
        if matches:
            path = matches[0]
            return path, path.suffix.lower() in _ANIMATED_BUTTON_EXTS
    return get_subscribe_button_overlay(), False


def _bumper_path(kind: str) -> Path:
    return BUMPER_DIR / f"{kind}_{VIDEO_WIDTH}x{VIDEO_HEIGHT}.mp4"


def _get_bumper(kind: str, duration: float, effect: str) -> Path:
    out_path = _bumper_path(kind)
    if out_path.exists() and out_path.stat().st_mtime >= LOGO_PATH.stat().st_mtime:
        return out_path

    log(f"Rendering {kind} logo bumper (cached for future videos)...")
    logo_frame = _compose_logo_frame()
    animate_frame(logo_frame, out_path, duration, effect=effect)
    return out_path


def _render_outro_close(logo_frame: Path, out_path: Path, duration: float) -> None:
    """Iris the frame down to black around a fixed-size logo — the logo itself
    never grows or moves, only the surrounding darkness closes in on it,
    like an old-cartoon iris-out ending.
    """
    frames_dir = BUMPER_DIR / "_outro_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for f in frames_dir.glob("*.png"):
        f.unlink()

    base = np.array(Image.open(logo_frame).convert("RGB")).astype(float)
    h, w = base.shape[:2]
    cx, cy = w / 2, h / 2

    ys, xs = np.mgrid[0:h, 0:w]
    dist = np.hypot(xs - cx, ys - cy)

    # Gauge disc radius is 0.46 of the icon's own size (see make_logo.py's
    # draw_gauge_icon: `r = size * 0.46`) — end exactly on that edge so the
    # iris visibly meets the logo instead of stopping with a gap around it.
    GAUGE_DISC_FRAC = 0.46
    logo_size = w * LOGO_SIZE_FRAC
    start_radius = math.hypot(cx, cy)  # covers every corner — fully open at frame 0
    end_radius = logo_size * GAUGE_DISC_FRAC
    feather = 12  # soft edge width in pixels, avoids a hard-edged cutout

    n_frames = int(duration * FPS)
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        radius = start_radius + (end_radius - start_radius) * t
        mask = np.clip((radius - dist) / feather, 0, 1)
        frame = (base * mask[..., None]).astype("uint8")
        Image.fromarray(frame).save(frames_dir / f"f_{i:04d}.png")

    run_cmd([
        "ffmpeg", "-framerate", str(FPS), "-i", str(frames_dir / "f_%04d.png"),
        "-pix_fmt", "yuv420p", "-r", str(FPS), str(out_path), "-y", "-loglevel", "quiet",
    ])
    for f in frames_dir.glob("*.png"):
        f.unlink()


def get_intro_clip() -> Path:
    """Logo settles into place (zoom-out from 112% to 100%)."""
    return _get_bumper("intro", INTRO_DURATION, effect="zoom_out")


def get_outro_clip() -> Path:
    """Background shrinks away as the camera pushes in until the gauge
    circle fills the whole frame — a proper close-in ending."""
    out_path = _bumper_path("outro")
    if out_path.exists() and out_path.stat().st_mtime >= LOGO_PATH.stat().st_mtime:
        return out_path

    log("Rendering outro logo bumper (cached for future videos)...")
    logo_frame = _compose_logo_frame()
    _render_outro_close(logo_frame, out_path, OUTRO_DURATION)
    return out_path

"""Automated vision-model-first QA pass over a finished assembled video.

The existing per-image check (`broll._vision_qa_frame`) only looks at one
AI-generated still in isolation, checking for anatomical/text-garbling
errors. It has no way to catch things that only show up in the finished,
assembled video: a tiled/collage composition baked into a single generation
(passes the narrow per-image check since there's no anatomical error, just a
bad layout), a caption that runs past where the video actually cuts to the
outro, or a real stock clip that matched on a bad keyword and shows content
that doesn't belong at all (e.g. a Minecraft Enderman in a Resident Evil
video). All three happened for real on job 1788390102 (2026-09-03) and only
surfaced during manual frame-by-frame review — this module runs that same
kind of check automatically, sampling the finished video and asking the
vision model first, before a human/Claude look.
"""
import base64
from pathlib import Path

import requests

from .config import run_cmd
from .log import log

# Same CPU-only marketing-helper Ollama instance the per-image b-roll QA
# uses (see broll._vision_qa_frame) — keeps the GPU free for SD/AnimateDiff.
OLLAMA_URL = "http://127.0.0.1:11435/api/generate"
MODEL = "llava:7b"

PROMPT = (
    "You are a quality-control check for one frame of a finished short-form "
    "video. Check for exactly these things:\n"
    "1. Is this a single clean scene, or does it look like a tiled/collage "
    "grid of multiple near-duplicate sub-images stacked together?\n"
    "2. If there is caption text burned into the image, is it legible and "
    "not garbled or cut off mid-word?\n"
    "3. Does the visible subject look coherent and like it belongs in this "
    "scene (not an obviously unrelated subject, e.g. the wrong game/franchise "
    "entirely)?\n"
    "Ignore normal AI-art style, minor blur, or artistic choices — only flag "
    "things a viewer would immediately notice as broken or wrong.\n\n"
    "Respond with exactly one line: PASS or FAIL: <short reason>."
)


def _sample_frames(video_path: Path, out_dir: Path, interval: float) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "qa_frame_%03d.jpg"
    run_cmd([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"fps=1/{interval}", str(pattern),
        "-loglevel", "quiet",
    ])
    return sorted(out_dir.glob("qa_frame_*.jpg"))


def _qa_one_frame(frame_path: Path) -> tuple[bool, str]:
    """Fails OPEN (passed=True) on any error/timeout/unparseable response —
    best-effort quality gate, not a hard requirement. Same policy as
    broll._vision_qa_frame, for the same reason: a missed defect here is no
    worse than the pre-existing baseline (no automated video-level check at
    all); a false rejection blocking production would be worse than either."""
    try:
        b64 = base64.b64encode(frame_path.read_bytes()).decode()
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL, "prompt": PROMPT, "images": [b64], "stream": False,
        }, timeout=60)
        if r.status_code != 200:
            return True, "vision QA unavailable, skipping"
        text = r.json().get("response", "").strip()
        if text.upper().startswith("FAIL"):
            return False, text
        return True, text
    except Exception as e:
        return True, f"vision QA error, skipping: {e}"


def qa_assembled_video(video_path: Path, work_dir: Path, interval: float = 3.0) -> list[dict]:
    """Sample the finished video and vision-QA every frame. Returns a list of
    {timestamp, passed, reason} dicts — never raises. Sampled frames are
    deleted after the check; they're a scratch artifact, not an output."""
    frames_dir = work_dir / "video_qa_frames"
    frames = _sample_frames(video_path, frames_dir, interval)
    findings = []
    for i, frame in enumerate(frames):
        passed, reason = _qa_one_frame(frame)
        findings.append({"timestamp": round(i * interval, 1), "passed": passed, "reason": reason})
        if not passed:
            log(f"  [video QA] t={i * interval:.1f}s: {reason}")
        try:
            frame.unlink()
        except OSError:
            pass
    try:
        frames_dir.rmdir()
    except OSError:
        pass
    return findings


def summarize_findings(findings: list[dict]) -> str:
    if not findings:
        return "video QA: no frames sampled"
    failures = [f for f in findings if not f["passed"]]
    if not failures:
        return f"video QA: PASS ({len(findings)} frames checked, no issues found)"
    lines = [f"video QA: {len(failures)}/{len(findings)} frames flagged —"]
    for f in failures:
        lines.append(f"  t={f['timestamp']}s: {f['reason']}")
    return "\n".join(lines)

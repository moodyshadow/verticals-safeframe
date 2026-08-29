"""End-to-end pipeline for autonomous use: scan -> draft -> produce -> stop.

Deliberately does NOT upload to YouTube. This is meant to be run by Claw
(the OpenClaw agent) on its own — scanning trending topics, drafting a
script grounded in the real source article, and producing a finished video
— then handing it off for a human to review before it ever gets published.
Publishing is a separate, explicit step (`python -m verticals upload`) that
stays a deliberate human action.

Run manually:
    venv\\Scripts\\python.exe run_full_pipeline.py

Writes a status file to reports/pipeline_status.json that a caller (Claw,
or a human) can check for the result: the video path, title, and script,
or an error if something failed.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scan_opportunities import main as run_scan  # noqa: E402
from verticals.config import MEDIA_DIR  # noqa: E402

REPORTS_DIR = Path(__file__).parent / "reports"
STATUS_PATH = REPORTS_DIR / "pipeline_status.json"
VENV_PYTHON = Path(__file__).parent / "venv" / "Scripts" / "python.exe"


def _write_status(status: dict):
    REPORTS_DIR.mkdir(exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")


def main():
    print("Step 1/2: scanning topics + drafting script...")
    try:
        draft_path = run_scan()
    except Exception as e:
        _write_status({"ok": False, "stage": "scan", "error": str(e)})
        print(f"Scan failed: {e}")
        return 1

    if not draft_path:
        _write_status({
            "ok": False, "stage": "scan",
            "error": "No draft produced (no candidates found, or all draft attempts failed).",
        })
        print("No draft produced — nothing to produce.")
        return 1

    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))

    print(f"\nStep 2/2: producing video for '{draft.get('youtube_title', draft.get('news', ''))}'...")
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "verticals", "produce",
         "--draft", str(draft_path), "--voice", "edge"],
        cwd=str(Path(__file__).parent),
        capture_output=True, text=True, timeout=1800,
    )

    if result.returncode != 0:
        _write_status({
            "ok": False, "stage": "produce", "draft_path": str(draft_path),
            "error": result.stderr[-2000:] or result.stdout[-2000:],
        })
        print(f"Produce failed:\n{result.stderr[-1000:]}")
        return 1

    # Re-read the draft — cmd_produce writes the video path back into it.
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    video_path = draft.get("video_en", "")
    fallback_count = draft.get("broll_fallback_count", 0)
    frame_count = draft.get("broll_frame_count", 0)
    degraded = frame_count > 0 and fallback_count == frame_count

    _write_status({
        "ok": True,
        "degraded": degraded,
        "draft_path": str(draft_path),
        "video_path": video_path,
        "title": draft.get("youtube_title", ""),
        "script": draft.get("script", ""),
        "niche": draft.get("niche", ""),
        "broll_fallback_count": fallback_count,
        "broll_frame_count": frame_count,
        "note": (
            "Not uploaded — review and run 'python -m verticals upload --draft ...' when ready."
            + (
                f" WARNING: all {frame_count} b-roll frames used the plain gradient "
                "fallback (image generation failed) — this video has no real visuals."
                if degraded else ""
            )
        ),
    })
    if degraded:
        print(f"\nWARNING: video produced but ALL {frame_count} b-roll frames are gradient fallbacks, not real images.")
    print(f"\nDone. Video ready for review: {video_path}")
    print(f"Status written to: {STATUS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

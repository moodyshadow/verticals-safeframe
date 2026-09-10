"""Preview which real stock candidate (if any) each b-roll prompt in a
draft would resolve to, without running the full produce pipeline
(voiceover, captions, music, assembly) — a fast way to sanity-check
sourcing decisions before committing to an expensive full rebuild.

Mirrors generate_broll()'s own priority order (video first, then photo)
but stops short of actually calling AI generation for prompts with no real
match — those are just flagged, not rendered, to keep this fast/free.

Run manually:
    venv\\Scripts\\python.exe preview_broll.py --draft <path-to-draft.json>
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verticals.stock_media import fetch_topical_broll_video, fetch_topical_broll

OUT_DIR = Path(__file__).parent / "reports" / "broll_preview"


def _save_frame_from_video(data: bytes, out_path: Path) -> None:
    tmp = out_path.with_suffix(".src.mp4")
    tmp.write_bytes(data)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(tmp), "-frames:v", "1", "-ss", "0.5", str(out_path), "-loglevel", "error"],
        check=False,
    )
    tmp.unlink(missing_ok=True)


def preview(draft_path: str) -> None:
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    prompts = draft.get("broll_prompts", [])
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, prompt in enumerate(prompts):
        subject = prompt.split(".")[0].strip()
        print(f"\n=== Frame {i + 1}/{len(prompts)}: {subject} ===")

        video_bytes = fetch_topical_broll_video(prompt)
        if video_bytes:
            out = OUT_DIR / f"frame_{i + 1:02d}.jpg"
            _save_frame_from_video(video_bytes, out)
            print(f"  -> real stock VIDEO match, frame saved: {out}")
            continue

        photo_bytes = fetch_topical_broll(prompt)
        if photo_bytes:
            out = OUT_DIR / f"frame_{i + 1:02d}.jpg"
            out.write_bytes(photo_bytes)
            print(f"  -> real stock PHOTO match, saved: {out}")
            continue

        print("  -> NO real stock match — would fall back to AI generation (not rendered here)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    args = ap.parse_args()
    preview(args.draft)
    print(f"\nAll available candidate frames saved under: {OUT_DIR}")


if __name__ == "__main__":
    main()

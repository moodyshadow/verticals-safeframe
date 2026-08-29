"""Failsafe QA: scan produced videos for flat gradient placeholder frames.

Checks actual pixels, not the pipeline's own self-reported metadata — so it
catches gradient frames regardless of how they got there (including videos
made before the no-gradient-fallback fix). A real photo/video frame always
has meaningful edge detail; a flat/gradient placeholder has almost none.

Usage:
    python check_video_quality.py                     # scan all videos in .verticals/media
    python check_video_quality.py path/to/video.mp4    # scan one video
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from verticals.config import MEDIA_DIR

SAMPLES_PER_VIDEO = 12
# Mean within-row pixel std-dev below this = suspected flat/gradient frame.
# Our gradient fallback fills each horizontal row with one solid color (see
# broll.py's old _fallback_frame), so within-row variance is near zero even
# though there's a smooth vertical color transition across rows. Real photo
# or video content always has spatial detail within a row, even in dark or
# blurry scenes. Calibrated against known samples: a gradient frame (with
# caption text burned in) scored ~3.0; real frames — including a dim, blurry
# one — scored 12+. Threshold sits with margin on both sides.
ROW_VARIANCE_THRESHOLD = 6.0


def _get_duration(video_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def _row_variance_score(frame_path: Path) -> float:
    """Mean within-row pixel std-dev — near-zero for flat gradients,
    meaningfully higher for any real photo/video content."""
    img = np.array(Image.open(frame_path).convert("L"), dtype=np.float64)
    return float(img.std(axis=1).mean())


def check_video(video_path: Path) -> dict:
    duration = _get_duration(video_path)
    flagged = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for i in range(SAMPLES_PER_VIDEO):
            t = duration * (i + 0.5) / SAMPLES_PER_VIDEO
            frame_path = tmp_dir / f"f{i}.png"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
                 "-frames:v", "1", "-update", "1", str(frame_path),
                 "-loglevel", "quiet"],
                capture_output=True,
            )
            if not frame_path.exists():
                continue
            score = _row_variance_score(frame_path)
            if score < ROW_VARIANCE_THRESHOLD:
                flagged.append({"timestamp": round(t, 1), "row_variance": round(score, 2)})

    return {
        "video": str(video_path),
        "duration": round(duration, 1),
        "samples_checked": SAMPLES_PER_VIDEO,
        "flagged_frames": flagged,
        "verdict": "FAIL — likely gradient placeholder frame(s)" if flagged else "PASS",
    }


def main():
    if len(sys.argv) > 1:
        targets = [Path(sys.argv[1])]
    else:
        targets = sorted(MEDIA_DIR.glob("*.mp4"))

    if not targets:
        print("No videos found.")
        return 0

    any_failed = False
    for video_path in targets:
        result = check_video(video_path)
        status = "FAIL" if result["flagged_frames"] else "PASS"
        print(f"[{status}] {video_path.name} ({result['duration']}s)")
        for f in result["flagged_frames"]:
            print(f"    suspected gradient frame at {f['timestamp']}s (row variance {f['row_variance']})")
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())

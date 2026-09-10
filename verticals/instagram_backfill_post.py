"""One-off helper: cross-post an already-published draft's video to Instagram.

Used for backfilling videos that went live on YouTube before the Instagram
cross-post step existed in cmd_upload, or that failed it non-fatally. Pulls
the video from PUBLISHED_DIR (the trusted, write-once archive — see the
comment in __main__.py's cmd_upload) rather than MEDIA_DIR scratch space.

Usage: python -m verticals.instagram_backfill_post <job_id>
"""
import json
import sys
from pathlib import Path

from .config import DRAFTS_DIR, PUBLISHED_DIR
from .instagram_upload import upload_to_instagram
from .log import log


def main():
    if len(sys.argv) != 2:
        print("Usage: python -m verticals.instagram_backfill_post <job_id>")
        sys.exit(1)
    job_id = sys.argv[1]

    draft_path = DRAFTS_DIR / f"{job_id}.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))

    if draft.get("instagram_media_id"):
        log(f"{job_id} already posted to Instagram: {draft['instagram_media_id']} — skipping")
        return

    video_path = PUBLISHED_DIR / f"{job_id}_en.mp4"
    if not video_path.exists():
        candidates = list(PUBLISHED_DIR.glob(f"{job_id}*.mp4"))
        if not candidates:
            print(f"No published video found for {job_id} in {PUBLISHED_DIR}")
            sys.exit(1)
        video_path = candidates[0]

    caption = draft.get("instagram_caption") or draft.get("youtube_title", "")
    media_id = upload_to_instagram(video_path, caption, job_id)
    draft["instagram_media_id"] = media_id
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"{job_id} -> Instagram media_id={media_id} (draft updated)")


if __name__ == "__main__":
    main()

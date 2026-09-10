"""Fully local Instagram-backlog poster — no Claude session involved.

Finds published (live-on-YouTube) videos that haven't been cross-posted to
Instagram yet and posts ONE per run, oldest first. Meant to be triggered
hourly by a native Windows Task Scheduler entry so a batch of pending
cross-posts drains itself gradually (no flooding) without needing a Claude
scheduled task per video.

Usage: venv\\Scripts\\python.exe publish_instagram_backlog.py
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

from verticals.config import DRAFTS_DIR, PUBLISHED_DIR, get_youtube_token_path  # noqa: E402
from verticals.instagram_upload import upload_to_instagram  # noqa: E402

LOG_PATH = REPO / "logs" / "publish.log"


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _youtube_client():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from verticals.config import write_secret_file

    token_path = get_youtube_token_path()
    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        write_secret_file(token_path, creds.to_json())
    return build("youtube", "v3", credentials=creds)


def _is_actually_public(youtube, youtube_url: str) -> bool:
    """Instagram's API can't schedule a post — media_publish goes live the
    moment it's called. YouTube uploads here are usually scheduled for a
    future publishAt and stay private until then, so cross-posting as soon
    as a draft has a youtube_url_en put the Instagram post out before the
    video was actually public (caught for real on 2026-09-04, same video
    published a review had asked to hold). Checking the live privacyStatus
    here — not just "does a YouTube URL exist" — is what actually enforces
    "wait until it's public".
    """
    m = re.search(r"(?:youtu\.be/|v=)([\w-]{11})", youtube_url)
    if not m:
        return False
    video_id = m.group(1)
    resp = youtube.videos().list(part="status", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        return False
    return items[0]["status"].get("privacyStatus") == "public"


def main():
    candidates = []
    for path in DRAFTS_DIR.glob("*.json"):
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not draft.get("youtube_url_en"):
            continue
        if draft.get("instagram_media_id"):
            continue
        job_id = draft.get("job_id")
        if not job_id:
            continue
        candidates.append((int(job_id), path, draft))

    if not candidates:
        log("instagram-backlog: nothing pending, all published videos are cross-posted.")
        return

    candidates.sort(key=lambda c: c[0])  # oldest first

    youtube = _youtube_client()
    job_id = draft_path = draft = None
    skipped_not_public = 0
    for cid, cpath, cdraft in candidates:
        if _is_actually_public(youtube, cdraft["youtube_url_en"]):
            job_id, draft_path, draft = cid, cpath, cdraft
            break
        skipped_not_public += 1

    if job_id is None:
        log(f"instagram-backlog: {skipped_not_public} pending video(s) still scheduled/private on YouTube — waiting.")
        return

    video_path = PUBLISHED_DIR / f"{job_id}_en.mp4"
    if not video_path.exists():
        found = list(PUBLISHED_DIR.glob(f"{job_id}*.mp4"))
        if not found:
            log(f"instagram-backlog: no published video file found for {job_id} — skipping.")
            return
        video_path = found[0]

    caption = draft.get("instagram_caption") or draft.get("youtube_title", "")
    try:
        media_id = upload_to_instagram(video_path, caption, str(job_id))
    except Exception as e:
        log(f"instagram-backlog: {job_id} FAILED: {e}")
        return

    draft["instagram_media_id"] = media_id
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    remaining = len(candidates) - 1
    log(f"instagram-backlog: posted {job_id} -> media_id={media_id} ({remaining} still pending)")


if __name__ == "__main__":
    main()

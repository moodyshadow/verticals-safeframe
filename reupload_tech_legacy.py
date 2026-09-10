"""One-off: re-upload the 6 tech videos currently living on the original
(soon-to-be-gaming) channel to the new Overclocked Tech News channel.

YouTube has no video-transfer API, so "moving" a video between channels
means uploading it fresh elsewhere. The originals are left untouched on the
old channel as legacy content — this script only adds, never deletes.

Needs ~/.verticals/youtube_token_tech.json (run
scripts/setup_youtube_oauth_tech.py first, authorizing the NEW channel).

Writes the new channel's video URL back into each draft's
`youtube_url_en_tech_channel` field (a separate field from the original
`youtube_url_en`, which keeps pointing at the legacy upload) and logs
results to logs/reupload_tech_legacy.log.

Usage: venv\\Scripts\\python.exe reupload_tech_legacy.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

from verticals.config import DRAFTS_DIR, PUBLISHED_DIR  # noqa: E402

TECH_TOKEN_PATH = Path.home() / ".verticals" / "youtube_token_tech.json"
LOG_PATH = REPO / "logs" / "reupload_tech_legacy.log"

# The 6 tech job_ids currently on the original channel, per the 2026-09-04
# brand-split decision.
TECH_JOB_IDS = [
    "1787684114",  # The Dark Side of AI Tools
    "1788008597",  # Nvidia's AI Advantage Goes Beyond the GPU
    "1788222392",  # Apple vs OpenAI: New Evidence Revealed
    "1788344949",  # Larry Page's Flying Car Company Pivotal Loses CEO
    "1788385583",  # The AI Ban in NYC Schools
    "1788467146",  # The Cybercab is Tesla's Fork in the Road Moment
]


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_video_file(job_id: str) -> Path | None:
    for pattern in (f"{job_id}_en.mp4", f"{job_id}.mp4"):
        p = PUBLISHED_DIR / pattern
        if p.exists():
            return p
    found = list(PUBLISHED_DIR.glob(f"{job_id}*.mp4"))
    return found[0] if found else None


def find_thumb_file(job_id: str) -> Path | None:
    for pattern in (f"{job_id}_thumb.png", f"{job_id}.jpg", f"{job_id}_thumb.jpg"):
        p = PUBLISHED_DIR / pattern
        if p.exists():
            return p
    return None


def upload_one(youtube, video_path: Path, draft: dict, thumb_path: Path | None) -> str:
    from googleapiclient.http import MediaFileUpload

    description = draft.get("youtube_description", "")
    music_credit = draft.get("music_credit", "")
    if music_credit:
        description = f"{description}\n\nMusic: {music_credit}".strip()

    body = {
        "snippet": {
            "title": draft.get("youtube_title", draft.get("news", ""))[:100],
            "description": description,
            "tags": draft.get("youtube_tags", "").split(","),
            "categoryId": "28",  # Science & Technology
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            log(f"  upload progress: {int(status.progress() * 100)}%")
    video_id = response["id"]

    if thumb_path:
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb_path))).execute()
        except Exception as e:
            log(f"  thumbnail set failed (non-fatal): {e}")

    return f"https://youtu.be/{video_id}"


def main():
    if not TECH_TOKEN_PATH.exists():
        print(f"No tech-channel token at {TECH_TOKEN_PATH}.")
        print("Run: venv\\Scripts\\python.exe scripts\\setup_youtube_oauth_tech.py")
        print("(authorize the NEW Overclocked Tech News channel, not the original one)")
        sys.exit(1)

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TECH_TOKEN_PATH))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TECH_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    youtube = build("youtube", "v3", credentials=creds)

    # Sanity check: confirm this token really points at the new channel, not
    # the original one, before uploading anything — cheap insurance against
    # accidentally duplicating videos onto the wrong channel.
    who = youtube.channels().list(part="snippet", mine=True).execute()
    channel_title = who["items"][0]["snippet"]["title"] if who.get("items") else "UNKNOWN"
    log(f"Authenticated as channel: {channel_title}")
    confirm = input(f"This will upload to '{channel_title}'. Correct channel? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted — re-run setup_youtube_oauth_tech.py against the right channel.")
        sys.exit(1)

    for job_id in TECH_JOB_IDS:
        draft_path = DRAFTS_DIR / f"{job_id}.json"
        if not draft_path.exists():
            log(f"{job_id}: no draft file found — skipping")
            continue
        draft = json.loads(draft_path.read_text(encoding="utf-8"))

        if draft.get("youtube_url_en_tech_channel"):
            log(f"{job_id}: already re-uploaded -> {draft['youtube_url_en_tech_channel']} — skipping")
            continue

        video_path = find_video_file(job_id)
        if not video_path:
            log(f"{job_id}: no local video file found — skipping")
            continue
        thumb_path = find_thumb_file(job_id)

        log(f"{job_id}: uploading {video_path.name} ({draft.get('youtube_title', '')})...")
        try:
            url = upload_one(youtube, video_path, draft, thumb_path)
        except Exception as e:
            log(f"{job_id}: upload FAILED: {e}")
            continue

        draft["youtube_url_en_tech_channel"] = url
        draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"{job_id}: done -> {url}")

    log("All done.")


if __name__ == "__main__":
    main()

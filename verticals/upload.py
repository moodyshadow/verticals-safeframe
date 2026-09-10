"""YouTube API upload + thumbnail + captions."""

import time as _time
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import get_youtube_token_path, write_secret_file
from .decision_log import log_decision
from .log import log
from .retry import with_retry

# Standing publish schedule (updated 2026-09-04 per user's explicit request
# to move off the US-peak-targeting times to fixed local times: tech 13:00,
# gaming 21:00, Europe/Bucharest). Using zoneinfo (not a fixed UTC offset) so
# this stays correct across the Europe/Bucharest DST transition rather than
# drifting an hour off in winter.
_TIMEZONE = ZoneInfo("Europe/Bucharest")
NICHE_PUBLISH_TIME = {
    "gaming": dtime(21, 0),
    "tech": dtime(13, 0),
}


def _next_occurrence(target_time: dtime, tz: ZoneInfo = _TIMEZONE) -> datetime:
    """Next future datetime (in `tz`) at `target_time` — today if that
    hasn't passed yet (with a few minutes' buffer so we don't schedule a
    "next" time that's actually seconds in the past), tomorrow otherwise."""
    now = datetime.now(tz)
    candidate = datetime.combine(now.date(), target_time, tzinfo=tz)
    if candidate <= now + timedelta(minutes=5):
        candidate += timedelta(days=1)
    return candidate


def _normalize_tags(tags) -> list[str]:
    """Accept youtube_tags stored either as a real list or a comma string."""
    if isinstance(tags, list):
        return [t.strip() for t in tags if t and t.strip()]
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    return []


@with_retry(max_retries=2, base_delay=5.0)
def upload_to_youtube(
    video_path: Path,
    draft: dict,
    srt_path: Path = None,
    lang: str = "en",
    thumbnail_path: Path = None,
    publish_now: bool = False,
) -> str:
    """Upload video to YouTube with metadata, captions, and optional thumbnail."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    token_path = get_youtube_token_path(draft.get("niche"))
    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds.expired:
        if creds.refresh_token:
            creds.refresh(Request())
            write_secret_file(token_path, creds.to_json())
        else:
            raise RuntimeError(
                "YouTube OAuth token is expired and has no refresh token.\n"
                "Re-run: python3 scripts/setup_youtube_oauth.py"
            )

    youtube = build("youtube", "v3", credentials=creds)
    log(f"Uploading {video_path.name}...")

    description = draft.get("youtube_description", "")
    music_credit = draft.get("music_credit", "")
    if music_credit:
        description = f"{description}\n\nMusic: {music_credit}".strip()

    if publish_now:
        status = {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
        log("Publishing immediately as public (publish_now=True).")
    else:
        status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
        target_time = NICHE_PUBLISH_TIME.get(draft.get("niche", ""))
        if target_time:
            publish_at = _next_occurrence(target_time)
            status["publishAt"] = publish_at.isoformat()
            log(f"Scheduling to go public at {publish_at.isoformat()} "
                f"({draft.get('niche')} niche's standing publish time)")

    body = {
        "snippet": {
            "title": (draft.get("youtube_title") or draft.get("news", ""))[:100],
            "description": description,
            "tags": _normalize_tags(draft.get("youtube_tags", "")),
            "categoryId": "20",
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": status,
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            log(f"Upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    url = f"https://youtu.be/{video_id}"
    log(f"Uploaded: {url}")

    try:
        log_decision(
            job_id=draft.get("job_id", ""),
            video_id=video_id,
            niche=draft.get("niche", ""),
            title=body["snippet"]["title"],
            topic=draft.get("news", ""),
            topic_score=draft.get("topic_score"),
            topic_source=draft.get("topic_source", ""),
        )
    except Exception as e:
        # Never let decision-log bookkeeping fail an otherwise-successful upload.
        log(f"Decision log write failed (non-fatal): {e}")

    # Upload SRT if available. YouTube's captions API can 404 with
    # "videoNotFound" for a video that was JUST created via videos.insert —
    # observed on both uploads tonight — because the new video isn't
    # immediately visible to every backend service. A short wait + one
    # retry clears it without needing to fail (or delay) the whole upload.
    if srt_path and srt_path.exists():
        for attempt in range(2):
            try:
                youtube.captions().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "language": lang,
                            "name": lang.upper(),
                            "isDraft": False,
                        }
                    },
                    media_body=MediaFileUpload(str(srt_path), mimetype="application/octet-stream"),
                ).execute()
                log("Captions uploaded.")
                break
            except Exception as e:
                if attempt == 0:
                    log(f"Caption upload failed ({e}) — retrying in 15s "
                        "in case the video isn't indexed yet...")
                    _time.sleep(15)
                else:
                    log(f"Caption upload failed (non-fatal): {e}")

    # Upload thumbnail if available
    if thumbnail_path and thumbnail_path.exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
            ).execute()
            log("Thumbnail uploaded.")
        except Exception as e:
            log(f"Thumbnail upload failed: {e}")

    return url

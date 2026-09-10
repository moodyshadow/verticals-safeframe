"""YouTube Analytics — per-video outcome data for the decision-log feedback loop.

Needs the yt-analytics.readonly scope, which the original OAuth token (upload-
only) doesn't have — re-run scripts/setup_youtube_oauth.py once to re-consent
with the broader scope before this will work.
"""
from datetime import date, timedelta
from pathlib import Path

from .config import get_youtube_token_path, write_secret_file
from .log import log


def _get_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    token_path = get_youtube_token_path()
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
    return creds


def fetch_video_outcome(video_id: str, uploaded_at: date, window_days: int = 7) -> dict:
    """Pull views/retention/subscriber-impact for one video over its first
    `window_days` days. Returns a plain dict — no analytics library object —
    so it serializes straight into the decision log.
    """
    from googleapiclient.discovery import build

    creds = _get_credentials()
    yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)

    start = uploaded_at
    end = min(uploaded_at + timedelta(days=window_days), date.today())

    response = yt_analytics.reports().query(
        ids="channel==MINE",
        startDate=start.isoformat(),
        endDate=end.isoformat(),
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
        dimensions="video",
        filters=f"video=={video_id}",
    ).execute()

    rows = response.get("rows") or []
    if not rows:
        return {
            "views": 0, "estimated_minutes_watched": 0, "average_view_duration_sec": 0,
            "average_view_percentage": 0, "subscribers_gained": 0,
            "window_days": (end - start).days, "note": "no data yet (too new, or zero views)",
        }

    row = rows[0]  # [video_id, views, minutes_watched, avg_duration, avg_pct, subs_gained]
    return {
        "views": row[1],
        "estimated_minutes_watched": row[2],
        "average_view_duration_sec": row[3],
        "average_view_percentage": row[4],
        "subscribers_gained": row[5],
        "window_days": (end - start).days,
    }


def fetch_channel_stats() -> dict:
    """The channel's own current subscriber count, total views, and video
    count, plus its title/description — the basic "what is this product"
    facts a marketing agent needs and can't get from per-video outcome data
    alone. Uses the YouTube Data API (not Analytics), so it works even
    before any per-video outcome has been recorded.
    """
    from googleapiclient.discovery import build

    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    items = response.get("items") or []
    if not items:
        return {}
    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
    }

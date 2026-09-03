"""Cross-platform follower/subscriber counter for a single channel.

Fetches the current YouTube, TikTok, and Instagram follower counts for one
handle by scraping each platform's public profile page (no API keys
required), then appends a snapshot to a local history file so counts can be
tracked over time.

Scraping unofficial pages is inherently brittle — any of the three
platforms can change its page markup and break extraction here. Each
fetch function fails soft (returns None) rather than raising, so one
broken platform never blocks the other two.
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import URLError, HTTPError

from .config import SKILL_DIR

# Override for CI/scheduled runs that want the history file committed to a
# repo path (e.g. data/channel_counter.jsonl) instead of the user's home dir.
_override = os.environ.get("CHANNEL_COUNTER_HISTORY_PATH")
HISTORY_PATH = Path(_override) if _override else SKILL_DIR / "channel_counter.jsonl"

# Set CHANNEL_COUNTER_DEBUG=1 to print why a fetch failed (HTTP status,
# timeout, DNS error) instead of silently returning None. Off by default
# so a normal run stays quiet.
_DEBUG = os.environ.get("CHANNEL_COUNTER_DEBUG") == "1"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# YouTube used to nest this as {"simpleText": "1.2M subscribers"}; as of
# late 2026 it's a plain string field instead — confirmed via a live debug
# run: `"subscriberCountText":"7 subscribers"`.
_YOUTUBE_SUB_RE = re.compile(r'"subscriberCountText":"([\d.,]+[KMB]?) subscribers?"')
_TIKTOK_FOLLOWER_RE = re.compile(r'"followerCount":(\d+)')
_INSTAGRAM_FOLLOWER_RE = re.compile(r'"edge_followed_by":\{"count":(\d+)\}')
_INSTAGRAM_META_RE = re.compile(
    r'content="([\d.,]+[KMB]?) Followers, [\d.,]+[KMB]? Following'
)


def _fetch(url: str, timeout: float = 10.0) -> str | None:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if _DEBUG:
                print(f"  [debug] GET {url} -> {resp.status}, {len(body)} bytes", file=sys.stderr)
            return body
    except HTTPError as e:
        if _DEBUG:
            print(f"  [debug] GET {url} -> HTTPError {e.code} {e.reason}", file=sys.stderr)
        return None
    except (URLError, TimeoutError, ValueError) as e:
        if _DEBUG:
            print(f"  [debug] GET {url} -> {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _parse_count(text: str) -> int | None:
    """Parse counts like '1.2M', '340K', '12,345' into an int."""
    text = text.strip().replace(",", "")
    multiplier = 1
    if text and text[-1] in "KMB":
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[text[-1]]
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


# Structural markers that confirm we got the real data-bearing page (not a
# consent/login interstitial) even though our specific regex didn't match —
# lets us tell "wrong regex, right page" apart from "wrong page entirely".
_PLATFORM_MARKERS = {
    "youtube": ["ytInitialData", "subscriberCount", "consent.youtube.com"],
    "tiktok": ["SIGI_STATE", "ItemModule", "followerCount", "\"redirect\""],
    "instagram": ["edge_followed_by", "edge_owner_to_timeline_media", "www.instagram.com/accounts/login"],
}


def _debug_no_match(platform: str, html: str) -> None:
    if not _DEBUG:
        return
    found = [m for m in _PLATFORM_MARKERS.get(platform, []) if m.lower() in html.lower()]
    print(
        f"  [debug] {platform}: fetched {len(html)} bytes, pattern didn't match. "
        f"Markers present: {found or '(none of ' + str(_PLATFORM_MARKERS.get(platform, [])) + ')'}",
        file=sys.stderr,
    )
    # Print raw context around the first hit of each found marker so the
    # actual current field name/shape can be read straight out of CI logs
    # instead of guessing at a new regex blind.
    for marker in found:
        idx = html.lower().find(marker.lower())
        start, end = max(0, idx - 60), min(len(html), idx + len(marker) + 140)
        print(f"  [debug] {platform}: context around '{marker}': ...{html[start:end]!r}...", file=sys.stderr)


def fetch_youtube_subscribers(handle: str) -> int | None:
    handle = handle.lstrip("@")
    html = _fetch(f"https://www.youtube.com/@{handle}/about")
    if not html:
        return None
    match = _YOUTUBE_SUB_RE.search(html)
    if not match:
        _debug_no_match("youtube", html)
        return None
    return _parse_count(match.group(1))


def fetch_tiktok_followers(handle: str) -> int | None:
    handle = handle.lstrip("@")
    html = _fetch(f"https://www.tiktok.com/@{handle}")
    if not html:
        return None
    match = _TIKTOK_FOLLOWER_RE.search(html)
    if not match:
        _debug_no_match("tiktok", html)
        return None
    return int(match.group(1))


def fetch_instagram_followers(handle: str) -> int | None:
    handle = handle.lstrip("@")
    html = _fetch(f"https://www.instagram.com/{handle}/")
    if not html:
        return None
    match = _INSTAGRAM_FOLLOWER_RE.search(html)
    if match:
        return int(match.group(1))
    match = _INSTAGRAM_META_RE.search(html)
    if match:
        return _parse_count(match.group(1))
    _debug_no_match("instagram", html)
    return None


_YOUTUBE_VIDEO_ID_RE = re.compile(r'"videoId":"([\w-]{11})"')
_YOUTUBE_VIEWCOUNT_RE = re.compile(r'"viewCount":"(\d+)"')
_YOUTUBE_TITLE_RE = re.compile(r'"title":\{"simpleText":"(.*?)"\}')
_YOUTUBE_LIKES_RE = re.compile(r'"label":"([\d.,]+[KMB]?) likes?"')

_TIKTOK_ITEM_RE = re.compile(
    r'"id":"(\d+)"[^}]*?"desc":"(.*?)".*?"diggCount":(\d+),"shareCount":(\d+),'
    r'"commentCount":(\d+),"playCount":(\d+)'
)

_INSTAGRAM_POST_RE = re.compile(
    r'"shortcode":"([\w-]+)".*?"edge_liked_by":\{"count":(\d+)\}.*?'
    r'"edge_media_to_comment":\{"count":(\d+)\}',
    re.DOTALL,
)


def fetch_youtube_recent_videos(handle: str, limit: int = 5) -> list[dict]:
    """Views come straight from `videoDetails.viewCount` (exact int). Likes
    are read from a toggle button's accessibility label ("12,345 likes"),
    which YouTube may render abbreviated or omit — best effort only.
    """
    handle = handle.lstrip("@")
    listing_html = _fetch(f"https://www.youtube.com/@{handle}/videos")
    if not listing_html:
        return []

    seen_ids = []
    for vid in _YOUTUBE_VIDEO_ID_RE.findall(listing_html):
        if vid not in seen_ids:
            seen_ids.append(vid)
        if len(seen_ids) >= limit:
            break

    videos = []
    for vid in seen_ids:
        watch_html = _fetch(f"https://www.youtube.com/watch?v={vid}")
        if not watch_html:
            videos.append({"video_id": vid, "title": None, "views": None, "likes": None})
            continue

        views_match = _YOUTUBE_VIEWCOUNT_RE.search(watch_html)
        title_match = _YOUTUBE_TITLE_RE.search(watch_html)
        likes_match = _YOUTUBE_LIKES_RE.search(watch_html)

        videos.append({
            "video_id": vid,
            "title": title_match.group(1) if title_match else None,
            "views": int(views_match.group(1)) if views_match else None,
            "likes": _parse_count(likes_match.group(1)) if likes_match else None,
        })

    return videos


def fetch_tiktok_recent_videos(handle: str, limit: int = 5) -> list[dict]:
    """Parses the ItemModule block TikTok embeds in the profile page — one
    entry per recent video with its view/like/comment/share counts already
    attached, so this needs only the one profile-page request.
    """
    handle = handle.lstrip("@")
    html = _fetch(f"https://www.tiktok.com/@{handle}")
    if not html:
        return []

    videos = []
    for match in _TIKTOK_ITEM_RE.finditer(html):
        video_id, desc, likes, shares, comments, views = match.groups()
        videos.append({
            "video_id": video_id,
            "title": desc,
            "views": int(views),
            "likes": int(likes),
            "comments": int(comments),
            "shares": int(shares),
        })
        if len(videos) >= limit:
            break

    return videos


def fetch_instagram_recent_posts(handle: str, limit: int = 5) -> list[dict]:
    """Instagram's anonymous profile HTML rarely embeds full post data
    anymore (most traffic gets redirected to a login wall), so this
    frequently returns an empty list — that's expected, not a bug.
    """
    handle = handle.lstrip("@")
    html = _fetch(f"https://www.instagram.com/{handle}/")
    if not html:
        return []

    posts = []
    for match in _INSTAGRAM_POST_RE.finditer(html):
        shortcode, likes, comments = match.groups()
        posts.append({
            "shortcode": shortcode,
            "likes": int(likes),
            "comments": int(comments),
            "url": f"https://www.instagram.com/p/{shortcode}/",
        })
        if len(posts) >= limit:
            break

    return posts


VIDEO_FETCHERS = {
    "youtube": fetch_youtube_recent_videos,
    "tiktok": fetch_tiktok_recent_videos,
    "instagram": fetch_instagram_recent_posts,
}


PLATFORM_FETCHERS = {
    "youtube": fetch_youtube_subscribers,
    "tiktok": fetch_tiktok_followers,
    "instagram": fetch_instagram_followers,
}


def snapshot(handle: str, handles: dict[str, str] | None = None) -> dict:
    """Fetch current counts for `handle` on all 3 platforms and record it.

    `handles` optionally overrides the handle used per platform, for
    channels that use a different @name on each platform, e.g.
    {"youtube": "dailyoverclocked", "tiktok": "dailyoverclockedtv"}.
    """
    handles = handles or {}
    counts = {
        "youtube": fetch_youtube_subscribers(handles.get("youtube", handle)),
        "tiktok": fetch_tiktok_followers(handles.get("tiktok", handle)),
        "instagram": fetch_instagram_followers(handles.get("instagram", handle)),
    }

    known = [c for c in counts.values() if c is not None]
    record = {
        "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "handle": handle,
        "counts": counts,
        "total": sum(known) if known else None,
    }

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return record


def load_history(handle: str | None = None) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    records = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if handle is None or record.get("handle") == handle:
            records.append(record)
    return records

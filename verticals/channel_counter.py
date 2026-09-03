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
import time
import urllib.request
from pathlib import Path
from urllib.error import URLError, HTTPError

from .config import SKILL_DIR

# Override for CI/scheduled runs that want the history file committed to a
# repo path (e.g. data/channel_counter.jsonl) instead of the user's home dir.
_override = os.environ.get("CHANNEL_COUNTER_HISTORY_PATH")
HISTORY_PATH = Path(_override) if _override else SKILL_DIR / "channel_counter.jsonl"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

_YOUTUBE_SUB_RE = re.compile(r'"subscriberCountText":\{"simpleText":"([\d.,KMB]+)')
_TIKTOK_FOLLOWER_RE = re.compile(r'"followerCount":(\d+)')
_INSTAGRAM_FOLLOWER_RE = re.compile(r'"edge_followed_by":\{"count":(\d+)\}')
_INSTAGRAM_META_RE = re.compile(
    r'content="([\d.,]+[KMB]?) Followers, [\d.,]+[KMB]? Following'
)


def _fetch(url: str, timeout: float = 10.0) -> str | None:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError, TimeoutError, ValueError):
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


def fetch_youtube_subscribers(handle: str) -> int | None:
    handle = handle.lstrip("@")
    html = _fetch(f"https://www.youtube.com/@{handle}/about")
    if not html:
        return None
    match = _YOUTUBE_SUB_RE.search(html)
    if not match:
        return None
    return _parse_count(match.group(1).replace(" subscribers", ""))


def fetch_tiktok_followers(handle: str) -> int | None:
    handle = handle.lstrip("@")
    html = _fetch(f"https://www.tiktok.com/@{handle}")
    if not html:
        return None
    match = _TIKTOK_FOLLOWER_RE.search(html)
    if not match:
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
    return None


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

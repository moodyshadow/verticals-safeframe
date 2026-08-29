"""Performance tracking — phase 1 of the marketing-learning helper.

Logs metadata for every video produced/uploaded so that, once videos are
public and have real view/engagement data, a later phase can correlate
performance against topic/title/niche/timing choices. Right now this is
pure data collection — there is nothing to "learn" from until videos
have run long enough to accumulate real analytics.
"""
import json
import time
from pathlib import Path

from .config import SKILL_DIR

LOG_PATH = SKILL_DIR / "performance_log.jsonl"


def log_video_metadata(draft: dict, video_url: str, lang: str = "en") -> None:
    """Append one record for a produced/uploaded video.

    Fields are chosen so a future analytics-correlation pass has enough
    to work with: what topic/niche/score drove the pick, what title/tags
    were used, and exactly when it went up (posting time matters for
    view velocity comparisons).
    """
    record = {
        "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "job_id": draft.get("job_id", ""),
        "video_url": video_url,
        "lang": lang,
        "niche": draft.get("niche", ""),
        "topic": draft.get("news", ""),
        "topic_score": draft.get("topic_score"),
        "youtube_title": draft.get("youtube_title", ""),
        "youtube_title_length": len(draft.get("youtube_title", "")),
        "youtube_tags": draft.get("youtube_tags", ""),
        "script_word_count": len(draft.get("script", "").split()),
        "platform": draft.get("platform", "shorts"),
        # Filled in by a later phase once analytics are available.
        "views": None,
        "ctr": None,
        "avg_view_duration_sec": None,
        "likes": None,
        "analytics_updated_at": None,
    }
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_all_records() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    records = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records

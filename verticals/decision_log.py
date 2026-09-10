"""Decision log — records what was made and why, then gets enriched with real
outcomes once analytics are available. The point isn't a list of past videos;
it's a growing dataset that lets the marketing helper learn what actually
works instead of guessing fresh every time.

One line per video in ~/.verticals/decision_log.jsonl (append-only). Each
entry starts as a "decision" (topic, niche, score, title) at upload time and
later gets an "outcome" merged in once track_performance.py pulls analytics.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import SKILL_DIR

LOG_PATH = SKILL_DIR / "decision_log.jsonl"


def log_decision(
    job_id: str,
    video_id: str,
    niche: str,
    title: str,
    topic: str = "",
    topic_score: float | None = None,
    topic_source: str = "",
) -> None:
    """Record a video the moment it's uploaded. Called from upload.py."""
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "job_id": job_id,
        "video_id": video_id,
        "niche": niche,
        "title": title,
        "topic": topic,
        "topic_score": topic_score,
        "topic_source": topic_source,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "outcome": None,  # filled in later by track_performance.py
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_all() -> list[dict]:
    """Read every logged decision, oldest first."""
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def pending_outcomes(min_age_days: float = 7.0) -> list[dict]:
    """Entries old enough to have a meaningful 7-day view count but without
    an outcome recorded yet."""
    now = datetime.now(timezone.utc)
    pending = []
    for entry in read_all():
        if entry.get("outcome") is not None:
            continue
        try:
            uploaded = datetime.fromisoformat(entry["uploaded_at"])
        except (KeyError, ValueError):
            continue
        age_days = (now - uploaded).total_seconds() / 86400
        if age_days >= min_age_days:
            pending.append(entry)
    return pending


def record_outcome(job_id: str, outcome: dict) -> None:
    """Rewrite the log with `outcome` merged into the matching entry.

    The log is small (one line per video) and append-only by convention, but
    updating a single entry means rewriting the file — fine at this scale,
    and keeps the format simple (no separate index/database to keep in sync).
    """
    entries = read_all()
    updated = False
    for entry in entries:
        if entry["job_id"] == job_id:
            entry["outcome"] = outcome
            entry["outcome_recorded_at"] = datetime.now(timezone.utc).isoformat()
            updated = True
            break
    if not updated:
        return
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

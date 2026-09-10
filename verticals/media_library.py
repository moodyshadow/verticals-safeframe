"""Persistent local library of real, freely-licensed stock media.

Every photo/video that passes the relevance check in stock_media.py gets
cached here (file + keyword index) instead of being used once and thrown
away. Future b-roll searches check this library *before* hitting an
external API — same relevance-matching logic as a fresh search, just
against media we already fetched and already know is legitimately
licensed for reuse (Pexels' free-to-use terms don't expire or become
invalid just because the file now lives on disk instead of on Pexels'
servers).

This cuts external API calls over time (rate limits, latency) and builds
up a growing archive of exactly the kind of generic, frequently-reused
footage (crowds, controllers, server rooms, stages) that keeps coming up
across many different videos/niches.
"""

import hashlib
import re
import sqlite3
import time
from pathlib import Path

from .log import log

LIBRARY_DIR = Path("D:/verticals_media_library")


def word_boundary_match(term: str, text: str) -> bool:
    """Whether `term` (a single word or multi-word phrase) appears in `text`
    as whole word(s), not as a substring buried inside some other unrelated
    word. A real, found bug: the short keyword "wide" plain-substring-matched
    inside the unrelated Pixabay tag "world wide", contributing a false
    relevance point that — combined with one genuine "tournament" tag hit —
    cleared the match threshold and served a completely unrelated Brazil/
    football video as "relevant" b-roll for a "gaming tournament arena"
    prompt. Used everywhere keyword/anchor relevance gets checked against a
    result's own text, not just here.
    """
    return re.search(r"\b" + re.escape(term) + r"\b", text) is not None
PHOTOS_DIR = LIBRARY_DIR / "photos"
VIDEOS_DIR = LIBRARY_DIR / "videos"
DB_PATH = LIBRARY_DIR / "index.db"

_EXT = {"photo": ".jpg", "video": ".mp4"}


REUSE_COOLDOWN_SECONDS = 7 * 24 * 3600  # never reuse the same asset within a week


def _connect() -> sqlite3.Connection:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    PHOTOS_DIR.mkdir(exist_ok=True)
    VIDEOS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type TEXT NOT NULL,
            keywords TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT,
            local_path TEXT NOT NULL UNIQUE,
            added_at REAL NOT NULL,
            times_used INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    try:
        conn.execute("ALTER TABLE assets ADD COLUMN last_used_at REAL")
    except sqlite3.OperationalError:
        pass  # column already exists from a previous run
    return conn


def asset_path_for(data: bytes, media_type: str) -> Path:
    """The content-hashed local path a piece of data would be stored at —
    shared by add_to_library() and the caller-side duplicate check in
    stock_media.py, so both agree on the same path without re-deriving the
    hashing scheme twice."""
    ext = _EXT.get(media_type, "")
    digest = hashlib.sha256(data).hexdigest()[:24]
    subdir = PHOTOS_DIR if media_type == "photo" else VIDEOS_DIR
    return subdir / f"{digest}{ext}"


def add_to_library(
    data: bytes, keywords: list[str], media_type: str, source: str, source_url: str = "",
) -> Path:
    """Save a fetched asset into the library and index it by keywords.

    Content-hashed filename means re-fetching the same asset (e.g. two
    prompts independently matching the same Pexels photo) is a harmless
    no-op rather than a duplicate file.
    """
    local_path = asset_path_for(data, media_type)
    if not local_path.exists():
        local_path.write_bytes(data)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO assets
                (media_type, keywords, source, source_url, local_path, added_at, times_used)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (media_type, " ".join(keywords), source, source_url, str(local_path), time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return local_path


def find_in_library(
    keywords: list[str], media_type: str, min_matches: int = 1, exclude_paths: set[str] | None = None,
    allow_cooldown_fallback: bool = True, anchors: set[str] | None = None,
) -> tuple[bytes, bool] | None:
    """Check the local library for an asset matching `keywords` before
    hitting an external API. Same overlap-counting relevance rule as a
    fresh stock search (see stock_media._is_relevant) — requires at least
    `min_matches` (clamped to however many keywords exist) independent hits
    against the asset's own indexed keywords, not just one incidental word.

    Hard rule: an asset used in an actual published video within the last
    `REUSE_COOLDOWN_SECONDS` (7 days) is excluded outright, not just
    de-prioritized — found after the same handful of cached clips kept
    turning up across videos made only a day or two apart. Among the
    remaining (cooldown-cleared) candidates, prefers the least-used one.
    "Used" specifically means aired: `last_used_at`/`times_used` are only
    stamped by mark_used(), called once a job's video is confirmed actually
    public on YouTube (see cleanup_published.py) — never here at generation
    time. A video that gets discarded, redone, or never makes it past
    review/upload leaves its content's cooldown untouched, free to be
    picked again by the next job.

    If literally nothing qualifies within the cooldown: with
    `allow_cooldown_fallback=True` (the default), falls back to the single
    least-recently-used match anyway (never producing zero b-roll) and
    flags it so the caller visually differentiates it (e.g. a horizontal
    mirror flip); with `allow_cooldown_fallback=False`, returns None
    instead so the caller tries a fresh live API search first. Callers
    should do exactly that: call once with fallback disabled, attempt a
    live search on a miss, and only call again with fallback enabled (the
    default) as the last resort before giving up — reusing a week-old clip
    should never preempt a genuinely fresh live search that might succeed.

    `exclude_paths` hard-excludes specific assets regardless of how well
    they'd otherwise score — added after two different b-roll prompts in
    the *same* video ("a prominent Apple logo" and "a MacBook with the
    Apple logo visible") both matched the identical cached clip, so the
    same footage played twice in one video. generate_broll() passes in
    every asset already used earlier in the current job.

    `anchors` (see stock_media._anchor_keywords) requires at least one
    genuinely distinctive proper-noun/acronym term from the query to appear
    in a candidate's own stored keywords — the same guard live searches
    already apply via `_has_anchor()`. Without it here too, a totally
    unrelated cached clip (e.g. a racing-game controller close-up, indexed
    under generic words like "controller"/"screen"/"close-up") could match
    a prompt naming a completely different specific game ("Resident Evil 2")
    purely on those generic overlaps — found after a gaming video's b-roll
    turned out to be five unrelated clips because the *last-resort*
    stale-reuse fallback below bypassed the anchor check a fresh live
    search would have enforced, repeatedly reusing the same irrelevant
    cached clip instead of falling through to real AI generation.

    Returns (asset_bytes, needs_visual_variation) or None if nothing in
    the library matches the keywords at all.
    """
    if not keywords:
        return None
    exclude_paths = exclude_paths or set()
    now = time.time()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, keywords, local_path, times_used, last_used_at FROM assets WHERE media_type = ?",
            (media_type,),
        ).fetchall()

        required = min(min_matches, len(keywords))
        best_fresh = None  # cooldown-cleared candidates: (matches, -times_used) key
        best_any = None    # best candidate regardless of cooldown, as a last resort
        for row_id, kw_text, local_path, times_used, last_used_at in rows:
            if local_path in exclude_paths:
                continue
            stored = set(kw_text.split())
            matches = sum(1 for kw in keywords if kw in stored)
            if matches < required:
                continue
            # anchors may be multi-word phrases ("resident evil") from
            # adjacent proper-noun runs — checked as a word-boundary match
            # against the joined keyword text (not set-membership against
            # individual space-split tokens, so a phrase anchor can actually
            # match; not plain substring either, which let short anchor
            # words false-match inside unrelated longer words).
            if anchors and not any(word_boundary_match(a, kw_text) for a in anchors):
                continue

            key = (matches, -times_used)
            if best_any is None or key > best_any[0]:
                best_any = (key, row_id, local_path, last_used_at)

            on_cooldown = last_used_at is not None and (now - last_used_at) < REUSE_COOLDOWN_SECONDS
            if not on_cooldown:
                if best_fresh is None or key > best_fresh[0]:
                    best_fresh = (key, row_id, local_path, last_used_at)

        if best_fresh is None and not allow_cooldown_fallback:
            return None
        chosen = best_fresh or best_any
        if chosen is None:
            return None
        needs_variation = chosen is best_any and best_fresh is None

        _, row_id, local_path, _ = chosen
        path = Path(local_path)
        if not path.exists():
            # File was moved/deleted out from under the index — drop the
            # stale row so it stops being considered, then fall through to
            # a fresh external search.
            conn.execute("DELETE FROM assets WHERE id = ?", (row_id,))
            conn.commit()
            return None

        # Deliberately does NOT stamp times_used/last_used_at here anymore —
        # see mark_used() below. A produced video can still be discarded,
        # redone, or fail review/upload; content that never actually aired
        # shouldn't burn its own reuse cooldown just for being picked during
        # generation.
        if needs_variation:
            log(f"Media library hit: {path.name} ({media_type}) — every match is inside the "
                f"7-day reuse cooldown, using the least-recent one with a visual variation applied")
        else:
            log(f"Media library hit: {path.name} ({media_type})")
        return path.read_bytes(), needs_variation
    finally:
        conn.close()


def mark_used(local_path: str) -> None:
    """Record that this library asset was actually used in a video that has
    now been confirmed live on YouTube — advances its 7-day reuse cooldown
    clock and its times_used count.

    Called from cleanup_published.py, once a job's real YouTube privacy
    status comes back "public" (the same authoritative "did this actually
    go live" check the published-video-lock rule already relies on), using
    the exact library paths generate_broll() recorded in each draft's
    broll.artifacts.used_asset_paths — not a fresh content-hash lookup,
    since a stock photo's on-disk frame gets resized/cropped for the final
    video and no longer hashes to the same bytes as the original library
    copy.

    A no-op if `local_path` isn't a known library asset (e.g. a pure
    AI-generated frame that was never sourced from the library) — nothing
    to advance a cooldown on.
    """
    conn = _connect()
    try:
        conn.execute(
            "UPDATE assets SET times_used = times_used + 1, last_used_at = ? WHERE local_path = ?",
            (time.time(), local_path),
        )
        conn.commit()
    finally:
        conn.close()


def is_on_cooldown(data: bytes, media_type: str) -> bool:
    """Whether this exact asset (by content hash) is already in the library
    and was used within the last `REUSE_COOLDOWN_SECONDS`.

    Needed because the 7-day cooldown was only ever enforced on the
    find_in_library() cache-lookup path — a *live* API search was always
    treated as "fresh" just because the search call itself was new. That
    missed a real recurring case: a generic query (e.g. "circuit board
    macro") gets the same stable top-ranked result from Pexels every time,
    so a live search kept re-returning the identical clip that was already
    sitting in the library on cooldown, only 7 hours after its last use.
    Callers that accept a live search result (stock_media.py's
    _download_and_cache and the single-provider search_* functions) must
    check this before using it, so a live hit that turns out to be the same
    cooldown-protected asset gets treated like a miss — falling through to
    the next candidate, and ultimately to the same mirror-flipped
    last-resort path a cache-only miss would use.
    """
    path = asset_path_for(data, media_type)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT last_used_at FROM assets WHERE local_path = ?", (str(path),),
        ).fetchone()
        if not row or row[0] is None:
            return False
        return (time.time() - row[0]) < REUSE_COOLDOWN_SECONDS
    finally:
        conn.close()


def library_stats() -> dict:
    """Quick counts for visibility into how the library is growing."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT media_type, COUNT(*), SUM(times_used) FROM assets GROUP BY media_type"
        ).fetchall()
        return {media_type: {"count": count, "reuses": reuses or 0} for media_type, count, reuses in rows}
    finally:
        conn.close()

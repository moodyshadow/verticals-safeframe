"""TopicEngine — orchestrates multi-source discovery + local auto-pick."""

import concurrent.futures

from ..config import load_config, NICHE_TO_SUBREDDITS, NICHE_TO_RSS_FEEDS
from ..log import log
from .base import TopicCandidate

# A dedicated CPU-only marketing-helper Ollama instance on its own port was
# planned (to avoid contending with the video pipeline's own GPU-adjacent
# Ollama) but verticals/marketing_llm.py that would launch it was never
# actually created — pointing this at the main pipeline Ollama instance
# instead. auto_pick's prompt is small (~20 ranked candidates, not a full
# script), so sharing the instance is fine.
MARKETING_OLLAMA_HOST = "http://127.0.0.1:11434"
MARKETING_MODEL = "qwen2.5:14b-instruct"


class TopicEngine:
    """Fetches from all enabled sources, deduplicates, ranks."""

    def __init__(self, niche: str = "general"):
        self._niche = niche or "general"
        self._sources = []
        self._load_sources()

    def _load_sources(self):
        """Load enabled topic sources from config.

        When a niche is set, subreddit and NewsAPI query defaults are overridden
        with niche-appropriate values (user config.json can still override).
        """
        config = load_config()
        source_config = config.get("topic_sources", {})

        # Always register these — they'll check their own enabled status
        from .reddit import RedditSource
        from .rss import RSSSource
        from .google_trends import GoogleTrendsSource

        source_map = {
            "reddit": RedditSource,
            "rss": RSSSource,
            "google_trends": GoogleTrendsSource,
        }

        # Optional sources
        try:
            from .newsapi import NewsAPISource
            source_map["newsapi"] = NewsAPISource
        except ImportError:
            pass

        try:
            from .twitter import TwitterSource
            source_map["twitter"] = TwitterSource
        except ImportError:
            pass

        try:
            from .tiktok import TikTokSource
            source_map["tiktok"] = TikTokSource
        except ImportError:
            pass

        for name, cls in source_map.items():
            src_cfg = dict(source_config.get(name, {}))  # shallow copy so we can mutate

            # Apply niche defaults when no explicit config is set by user
            if self._niche != "general":
                if name == "reddit" and "subreddits" not in src_cfg:
                    niche_subs = NICHE_TO_SUBREDDITS.get(self._niche, [])
                    if niche_subs:
                        src_cfg["subreddits"] = niche_subs
                if name == "rss" and "feeds" not in src_cfg:
                    niche_feeds = NICHE_TO_RSS_FEEDS.get(self._niche, [])
                    if niche_feeds:
                        src_cfg["feeds"] = niche_feeds
                if name == "newsapi":
                    src_cfg.setdefault("niche", self._niche)

            # NewsAPI enabled if key is present (checked by is_available); others default on/off
            default_enabled = name in ("reddit", "rss", "google_trends", "newsapi")
            if src_cfg.get("enabled", default_enabled):
                try:
                    self._sources.append(cls(src_cfg))
                except Exception as e:
                    log(f"Failed to init source {name}: {e}")

    def discover(self, limit: int = 15) -> list[TopicCandidate]:
        """Fetch from all sources in parallel, deduplicate, rank."""
        all_topics = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(src.fetch_topics, limit): src
                for src in self._sources if src.is_available
            }
            for future in concurrent.futures.as_completed(futures):
                src = futures[future]
                try:
                    topics = future.result(timeout=15)
                    all_topics.extend(topics)
                    log(f"{src.name}: found {len(topics)} topics")
                except Exception as e:
                    log(f"{src.name}: failed — {e}")

        # Deduplicate by fuzzy title matching
        seen = set()
        unique = []
        for t in all_topics:
            key = t.title.lower().strip()[:50]
            if key not in seen:
                seen.add(key)
                unique.append(t)

        # Sort by trending score (highest first)
        unique.sort(key=lambda t: t.trending_score, reverse=True)
        return unique[:limit]

    def _recent_titles(self, days: float = 5.0) -> list[str]:
        """Titles of this niche's own videos *produced* in the last few
        days (not just uploaded — decision_log.jsonl only logs at upload
        time, and with a human-review-before-upload gate most produced
        drafts never reach it at all, so it has zero memory of e.g. three
        different Pokémon stories produced back to back while none of them
        had been uploaded yet). Scanning drafts directly is what actually
        reflects what auto_pick has been choosing, regardless of whether a
        human has gotten around to publishing it.
        """
        import json
        import time
        from ..config import DRAFTS_DIR

        cutoff = time.time() - days * 86400
        titles = []
        for path in DRAFTS_DIR.glob("*.json"):
            try:
                job_id = float(path.stem)
            except ValueError:
                continue
            if job_id < cutoff:
                continue
            try:
                draft = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if draft.get("niche") != self._niche:
                continue
            title = draft.get("youtube_title") or draft.get("news", "")
            if title:
                titles.append(title)
        return titles

    def _real_outcome_summary(self, min_videos: int = 3, top_n: int = 8) -> str:
        """Build the auto_pick prompt's performance-data block from real
        recorded outcomes (decision_log.jsonl, filled in by
        track_performance.py against actual YouTube Analytics) instead of a
        one-time hardcoded snapshot that never updates as more data comes
        in. Ranked, not interpreted — the model draws its own conclusion
        about what's working fresh each call, since the pattern is free to
        change as the channel accumulates more real results.
        """
        from ..decision_log import read_all

        entries = [e for e in read_all() if e.get("outcome") and e["outcome"].get("views") is not None]
        if len(entries) < min_videos:
            return ""
        entries.sort(key=lambda e: e["outcome"].get("views", 0), reverse=True)
        total_views = sum(e["outcome"].get("views", 0) for e in entries)

        lines = [
            f"REAL OUTCOME DATA from this channel ({len(entries)} videos with recorded "
            f"outcomes, {total_views} total views) — ranked by actual views, best first:"
        ]
        for e in entries[:top_n]:
            views = e["outcome"].get("views", 0)
            pct = e["outcome"].get("average_view_percentage", 0) or 0
            lines.append(f"  - {views} views, {pct:.0f}% avg watched, {e['niche']}: \"{e['title']}\"")
        lines.append(
            "Look for what the best performers actually have in common (a "
            "recognizable name/brand, a type of story, timing, anything) and "
            "weigh that pattern above a purely trending-score-driven pick."
        )
        return "\n".join(lines)

    def auto_pick(self, candidates: list[TopicCandidate]) -> str:
        """Use the local marketing-helper model to pick the best topic for a
        YouTube Short. Runs on the dedicated CPU-only Ollama instance (see
        MARKETING_OLLAMA_HOST) — picking the best of ~20 pre-ranked candidates
        is a much easier task than full script generation, well within a
        14B model's range, and keeps this step at $0 like the rest of the
        pipeline instead of a paid Claude API call.
        """
        import requests

        topics_text = "\n".join(
            f"{i+1}. [{t.source}] {t.title} (score: {t.trending_score:.2f})"
            for i, t in enumerate(candidates[:20])
        )

        recent = self._recent_titles()
        recent_block = ""
        if recent:
            recent_list = "\n".join(f"- {t}" for t in recent)
            recent_block = f"""

ALREADY COVERED IN THE LAST FEW DAYS on this channel (avoid picking the same
franchise/subject again unless the new story is genuinely a different,
must-cover event — being the top-scoring topic today isn't enough on its
own if it's the same subject as one of these):
{recent_list}"""

        outcome_block = self._real_outcome_summary()
        if not outcome_block:
            outcome_block = (
                "No real outcome data recorded yet for this channel — weigh "
                "trending score, visual potential, timeliness, and "
                "controversy/surprise factor, with a preference for topics "
                "carrying a mainstream, recognizable name or brand a non-fan "
                "would still recognize."
            )

        prompt = f"""Pick the single best topic from this list for a viral YouTube Short (60-90 sec).

{outcome_block}

{topics_text}{recent_block}

Reply with ONLY the topic title text, nothing else."""

        r = requests.post(
            f"{MARKETING_OLLAMA_HOST}/api/generate",
            json={"model": MARKETING_MODEL, "prompt": prompt, "stream": False},
            # CPU-only inference on this machine runs prompt eval at only
            # ~2 tok/s (verified directly) — a real ~20-candidate topic list
            # is 400-600+ prompt tokens, so 120s was cutting it off mid-eval
            # even when the instance was perfectly healthy, not hung.
            timeout=420,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Marketing-helper Ollama {r.status_code}: {r.text[:200]} — "
                f"is it running at {MARKETING_OLLAMA_HOST}?"
            )
        return r.json().get("response", "").strip()

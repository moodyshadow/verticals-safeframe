"""RSS/Atom feed topic source."""

import time

from .base import TopicCandidate, TopicSource


class RSSSource(TopicSource):
    name = "rss"

    def __init__(self, config: dict = None):
        config = config or {}
        self.feeds = config.get("feeds", ["https://hnrss.org/frontpage"])

    @property
    def is_available(self) -> bool:
        try:
            import feedparser  # noqa: F401
            return True
        except ImportError:
            return False

    def _recency_score(self, entry) -> float:
        """Score by freshness: <=1h old = 1.0, decaying to a 0.15 floor by ~48h.

        RSS has no popularity metric, so recency is the best proxy we have —
        a story an hour old is more "trending" for our purposes than one
        from three days ago sitting in the same feed.
        """
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if not published:
            return 0.5  # no timestamp available — neutral default
        age_hours = max(0.0, (time.time() - time.mktime(published)) / 3600)
        score = 1.0 * (0.5 ** (age_hours / 12))  # halve every 12h
        return max(0.15, min(1.0, score))

    def fetch_topics(self, limit: int = 10) -> list[TopicCandidate]:
        import feedparser

        topics = []
        per_feed = max(1, limit // len(self.feeds))

        for feed_url in self.feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:per_feed]:
                    topics.append(TopicCandidate(
                        title=entry.get("title", ""),
                        source=f"rss/{feed.feed.get('title', feed_url)[:30]}",
                        trending_score=self._recency_score(entry),
                        summary=entry.get("summary", "")[:200],
                        url=entry.get("link", ""),
                    ))
            except Exception:
                continue

        topics.sort(key=lambda t: t.trending_score, reverse=True)
        return topics[:limit]

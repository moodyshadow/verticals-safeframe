"""Reddit API topic source (hot/trending) via OAuth app-only auth.

Reddit's unauthenticated .json endpoints now redirect to a login wall for
most traffic, so this uses the "script" app client_credentials grant —
read-only access to public listings, no user login required, just a free
app registration at https://reddit.com/prefs/apps.
"""

import time

import requests

from ..config import get_reddit_credentials
from .base import TopicCandidate, TopicSource

# Required format per Reddit's Data API rules:
# <platform>:<app ID>:<version string> (by /u/<reddit username>)
USER_AGENT = "windows:dailyoverclocked-verticals:v1.0.0 (by /u/dailyoverclocked)"

_TOKEN_CACHE = {"token": None, "expires_at": 0}


class RedditSource(TopicSource):
    name = "reddit"

    def __init__(self, config: dict = None):
        config = config or {}
        self.subreddits = config.get("subreddits", ["technology", "worldnews"])

    @property
    def is_available(self) -> bool:
        client_id, client_secret = get_reddit_credentials()
        return bool(client_id and client_secret)

    def _get_token(self) -> str:
        if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires_at"]:
            return _TOKEN_CACHE["token"]

        client_id, client_secret = get_reddit_credentials()
        if not client_id or not client_secret:
            raise RuntimeError(
                "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set — create a free "
                "'script' app at https://reddit.com/prefs/apps and add the "
                "credentials to config.json or as environment variables."
            )
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        _TOKEN_CACHE["token"] = data["access_token"]
        _TOKEN_CACHE["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
        return _TOKEN_CACHE["token"]

    def fetch_topics(self, limit: int = 10) -> list[TopicCandidate]:
        topics = []
        per_sub = max(1, limit // len(self.subreddits))

        for sub in self.subreddits:
            try:
                topics.extend(self._fetch_subreddit(sub, per_sub))
            except Exception:
                continue

        return topics[:limit]

    def _fetch_subreddit(self, subreddit: str, limit: int) -> list[TopicCandidate]:
        token = self._get_token()
        url = f"https://oauth.reddit.com/r/{subreddit}/hot"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        }
        r = requests.get(url, headers=headers, params={"limit": limit + 2}, timeout=10)
        r.raise_for_status()
        data = r.json()

        topics = []
        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            if d.get("stickied"):
                continue

            score = d.get("score", 0)
            # Normalize score: 10K+ = 1.0, logarithmic scale
            import math
            normalized = min(1.0, math.log10(max(score, 1)) / 4)

            topics.append(TopicCandidate(
                title=d.get("title", ""),
                source=f"reddit/r/{subreddit}",
                trending_score=normalized,
                summary=d.get("selftext", "")[:200],
                url=f"https://reddit.com{d.get('permalink', '')}",
                metadata={"score": score, "num_comments": d.get("num_comments", 0)},
            ))

        return topics

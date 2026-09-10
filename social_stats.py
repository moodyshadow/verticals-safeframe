"""One-shot fetch of current follower/subscriber/like counts across YouTube,
TikTok, and Instagram — reuses each platform's existing upload-flow auth
(analytics.py's YouTube OAuth, tiktok_upload.py's token refresh,
instagram_upload.py's Meta access token) rather than a separate credential
setup. Prints a single JSON line so status_dashboard.ps1 can parse it.

Each platform is independently try/excepted: a missing scope or an expired
token on one platform (e.g. TikTok's sandbox app may not have been granted
user.info.stats) degrades that platform to an "error" field instead of
taking down the whole dashboard refresh.

Run standalone: venv\\Scripts\\python.exe social_stats.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _youtube_stats() -> dict:
    from verticals.analytics import fetch_channel_stats
    stats = fetch_channel_stats()
    if not stats:
        return {"error": "no channel data returned"}
    return {
        "subscribers": stats["subscriber_count"],
        "views": stats["view_count"],
        "videos": stats["video_count"],
    }


def _tiktok_stats() -> dict:
    from verticals.tiktok_upload import _get_valid_access_token, API_BASE
    import requests
    token = _get_valid_access_token()
    r = requests.get(
        f"{API_BASE}/user/info/",
        params={"fields": "follower_count,likes_count,video_count"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if r.status_code != 200:
        return {"error": f"{r.status_code} {r.text[:150]}"}
    data = r.json().get("data", {}).get("user", {})
    if not data:
        return {"error": r.text[:150]}
    return {
        "followers": data.get("follower_count", 0),
        "likes": data.get("likes_count", 0),
        "videos": data.get("video_count", 0),
    }


def _instagram_stats() -> dict:
    from verticals.instagram_upload import _get_ig_user_id, API_BASE
    from verticals.config import get_meta_access_token
    import requests
    access_token = get_meta_access_token()
    if not access_token:
        return {"error": "META_ACCESS_TOKEN not set"}
    ig_user_id = _get_ig_user_id(access_token)
    r = requests.get(
        f"{API_BASE}/{ig_user_id}",
        params={"fields": "followers_count,media_count", "access_token": access_token},
        timeout=15,
    )
    if r.status_code != 200:
        return {"error": f"{r.status_code} {r.text[:150]}"}
    data = r.json()
    return {
        "followers": data.get("followers_count", 0),
        "posts": data.get("media_count", 0),
    }


def main() -> None:
    out = {}
    for name, fn in (("youtube", _youtube_stats), ("tiktok", _tiktok_stats), ("instagram", _instagram_stats)):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": str(e)[:200]}
    print(json.dumps(out))


if __name__ == "__main__":
    main()

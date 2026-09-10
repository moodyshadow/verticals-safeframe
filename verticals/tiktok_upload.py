"""TikTok Content Posting API — Direct Post upload.

Needs a token from scripts/setup_tiktok_oauth.py first. While the app is
unaudited (sandbox mode), TikTok restricts posts to SELF_ONLY visibility —
that's a platform-side restriction, not something this code can work around,
and it lifts once the app passes TikTok's review.
"""
import json
import time
from pathlib import Path

import requests

from .config import get_tiktok_credentials, get_tiktok_token_path, write_secret_file
from .log import log

API_BASE = "https://open.tiktokapis.com/v2"

# TikTok requires chunks between 5MB and 64MB, except when the whole video is
# under 64MB, in which case it's uploaded as a single chunk. Every video this
# pipeline has produced so far is well under that (a few MB), so this only
# implements the single-chunk path — raise clearly if that assumption breaks
# rather than silently mis-chunking a larger file.
MAX_SINGLE_CHUNK_BYTES = 64 * 1024 * 1024


def _load_token() -> dict:
    token_path = get_tiktok_token_path()
    return json.loads(token_path.read_text(encoding="utf-8"))


def _get_valid_access_token() -> str:
    """Return a usable access token, refreshing it first if expired."""
    token = _load_token()
    obtained_at = token.get("obtained_at", 0)
    expires_in = token.get("expires_in", 0)
    # Refresh a bit early (60s buffer) rather than cutting it exactly at expiry.
    if time.time() < obtained_at + expires_in - 60:
        return token["access_token"]

    log("TikTok access token expired, refreshing...")
    # Use the same credential set that originally issued this token — a
    # sandbox-issued refresh_token is invalid against production
    # client_key/secret and vice versa. Tokens saved before this field
    # existed default to sandbox, since that's the only mode this project
    # has ever successfully authenticated against (the Production app has
    # never passed TikTok's review).
    sandbox = token.get("sandbox", True)
    client_key, client_secret = get_tiktok_credentials(sandbox=sandbox)
    r = requests.post(
        f"{API_BASE}/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
        timeout=30,
    )
    if r.status_code != 200 or "access_token" not in r.json():
        raise RuntimeError(
            f"TikTok token refresh failed: {r.status_code} {r.text[:300]}\n"
            "Re-run: python scripts/setup_tiktok_oauth.py"
        )
    new_token = r.json()
    new_token["obtained_at"] = time.time()
    new_token["sandbox"] = sandbox
    write_secret_file(get_tiktok_token_path(), json.dumps(new_token, indent=2))
    return new_token["access_token"]


def upload_to_tiktok(video_path: Path, title: str, privacy_level: str = "SELF_ONLY") -> str:
    """Publish a video via TikTok's Content Posting API (Direct Post).

    privacy_level: SELF_ONLY (default — required while the app is in sandbox/
    unaudited mode), or PUBLIC_TO_EVERYONE / MUTUAL_FOLLOW_FRIENDS /
    FOLLOWER_OF_CREATOR once the app has passed TikTok's review.
    """
    access_token = _get_valid_access_token()
    video_size = video_path.stat().st_size
    if video_size > MAX_SINGLE_CHUNK_BYTES:
        raise RuntimeError(
            f"{video_path.name} is {video_size / 1024 / 1024:.1f}MB — over the "
            f"{MAX_SINGLE_CHUNK_BYTES / 1024 / 1024:.0f}MB single-chunk limit this "
            "function implements. Multi-chunk upload isn't built; either shrink "
            "the video or extend this function to chunk the upload."
        )

    log(f"Initiating TikTok upload for {video_path.name}...")
    init_resp = requests.post(
        f"{API_BASE}/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title": title[:150],
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
        },
        timeout=30,
    )
    init_data = init_resp.json()
    if init_resp.status_code != 200 or init_data.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"TikTok init failed: {init_resp.status_code} {init_resp.text[:500]}")

    publish_id = init_data["data"]["publish_id"]
    upload_url = init_data["data"]["upload_url"]

    log("Uploading video bytes...")
    video_bytes = video_path.read_bytes()
    put_resp = requests.put(
        upload_url,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        },
        data=video_bytes,
        timeout=120,
    )
    if put_resp.status_code not in (200, 201):
        raise RuntimeError(f"TikTok video upload failed: {put_resp.status_code} {put_resp.text[:500]}")

    log("Waiting for TikTok to finish processing...")
    for _ in range(30):  # up to ~60s
        time.sleep(2)
        status_resp = requests.post(
            f"{API_BASE}/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
            timeout=30,
        )
        status_data = status_resp.json().get("data", {})
        status = status_data.get("status")
        if status == "PUBLISH_COMPLETE":
            log(f"Published to TikTok: publish_id={publish_id}")
            return publish_id
        if status == "FAILED":
            raise RuntimeError(f"TikTok publish failed: {status_data}")

    log("TikTok publish still processing after timeout — check status manually later "
        f"(publish_id={publish_id}); this doesn't necessarily mean it failed.")
    return publish_id

"""Instagram Reels publishing via the Instagram API (Instagram Login flow).

Unlike TikTok's Content Posting API (which accepts an uploaded file directly),
Instagram's Content Publishing API only accepts a *URL* it fetches the video
from itself — there's no raw-upload endpoint. So this module first stages the
video at a public URL in Cloudflare R2 (see get_r2_config in config.py), then
points Instagram at that URL.

Needs META_ACCESS_TOKEN in config.json — a token from the app's "API setup
with Instagram login" page (Meta for Developers → your app → Instagram).
That token is short-lived (~1hr) unless exchanged for a long-lived one (~60
days) via ig_exchange_token; this module assumes whatever's in config.json is
already valid and doesn't attempt to refresh it — re-run the Meta dashboard
flow (or the exchange call) and update config.json when it expires.
"""
import time
from pathlib import Path

import boto3
import requests

from .config import get_meta_access_token, get_r2_config
from .log import log

API_BASE = "https://graph.instagram.com/v21.0"


def _stage_video_publicly(video_path: Path, key: str) -> str:
    """Upload the video to R2 and return its public URL."""
    r2 = get_r2_config()
    missing = [k for k in ("account_id", "access_key_id", "secret_access_key", "bucket", "public_url") if not r2.get(k)]
    if missing:
        raise RuntimeError(f"R2 not configured — missing {missing} in config.json")

    log(f"Staging {video_path.name} at a public URL (R2)...")
    s3 = boto3.client(
        "s3",
        endpoint_url=r2["endpoint"],
        aws_access_key_id=r2["access_key_id"],
        aws_secret_access_key=r2["secret_access_key"],
        region_name="auto",
    )
    s3.upload_file(str(video_path), r2["bucket"], key)
    return f"{r2['public_url'].rstrip('/')}/{key}"


def _get_ig_user_id(access_token: str) -> str:
    r = requests.get(f"{API_BASE}/me", params={"fields": "id,username", "access_token": access_token}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Instagram identity check failed: {r.status_code} {r.text[:300]}")
    data = r.json()
    log(f"Instagram account: {data.get('username')} ({data['id']})")
    return data["id"]


def upload_to_instagram(video_path: Path, caption: str, job_id: str) -> str:
    """Publish a video as an Instagram Reel. Returns the published media id."""
    access_token = get_meta_access_token()
    if not access_token:
        raise RuntimeError("META_ACCESS_TOKEN not set in config.json")

    ig_user_id = _get_ig_user_id(access_token)
    video_url = _stage_video_publicly(video_path, f"reels/{job_id}.mp4")

    log("Creating Instagram media container...")
    create_resp = requests.post(
        f"{API_BASE}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "access_token": access_token,
        },
        timeout=30,
    )
    create_data = create_resp.json()
    if create_resp.status_code != 200 or "id" not in create_data:
        raise RuntimeError(f"Instagram container creation failed: {create_resp.status_code} {create_resp.text[:500]}")
    creation_id = create_data["id"]

    log("Waiting for Instagram to finish processing the video...")
    for _ in range(30):  # up to ~5 minutes
        time.sleep(10)
        status_resp = requests.get(
            f"{API_BASE}/{creation_id}",
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        status_data = status_resp.json()
        status_code = status_data.get("status_code")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise RuntimeError(f"Instagram failed to process the video: {status_data}")
    else:
        raise RuntimeError(
            f"Instagram container still processing after timeout (creation_id={creation_id}) — "
            "it may still finish; check manually before retrying to avoid a duplicate post."
        )

    log("Publishing...")
    publish_resp = requests.post(
        f"{API_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    publish_data = publish_resp.json()
    if publish_resp.status_code != 200 or "id" not in publish_data:
        raise RuntimeError(f"Instagram publish failed: {publish_resp.status_code} {publish_resp.text[:500]}")

    media_id = publish_data["id"]
    log(f"Published to Instagram: media_id={media_id}")
    return media_id

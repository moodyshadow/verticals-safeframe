"""Automatic cleanup for videos that have actually gone public on YouTube.

A video finishing `python -m verticals upload` is NOT the trigger — every
upload starts as "private" and only becomes public via a manual switch in
YouTube Studio (there's no code path in this project that sets it public
automatically). This script checks each uploaded draft's REAL current
privacy status via the YouTube Data API and only cleans up once it's
actually public — never on trust that "it was uploaded, so it's probably
done."

Cleans up, per newly-public job:
  - redo_<niche>*.log / redraft_<niche>*.log working logs in the user's home dir
  - a superseded draft JSON for the same topic, if one exists (rare)
  - reports/broll_preview/ leftovers (shared across jobs, safe to clear
    once nothing is mid-iteration)

Marks the draft with "cleanup_done": true so it's never processed twice,
even if the video stays public forever after.

Run manually:
    venv\\Scripts\\python.exe cleanup_published.py

Intended to run daily via a scheduled task, same pattern as the other
Daily Overclocked automations.
"""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verticals.analytics import _get_credentials  # noqa: E402
from verticals.config import DRAFTS_DIR  # noqa: E402
from verticals.log import log  # noqa: E402

HOME = Path.home()
BROLL_PREVIEW_DIR = Path(__file__).parent / "reports" / "broll_preview"


def _fetch_privacy_statuses(video_ids: list[str]) -> dict[str, str]:
    """One batched API call for up to 50 video IDs — id -> privacyStatus."""
    if not video_ids:
        return {}
    from googleapiclient.discovery import build

    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = youtube.videos().list(part="status", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            result[item["id"]] = item["status"].get("privacyStatus", "unknown")
    return result


def _video_id_from_url(url: str) -> str | None:
    if not url:
        return None
    return url.rstrip("/").split("/")[-1]


def _cleanup_job_files(job_id: str) -> list[str]:
    """Delete this job's leftover working files. Returns what was removed."""
    removed = []
    for pattern in [f"redo_*{job_id}*.log", f"redraft_*{job_id}*.log"]:
        for f in glob.glob(str(HOME / pattern)):
            Path(f).unlink(missing_ok=True)
            removed.append(f)
    return removed


def main() -> None:
    drafts = sorted(DRAFTS_DIR.glob("*.json"))
    uploaded = []
    for path in drafts:
        try:
            draft = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if draft.get("cleanup_done"):
            continue
        url = draft.get("youtube_url_en") or draft.get("youtube_url")
        vid = _video_id_from_url(url)
        if vid:
            uploaded.append((path, draft, vid))

    if not uploaded:
        print("No uploaded-but-not-yet-cleaned jobs found.")
        return

    statuses = _fetch_privacy_statuses([vid for _, _, vid in uploaded])

    cleaned_any = False
    for path, draft, vid in uploaded:
        status = statuses.get(vid, "unknown")
        if status != "public":
            print(f"  job {draft.get('job_id', path.stem)}: video {vid} is '{status}' — not cleaning up yet")
            continue

        removed = _cleanup_job_files(str(draft.get("job_id", path.stem)))
        draft["cleanup_done"] = True
        path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
        cleaned_any = True
        print(f"  job {draft.get('job_id', path.stem)}: video {vid} is public — "
              f"cleaned up {len(removed)} file(s)")
        for f in removed:
            print(f"    removed: {f}")

    # Preview frames are shared scratch space, not tied to one job — safe
    # to clear whenever at least one job just got cleaned up (nothing
    # should still be relying on stale preview frames from a finished job).
    if cleaned_any and BROLL_PREVIEW_DIR.exists():
        for f in BROLL_PREVIEW_DIR.iterdir():
            f.unlink(missing_ok=True)
        print(f"  cleared {BROLL_PREVIEW_DIR}")


if __name__ == "__main__":
    main()

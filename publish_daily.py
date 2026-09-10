"""Fully local daily-publish runner — no Claude session involved.

Replicates the deterministic selection logic that used to live in the
`publish-tech-daily` / `publish-gaming-daily` Claude scheduled tasks: scan
drafts for the given niche, pick the most recent one that's produced but not
yet uploaded, and if it's been reviewed, upload it. Meant to be triggered by
a native Windows Task Scheduler entry (see setup_local_schedulers.ps1) so the
recurring daily publish no longer costs a Claude agent session.

Usage: venv\\Scripts\\python.exe publish_daily.py <tech|gaming>

Logs every run (even no-ops) to logs/publish.log so results are checkable
without needing to ask Claude.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).parent
DRAFTS_DIR = Path.home() / ".verticals" / "drafts"
LOG_PATH = REPO / "logs" / "publish.log"
PYTHON = REPO / "venv" / "Scripts" / "python.exe"


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("tech", "gaming"):
        print("Usage: publish_daily.py <tech|gaming>")
        sys.exit(1)
    niche = sys.argv[1]

    candidates = []
    for path in DRAFTS_DIR.glob("*.json"):
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if draft.get("niche") != niche:
            continue
        if draft.get("youtube_url_en"):
            continue
        pipeline_state = draft.get("_pipeline_state", {})
        if pipeline_state.get("assemble", {}).get("status") != "done":
            continue
        if pipeline_state.get("upload", {}).get("status") == "done":
            continue
        job_id = draft.get("job_id")
        if not job_id:
            continue
        candidates.append((int(job_id), path, draft))

    if not candidates:
        log(f"{niche}: no unpublished video ready to publish today.")
        return

    candidates.sort(key=lambda c: c[0], reverse=True)
    job_id, draft_path, draft = candidates[0]

    if not draft.get("reviewed_by_claude"):
        log(
            f"{niche}: {job_id} ({draft.get('youtube_title', '?')}) is produced "
            f"but not yet reviewed — skipping. Review it first with: "
            f"python -m verticals review --draft {draft_path}"
        )
        return

    log(f"{niche}: publishing {job_id} ({draft.get('youtube_title', '?')})...")
    result = subprocess.run(
        [str(PYTHON), "-m", "verticals", "upload", "--draft", str(draft_path)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    log(result.stdout.strip())
    if result.stderr.strip():
        log("STDERR: " + result.stderr.strip())
    if result.returncode != 0:
        log(f"{niche}: upload FAILED (exit {result.returncode}) for {job_id}")
    else:
        log(f"{niche}: upload succeeded for {job_id}")


if __name__ == "__main__":
    main()

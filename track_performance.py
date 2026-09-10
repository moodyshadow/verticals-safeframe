"""Analytics feedback loop for Daily Overclocked.

Finds decision-log entries old enough to have a meaningful view count,
pulls their real YouTube Analytics outcome, records it back into the log,
and writes a report ranking what's actually worked — so future topic
picks are informed by real outcomes instead of guessing fresh every time.

Requires the yt-analytics.readonly OAuth scope — re-run
scripts/setup_youtube_oauth.py once if this errors with a scope/permission
message (the original token was upload-only).

Run manually:
    venv\\Scripts\\python.exe track_performance.py

Or via a scheduled task, same pattern as scan_opportunities.py.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verticals.analytics import fetch_video_outcome  # noqa: E402
from verticals.decision_log import pending_outcomes, record_outcome, read_all  # noqa: E402

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def main() -> int:
    print(f"Tracking performance — {datetime.now().isoformat(timespec='seconds')}")

    pending = pending_outcomes(min_age_days=7.0)
    if not pending:
        print("  No videos are due for an outcome check yet (need 7+ days since upload).")
    for entry in pending:
        print(f"  pulling analytics: {entry['title'][:60]}...")
        try:
            uploaded = datetime.fromisoformat(entry["uploaded_at"]).date()
            outcome = fetch_video_outcome(entry["video_id"], uploaded)
            record_outcome(entry["job_id"], outcome)
            print(f"    views={outcome.get('views', 0)} "
                  f"avg_view_pct={outcome.get('average_view_percentage', 0):.1f}%")
        except Exception as e:
            print(f"    failed: {e}")

    # Build the "what's working" report from everything with a recorded outcome
    all_entries = [e for e in read_all() if e.get("outcome") and e["outcome"].get("views", 0) > 0]
    all_entries.sort(key=lambda e: e["outcome"].get("views", 0), reverse=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"performance_{date_str}.md"
    lines = [f"# Performance report — {date_str}", ""]

    if not all_entries:
        lines.append("No videos with recorded outcomes yet — check back after videos have 7+ days of data.")
    else:
        lines.append("| Views | Avg view % | Niche | Topic source | Title |")
        lines.append("|---|---|---|---|---|")
        for e in all_entries[:25]:
            o = e["outcome"]
            title = e["title"].replace("|", "-")[:70]
            lines.append(
                f"| {o.get('views', 0)} | {o.get('average_view_percentage', 0):.1f}% | "
                f"{e['niche']} | {e.get('topic_source', 'manual')} | {title} |"
            )

        # Surface a simple signal: which niche and topic source are actually working
        from collections import defaultdict
        by_niche = defaultdict(list)
        by_source = defaultdict(list)
        for e in all_entries:
            by_niche[e["niche"]].append(e["outcome"].get("views", 0))
            by_source[e.get("topic_source") or "manual"].append(e["outcome"].get("views", 0))

        def _avg(vals):
            return sum(vals) / len(vals) if vals else 0

        lines.append("")
        lines.append("## Average views by niche")
        for niche, views in sorted(by_niche.items(), key=lambda kv: -_avg(kv[1])):
            lines.append(f"- **{niche}**: {_avg(views):.0f} avg views ({len(views)} videos)")

        lines.append("")
        lines.append("## Average views by topic source")
        for source, views in sorted(by_source.items(), key=lambda kv: -_avg(kv[1])):
            lines.append(f"- **{source}**: {_avg(views):.0f} avg views ({len(views)} videos)")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport saved: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

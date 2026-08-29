"""Daily content-opportunity scanner for Daily Overclocked.

Scans trending topics across a mix of niches — some suited to the AI
pipeline (script + local Stable Diffusion visuals), others suited to
real footage from the owned drone/action camera (travel, nature) —
ranks them, and writes a dated report. Optionally auto-drafts a free
Ollama script for the single best pick so it's ready to produce.

Run manually:
    venv\\Scripts\\python.exe scan_opportunities.py

Or via the scheduled task set up alongside this script.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verticals.config import DRAFTS_DIR  # noqa: E402
from verticals.topics.engine import TopicEngine  # noqa: E402
from verticals.draft import generate_draft  # noqa: E402

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# Niches suited to the $0 AI pipeline (script + local SD visuals)
AI_NICHES = ["tech", "gaming", "science", "entertainment"]

# Niches better served by real footage from the owned drone/action camera
CAMERA_NICHES = ["travel"]

TOP_N_PER_NICHE = 5
AUTO_DRAFT_TOP_PICK = True


def scan_niche(niche: str, is_camera_fit: bool) -> list[dict]:
    try:
        engine = TopicEngine(niche=niche)
        candidates = engine.discover(limit=TOP_N_PER_NICHE)
    except Exception as e:
        print(f"  [{niche}] scan failed: {e}")
        return []
    return [
        {
            "niche": niche,
            "title": c.title,
            "source": c.source,
            "score": c.trending_score,
            "url": c.url,
            "summary": c.summary,
            "camera_fit": is_camera_fit,
        }
        for c in candidates
    ]


def main() -> Path | None:
    """Scan + auto-draft. Returns the saved draft's Path if one was drafted,
    else None (used by run_full_pipeline.py to chain into produce)."""
    print(f"Scanning content opportunities — {datetime.now().isoformat(timespec='seconds')}")
    all_results = []

    for niche in AI_NICHES:
        print(f"  scanning [{niche}] (AI pipeline)...")
        all_results += scan_niche(niche, is_camera_fit=False)

    for niche in CAMERA_NICHES:
        print(f"  scanning [{niche}] (camera footage fit)...")
        all_results += scan_niche(niche, is_camera_fit=True)

    all_results.sort(key=lambda r: r["score"], reverse=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{date_str}.md"

    lines = [f"# Content opportunity scan — {date_str}", ""]
    if not all_results:
        lines.append("No topics found today (sources may be rate-limited or empty).")
    else:
        lines.append("| Score | Niche | Camera fit | Title | Source |")
        lines.append("|---|---|---|---|---|")
        for r in all_results[:20]:
            fit = "yes (drone/action cam)" if r["camera_fit"] else "AI pipeline"
            title = r["title"].replace("|", "-")[:90]
            lines.append(f"| {r['score']:.2f} | {r['niche']} | {fit} | {title} | {r['source']} |")

    top_pick = all_results[0] if all_results else None

    # Try the top non-camera picks in ranked order — the small local model
    # occasionally fails on a given topic/prompt combo no matter how many
    # times you retry it, so fall through to the next-best topic instead
    # of giving up on the whole run.
    candidates = [r for r in all_results if not r["camera_fit"]][:5]
    drafted = False
    draft_path = None
    if AUTO_DRAFT_TOP_PICK and candidates:
        for candidate in candidates:
            print(f"  drafting: {candidate['title'][:60]}...")
            draft = None
            last_err = None
            for attempt in range(3):
                try:
                    draft = generate_draft(
                        news=candidate["title"], niche=candidate["niche"],
                        provider="ollama", platform="shorts",
                        url=candidate.get("url", ""), summary=candidate.get("summary", ""),
                    )
                    break
                except Exception as e:
                    last_err = e
                    print(f"  attempt {attempt + 1}/3 failed: {e}")
            if draft is not None:
                job_id = str(int(time.time()))
                draft["job_id"] = job_id
                draft["topic_score"] = candidate["score"]
                DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
                draft_path = DRAFTS_DIR / f"{job_id}.json"
                draft_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
                lines.append("")
                lines.append(f"## Auto-drafted pick")
                lines.append(f"Topic: {candidate['title']}")
                lines.append(f"Draft saved: `{draft_path}`")
                lines.append(f"Produce with: `python -m verticals produce --draft \"{draft_path}\" --voice edge`")
                drafted = True
                break
            print(f"  giving up on this topic after 3 attempts, trying next candidate...")
        if not drafted:
            lines.append(f"\n(Auto-draft failed on all {len(candidates)} top candidates — last error: {last_err})")
    if top_pick and top_pick["camera_fit"]:
        lines.append("")
        lines.append("## Top pick suits your camera footage")
        lines.append(f"\"{top_pick['title']}\" — consider filming this with the drone/action cam instead of AI visuals.")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport saved: {report_path}")
    print(f"Top result: {top_pick['title'][:80] if top_pick else 'none'}")
    return draft_path


if __name__ == "__main__":
    main()

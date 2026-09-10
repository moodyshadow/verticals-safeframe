"""Daily marketing/growth-strategy agent for Daily Overclocked.

Runs entirely on the local Ollama LLM (no cloud API cost) — pairs it with
real research (DuckDuckGo, same approach as draft.py's topic research) and
the channel's own recorded video outcomes (decision_log.py /
track_performance.py) so its suggestions are grounded in current tactics
and this channel's actual data, not just the model's stale training
knowledge or generic advice that ignores what's already been tried.

Writes a dated report to reports/marketing_ideas_<date>.md. A separate
scheduled Claude Code task checks for a new report each day and relays it
to the user directly — this script itself has no way to message anyone,
by design (it's meant to run unattended via Windows Task Scheduler).

Run manually:
    venv\\Scripts\\python.exe marketing_agent.py
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verticals.analytics import fetch_channel_stats  # noqa: E402
from verticals.decision_log import read_all  # noqa: E402
from verticals.llm import call_llm  # noqa: E402
from verticals.niche import load_niche  # noqa: E402
from verticals.research import search_ddg_snippets  # noqa: E402

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

ACTIVE_NICHES = ["gaming", "tech"]  # the niches run_two_niches.py actually produces


def _channel_facts() -> str:
    """The channel's own current stats — the basic "what is this product"
    facts a marketing agent needs (subscriber count, total views, how many
    videos exist) that no per-video outcome log can tell it on its own."""
    try:
        stats = fetch_channel_stats()
    except Exception as e:
        return f"(Channel stats unavailable: {e} — YouTube OAuth may need re-consent via scripts/setup_youtube_oauth.py)"
    if not stats:
        return "(Channel stats unavailable — no channel found for these credentials.)"
    return (
        f"Channel: \"{stats['title']}\"\n"
        f"Subscribers: {stats['subscriber_count']}\n"
        f"Total views: {stats['view_count']}\n"
        f"Total videos: {stats['video_count']}\n"
        f"Channel description: {stats['description'][:300] or '(none set)'}"
    )


# What the production pipeline already does automatically for every video —
# without this, the agent has no way to know these exist and keeps
# re-suggesting them as if they were gaps. Added after it suggested "add a
# consistent branded intro with a subscribe CTA" (already exists) and "add
# keywords to the description/tags" (already generated per-video by the
# LLM) as if they were missing.
ALREADY_BUILT = """- Every video already opens AND closes with a branded intro/outro bumper (logo + channel name), see branding.py.
- Every video already gets an LLM-written YouTube title, description, and comma-separated tags per upload — not just a bare title (see draft.py's youtube_title/youtube_description/youtube_tags fields, used directly by upload.py).
- Every niche's script prompt already picks from a set of "hook" templates (outrage/hype/drama/hot-take style openers) tuned per niche — titles and hooks are not written from scratch with no structure.
- Captions already highlight the active word in a distinct color as it's spoken, word-by-word, burned into the video (not just static subtitles).
- Every video already gets a custom-generated thumbnail (AI-generated image + text overlay, sized correctly for Shorts) automatically at upload time — see thumbnail.py. Thumbnails are not a manual or missing step."""


def _brand_voice() -> str:
    """Tone/perspective/audience for each niche this channel actually
    covers — without this, the LLM has no idea what "Daily Overclocked"
    actually sounds like or is about beyond its name."""
    lines = []
    for niche_name in ACTIVE_NICHES:
        profile = load_niche(niche_name)
        script = profile.get("script", {})
        lines.append(
            f"- {profile.get('display_name', niche_name)}: {profile.get('description', '')} "
            f"Tone: {script.get('tone', '')}. Perspective: {script.get('perspective', '')}."
        )
    return "\n".join(lines)


def _content_history(max_entries: int = 25) -> str:
    """Every video actually made so far (title, niche, topic, upload date)
    — the real content catalog, not just the ones with recorded outcomes.
    Without this the agent has no idea what's already been covered and
    might suggest topics/formats that are just a repeat of last week."""
    entries = read_all()
    if not entries:
        return "No videos uploaded yet."
    entries = entries[-max_entries:]  # most recent
    lines = [
        f"- [{e.get('uploaded_at', '')[:10]}] ({e.get('niche', '?')}) \"{e.get('title', '')}\""
        for e in reversed(entries)
    ]
    return "\n".join(lines)


def _performance_summary(max_entries: int = 8) -> str:
    """Recent videos with a recorded outcome, best-performing first — gives
    the LLM real signal about what's actually worked instead of guessing
    blind, the same "learn from outcomes" principle track_performance.py
    already applies to topic selection."""
    entries = [e for e in read_all() if e.get("outcome")]
    if not entries:
        return "No recorded video outcomes yet — track_performance.py hasn't logged any results."

    entries.sort(key=lambda e: e["outcome"].get("views", 0), reverse=True)
    lines = []
    for e in entries[:max_entries]:
        o = e["outcome"]
        lines.append(
            f"- \"{e['title']}\" (niche: {e['niche']}) — {o.get('views', 0)} views, "
            f"{o.get('average_view_percentage', 0):.0f}% avg retention, "
            f"{o.get('subscribers_gained', 0)} subs gained"
        )
    return "\n".join(lines)


def _current_tactics_research() -> str:
    # A short, already-concise query sent to DDG as-is (see
    # search_ddg_snippets' docstring) — a longer natural-language query
    # would get mangled down to its 4 longest words by the headline-tuned
    # extract_keywords() that research_topic() normally routes through.
    return search_ddg_snippets("YouTube Shorts algorithm 2026")


def generate_report() -> Path:
    print("Fetching channel stats...")
    channel_facts = _channel_facts()

    print("Building content history...")
    history = _content_history()
    brand_voice = _brand_voice()

    print("Researching current YouTube Shorts growth tactics...")
    research = _current_tactics_research()

    print("Reading channel's own recorded video outcomes...")
    performance = _performance_summary()

    has_performance_data = "No recorded video outcomes yet" not in performance
    has_research = bool(research.strip())
    research_block = research if has_research else "(DDG search returned nothing this run — this happens often; it increasingly serves anti-bot challenges to scripted requests.)"

    prompt = f"""You are a YouTube growth strategist for the channel described below. Read the product context carefully before suggesting anything — a suggestion that ignores what this channel actually is or has already made is not useful.

THE PRODUCT (channel facts):
{channel_facts}

BRAND VOICE (per niche this channel covers):
{brand_voice}

ALREADY BUILT INTO EVERY VIDEO (do not suggest any of this as if it were missing):
{ALREADY_BUILT}

CONTENT ALREADY MADE (most recent videos, so you don't suggest repeating them):
{history}

RECENT VIDEO PERFORMANCE (this channel's own real data):
{performance}

CURRENT RESEARCH on YouTube Shorts growth tactics:
{research_block}

Based on the channel's actual product context, its content history, its real performance data, AND the current research, give 3-5 concrete, actionable suggestions for improving this channel's growth/promotion. Each suggestion must:
- Be specific enough to act on today (not generic advice like "be consistent" or "engage with your audience")
- Reference either a pattern in the channel's own data OR a specific current tactic from the research — say which. {"There is NO performance data yet (see above) — do not claim any suggestion is based on 'a pattern in the channel's own data.'" if not has_performance_data else ""} {"There is NO research available this run (see above) — base every suggestion on your own general knowledge of YouTube Shorts instead, and say so plainly (e.g. 'general best practice:') rather than attributing it to research that doesn't exist." if not has_research else ""}
- NEVER invent a specific statistic, percentage, or study result (e.g. "increases views by 33%") unless that exact number appears verbatim in the research text above. If you don't have a real number, don't state one — describe the tactic without a fabricated figure.
- NEVER cite a source that isn't the actual research text above — do not write things like "(according to research)" as a fake citation when you have no real source for a claim.
- Avoid repeating suggestions that are already standard practice here: daily uploads, a subscribe/follow CTA, topic-specific hooks, and everything listed under "ALREADY BUILT INTO EVERY VIDEO" above
- NEVER suggest teasing, previewing, or promising specific future content ("Part 2 tomorrow," countdown timers, "coming soon" graphics, cliffhangers) — this channel does not commit to specific follow-up videos, since tomorrow's topic is picked fresh each day and is never guaranteed to continue today's story
- NEVER suggest a YouTube feature that has been discontinued (e.g. "video annotations," which YouTube removed in 2019). If you're not certain a feature still exists on YouTube today, don't suggest it — describe the underlying goal instead (e.g. "give viewers a next step" rather than naming a specific possibly-outdated feature).

Format as a numbered list. No preamble, no summary at the end — just the numbered suggestions."""

    print("Asking local LLM (Ollama) to synthesize suggestions...")
    suggestions = call_llm(prompt, provider="ollama", max_tokens=1200)

    report_path = REPORTS_DIR / f"marketing_ideas_{date.today().isoformat()}.md"
    report_path.write_text(
        f"# Marketing ideas — {date.today().isoformat()}\n\n"
        f"## Channel facts\n{channel_facts}\n\n"
        f"## Content already made (recent)\n{history}\n\n"
        f"## Performance considered\n{performance}\n\n"
        f"## Suggestions\n{suggestions}\n",
        encoding="utf-8",
    )
    print(f"\nReport saved: {report_path}")
    _notify_windows(report_path)

    from verticals.agent_bus import send as bus_send, GENERAL_CHANNEL
    first_suggestion = next((line for line in suggestions.splitlines() if line.strip()), "").strip()
    bus_send(
        GENERAL_CHANNEL, "marketing",
        f"Today's top growth suggestion: {first_suggestion}",
        meta={"report_path": str(report_path), "date": date.today().isoformat()},
    )

    return report_path


def _notify_windows(report_path: Path) -> None:
    """Native Windows toast notification — fires independent of whether any
    Claude Code session is open, since this script runs unattended via
    Windows Task Scheduler and has no other way to reach the user directly."""
    import subprocess
    ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode('Daily Overclocked — marketing ideas ready')) > $null
$texts.Item(1).AppendChild($template.CreateTextNode('{report_path.name}')) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Daily Overclocked Marketing Agent').Show($toast)
"""
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_script], timeout=15, capture_output=True)
    except Exception as e:
        print(f"  (toast notification failed, non-fatal: {e})")


if __name__ == "__main__":
    generate_report()

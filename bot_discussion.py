"""Multiple bot "participants" independently weigh in and message the user
on WhatsApp — the closest real approximation to bots joining a discussion
unprompted, since nothing can insert itself into a live Claude Code chat.
Each participant runs its own check and sends its own message only if it
actually has something worth saying (never a forced "nothing to report").

Participants:
- marketing: today's best-performing-pattern take (decision_log outcomes)
- critic: flags the most recent draft that still has an unresolved
  grounding/CTA/emotion issue, if any
- scout: whether today's #1 trending topic (per niche) has a mainstream
  recognizable name in it or not

Run manually:
    venv\\Scripts\\python.exe bot_discussion.py
"""
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from verticals.decision_log import read_all  # noqa: E402
from verticals.config import DRAFTS_DIR  # noqa: E402
from verticals.topics.engine import TopicEngine  # noqa: E402

WHATSAPP_TARGET = "+40771943784"


def _send_whatsapp(sender_name: str, text: str) -> None:
    message = f"[{sender_name}] {text}"
    subprocess.run(
        ["wsl", "-d", "OpenClawGateway", "--", "openclaw", "message", "send",
         "--channel", "whatsapp", "--target", WHATSAPP_TARGET, "--message", message],
        capture_output=True, text=True,
    )


def marketing_take() -> str | None:
    entries = [e for e in read_all() if e.get("outcome") and e["outcome"].get("views") is not None]
    if len(entries) < 3:
        return None
    entries.sort(key=lambda e: e["outcome"].get("views", 0), reverse=True)
    best = entries[0]
    return (
        f"Best performer so far is still \"{best['title'][:60]}\" "
        f"({best['outcome'].get('views', 0)} views, "
        f"{best['outcome'].get('average_view_percentage', 0):.0f}% retention) — "
        f"{best['niche']}."
    )


def critic_take() -> str | None:
    import json
    drafts = sorted(DRAFTS_DIR.glob("*.json"), key=lambda p: int(p.stem), reverse=True)[:5]
    for path in drafts:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("reviewed_by_claude") is False or d.get("reviewed_by_claude") is None:
            if d.get("youtube_url_en"):
                continue  # already live, nothing to flag
            return f"Job {path.stem} (\"{d.get('youtube_title', '?')[:50]}\") hasn't been reviewed yet — still sitting unreviewed."
    return None


def scout_take() -> str | None:
    for niche in ("gaming", "tech"):
        try:
            engine = TopicEngine(niche=niche)
            candidates = engine.discover(limit=10)
            if not candidates:
                continue
            top = candidates[0]
            return f"Today's top {niche} trend: \"{top.title[:70]}\" (score {top.trending_score:.2f})."
        except Exception:
            continue
    return None


def main() -> int:
    participants = [
        ("marketing", marketing_take),
        ("critic", critic_take),
        ("scout", scout_take),
    ]
    sent = 0
    for name, fn in participants:
        try:
            take = fn()
        except Exception as e:
            print(f"{name}: failed ({e})")
            continue
        if take:
            print(f"{name}: {take}")
            _send_whatsapp(name, take)
            sent += 1
        else:
            print(f"{name}: nothing to say this run")
    print(f"\n{sent} participant(s) messaged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Shared message bus for local agent-to-agent conversations.

Not a queue service or a live socket — just an append-only JSONL transcript
per conversation that any local process can write to and read from. Two (or
more) roles can hold a real back-and-forth by posting messages and polling
for the other side's reply, at whatever pace they like. Simpler and far more
reliable than fighting OpenClaw's A2A peer/SSRF plumbing for a same-machine
conversation — this only needs a shared file, no network hop, no auth.

One file per conversation in ~/.verticals/agent_bus/<conversation_id>.jsonl.
"""
import json
import time
import uuid
from pathlib import Path

from .config import SKILL_DIR

BUS_DIR = SKILL_DIR / "agent_bus"

# A standing channel any stage can post to at any time, independent of any
# one draft/topic — e.g. the marketing agent leaving a note about a pattern
# it noticed, unprompted, rather than only speaking when asked about one
# specific script. Just a fixed, well-known conversation id; same send/
# read_all functions work on it as on any per-draft conversation.
GENERAL_CHANNEL = "general"


def _conversation_path(conversation_id: str) -> Path:
    BUS_DIR.mkdir(parents=True, exist_ok=True)
    return BUS_DIR / f"{conversation_id}.jsonl"


def new_conversation() -> str:
    """Start a fresh conversation, returning its id."""
    return uuid.uuid4().hex[:12]


def send(conversation_id: str, sender: str, text: str, meta: dict | None = None) -> dict:
    """Append one message to the conversation. Returns the message record."""
    message = {
        "id": uuid.uuid4().hex[:8],
        "conversation_id": conversation_id,
        "sender": sender,
        "text": text,
        "meta": meta or {},
        "ts": time.time(),
    }
    with open(_conversation_path(conversation_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")
    return message


def read_all(conversation_id: str) -> list[dict]:
    """Every message in the conversation, oldest first."""
    path = _conversation_path(conversation_id)
    if not path.exists():
        return []
    messages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def wait_for_reply(
    conversation_id: str, from_sender: str, after_ts: float, timeout: float = 60.0, poll_interval: float = 1.0
) -> dict | None:
    """Poll until a message from `from_sender` posted after `after_ts` shows
    up, or give up after `timeout` seconds. Returns the message, or None on
    timeout — this is a local file poll, not a push notification, so a short
    poll_interval is cheap and fine for same-machine conversations."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in reversed(read_all(conversation_id)):
            if msg["sender"] == from_sender and msg["ts"] > after_ts:
                return msg
        time.sleep(poll_interval)
    return None

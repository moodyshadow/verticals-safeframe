"""Overnight pipeline: produce two videos, one gaming and one tech.

Runs at 2am instead of 7am so both videos are sitting ready for review by
the time the human wakes up, rather than starting production at 7am and
making them wait. Deliberately does NOT upload to YouTube — same
draft -> produce, human-reviews-then-uploads convention as
run_full_pipeline.py. Publishing to YouTube stays a separate, explicit
`python -m verticals upload` step.

TikTok is different: it auto-uploads here, but ONLY when the visual QA
pass below found nothing suspicious. TikTok's sandbox app is restricted to
SELF_ONLY (private) visibility regardless — nobody but this account can see
it — so auto-posting doesn't carry YouTube's public-exposure risk; the
video is still gated on looking right.

Run manually:
    venv\\Scripts\\python.exe run_two_niches.py

Writes reports/pipeline_status.json as a list of per-niche results (video
path, title, or error) for daily_review_reminder.ps1 to read.
"""
import argparse
import base64
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

REPORTS_DIR = Path(__file__).parent / "reports"
STATUS_PATH = REPORTS_DIR / "pipeline_status.json"
VENV_PYTHON = Path(__file__).parent / "venv" / "Scripts" / "python.exe"
NICHES = ["gaming", "tech"]

VISION_QA_MODEL = "llava:7b"  # llama3.2-vision fails to load on this Ollama
# build ("unknown model architecture: 'mllama'") — llava is a well-
# established, broadly-compatible vision model that actually works here.
VISION_QA_SAMPLES = 5


def visual_qa_video(video_path: str) -> list[dict]:
    """Sample frames evenly across the finished video and ask a local
    vision model whether each one looks like a broken/garbled AI generation
    (extra limbs, warped geometry, nonsense text, ghosted artifacts like the
    fireman clip's blended-wing issue) — a defensive pass so a visibly
    broken frame gets flagged in the status report before a human ever
    opens the video, rather than only being caught on manual review.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        return [{"error": "could not read video duration for QA"}]

    findings = []
    for i in range(VISION_QA_SAMPLES):
        t = duration * (i + 0.5) / VISION_QA_SAMPLES
        frame_path = Path(video_path).with_suffix(f".qa_{i}.jpg")
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", video_path, "-frames:v", "1",
             "-vf", "scale=512:-1", str(frame_path), "-loglevel", "error"],
            capture_output=True,
        )
        if r.returncode != 0 or not frame_path.exists():
            continue

        img_b64 = base64.b64encode(frame_path.read_bytes()).decode("ascii")
        frame_path.unlink(missing_ok=True)
        body = json.dumps({
            "model": VISION_QA_MODEL,
            "prompt": (
                "Look at this video frame. Does it contain any visual defect "
                "typical of broken AI image generation — extra/fused limbs, "
                "warped or nonsensical geometry, garbled/illegible text, "
                "ghosted or blended artifacts, distorted faces? Answer with "
                "exactly one word (YES or NO), then a colon, then a one-sentence reason."
            ),
            "images": [img_b64],
            "stream": False,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate", data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                parsed = json.loads(resp.read())
            if "error" in parsed:
                # Ollama can return HTTP 200 with an {"error": ...} body
                # (e.g. the model failing to load) rather than raising —
                # treat that the same as a failed call, not as "no defect".
                answer = f"QA call failed: {parsed['error']}"
            else:
                answer = parsed.get("response", "").strip()
        except Exception as e:
            answer = f"QA call failed: {e}"

        findings.append({"timestamp_sec": round(t, 1), "verdict": answer})

    return findings


def _run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(VENV_PYTHON), "-m", "verticals", *args],
        cwd=str(Path(__file__).parent),
        capture_output=True, text=True, timeout=3600,
    )


def _extract_draft_path(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("Draft saved:"):
            return line.split("Draft saved:", 1)[1].strip()
    return None


def produce_one(niche: str) -> dict:
    print(f"\n=== [{niche}] drafting ===")
    # Explicit --provider ollama: without it, `draft` defaults to Claude,
    # which isn't configured on this machine (no ANTHROPIC_API_KEY / claude
    # login) — that's what silently failed both niches the first night this
    # script ran. scan_opportunities.py's own working path uses ollama too.
    draft_result = _run_cli("draft", "--niche", niche, "--discover", "--auto-pick", "--platform", "shorts", "--provider", "ollama")
    if draft_result.returncode != 0:
        return {"niche": niche, "ok": False, "stage": "draft", "error": draft_result.stderr[-2000:] or draft_result.stdout[-2000:]}

    draft_path = _extract_draft_path(draft_result.stdout)
    if not draft_path:
        return {"niche": niche, "ok": False, "stage": "draft", "error": "Could not find draft path in output"}

    print(f"=== [{niche}] producing ({draft_path}) ===")
    produce_result = _run_cli("produce", "--draft", draft_path, "--voice", "edge")
    if produce_result.returncode != 0:
        return {"niche": niche, "ok": False, "stage": "produce", "draft_path": draft_path, "error": produce_result.stderr[-2000:] or produce_result.stdout[-2000:]}

    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    video_path = draft.get("video_en", "")
    fallback_count = draft.get("broll_fallback_count", 0)
    frame_count = draft.get("broll_frame_count", 0)
    degraded = frame_count > 0 and fallback_count == frame_count

    print(f"=== [{niche}] running visual QA on the finished video ===")
    visual_qa = visual_qa_video(video_path) if video_path else []
    # Fail closed: a sample only counts as "clean" if the model explicitly
    # said NO. Anything else — YES, a garbled non-YES/NO answer, or the call
    # failing outright (e.g. the vision model can't even load) — is treated
    # as a flag. An upload gate that silently passes when the check itself
    # is broken isn't a gate at all.
    clean = [f for f in visual_qa if f.get("verdict", "").strip().upper().startswith("NO")]
    visual_qa_flagged = len(visual_qa) == 0 or len(clean) < len(visual_qa)

    tiktok_result = None
    if video_path and not visual_qa_flagged:
        print(f"=== [{niche}] visual QA clean — uploading to TikTok (sandbox, SELF_ONLY) ===")
        try:
            from verticals.tiktok_upload import upload_to_tiktok
            caption = draft.get("tiktok_caption") or draft.get("youtube_title", "")
            tiktok_url = upload_to_tiktok(Path(video_path), caption)
            tiktok_result = {"ok": True, "url": tiktok_url}
        except Exception as e:
            tiktok_result = {"ok": False, "error": str(e)}
            print(f"  TikTok upload failed: {e}")
    elif visual_qa_flagged:
        print(f"=== [{niche}] visual QA flagged something — skipping TikTok upload ===")

    return {
        "niche": niche, "ok": True, "draft_path": draft_path,
        "video_path": video_path, "title": draft.get("youtube_title", ""),
        "script": draft.get("script", ""), "degraded": degraded,
        "visual_qa": visual_qa, "visual_qa_flagged": visual_qa_flagged,
        "tiktok": tiktok_result,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--niche", choices=NICHES, default=None,
        help="Produce only this niche (gaming/tech run on separate schedules "
             "so each niche gets its own lead time before its own publish "
             "slot). Omit to produce both, as before.",
    )
    args = parser.parse_args()
    niches_to_run = [args.niche] if args.niche else NICHES

    REPORTS_DIR.mkdir(exist_ok=True)
    results = [produce_one(niche) for niche in niches_to_run]

    # Merge into any existing status file rather than overwriting it — when
    # gaming and tech run as separate scheduled jobs (see run_scan_tech.bat /
    # run_scan_gaming.bat), each run only touches its own niche's entry so
    # the other niche's still-fresh result from earlier isn't clobbered.
    prior_results = []
    if STATUS_PATH.exists():
        try:
            prior_results = json.loads(STATUS_PATH.read_text(encoding="utf-8")).get("results", [])
        except Exception:
            prior_results = []
    merged = {r["niche"]: r for r in prior_results}
    for r in results:
        merged[r["niche"]] = r
    all_results = list(merged.values())
    STATUS_PATH.write_text(json.dumps({"ok": all(r["ok"] for r in all_results), "results": all_results}, indent=2), encoding="utf-8")

    for r in results:
        if r["ok"]:
            flag_note = " — VISUAL QA FLAGGED SOMETHING, check before uploading" if r.get("visual_qa_flagged") else ""
            print(f"\n[{r['niche']}] done: {r['video_path']}{flag_note}")
            tk = r.get("tiktok")
            if tk:
                print(f"  TikTok: {'uploaded — ' + tk['url'] if tk['ok'] else 'FAILED — ' + tk['error'][:200]}")
        else:
            print(f"\n[{r['niche']}] FAILED at {r['stage']}: {r['error'][:300]}")

    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

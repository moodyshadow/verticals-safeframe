"""CLI entry point — python -m verticals."""

import argparse
import shutil
import sys
import time
from pathlib import Path

from .config import CONFIG_FILE, DRAFTS_DIR, MEDIA_DIR, PUBLISHED_DIR, run_setup
from .log import log, set_verbose
from .niche import list_niches


def maybe_run_setup(args):
    """Run first-run setup only for commands that need creator credentials.

    Help, niche listing, topic discovery, and local/free-provider paths should
    not block on an interactive setup wizard.
    """
    if CONFIG_FILE.exists() or args.cmd not in {"draft", "run"}:
        return

    provider = getattr(args, "provider", None)
    if provider in {"ollama", "gemini", "openai"}:
        return

    print("  First run detected. Running setup...")
    run_setup()


def cmd_draft(args):
    from .draft import generate_draft
    from .state import PipelineState
    import json

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = str(int(time.time()))

    niche = getattr(args, "niche", "general") or "general"
    platform = getattr(args, "platform", "shorts") or "shorts"
    provider = getattr(args, "provider", None)

    print(f"\n  Drafting: {args.news} [niche: {niche}, platform: {platform}]\n")
    draft = generate_draft(
        args.news,
        getattr(args, "context", ""),
        niche=niche,
        platform=platform,
        provider=provider,
        url=getattr(args, "topic_url", "") or "",
        summary=getattr(args, "topic_summary", "") or "",
        word_count=getattr(args, "word_count", None),
    )
    draft["job_id"] = job_id

    out_path = DRAFTS_DIR / f"{job_id}.json"
    state = PipelineState(draft)
    state.complete_stage("research")
    state.complete_stage("draft")
    state.save(out_path)

    print(f"\n  Draft saved: {out_path}")
    print(f"\n  Script:\n{draft['script']}")
    print(f"\n  Title: {draft.get('youtube_title', '')}")
    print(f"\n  B-roll prompts:")
    for i, p in enumerate(draft.get("broll_prompts", [])):
        print(f"  {i+1}. {p}")

    return out_path


def cmd_produce(args):
    from .broll import generate_broll
    from .tts import generate_voiceover
    from .captions import generate_captions
    from .music import select_and_prepare_music
    from .assemble import assemble_video
    from .niche import load_niche, get_voice_config, get_caption_config, get_music_config
    from .state import PipelineState
    import json
    import shutil

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    job_id = draft["job_id"]
    lang = args.lang
    state = PipelineState(draft)

    # Load niche profile for voice/caption/music config
    niche_name = draft.get("niche", "general")
    profile = load_niche(niche_name)

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = MEDIA_DIR / f"work_{job_id}_{lang}"
    work_dir.mkdir(exist_ok=True)

    force = getattr(args, "force", False)
    tts_provider = getattr(args, "voice", None)
    script = getattr(args, "script", None) or (
        draft.get("script_hi") if lang == "hi" else draft.get("script")
    )

    print(f"\n  Producing {lang.upper()} video for job {job_id} [niche: {niche_name}]")

    # B-roll
    if force or not state.is_done("broll"):
        from .config import BROLL_COUNT
        from .niche import get_visual_source_priority
        default_prompts = [f"Cinematic landscape, variation {i + 1}" for i in range(BROLL_COUNT)]
        use_stock = get_visual_source_priority(profile) != "ai_only"
        frames, fallback_count, used_asset_paths = generate_broll(draft.get("broll_prompts", default_prompts), work_dir, use_stock=use_stock)
        if fallback_count:
            log(f"WARNING: {fallback_count}/{len(frames)} b-roll frames used the plain gradient fallback (image generation failed)")

        # Optional per-slot override: draft["broll_multi_image_overrides"] is
        # {"<frame index>": ["path1", "path2", ...]} — lets a slot show a
        # crossfading sequence across several manually-picked real photos/
        # clips instead of generate_broll()'s single best automatic match,
        # for when more than one genuinely good, distinct real item exists
        # for the same beat.
        overrides = draft.get("broll_multi_image_overrides", {})
        for idx_str, paths in overrides.items():
            idx = int(idx_str)
            if 0 <= idx < len(frames):
                frames[idx] = [Path(p) for p in paths]

        def _serialize_frame(f):
            return [str(x) for x in f] if isinstance(f, (list, tuple)) else str(f)

        state.complete_stage("broll", {"frames": [_serialize_frame(f) for f in frames], "fallback_count": fallback_count, "used_asset_paths": used_asset_paths})
        draft["broll_fallback_count"] = fallback_count
        draft["broll_frame_count"] = len(frames)
    else:
        log("Skipping b-roll (already done)")
        raw_frames = state.get_artifact("broll", "frames", [])
        frames = [[Path(x) for x in f] if isinstance(f, list) else Path(f) for f in raw_frames]
        draft["broll_fallback_count"] = state.get_artifact("broll", "fallback_count", 0)
        draft["broll_frame_count"] = len(frames)

    # Voiceover (niche-aware voice selection)
    if force or not state.is_done("voiceover"):
        voice_config = get_voice_config(
            profile,
            provider=tts_provider or "edge_tts",
            lang=lang,
        )
        vo_path = generate_voiceover(
            script, work_dir, lang,
            provider=tts_provider,
            voice_config=voice_config,
        )
        state.complete_stage("voiceover", {"path": str(vo_path)})
    else:
        log("Skipping voiceover (already done)")
        vo_path = Path(state.get_artifact("voiceover", "path"))

    # Whisper + Captions (niche-aware styling)
    caption_config = get_caption_config(profile)
    if force or not state.is_done("captions"):
        captions_result = generate_captions(
            vo_path, work_dir, lang,
            highlight_color=caption_config.get("highlight_color", "#FFFF00"),
            words_per_group=caption_config.get("words_per_group", 4),
            font_family=caption_config.get("font_family", "Arial"),
            font_size=int(caption_config.get("font_size", 72)),
            script=script,
        )
        state.complete_stage("captions", {
            "srt_path": str(captions_result.get("srt_path", "")),
            "ass_path": str(captions_result.get("ass_path", "")),
            "words": captions_result.get("words", []),
        })
    else:
        log("Skipping captions (already done)")
        captions_result = {
            "srt_path": state.get_artifact("captions", "srt_path", ""),
            "ass_path": state.get_artifact("captions", "ass_path", ""),
            "words": state.get_artifact("captions", "words", []),
        }

    # Music (niche-aware mood/ducking)
    music_config = get_music_config(profile)
    if force or not state.is_done("music"):
        music_result = select_and_prepare_music(
            vo_path, work_dir, niche=niche_name,
            duck_speech=music_config.get("duck_volume_speech", 0.12),
            duck_gap=music_config.get("duck_volume_gap", 0.25),
        )
        state.complete_stage("music", {
            "track_path": str(music_result.get("track_path", "")),
            "duck_filter": music_result.get("duck_filter", ""),
            "music_credit": music_result.get("music_credit", ""),
        })
    else:
        log("Skipping music (already done)")
        music_result = {
            "track_path": state.get_artifact("music", "track_path", ""),
            "duck_filter": state.get_artifact("music", "duck_filter", ""),
            "music_credit": state.get_artifact("music", "music_credit", ""),
        }
    if music_result.get("music_credit"):
        draft["music_credit"] = music_result["music_credit"]

    # Assemble
    if force or not state.is_done("assemble"):
        # Optional per-slot punch-in: draft["broll_punch_in_frames"] is a
        # list of 0-based frame indices that should get a slow continuous
        # zoom instead of holding a static locked-off framing for the whole
        # slot — added after a real, otherwise-good stock clip (a still
        # product shot) read as "stale" held at one framing the whole time.
        punch_in_frames = {int(i) for i in draft.get("broll_punch_in_frames", [])}
        video_path = assemble_video(
            frames=frames,
            voiceover=vo_path,
            out_dir=work_dir,
            job_id=job_id,
            lang=lang,
            ass_path=captions_result.get("ass_path"),
            music_path=music_result.get("track_path"),
            duck_filter=music_result.get("duck_filter"),
            srt_path=captions_result.get("srt_path"),
            punch_in_frames=punch_in_frames,
            words=captions_result.get("words"),
        )
        state.complete_stage("assemble", {"video_path": str(video_path)})

        # Vision-model-first QA pass over the actual finished video — catches
        # what the per-image generation-time check can't see (a tiled/collage
        # composition, captions running past where the video cuts to the
        # outro, a stock clip that's simply the wrong content). Fails open,
        # never blocks production — findings are surfaced to `review` so
        # whoever marks a draft reviewed sees the vision model's read first.
        from .video_qa import qa_assembled_video, summarize_findings
        log("Running vision-model QA pass over the assembled video...")
        qa_findings = qa_assembled_video(video_path, work_dir)
        log("  " + summarize_findings(qa_findings).replace("\n", "\n  "))
        state.complete_stage("video_qa", {"findings": qa_findings})
    else:
        log("Skipping assembly (already done)")
        video_path = Path(state.get_artifact("assemble", "video_path"))

    # Save SRT to media dir
    srt_path = captions_result.get("srt_path")
    if srt_path and Path(srt_path).exists():
        final_srt = MEDIA_DIR / f"verticals_{job_id}_{lang}.srt"
        shutil.copy(srt_path, final_srt)
        draft[f"srt_{lang}"] = str(final_srt)

    draft[f"video_{lang}"] = str(video_path)
    state.save(draft_path)

    print(f"\n  Video: {video_path}")
    return video_path


def cmd_upload(args):
    from .upload import upload_to_youtube
    from .thumbnail import generate_thumbnail
    from .state import PipelineState
    import json

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    lang = args.lang
    state = PipelineState(draft)
    force = getattr(args, "force", False)

    video_path = Path(draft.get(f"video_{lang}", ""))
    srt_path_str = draft.get(f"srt_{lang}")
    srt_path = Path(srt_path_str) if srt_path_str else None

    # Review gate (added 2026-09-02): the automated llava vision-QA check
    # has repeatedly missed real defects this project has hit (halos from
    # bad compositing, reused/duplicate b-roll frames, a garbled caption
    # word) that only turned up once a person actually looked at the
    # frames. Requiring an explicit human/Claude review before every
    # upload — not just an automated score — closes that gap. Set via
    # `python -m verticals review --draft <path>` (or --skip-review to
    # bypass deliberately, e.g. for an already-reviewed re-upload).
    if not draft.get("reviewed_by_claude") and not getattr(args, "skip_review", False):
        print(
            "\n  BLOCKED: this draft hasn't been marked as reviewed.\n"
            "  Before uploading, have Claude actually look at the finished "
            "video's frames (not just the automated vision-QA score) and "
            "confirm it's clean, then run:\n"
            f"    python -m verticals review --draft {draft_path}\n"
            "  Or pass --skip-review to upload anyway (only if you've "
            "already reviewed it yourself).\n"
        )
        sys.exit(1)

    if not video_path.exists():
        print(f"  No produced video found for lang={lang}. Run produce first.")
        sys.exit(1)

    # Thumbnail
    thumb_path = None
    if force or not state.is_done("thumbnail"):
        try:
            thumb_path = generate_thumbnail(draft, MEDIA_DIR)
            state.complete_stage("thumbnail", {"path": str(thumb_path)})
        except Exception as e:
            log(f"Thumbnail generation failed: {e} — uploading without thumbnail")
    else:
        thumb_p = state.get_artifact("thumbnail", "path", "")
        if thumb_p and Path(thumb_p).exists():
            thumb_path = Path(thumb_p)

    # Upload
    if force or not state.is_done("upload"):
        url = upload_to_youtube(video_path, draft, srt_path, lang, thumb_path)
        state.complete_stage("upload", {"url": url})
        try:
            from .tracking import log_video_metadata
            log_video_metadata(draft, url, lang)
        except Exception as e:
            log(f"Performance tracking log failed (non-fatal): {e}")
    else:
        url = state.get_artifact("upload", "url", "")
        log(f"Skipping upload (already done): {url}")

    draft[f"youtube_url_{lang}"] = url

    # Advance the 7-day reuse cooldown for this job's b-roll now, at actual
    # YouTube upload time — not by waiting for the next scheduled
    # cleanup_published.py run to confirm the video went public. Found a
    # real bug from that delay: cleanup only runs once daily, so a video
    # that went public last night wasn't marked "used" until the next
    # morning's cleanup, leaving an hours-long window where another video
    # produced overnight could freely re-pick the exact same clip (caught
    # in practice — a server-room clip from an already-live video got
    # reused in a same-morning job). A successful upload here always sets
    # a scheduled publishAt (see upload_to_youtube), so it's a reliable
    # enough signal of "this is really going out" without needing to wait
    # for after-the-fact confirmation — same reasoning as why a discarded/
    # never-uploaded draft still doesn't burn its cooldown (this line never
    # runs for those). Deliberately NOT done for the automated pipeline's
    # TikTok-only cross-post (see run_two_niches.py) since that uses
    # sandbox/SELF_ONLY visibility — private, not real public distribution.
    try:
        from .media_library import mark_used
        used_paths = draft.get("_pipeline_state", {}).get("broll", {}).get("artifacts", {}).get("used_asset_paths", [])
        for path in used_paths:
            mark_used(path)
    except Exception as e:
        log(f"Marking b-roll assets as used failed (non-fatal): {e}")

    # Archive the exact video/thumbnail that just went live. MEDIA_DIR is
    # scratch space that later jobs reuse/overwrite, so a cross-post done
    # any time after this run (not inline, like TikTok/Instagram below) can
    # silently grab a stale file that no longer matches YouTube — this
    # happened for real on 2026-09-02. PUBLISHED_DIR is write-once per job
    # and is the only path cross-posting should trust after the fact.
    try:
        PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
        job_id = draft["job_id"]
        archived_video = PUBLISHED_DIR / f"{job_id}_{lang}{video_path.suffix}"
        if not archived_video.exists():
            shutil.copy2(video_path, archived_video)
        draft[f"published_video_path_{lang}"] = str(archived_video)
        if thumb_path and Path(thumb_path).exists():
            archived_thumb = PUBLISHED_DIR / f"{job_id}_thumb{Path(thumb_path).suffix}"
            if not archived_thumb.exists():
                shutil.copy2(thumb_path, archived_thumb)
            draft["published_thumb_path"] = str(archived_thumb)
        log(f"Archived published copy: {archived_video.name}")
    except Exception as e:
        log(f"Archiving published copy failed (non-fatal): {e}")

    # TikTok cross-post: keeps the TikTok library matched to YouTube instead
    # of needing a separate manual push every time. Non-fatal — a TikTok
    # failure shouldn't undo an otherwise-successful YouTube upload.
    # PUBLIC_TO_EVERYONE as of 2026-09-04 — the app passed TikTok's review,
    # so the SELF_ONLY sandbox restriction no longer applies (see
    # tiktok_upload.py and scripts/setup_tiktok_oauth.py; requires the token
    # in ~/.verticals/tiktok_token.json to have been issued against the
    # PRODUCTION client_key/secret, not the sandbox one — a sandbox-issued
    # token is still capped at SELF_ONLY regardless of this parameter).
    if not draft.get("tiktok_publish_id"):
        try:
            from .tiktok_upload import upload_to_tiktok
            caption = draft.get("tiktok_caption") or draft.get("youtube_title", "")
            draft["tiktok_publish_id"] = upload_to_tiktok(video_path, caption, privacy_level="PUBLIC_TO_EVERYONE")
            log(f"Also posted to TikTok: {draft['tiktok_publish_id']}")
        except Exception as e:
            log(f"TikTok cross-post failed (non-fatal, YouTube upload still succeeded): {e}")

    # Instagram Reels cross-post deliberately does NOT happen here. Unlike
    # TikTok (which accepts an immediate SELF_ONLY-visibility upload),
    # Instagram's API has no scheduling — a call to media_publish goes live
    # immediately. YouTube uploads here are usually scheduled for a future
    # publishAt (still private until then, see upload_to_youtube), so
    # cross-posting at upload time made the Instagram post go out before
    # the video was actually public on YouTube, regardless of review timing
    # (caught for real on 2026-09-04). publish_instagram_backlog.py (a local
    # Task Scheduler job, see setup notes) handles this instead, checking
    # each candidate's actual YouTube privacyStatus and only posting once
    # it's really public.

    state.save(draft_path)
    print(f"\n  Live: {url}")
    return url


def cmd_review(args):
    """Record that a draft's video was actually looked at (frames, not
    just the automated vision-QA score) — required before `upload` will
    proceed. See the comment in cmd_upload for why this exists."""
    import json
    from datetime import datetime, timezone

    draft_path = Path(args.draft)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))

    # Show the vision model's read first — ask the LLM before the human/
    # Claude decision, not after. Purely informational: this never blocks
    # `review` from proceeding, since the per-frame check fails open too.
    qa_state = draft.get("_pipeline_state", {}).get("video_qa", {})
    qa_findings = qa_state.get("artifacts", {}).get("findings")
    if qa_findings:
        from .video_qa import summarize_findings
        print(f"\n  {summarize_findings(qa_findings)}\n")
    else:
        print("\n  (no automated video QA findings on record for this draft)\n")

    draft["reviewed_by_claude"] = True
    draft["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    if getattr(args, "note", ""):
        draft["review_note"] = args.note
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Marked reviewed: {draft_path}")


def cmd_run(args):
    draft_path = cmd_draft(args)
    if args.dry_run:
        print("  Dry run — skipping produce + upload")
        return

    class ProduceArgs:
        draft = str(draft_path)
        lang = args.lang
        script = None
        force = False
        voice = getattr(args, "voice", None)

    video_path = cmd_produce(ProduceArgs())

    class UploadArgs:
        draft = str(draft_path)
        lang = args.lang
        force = False

    url = cmd_upload(UploadArgs())
    print(f"\n  Done! {url}")


def cmd_topics(args):
    from .topics import TopicEngine

    niche = getattr(args, "niche", "general") or "general"
    engine = TopicEngine(niche=niche)
    candidates = engine.discover(limit=getattr(args, "limit", 15))

    if not candidates:
        print("  No topics found from enabled sources.")
        return

    print(f"\n  Trending topics for [{niche}] ({len(candidates)} found):\n")
    for i, topic in enumerate(candidates, 1):
        score = f" [{topic.trending_score:.2f}]" if topic.trending_score else ""
        print(f"  {i:2d}. [{topic.source}] {topic.title}{score}")
        if topic.summary:
            print(f"      {topic.summary[:100]}")


def cmd_niches(args):
    """List all available niche profiles."""
    niches = list_niches()
    print(f"\n  Available niches ({len(niches)}):\n")
    for n in niches:
        from .niche import load_niche
        profile = load_niche(n)
        display = profile.get("display_name", n)
        desc = profile.get("description", "")[:80]
        print(f"    {n:20s}  {display}")
        if desc:
            print(f"    {' ':20s}  {desc}")


def cmd_voices(args):
    """List voices available for a TTS provider.

    Currently only 60db is supported — it exposes GET /myvoices and
    GET /default-voices. Edge TTS voices are language-coded strings (see
    EDGE_VOICES in tts.py); ElevenLabs voice IDs come from the ElevenLabs
    dashboard.
    """
    provider = (args.provider or "").lower()
    if provider not in ("60db", "sixtydb"):
        print("  Error: --provider 60db is the only listing currently supported.")
        print("  Edge voices: see EDGE_VOICES in verticals/tts.py.")
        print("  ElevenLabs voices: https://elevenlabs.io/app/voice-library")
        sys.exit(1)

    import requests
    from .config import get_60db_key

    api_key = get_60db_key()
    if not api_key:
        print("  Error: SIXTYDB_API_KEY not set. Run setup or export the env var.")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {api_key}"}
    endpoints = [
        ("Default voices", "https://api.60db.ai/default-voices"),
        ("My voices",      "https://api.60db.ai/myvoices"),
    ]

    def _print_voice_row(v: dict):
        labels = v.get("labels") or {}
        lang = labels.get("language_name") or labels.get("language") or "?"
        gender = labels.get("gender") or "?"
        accent = labels.get("accent") or "?"
        model = v.get("model") or "?"
        category = v.get("category") or "?"
        name = v.get("name") or "?"
        vid = v.get("voice_id") or "?"
        print(f"    {vid}  {name:18.18}  {lang:10.10}  {gender:6.6}  {accent:10.10}  {model:14.14}  {category}")

    for title, url in endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except Exception as exc:
            print(f"\n  {title}: request failed — {exc}")
            continue
        if r.status_code != 200:
            print(f"\n  {title}: HTTP {r.status_code} — {r.text[:120]}")
            continue
        body = r.json()
        items = body.get("data") or []
        print(f"\n  {title} ({len(items)}):")
        if not items:
            print("    (none)")
            continue
        print(f"    {'voice_id':36}  {'name':18}  {'language':10}  {'gender':6}  {'accent':10}  {'model':14}  category")
        print(f"    {'-' * 36}  {'-' * 18}  {'-' * 10}  {'-' * 6}  {'-' * 10}  {'-' * 14}  {'-' * 8}")
        for v in items:
            _print_voice_row(v)


def main():
    parser = argparse.ArgumentParser(
        description="Verticals v3 — AI-Native Vertical Video Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Docs: https://github.com/rushindrasinha/verticals\n"
               "Product: https://verticals.gg",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="cmd")

    # Shared niche/provider args
    niche_help = f"Content niche ({', '.join(list_niches()[:8])}...)"

    # draft
    p_draft = sub.add_parser("draft", help="Generate script + metadata")
    p_draft.add_argument("--topic", "--news", dest="news", required=False, help="Topic/news headline")
    p_draft.add_argument("--context", default="", help="Channel context")
    p_draft.add_argument("--niche", default="general", help=niche_help)
    p_draft.add_argument("--platform", default="shorts", choices=["shorts", "reels", "tiktok", "all"])
    p_draft.add_argument("--provider", default=None, help="LLM: claude, gemini, openai, ollama")
    p_draft.add_argument("--word-count", dest="word_count", default=None,
                          help="Override script length, e.g. '80-100' or '200' (default: the niche's own target)")
    p_draft.add_argument("--discover", action="store_true", help="Use topic engine")
    p_draft.add_argument("--auto-pick", action="store_true", help="Let LLM pick the best topic")
    p_draft.add_argument("--dry-run", action="store_true", help="Draft only")

    # produce
    p_produce = sub.add_parser("produce", help="Generate video from draft")
    p_produce.add_argument("--draft", required=True)
    p_produce.add_argument("--lang", default="en", choices=["en", "hi", "es", "pt", "de", "fr", "ja", "ko"])
    p_produce.add_argument("--voice", default=None, help="TTS: edge, elevenlabs, 60db, say")
    p_produce.add_argument("--script", default=None, help="Override script text")
    p_produce.add_argument("--force", action="store_true", help="Redo all stages")

    # upload
    p_upload = sub.add_parser("upload", help="Upload to YouTube")
    p_upload.add_argument("--draft", required=True)
    p_upload.add_argument("--lang", default="en", choices=["en", "hi", "es", "pt", "de", "fr", "ja", "ko"])
    p_upload.add_argument("--force", action="store_true", help="Re-upload even if done")
    p_upload.add_argument("--skip-review", action="store_true", help="Bypass the Claude-review gate")

    # review — mark a draft as having actually been looked at (frames
    # inspected, not just the automated vision-QA score) before it's
    # allowed through the upload gate.
    p_review = sub.add_parser("review", help="Mark a draft as reviewed (required before upload)")
    p_review.add_argument("--draft", required=True)
    p_review.add_argument("--note", default="", help="Optional note on what was checked")

    # run (full pipeline)
    p_run = sub.add_parser("run", help="Full pipeline: draft -> produce -> upload")
    p_run.add_argument("--topic", "--news", dest="news", required=False, help="Topic/news headline")
    p_run.add_argument("--niche", default="general", help=niche_help)
    p_run.add_argument("--platform", default="shorts", choices=["shorts", "reels", "tiktok", "all"])
    p_run.add_argument("--provider", default=None, help="LLM: claude, gemini, openai, ollama")
    p_run.add_argument("--word-count", dest="word_count", default=None,
                        help="Override script length, e.g. '80-100' or '200' (default: the niche's own target)")
    p_run.add_argument("--voice", default=None, help="TTS: edge, elevenlabs, 60db, say")
    p_run.add_argument("--lang", default="en", choices=["en", "hi", "es", "pt", "de", "fr", "ja", "ko"])
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--context", default="")
    p_run.add_argument("--discover", action="store_true")
    p_run.add_argument("--auto-pick", action="store_true")

    # topics
    p_topics = sub.add_parser("topics", help="Discover trending topics")
    p_topics.add_argument("--niche", default="general", help=niche_help)
    p_topics.add_argument("--limit", type=int, default=15, help="Max topics to show")

    # niches
    sub.add_parser("niches", help="List available niche profiles")

    # voices
    p_voices = sub.add_parser("voices", help="List TTS voices (currently: 60db)")
    p_voices.add_argument("--provider", default="60db", help="TTS provider (only '60db' supported)")

    args = parser.parse_args()

    if args.verbose:
        set_verbose(True)

    if not args.cmd:
        parser.print_help()
        return

    # Handle utility commands that don't need first-run setup
    if args.cmd == "niches":
        cmd_niches(args)
        return
    if args.cmd == "voices":
        cmd_voices(args)
        return

    maybe_run_setup(args)

    # Handle --discover flag for draft/run
    if args.cmd in ("draft", "run") and getattr(args, "discover", False):
        from .topics import TopicEngine
        niche = getattr(args, "niche", "general") or "general"
        engine = TopicEngine(niche=niche)
        candidates = engine.discover(limit=15)
        if not candidates:
            print("  No trending topics found. Use --topic instead.")
            sys.exit(1)

        def _apply_candidate(c):
            args.news = c.title
            args.topic_url = c.url
            args.topic_summary = c.summary

        if getattr(args, "auto_pick", False):
            picked_title = engine.auto_pick(candidates)
            match = next((c for c in candidates if c.title == picked_title), None)
            if match:
                _apply_candidate(match)
            else:
                args.news = picked_title
            print(f"  Auto-picked: {args.news}")
        else:
            print("\n  Trending topics:\n")
            for i, t in enumerate(candidates, 1):
                print(f"  {i:2d}. [{t.source}] {t.title}")
            choice = input("\n  Pick a number (or enter custom topic): ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                _apply_candidate(candidates[int(choice) - 1])
            else:
                args.news = choice
    elif args.cmd in ("draft", "run") and not getattr(args, "news", None):
        print("  Error: --topic or --discover required")
        sys.exit(1)

    # draft/produce are the stages that touch a GPU (Ollama on GPU 1, the
    # SD webui on GPU 0 — see joblock.py) — lock each against any other
    # pipeline invocation using the *same* GPU (a manual redo, the 2am cron
    # job, run_full_pipeline.py) so two jobs never share one physical card.
    # "run" does both stages itself (see cmd_run) so each sub-stage locks
    # only its own GPU rather than blocking the whole run behind one lock —
    # that would defeat the point of having draft (GPU 1) and another job's
    # produce (GPU 0) run at the same time. upload/topics are
    # network/read-only and don't need a lock at all.
    if args.cmd == "draft":
        from .joblock import job_lock
        with job_lock("draft"):
            cmd_draft(args)
    elif args.cmd == "produce":
        from .joblock import job_lock
        with job_lock("produce"):
            cmd_produce(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "upload":
        cmd_upload(args)
    elif args.cmd == "review":
        cmd_review(args)
    elif args.cmd == "topics":
        cmd_topics(args)


if __name__ == "__main__":
    main()

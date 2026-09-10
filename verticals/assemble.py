"""ffmpeg video assembly — frames + voiceover + music + captions."""

import re
from pathlib import Path

from .branding import LOGO_PATH, get_outro_clip, get_platform_button_overlay
from .broll import animate_frame, animate_multi_image_frame, prepare_video_clip
from .config import MEDIA_DIR, VIDEO_HEIGHT, VIDEO_WIDTH, run_cmd
from .log import log

WATERMARK_WIDTH = round(VIDEO_WIDTH * 0.15)
WATERMARK_OPACITY = 0.55
WATERMARK_MARGIN = 28

# Any of these appearing (case-insensitive, punctuation stripped) marks
# where the spoken CTA begins, regardless of which cta_variant (pick_a_side/
# prediction/stay_overclocked) the script used — see niches/*.yaml's
# cta_variants. Only scanned from _CTA_SEARCH_FRACTION onward so an
# unrelated earlier use of "comment" in the main content (e.g. "a comment
# thread") can't be mistaken for the actual CTA.
_CTA_TRIGGER_WORDS = ("subscribe", "comment")
_CTA_SEARCH_FRACTION = 0.5


def _shift_duck_filter(duck_filter: str, offset: float) -> str:
    """Shift a duck_filter's between(t, start, end) windows by `offset` seconds.

    duck_filter's timestamps are relative to the voiceover's own start (t=0).
    Once the video timeline is prefixed with an intro bumper, the voiceover
    actually starts `offset` seconds in, so the ducking windows need to move
    with it or music will duck at the wrong moments.
    """
    def _shift(m):
        start, end = float(m.group(1)), float(m.group(2))
        return f"between(t,{start + offset:.2f},{end + offset:.2f})"

    return re.sub(r"between\(t,([\d.]+),([\d.]+)\)", _shift, duck_filter)


def _ffmpeg_has_libass() -> bool:
    """Check whether this ffmpeg build ships the `ass` filter (libass).

    Some builds (e.g. minimal/static ones) omit libass; burning captions in
    would fail with `No such filter: 'ass'`, so we skip burn-in instead.
    """
    try:
        r = run_cmd(["ffmpeg", "-hide_banner", "-filters"], capture=True)
        return any(line.split()[1:2] == ["ass"] for line in r.stdout.splitlines())
    except Exception:
        return False


def _find_cta_popup_time(words: list[dict] | None, duration: float) -> float:
    """When the spoken CTA begins, in content-timeline seconds (t=0 at the
    voiceover's own start, same as duck_filter's windows before shifting).

    Falls back to the last 3 seconds of content if word timestamps aren't
    available or no trigger word is found — the button should always show
    up before the outro, never silently not appear at all.
    """
    fallback = max(duration - 3.0, 0.0)
    if not words:
        return fallback

    search_from = duration * _CTA_SEARCH_FRACTION
    for w in words:
        word = w.get("word", "").strip(" .,!?:;\"'").lower()
        if word in _CTA_TRIGGER_WORDS and w.get("start", 0) >= search_from:
            return w["start"]
    return fallback


def get_audio_duration(path: Path) -> float:
    """Get duration of an audio file in seconds."""
    r = run_cmd(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture=True,
    )
    return float(r.stdout.strip())


_SRT_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
_ASS_TS = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")


def _shift_srt(path: Path, offset: float) -> None:
    """Shift every SRT timestamp forward by `offset` seconds, in place."""
    def _shift(m):
        h, mi, s, ms = (int(g) for g in m.groups())
        total = h * 3600 + mi * 60 + s + ms / 1000 + offset
        h2, total = divmod(total, 3600)
        mi2, total = divmod(total, 60)
        s2 = int(total)
        ms2 = round((total - s2) * 1000)
        return f"{int(h2):02d}:{int(mi2):02d}:{s2:02d},{ms2:03d}"

    text = path.read_text(encoding="utf-8")
    path.write_text(_SRT_TS.sub(_shift, text), encoding="utf-8")


def _shift_ass(path: Path, offset: float) -> Path:
    """Write a copy of an ASS file with every timestamp shifted forward.

    Used for burn-in only — the original stays untouched since it may be
    regenerated/reused across reruns; the shifted copy is a throwaway.
    """
    def _shift(m):
        h, mi, s, cs = (int(g) for g in m.groups())
        total = h * 3600 + mi * 60 + s + cs / 100 + offset
        h2, total = divmod(total, 3600)
        mi2, total = divmod(total, 60)
        s2 = int(total)
        cs2 = round((total - s2) * 100)
        return f"{int(h2)}:{int(mi2):02d}:{s2:02d}.{cs2:02d}"

    text = path.read_text(encoding="utf-8")
    shifted = path.with_name(path.stem + "_shifted" + path.suffix)
    shifted.write_text(_ASS_TS.sub(_shift, text), encoding="utf-8")
    return shifted


def assemble_video(
    frames: list[Path],
    voiceover: Path,
    out_dir: Path,
    job_id: str,
    lang: str = "en",
    ass_path: str | None = None,
    music_path: str | None = None,
    duck_filter: str | None = None,
    srt_path: str | None = None,
    punch_in_frames: set[int] | None = None,
    words: list[dict] | None = None,
    platform: str = "youtube",
    frame_durations: list[float] | None = None,
) -> Path:
    """Assemble final video from frames, voiceover, captions, and music.

    Every video is bookended with the cached channel-logo bumpers (see
    branding.py) — the content timeline is delayed to sit after the intro
    and the whole thing (captions, ducking, audio length) is shifted to match.

    frame_durations: optional per-frame screen-time override (seconds),
    one entry per frame, same length as `frames`. Must sum to the
    voiceover's duration — this only lets you redistribute time between
    frames, not change the video's total length. Defaults to an even split.
    """
    log("Assembling video...")
    duration = get_audio_duration(voiceover)
    if frame_durations:
        if len(frame_durations) != len(frames):
            raise ValueError(
                f"frame_durations has {len(frame_durations)} entries but there are {len(frames)} frames"
            )
        per_frame_list = frame_durations
    else:
        per_frame_list = [duration / len(frames)] * len(frames)
    effects = ["zoom_in", "pan_right", "zoom_out"]
    punch_in_frames = punch_in_frames or set()
    cta_popup_time = _find_cta_popup_time(words, duration)

    # Animate each still frame with Ken Burns; real video clips get
    # cropped/trimmed to the slot duration instead (already have motion).
    # A slot can also be a list of multiple image paths — a crossfading
    # Ken Burns sequence across all of them — for when several genuinely
    # good, distinct real photos exist for the same beat instead of just one.
    animated = []
    for i, frame in enumerate(frames):
        anim = out_dir / f"anim_{i}.mp4"
        slot = per_frame_list[i] + 0.1
        if isinstance(frame, (list, tuple)):
            animate_multi_image_frame(list(frame), anim, slot, out_dir)
        elif Path(frame).suffix.lower() == ".mp4":
            prepare_video_clip(frame, anim, slot, punch_in=i in punch_in_frames)
        else:
            animate_frame(frame, anim, slot, effects[i % len(effects)])
        animated.append(anim)

    # 2026-09-09: dropped the intro logo bumper — a static logo before the
    # hook plays works against the script's own "no intro, straight into
    # the tension" structure guidance, and every second matters for Shorts
    # retention. intro_dur now shifts nothing.
    #
    # Also trying without the outro close-in bumper: the loop-back script
    # ending (see draft.py's "Loop quality" rule) is meant to land right
    # before the video repeats, but the ~1.5s branded logo close-in sits
    # between the punchline and the actual loop point, softening the
    # effect. Deliberately left as a one-line toggle (not deleted) in case
    # this doesn't work out and the outro needs to come back.
    USE_OUTRO_BUMPER = False
    intro_dur = 0.0

    # Concat content (+ outro, if enabled) — escape single quotes for the concat demuxer
    concat_file = out_dir / "concat.txt"
    def _esc(p):
        return str(p).replace("'", "'\\''" )
    concat_list = animated + ([get_outro_clip()] if USE_OUTRO_BUMPER else [])
    concat_file.write_text("\n".join(f"file '{_esc(p)}'" for p in concat_list), encoding="utf-8")

    merged_video = out_dir / "merged_video.mp4"
    run_cmd([
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(merged_video), "-y", "-loglevel", "quiet",
    ])

    # Each content clip gets a small +0.1s padding buffer to avoid gaps at
    # cuts, so merged_video ends up slightly longer than intro+duration+outro.
    # Trim to that *assumed* length would cut into the outro before it finishes
    # closing — measure the real merged length instead so the outro's full
    # close-in survives to the final output.
    full_duration = get_audio_duration(merged_video)

    # Build the final ffmpeg command with optional captions + music.
    # "youtube" keeps the original unsuffixed filename other code already
    # references (draft["video_en"] etc.) — other platforms get their own
    # file since each needs a different button overlay burned in.
    suffix = "" if platform == "youtube" else f"_{platform}"
    out_path = MEDIA_DIR / f"verticals_{job_id}_{lang}{suffix}.mp4"

    # Video chain: burn in captions (if available), then overlay the small
    # corner watermark — only during the main content window, since the
    # full logo already fills the screen during the intro/outro bumpers.
    video_parts = []
    video_src = "[0:v]"
    if ass_path and Path(ass_path).exists():
        if _ffmpeg_has_libass():
            shifted_ass = _shift_ass(Path(ass_path), intro_dur)
            escaped_ass = str(shifted_ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            video_parts.append(f"{video_src}ass='{escaped_ass}'[capped]")
            video_src = "[capped]"
        else:
            log(
                "WARNING: this ffmpeg build has no libass — captions will NOT "
                "be burned in. The SRT is still uploaded to YouTube. Install "
                "an ffmpeg with libass (brew/apt builds include it) for "
                "burned-in captions."
            )

    # The uploaded SRT describes the final video's timeline too, so it needs
    # the same shift (in place — it's copied to its final destination after this).
    if srt_path and Path(srt_path).exists():
        _shift_srt(Path(srt_path), intro_dur)

    intro_ms = round(intro_dur * 1000)

    def _add_overlays(inputs: list, start_index: int) -> str:
        """Corner watermark (content window only) + the subscribe/follow button.

        The button pops in right as the CTA line starts (see
        _find_cta_popup_time) and — unlike the watermark — has no end
        bound, so it stays on screen through the outro bumper instead of
        disappearing the moment the main content ends.

        The button asset is platform-specific (see branding.get_platform_
        button_overlay): a custom animated file (gif/mp4/webm/mov) dropped
        in branding/custom_buttons/ for this platform, or — until one
        exists — the plain generated full-frame SUBSCRIBE pill. A custom
        asset is its own native (small) size and gets scaled + centered;
        the generated fallback already fills the frame with the button
        placed internally, so it's overlaid at 0:0 unscaled as before.
        """
        button_path, is_animated = get_platform_button_overlay(platform)
        is_custom = button_path.parent.name == "custom_buttons"
        if is_animated:
            inputs[:] = inputs + [
                "-loop", "1", "-i", str(LOGO_PATH),
                "-stream_loop", "-1", "-i", str(button_path),
            ]
        else:
            inputs[:] = inputs + [
                "-loop", "1", "-i", str(LOGO_PATH),
                "-loop", "1", "-i", str(button_path),
            ]
        wm_index, btn_index = start_index, start_index + 1
        video_parts.append(
            f"[{wm_index}:v]scale={WATERMARK_WIDTH}:-1,format=rgba,"
            f"colorchannelmixer=aa={WATERMARK_OPACITY}[wm]"
        )
        video_parts.append(
            f"{video_src}[wm]overlay=W-w-{WATERMARK_MARGIN}:{WATERMARK_MARGIN}:"
            f"enable='between(t,{intro_dur:.2f},{intro_dur + duration:.2f})'[wmed]"
        )
        popup_at = intro_dur + cta_popup_time
        if is_custom:
            btn_target_w = round(VIDEO_WIDTH * 0.55)
            video_parts.append(f"[{btn_index}:v]scale={btn_target_w}:-1,format=rgba[btn]")
            btn_y = round(VIDEO_HEIGHT * 0.15)
            video_parts.append(
                f"[wmed][btn]overlay=(W-w)/2:{btn_y}:enable='gte(t,{popup_at:.2f})'[vout]"
            )
        else:
            video_parts.append(
                f"[wmed][{btn_index}:v]overlay=0:0:enable='gte(t,{popup_at:.2f})'[vout]"
            )
        return ";".join(video_parts)

    if music_path and Path(music_path).exists():
        # Base inputs: video, voiceover, music — logo gets prepended by _add_watermark.
        base_inputs = ["-i", str(merged_video), "-i", str(voiceover),
                       "-stream_loop", "-1", "-i", str(music_path)]

        # Loop music across the *whole* video (plays under the bumpers too),
        # ducking only during the shifted speech windows.
        music_filter = f"[2:a]aloop=loop=-1:size=2e+09,atrim=0:{full_duration}"
        shifted_duck = _shift_duck_filter(duck_filter, intro_dur) if duck_filter else None
        if shifted_duck:
            music_filter += f",{shifted_duck}"
        music_filter += "[music]"

        # Delay the voiceover so it starts after the intro, pad it to the
        # full length, then mix with the (also full-length) ducked music.
        voice_filter = (
            f"[1:a]adelay={intro_ms}:all=1,apad=whole_dur={full_duration}[voice]"
        )

        audio_filter = (
            f"{music_filter};{voice_filter};"
            f"[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

        video_filter = _add_overlays(base_inputs, start_index=3)
        cmd = ["ffmpeg"] + base_inputs + [
            "-filter_complex", f"{audio_filter};{video_filter}",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-t", str(full_duration),
            str(out_path), "-y", "-loglevel", "quiet",
        ]
    else:
        # Base inputs: video, voiceover — logo gets prepended by _add_watermark.
        base_inputs = ["-i", str(merged_video), "-i", str(voiceover)]

        voice_filter = (
            f"[1:a]adelay={intro_ms}:all=1,apad=whole_dur={full_duration}[aout]"
        )

        video_filter = _add_overlays(base_inputs, start_index=2)
        cmd = ["ffmpeg"] + base_inputs + [
            "-filter_complex", f"{voice_filter};{video_filter}",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264",
            "-c:a", "aac", "-t", str(full_duration),
            str(out_path), "-y", "-loglevel", "quiet",
        ]

    run_cmd(cmd)
    log(f"Video assembled: {out_path}")
    return out_path

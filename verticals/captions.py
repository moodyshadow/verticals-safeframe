"""Whisper word-level timestamps + ASS subtitle generation + Pillow fallback."""

from pathlib import Path

from .log import log


def _has_ass_filter() -> bool:
    """Check if ffmpeg has libass (for ASS subtitle burn-in)."""
    import subprocess
    try:
        r = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True, text=True, timeout=5,
        )
        return "ass" in r.stdout
    except Exception:
        return False


def _whisperx_word_timestamps(audio_path: Path, script: str, lang: str = "en") -> list[dict]:
    """Get word-level timestamps via forced alignment against the KNOWN
    script text, instead of guessing words from audio like plain Whisper
    transcription does.

    This is the right tool for the job here specifically because we
    already wrote the exact words (it's our own TTS voiceover, not
    unknown speech) — a wav2vec2 forced aligner just needs to find where
    each of those known words falls in the audio, which gives
    meaningfully tighter word-boundary precision than Whisper's own
    word_timestamps (used for both caption highlight timing and the
    subscribe-button CTA popup — see assemble.py's _find_cta_popup_time).
    Falls back to None (caller falls back to _whisper_word_timestamps) on
    any failure — missing package, unsupported language, alignment error.
    """
    try:
        import whisperx
    except ImportError:
        log("whisperx not installed — falling back to plain Whisper timestamps")
        return None

    try:
        log("Running WhisperX forced alignment against the known script...")
        device = "cpu"
        audio = whisperx.load_audio(str(audio_path))
        duration = len(audio) / 16000  # whisperx loads audio resampled to 16kHz
        model_a, metadata = whisperx.load_align_model(language_code=lang[:2], device=device)
        segments = [{"start": 0.0, "end": duration, "text": script}]
        result = whisperx.align(segments, model_a, metadata, audio, device, return_char_alignments=False)

        words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                if "start" not in w or "end" not in w:
                    continue  # words WhisperX couldn't confidently place get no timing
                words.append({"word": w["word"].strip(), "start": w["start"], "end": w["end"]})

        if not words:
            log("WhisperX alignment returned no timed words — falling back to plain Whisper.")
            return None
        log(f"WhisperX aligned {len(words)} words.")
        return words
    except Exception as e:
        log(f"WhisperX alignment failed ({e}) — falling back to plain Whisper timestamps")
        return None


def _whisper_word_timestamps(audio_path: Path, lang: str = "en") -> list[dict]:
    """Get word-level timestamps from Whisper.

    Returns list of {"word": str, "start": float, "end": float}.
    """
    try:
        import whisper
    except ImportError:
        log("Whisper not installed — skipping word timestamps")
        return []

    log("Running Whisper for word-level timestamps...")
    model = whisper.load_model("base")
    result = model.transcribe(
        str(audio_path),
        language=lang[:2],
        word_timestamps=True,
    )

    words = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            words.append({
                "word": w["word"].strip(),
                "start": w["start"],
                "end": w["end"],
            })

    log(f"Got {len(words)} word timestamps.")
    return words


def _group_words(words: list[dict], group_size: int = 4) -> list[list[dict]]:
    groups = []
    for i in range(0, len(words), group_size):
        groups.append(words[i:i + group_size])
    return groups


def _format_ass_time(seconds: float) -> str:
    """Format seconds to ASS timestamp: H:MM:SS.cc (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _generate_ass(
    words: list[dict],
    output_path: Path,
    video_width: int = 1080,
    video_height: int = 1920,
    highlight_color: str = "#FFFF00",
    group_size: int = 4,
    font_family: str = "Arial",
    font_size: int = 72,
):
    """Generate ASS subtitle file with word-by-word color highlighting.

    White text for inactive words, highlight color for current word.
    Semi-transparent background, positioned at lower third (~70% down).

    The font_family is taken from the niche profile (captions.font_family) so
    non-Latin scripts (Korean, Japanese, Chinese, Arabic, etc.) can render
    correctly. The default "Arial" preserves the original behavior for English.
    """
    # ASS header
    margin_v = int(video_height * 0.25)  # ~75% down from top = 25% from bottom
    header = f"""[Script Info]
Title: Pipeline Captions
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_family},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,3,0,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Convert hex color to ASS BGR format (e.g. #00FF88 -> 88FF00).
    # Override tags use &HBBGGRR& without the alpha byte.
    hc = highlight_color.lstrip("#")
    if len(hc) == 6:
        ass_highlight = f"&H{hc[4:6]}{hc[2:4]}{hc[0:2]}&"
    else:
        ass_highlight = "&H00FFFF&"  # fallback yellow

    groups = _group_words(words, group_size=group_size)
    events = []

    for group in groups:
        if not group:
            continue

        group_start = group[0]["start"]
        group_end = group[-1]["end"]

        # For each word in the group being active, emit one dialogue line
        for active_idx, active_word in enumerate(group):
            start = active_word["start"]
            end = active_word["end"]

            # Build text with override tags: highlight color for active, white for rest
            active_fs = font_size + 8  # slight pop on the active word, scaled
            # to the configured font_size — this was hardcoded to a fixed 80
            # before, so shrinking font_size in a niche profile had no visible
            # effect on the (usually most prominent) highlighted word.
            parts = []
            for j, w in enumerate(group):
                word_text = w["word"].upper()
                if j == active_idx:
                    parts.append(f"{{\\c{ass_highlight}\\b1\\fs{active_fs}}}{word_text}{{\\r}}")
                else:
                    parts.append(word_text)

            text = " ".join(parts)
            events.append(
                f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},Default,,0,0,0,,{text}"
            )

    output_path.write_text(header + "\n".join(events), encoding="utf-8")
    log(f"ASS captions saved: {output_path.name}")
    return output_path


def _generate_srt(words: list[dict], output_path: Path, group_size: int = 4) -> Path:
    """Generate standard SRT file from word timestamps."""
    groups = _group_words(words, group_size=group_size)
    lines = []

    for i, group in enumerate(groups, 1):
        if not group:
            continue
        start = group[0]["start"]
        end = group[-1]["end"]
        text = " ".join(w["word"] for w in group)

        start_ts = _srt_time(start)
        end_ts = _srt_time(end)
        lines.append(f"{i}\n{start_ts} --> {end_ts}\n{text}\n")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"SRT captions saved: {output_path.name}")
    return output_path


def _srt_time(seconds: float) -> str:
    """Format seconds to SRT timestamp: HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_captions(
    audio_path: Path,
    work_dir: Path,
    lang: str = "en",
    highlight_color: str = "#FFFF00",
    words_per_group: int = 4,
    font_family: str = "Arial",
    font_size: int = 72,
    script: str | None = None,
) -> dict:
    """Generate captions: ASS (for burn-in) + SRT (for YouTube upload).

    Args:
        font_family: ASS Style font name. Use a CJK-capable font (e.g.
            "Noto Sans CJK KR", "Noto Sans CJK JP") for non-Latin languages,
            otherwise glyphs render as boxes. Pulled from the niche profile's
            captions.font_family field.
        font_size: ASS Style font size. Pulled from the niche profile's
            captions.font_size field.
        script: The exact voiceover text, if available — enables WhisperX
            forced alignment (tighter word-boundary timing than plain
            Whisper transcription) instead of re-guessing the words from
            audio. Falls back to plain Whisper if omitted or if alignment
            fails for any reason.

    Returns dict with keys: srt_path, ass_path, words (for music ducking).
    """
    words = _whisperx_word_timestamps(audio_path, script, lang) if script else None
    if words is None:
        words = _whisper_word_timestamps(audio_path, lang)

    result = {"words": words}

    if not words:
        log("No word timestamps — skipping caption generation")
        # Fallback: run whisper CLI for SRT only
        try:
            from .config import run_cmd
            run_cmd([
                "whisper", str(audio_path),
                "--model", "base",
                "--language", lang[:2],
                "--output_format", "srt",
                "--output_dir", str(work_dir),
            ], capture=True)
            candidates = list(work_dir.glob("*.srt"))
            if candidates:
                srt = candidates[0]
                final = audio_path.with_suffix(".srt")
                srt.rename(final)
                result["srt_path"] = str(final)
        except Exception as e:
            log(f"Whisper CLI fallback failed: {e}")
        return result

    # Generate SRT
    srt_path = work_dir / f"captions_{lang}.srt"
    _generate_srt(words, srt_path, group_size=words_per_group)
    result["srt_path"] = str(srt_path)

    # Generate ASS for burn-in (niche-aware highlight color)
    ass_path = work_dir / f"captions_{lang}.ass"
    _generate_ass(
        words, ass_path,
        highlight_color=highlight_color,
        group_size=words_per_group,
        font_family=font_family,
        font_size=font_size,
    )
    result["ass_path"] = str(ass_path)

    return result

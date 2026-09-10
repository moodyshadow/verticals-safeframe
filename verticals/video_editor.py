"""Natural-language video editing for arbitrary footage (your own filmed
clips, not the niche pipeline's draft/b-roll frames).

Flow: describe an edit in plain English -> the local LLM parses it into one
structured action -> ffmpeg executes it. One action per call, so a multi-step
edit is just multiple calls piped output-to-input (the dashboard chat UI
does exactly that).
"""
import base64
import json
import subprocess
import tempfile
from pathlib import Path

import requests

from .llm import call_llm
from .log import log

# Same local vision model already used for b-roll QA (broll.py) — CPU-only
# marketing-helper Ollama instance, kept off the GPU.
_VISION_HOST = "http://127.0.0.1:11435"
_VISION_MODEL = "llava:7b"

ACTIONS_SCHEMA = """Respond with ONLY a JSON object describing ONE edit action, one of:
{"action": "trim", "start_sec": <number>, "end_sec": <number>}
{"action": "crop_aspect", "ratio": "9:16" | "16:9" | "1:1" | "4:5"}
{"action": "speed", "factor": <number, e.g. 1.5 for 1.5x faster, 0.5 for half speed>}
{"action": "grade", "brightness": <-1..1, default 0>, "contrast": <0.5..2, default 1>, "saturation": <0..3, default 1>}
{"action": "mute"}
{"action": "volume", "factor": <number, e.g. 1.5 for +50% louder, 0.5 for half>}
{"action": "text_overlay", "text": "<caption text>", "position": "top" | "center" | "bottom"}
{"action": "rotate", "degrees": 90 | 180 | 270}
No other keys, no explanation, no markdown fences — just the raw JSON object."""


def _video_info(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    data = json.loads(r.stdout)
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration": float(data.get("format", {}).get("duration", 0)),
    }


def _extract_frame_at(video_path: Path, timestamp: float, out_path: Path) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path),
         "-frames:v", "1", "-update", "1", str(out_path), "-loglevel", "quiet"],
        capture_output=True,
    )
    return result.returncode == 0 and out_path.exists()


def describe_video(video_path: Path, num_frames: int = 5) -> str:
    """Sample frames across the video and ask the local vision model to
    describe each one — grounds vague/relative instructions ("cut the
    boring part", "start where the car appears") in what's actually on
    screen instead of guessing from duration math alone. Best-effort: any
    failure (vision model down, etc.) just skips this frame, never blocks
    the edit — a caption gap here just means slightly less-informed parsing,
    not a broken feature.
    """
    duration = _video_info(video_path)["duration"]
    if num_frames < 2:
        timestamps = [0.0]
    else:
        timestamps = [duration * i / (num_frames - 1) for i in range(num_frames)]

    descriptions = []
    with tempfile.TemporaryDirectory() as tmp:
        for ts in timestamps:
            frame_path = Path(tmp) / f"frame_{ts:.2f}.jpg"
            if not _extract_frame_at(video_path, min(ts, max(duration - 0.1, 0)), frame_path):
                continue
            try:
                b64 = base64.b64encode(frame_path.read_bytes()).decode()
                r = requests.post(
                    f"{_VISION_HOST}/api/generate",
                    json={
                        "model": _VISION_MODEL,
                        "prompt": "Describe what's happening in this video frame in one short sentence.",
                        "images": [b64], "stream": False,
                    },
                    timeout=45,
                )
                if r.status_code == 200:
                    text = r.json().get("response", "").strip()
                    descriptions.append(f"At {ts:.1f}s: {text}")
            except Exception as e:
                log(f"describe_video: skipping frame at {ts:.1f}s ({e})")
                continue
    return "\n".join(descriptions)


def parse_instruction(instruction: str, video_path: Path, use_vision: bool = True) -> dict:
    """Turn a free-text edit request into one structured action dict.
    Video duration is given to the LLM so relative language ("cut the last
    10 seconds", "trim the first half") resolves to real numbers. When
    use_vision is True, a few sampled frames are described by a local
    vision model first, so instructions referencing actual content ("cut
    to where the car appears") can be resolved too.
    """
    info = _video_info(video_path)
    visual_block = ""
    if use_vision:
        described = describe_video(video_path)
        if described:
            visual_block = f"\nWHAT'S ACTUALLY IN THE VIDEO (sampled frames, timestamped):\n{described}\n"
    prompt = (
        f"{ACTIONS_SCHEMA}\n\n"
        f"This video is {info['duration']:.1f} seconds long, {info['width']}x{info['height']}.\n"
        f"{visual_block}\n"
        f"Instruction: {instruction}\n\nJSON:"
    )
    raw = call_llm(prompt, provider="ollama").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError(f"Could not find a JSON action in the model's response: {raw!r}")
    action = json.loads(raw[start:end])
    action["_video_info"] = info
    return action


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-1500:]}")


def apply_action(input_path: Path, action: dict, output_path: Path) -> Path:
    """Execute one parsed action, writing the result to output_path."""
    kind = action.get("action")
    info = action.get("_video_info") or _video_info(input_path)

    if kind == "trim":
        start = float(action.get("start_sec", 0))
        end = action.get("end_sec")
        args = ["-ss", str(start), "-i", str(input_path)]
        if end is not None:
            duration = max(float(end) - start, 0.1)
            args += ["-t", str(duration)]
        args += ["-c:v", "libx264", "-c:a", "aac", str(output_path)]
        _run_ffmpeg(args)

    elif kind == "crop_aspect":
        ratio = str(action.get("ratio", "9:16"))
        w_ratio, h_ratio = (float(x) for x in ratio.split(":"))
        target_ar = w_ratio / h_ratio
        src_ar = info["width"] / info["height"] if info["width"] and info["height"] else target_ar
        if src_ar > target_ar:
            vf = f"crop=ih*{w_ratio}/{h_ratio}:ih"
        else:
            vf = f"crop=iw:iw*{h_ratio}/{w_ratio}"
        _run_ffmpeg(["-i", str(input_path), "-vf", vf, "-c:a", "copy", str(output_path)])

    elif kind == "speed":
        factor = float(action.get("factor", 1.0))
        video_filter = f"setpts=PTS/{factor}"
        # atempo only accepts 0.5-2.0 per instance; chain two for a bigger factor
        if 0.5 <= factor <= 2.0:
            audio_filter = f"atempo={factor}"
        else:
            half = factor ** 0.5
            audio_filter = f"atempo={half},atempo={half}"
        _run_ffmpeg([
            "-i", str(input_path),
            "-filter_complex", f"[0:v]{video_filter}[v];[0:a]{audio_filter}[a]",
            "-map", "[v]", "-map", "[a]", str(output_path),
        ])

    elif kind == "grade":
        brightness = float(action.get("brightness", 0))
        contrast = float(action.get("contrast", 1))
        saturation = float(action.get("saturation", 1))
        vf = f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
        _run_ffmpeg(["-i", str(input_path), "-vf", vf, "-c:a", "copy", str(output_path)])

    elif kind == "mute":
        _run_ffmpeg(["-i", str(input_path), "-c:v", "copy", "-an", str(output_path)])

    elif kind == "volume":
        factor = float(action.get("factor", 1.0))
        _run_ffmpeg(["-i", str(input_path), "-vf", "null", "-af", f"volume={factor}", "-c:v", "copy", str(output_path)])

    elif kind == "text_overlay":
        text = str(action.get("text", "")).replace("'", "’").replace(":", "\\:")
        position = action.get("position", "bottom")
        y = {"top": "60", "center": "(h-text_h)/2", "bottom": "h-th-80"}.get(position, "h-th-80")
        vf = (
            f"drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':text='{text}':"
            f"fontcolor=white:fontsize=48:x=(w-text_w)/2:y={y}:"
            f"box=1:boxcolor=black@0.5:boxborderw=12"
        )
        _run_ffmpeg(["-i", str(input_path), "-vf", vf, "-c:a", "copy", str(output_path)])

    elif kind == "rotate":
        degrees = int(action.get("degrees", 90))
        transpose = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}.get(degrees, "transpose=1")
        _run_ffmpeg(["-i", str(input_path), "-vf", transpose, "-c:a", "copy", str(output_path)])

    else:
        raise ValueError(f"Unknown action: {kind!r}")

    log(f"Applied {kind} -> {output_path}")
    return output_path


def split_into_clips(input_path: Path, clip_seconds: float, out_dir: Path) -> list[Path]:
    """Cut one long video into sequential clips of `clip_seconds` each — for
    prepping footage into stock-site-sized pieces (most stock sites want
    short clips, not one long file). The last clip gets whatever's left
    over, even if shorter than clip_seconds; a leftover under 1s is dropped
    since stock sites won't accept a near-zero-length clip anyway.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = _video_info(input_path)["duration"]
    stem = input_path.stem
    clips = []
    start = 0.0
    index = 1
    while start < duration:
        remaining = duration - start
        if remaining < 1.0:
            break
        length = min(clip_seconds, remaining)
        out_path = out_dir / f"{stem}_clip{index:02d}.mp4"
        _run_ffmpeg([
            "-ss", str(start), "-i", str(input_path), "-t", str(length),
            "-c:v", "libx264", "-c:a", "aac", str(out_path),
        ])
        clips.append(out_path)
        start += clip_seconds
        index += 1
    return clips


def edit_video(input_path: Path, instruction: str, output_path: Path, use_vision: bool = True) -> dict:
    """Parse + apply in one call. Returns the parsed action (minus the
    internal _video_info) so the caller can show what was actually done."""
    action = parse_instruction(instruction, input_path, use_vision=use_vision)
    apply_action(input_path, action, output_path)
    action.pop("_video_info", None)
    return action

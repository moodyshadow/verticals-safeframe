import json
from pathlib import Path
from PIL import Image
from verticals.config import VIDEO_WIDTH, VIDEO_HEIGHT
from verticals.assemble import assemble_video
from verticals.media_library import add_to_library
from verticals.tts import generate_voiceover
from verticals.captions import generate_captions
from verticals.music import select_and_prepare_music

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788467225_en")
draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788467225.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))

def resize_crop(src, dst):
    img = Image.open(src).convert("RGB")
    ow, oh = img.size
    scale = max(VIDEO_WIDTH / ow, VIDEO_HEIGHT / oh)
    nw, nh = int(ow * scale), int(oh * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - VIDEO_WIDTH) // 2
    top = (nh - VIDEO_HEIGHT) // 2
    img = img.crop((left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT))
    img.save(dst)

# Frame 0: FF7 Rebirth logo opener
logo_src = Path(r"C:\Users\szabo\AppData\Local\Temp\claude\C--Users-szabo-stable-diffusion-webui\807866e8-4ac8-43a4-921c-fa7e7ce9d047\scratchpad\ff7_logo_only2.png")
frame0_out = WORK_DIR / "broll_0_ff7logo.png"
resize_crop(logo_src, frame0_out)
data = frame0_out.read_bytes()
lib_path = add_to_library(
    data, ["final fantasy 7", "final fantasy vii", "ff7", "ff7 rebirth", "square enix", "game logo"],
    "photo", source="user_provided", source_url="",
)
print("Registered opener in library:", lib_path)

# Frame 7: headset-on-desk AnimateDiff clip (already generated + approved earlier)
candidate7 = Path(r"C:\Users\szabo\AppData\Local\Temp\claude\C--Users-szabo-stable-diffusion-webui\807866e8-4ac8-43a4-921c-fa7e7ce9d047\scratchpad\ff7_candidate7.mp4")
frame7_out = WORK_DIR / "broll_7_headset.mp4"
frame7_out.write_bytes(candidate7.read_bytes())

frames = draft["_pipeline_state"]["broll"]["artifacts"]["frames"]
frames[0] = str(frame0_out)
frames[7] = str(frame7_out)
draft["_pipeline_state"]["broll"]["artifacts"]["frames"] = frames

# Regenerate voiceover/captions/music for the updated script (comment-topic-prompt line added)
vo_path = generate_voiceover(draft["script"], WORK_DIR, lang="en", provider="edge")
print("Voiceover regenerated:", vo_path)
captions = generate_captions(vo_path, WORK_DIR, lang="en")
print("Captions regenerated")
music = select_and_prepare_music(vo_path, WORK_DIR, niche="gaming")
print("Music selected:", music.get("track_path"))

draft["_pipeline_state"]["voiceover"]["artifacts"]["path"] = str(vo_path)
draft["_pipeline_state"]["captions"]["artifacts"] = captions
draft["_pipeline_state"]["music"]["artifacts"] = music

video_path = assemble_video(
    frames=[Path(f) for f in frames], voiceover=Path(vo_path), out_dir=WORK_DIR, job_id="1788467225", lang="en",
    ass_path=captions["ass_path"], music_path=music["track_path"],
    duck_filter=music["duck_filter"], srt_path=captions["srt_path"],
)
print("Rebuilt video:", video_path)

draft["_pipeline_state"]["assemble"]["artifacts"]["video_path"] = str(video_path)
draft["video_en"] = str(video_path)
draft.pop("reviewed_by_claude", None)
draft.pop("reviewed_at", None)
draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
print("Draft updated.")

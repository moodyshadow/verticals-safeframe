import json
from pathlib import Path
from PIL import Image
from verticals.config import VIDEO_WIDTH, VIDEO_HEIGHT
from verticals.assemble import assemble_video
from verticals.media_library import add_to_library

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788467225_en")
draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788467225.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))

SRC = Path(r"C:\Users\szabo\AppData\Local\Temp\claude\C--Users-szabo-stable-diffusion-webui\807866e8-4ac8-43a4-921c-fa7e7ce9d047\scratchpad\ff7_logo_only2.png")

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

out_path = WORK_DIR / "broll_0_ff7logo.png"
resize_crop(SRC, out_path)

frames = draft["_pipeline_state"]["broll"]["artifacts"]["frames"]
frames[0] = str(out_path)

# also apply the approved frame 7 replacement (headset-on-desk AnimateDiff
# clip, generated and confirmed earlier this session)
candidate7 = Path(r"C:\Users\szabo\AppData\Local\Temp\claude\C--Users-szabo-stable-diffusion-webui\807866e8-4ac8-43a4-921c-fa7e7ce9d047\scratchpad\ff7_candidate7.mp4")
frame7_final = WORK_DIR / "broll_7_headset.mp4"
frame7_final.write_bytes(candidate7.read_bytes())
frames[7] = str(frame7_final)

draft["_pipeline_state"]["broll"]["artifacts"]["frames"] = frames

# register as a reusable topical asset for future FF7 coverage
data = out_path.read_bytes()
lib_path = add_to_library(
    data,
    ["final fantasy 7", "final fantasy vii", "ff7", "ff7 rebirth", "square enix", "game logo"],
    "photo", source="user_provided", source_url="",
)
print("Registered in library:", lib_path)

ps = draft["_pipeline_state"]
vo_path = Path(ps["voiceover"]["artifacts"]["path"])
captions = ps["captions"]["artifacts"]
music = ps["music"]["artifacts"]

video_path = assemble_video(
    frames=[Path(f) for f in frames], voiceover=vo_path, out_dir=WORK_DIR, job_id="1788467225", lang="en",
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

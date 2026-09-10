import json
from pathlib import Path
from PIL import Image
from verticals.config import VIDEO_WIDTH, VIDEO_HEIGHT
from verticals.assemble import assemble_video

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788467146_en")
draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788467146.json")
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

src = WORK_DIR / "broll_4_realphoto_src.jpg"
out = WORK_DIR / "broll_4_realphoto.png"
resize_crop(src, out)

frames = draft["_pipeline_state"]["broll"]["artifacts"]["frames"]
frames[4] = str(out)

ps = draft["_pipeline_state"]
vo_path = Path(ps["voiceover"]["artifacts"]["path"])
captions = ps["captions"]["artifacts"]
music = ps["music"]["artifacts"]

video_path = assemble_video(
    frames=[Path(f) for f in frames], voiceover=vo_path, out_dir=WORK_DIR, job_id="1788467146", lang="en",
    ass_path=captions["ass_path"], music_path=music["track_path"],
    duck_filter=music["duck_filter"], srt_path=captions["srt_path"],
)
print("Rebuilt video:", video_path)

draft["_pipeline_state"]["broll"]["artifacts"]["frames"] = frames
draft["_pipeline_state"]["assemble"]["artifacts"]["video_path"] = str(video_path)
draft["video_en"] = str(video_path)
draft.pop("reviewed_by_claude", None)
draft.pop("reviewed_at", None)
draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
print("Draft updated.")

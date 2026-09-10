import os
os.environ["SD_WEBUI_URL"] = "http://127.0.0.1:7860"
import json, random
from pathlib import Path
from PIL import Image
from verticals.tts import generate_voiceover
from verticals.captions import generate_captions
from verticals.music import select_and_prepare_music
from verticals.broll import _generate_image_img2img_local_sd, _vision_qa_frame
from verticals.config import VIDEO_WIDTH, VIDEO_HEIGHT
from verticals.assemble import assemble_video
from verticals.media_library import add_to_library

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

# 1. Regenerate voiceover/captions/music for the new CTA line
tts_script = draft["script"].replace("Elon Musk", "EE-lawn Musk")
vo_path = generate_voiceover(tts_script, WORK_DIR, lang="en", provider="edge")
print("Voiceover regenerated:", vo_path)
captions = generate_captions(vo_path, WORK_DIR, lang="en")
print("Captions regenerated")
music = select_and_prepare_music(vo_path, WORK_DIR, niche="tech")
print("Music selected:", music["track_path"])

draft["_pipeline_state"]["voiceover"]["artifacts"]["path"] = str(vo_path)
draft["_pipeline_state"]["captions"]["artifacts"] = captions
draft["_pipeline_state"]["music"]["artifacts"] = music

# 2. Swap server-room slot (frame index 2) for the fleet photo, close-match img2img
ref_bytes = Path(r"C:\Users\szabo\Downloads\large_cybercab_jpg_2ee0cf8e6a.jpg").read_bytes()
out_path = WORK_DIR / "broll_2_fleet.png"
prompt = ("A wide aerial shot of a fleet of gold robotaxi vehicles parked in neat rows, "
          "representing a large-scale autonomous vehicle network, one continuous scene, "
          "no collage, no split screen, photorealistic, cinematic lighting, dark moody "
          "atmosphere, 8K detail, shallow depth of field")
qa_passed = False
for attempt in range(3):
    _generate_image_img2img_local_sd(prompt, ref_bytes, out_path, denoising_strength=0.25, seed=random.randint(0, 2**31 - 1))
    qa_passed, reason = _vision_qa_frame(out_path)
    print(f"  fleet attempt {attempt+1}: passed={qa_passed} reason={reason}")
    if qa_passed:
        break
resize_crop(out_path, out_path)

frames = draft["_pipeline_state"]["broll"]["artifacts"]["frames"]
frames[2] = str(out_path)
draft["_pipeline_state"]["broll"]["artifacts"]["frames"] = frames

# also add the unused fleet + robotaxi photos to the permanent library as spares
for name, kw in [
    ("large_cybercab_jpg_2ee0cf8e6a.jpg", ["tesla", "cybercab", "robotaxi", "fleet", "parking lot", "aerial"]),
    ("Tesla-Model-Y-Robotaxi-Cybercab-2025.jpg", ["tesla", "cybercab", "robotaxi", "model y", "street"]),
]:
    data = Path(r"C:\Users\szabo\Downloads", name).read_bytes()
    p = add_to_library(data, kw, "photo", source="user_provided", source_url="")
    print("Library spare added:", p)

# 3. Reassemble
video_path = assemble_video(
    frames=[Path(f) for f in frames], voiceover=Path(vo_path), out_dir=WORK_DIR, job_id="1788467146", lang="en",
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

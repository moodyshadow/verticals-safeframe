import os
os.environ["SD_WEBUI_URL"] = "http://127.0.0.1:7860"
import json, random
from pathlib import Path
from PIL import Image
from verticals.broll import _generate_image_img2img_local_sd, _vision_qa_frame
from verticals.config import VIDEO_WIDTH, VIDEO_HEIGHT
from verticals.assemble import assemble_video

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788467146_en")
draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788467146.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))

DENOISE = 0.25  # closest-possible match to the user's own reference photo

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

def img2img_frame(ref_path, prompt, out_path):
    ref_bytes = Path(ref_path).read_bytes()
    qa_passed = False
    for attempt in range(3):
        _generate_image_img2img_local_sd(prompt, ref_bytes, out_path, denoising_strength=DENOISE, seed=random.randint(0, 2**31 - 1))
        qa_passed, reason = _vision_qa_frame(out_path)
        print(f"  attempt {attempt+1}: passed={qa_passed} reason={reason}")
        if qa_passed:
            break
    resize_crop(out_path, out_path)
    return out_path

frames = draft["_pipeline_state"]["broll"]["artifacts"]["frames"]

print("Frame 1 (camera system) - closest match...")
p = img2img_frame(
    r"C:\Users\szabo\Downloads\tesla-cybercab-enthullt-preis-fsd-hardware-release-date-und-mehr-384874.jpg",
    "A close-up of a futuristic vehicle's camera system mounted above the windshield, neon blue accents on a dark screen, one continuous scene, no collage, no split screen, cinematic, high contrast, 4K quality",
    WORK_DIR / "broll_0_v3.png",
)
frames[0] = str(p)

print("Frame 4 (minimalist product shot) - closest match...")
p = img2img_frame(
    r"C:\Users\szabo\Downloads\Tesla_Cybercab_-_Berlin_2024.jpg",
    "A minimalist product shot of a futuristic gold robotaxi vehicle with gull-wing doors open in a showroom, dark background, subtle glow effect, one continuous scene, no collage, cinematic, high contrast, 4K quality",
    WORK_DIR / "broll_3_v3.png",
)
frames[3] = str(p)

print("Frame 8 (wide city street) - closest match...")
p = img2img_frame(
    r"C:\Users\szabo\Downloads\2024-10-cybercab-1-copy.jpg",
    "A wide shot of a city street at night with futuristic robotaxi vehicles driving by, a crowd of people watching in the background, one continuous scene, no collage, no split screen, cinematic, high contrast, 4K quality",
    WORK_DIR / "broll_7_v3.png",
)
frames[7] = str(p)

# Clean up old (too-different) versions
for old_name in ["broll_0_v2.png", "broll_3_v2.png", "broll_7_v2.png"]:
    old_f = WORK_DIR / old_name
    if old_f.exists() and str(old_f) not in frames:
        old_f.unlink()

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

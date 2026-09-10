"""One-off: regenerate the flagged frame 7 (stock market chart, blurry +
reused) in the tech video (job 1788344949) and rebuild the final video.
"""
import json
import shutil
from pathlib import Path

from verticals.broll import generate_broll
from verticals.assemble import assemble_video
from verticals.media_library import asset_path_for

DRAFT_PATH = Path(r"C:\Users\szabo\.verticals\drafts\1788344949.json")
WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788344949_en")
MEDIA_DIR = Path(r"C:\Users\szabo\.verticals\media")
JOB_ID = "1788344949"

draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
prompts = draft["broll_prompts"]
bad_index = 7

existing_frames = sorted(WORK_DIR.glob("broll_*.mp4")) + sorted(WORK_DIR.glob("broll_*.png"))
exclude_paths = set()
for f in existing_frames:
    idx = int(f.stem.split("_")[1])
    if idx == bad_index:
        continue
    media_type = "video" if f.suffix == ".mp4" else "photo"
    exclude_paths.add(str(asset_path_for(f.read_bytes(), media_type)))

print(f"Excluding {len(exclude_paths)} already-used assets from the other 7 frames.")
print(f"Regenerating frame {bad_index}: {prompts[bad_index]}")

tmp_dir = WORK_DIR / "redo_tmp"
tmp_dir.mkdir(exist_ok=True)
new_frames, fallback_count = generate_broll(
    [prompts[bad_index]], tmp_dir, use_stock=True, exclude_paths=exclude_paths,
)
new_frame = new_frames[0]
print(f"New frame generated: {new_frame} (fallback_count={fallback_count})")

# Replace the old broll_7.* with the new asset, removing any stale extension.
for old in WORK_DIR.glob(f"broll_{bad_index}.*"):
    old.unlink()
final_broll_path = WORK_DIR / f"broll_{bad_index}{new_frame.suffix}"
shutil.copy(new_frame, final_broll_path)
shutil.rmtree(tmp_dir, ignore_errors=True)
print(f"Placed new frame at: {final_broll_path}")

# Rebuild the ordered frame list matching the original assembly.
frames = []
for i in range(len(prompts)):
    matches = sorted(WORK_DIR.glob(f"broll_{i}.*"))
    frames.append(matches[0])

ps = draft["_pipeline_state"]
voiceover = Path(ps["voiceover"]["artifacts"]["path"])
ass_path = ps["captions"]["artifacts"]["ass_path"]
srt_path = ps["captions"]["artifacts"]["srt_path"]
music = ps["music"]["artifacts"]

video_path = assemble_video(
    frames=frames,
    voiceover=voiceover,
    out_dir=WORK_DIR,
    job_id=JOB_ID,
    lang="en",
    ass_path=ass_path,
    music_path=music["track_path"],
    duck_filter=music["duck_filter"],
    srt_path=srt_path,
)
print(f"\nRebuilt video: {video_path}")

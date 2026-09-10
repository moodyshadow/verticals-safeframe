"""One-off: regenerate all b-roll frames for the tech video (job
1788385583) using the newly-fixed 7-day reuse cooldown, since the frames
currently in place were flagged as recycled from videos made 1-2 days
earlier. Voiceover/captions/music stay untouched — only visuals + final
reassembly change.
"""
import os
os.environ["SD_WEBUI_URL"] = "http://127.0.0.1:7861"  # Forge

import json
from pathlib import Path

from verticals.broll import generate_broll
from verticals.assemble import assemble_video
from verticals.niche import load_niche, get_visual_source_priority
from verticals.config import BROLL_COUNT

DRAFT_PATH = Path(r"C:\Users\szabo\.verticals\drafts\1788385583.json")
WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788385583_en")
JOB_ID = "1788385583"
LANG = "en"

draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
profile = load_niche(draft.get("niche", "tech"))
use_stock = get_visual_source_priority(profile) != "ai_only"

# Clear the old frame files so generate_broll can't accidentally reuse a
# stale local file by name collision — it should hit the (now
# cooldown-aware) library/live search fresh for every prompt.
for old in WORK_DIR.glob("broll_*.*"):
    old.unlink()

prompts = draft["broll_prompts"]
frames, fallback_count, used_asset_paths = generate_broll(prompts, WORK_DIR, use_stock=use_stock)
print(f"Regenerated {len(frames)} frames, fallback_count={fallback_count}")
for f in frames:
    print(" ", f)

ps = draft["_pipeline_state"]
vo_path = Path(ps["voiceover"]["artifacts"]["path"])
captions = ps["captions"]["artifacts"]
music = ps["music"]["artifacts"]

video_path = assemble_video(
    frames=frames,
    voiceover=vo_path,
    out_dir=WORK_DIR,
    job_id=JOB_ID,
    lang=LANG,
    ass_path=captions["ass_path"],
    music_path=music["track_path"],
    duck_filter=music["duck_filter"],
    srt_path=captions["srt_path"],
)
print(f"\nRebuilt video: {video_path}")

draft["_pipeline_state"]["broll"]["artifacts"] = {
    "frames": [str(f) for f in frames],
    "fallback_count": fallback_count,
    "used_asset_paths": used_asset_paths,
}
draft["_pipeline_state"]["assemble"]["artifacts"]["video_path"] = str(video_path)
draft[f"video_{LANG}"] = str(video_path)
draft.pop("reviewed_by_claude", None)
draft.pop("reviewed_at", None)
DRAFT_PATH.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
print("Draft JSON updated.")

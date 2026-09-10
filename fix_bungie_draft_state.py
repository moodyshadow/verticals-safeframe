import json
from pathlib import Path
from verticals.captions import generate_captions
from verticals.music import select_and_prepare_music
from verticals.assemble import assemble_video

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788636195_en")
draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788636195.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))

vo_path = WORK_DIR / "voiceover_en.mp3"
assert vo_path.exists(), "voiceover missing!"

captions = generate_captions(vo_path, WORK_DIR, lang="en")
print("Captions regenerated")
music = select_and_prepare_music(vo_path, WORK_DIR, niche="gaming")
print("Music selected:", music.get("track_path"))

frames = [str(WORK_DIR / f"broll_{i}_d2.png") for i in range(8)]
for f in frames:
    assert Path(f).exists(), f"missing frame {f}"

video_path = assemble_video(
    frames=[Path(f) for f in frames], voiceover=vo_path, out_dir=WORK_DIR, job_id="1788636195", lang="en",
    ass_path=captions["ass_path"], music_path=music["track_path"],
    duck_filter=music["duck_filter"], srt_path=captions["srt_path"],
)
print("Rebuilt video:", video_path)

# Reconstruct full pipeline state
draft.setdefault("_pipeline_state", {})
draft["_pipeline_state"]["broll"] = {"status": "done", "artifacts": {"frames": frames, "fallback_count": 0, "used_asset_paths": []}}
draft["_pipeline_state"]["voiceover"] = {"status": "done", "artifacts": {"path": str(vo_path)}}
draft["_pipeline_state"]["captions"] = {"status": "done", "artifacts": captions}
draft["_pipeline_state"]["music"] = {"status": "done", "artifacts": music}
draft["_pipeline_state"]["assemble"] = {"status": "done", "artifacts": {"video_path": str(video_path)}}
draft["video_en"] = str(video_path)
draft.pop("reviewed_by_claude", None)
draft.pop("reviewed_at", None)
draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
print("Draft state fully reconstructed and saved.")

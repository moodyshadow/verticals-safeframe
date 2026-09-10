import json
from pathlib import Path
from verticals.tts import generate_voiceover
from verticals.captions import generate_captions
from verticals.music import select_and_prepare_music
from verticals.assemble import assemble_video
from verticals.media_library import add_to_library

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788636253_en")
draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788636253.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))

frames = [
    str(WORK_DIR / "stocktest_0.mp4"),
    str(WORK_DIR / "broll_1_realisticvision.png"),
    str(WORK_DIR / "stocktest_2.mp4"),
    str(WORK_DIR / "stocktest_3.mp4"),  # already replaced with the "search" clip
    str(WORK_DIR / "stocktest_4.mp4"),
    str(WORK_DIR / "stocktest_5.mp4"),
    str(WORK_DIR / "stocktest_6.mp4"),
    str(WORK_DIR / "stocktest_7.mp4"),
]
for f in frames:
    assert Path(f).exists(), f"missing {f}"

vo_path = generate_voiceover(draft["script"], WORK_DIR, lang="en", provider="edge")
print("Voiceover:", vo_path)
captions = generate_captions(vo_path, WORK_DIR, lang="en")
print("Captions regenerated")
music = select_and_prepare_music(vo_path, WORK_DIR, niche="tech")
print("Music:", music.get("track_path"))

video_path = assemble_video(
    frames=[Path(f) for f in frames], voiceover=Path(vo_path), out_dir=WORK_DIR, job_id="1788636253", lang="en",
    ass_path=captions["ass_path"], music_path=music["track_path"],
    duck_filter=music["duck_filter"], srt_path=captions["srt_path"],
)
print("Rebuilt video:", video_path)

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
print("Draft saved.")

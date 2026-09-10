from pathlib import Path
import json
from verticals.tts import generate_voiceover
from verticals.captions import generate_captions
from verticals.music import select_and_prepare_music
from verticals.assemble import assemble_video

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788467146_en")
draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788467146.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))

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

frames = draft["_pipeline_state"]["broll"]["artifacts"]["frames"]
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

"""Re-produce a video with a different Edge TTS voice, reusing existing b-roll."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verticals.config import MEDIA_DIR  # noqa: E402
from verticals.tts import generate_voiceover  # noqa: E402
from verticals.captions import generate_captions  # noqa: E402
from verticals.music import select_and_prepare_music  # noqa: E402
from verticals.assemble import assemble_video  # noqa: E402
from verticals.niche import load_niche, get_caption_config, get_music_config  # noqa: E402
from verticals.state import PipelineState  # noqa: E402

DRAFT_PATH = Path(sys.argv[1])
NEW_VOICE_ID = sys.argv[2]

with open(DRAFT_PATH, encoding="utf-8", errors="replace") as f:
    draft = json.load(f)

job_id = draft["job_id"]
lang = "en"
niche_name = draft.get("niche", "general")
profile = load_niche(niche_name)
work_dir = MEDIA_DIR / f"work_{job_id}_{lang}"
state = PipelineState(draft)

frames = [Path(p) for p in state.get_artifact("broll", "frames", [])]
script = draft.get("script")

print(f"Reusing {len(frames)} existing b-roll frames")
print(f"Regenerating voiceover with {NEW_VOICE_ID}...")
vo_path = generate_voiceover(
    script, work_dir, lang,
    provider="edge",
    voice_config={"voice_id": NEW_VOICE_ID},
)

print("Regenerating captions...")
caption_config = get_caption_config(profile)
captions_result = generate_captions(
    vo_path, work_dir, lang,
    highlight_color=caption_config.get("highlight_color", "#FFFF00"),
    words_per_group=caption_config.get("words_per_group", 4),
    font_family=caption_config.get("font_family", "Arial"),
    font_size=int(caption_config.get("font_size", 72)),
)

print("Reusing existing music selection...")
music_config = get_music_config(profile)
music_result = select_and_prepare_music(
    vo_path, work_dir,
    duck_speech=music_config.get("duck_volume_speech", 0.12),
    duck_gap=music_config.get("duck_volume_gap", 0.25),
)

print("Reassembling video...")
video_path = assemble_video(
    frames=frames,
    voiceover=vo_path,
    out_dir=work_dir,
    job_id=job_id,
    lang=lang,
    ass_path=captions_result.get("ass_path"),
    music_path=music_result.get("track_path"),
    duck_filter=music_result.get("duck_filter"),
)

srt_path = captions_result.get("srt_path")
if srt_path and Path(srt_path).exists():
    final_srt = MEDIA_DIR / f"verticals_{job_id}_{lang}.srt"
    shutil.copy(srt_path, final_srt)
    draft[f"srt_{lang}"] = str(final_srt)

draft[f"video_{lang}"] = str(video_path)
state.complete_stage("voiceover", {"path": str(vo_path)})
state.complete_stage("captions", {
    "srt_path": str(captions_result.get("srt_path", "")),
    "ass_path": str(captions_result.get("ass_path", "")),
})
state.complete_stage("music", {
    "track_path": str(music_result.get("track_path", "")),
    "duck_filter": music_result.get("duck_filter", ""),
})
state.complete_stage("assemble", {"video_path": str(video_path)})
# Force a fresh upload for this re-voiced version rather than skipping
# (cmd_upload would otherwise see the old "done" upload stage and reuse
# the previous URL instead of uploading the re-voiced file).
state.state.pop("upload", None)
state.state.pop("thumbnail", None)
state.save(DRAFT_PATH)

print(f"\nDone. Video: {video_path}")

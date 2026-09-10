"""One-off: fix the gaming video's script (awkward "GTA 6's" opening +
fabricated newsletter CTA -> the approved "stay_overclocked" CTA), then
regenerate voiceover, captions, and reassemble the final video. B-roll and
music are untouched — the visuals/timing don't need to change, just audio.
"""
import json
from pathlib import Path

from verticals.niche import load_niche, get_voice_config, get_caption_config
from verticals.tts import generate_voiceover
from verticals.captions import generate_captions
from verticals.assemble import assemble_video

DRAFT_PATH = Path(r"C:\Users\szabo\.verticals\drafts\1788343666.json")
WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788343666_en")
MEDIA_DIR = Path(r"C:\Users\szabo\.verticals\media")
JOB_ID = "1788343666"
LANG = "en"

NEW_SCRIPT = (
    "The dating mechanics in GTA 6 have sparked a debate on TikTok. Some are "
    "claiming it's cheating in real life. But here's the thing: most people "
    "don't believe it's cheating. So, where's the line? Is it polyamory if "
    "your partner has an AI chatbot spouse? Is it okay to text your Persona 5 "
    "waifu before your real partner? The GTA 6 community is at war right now, "
    "and we're diving in to explore the controversy. Subscribe to stay "
    "overclocked on the latest gaming news."
)

draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
draft["script"] = NEW_SCRIPT
profile = load_niche(draft.get("niche", "gaming"))

# Voiceover
voice_config = get_voice_config(profile, provider="edge", lang=LANG)
vo_path = generate_voiceover(NEW_SCRIPT, WORK_DIR, LANG, provider="edge", voice_config=voice_config)
print(f"Voiceover regenerated: {vo_path}")

# Captions
caption_config = get_caption_config(profile)
captions_result = generate_captions(
    vo_path, WORK_DIR, LANG,
    highlight_color=caption_config.get("highlight_color", "#FFFF00"),
    words_per_group=caption_config.get("words_per_group", 4),
    font_family=caption_config.get("font_family", "Arial"),
    font_size=int(caption_config.get("font_size", 72)),
)
print(f"Captions regenerated: {captions_result}")

# Frames (unchanged) — reuse exactly what's already on disk.
prompts = draft["broll_prompts"]
frames = []
for i in range(len(prompts)):
    matches = sorted(WORK_DIR.glob(f"broll_{i}.*"))
    frames.append(matches[0])

ps = draft["_pipeline_state"]
music = ps["music"]["artifacts"]

video_path = assemble_video(
    frames=frames,
    voiceover=vo_path,
    out_dir=WORK_DIR,
    job_id=JOB_ID,
    lang=LANG,
    ass_path=captions_result.get("ass_path"),
    music_path=music["track_path"],
    duck_filter=music["duck_filter"],
    srt_path=captions_result.get("srt_path"),
)
print(f"\nRebuilt video: {video_path}")

# Persist the updated draft (script + refreshed artifact paths).
draft["_pipeline_state"]["voiceover"]["artifacts"]["path"] = str(vo_path)
draft["_pipeline_state"]["captions"]["artifacts"] = {
    "srt_path": str(captions_result.get("srt_path", "")),
    "ass_path": str(captions_result.get("ass_path", "")),
}
draft["_pipeline_state"]["assemble"]["artifacts"]["video_path"] = str(video_path)
draft[f"video_{LANG}"] = str(video_path)
DRAFT_PATH.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
print("Draft JSON updated.")

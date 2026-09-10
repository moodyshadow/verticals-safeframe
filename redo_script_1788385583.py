"""One-off: replace the tech video's script with a stronger, properly-
grounded take on Mayor Mamdani's actual quoted reasoning (verified via
live web search — the original script's "vocal minority of parents" and
"1 in 5 schools" claims don't appear in any real coverage and were likely
fabricated by the original thin-research draft), then regenerate
voiceover, captions, and reassemble. B-roll and music are untouched.
"""
import json
from pathlib import Path

from verticals.niche import load_niche, get_voice_config, get_caption_config
from verticals.tts import generate_voiceover
from verticals.captions import generate_captions
from verticals.assemble import assemble_video

DRAFT_PATH = Path(r"C:\Users\szabo\.verticals\drafts\1788385583.json")
WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788385583_en")
JOB_ID = "1788385583"
LANG = "en"

NEW_SCRIPT = (
    "Mayor Mamdani just banned AI for nearly 600,000 NYC students — through "
    "8th grade, for a full year. And his reasoning? Here's his actual quote: "
    "\"The tech industry wants us to believe that AI-powered early education "
    "is not only inevitable, but necessary. We do not see it that way.\" "
    "That's not a nuanced policy stance — that's a mayor dismissing an entire "
    "technology wholesale instead of teaching kids how to use it responsibly. "
    "Kids under 2nd grade can't even touch a laptop in class now. Meanwhile, "
    "high schoolers still get AI pilot programs, and teachers are exempt "
    "entirely — so the ban isn't really about AI being harmful, it's about "
    "who gets access to it. This is the nation's largest school district "
    "setting the tone for how a whole generation learns to think about AI, "
    "and the tone it's setting is \"avoid it,\" not \"understand it.\" "
    "Subscribe to stay overclocked on the latest tech and gaming news."
)

draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
draft["script"] = NEW_SCRIPT
profile = load_niche(draft.get("niche", "tech"))

voice_config = get_voice_config(profile, provider="edge", lang=LANG)
vo_path = generate_voiceover(NEW_SCRIPT, WORK_DIR, LANG, provider="edge", voice_config=voice_config)
print(f"Voiceover regenerated: {vo_path}")

caption_config = get_caption_config(profile)
captions_result = generate_captions(
    vo_path, WORK_DIR, LANG,
    highlight_color=caption_config.get("highlight_color", "#FFFF00"),
    words_per_group=caption_config.get("words_per_group", 4),
    font_family=caption_config.get("font_family", "Arial"),
    font_size=int(caption_config.get("font_size", 72)),
)
print(f"Captions regenerated")

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

draft["_pipeline_state"]["voiceover"]["artifacts"]["path"] = str(vo_path)
draft["_pipeline_state"]["captions"]["artifacts"] = {
    "srt_path": str(captions_result.get("srt_path", "")),
    "ass_path": str(captions_result.get("ass_path", "")),
}
draft["_pipeline_state"]["assemble"]["artifacts"]["video_path"] = str(video_path)
draft[f"video_{LANG}"] = str(video_path)
# Script content changed materially — clear any prior review flag so the
# new cut goes through the review gate again before it can be uploaded.
draft.pop("reviewed_by_claude", None)
draft.pop("reviewed_at", None)
DRAFT_PATH.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
print("Draft JSON updated.")

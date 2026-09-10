"""Niche profile loader — reads YAML profiles and provides stage-specific context.

Each niche profile configures: script tone/hooks/CTAs, visual style/subjects,
voice pace/energy, caption styling, music mood, thumbnail strategy, and
topic discovery sources.
"""

import yaml
from pathlib import Path
from typing import Any

from .log import log

# Niche profiles live in niches/ at the project root
NICHES_DIR = Path(__file__).resolve().parent.parent / "niches"

# Cache loaded profiles to avoid re-reading YAML on every stage
_cache: dict[str, dict] = {}


def load_niche(name: str = "general") -> dict:
    """Load a niche profile by name. Returns general fallback if not found."""
    name = (name or "general").strip().lower()

    if name in _cache:
        return _cache[name]

    profile_path = NICHES_DIR / f"{name}.yaml"
    if not profile_path.exists():
        log(f"Niche profile '{name}' not found at {profile_path}")
        if name != "general":
            log("Falling back to 'general' profile")
            return load_niche("general")
        # Return minimal default if even general.yaml is missing
        return _minimal_profile(name)

    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
        profile.setdefault("name", name)
        _cache[name] = profile
        log(f"Loaded niche profile: {name}")
        return profile
    except Exception as e:
        log(f"Failed to parse niche profile '{name}': {e}")
        return _minimal_profile(name)


def _minimal_profile(name: str) -> dict:
    """Bare minimum profile when YAML is missing or broken."""
    return {
        "name": name,
        "display_name": name.title(),
        "script": {
            "tone": "clear, engaging, conversational",
            "pacing": "moderate, well structured",
            "word_count": "150 to 180",
        },
        "visuals": {
            "style": "cinematic, professional",
            "prompt_suffix": "photorealistic, cinematic lighting, high quality",
        },
        "voice": {},
        "captions": {},
        "music": {},
        "thumbnail": {},
        "discovery": {},
    }


def get_script_context(profile: dict) -> str:
    """Build the script intelligence block for the LLM prompt.

    Returns a multi-line string that goes into the Claude/Gemini/GPT prompt
    to shape the script tone, hooks, structure, and CTAs.
    """
    script = profile.get("script", {})
    if not script:
        return ""

    niche_display = profile.get("display_name", profile.get("name", "General"))
    parts = []
    parts.append(f"NICHE: {niche_display}")

    if script.get("tone"):
        parts.append(f"TONE: {script['tone']}")
    if script.get("pacing"):
        parts.append(f"PACING: {script['pacing']}")
    if script.get("perspective"):
        parts.append(f"PERSPECTIVE: {script['perspective']}")
    if script.get("word_count"):
        parts.append(f"TARGET WORD COUNT: {script['word_count']}")
    if script.get("sentence_style"):
        parts.append(f"SENTENCE STYLE: {script['sentence_style']}")

    # Mood variants — pick one, let it shape word choice/energy through the
    # whole script (not just the hook or CTA line).
    moods = script.get("mood_variants", [])
    if moods:
        mood_lines = []
        for m in moods:
            desc = m.get("description", "")
            when = m.get("when", "")
            if desc:
                line = f"  {m.get('id', 'mood')}: {desc}"
                if when:
                    line += f" (use when: {when})"
                mood_lines.append(line)
        if mood_lines:
            parts.append(
                "MOOD OPTIONS (pick the one that actually fits today's story "
                "and let it color the whole script's word choice and energy, "
                "not just the opening or closing line):"
            )
            parts.extend(mood_lines)

    # Hook patterns
    hooks = script.get("hooks", [])
    if hooks:
        hook_lines = []
        for h in hooks:
            template = h.get("template", "")
            when = h.get("when", "")
            if template:
                line = f"  {h.get('id', 'hook')}: \"{template}\""
                if when:
                    line += f" (use when: {when})"
                hook_lines.append(line)
        if hook_lines:
            parts.append(
                "HOOK PATTERNS (style/structure inspiration, not a literal "
                "pick-list — write an original opening line in the spirit "
                "of whichever pattern best fits today's story, rather than "
                "reusing one of these verbatim. The goal is a hook that "
                "feels fresh every video, not a repeated template):"
            )
            parts.extend(hook_lines)

    # Structure guidance
    structure = script.get("structure", {})
    if structure:
        parts.append("SCRIPT STRUCTURE:")
        if structure.get("opening"):
            parts.append(f"  Opening: {structure['opening']}")
        if structure.get("middle"):
            parts.append(f"  Middle: {structure['middle']}")
        if structure.get("closing"):
            parts.append(f"  Closing: {structure['closing']}")

    # CTA variants — templates with a {beat}-style placeholder, same pattern
    # as hooks. YouTube's own analytics flagged generic CTAs as a growth
    # blocker, but the fix is NOT to name the day's specific one-off subject
    # (a real miss this project made: "Follow for more Pokémon updates!" on
    # a channel that covers a completely different game/topic every day —
    # nobody who subscribed for Pokémon updates has any reason to stick
    # around for tomorrow's Nvidia story). The placeholder must be filled
    # with the channel's ongoing coverage CATEGORY (e.g. "gaming news",
    # "entertainment news") — something true of every future video, not
    # just this one. "Subscribe to stay overclocked on the latest gaming
    # news" converts because it's concrete AND still true next week;
    # "...on the latest Pokémon news" doesn't.
    ctas = script.get("cta_variants", [])
    if ctas:
        cta_lines = []
        for c in ctas:
            if isinstance(c, dict):
                template = c.get("template", "")
                when = c.get("when", "")
                if template:
                    line = f"  {c.get('id', 'cta')}: \"{template}\""
                    if when:
                        line += f" (use when: {when})"
                    cta_lines.append(line)
            else:
                cta_lines.append(f"  \"{c}\"")
        if cta_lines:
            parts.append(
                "CTA OPTIONS (pick the most appropriate; fill any {beat} "
                "placeholder with this channel's ongoing coverage category, "
                "per that option's own guidance below — NEVER with today's "
                "specific one-off subject/character/game name, since "
                "tomorrow's video won't be about that. The exception: a "
                "variant whose own guidance explicitly asks for today's real "
                "specifics (e.g. {subject}, {side_a}/{side_b}) — those exist "
                "specifically to reference this video's actual story, but "
                "ONLY use that variant at all when its own 'when' guidance "
                "genuinely applies). Use the chosen template's fixed wording "
                "VERBATIM (only the {placeholder} itself gets filled in) — "
                "do not paraphrase or rewrite it, since the closing line is "
                "checked against these exact templates and a paraphrase "
                "will be rejected:"
            )
            parts.extend(cta_lines)

    # Forbidden phrases
    forbidden = script.get("forbidden_phrases", [])
    if forbidden:
        parts.append(f"NEVER USE: {', '.join(forbidden)}")

    return "\n".join(parts)


def get_visual_context(profile: dict) -> dict:
    """Extract visual intelligence for b-roll prompt shaping.

    Returns dict with style, mood, subjects, avoid, prompt_suffix.
    """
    return profile.get("visuals", {})


def get_visual_source_priority(profile: dict) -> str:
    """How this niche should source b-roll: "pexels_first" (default) tries a
    real, freely-licensed stock photo before falling back to AI generation;
    "ai_only" skips stock search entirely.

    Niches about specific copyrighted media (a particular game, movie, show,
    celebrity) should be "ai_only" — no free stock site can legally host
    actual screenshots/stills of someone else's IP, so searching for one is
    a wasted API call that only ever returns generic, off-subject filler.
    """
    visuals = profile.get("visuals", {})
    return visuals.get("stock_source", "pexels_first")


def get_visual_prompt_suffix(profile: dict) -> str:
    """Get the image prompt suffix from the niche profile."""
    visuals = profile.get("visuals", {})
    return visuals.get("prompt_suffix", "photorealistic, cinematic lighting, high quality")


def get_visual_subjects(profile: dict) -> dict:
    """Get preferred and avoided visual subjects."""
    visuals = profile.get("visuals", {})
    subjects = visuals.get("subjects", {})
    return {
        "prefer": subjects.get("prefer", []),
        "avoid": subjects.get("avoid", []),
    }


def get_voice_config(profile: dict, provider: str = "edge_tts", lang: str = "en") -> dict:
    """Get voice configuration for the specified provider and language."""
    voice = profile.get("voice", {})
    suggested = voice.get("suggested_voices", {})

    config = {
        "pace": voice.get("pace", ""),
        "energy": voice.get("energy", ""),
        "style": voice.get("style", ""),
    }

    provider_voices = suggested.get(provider, {})
    if isinstance(provider_voices, dict):
        config["voice_id"] = provider_voices.get(lang, provider_voices.get("en", ""))
        # Providers that ship a single voice_id + a settings dict (rather than
        # one voice_id per language) — ElevenLabs and 60db follow this shape.
        if provider in ("elevenlabs", "60db"):
            config["voice_id"] = provider_voices.get("voice_id", "")
            config["settings"] = provider_voices.get("settings", {})
    elif isinstance(provider_voices, str):
        config["voice_id"] = provider_voices

    return config


def get_caption_config(profile: dict) -> dict:
    """Get caption styling from the niche profile."""
    defaults = {
        "highlight_color": "#FFFF00",
        "text_color": "#FFFFFF",
        "font_family": "Arial",
        "font_size": 72,
        "font_weight": "bold",
        "position": "lower_third",
        "background": "semi_transparent_dark",
        "words_per_group": 4,
    }
    captions = profile.get("captions", {})
    defaults.update(captions)
    return defaults


def get_music_config(profile: dict) -> dict:
    """Get music mood and ducking config from the niche profile."""
    defaults = {
        "mood": "ambient, subtle, no lyrics",
        "energy": "medium",
        "tags": [],
        "duck_volume_speech": 0.12,
        "duck_volume_gap": 0.25,
    }
    music = profile.get("music", {})
    defaults.update(music)
    return defaults


def get_thumbnail_config(profile: dict) -> dict:
    """Get thumbnail style guidance from the niche profile."""
    return profile.get("thumbnail", {})


def get_discovery_config(profile: dict) -> dict:
    """Get topic discovery sources from the niche profile."""
    return profile.get("discovery", {})


def list_niches() -> list[str]:
    """List all available niche profile names."""
    if not NICHES_DIR.exists():
        return ["general"]
    names = [p.stem for p in NICHES_DIR.glob("*.yaml")]
    if "general" not in names:
        names.append("general")
    return sorted(names)

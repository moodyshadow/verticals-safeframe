"""Background music — track selection + volume ducking."""

import hashlib
import random
from pathlib import Path

import requests

from .log import log

# Music directory ships with the package
MUSIC_DIR = Path(__file__).resolve().parent.parent / "music"

# Downloaded Jamendo tracks are cached here so a repeat niche/mood search
# doesn't re-download the same track — same reasoning as
# media_library.py's photo/video cache, just for audio.
JAMENDO_CACHE_DIR = MUSIC_DIR / "jamendo_cache"
JAMENDO_API_URL = "https://api.jamendo.com/v3.0/tracks/"

# Jamendo's own genre/tag vocabulary that fits each niche — distinct from
# _NICHE_MOODS above (that one matches the *local* library's hand-assigned
# tags; this one has to match whatever tags Jamendo's own catalog actually
# uses, which don't line up 1:1 with our local naming).
_NICHE_JAMENDO_TAGS: dict[str, str] = {
    "gaming": "electronic+energetic",
    "tech": "electronic+corporate",
}

# Mood tags per local track, hand-assigned from each track's own title/feel
# (see music/CREDITS.txt for the actual titles). Selection used to be pure
# random.choice() with zero regard for the video's niche — a somber ambient
# track ("Aphelion") was exactly as likely to land under a fast, punchy
# gaming script as an actually energetic one. Matching against a niche's own
# `script.tone`/`script.pacing` (see niches/*.yaml) picks from the subset
# that actually fits instead.
_TRACK_MOODS: dict[str, set[str]] = {
    "01-Journey.mp3": {"adventurous", "uplifting"},
    "02-Motivate.mp3": {"energetic", "motivational"},
    "03-Future-Technology.mp3": {"tech", "futuristic", "energetic"},
    "04-Upbeat-Inspiring-Corporate.mp3": {"corporate", "upbeat", "energetic"},
    "05-Chillout.mp3": {"calm", "chill"},
    "06-Inspire-2.mp3": {"inspiring", "corporate"},
    "07-Inspire.mp3": {"inspiring", "corporate"},
    "08-Main-Street.mp3": {"upbeat", "energetic"},
    "09-Inspiring.mp3": {"inspiring", "calm"},
    "10-Future.mp3": {"tech", "futuristic"},
    "11-Code-Switch.mp3": {"tech", "energetic"},
    "12-Powerful-Emotional-Trailer.mp3": {"dramatic", "powerful", "energetic"},
    "13-The-Inspiration.mp3": {"inspiring", "calm"},
    "14-Aphelion.mp3": {"calm", "ambient", "cinematic"},
    "15-Sweet-Dreams.mp3": {"calm", "ambient"},
    "16-Beauty.mp3": {"calm", "elegant"},
    "17-Hyperfun.mp3": {"energetic", "upbeat", "adventurous"},
    "18-Local-Forecast-Elevator.mp3": {"corporate", "calm", "inspiring"},
    # 2026-09-09: dropped "energetic" — that tag alone was enough to make
    # this track match gaming's mood pool too, despite it being a
    # tech/futuristic track that read as tonally off for gaming content.
    "19-Cephalopod.mp3": {"tech", "futuristic"},
    "20-Digital-Lemonade.mp3": {"tech", "futuristic", "energetic"},
    "21-Call-to-Adventure.mp3": {"adventurous", "dramatic", "powerful"},
    "22-Life-of-Riley.mp3": {"upbeat", "adventurous", "energetic"},
}

# Which moods fit each niche — matches the tone/pacing each niche.yaml
# already describes (gaming: "energetic... fast and punchy"; tech:
# "informed... fast and dense"), so the two niches don't draw from the same
# pool of somber/ambient tracks that fit neither.
_NICHE_MOODS: dict[str, set[str]] = {
    "gaming": {"energetic", "motivational", "dramatic", "powerful", "upbeat", "adventurous"},
    "tech": {"tech", "futuristic", "energetic", "corporate", "inspiring"},
}


def _find_tracks(niche: str | None = None) -> list[Path]:
    """Find all MP3 tracks in the music/ directory, filtered to those whose
    mood tags fit `niche` when one is given and any actually match — falls
    back to the full library rather than returning nothing for an
    unrecognized/untagged niche."""
    if not MUSIC_DIR.exists():
        return []
    all_tracks = sorted(MUSIC_DIR.glob("*.mp3"))
    if not niche:
        return all_tracks
    wanted_moods = _NICHE_MOODS.get(niche.lower())
    if not wanted_moods:
        return all_tracks
    matched = [t for t in all_tracks if _TRACK_MOODS.get(t.name, set()) & wanted_moods]
    return matched or all_tracks


def _fetch_jamendo_track(niche: str | None) -> tuple[Path, str] | None:
    """Search Jamendo (real Creative Commons-licensed catalog, actively
    searchable by tag — unlike chosic.com/YouTube Audio Library, which are
    curation sites with no public API) for a track matching the niche's
    mood, download it, and cache it locally.

    Returns None (caller falls back to the local library) when no API key
    is configured, the request fails, or nothing matches — this must never
    raise and block video production over a missing/optional feature.
    Otherwise returns (track_path, credit_line) — the credit line is
    required by Jamendo's CC license and must reach the video description.
    """
    from .config import get_jamendo_key

    client_id = get_jamendo_key()
    if not client_id:
        return None

    tags = _NICHE_JAMENDO_TAGS.get((niche or "").lower(), "")
    try:
        r = requests.get(
            JAMENDO_API_URL,
            params={
                "client_id": client_id,
                "format": "json",
                "limit": 10,
                "tags": tags,
                "audioformat": "mp32",
                "include": "musicinfo",
                "boost": "popularity_week",
            },
            timeout=15,
        )
        if r.status_code != 200:
            log(f"Jamendo search {r.status_code}: {r.text[:150]}")
            return None
        results = r.json().get("results") or []
        if not results:
            return None

        choice = random.choice(results)
        audio_url = choice.get("audio")
        if not audio_url:
            return None

        digest = hashlib.sha256(audio_url.encode()).hexdigest()[:24]
        JAMENDO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached_path = JAMENDO_CACHE_DIR / f"{digest}.mp3"

        track_name = choice.get("name", "Unknown")
        artist_name = choice.get("artist_name", "Unknown")
        license_url = choice.get("license_ccurl", "https://jamendo.com")
        credit = f'"{track_name}" by {artist_name} (Jamendo, {license_url})'

        if cached_path.exists():
            return cached_path, credit

        audio_r = requests.get(audio_url, timeout=30)
        if audio_r.status_code != 200:
            return None
        cached_path.write_bytes(audio_r.content)
        log(f"Jamendo track fetched: \"{track_name}\" by {artist_name} (CC-licensed, see {license_url})")
        return cached_path, credit
    except Exception as e:
        log(f"Jamendo fetch failed (non-fatal, falling back to local library): {e}")
        return None


def _get_speech_regions(audio_path: Path) -> list[tuple[float, float]]:
    """Extract speech regions from Whisper word timestamps (reuses captions data).

    Falls back to treating the entire audio as one speech region.
    """
    try:
        from .captions import _whisper_word_timestamps
        words = _whisper_word_timestamps(audio_path)
        if words:
            # Merge close words into speech regions (gap < 0.5s = same region)
            regions = []
            region_start = words[0]["start"]
            region_end = words[0]["end"]

            for w in words[1:]:
                if w["start"] - region_end < 0.5:
                    region_end = w["end"]
                else:
                    regions.append((region_start, region_end))
                    region_start = w["start"]
                    region_end = w["end"]
            regions.append((region_start, region_end))
            return regions
    except Exception:
        pass

    # Fallback: get total duration and treat as one speech region
    try:
        from .assemble import get_audio_duration
        dur = get_audio_duration(audio_path)
        return [(0.0, dur)]
    except Exception:
        return [(0.0, 60.0)]


_local_credits_cache: dict[str, str] | None = None


def _local_track_credit(filename: str) -> str:
    """Look up a local track's required attribution line from
    music/CREDITS.txt (parsed once, cached) — every local track is CC
    BY/BY-SA licensed and requires exactly this kind of credit in the video
    description, the same requirement Jamendo tracks have."""
    global _local_credits_cache
    if _local_credits_cache is None:
        _local_credits_cache = {}
        credits_path = MUSIC_DIR / "CREDITS.txt"
        if credits_path.exists():
            blocks = credits_path.read_text(encoding="utf-8").split("\n\n")
            for block in blocks:
                lines = [l for l in block.strip().splitlines() if l.strip()]
                if len(lines) >= 2 and lines[0].endswith(".mp3"):
                    _local_credits_cache[lines[0]] = " ".join(lines[1:])
    return _local_credits_cache.get(filename, "")


def build_duck_filter(speech_regions: list[tuple[float, float]], buffer: float = 0.3, vol_speech: float = 0.12, vol_gap: float = 0.25) -> str:
    """Build ffmpeg volume filter expression for ducking during speech.

    During speech: volume = vol_speech (default 0.12)
    During gaps: volume = vol_gap (default 0.25)
    Transitions smoothed by ±buffer seconds.
    """
    if not speech_regions:
        return f"volume={vol_gap}"

    # Build between() conditions for speech regions
    conditions = []
    for start, end in speech_regions:
        s = max(0, start - buffer)
        e = end + buffer
        conditions.append(f"between(t,{s:.2f},{e:.2f})")

    condition_expr = "+".join(conditions)
    return f"volume='if({condition_expr}, {vol_speech}, {vol_gap})':eval=frame"


def select_and_prepare_music(
    voiceover_path: Path,
    work_dir: Path,
    niche: str | None = None,
    duck_speech: float = 0.12,
    duck_gap: float = 0.25,
) -> dict:
    """Select a track (Jamendo live search first, mood-matched local library
    as fallback), build duck filter from speech regions.

    Returns dict with track_path and duck_filter for use by assemble.py.
    """
    jamendo_result = _fetch_jamendo_track(niche)
    if jamendo_result:
        track, credit = jamendo_result
    else:
        tracks = _find_tracks(niche)
        if not tracks:
            log("No music tracks found in music/ — skipping background music")
            return {}
        track = random.choice(tracks)
        credit = _local_track_credit(track.name)
    log(f"Selected music track: {track.name}")

    # Get speech regions for ducking
    speech_regions = _get_speech_regions(voiceover_path)
    duck_filter = build_duck_filter(speech_regions, vol_speech=duck_speech, vol_gap=duck_gap)
    log(f"Built duck filter with {len(speech_regions)} speech regions")

    return {
        "track_path": str(track),
        "duck_filter": duck_filter,
        "music_credit": credit,
    }

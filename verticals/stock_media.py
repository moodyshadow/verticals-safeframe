"""Real, freely-licensed stock photo search (Pexels) for topical b-roll.

Pexels photos are free to use for commercial purposes with no attribution
required (https://www.pexels.com/license/), which is why this is the
preferred source over AI-generated images when a good match exists: it's
actual real-world footage relevant to the topic instead of an abstract
AI-generated scene, without the copyright risk of scraping arbitrary
"public" images that aren't actually licensed for reuse.
"""

import re

import requests

from .config import extract_keywords, get_pexels_key
from .log import log
from .retry import with_retry

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"

# Descriptive words that show up in Pexels alt-text/slugs across totally
# unrelated content ("a photo/video/shot/scene of ...") — matching on these
# alone is how a completely unrelated clip (e.g. a random drummer) can look
# like a keyword "hit" for a prompt about a specific news topic. Also
# includes the aesthetic/quality words niche profiles append to every b-roll
# prompt (see niches/*.yaml prompt_suffix) — "cinematic", "lighting", etc.
# describe *style*, not subject, and are common enough in Pexels' own
# metadata that they cause the same false-relevance problem.
_GENERIC_DESCRIPTOR_WORDS = {
    "photo", "photos", "picture", "pictures", "image", "images", "video",
    "videos", "shot", "shots", "scene", "scenes", "stock", "footage", "clip",
    "photorealistic", "cinematic", "lighting", "quality", "dramatic",
    "dynamic", "motivational", "energy", "warm", "dark", "moody",
    "atmosphere", "atmospheric", "detail", "depth", "field", "shallow",
    "vibrant", "colors", "aesthetic", "contrast", "professional", "awe",
    "inspiring", "scale", "documentary", "feel", "noir", "desaturated",
    "fog", "golden", "hour", "style", "national", "geographic", "nasa",
    "natural", "appetizing", "rustic", "gaming", "high", "looking",
}


def _query_from_prompt(prompt: str) -> str:
    """Extract a clean search query from a b-roll prompt.

    Niche visual-style suffixes get appended after a period (see draft.py's
    `f"{p}. {suffix}"`), so the first sentence is the actual subject — that's
    what we want to search for, not the style/mood suffix.
    """
    return prompt.split(".")[0].strip()


def _relevance_keywords(query: str) -> list[str]:
    """Distinctive keywords a genuinely matching result should mention.

    Reuses the same stopword/length filtering as script b-roll-prompt
    keyword extraction, so a description like "A shot of Bill Maher on his
    show..." reduces to distinctive terms like "maher"/"awkward" rather than
    generic connector words that any unrelated clip could also contain.
    """
    words = [w for w in extract_keywords(query).split() if w not in _GENERIC_DESCRIPTOR_WORDS]
    return words


def _is_relevant(descriptive_text: str, keywords: list[str]) -> bool:
    """Whether a Pexels result's own alt-text/slug actually mentions the
    subject we searched for, not just some incidental shared word.

    Without this check, a keyword search on a prompt naming a specific real
    person/event can return completely unrelated footage that happens to
    share one generic word (e.g. "show") — worse than no match at all, since
    it looks like a confident real-footage hit while showing the wrong thing
    entirely.

    Requires at least 2 distinctive keywords to even attempt a match — a
    single surviving keyword (e.g. just "embarrassed" once names/places are
    stripped from a person-centric prompt) is too weak a signal on its own:
    plenty of unrelated clips share one generic-adjacent word. With too few
    keywords to reliably confirm relevance, skip stock and let AI generation
    depict the specific subject instead of guessing with a shaky match.
    """
    if len(keywords) < 2:
        return False
    text = descriptive_text.lower()
    return any(kw in text for kw in keywords)


@with_retry(max_retries=2, base_delay=1.5)
def search_pexels_photo(query: str) -> bytes | None:
    """Search Pexels for a photo matching `query`. Returns image bytes, or
    None if no API key is configured or no result was found."""
    api_key = get_pexels_key()
    if not api_key:
        return None

    r = requests.get(
        PEXELS_SEARCH_URL,
        params={"query": query, "per_page": 5, "orientation": "portrait"},
        headers={"Authorization": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        log(f"Pexels search {r.status_code} for '{query}': {r.text[:150]}")
        return None

    photos = r.json().get("photos") or []
    if not photos:
        return None

    keywords = _relevance_keywords(query)
    for photo in photos:
        if not _is_relevant(photo.get("alt", ""), keywords):
            continue
        # Prefer the largest portrait-friendly size available.
        src = photo.get("src", {})
        img_url = src.get("large2x") or src.get("large") or src.get("original")
        if not img_url:
            continue
        img_r = requests.get(img_url, timeout=30)
        if img_r.status_code == 200:
            return img_r.content

    log(f"Pexels photo search for '{query}': {len(photos)} result(s), none actually relevant")
    return None


@with_retry(max_retries=2, base_delay=1.5)
def search_pexels_video(query: str) -> bytes | None:
    """Search Pexels for a video clip matching `query`. Returns raw mp4
    bytes, or None if no API key is configured or no result was found."""
    api_key = get_pexels_key()
    if not api_key:
        return None

    r = requests.get(
        PEXELS_VIDEO_SEARCH_URL,
        params={"query": query, "per_page": 5, "orientation": "portrait"},
        headers={"Authorization": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        log(f"Pexels video search {r.status_code} for '{query}': {r.text[:150]}")
        return None

    videos = r.json().get("videos") or []
    if not videos:
        return None

    keywords = _relevance_keywords(query)
    for video in videos:
        # Pexels videos have no alt-text field, but the page URL slug is
        # descriptive, e.g. ".../video/a-man-playing-drums-1234567/" — the
        # only signal available to check the result actually matches.
        slug = re.sub(r"[-/]", " ", video.get("url", ""))
        if not _is_relevant(slug, keywords):
            continue
        # Prefer the smallest file that's still at least 720p tall, portrait
        # orientation, to keep download/transcode time reasonable.
        files = [f for f in (video.get("video_files") or []) if f.get("height", 0) >= 720]
        if not files:
            files = video.get("video_files") or []
        if not files:
            continue
        files.sort(key=lambda f: f.get("height", 0))
        video_url = files[0].get("link")
        if not video_url:
            continue
        video_r = requests.get(video_url, timeout=60)
        if video_r.status_code == 200:
            return video_r.content

    log(f"Pexels video search for '{query}': {len(videos)} result(s), none actually relevant")
    return None


def fetch_topical_broll_video(prompt: str) -> bytes | None:
    """Try to find a real, licensed stock video clip matching a b-roll prompt.

    Returns None (caller should fall back to a still photo or AI generation)
    if Pexels isn't configured or no relevant clip was found.
    """
    query = _query_from_prompt(prompt)
    if not query:
        return None
    try:
        return search_pexels_video(query)
    except Exception as e:
        log(f"Pexels video lookup failed for '{query}': {e} — falling back to photo/AI generation")
        return None


def fetch_topical_broll(prompt: str) -> bytes | None:
    """Try to find real, licensed stock footage matching a b-roll prompt.

    Returns None (caller should fall back to AI generation) if Pexels isn't
    configured or no relevant photo was found.
    """
    query = _query_from_prompt(prompt)
    if not query:
        return None
    try:
        return search_pexels_photo(query)
    except Exception as e:
        log(f"Pexels lookup failed for '{query}': {e} — falling back to AI generation")
        return None

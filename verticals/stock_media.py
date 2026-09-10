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

from .config import STOPWORDS, get_pexels_key, get_pixabay_key, run_cmd
from .log import log
from .media_library import add_to_library, asset_path_for, find_in_library, is_on_cooldown, word_boundary_match
from .retry import with_retry

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Specific source URLs confirmed, by direct inspection, to carry inaccurate
# uploader-supplied tags that defeat keyword/anchor relevance matching no
# matter how it's tuned — the tags themselves lie, not our scoring of them.
# Found via Pixabay video id 26619: a generic PlayStation-controller stock
# clip tagged with "resident evil" (alongside "super mario", "the wii", and
# a dozen other unrelated franchise names — evident SEO keyword-stuffing by
# the uploader), which kept passing a "resident evil" b-roll prompt's
# anchor check because that literal phrase really is in its tags field.
# Hard-blocking by URL is the only reliable fix for a specific known-bad
# asset like this; it doesn't address the general case of other stuffed
# tags elsewhere in either catalog.
_BLOCKED_SOURCE_URLS = {
    "https://cdn.pixabay.com/video/2019/09/06/26619-359604050_medium.mp4",
}


def _mirror_asset(data: bytes, media_type: str) -> bytes:
    """Horizontally flip an asset — used when find_in_library() had to
    reuse something still inside the 7-day cooldown (every real match was
    on cooldown) so the exact same footage still doesn't appear
    pixel-identical to a recent video. Falls back to the original bytes if
    the flip itself fails, rather than losing the frame entirely.
    """
    import tempfile
    from pathlib import Path

    try:
        if media_type == "photo":
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data)).transpose(Image.FLIP_LEFT_RIGHT)
            out = io.BytesIO()
            img.save(out, format=img.format or "JPEG")
            return out.getvalue()
        else:
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "in.mp4"
                dst = Path(tmp) / "out.mp4"
                src.write_bytes(data)
                run_cmd([
                    "ffmpeg", "-y", "-i", str(src), "-vf", "hflip",
                    "-c:v", "libx264", "-preset", "veryfast", "-an",
                    str(dst), "-loglevel", "error",
                ])
                return dst.read_bytes() if dst.exists() else data
    except Exception as e:
        log(f"Mirror-flip variation failed ({e}) — using the asset as-is")
        return data
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH_URL = "https://pixabay.com/api/"
PIXABAY_VIDEO_SEARCH_URL = "https://pixabay.com/api/videos/"

# Descriptive words that show up in Pexels alt-text/slugs across totally
# unrelated content ("a photo/video/shot/scene of ...") — matching on these
# alone is how a completely unrelated clip (e.g. a random drummer) can look
# like a keyword "hit" for a prompt about a specific news topic. Also
# includes the aesthetic/quality words niche profiles append to every b-roll
# prompt (see niches/*.yaml prompt_suffix) — "cinematic", "lighting", etc.
# describe *style*, not subject, and are common enough in Pexels' own
# metadata that they cause the same false-relevance problem. Also includes
# generic-retail-environment words ("store", "aisle", "shelf", "counter",
# "retail") — a prompt naming "a Walmart store's retail aisle with neon
# shelf signage" matched a totally unrelated small shop's food-jar shelf on
# "store"/"aisle"/"shelf" alone; those words describe *any* retail setting,
# not the specific subject, the same non-discriminating role as "photo".
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
    "store", "stores", "aisle", "aisles", "shelf", "shelves", "counter",
    "counters", "retail", "signage",
    # Generic human-activity words: "a crowd of people in line, waiting" is
    # near-identical wording to how countless unrelated real photos/videos
    # get captioned (a train station, a concert, any queue anywhere) — no
    # keyword-overlap count can tell those apart from a prompt about a
    # specific queue for a specific product. A busy station escalator video
    # passed relevance on "crowd"+"people"+"line" alone with zero connection
    # to the actual subject. Removing them means this class of beat falls
    # back to AI generation (which already avoids crowd-shaped motion
    # artifacts — see broll.py's AnimateDiff risk check) instead of
    # confidently presenting unrelated real footage as a match.
    "crowd", "crowds", "people", "person", "persons", "line", "lines",
    "waiting", "wait", "purchase", "purchasing", "holding", "hold",
    # Generic body-part/framing/emotion words: a fried-egg breakfast video
    # passed relevance for "a close-up of a booster pack with a surprised
    # gamer's hands reaching for it" purely on "hands"/"reaching"/
    # "close-up"/"background" — someone using a fork and knife is also,
    # technically, "hands reaching" toward something in close-up. These
    # describe the shot's framing/body-parts/mood, not its actual subject.
    "background", "backgrounds", "hand", "hands", "reaching", "reach",
    "close-up", "closeup", "surprised", "angle", "view",
    # "wide" (as in "wide shot") is the same category as "close-up"/"angle"
    # above — camera-framing terminology, not a subject word. Found after
    # it genuinely (not as a substring-matching bug) matched the unrelated
    # tag "world wide" on a Brazil/football stock video, which combined
    # with one incidental "tournament" tag hit to clear the match threshold
    # for a "wide shot of a gaming tournament arena" prompt.
    "wide",
    # Generic emotion/reaction words: "a gamer's face looking shocked and
    # disappointed" matched a generic 3D cartoon reaction figurine (an
    # unrelated stock "shocked face" render) that then got reused across
    # multiple frames throughout the video — any face/reaction stock clip
    # tagged with these words looks like a match regardless of who or what
    # it's actually depicting.
    "shocked", "disappointed", "frustrated", "angry", "emotion",
    "emotional", "reaction", "reactions", "expression", "expressions",
}


def _query_from_prompt(prompt: str) -> str:
    """Extract a clean search query from a b-roll prompt.

    Niche visual-style suffixes get appended after a period (see draft.py's
    `f"{p}. {suffix}"`), so the first sentence is the actual subject — that's
    what we want to search for, not the style/mood suffix.
    """
    return prompt.split(".")[0].strip()


def _pixabay_query(query: str) -> str:
    """Pixabay's API hard-rejects any `q` over 100 characters (400 error),
    unlike Pexels which has no such limit — our b-roll queries are full
    descriptive sentences that regularly exceed it. Truncate at a word
    boundary so every Pixabay call actually runs instead of erroring out."""
    if len(query) <= 100:
        return query
    return query[:100].rsplit(" ", 1)[0]


def _relevance_keywords(query: str) -> list[str]:
    """Distinctive keywords a genuinely matching result should mention.

    This used to reuse `config.extract_keywords()`, which is tuned for
    short news headlines: it ranks candidates by raw word length and keeps
    only the top 4. That's a bad fit for a full descriptive b-roll sentence
    ("A cluttered retail checkout counter with a hand holding a lottery
    ticket next to a stack of trading cards, conveying a sense of chaos and
    confusion") — the length ranking picked "cluttered"/"conveying"/
    "confusion"/"checkout" (long mood adjectives) and dropped the actual
    subject nouns ("lottery", "ticket", "cards", "stack") entirely, so
    real matching stock footage could never be found for that subject.
    Preserving reading order and filtering by stopword/generic-descriptor
    lists only — no length ranking, no hard word cap — keeps every concrete
    noun a search actually needs; a cap at 8 still dropped "cards" from a
    17-keyword prompt about trading cards. The relevance bar itself stays
    meaningful despite the longer list: `_is_relevant`/`_match_score` still
    require multiple independent substring hits against the result's own
    real description text, not just list membership.
    """
    raw_words = [w.strip(".,!?\"'()[]").lower() for w in query.split()]
    filtered = [
        w for w in raw_words
        if w and len(w) > 2 and w not in STOPWORDS and w not in _GENERIC_DESCRIPTOR_WORDS
    ]
    # Deduplicate (preserving first-seen order): a prompt repeating a word
    # ("Pokemon TCG cards and a Pokemon plush toy") otherwise let that one
    # repeated word count twice toward the match-count threshold — that's
    # exactly how a cache entry stored under the single keyword "pokemon"
    # passed a nominal "2 independent matches required" check purely from
    # counting the same word against itself twice.
    return list(dict.fromkeys(filtered))


def _is_relevant(
    descriptive_text: str, keywords: list[str], min_keywords: int = 2, min_matches: int = 1,
) -> bool:
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

    `min_matches` guards a separate failure mode found in practice: a rich,
    multi-object prompt (e.g. "a top hat, a wooden cane, and green plant
    leaves...") extracts many keywords, so a single incidental hit (e.g. just
    "leaves") was enough to "confirm" a completely unrelated photo (a sunhat
    stuffed with a paper bag on banana leaves) — nothing else about it
    matched. Requiring 2 independent hits when 2+ keywords exist (clamped to
    however many keywords are actually available, so a genuinely sparse
    1-keyword prompt isn't penalized) makes that kind of coincidental overlap
    much less likely to pass as a real match.
    """
    if len(keywords) < min_keywords:
        return False
    text = descriptive_text.lower()
    matches = sum(1 for kw in keywords if word_boundary_match(kw, text))
    required = min(min_matches, len(keywords))
    return matches >= required


@with_retry(max_retries=2, base_delay=1.5)
def search_pexels_photo(query: str, min_keywords: int = 2, min_matches: int = 1) -> bytes | None:
    """Search Pexels for a photo matching `query`. Returns image bytes, or
    None if no API key is configured or no result was found."""
    keywords = _relevance_keywords(query)
    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "photo", min_matches=min_matches, allow_cooldown_fallback=False)
        if cached:
            data, _ = cached
            return data

    api_key = get_pexels_key()
    if api_key:
        r = requests.get(
            PEXELS_SEARCH_URL,
            params={"query": query, "per_page": 5, "orientation": "portrait"},
            headers={"Authorization": api_key},
            timeout=30,
        )
        if r.status_code != 200:
            log(f"Pexels search {r.status_code} for '{query}': {r.text[:150]}")
        else:
            photos = r.json().get("photos") or []
            for photo in photos:
                if not _is_relevant(photo.get("alt", ""), keywords, min_keywords=min_keywords, min_matches=min_matches):
                    continue
                # Prefer the largest portrait-friendly size available.
                src = photo.get("src", {})
                img_url = src.get("large2x") or src.get("large") or src.get("original")
                if not img_url:
                    continue
                img_r = requests.get(img_url, timeout=30)
                if img_r.status_code == 200:
                    if is_on_cooldown(img_r.content, "photo"):
                        continue
                    if keywords:
                        add_to_library(img_r.content, keywords, "photo", source="pexels", source_url=img_url)
                    return img_r.content
            if photos:
                log(f"Pexels photo search for '{query}': {len(photos)} result(s), none actually relevant")

    # Live search found nothing new — a stale (cooldown-violating) cache
    # hit, mirror-flipped, beats no b-roll at all.
    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "photo", min_matches=min_matches)
        if cached:
            data, needs_variation = cached
            return _mirror_asset(data, "photo") if needs_variation else data
    return None


@with_retry(max_retries=2, base_delay=1.5)
def search_pixabay_photo(query: str, min_keywords: int = 2, min_matches: int = 1) -> bytes | None:
    """Search Pixabay for a photo matching `query`. Second-tier fallback
    behind Pexels — same CC0-equivalent free-for-commercial-use license
    (https://pixabay.com/service/license/), no attribution required.
    Returns image bytes, or None if no API key is configured or no result
    was found.
    """
    keywords = _relevance_keywords(query)
    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "photo", min_matches=min_matches, allow_cooldown_fallback=False)
        if cached:
            data, _ = cached
            return data

    api_key = get_pixabay_key()
    if api_key:
        r = requests.get(
            PIXABAY_SEARCH_URL,
            params={"key": api_key, "q": _pixabay_query(query), "image_type": "photo", "orientation": "vertical", "per_page": 5, "safesearch": "true"},
            timeout=30,
        )
        if r.status_code != 200:
            log(f"Pixabay search {r.status_code} for '{query}': {r.text[:150]}")
        else:
            hits = r.json().get("hits") or []
            for hit in hits:
                # Pixabay's own tags field (comma-separated) is the relevance
                # signal, same role as Pexels' alt-text.
                if not _is_relevant(hit.get("tags", ""), keywords, min_keywords=min_keywords, min_matches=min_matches):
                    continue
                img_url = hit.get("largeImageURL") or hit.get("webformatURL")
                if not img_url:
                    continue
                img_r = requests.get(img_url, timeout=30)
                if img_r.status_code == 200:
                    if is_on_cooldown(img_r.content, "photo"):
                        continue
                    if keywords:
                        add_to_library(img_r.content, keywords, "photo", source="pixabay", source_url=img_url)
                    return img_r.content
            if hits:
                log(f"Pixabay photo search for '{query}': {len(hits)} result(s), none actually relevant")

    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "photo", min_matches=min_matches)
        if cached:
            data, needs_variation = cached
            return _mirror_asset(data, "photo") if needs_variation else data
    return None


@with_retry(max_retries=2, base_delay=1.5)
def search_pixabay_video(query: str, min_keywords: int = 2, min_matches: int = 1) -> bytes | None:
    """Search Pixabay for a video clip matching `query`. Second-tier
    fallback behind Pexels. Returns raw mp4 bytes, or None if no API key is
    configured or no result was found.
    """
    keywords = _relevance_keywords(query)
    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "video", min_matches=min_matches, allow_cooldown_fallback=False)
        if cached:
            data, _ = cached
            return data

    api_key = get_pixabay_key()
    if api_key:
        r = requests.get(
            PIXABAY_VIDEO_SEARCH_URL,
            params={"key": api_key, "q": _pixabay_query(query), "per_page": 15, "safesearch": "true"},
            timeout=30,
        )
        if r.status_code != 200:
            log(f"Pixabay video search {r.status_code} for '{query}': {r.text[:150]}")
        else:
            hits = r.json().get("hits") or []
            for hit in hits:
                if not _is_relevant(hit.get("tags", ""), keywords, min_keywords=min_keywords, min_matches=min_matches):
                    continue
                variants = hit.get("videos", {})
                # Prefer the smallest variant that's still at least 720p tall,
                # same reasoning as the Pexels video picker — keep download/
                # transcode time reasonable.
                candidates = [v for v in variants.values() if v.get("url") and v.get("height", 0) >= 720]
                if not candidates:
                    candidates = [v for v in variants.values() if v.get("url")]
                if not candidates:
                    continue
                candidates.sort(key=lambda v: v.get("height", 0))
                video_url = candidates[0]["url"]
                video_r = requests.get(video_url, timeout=60)
                if video_r.status_code == 200:
                    if is_on_cooldown(video_r.content, "video"):
                        continue
                    if keywords:
                        add_to_library(video_r.content, keywords, "video", source="pixabay", source_url=video_url)
                    return video_r.content
            if hits:
                log(f"Pixabay video search for '{query}': {len(hits)} result(s), none actually relevant")

    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "video", min_matches=min_matches)
        if cached:
            data, needs_variation = cached
            return _mirror_asset(data, "video") if needs_variation else data
    return None


def fetch_last_resort_stock_photo(prompt: str) -> bytes | None:
    """Real-photo rule: when AI generation has already failed vision QA on
    every retry, a real stock photo with a merely plausible keyword match
    beats accepting a known-flawed AI image — real photos carry zero
    anatomy/text-garbling risk. Only call this after generation has actually
    exhausted its own retries, not as a first choice (the normal strict-match
    search in generate_broll() already runs first and is preferred whenever
    it succeeds).

    Relaxes the relevance bar to a single keyword match instead of two —
    still real-text-based, just less conservative than the default search,
    since the alternative at this point is a confirmed-broken generation.
    """
    query = _query_from_prompt(prompt)
    return search_pexels_photo(query, min_keywords=1)


@with_retry(max_retries=2, base_delay=1.5)
def search_pexels_video(query: str, min_keywords: int = 2, min_matches: int = 1) -> bytes | None:
    """Search Pexels for a video clip matching `query`. Returns raw mp4
    bytes, or None if no API key is configured or no result was found."""
    keywords = _relevance_keywords(query)
    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "video", min_matches=min_matches, allow_cooldown_fallback=False)
        if cached:
            data, _ = cached
            return data

    api_key = get_pexels_key()
    if api_key:
        r = requests.get(
            PEXELS_VIDEO_SEARCH_URL,
            # Pexels videos carry no alt-text (only a URL slug as a relevance
            # signal, weaker than photos' full alt-text), so a wider candidate
            # pool is checked here to give the same 2-keyword relevance bar more
            # chances to find an actual match rather than falling back to a
            # still image just because the first few candidates didn't hit.
            params={"query": query, "per_page": 15, "orientation": "portrait"},
            headers={"Authorization": api_key},
            timeout=30,
        )
        if r.status_code != 200:
            log(f"Pexels video search {r.status_code} for '{query}': {r.text[:150]}")
        else:
            videos = r.json().get("videos") or []
            for video in videos:
                # Pexels videos have no alt-text field, but the page URL slug is
                # descriptive, e.g. ".../video/a-man-playing-drums-1234567/" — the
                # only signal available to check the result actually matches.
                slug = re.sub(r"[-/]", " ", video.get("url", ""))
                if not _is_relevant(slug, keywords, min_keywords=min_keywords, min_matches=min_matches):
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
                    if is_on_cooldown(video_r.content, "video"):
                        continue
                    if keywords:
                        add_to_library(video_r.content, keywords, "video", source="pexels", source_url=video_url)
                    return video_r.content
            if videos:
                log(f"Pexels video search for '{query}': {len(videos)} result(s), none actually relevant")

    if len(keywords) >= min_keywords:
        cached = find_in_library(keywords, "video", min_matches=min_matches)
        if cached:
            data, needs_variation = cached
            return _mirror_asset(data, "video") if needs_variation else data
    return None


# Real stock footage over AI generation, as a general policy: a real photo
# or clip carries zero anatomy/text-garbling risk, while AI generation has
# repeatedly produced exactly that risk this project — extra limbs, melted
# keyboard keys, warped crowd seating, malformed controllers. Only 1
# keyword needs to *exist* to attempt a match (down from the original
# 2-keyword bar), so the tradeoff now favors real footage broadly, not just
# a narrow hardware allowlist — but callers using this constant also pass
# min_matches=2, requiring 2 independent keyword hits whenever 2+ keywords
# are available. That second guard was added after a real miss: a rich,
# multi-object prompt matched an unrelated photo on a single incidental
# word ("leaves") while everything else about it was wrong. This doesn't
# reintroduce the real-person risk the original 2-keyword bar was written
# to guard against — that's independently handled by draft.py's rule
# against ever prompting for a real person's likeness in the first place,
# so a real stock photo is never being asked to stand in for a specific
# named individual here.
DEFAULT_MIN_KEYWORDS = 1


def _match_score(descriptive_text: str, keywords: list[str]) -> int:
    """How many distinctive keywords a result's own text actually mentions —
    the ranking counterpart to `_is_relevant`'s pass/fail check."""
    text = descriptive_text.lower()
    return sum(1 for kw in keywords if word_boundary_match(kw, text))


def _anchor_keywords(query: str) -> set[str]:
    """The genuinely distinctive proper-noun/acronym terms in a query
    ("Pokemon", "TCG", "Walmart", "MacBook") — as opposed to any of the
    ordinary lowercase descriptive words around them.

    This is the fix for a whack-a-mole pattern that kept recurring: a
    prompt like "Pokemon TCG cards selling for pennies, stock price
    falling" matched a totally unrelated video of kids playing with toy
    blocks, purely because it happened to mention "red"/"yellow" (matching
    "red and yellow color scheme" from a *different* prompt's cached tags).
    No amount of blacklisting individual generic words fixes this in
    general — ordinary English words will always coincidentally overlap
    with *something*. Requiring the result to mention at least one
    genuinely distinctive term (when the query has one) catches this
    whole class of false positive at once, instead of one word at a time.

    Consecutive proper-noun/acronym words are grouped into a single phrase
    anchor ("Resident Evil 2" -> "resident evil") rather than two
    independent single-word anchors. Splitting them independently was a
    real, found bug: a completely unrelated racing-game clip passed the
    anchor check for a "Resident Evil 2" prompt because its own indexed
    text happened to separately contain "resident" and "evil" nowhere near
    each other — either word alone is common enough to false-positive, but
    the adjacent two-word phrase is a much stronger, much rarer signal for
    a specific multi-word franchise/product name. Separate, unrelated
    anchor terms in the same prompt (e.g. "Pokemon TCG cards on a Walmart
    shelf" naming two distinct brands) still end up as two independent
    phrase anchors here, each still satisfying `_has_anchor`'s `any()`
    check on its own — only genuinely adjacent proper-noun runs get merged.
    """
    raw = [w.strip(".,!?\"'()[]") for w in query.split()]
    anchors = set()
    current_run: list[str] = []

    def _flush():
        if current_run:
            anchors.add(" ".join(current_run))
            current_run.clear()

    for w in raw:
        lw = w.lower()
        # A bare number inside a run (e.g. the "7" in "Final Fantasy 7
        # Remake") does not break it, even though a lone digit is too short
        # to itself count as proper-noun evidence. Without this, "Final
        # Fantasy 7 Remake" split into two independent anchors at the
        # digit — "final fantasy" and "remake" — and "remake" alone turned
        # out to be a real tag on an unrelated Pixabay video (a generic
        # "old movie remake" intro template), which passed the anchor check
        # for a Final Fantasy prompt and served a completely unrelated
        # dinosaur video as "relevant" b-roll.
        if w.isdigit():
            continue
        # Filtering stopwords/generic-descriptors first already excludes
        # ordinary sentence-initial words ("A", "The") — no need to special-
        # case position 0 on top of that, and some of our own prompts are
        # written as just the bare subject ("Pokemon TCG cards..."), where
        # the one proper noun that matters IS the first word.
        if not w or len(lw) <= 2 or lw in STOPWORDS or lw in _GENERIC_DESCRIPTOR_WORDS:
            _flush()
            continue
        is_acronym = w.isupper() and len(w) > 1
        is_proper = w[:1].isupper()
        if is_acronym or is_proper:
            current_run.append(lw)
        else:
            _flush()
    _flush()
    return anchors


def _has_anchor(descriptive_text: str, anchors: set[str]) -> bool:
    """Whether a result's own text mentions at least one anchor term — a
    no-op (always True) when the query had no proper-noun/acronym anchors
    to check, since not every prompt names a specific brand/product."""
    if not anchors:
        return True
    text = descriptive_text.lower()
    return any(word_boundary_match(a, text) for a in anchors)


@with_retry(max_retries=2, base_delay=1.5)
def _pexels_photo_candidates(query: str, keywords: list[str], min_keywords: int, min_matches: int, anchors: set[str] | None = None) -> list[tuple[int, str]]:
    """Score every Pexels photo result against `keywords` without
    downloading anything, so the caller can compare Pexels' best candidate
    against Pixabay's best candidate instead of accepting Pexels' first
    passable hit sight-unseen. Returns (score, image_url) pairs that clear
    the relevance bar."""
    if len(keywords) < min_keywords:
        return []
    api_key = get_pexels_key()
    if not api_key:
        return []
    r = requests.get(
        PEXELS_SEARCH_URL,
        params={"query": query, "per_page": 5, "orientation": "portrait"},
        headers={"Authorization": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        log(f"Pexels search {r.status_code} for '{query}': {r.text[:150]}")
        return []
    required = min(min_matches, len(keywords))
    out = []
    for photo in r.json().get("photos") or []:
        alt = photo.get("alt", "")
        score = _match_score(alt, keywords)
        if score < required or not _has_anchor(alt, anchors):
            continue
        src = photo.get("src", {})
        img_url = src.get("large2x") or src.get("large") or src.get("original")
        if img_url:
            out.append((score, img_url))
    return out


@with_retry(max_retries=2, base_delay=1.5)
def _pixabay_photo_candidates(query: str, keywords: list[str], min_keywords: int, min_matches: int, anchors: set[str] | None = None) -> list[tuple[int, str]]:
    """Pixabay counterpart to `_pexels_photo_candidates` — scores results
    against `keywords` using Pixabay's tags field, without downloading."""
    if len(keywords) < min_keywords:
        return []
    api_key = get_pixabay_key()
    if not api_key:
        return []
    r = requests.get(
        PIXABAY_SEARCH_URL,
        params={"key": api_key, "q": _pixabay_query(query), "image_type": "photo", "orientation": "vertical", "per_page": 5, "safesearch": "true"},
        timeout=30,
    )
    if r.status_code != 200:
        log(f"Pixabay search {r.status_code} for '{query}': {r.text[:150]}")
        return []
    required = min(min_matches, len(keywords))
    out = []
    for hit in r.json().get("hits") or []:
        tags = hit.get("tags", "")
        score = _match_score(tags, keywords)
        if score < required or not _has_anchor(tags, anchors):
            continue
        img_url = hit.get("largeImageURL") or hit.get("webformatURL")
        if img_url:
            out.append((score, img_url))
    return out


@with_retry(max_retries=2, base_delay=1.5)
def _pexels_video_candidates(query: str, keywords: list[str], min_keywords: int, min_matches: int, anchors: set[str] | None = None) -> list[tuple[int, str]]:
    """Video counterpart to `_pexels_photo_candidates`, scored against the
    page-URL slug (Pexels videos carry no alt-text)."""
    if len(keywords) < min_keywords:
        return []
    api_key = get_pexels_key()
    if not api_key:
        return []
    r = requests.get(
        PEXELS_VIDEO_SEARCH_URL,
        params={"query": query, "per_page": 15, "orientation": "portrait"},
        headers={"Authorization": api_key},
        timeout=30,
    )
    if r.status_code != 200:
        log(f"Pexels video search {r.status_code} for '{query}': {r.text[:150]}")
        return []
    required = min(min_matches, len(keywords))
    out = []
    for video in r.json().get("videos") or []:
        slug = re.sub(r"[-/]", " ", video.get("url", ""))
        score = _match_score(slug, keywords)
        if score < required or not _has_anchor(slug, anchors):
            continue
        files = [f for f in (video.get("video_files") or []) if f.get("height", 0) >= 720]
        if not files:
            files = video.get("video_files") or []
        if not files:
            continue
        files.sort(key=lambda f: f.get("height", 0))
        video_url = files[0].get("link")
        if video_url:
            out.append((score, video_url))
    return out


@with_retry(max_retries=2, base_delay=1.5)
def _pixabay_video_candidates(query: str, keywords: list[str], min_keywords: int, min_matches: int, anchors: set[str] | None = None) -> list[tuple[int, str]]:
    """Video counterpart to `_pixabay_photo_candidates`."""
    if len(keywords) < min_keywords:
        return []
    api_key = get_pixabay_key()
    if not api_key:
        return []
    r = requests.get(
        PIXABAY_VIDEO_SEARCH_URL,
        params={"key": api_key, "q": _pixabay_query(query), "per_page": 15, "safesearch": "true"},
        timeout=30,
    )
    if r.status_code != 200:
        log(f"Pixabay video search {r.status_code} for '{query}': {r.text[:150]}")
        return []
    required = min(min_matches, len(keywords))
    out = []
    for hit in r.json().get("hits") or []:
        tags = hit.get("tags", "")
        score = _match_score(tags, keywords)
        if score < required or not _has_anchor(tags, anchors):
            continue
        variants = hit.get("videos", {})
        candidates = [v for v in variants.values() if v.get("url") and v.get("height", 0) >= 720]
        if not candidates:
            candidates = [v for v in variants.values() if v.get("url")]
        if not candidates:
            continue
        candidates.sort(key=lambda v: v.get("height", 0))
        out.append((score, candidates[0]["url"]))
    return out


def _required_matches(keywords: list[str]) -> int:
    """How many independent keyword hits a result needs to count as
    relevant, scaled to the length of the keyword list.

    A flat `min_matches=2` was tuned back when `_relevance_keywords()`
    always returned at most 4 words (via the old length-ranked
    headline-extraction heuristic). Now that it returns every content word
    in the description — sometimes 15+ for a rich b-roll prompt — a flat 2
    became too loose a bar: 2 coincidental word hits out of 17 is easy to
    hit by chance, which is exactly how a totally unrelated cached photo
    (an alley/balcony shot matched on "cluttered"+"checkout" alone) got
    served as a "relevant" match. Scaling keeps short lists at the original
    bar (2) while requiring proportionally more agreement for longer ones.
    """
    return max(2, len(keywords) // 4)


def _broadened_keywords(keywords: list[str]) -> list[str] | None:
    """Drop down to just the core subject words, keeping only the first few
    (reading order tends to put the subject before its qualifying details:
    "A [shot] of [SUBJECT] with/next to [DETAIL], conveying [MOOD]") — the
    fix for a real, recurring failure mode: a b-roll prompt describing a
    compound scene ("Pokemon cards next to a lottery ticket") searches stock
    providers for the literal compound scene, which usually doesn't exist as
    a real photo/clip even though the core subject ("Pokemon cards") very
    much does. Capped at 4 regardless of the full list's length — keeping
    "half" of a 17-word list is still 8 words, nowhere near loose enough,
    and paired with a low min_matches that produced an unrelated clothing-
    rack video matching on a single incidental word. Returns None when
    there's nothing left to drop (1 or 0 keywords already)."""
    if len(keywords) <= 1:
        return None
    return keywords[: min(4, max(1, len(keywords) // 2))]


def _download_and_cache(
    url: str, keywords: list[str], media_type: str, source: str, timeout: int = 60,
    exclude_paths: set[str] | None = None,
) -> bytes | None:
    if url in _BLOCKED_SOURCE_URLS:
        return None
    r = requests.get(url, timeout=timeout)
    if r.status_code != 200:
        return None
    if exclude_paths and str(asset_path_for(r.content, media_type)) in exclude_paths:
        # This exact asset (by content hash) was already used earlier in
        # the same video, even though it came from a fresh live search this
        # time — the caller should move on to the next-best candidate.
        return None
    if is_on_cooldown(r.content, media_type):
        # Same asset (by content hash) is already in the library and was
        # used within the last 7 days — a stock provider returning its same
        # stable top result for a generic query doesn't make it "fresh"
        # just because this search call is new. Move on to the next
        # candidate instead of silently violating the cooldown.
        log(f"Live search hit for '{source}' is on the 7-day reuse cooldown — trying next candidate")
        return None
    if keywords:
        add_to_library(r.content, keywords, media_type, source=source, source_url=url)
    return r.content


def fetch_topical_broll_video(prompt: str, exclude_paths: set[str] | None = None) -> bytes | None:
    """Try to find a real, licensed stock video clip matching a b-roll prompt.

    Queries Pexels and Pixabay in parallel and downloads whichever result
    scores as the stronger keyword match — not just whichever provider was
    checked first. The two catalogs don't overlap much, and always stopping
    at Pexels' first passable hit meant a genuinely better Pixabay match for
    the same prompt was never even considered. Returns None (caller should
    fall back to a still photo or AI generation) if neither is configured or
    no relevant clip was found on either side.

    `exclude_paths` (asset local paths already used earlier in the same
    video — see generate_broll()) are skipped even if they'd otherwise be
    the top match, so two similarly-worded prompts in one video ("a
    prominent Apple logo" / "a MacBook with the Apple logo visible") can't
    end up showing the identical clip twice.
    """
    query = _query_from_prompt(prompt)
    if not query:
        return None
    keywords = _relevance_keywords(query)
    anchors = _anchor_keywords(query)
    if len(keywords) >= DEFAULT_MIN_KEYWORDS:
        cached = find_in_library(
            keywords, "video", min_matches=_required_matches(keywords),
            exclude_paths=exclude_paths, allow_cooldown_fallback=False, anchors=anchors,
        )
        if cached:
            data, _ = cached
            return data

    candidates: list[tuple[int, str, str]] = []
    try:
        candidates += [(s, u, "pexels") for s, u in _pexels_video_candidates(query, keywords, DEFAULT_MIN_KEYWORDS, _required_matches(keywords), anchors)]
    except Exception as e:
        log(f"Pexels video lookup failed for '{query}': {e}")
    try:
        candidates += [(s, u, "pixabay") for s, u in _pixabay_video_candidates(query, keywords, DEFAULT_MIN_KEYWORDS, _required_matches(keywords), anchors)]
    except Exception as e:
        log(f"Pixabay video lookup failed for '{query}': {e}")

    if not candidates:
        broad_keywords = _broadened_keywords(keywords)
        if broad_keywords:
            broad_query = " ".join(broad_keywords)
            log(f"No stock video match for '{query}' — retrying broader: '{broad_query}'")
            # Broaden the SEARCH TEXT sent to the API (a shorter, more
            # generic query casts a wider net), but still score results
            # against the FULL original keyword list, not just the shrunk
            # broad_keywords — scoring against only 4 generic words let a
            # completely unrelated cartoon shopping-ad clip "pass" on 2
            # incidental hits. A genuinely relevant result for a specific
            # compound scene tends to still mention several of the *other*
            # original keywords too (a real lottery-ticket photo's own
            # alt-text says "lottery"/"ticket"/"stack", even though the
            # broadened query text that found it didn't contain them).
            try:
                candidates += [(s, u, "pexels") for s, u in _pexels_video_candidates(broad_query, keywords, 1, _required_matches(keywords), anchors)]
            except Exception as e:
                log(f"Pexels broadened video lookup failed for '{broad_query}': {e}")
            try:
                candidates += [(s, u, "pixabay") for s, u in _pixabay_video_candidates(broad_query, keywords, 1, _required_matches(keywords), anchors)]
            except Exception as e:
                log(f"Pixabay broadened video lookup failed for '{broad_query}': {e}")

    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        for score, url, source in candidates:
            result = _download_and_cache(url, keywords, "video", source, exclude_paths=exclude_paths)
            if result:
                return result

    # No live match either — a stale (cooldown-violating) cache hit,
    # mirror-flipped, beats no b-roll at all. Still anchor-gated: a stale
    # clip that fails the same proper-noun check a live search would have
    # enforced is worse than no stock match (the caller falls through to
    # real AI generation instead), not better.
    if len(keywords) >= DEFAULT_MIN_KEYWORDS:
        cached = find_in_library(keywords, "video", min_matches=_required_matches(keywords), exclude_paths=exclude_paths, anchors=anchors)
        if cached:
            data, needs_variation = cached
            return _mirror_asset(data, "video") if needs_variation else data
    return None


def fetch_topical_broll(prompt: str, exclude_paths: set[str] | None = None) -> bytes | None:
    """Try to find real, licensed stock footage matching a b-roll prompt.

    Queries Pexels and Pixabay in parallel and downloads whichever result
    scores as the stronger keyword match, rather than accepting Pexels'
    first merely-passable hit and never checking whether Pixabay actually
    had a better one for this specific prompt. Returns None (caller should
    fall back to AI generation) if neither is configured or no relevant
    photo was found on either side.
    """
    query = _query_from_prompt(prompt)
    if not query:
        return None
    keywords = _relevance_keywords(query)
    anchors = _anchor_keywords(query)
    if len(keywords) >= DEFAULT_MIN_KEYWORDS:
        cached = find_in_library(
            keywords, "photo", min_matches=_required_matches(keywords),
            exclude_paths=exclude_paths, allow_cooldown_fallback=False, anchors=anchors,
        )
        if cached:
            data, _ = cached
            return data

    candidates: list[tuple[int, str, str]] = []
    try:
        candidates += [(s, u, "pexels") for s, u in _pexels_photo_candidates(query, keywords, DEFAULT_MIN_KEYWORDS, _required_matches(keywords), anchors)]
    except Exception as e:
        log(f"Pexels lookup failed for '{query}': {e}")
    try:
        candidates += [(s, u, "pixabay") for s, u in _pixabay_photo_candidates(query, keywords, DEFAULT_MIN_KEYWORDS, _required_matches(keywords), anchors)]
    except Exception as e:
        log(f"Pixabay lookup failed for '{query}': {e}")

    if not candidates:
        broad_keywords = _broadened_keywords(keywords)
        if broad_keywords:
            broad_query = " ".join(broad_keywords)
            log(f"No stock photo match for '{query}' — retrying broader: '{broad_query}'")
            # See the matching comment in fetch_topical_broll_video: broaden
            # the search text, but keep scoring against the full keyword
            # list so an unrelated result can't pass on a couple of generic
            # words alone.
            try:
                candidates += [(s, u, "pexels") for s, u in _pexels_photo_candidates(broad_query, keywords, 1, _required_matches(keywords), anchors)]
            except Exception as e:
                log(f"Pexels broadened lookup failed for '{broad_query}': {e}")
            try:
                candidates += [(s, u, "pixabay") for s, u in _pixabay_photo_candidates(broad_query, keywords, 1, _required_matches(keywords), anchors)]
            except Exception as e:
                log(f"Pixabay broadened lookup failed for '{broad_query}': {e}")

    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        for score, url, source in candidates:
            result = _download_and_cache(url, keywords, "photo", source, exclude_paths=exclude_paths)
            if result:
                return result

    # No live match either — a stale (cooldown-violating) cache hit,
    # mirror-flipped, beats no b-roll at all. Still anchor-gated: a stale
    # clip that fails the same proper-noun check a live search would have
    # enforced is worse than no stock match (the caller falls through to
    # real AI generation instead), not better.
    if len(keywords) >= DEFAULT_MIN_KEYWORDS:
        cached = find_in_library(keywords, "photo", min_matches=_required_matches(keywords), exclude_paths=exclude_paths, anchors=anchors)
        if cached:
            data, needs_variation = cached
            return _mirror_asset(data, "photo") if needs_variation else data
    return None


def fetch_img2img_reference_photo(prompt: str) -> bytes | None:
    """Find a real photo to use as an img2img *starting point* for AI
    generation — a materially looser bar than fetch_topical_broll(), which
    decides whether to use a photo directly as the final frame.

    This has to be a genuinely different (looser) tier, not the same search
    fetch_topical_broll() already tried: since that search is deterministic,
    re-running it with identical arguments after it has already returned
    None for this exact prompt can never succeed — that was the original bug
    (the img2img path could never actually trigger from generate_broll()).

    The looser min_matches=1 bar is acceptable specifically because this
    result is never shown as-is — it's only a structural/compositional
    starting point for img2img at a high denoising strength (~0.7), which
    redraws the reference rather than preserving it. A same-object-family
    but imperfect match (found, but not confident enough for a direct swap)
    is still useful raw material here in a way it wouldn't be as a final
    frame.
    """
    query = _query_from_prompt(prompt)
    if not query:
        return None
    try:
        return search_pexels_photo(query, min_keywords=1, min_matches=1)
    except Exception as e:
        log(f"Pexels img2img-reference lookup failed for '{query}': {e} — falling back to pure txt2img")
        return None

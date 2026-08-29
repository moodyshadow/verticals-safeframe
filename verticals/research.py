"""Article-fetch + DuckDuckGo research — anti-hallucination gate."""

import requests
from html.parser import HTMLParser

from .config import extract_keywords
from .log import log
from .retry import with_retry


class _ArticleTextParser(HTMLParser):
    """Pulls visible text out of <p> tags, skipping script/style content."""

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._in_p = False
        self._current = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip_depth += 1
        elif tag == "p":
            self._in_p = True
            self._current = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "p" and self._in_p:
            text = "".join(self._current).strip()
            if text:
                self.paragraphs.append(text)
            self._in_p = False

    def handle_data(self, data):
        if self._in_p and self._skip_depth == 0:
            self._current.append(data)


@with_retry(max_retries=2, base_delay=1.5)
def fetch_article_text(url: str, max_chars: int = 3000) -> str:
    """Fetch and extract the visible paragraph text of a real source article.

    This is the actual article that inspired the topic (its URL comes
    straight from the RSS/news feed entry), so it's a far more reliable
    anti-hallucination grounding source than a blind keyword search — and
    it doesn't depend on a search engine's HTML endpoint staying scrapable.
    """
    if not url:
        return ""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()

    parser = _ArticleTextParser()
    parser.feed(r.text)
    text = "\n".join(parser.paragraphs)
    return text[:max_chars]


@with_retry(max_retries=2, base_delay=2.0)
def _fetch_ddg(keywords: str) -> str:
    """Fetch search snippets from DuckDuckGo HTML endpoint."""
    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
    r = requests.post(url, data={"q": keywords}, headers=headers, timeout=10)
    r.raise_for_status()
    return r.text


def _research_via_ddg(news: str) -> str:
    keywords = extract_keywords(news)
    html = _fetch_ddg(keywords)

    snippets = []

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self._in = False
            self._text = []

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "a" and "result__snippet" in d.get("class", ""):
                self._in = True
                self._text = []

        def handle_endtag(self, tag):
            if self._in and tag == "a":
                snippets.append("".join(self._text).strip())
                self._in = False

        def handle_data(self, data):
            if self._in:
                self._text.append(data)

    p = Parser()
    p.feed(html)
    # Sanitize snippets: truncate each to limit prompt injection surface
    snippets = [s[:300] for s in snippets]
    return "\n".join(snippets[:8]) if snippets else ""


def research_topic(news: str, url: str = "", summary: str = "") -> str:
    """Ground the script in real facts, preferring the actual source article.

    Priority order:
    1. The real article the topic came from (`url`, from RSS/news feed data)
       — this is the most reliable source since it's exactly the thing the
       headline is about, not a guess from a keyword search.
    2. The feed's own summary/snippet for that entry, if fetching the full
       article failed.
    3. A DuckDuckGo keyword search, kept as a last resort (DDG's HTML
       endpoint increasingly serves anti-bot challenges to scripted
       requests, so this frequently returns nothing).
    4. A "no research available" placeholder — the script must stay general.
    """
    if url:
        log(f"Fetching source article: {url}")
        try:
            article = fetch_article_text(url)
            if len(article) > 200:
                return f"Topic: {news}\nSource article ({url}):\n{article}"
        except Exception as e:
            log(f"Article fetch failed: {e} — trying other sources.")

    if summary and len(summary) > 40:
        return f"Topic: {news}\nSource summary: {summary}"

    log("Researching topic via DuckDuckGo...")
    try:
        research = _research_via_ddg(news)
        if research:
            log(f"Found DuckDuckGo snippets.")
            return research
    except Exception as e:
        log(f"DuckDuckGo research failed: {e} — proceeding without.")

    return f"Topic: {news}\n(No live research available — script must stay general.)"

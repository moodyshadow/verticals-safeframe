"""One-off: feed the channel's real, current YouTube stats through the
local marketing-helper Ollama model (qwen2.5:14b-instruct, port 11435) to
produce a blunt strategy critique — reusing the same "no cloud API cost,
grounded in real data" approach as marketing_agent.py, just with the
stronger local model and a user-supplied critique structure instead of
that script's own daily-report format.
"""
import json
import requests

CHANNEL_FACTS = """
Channel: "daily overclocked" (@dailyoverclocked)
Description: "Daily Overclocked. Fast, no-fluff breakdowns of the tech and gaming news that actually matters — new video every day."
Subscribers: 7
Total views (channel-level stat): 277
Total public videos: 9
All videos are YouTube Shorts, 30s-1m19s long.
Note: summing the per-video view counts below gives 585, not 277 — YouTube's
channel-level aggregate is known to lag behind per-video counts by up to
1-2 days, so treat 277 as stale and the per-video numbers as current truth.
"""

VIDEOS = """
1. "The AI Ban in NYC Schools: A Problem in Disguise?" | tech | 2 views | 0 likes | published 2026-09-03 | 52s
2. "Larry Page's Flying Car Company Pivotal Loses CEO" | tech | 20 views | 0 likes | published 2026-09-02 | 1m19s
3. "GTA 6 Dating Mechanics Spark Controversy on TikTok" | gaming | 13 views | 0 likes | published 2026-09-02 | 30s
4. "Pokémon TCG Debacle: Walmart Resorts to Lottery due to desperation" | gaming | 159 views | 0 likes | published 2026-09-01 | 50s
5. "Apple vs OpenAI: New Evidence Revealed" | tech | 25 views | 0 likes | published 2026-09-01 | 56s
6. "Gamescom 2026: PoE 2 Hits 1.0, FF7 Revelation, Snoop Dogg Joins Hitman" | gaming | 83 views | 2 likes | published 2026-08-31 | 48s
7. "Nvidia's AI Advantage Goes Beyond the GPU" | tech | 176 views | 3 likes | published 2026-08-29 | 47s
8. "GAMESCOM 2026: Record Attendance, Biggest Reveals Yet!" | gaming | 85 views | 2 likes | published 2026-08-29 | 35s
9. "The Dark Side of AI Tools" | tech | 22 views | 4 likes | published 2026-08-25 | 41s
"""

UPLOAD_PATTERN = """
Actual upload cadence derived from the dates above: first video 2026-08-25,
most recent 2026-09-03 — 9 videos across 9 calendar days, but NOT one
per day: a 4-day gap between Aug 25 and Aug 29, then two videos on the
same day on Aug 29, Sep 1, and Sep 2. The channel's own tagline claims
"new video every day" but the real upload log doesn't match that yet.
"""

PROMPT = f"""You are a blunt, experienced YouTube strategy consultant reviewing a small
channel's real performance data. Do not soften the critique or validate
choices just because the creator made them. Base every claim strictly on
the data given — do not invent numbers, view counts, or trends not shown
here.

{CHANNEL_FACTS}

RECENT VIDEOS (most recent first):
{VIDEOS}

{UPLOAD_PATTERN}

Monetization: not stated, assume not monetized (7 subscribers, well below
YouTube Partner Program thresholds).
Target audience: tech/gaming news viewers, fast-consumption Shorts format.
Format: daily-intended tech and gaming news breakdowns, no-fluff, under 90
seconds each, both niches run in parallel from the same channel.

Answer these six questions directly, in order, with a short header for
each:

1. Given the intended daily upload frequency in a news-breakdown niche, is
   that pace realistic at this stage, or is it likely hurting quality/
   burning the creator out for little payoff? Use the actual upload
   cadence data above, not the channel's stated intent.
2. Based on the real view numbers, which topics/stories are clearly
   hitting vs. flopping? Is there a pattern in what performs (e.g. a real
   consumer controversy vs. an incremental company-news recap)?
3. Is "fast, no-fluff" actually a differentiator in this niche, or is this
   channel competing head-on with much bigger established news-breakdown
   channels without a clear edge? Be specific about what a genuine edge
   would need to look like given the current output.
4. What is the single biggest thing holding growth back right now, based
   on this data (7 subscribers despite 585 cumulative views is a real
   signal — address it)?
5. Give 3 concrete, specific changes to try in the next 5 videos, each
   directly justified by a specific number or pattern from the data above
   — not generic YouTube advice.
6. Be direct: is this daily-upload two-niche news format working, or should
   it change? If the honest answer is "not yet proven" or "not working",
   say so plainly instead of hedging.

Keep the whole response under 500 words. Be specific and numbers-driven,
not encouraging for its own sake."""


def main() -> None:
    r = requests.post(
        "http://127.0.0.1:11435/api/generate",
        json={"model": "qwen2.5:14b-instruct", "prompt": PROMPT, "stream": False},
        timeout=900,
    )
    r.raise_for_status()
    text = r.json().get("response", "").strip()
    print(text)


if __name__ == "__main__":
    main()

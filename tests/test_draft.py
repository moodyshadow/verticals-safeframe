"""Tests for pipeline/draft.py — draft generation with mocked Claude API."""

import json
from unittest.mock import patch, MagicMock

from verticals.draft import generate_draft


class TestGenerateDraft:
    @patch("verticals.draft.research_topic")
    @patch("verticals.draft._call_claude")
    def test_basic_draft_generation(self, mock_claude, mock_research):
        mock_research.return_value = "Some research data about the topic."
        mock_claude.return_value = json.dumps({
            "script": "This is a test script about AI.",
            "broll_prompts": ["Prompt 1", "Prompt 2", "Prompt 3"],
            "youtube_title": "AI Revolution 2026",
            "youtube_description": "All about AI.",
            "youtube_tags": "ai,tech,2026",
            "instagram_caption": "AI is changing the world!",
            "thumbnail_prompt": "Futuristic AI image",
        })

        draft = generate_draft("AI is changing everything in 2026")

        assert draft["script"] == "This is a test script about AI."
        # Fewer prompts than BROLL_COUNT get cycled (repeated), not padded
        # with disconnected generic filler, so every frame stays on-topic.
        from verticals.config import BROLL_COUNT
        assert len(draft["broll_prompts"]) == BROLL_COUNT
        assert draft["youtube_title"] == "AI Revolution 2026"
        assert draft["news"] == "AI is changing everything in 2026"
        assert draft["research"] == "Some research data about the topic."

    @patch("verticals.draft.research_topic")
    @patch("verticals.draft._call_claude")
    def test_handles_code_block_wrapper(self, mock_claude, mock_research):
        mock_research.return_value = "research"
        mock_claude.return_value = '```json\n{"script":"test","broll_prompts":["p1","p2","p3"],"youtube_title":"T","youtube_description":"D","youtube_tags":"t","instagram_caption":"C","thumbnail_prompt":"P"}\n```'

        draft = generate_draft("Test topic")
        assert draft["script"] == "test"

    @patch("verticals.draft.research_topic")
    @patch("verticals.draft._call_claude")
    def test_sanitizes_non_string_fields(self, mock_claude, mock_research):
        mock_research.return_value = "research"
        mock_claude.return_value = json.dumps({
            "script": 12345,  # non-string
            "broll_prompts": [1, 2, 3],  # non-string items, but still a real list
            "youtube_title": "T",
            "youtube_description": "D",
            "youtube_tags": "t",
            "instagram_caption": "C",
            "thumbnail_prompt": "P",
        })

        draft = generate_draft("Test")
        assert isinstance(draft["script"], str)
        assert isinstance(draft["broll_prompts"], list)
        assert all(isinstance(p, str) for p in draft["broll_prompts"])

    @patch("verticals.draft.research_topic")
    @patch("verticals.draft._call_claude")
    def test_raises_when_broll_prompts_missing(self, mock_claude, mock_research):
        """A disconnected generic filler image is worse than retrying the LLM —
        omitted broll_prompts should raise so the caller retries/skips the topic,
        never silently fall back to off-topic imagery."""
        mock_research.return_value = "research"
        mock_claude.return_value = json.dumps({
            "script": "A real script about something specific.",
            "youtube_title": "T", "youtube_description": "D",
            "youtube_tags": "t", "instagram_caption": "C",
            "thumbnail_prompt": "P",
        })

        import pytest
        with pytest.raises(Exception):
            generate_draft("Test")

    @patch("verticals.draft.research_topic")
    @patch("verticals.draft._call_claude")
    def test_includes_channel_context(self, mock_claude, mock_research):
        mock_research.return_value = "research"
        mock_claude.return_value = json.dumps({
            "script": "s", "broll_prompts": ["p1", "p2", "p3"],
            "youtube_title": "T", "youtube_description": "D",
            "youtube_tags": "t", "instagram_caption": "C",
            "thumbnail_prompt": "P",
        })

        draft = generate_draft("Test", channel_context="esports news channel")
        # Verify the channel context was passed to Claude
        call_args = mock_claude.call_args[0][0]
        assert "esports news channel" in call_args

    @patch("verticals.draft.research_topic")
    @patch("verticals.draft._call_claude")
    def test_truncates_broll_prompts(self, mock_claude, mock_research):
        from verticals.config import BROLL_COUNT
        mock_research.return_value = "research"
        too_many = [f"p{i}" for i in range(BROLL_COUNT + 5)]
        mock_claude.return_value = json.dumps({
            "script": "s",
            "broll_prompts": too_many,
            "youtube_title": "T", "youtube_description": "D",
            "youtube_tags": "t", "instagram_caption": "C",
            "thumbnail_prompt": "P",
        })

        draft = generate_draft("Test")
        assert len(draft["broll_prompts"]) == BROLL_COUNT

    @patch("verticals.draft.research_topic")
    @patch("verticals.draft._call_claude")
    def test_cycles_short_broll_prompts(self, mock_claude, mock_research):
        """Fewer real prompts than BROLL_COUNT get cycled, not padded with
        disconnected generic filler."""
        from verticals.config import BROLL_COUNT
        mock_research.return_value = "research"
        mock_claude.return_value = json.dumps({
            "script": "s",
            "broll_prompts": ["p1", "p2", "p3"],
            "youtube_title": "T", "youtube_description": "D",
            "youtube_tags": "t", "instagram_caption": "C",
            "thumbnail_prompt": "P",
        })

        draft = generate_draft("Test")
        assert len(draft["broll_prompts"]) == BROLL_COUNT
        assert draft["broll_prompts"][0].startswith("p1")
        assert draft["broll_prompts"][3].startswith("p1")  # cycled back around

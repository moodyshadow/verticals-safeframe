"""Tests for the no-gradient-fallback policy and the stock-relevance threshold.

A produced video must never contain a plain color placeholder frame instead
of real b-roll — these tests lock in that a broken SD webui (or a weak,
one-keyword stock match) causes a loud failure instead of a silent degrade.
"""

from unittest.mock import MagicMock, patch

import pytest

from verticals.stock_media import _is_relevant


class TestRelevanceThreshold:
    def test_rejects_single_keyword_match(self):
        # Only "embarrassed" survives generic-word filtering for a
        # person-centric prompt — too weak a signal on its own.
        assert _is_relevant("a dimly lit concert crowd embarrassed applause", ["embarrassed"]) is False

    def test_accepts_two_or_more_keyword_match(self):
        assert _is_relevant("santa monica ferris wheel at night", ["ferris", "wheel"]) is True

    def test_rejects_when_no_keywords(self):
        assert _is_relevant("anything at all", []) is False

    def test_rejects_two_keywords_with_no_overlap(self):
        assert _is_relevant("a man playing drums live show", ["maher", "awkward"]) is False


class TestNoGradientFallback:
    @patch("verticals.broll.requests.get")
    def test_raises_when_sd_webui_unreachable(self, mock_get):
        from verticals.broll import generate_broll

        mock_get.side_effect = ConnectionError("refused")
        with pytest.raises(RuntimeError, match="not reachable"):
            generate_broll(["a prompt"], out_dir=MagicMock(), use_stock=False)

    def test_raises_when_gemini_key_missing(self, monkeypatch, tmp_path):
        from verticals import broll

        monkeypatch.setenv("BROLL_PROVIDER", "gemini")
        monkeypatch.setattr(broll, "get_gemini_key", lambda: None)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            broll.generate_broll(["a prompt"], out_dir=tmp_path, use_stock=False)

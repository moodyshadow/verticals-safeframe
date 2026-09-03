"""Tests for verticals/channel_counter.py — parsing and snapshot logic."""

from unittest.mock import patch

from verticals.channel_counter import (
    _parse_count,
    fetch_youtube_subscribers,
    fetch_tiktok_followers,
    fetch_instagram_followers,
    snapshot,
    load_history,
)


class TestParseCount:
    def test_plain_number(self):
        assert _parse_count("12345") == 12345

    def test_with_commas(self):
        assert _parse_count("12,345") == 12345

    def test_thousands_suffix(self):
        assert _parse_count("340K") == 340_000

    def test_millions_suffix(self):
        assert _parse_count("1.2M") == 1_200_000

    def test_invalid(self):
        assert _parse_count("not a number") is None


class TestFetchYoutube:
    def test_parses_subscriber_count(self):
        html = '"subscriberCountText":{"simpleText":"1.5M subscribers"}'
        with patch("verticals.channel_counter._fetch", return_value=html):
            assert fetch_youtube_subscribers("dailyoverclocked") == 1_500_000

    def test_missing_data_returns_none(self):
        with patch("verticals.channel_counter._fetch", return_value="<html></html>"):
            assert fetch_youtube_subscribers("dailyoverclocked") is None

    def test_fetch_failure_returns_none(self):
        with patch("verticals.channel_counter._fetch", return_value=None):
            assert fetch_youtube_subscribers("dailyoverclocked") is None


class TestFetchTiktok:
    def test_parses_follower_count(self):
        html = '"followerCount":98765,"followingCount":12'
        with patch("verticals.channel_counter._fetch", return_value=html):
            assert fetch_tiktok_followers("dailyoverclocked") == 98765

    def test_missing_data_returns_none(self):
        with patch("verticals.channel_counter._fetch", return_value="<html></html>"):
            assert fetch_tiktok_followers("dailyoverclocked") is None


class TestFetchInstagram:
    def test_parses_edge_followed_by(self):
        html = '"edge_followed_by":{"count":54321}'
        with patch("verticals.channel_counter._fetch", return_value=html):
            assert fetch_instagram_followers("dailyoverclocked") == 54321

    def test_parses_meta_fallback(self):
        html = 'content="12.3K Followers, 100 Following, 50 Posts"'
        with patch("verticals.channel_counter._fetch", return_value=html):
            assert fetch_instagram_followers("dailyoverclocked") == 12300

    def test_missing_data_returns_none(self):
        with patch("verticals.channel_counter._fetch", return_value="<html></html>"):
            assert fetch_instagram_followers("dailyoverclocked") is None


class TestSnapshot:
    def test_snapshot_sums_known_counts_and_logs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("verticals.channel_counter.HISTORY_PATH", tmp_path / "counter.jsonl")
        monkeypatch.setattr("verticals.channel_counter.SKILL_DIR", tmp_path)

        with patch("verticals.channel_counter.fetch_youtube_subscribers", return_value=1000), \
             patch("verticals.channel_counter.fetch_tiktok_followers", return_value=2000), \
             patch("verticals.channel_counter.fetch_instagram_followers", return_value=None):
            record = snapshot("dailyoverclocked")

        assert record["counts"] == {"youtube": 1000, "tiktok": 2000, "instagram": None}
        assert record["total"] == 3000
        assert record["handle"] == "dailyoverclocked"

        history = load_history("dailyoverclocked")
        assert len(history) == 1
        assert history[0]["total"] == 3000

    def test_snapshot_all_unavailable_gives_none_total(self, tmp_path, monkeypatch):
        monkeypatch.setattr("verticals.channel_counter.HISTORY_PATH", tmp_path / "counter.jsonl")
        monkeypatch.setattr("verticals.channel_counter.SKILL_DIR", tmp_path)

        with patch("verticals.channel_counter.fetch_youtube_subscribers", return_value=None), \
             patch("verticals.channel_counter.fetch_tiktok_followers", return_value=None), \
             patch("verticals.channel_counter.fetch_instagram_followers", return_value=None):
            record = snapshot("dailyoverclocked")

        assert record["total"] is None

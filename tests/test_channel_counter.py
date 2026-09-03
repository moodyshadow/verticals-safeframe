"""Tests for verticals/channel_counter.py — parsing and snapshot logic."""

from unittest.mock import patch

from verticals.channel_counter import (
    _parse_count,
    fetch_youtube_subscribers,
    fetch_tiktok_followers,
    fetch_instagram_followers,
    fetch_youtube_recent_videos,
    fetch_tiktok_recent_videos,
    fetch_instagram_recent_posts,
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


class TestFetchYoutubeRecentVideos:
    def test_parses_listing_then_watch_pages(self):
        listing_html = '"videoId":"aaaaaaaaaaa","videoId":"bbbbbbbbbbb","videoId":"aaaaaaaaaaa"'
        watch_html = (
            '"videoDetails":{"videoId":"aaaaaaaaaaa"},"viewCount":"123456",'
            '"title":{"simpleText":"Overclocking a 14900KS to 6.2GHz"},'
            '"label":"12,345 likes"'
        )

        def fake_fetch(url, timeout=10.0):
            if "/videos" in url:
                return listing_html
            return watch_html

        with patch("verticals.channel_counter._fetch", side_effect=fake_fetch):
            videos = fetch_youtube_recent_videos("dailyoverclocked", limit=2)

        assert len(videos) == 2
        assert videos[0]["video_id"] == "aaaaaaaaaaa"
        assert videos[0]["views"] == 123456
        assert videos[0]["likes"] == 12345
        assert videos[0]["title"] == "Overclocking a 14900KS to 6.2GHz"

    def test_listing_fetch_failure_returns_empty(self):
        with patch("verticals.channel_counter._fetch", return_value=None):
            assert fetch_youtube_recent_videos("dailyoverclocked") == []


class TestFetchTiktokRecentVideos:
    def test_parses_item_module(self):
        html = (
            '{"id":"7123456789012345678","desc":"6.2GHz on air, no cap",'
            '"stats":{"diggCount":5000,"shareCount":200,"commentCount":150,"playCount":100000}}'
        )
        with patch("verticals.channel_counter._fetch", return_value=html):
            videos = fetch_tiktok_recent_videos("dailyoverclocked", limit=5)

        assert len(videos) == 1
        assert videos[0]["video_id"] == "7123456789012345678"
        assert videos[0]["title"] == "6.2GHz on air, no cap"
        assert videos[0]["views"] == 100000
        assert videos[0]["likes"] == 5000
        assert videos[0]["comments"] == 150
        assert videos[0]["shares"] == 200

    def test_no_items_returns_empty(self):
        with patch("verticals.channel_counter._fetch", return_value="<html></html>"):
            assert fetch_tiktok_recent_videos("dailyoverclocked") == []

    def test_fetch_failure_returns_empty(self):
        with patch("verticals.channel_counter._fetch", return_value=None):
            assert fetch_tiktok_recent_videos("dailyoverclocked") == []


class TestFetchInstagramRecentPosts:
    def test_parses_post_edges(self):
        html = (
            '"shortcode":"Cabc123XYZ","edge_liked_by":{"count":4200},'
            '"edge_media_to_comment":{"count":85}'
        )
        with patch("verticals.channel_counter._fetch", return_value=html):
            posts = fetch_instagram_recent_posts("dailyoverclocked", limit=5)

        assert len(posts) == 1
        assert posts[0]["shortcode"] == "Cabc123XYZ"
        assert posts[0]["likes"] == 4200
        assert posts[0]["comments"] == 85
        assert posts[0]["url"] == "https://www.instagram.com/p/Cabc123XYZ/"

    def test_no_posts_returns_empty(self):
        with patch("verticals.channel_counter._fetch", return_value="<html></html>"):
            assert fetch_instagram_recent_posts("dailyoverclocked") == []


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

from nonebot_plugin_xfetch.core import tweet_pipeline, vision_images
from nonebot_plugin_xfetch.models.tweet import TweetItem, TweetMedia


def _item(*media):
    return TweetItem(id="1", text="t", media=list(media))


def test_select_post_images_caps_and_dedups(monkeypatch):
    monkeypatch.setattr(vision_images, "XFETCH_VISION_MAX_IMAGES_PER_SOURCE", 2)
    item = _item(
        TweetMedia(url="u1", type="photo"),
        TweetMedia(url="u1", type="photo"),
        TweetMedia(url="u2", type="photo"),
        TweetMedia(url="u3", type="photo"),
    )

    assert vision_images.select_post_images(item) == ["u1", "u2"]


def test_select_post_images_skips_video_by_default(monkeypatch):
    monkeypatch.setattr(
        vision_images, "XFETCH_VISION_INCLUDE_VIDEO_THUMB", False
    )
    item = _item(
        TweetMedia(url="v", thumbnail_url="thumb", type="video"),
        TweetMedia(url="p", type="photo"),
    )

    assert vision_images.select_post_images(item) == ["p"]


def test_select_post_images_uses_video_thumb_when_enabled(monkeypatch):
    monkeypatch.setattr(
        vision_images, "XFETCH_VISION_INCLUDE_VIDEO_THUMB", True
    )
    item = _item(TweetMedia(url="v", thumbnail_url="thumb", type="video"))

    assert vision_images.select_post_images(item) == ["thumb"]


def test_vision_url_requests_small_twitter_rendition():
    url = "https://pbs.twimg.com/media/ABC.jpg?name=medium"

    assert "name=small" in vision_images._vision_image_url(url)
    assert (
        vision_images._vision_image_url("https://example.com/a.jpg")
        == "https://example.com/a.jpg"
    )


def test_normalize_detected_events_dedups_and_caps(monkeypatch):
    monkeypatch.setattr(
        tweet_pipeline, "XFETCH_VISION_MAX_EVENTS_PER_POST", 2
    )
    events = [
        {"tid": "1:target", "event_key": "1:target#0",
         "event_title": "A", "event_start_utc": "2026-01-01T00:00:00Z"},
        {"tid": "1:target", "event_key": "1:target#1",
         "event_title": " A ", "event_start_utc": "2026-01-01T00:00:00Z"},
        {"tid": "1:target", "event_key": "1:target#2",
         "event_title": "B", "event_start_utc": "2026-01-02T00:00:00Z"},
        {"tid": "1:target", "event_key": "1:target#3",
         "event_title": "C", "event_start_utc": "2026-01-03T00:00:00Z"},
    ]

    result = tweet_pipeline._normalize_detected_events(events)

    assert [event["event_title"] for event in result] == ["A", "B"]


def test_normalize_detected_events_drops_empty():
    events = [
        {"tid": "1:target", "event_key": "k",
         "event_title": "", "event_start_utc": ""},
    ]

    assert tweet_pipeline._normalize_detected_events(events) == []

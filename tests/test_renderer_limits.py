import pytest

from nonebot_plugin_xfetch.models.tweet import (
    TweetAuthor,
    TweetConversation,
    TweetItem,
    TweetMedia,
)
from nonebot_plugin_xfetch.renderer import engine


def _tweet(tweet_id: str, text: str = "post") -> TweetItem:
    return TweetItem(
        id=tweet_id,
        author=TweetAuthor(name=tweet_id, screen_name=tweet_id),
        text=text,
    )


def test_conversation_html_keeps_full_thread_text_and_media():
    ancestors = [_tweet(f"ancestor-{index}") for index in range(5)]
    target = _tweet("target", "x" * 2500)
    target.media = [
        TweetMedia(
            url="https://pbs.twimg.com/media/photo?format=jpg&name=large",
            type="photo",
        ),
        TweetMedia(
            url="https://video.twimg.com/video.mp4",
            thumbnail_url="https://pbs.twimg.com/tweet_video_thumb/poster.jpg",
            type="video",
        ),
    ]

    html = engine._render_conversation_html(
        TweetConversation(ancestors=ancestors, target=target)
    )

    assert "ancestor-0" in html
    assert "ancestor-4" in html
    assert "x" * 2500 in html
    assert "name=large" in html
    assert "tweet_video_thumb/poster.jpg" in html
    assert "video.twimg.com/video.mp4" not in html
    assert "视频（请查看原文）" not in html


def test_conversation_html_uses_placeholder_without_video_thumbnail():
    target = _tweet("target")
    target.media = [
        TweetMedia(url="https://video.twimg.com/video.mp4", type="video")
    ]

    html = engine._render_conversation_html(TweetConversation(target=target))

    assert "视频（请查看原文）" in html
    assert "video.twimg.com/video.mp4" not in html


class _FakePage:
    def __init__(self):
        self.content_kwargs = None
        self.screenshot_kwargs = None
        self.closed = False

    async def route(self, *_args):
        return None

    async def set_content(self, _html, **kwargs):
        self.content_kwargs = kwargs

    async def screenshot(self, **kwargs):
        self.screenshot_kwargs = kwargs

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, page):
        self.page = page

    async def new_page(self, **_kwargs):
        return self.page


@pytest.mark.asyncio
async def test_render_waits_for_networkidle_and_captures_full_page(monkeypatch, tmp_path):
    page = _FakePage()
    browser = _FakeBrowser(page)

    async def get_browser():
        return browser

    monkeypatch.setattr(engine, "_get_browser", get_browser)

    await engine._render_once("<html></html>", tmp_path / "card.png", 650)

    assert page.content_kwargs == {
        "wait_until": "networkidle",
        "timeout": engine.XFETCH_RENDER_TIMEOUT * 1000,
    }
    assert page.screenshot_kwargs["full_page"] is True
    assert page.closed is True

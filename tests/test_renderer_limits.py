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


def test_twitter_media_url_is_bounded_without_changing_other_hosts():
    source = "https://pbs.twimg.com/media/card.jpg?format=jpg&name=orig"

    bounded = engine._bounded_image_url(source)

    assert "format=jpg" in bounded
    assert "name=medium" in bounded
    assert "name=orig" not in bounded
    assert (
        engine._bounded_image_url("https://example.com/image.jpg?name=orig")
        == "https://example.com/image.jpg?name=orig"
    )


class _FakeRequest:
    def __init__(self, resource_type="image"):
        self.resource_type = resource_type
        self.url = "https://pbs.twimg.com/media/card.jpg?name=orig"


class _FakeResponse:
    def __init__(self, *, content_length="1024"):
        self.ok = True
        self.status = 200
        self.headers = {
            "content-type": "image/jpeg",
            "content-length": content_length,
        }
        self.disposed = False

    async def dispose(self):
        self.disposed = True


class _FakeRoute:
    def __init__(self, *, response=None, error=None, resource_type="image"):
        self.request = _FakeRequest(resource_type)
        self.response = response
        self.error = error
        self.fetch_kwargs = None
        self.fulfill_kwargs = None
        self.aborted = False

    async def fetch(self, **kwargs):
        self.fetch_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response

    async def fulfill(self, **kwargs):
        self.fulfill_kwargs = kwargs

    async def abort(self):
        self.aborted = True


@pytest.mark.asyncio
async def test_remote_image_uses_timeout_and_disposes_response():
    response = _FakeResponse()
    route = _FakeRoute(response=response)

    await engine._allow_card_image_only(route)

    assert route.fetch_kwargs["timeout"] == engine.XFETCH_RENDER_IMAGE_TIMEOUT * 1000
    assert "name=medium" in route.fetch_kwargs["url"]
    assert route.fulfill_kwargs == {"response": response}
    assert response.disposed is True


@pytest.mark.asyncio
async def test_remote_image_failure_degrades_to_transparent_png():
    route = _FakeRoute(error=TimeoutError("remote image stalled"))

    await engine._allow_card_image_only(route)

    assert route.fulfill_kwargs["content_type"] == "image/png"
    assert route.fulfill_kwargs["body"] == engine._TRANSPARENT_PNG


@pytest.mark.asyncio
async def test_oversized_remote_image_degrades_and_is_disposed():
    response = _FakeResponse(
        content_length=str(engine.XFETCH_RENDER_IMAGE_MAX_BYTES + 1)
    )
    route = _FakeRoute(response=response)

    await engine._allow_card_image_only(route)

    assert route.fulfill_kwargs["content_type"] == "image/png"
    assert response.disposed is True


@pytest.mark.asyncio
async def test_non_image_remote_resource_is_aborted():
    route = _FakeRoute(resource_type="font")

    await engine._allow_card_image_only(route)

    assert route.aborted is True
    assert route.fetch_kwargs is None


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
async def test_render_waits_for_networkidle_and_captures_full_page(
    monkeypatch, tmp_path
):
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

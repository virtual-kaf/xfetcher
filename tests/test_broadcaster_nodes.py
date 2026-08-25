from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11 import MessageSegment

from nonebot_plugin_xfetch.models.group import GroupConfig
from nonebot_plugin_xfetch.models.tweet import (
    TweetAuthor,
    TweetConversation,
    TweetItem,
)
from nonebot_plugin_xfetch.services import broadcaster


def _conversation(tweet_id: str, *, reply: bool = False, quote: bool = False):
    target = TweetItem(
        id=tweet_id,
        author=TweetAuthor(screen_name="virtual_kaf"),
        text="short reply" if reply else "regular post",
        is_reply=reply,
    )
    quoted = TweetItem(id=f"{tweet_id}-quote") if quote else None
    return TweetConversation(target=target, quote=quoted)


class _FakeBot:
    self_id = "12345"

    def __init__(self, group_ids=None):
        self.calls = []
        self.group_ids = group_ids or [100]

    async def get_group_list(self):
        return [{"group_id": group_id} for group_id in self.group_ids]

    async def call_api(self, api, **kwargs):
        self.calls.append((api, kwargs))


class _ForwardFailingBot(_FakeBot):
    async def call_api(self, api, **kwargs):
        self.calls.append((api, kwargs))
        if api == "send_group_forward_msg":
            raise RuntimeError("forward unavailable")


def _base64_segment(_path):
    return MessageSegment.image(b"PNG")


def _base64_content(_path):
    return str(_base64_segment(_path))


def test_water_post_classification():
    assert broadcaster._is_water_post(_conversation("1", reply=True)) is True
    assert broadcaster._is_water_post(_conversation("2")) is False
    assert broadcaster._is_water_post(
        _conversation("3", reply=True, quote=True)
    ) is False
    long_reply = _conversation("4", reply=True)
    long_reply.target.text = "x" * 101
    assert broadcaster._is_water_post(long_reply) is False


def test_merge_nodes_embed_base64_images(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    nodes = broadcaster._build_image_nodes([
        (_conversation("1"), [first], "virtual_kaf", False),
        (_conversation("2"), [second], "virtual_kaf", False),
    ], "12345")

    assert len(nodes) == 2
    for node in nodes:
        content = node["data"]["content"]
        assert "base64://" in content
        assert "file:///" not in content


def _prepare(monkeypatch, *, filter_water: bool):
    async def render(conv):
        return [Path(f"{conv.target.id}.png")]

    monkeypatch.setattr(broadcaster, "render_conversation_card", render)
    monkeypatch.setattr(broadcaster, "image_segment_from_path", _base64_segment)
    monkeypatch.setattr(broadcaster, "image_cq_from_path", _base64_content)
    monkeypatch.setattr(
        broadcaster,
        "get_all_group_configs",
        lambda: [GroupConfig(
            group_id="100",
            filter_water=filter_water,
        )],
    )
    monkeypatch.setattr(broadcaster, "is_master_on", lambda _gid: True)


@pytest.mark.asyncio
async def test_single_non_water_post_sends_base64_image(monkeypatch):
    _prepare(monkeypatch, filter_water=True)
    bot = _FakeBot()

    await broadcaster.broadcast_to_groups(bot, [_conversation("1")])

    assert [call[0] for call in bot.calls] == ["send_group_msg"]
    image = bot.calls[0][1]["message"]
    assert image.type == "image"
    assert image.data["file"].startswith("base64://")


@pytest.mark.asyncio
async def test_multiple_posts_use_base64_merge_forward(monkeypatch):
    _prepare(monkeypatch, filter_water=True)
    bot = _FakeBot()

    await broadcaster.broadcast_to_groups(
        bot,
        [_conversation("1"), _conversation("2")],
    )

    assert [call[0] for call in bot.calls] == ["send_group_forward_msg"]
    for node in bot.calls[0][1]["messages"]:
        assert "base64://" in node["data"]["content"]
        assert "file:///" not in node["data"]["content"]


@pytest.mark.asyncio
async def test_merge_fallback_reuses_base64_content(monkeypatch):
    _prepare(monkeypatch, filter_water=True)
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(broadcaster.asyncio, "sleep", no_sleep)
    bot = _ForwardFailingBot()

    await broadcaster.broadcast_to_groups(
        bot,
        [_conversation("1"), _conversation("2")],
    )

    assert [call[0] for call in bot.calls] == [
        "send_group_forward_msg",
        "send_group_msg",
        "send_group_msg",
    ]
    for _api, kwargs in bot.calls[1:]:
        assert "base64://" in kwargs["message"]
        assert "file:///" not in kwargs["message"]


@pytest.mark.asyncio
async def test_single_allowed_water_post_stays_merged(monkeypatch):
    _prepare(monkeypatch, filter_water=False)
    bot = _FakeBot()

    await broadcaster.broadcast_to_groups(
        bot,
        [_conversation("1", reply=True)],
    )

    assert [call[0] for call in bot.calls] == ["send_group_forward_msg"]


@pytest.mark.asyncio
async def test_filtered_water_post_sends_nothing(monkeypatch):
    _prepare(monkeypatch, filter_water=True)
    bot = _FakeBot()

    await broadcaster.broadcast_to_groups(
        bot,
        [_conversation("1", reply=True)],
    )

    assert bot.calls == []


@pytest.mark.asyncio
async def test_broadcast_waits_three_to_five_seconds_between_target_groups(
    monkeypatch,
):
    _prepare(monkeypatch, filter_water=True)
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(
        broadcaster,
        "get_all_group_configs",
        lambda: [
            GroupConfig(group_id="100"),
            GroupConfig(group_id="200"),
            GroupConfig(group_id="300"),
        ],
    )
    monkeypatch.setattr(broadcaster.random, "uniform", lambda low, high: 4.0)
    monkeypatch.setattr(broadcaster.asyncio, "sleep", fake_sleep)
    bot = _FakeBot([100, 200, 300])

    await broadcaster.broadcast_to_groups(bot, [_conversation("1")])

    assert [call[1]["group_id"] for call in bot.calls] == [100, 200, 300]
    assert delays == [4.0, 4.0]

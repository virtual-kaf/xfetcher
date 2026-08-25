from datetime import datetime, timedelta, timezone

import pytest

from nonebot_plugin_xfetch.config import MAX_POST_AGE
from nonebot_plugin_xfetch.core import tweet_pipeline
from nonebot_plugin_xfetch.models.tweet import TweetConversation, TweetItem


def test_fxtwitter_jst_timestamp_respects_max_post_age():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
    at_limit = tweet_pipeline._parse_tweet_created_at(
        "2026-08-21 15:00:00 JST"
    )
    too_old = tweet_pipeline._parse_tweet_created_at(
        "2026-08-21 14:59:59 JST"
    )

    assert at_limit is not None
    assert too_old is not None
    assert now - at_limit == MAX_POST_AGE
    assert now - too_old == MAX_POST_AGE + timedelta(seconds=1)


def test_invalid_fxtwitter_timestamp_is_rejected():
    assert tweet_pipeline._parse_tweet_created_at("not-a-time") is None


@pytest.mark.asyncio
async def test_pipeline_drops_old_tweet_before_translation(monkeypatch):
    old_time = datetime.now(timezone.utc) - MAX_POST_AGE - timedelta(minutes=1)
    conversation = TweetConversation(target=TweetItem(
        id="123456789",
        created_at=old_time.strftime("%Y-%m-%d %H:%M:%S +0000"),
        text="old post",
    ))
    translated = False

    async def fake_discover(_members):
        return [{
            "member": "Alpha",
            "url": "https://x.com/Alpha/status/123456789",
        }]

    def fake_fetch(_tweet_id):
        return conversation

    async def forbidden_translate(_items):
        nonlocal translated
        translated = True
        raise AssertionError("old tweets must not reach translation")

    monkeypatch.setattr(tweet_pipeline, "discover_tweet_urls", fake_discover)
    monkeypatch.setattr(tweet_pipeline, "is_duplicate", lambda *_args: False)
    monkeypatch.setattr(tweet_pipeline, "mark_sent", lambda *_args: None)
    monkeypatch.setattr(tweet_pipeline, "fetch_conversation", fake_fetch)
    monkeypatch.setattr(
        tweet_pipeline,
        "translate_and_review_batch",
        forbidden_translate,
    )

    result = await tweet_pipeline.run_tweet_pipeline(["Alpha"])

    assert result == []
    assert translated is False

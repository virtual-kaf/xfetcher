import pytest
from nonebot_plugin_xfetch.clients import discovery, remote_grok


def _entry(member: str, tweet_id: str) -> dict[str, str]:
    return {
        "member": member,
        "url": f"https://x.com/{member}/status/{tweet_id}",
    }


@pytest.mark.asyncio
async def test_remote_grok_success_does_not_call_fallback(monkeypatch):
    fallback_calls = 0

    async def fake_remote(members):
        assert members == ["Alpha", "Beta"]
        return [_entry("Alpha", "101")]

    async def fake_fallback(_members):
        nonlocal fallback_calls
        fallback_calls += 1
        return []

    monkeypatch.setattr(remote_grok, "remote_fetch_urls", fake_remote)
    monkeypatch.setattr(
        discovery.twitterapi_io,
        "twitterapi_fetch_urls",
        fake_fallback,
    )

    result = await discovery.discover_tweet_urls(["Alpha", "Beta"])

    assert result == [_entry("Alpha", "101")]
    assert fallback_calls == 0


@pytest.mark.asyncio
async def test_partial_remote_result_does_not_supplement_missing_members(
    monkeypatch,
):
    async def fake_remote(_members):
        return [_entry("Alpha", "101")]

    async def forbidden_fallback(_members):
        raise AssertionError("partial remote results must not trigger fallback")

    monkeypatch.setattr(remote_grok, "remote_fetch_urls", fake_remote)
    monkeypatch.setattr(
        discovery.twitterapi_io,
        "twitterapi_fetch_urls",
        forbidden_fallback,
    )

    result = await discovery.discover_tweet_urls(["Alpha", "Beta"])

    assert result == [_entry("Alpha", "101")]


@pytest.mark.asyncio
async def test_valid_empty_remote_result_does_not_call_fallback(monkeypatch):
    async def fake_remote(_members):
        return []

    async def forbidden_fallback(_members):
        raise AssertionError("a valid empty remote result must not trigger fallback")

    monkeypatch.setattr(remote_grok, "remote_fetch_urls", fake_remote)
    monkeypatch.setattr(
        discovery.twitterapi_io,
        "twitterapi_fetch_urls",
        forbidden_fallback,
    )

    assert await discovery.discover_tweet_urls(["Alpha"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        remote_grok.RemoteDiscoveryError("http_502", 502),
        remote_grok.RemoteDiscoveryError("remote_request_failed"),
        remote_grok.RemoteDiscoveryError("invalid_remote_response"),
        RuntimeError("unexpected remote client failure"),
    ],
)
async def test_remote_failures_trigger_fallback_with_same_members(
    monkeypatch,
    error,
):
    fallback_calls = []

    async def failed_remote(_members):
        raise error

    async def fake_fallback(members):
        fallback_calls.append(members)
        return [_entry("Alpha", "303")]

    monkeypatch.setattr(remote_grok, "remote_fetch_urls", failed_remote)
    monkeypatch.setattr(
        discovery.twitterapi_io,
        "twitterapi_fetch_urls",
        fake_fallback,
    )

    members = ["Alpha"]
    result = await discovery.discover_tweet_urls(members)

    assert result == [_entry("Alpha", "303")]
    assert fallback_calls == [members]


def test_remote_normalization_uses_configured_handle_and_deduplicates():
    result = discovery._normalize_entries([
        {
            "member": "@alpha",
            "url": "https://x.com/alpha/status/101",
        },
        _entry("Alpha", "101"),
        _entry("ALPHA", "102"),
        _entry("Alpha", "103"),
        _entry("Beta", "104"),
    ], ["Alpha"])

    assert result == [
        _entry("Alpha", "101"),
        _entry("Alpha", "102"),
    ]

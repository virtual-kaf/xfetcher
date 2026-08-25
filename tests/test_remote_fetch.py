import httpx
import pytest
from nonebot_plugin_xfetch import remote_fetch
from nonebot_plugin_xfetch.clients.grok import GrokDiscoveryError


def test_requested_members_are_normalized_and_deduplicated():
    assert remote_fetch.normalize_requested_members([
        " @Alpha ",
        "alpha",
        "Beta_2",
    ]) == ["Alpha", "Beta_2"]


@pytest.mark.parametrize(
    "members",
    [
        None,
        [],
        "Alpha",
        ["bad-handle"],
        [123],
        [f"member{index}" for index in range(31)],
    ],
)
def test_invalid_requested_members_are_rejected(members):
    with pytest.raises(remote_fetch.InvalidMembersError):
        remote_fetch.normalize_requested_members(members)


@pytest.mark.asyncio
async def test_fetch_latest_urls_uses_grok_and_canonicalizes_urls(monkeypatch):
    calls = []

    async def fake_grok(members):
        calls.append(members)
        return [
            {
                "member": "Alpha",
                "url": "https://twitter.com/Alpha/status/101?ref=test",
            },
            {
                "member": "alpha",
                "url": "https://x.com/alpha/status/101",
            },
            {
                "member": "Beta",
                "url": "https://fxtwitter.com/Beta/status/202",
            },
        ]

    monkeypatch.setattr(remote_fetch, "grok_fetch_urls", fake_grok)

    result = await remote_fetch.fetch_latest_urls(["@Alpha", "Beta"])

    assert calls == [["Alpha", "Beta"]]
    assert result == [
        "https://x.com/Alpha/status/101",
        "https://x.com/Beta/status/202",
    ]


@pytest.mark.asyncio
async def test_valid_empty_grok_result_returns_empty_urls(monkeypatch):
    async def fake_grok(_members):
        return []

    monkeypatch.setattr(remote_fetch, "grok_fetch_urls", fake_grok)

    assert await remote_fetch.fetch_latest_urls(["Alpha"]) == []


@pytest.mark.asyncio
async def test_invalid_grok_url_becomes_typed_upstream_failure(monkeypatch):
    async def fake_grok(_members):
        return [{"member": "Alpha", "url": "not-a-tweet-url"}]

    monkeypatch.setattr(remote_fetch, "grok_fetch_urls", fake_grok)

    with pytest.raises(GrokDiscoveryError) as error:
        await remote_fetch.fetch_latest_urls(["Alpha"])

    assert error.value.reason == "grok_failed"


@pytest.mark.asyncio
async def test_grok_network_error_becomes_typed_upstream_failure(monkeypatch):
    async def fake_grok(_members):
        request = httpx.Request("POST", "https://grok.test")
        raise httpx.ConnectError("unavailable", request=request)

    monkeypatch.setattr(remote_fetch, "grok_fetch_urls", fake_grok)

    with pytest.raises(GrokDiscoveryError) as error:
        await remote_fetch.fetch_latest_urls(["Alpha"])

    assert error.value.reason == "grok_failed"


@pytest.mark.asyncio
async def test_twenty_members_thirty_eight_raw_urls_keep_valid_subset_and_report(
    monkeypatch,
):
    members = [f"member{index:02d}" for index in range(20)]
    raw_entries = []
    for member_index in range(17):
        for url_index in range(2):
            tweet_id = 100_000 + member_index * 10 + url_index
            raw_entries.append({
                "member": members[member_index],
                "url": (
                    f"https://x.com/{members[member_index]}/status/{tweet_id}"
                ),
            })
    raw_entries.append({
        "member": members[17],
        "url": f"https://x.com/{members[17]}/status/200000",
    })
    raw_entries.extend([
        {
            "member": members[0],
            "url": f"https://x.com/{members[0]}/status/300000",
        },
        dict(raw_entries[2]),
        {
            "member": members[18],
            "url": f"https://x.com/{members[19]}/status/400000",
        },
    ])
    assert len(raw_entries) == 38

    async def fake_grok(received_members):
        assert received_members == members
        return raw_entries

    messages = []

    class CaptureLogger:
        def info(self, message):
            messages.append(message)

        def warning(self, message):
            messages.append(message)

    monkeypatch.setattr(remote_fetch, "grok_fetch_urls", fake_grok)
    monkeypatch.setattr(remote_fetch, "logger", CaptureLogger())

    result = await remote_fetch.fetch_latest_urls(members)

    assert len(result) == 35
    summary = next(
        message for message in messages
        if "rejected_entries=3" in message
    )
    assert "duplicate_tweet_id=1" in summary
    assert "member_url_limit=1" in summary
    assert "url_handle_mismatch=1" in summary
    assert any(
        "reason=duplicate_tweet_id" in message
        and raw_entries[36]["url"] in message
        for message in messages
    )
    assert any(
        "reason=member_url_limit" in message
        and raw_entries[35]["url"] in message
        for message in messages
    )
    assert any(
        "reason=url_handle_mismatch" in message
        and raw_entries[37]["url"] in message
        for message in messages
    )

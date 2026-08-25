import re
from datetime import datetime, timezone

import httpx
import pytest
from nonebot_plugin_xfetch.clients import twitterapi_io


class _FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _page(tweets=None, *, has_next=False, cursor=""):
    return _FakeResponse(body={
        "tweets": tweets or [],
        "has_next_page": has_next,
        "next_cursor": cursor,
    })


def _tweet(tweet_id, author):
    return {
        "id": tweet_id,
        "author": {"userName": author},
    }


def _install_client(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(twitterapi_io, "TWITTERAPI_IO_API_KEY", "test-key")
    monkeypatch.setattr(
        twitterapi_io.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )
    return client


@pytest.mark.asyncio
async def test_twenty_members_are_split_into_eight_eight_four(monkeypatch):
    client = _install_client(monkeypatch, [_page(), _page(), _page()])
    members = [f"member{index}" for index in range(20)]
    before = int(datetime.now(timezone.utc).timestamp())

    result = await twitterapi_io.twitterapi_fetch_urls(members)

    after = int(datetime.now(timezone.utc).timestamp())
    assert result == []
    assert len(client.calls) == 3
    expected_batches = [members[:8], members[8:16], members[16:]]
    for (_, request), expected_members in zip(
        client.calls,
        expected_batches,
    ):
        assert request["headers"] == {"X-API-Key": "test-key"}
        assert request["params"]["queryType"] == "Latest"
        assert "cursor" not in request["params"]
        query = request["params"]["query"]
        for member in expected_members:
            assert f"from:{member}" in query
        since_match = re.search(r"since_time:(\d+)", query)
        until_match = re.search(r"until_time:(\d+)", query)
        assert since_match and until_match
        since = int(since_match.group(1))
        until = int(until_match.group(1))
        assert until - since == 60 * 60
        assert before <= until <= after


@pytest.mark.asyncio
async def test_pagination_filters_caps_and_deduplicates(monkeypatch):
    client = _install_client(monkeypatch, [
        _page([
            _tweet("101", "aLpHa"),
            _tweet("101", "Alpha"),
            _tweet("bad-id", "Alpha"),
            _tweet("999", "Unknown"),
            _tweet("201", "Beta"),
        ], has_next=True, cursor="page-2"),
        _page([
            _tweet("102", "ALPHA"),
            _tweet("202", "bEtA"),
            _tweet("103", "Alpha"),
            _tweet("203", "Beta"),
        ], has_next=True, cursor="unused-page"),
    ])

    result = await twitterapi_io.twitterapi_fetch_urls(["Alpha", "Beta"])

    assert result == [
        {"member": "Alpha", "url": "https://x.com/Alpha/status/101"},
        {"member": "Beta", "url": "https://x.com/Beta/status/201"},
        {"member": "Alpha", "url": "https://x.com/Alpha/status/102"},
        {"member": "Beta", "url": "https://x.com/Beta/status/202"},
    ]
    assert len(client.calls) == 2
    assert client.calls[1][1]["params"]["cursor"] == "page-2"


@pytest.mark.asyncio
async def test_pagination_stops_at_three_pages(monkeypatch):
    client = _install_client(monkeypatch, [
        _page(has_next=True, cursor="page-2"),
        _page(has_next=True, cursor="page-3"),
        _page(has_next=True, cursor="page-4"),
    ])

    result = await twitterapi_io.twitterapi_fetch_urls(["Alpha"])

    assert result == []
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_429_honors_retry_after(monkeypatch):
    client = _install_client(monkeypatch, [
        _FakeResponse(status_code=429, headers={"Retry-After": "7"}),
        _page([_tweet("101", "Alpha")]),
    ])
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(twitterapi_io.asyncio, "sleep", fake_sleep)

    result = await twitterapi_io.twitterapi_fetch_urls(["Alpha"])

    assert len(client.calls) == 2
    assert sleeps == [7.0]
    assert result[0]["url"].endswith("/101")


@pytest.mark.asyncio
async def test_5xx_and_network_errors_retry_with_backoff(monkeypatch):
    request = httpx.Request("GET", "https://api.test")
    client = _install_client(monkeypatch, [
        _FakeResponse(status_code=503),
        httpx.ConnectError("offline", request=request),
        _page([_tweet("101", "Alpha")]),
    ])
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(twitterapi_io.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(twitterapi_io.random, "uniform", lambda *_args: 0.0)

    result = await twitterapi_io.twitterapi_fetch_urls(["Alpha"])

    assert len(client.calls) == 3
    assert sleeps == [1.0, 2.0]
    assert len(result) == 1


@pytest.mark.asyncio
async def test_deterministic_4xx_does_not_retry_and_other_batch_survives(
    monkeypatch,
):
    members = [f"member{index}" for index in range(9)]
    client = _install_client(monkeypatch, [
        _FakeResponse(status_code=403),
        _page([_tweet("901", "MEMBER8")]),
    ])

    result = await twitterapi_io.twitterapi_fetch_urls(members)

    assert len(client.calls) == 2
    assert result == [{
        "member": "member8",
        "url": "https://x.com/member8/status/901",
    }]


@pytest.mark.asyncio
async def test_missing_key_returns_without_creating_client(monkeypatch):
    monkeypatch.setattr(twitterapi_io, "TWITTERAPI_IO_API_KEY", "")

    def forbidden_client(**_kwargs):
        raise AssertionError("client should not be created without an API key")

    monkeypatch.setattr(twitterapi_io.httpx, "AsyncClient", forbidden_client)

    result = await twitterapi_io.twitterapi_fetch_urls(["Alpha"])

    assert result == []

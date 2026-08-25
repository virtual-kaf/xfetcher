import httpx
import pytest
from nonebot_plugin_xfetch.clients import remote_grok

TOKEN = "test-remote-token"


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeClient:
    def __init__(self, calls, response=None, error=None, **options):
        self.calls = calls
        self.response = response
        self.error = error
        calls["client_options"] = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, headers, json):
        self.calls["request"] = {
            "url": url,
            "headers": headers,
            "json": json,
        }
        if self.error is not None:
            raise self.error
        return self.response


def _configure(monkeypatch):
    monkeypatch.setattr(remote_grok, "XFETCH_REMOTE_ENABLED", "true")
    monkeypatch.setattr(remote_grok, "XFETCH_REMOTE_HOST", "100.98.44.83")
    monkeypatch.setattr(remote_grok, "XFETCH_REMOTE_PORT", "8765")
    monkeypatch.setattr(remote_grok, "XFETCH_REMOTE_TOKEN", TOKEN)


def _install_client(monkeypatch, *, response=None, error=None):
    calls = {}

    def create_client(**options):
        return FakeClient(
            calls,
            response=response,
            error=error,
            **options,
        )

    monkeypatch.setattr(remote_grok.httpx, "AsyncClient", create_client)
    return calls


@pytest.mark.asyncio
async def test_success_posts_members_with_bearer_and_without_proxy(monkeypatch):
    _configure(monkeypatch)
    calls = _install_client(
        monkeypatch,
        response=FakeResponse(body={
            "ok": True,
            "urls": [
                "https://x.com/Alpha/status/101",
                "https://x.com/Beta/status/202",
            ],
            "source": "grok",
            "error": None,
        }),
    )

    members = ["@Alpha", "Beta"]
    result = await remote_grok.remote_fetch_urls(members)

    assert result == [
        {"member": "Alpha", "url": "https://x.com/Alpha/status/101"},
        {"member": "Beta", "url": "https://x.com/Beta/status/202"},
    ]
    assert calls["request"] == {
        "url": "http://100.98.44.83:8765/api/xfetch/poll",
        "headers": {"Authorization": f"Bearer {TOKEN}"},
        "json": {"members": members},
    }
    assert calls["client_options"] == {
        "timeout": remote_grok.REQUEST_TIMEOUT,
        "trust_env": False,
    }


@pytest.mark.asyncio
async def test_successful_empty_url_list_is_valid(monkeypatch):
    _configure(monkeypatch)
    _install_client(
        monkeypatch,
        response=FakeResponse(body={"ok": True, "urls": []}),
    )

    assert await remote_grok.remote_fetch_urls(["Alpha"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError(
            "connection failed",
            request=httpx.Request("POST", "http://remote.test"),
        ),
        httpx.ReadTimeout(
            "timed out",
            request=httpx.Request("POST", "http://remote.test"),
        ),
    ],
)
async def test_network_failures_become_remote_discovery_errors(
    monkeypatch,
    error,
):
    _configure(monkeypatch)
    _install_client(monkeypatch, error=error)

    with pytest.raises(remote_grok.RemoteDiscoveryError) as caught:
        await remote_grok.remote_fetch_urls(["Alpha"])

    assert caught.value.reason == "remote_request_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        (FakeResponse(201, {"ok": True, "urls": []}), "http_201"),
        (FakeResponse(502, {"ok": False, "urls": []}), "http_502"),
        (FakeResponse(body={"ok": False, "urls": [], "error": "grok_failed"}), "grok_failed"),
        (FakeResponse(body=ValueError("invalid json")), "invalid_remote_response"),
        (FakeResponse(body=None), "invalid_remote_response"),
        (FakeResponse(body={"ok": True}), "invalid_remote_response"),
        (FakeResponse(body={"ok": True, "urls": "not-a-list"}), "invalid_remote_response"),
        (FakeResponse(body={"ok": True, "urls": [123]}), "invalid_remote_response"),
        (FakeResponse(body={"ok": True, "urls": ["not-a-tweet-url"]}), "invalid_remote_response"),
        (
            FakeResponse(body={
                "ok": True,
                "urls": ["https://x.com/Unknown/status/101"],
            }),
            "invalid_remote_response",
        ),
    ],
)
async def test_invalid_http_or_response_envelope_raises(
    monkeypatch,
    response,
    expected_reason,
):
    _configure(monkeypatch)
    _install_client(monkeypatch, response=response)

    with pytest.raises(remote_grok.RemoteDiscoveryError) as caught:
        await remote_grok.remote_fetch_urls(["Alpha"])

    assert caught.value.reason == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "value", "expected_reason"),
    [
        ("XFETCH_REMOTE_ENABLED", "false", "remote_disabled"),
        ("XFETCH_REMOTE_ENABLED", "maybe", "invalid_remote_config"),
        ("XFETCH_REMOTE_HOST", "", "invalid_remote_config"),
        ("XFETCH_REMOTE_HOST", "not-an-ip", "invalid_remote_config"),
        ("XFETCH_REMOTE_PORT", "0", "invalid_remote_config"),
        ("XFETCH_REMOTE_PORT", "not-a-port", "invalid_remote_config"),
        ("XFETCH_REMOTE_PORT", "65536", "invalid_remote_config"),
        ("XFETCH_REMOTE_TOKEN", "", "invalid_remote_config"),
    ],
)
async def test_invalid_remote_config_raises_before_request(
    monkeypatch,
    setting,
    value,
    expected_reason,
):
    _configure(monkeypatch)
    monkeypatch.setattr(remote_grok, setting, value)

    def forbidden_client(**_options):
        raise AssertionError("invalid config must not create an HTTP client")

    monkeypatch.setattr(remote_grok.httpx, "AsyncClient", forbidden_client)

    with pytest.raises(remote_grok.RemoteDiscoveryError) as caught:
        await remote_grok.remote_fetch_urls(["Alpha"])

    assert caught.value.reason == expected_reason

import subprocess
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer
from nonebot_plugin_xfetch.clients.grok import GrokDiscoveryError
from nonebot_plugin_xfetch.remote_fetch import normalize_requested_members
from nonebot_plugin_xfetch.remote_service import (
    POLL_PATH,
    RemoteServiceConfigError,
    RemoteServiceSettings,
    create_app,
    load_remote_settings,
)

TOKEN = "test-remote-token"


def _settings() -> RemoteServiceSettings:
    return RemoteServiceSettings(
        enabled=True,
        host="127.0.0.1",
        port=8765,
        token=TOKEN,
    )


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_poll_success_returns_full_envelope():
    received = []

    async def fake_fetcher(members):
        received.append(members)
        return ["https://x.com/Alpha/status/101"]

    async with TestClient(
        TestServer(create_app(_settings(), fetcher=fake_fetcher))
    ) as client:
        response = await client.post(
            POLL_PATH,
            headers=_auth(),
            json={"members": ["Alpha"]},
        )

        assert response.status == 200
        assert await response.json() == {
            "ok": True,
            "urls": ["https://x.com/Alpha/status/101"],
            "source": "grok",
            "error": None,
        }
        assert received == [["Alpha"]]


@pytest.mark.asyncio
async def test_poll_valid_empty_result_returns_200():
    async def fake_fetcher(_members):
        return []

    async with TestClient(
        TestServer(create_app(_settings(), fetcher=fake_fetcher))
    ) as client:
        response = await client.post(
            POLL_PATH,
            headers=_auth(),
            json={"members": ["Alpha"]},
        )

        assert response.status == 200
        assert await response.json() == {
            "ok": True,
            "urls": [],
            "source": "grok",
            "error": None,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic test"])
async def test_poll_rejects_missing_or_wrong_bearer_token(authorization):
    calls = 0

    async def forbidden_fetcher(_members):
        nonlocal calls
        calls += 1
        return []

    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization

    async with TestClient(
        TestServer(create_app(_settings(), fetcher=forbidden_fetcher))
    ) as client:
        response = await client.post(
            POLL_PATH,
            headers=headers,
            json={"members": ["Alpha"]},
        )

        assert response.status == 401
        assert (await response.json())["error"] == "unauthorized"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"members": []},
        {"members": "Alpha"},
        {"members": ["bad-handle"]},
        {"members": [f"member{index}" for index in range(31)]},
    ],
)
async def test_poll_rejects_invalid_requests(body):
    async def validating_fetcher(members):
        normalize_requested_members(members)
        return []

    async with TestClient(
        TestServer(create_app(_settings(), fetcher=validating_fetcher))
    ) as client:
        response = await client.post(POLL_PATH, headers=_auth(), json=body)

        assert response.status == 400
        assert await response.json() == {
            "ok": False,
            "urls": [],
            "source": None,
            "error": "invalid_request",
        }


@pytest.mark.asyncio
async def test_poll_maps_grok_failure_to_502():
    async def failed_fetcher(_members):
        raise GrokDiscoveryError("http_503", status_code=503)

    async with TestClient(
        TestServer(create_app(_settings(), fetcher=failed_fetcher))
    ) as client:
        response = await client.post(
            POLL_PATH,
            headers=_auth(),
            json={"members": ["Alpha"]},
        )

        assert response.status == 502
        assert await response.json() == {
            "ok": False,
            "urls": [],
            "source": None,
            "error": "grok_failed",
        }


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": "false", "host": "127.0.0.1", "port": "8765", "token": TOKEN},
        {"enabled": "maybe", "host": "127.0.0.1", "port": "8765", "token": TOKEN},
        {"enabled": "true", "host": "0.0.0.0.example", "port": "8765", "token": TOKEN},
        {"enabled": "true", "host": "127.0.0.1", "port": "0", "token": TOKEN},
        {"enabled": "true", "host": "127.0.0.1", "port": "nope", "token": TOKEN},
        {"enabled": "true", "host": "127.0.0.1", "port": "8765", "token": ""},
    ],
)
def test_invalid_or_disabled_settings_are_rejected(values):
    with pytest.raises(RemoteServiceConfigError):
        RemoteServiceSettings.from_raw(**values)


def test_valid_settings_are_parsed():
    settings = RemoteServiceSettings.from_raw(
        enabled="true",
        host="100.98.44.83",
        port="8765",
        token=f" {TOKEN} ",
    )

    assert settings == RemoteServiceSettings(
        enabled=True,
        host="100.98.44.83",
        port=8765,
        token=TOKEN,
    )


def test_process_environment_config_is_loaded(monkeypatch):
    monkeypatch.setenv("KABUBU_XFETCH_REMOTE_ENABLED", "true")
    monkeypatch.setenv("KABUBU_XFETCH_REMOTE_HOST", "127.0.0.1")
    monkeypatch.setenv("KABUBU_XFETCH_REMOTE_PORT", "18765")
    monkeypatch.setenv("KABUBU_XFETCH_REMOTE_TOKEN", TOKEN)

    assert load_remote_settings() == RemoteServiceSettings(
        enabled=True,
        host="127.0.0.1",
        port=18765,
        token=TOKEN,
    )


def test_project_dotenv_config_is_loaded(monkeypatch, tmp_path):
    for name in (
        "KABUBU_XFETCH_REMOTE_ENABLED",
        "KABUBU_XFETCH_REMOTE_HOST",
        "KABUBU_XFETCH_REMOTE_PORT",
        "KABUBU_XFETCH_REMOTE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join([
            "ENVIRONMENT=dev",
            "KABUBU_XFETCH_REMOTE_ENABLED=true",
            "KABUBU_XFETCH_REMOTE_HOST=127.0.0.1",
            "KABUBU_XFETCH_REMOTE_PORT=18766",
            f"KABUBU_XFETCH_REMOTE_TOKEN={TOKEN}",
        ]),
        encoding="utf-8",
    )

    assert load_remote_settings() == RemoteServiceSettings(
        enabled=True,
        host="127.0.0.1",
        port=18766,
        token=TOKEN,
    )


def test_standalone_import_does_not_load_scheduler_qq_or_twitterapi():
    script = """
import sys
from nonebot_plugin_xfetch.remote_service import (
    RemoteServiceSettings,
    create_app,
)
create_app(RemoteServiceSettings(
    enabled=True,
    host='127.0.0.1',
    port=8765,
    token='test',
))
blocked = [
    'nonebot_plugin_xfetch.scheduler',
    'nonebot_plugin_xfetch.services.broadcaster',
    'nonebot_plugin_xfetch.storage.database',
    'nonebot_plugin_xfetch.clients.twitterapi_io',
]
print(','.join(name for name in blocked if name in sys.modules))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == ""

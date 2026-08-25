import json

import pytest
from nonebot_plugin_xfetch.clients import grok


class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return self.response


def _install_client(monkeypatch, response):
    monkeypatch.setattr(
        grok.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeClient(response),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [403, 429, 500])
async def test_non_200_raises_typed_discovery_error(
    monkeypatch,
    status_code,
):
    _install_client(monkeypatch, _FakeResponse(status_code=status_code))

    with pytest.raises(grok.GrokDiscoveryError) as error:
        await grok.grok_fetch_urls(["Alpha"])

    assert error.value.reason == f"http_{status_code}"
    assert error.value.status_code == status_code


@pytest.mark.asyncio
async def test_malformed_upstream_shape_raises_typed_error(monkeypatch):
    _install_client(monkeypatch, _FakeResponse(body={"unexpected": True}))

    with pytest.raises(grok.GrokDiscoveryError) as error:
        await grok.grok_fetch_urls(["Alpha"])

    assert error.value.reason == "invalid_upstream_response"


@pytest.mark.asyncio
async def test_malformed_model_json_raises_typed_error(monkeypatch):
    _install_client(monkeypatch, _FakeResponse(body={
        "choices": [{"message": {"content": "not json"}}],
    }))

    with pytest.raises(grok.GrokDiscoveryError) as error:
        await grok.grok_fetch_urls(["Alpha"])

    assert error.value.reason == "invalid_model_json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        {},
        {"members": "not-a-list"},
        {"members": ["not-an-object"]},
        {"members": [{"handle": "Alpha", "urls": "not-a-list"}]},
        {"members": [{"handle": "Alpha", "urls": [123]}]},
    ],
)
async def test_invalid_model_schema_raises_typed_error(monkeypatch, content):
    _install_client(
        monkeypatch,
        _FakeResponse(
            body={
                "choices": [
                    {"message": {"content": json.dumps(content)}}
                ]
            }
        ),
    )

    with pytest.raises(grok.GrokDiscoveryError) as error:
        await grok.grok_fetch_urls(["Alpha"])

    assert error.value.reason == "invalid_model_schema"


@pytest.mark.asyncio
async def test_empty_member_urls_are_a_valid_empty_result(monkeypatch):
    _install_client(
        monkeypatch,
        _FakeResponse(
            body={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "members": [
                                    {"handle": "Alpha", "urls": []}
                                ]
                            })
                        }
                    }
                ]
            }
        ),
    )

    assert await grok.grok_fetch_urls(["Alpha"]) == []

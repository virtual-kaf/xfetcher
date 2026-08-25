import json

import pytest
from nonebot_plugin_xfetch.clients import deepseek


class _FakeResponse:
    def __init__(self, body: dict, status_code: int = 200):
        self._body = body
        self.status_code = status_code
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, body: dict):
        self.body = body
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse(self.body)


def _completed(output: dict) -> dict:
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(output, ensure_ascii=False)},
        }],
    }


@pytest.mark.asyncio
async def test_deepseek_json_uses_chat_completions_without_tools(monkeypatch):
    client = _FakeClient(_completed({"translations": []}))
    monkeypatch.setattr(deepseek, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        deepseek,
        "DEEPSEEK_API_URL",
        "https://example.test/chat/completions",
    )
    monkeypatch.setattr(deepseek, "DEEPSEEK_MODEL", "deepseek-v4-flash")

    data = await deepseek._deepseek_json(
        client,
        "system json",
        "input",
        "translations",
        deepseek.TRANSLATE_SCHEMA,
    )

    assert data == {"translations": []}
    url, request = client.calls[0]
    assert url.endswith("/chat/completions")
    assert request["headers"] == {"Authorization": "Bearer test-key"}
    payload = request["json"]
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["max_tokens"] == 32768
    assert payload["temperature"] == 0
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"].startswith("system json")
    assert "EXAMPLE JSON OUTPUT (translations)" in (
        payload["messages"][0]["content"]
    )
    assert json.dumps(
        deepseek.JSON_OUTPUT_EXAMPLES["translations"],
        ensure_ascii=False,
    ) in payload["messages"][0]["content"]
    assert "JSON schema (translations)" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "input"}
    assert "tools" not in payload
    assert "google_search" not in payload


def test_incomplete_error_detects_deepseek_output_limit():
    body = {
        "choices": [{
            "finish_reason": "length",
            "message": {"content": ""},
        }],
        "usage": {
            "completion_tokens": 8192,
        },
    }

    with pytest.raises(deepseek.DeepSeekIncompleteError) as error:
        deepseek._extract_output_text(body)

    assert error.value.reason == "max_output_tokens"
    assert "completion_tokens" in str(error.value)


def test_empty_content_is_a_retryable_response_error():
    body = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": ""},
        }],
    }

    with pytest.raises(
        deepseek.DeepSeekResponseError,
        match="no message content",
    ):
        deepseek._extract_output_text(body)


def test_combined_schema_contains_no_keywords():
    assert "keywords" not in json.dumps(
        deepseek.TRANSLATE_REVIEW_EVENT_SCHEMA,
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_completed_batch_calls_model_once(monkeypatch):
    calls = []

    async def fake_once(items):
        calls.append(items)
        return ({item[0]: "translated" for item in items}, {}, [])

    monkeypatch.setattr(deepseek, "_translate_and_review_once", fake_once)
    items = [("1:target", "one", True), ("2:target", "two", True)]

    translations, reviews, events = (
        await deepseek.translate_and_review_batch(items)
    )

    assert calls == [items]
    assert translations == {"1:target": "translated", "2:target": "translated"}
    assert reviews == {}
    assert events == []


@pytest.mark.asyncio
async def test_max_token_incomplete_splits_without_same_request_retry(monkeypatch):
    call_ids = []

    async def fake_once(items):
        call_ids.append([item[0] for item in items])
        if len(items) > 2:
            raise deepseek.DeepSeekIncompleteError(
                "max_output_tokens",
                "token cap",
            )
        return ({item[0]: "translated" for item in items}, {}, [])

    monkeypatch.setattr(deepseek, "_translate_and_review_once", fake_once)
    items = [(f"{index}:target", "text", True) for index in range(4)]

    translations, _, _ = await deepseek.translate_and_review_batch(items)

    assert call_ids == [
        ["0:target", "1:target", "2:target", "3:target"],
        ["0:target", "1:target"],
        ["2:target", "3:target"],
    ]
    assert len(translations) == 4


@pytest.mark.asyncio
async def test_transient_error_retries_once(monkeypatch):
    calls = 0

    async def fake_once(items):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise deepseek.DeepSeekServerError("server busy")
        return ({items[0][0]: "translated"}, {}, [])

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(deepseek, "_translate_and_review_once", fake_once)
    monkeypatch.setattr(deepseek.asyncio, "sleep", no_sleep)

    result = await deepseek.translate_and_review_batch([
        ("1:target", "text", True),
    ])

    assert calls == 2
    assert result[0] == {"1:target": "translated"}


@pytest.mark.asyncio
async def test_single_item_incomplete_does_not_recurse(monkeypatch):
    calls = 0

    async def fake_once(_items):
        nonlocal calls
        calls += 1
        raise deepseek.DeepSeekIncompleteError(
            "max_output_tokens",
            "token cap",
        )

    monkeypatch.setattr(deepseek, "_translate_and_review_once", fake_once)

    result = await deepseek.translate_and_review_batch([
        ("1:target", "text", True),
    ])

    assert calls == 1
    assert result == ({}, {}, [])

import asyncio
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from nonebot import logger

from ..config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    REQUEST_TIMEOUT,
    XFETCH_VISION_DETAIL,
    XFETCH_VISION_MAX_IMAGES_PER_REQUEST,
    XFETCH_VISION_MAX_REQUEST_BYTES,
)

# ===== Prompts =====

TRANSLATE_SYSTEM_PROMPT = """\
Translate each X post from Japanese to natural Chinese. Preserve every fact,
emoji, and hashtag. Return only JSON matching the supplied JSON Schema.
"""

TRANSLATE_REVIEW_EVENT_PROMPT = """\
Process X/Twitter posts as translator, safety reviewer, and event detector.

Current UTC time: __NOW_UTC__
Current year: __NOW_YEAR__. ALWAYS use this year for dates!

Some posts are followed by one or more image blocks. Each image belongs to the
post whose text appears immediately before it. Images are provided ONLY to help
detect scheduled events; never use images for translation or review.

For every item:
1. Translate its TEXT to natural Chinese; preserve all facts, emoji, and hashtags.
2. If review=true, flag only clear NSFW, hate, harassment, gore, self-harm,
   extremist/political-sensitive content, doxxing, or Chinese-regulation violations.
   Judge the review from the text only.
3. If review=true, detect scheduled real-time streams, live broadcasts,
   concerts, festivals, stage appearances, or live radio/talk programs. Use the
   text and, when present, the attached poster/flyer/screenshot to read the
   event title, date and time. Exclude MVs, songs/albums, recordings, trailers,
   merchandise, tickets, campaigns, articles, and other releases. A title
   containing LIVE alone is insufficient.
4. Return every DISTINCT event in the "events" array (at most 10). Do not merge
   separate events and do not repeat the same event twice.
5. Explicit date+time: UTC ISO-8601 and is_precise=true. Date without time:
   00:00 JST converted to UTC and is_precise=false. No event: empty array.
6. If review=false: flagged=false and events=[].
Return only JSON matching the supplied JSON Schema.
"""

EVENT_MATCH_PROMPT = """\
You match newly detected events against an existing calendar.
For each new event, determine if it refers to the same real-world event as
any existing calendar entry. Consider: similar title keywords, time proximity
(±24h), and member/group overlap.
Return the matching event_id from the calendar, or empty string if no match.
Return only JSON matching the supplied JSON Schema.
"""


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "translated": {"type": "string"},
                },
                "required": ["id", "translated"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["translations"],
    "additionalProperties": False,
}

TRANSLATE_REVIEW_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "translated": {"type": "string"},
                    "flagged": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "events": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "start_utc": {"type": "string"},
                                "is_precise": {"type": "boolean"},
                            },
                            "required": ["title", "start_utc", "is_precise"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "id",
                    "translated",
                    "flagged",
                    "reason",
                    "events",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

EVENT_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "new_idx": {"type": "integer"},
                    "match_event_id": {"type": "string"},
                },
                "required": ["new_idx", "match_event_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["matches"],
    "additionalProperties": False,
}

JSON_OUTPUT_EXAMPLES = {
    "translations": {
        "translations": [{
            "id": "example:target",
            "translated": "示例译文",
        }],
    },
    "translate_review_event": {
        "results": [{
            "id": "example:target",
            "translated": "示例译文",
            "flagged": False,
            "reason": "",
            "events": [{
                "title": "示例活动",
                "start_utc": "2026-01-01T10:00:00Z",
                "is_precise": True,
            }],
        }],
    },
    "event_matches": {
        "matches": [{
            "new_idx": 0,
            "match_event_id": "",
        }],
    },
}


class DeepSeekIncompleteError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class DeepSeekServerError(RuntimeError):
    pass


class DeepSeekResponseError(RuntimeError):
    pass


class DeepSeekContentFilterError(RuntimeError):
    """The model blocked the response with its own content filter."""


class DeepSeekImageInputError(RuntimeError):
    """The model rejected the image payload (size/body/format)."""


def _usage_detail(body: dict) -> str:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return ""
    return f", usage={usage!r}"


def _extract_output_text(body: dict) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DeepSeekResponseError(
            f"DeepSeek response contains no choices{_usage_detail(body)}"
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise DeepSeekResponseError("DeepSeek response choice is not an object")
    if choice.get("finish_reason") in {"length", "max_tokens"}:
        raise DeepSeekIncompleteError(
            "max_output_tokens",
            f"DeepSeek response reached the output limit{_usage_detail(body)}",
        )
    if choice.get("finish_reason") == "content_filter":
        raise DeepSeekContentFilterError(
            f"DeepSeek content filter blocked the response{_usage_detail(body)}"
        )
    message = choice.get("message")
    output_text = (
        str(message.get("content", "")).strip()
        if isinstance(message, dict)
        else ""
    )
    if not output_text:
        raise DeepSeekResponseError(
            "DeepSeek response contains no message content"
        )
    return output_text


def _image_mime(raw: bytes) -> str:
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _image_block(image: dict) -> dict:
    """Encode one cached image file as an inline base64 content block."""
    raw = Path(image["path"]).read_bytes()
    mime = _image_mime(raw)
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime};base64,{encoded}",
            "detail": XFETCH_VISION_DETAIL,
        },
    }


def _build_user_content(
    items: list[tuple[str, str, bool]],
    images_by_id: dict[str, list[dict]],
) -> str | list[dict]:
    """Build a text-only prompt, or text blocks interleaved with image blocks."""
    payload_items = [
        {"id": tid, "text": t, "review": rv} for tid, t, rv in items
    ]
    if not images_by_id:
        return "Process:\n" + json.dumps(payload_items, ensure_ascii=False)

    blocks: list[dict] = [{
        "type": "text",
        "text": (
            "Process the posts below. Each image block belongs to the post whose "
            "text appears immediately before it. Use images only for event "
            "detection."
        ),
    }]
    for tid, text, review in items:
        blocks.append({
            "type": "text",
            "text": json.dumps(
                {"id": tid, "text": text, "review": review},
                ensure_ascii=False,
            ),
        })
        for image in images_by_id.get(tid, []):
            blocks.append(_image_block(image))
    return blocks


async def _deepseek_json(
    client: httpx.AsyncClient,
    system: str,
    user: str | list[dict],
    schema_name: str,
    schema: dict,
) -> dict:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("KABUBU_DEEPSEEK_API_KEY is not configured")
    example = JSON_OUTPUT_EXAMPLES.get(schema_name, {})
    system_with_schema = (
        f"{system.rstrip()}\n\n"
        "Return valid JSON only. Copy IDs and values from the actual input; "
        "the example below demonstrates the required JSON shape only.\n"
        f"EXAMPLE JSON OUTPUT ({schema_name}):\n"
        f"{json.dumps(example, ensure_ascii=False)}\n"
        f"JSON schema ({schema_name}):\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_with_schema},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 32768,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    r = await client.post(
        DEEPSEEK_API_URL,
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json=payload,
    )
    if r.status_code >= 500:
        detail = r.text[:300].replace("\r", " ").replace("\n", " ").strip()
        suffix = f": {detail}" if detail else ""
        raise DeepSeekServerError(f"DeepSeek HTTP {r.status_code}{suffix}")
    if r.status_code in {400, 413}:
        detail = r.text[:300].replace("\r", " ").replace("\n", " ").strip()
        lowered = detail.casefold()
        if any(
            keyword in lowered
            for keyword in ("image", "request body", "too large", "payload")
        ):
            raise DeepSeekImageInputError(
                f"DeepSeek rejected image input (HTTP {r.status_code}): {detail}"
            )
    if r.status_code != 200:
        detail = r.text[:300].replace("\r", " ").replace("\n", " ").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"DeepSeek HTTP {r.status_code}{suffix}")
    try:
        body = r.json()
    except ValueError as e:
        raise DeepSeekResponseError(
            "DeepSeek response body is not JSON"
        ) from e
    if not isinstance(body, dict):
        raise DeepSeekResponseError("DeepSeek response body is not an object")

    output_text = _extract_output_text(body)
    try:
        data = json.loads(output_text)
    except json.JSONDecodeError as e:
        raise DeepSeekResponseError(
            "DeepSeek message content is not valid JSON"
        ) from e
    if not isinstance(data, dict):
        raise DeepSeekResponseError(
            "DeepSeek message content JSON is not an object"
        )
    return data


# ===== Combined translate + review + event detection =====

def _parse_combined_results(
    data: dict,
) -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    """Turn one DeepSeek response into translations, reviews, and events."""
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise DeepSeekResponseError(
            "DeepSeek response is missing a results list"
        )

    translations: dict[str, str] = {}
    reviews: dict[str, dict] = {}
    events: list[dict] = []
    for r in raw_results:
        if not isinstance(r, dict):
            raise DeepSeekResponseError(
                "DeepSeek response contains a malformed result"
            )
        rid = str(r.get("id", ""))
        translated = r.get("translated", "")
        translations[rid] = str(translated) if translated is not None else ""
        if bool(r.get("flagged", False)):
            reason = str(r.get("reason", ""))
            reviews[rid] = {"flagged": True, "reason": reason}
            logger.info(f"[Review] Content flagged for {rid}: {reason}")
        raw_events = r.get("events")
        if not isinstance(raw_events, list):
            continue
        for index, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, dict):
                continue
            title = str(raw_event.get("title", "")).strip()
            start = str(raw_event.get("start_utc", "")).strip()
            if not title or not start:
                continue
            events.append({
                "tid": rid,
                "event_key": f"{rid}#{index}",
                "event_title": title,
                "event_start_utc": start,
                "event_is_precise": bool(raw_event.get("is_precise", False)),
            })
    return translations, reviews, events


async def _translate_and_review_once(
    items: list[tuple[str, str, bool]],
    images_by_id: dict[str, list[dict]] | None = None,
) -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    """Run one combined translation, review, and event-detection request."""
    images_by_id = images_by_id or {}
    user_content = _build_user_content(items, images_by_id)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        now_utc = datetime.now(timezone.utc)
        system_prompt = TRANSLATE_REVIEW_EVENT_PROMPT.replace(
            "__NOW_UTC__",
            now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ).replace("__NOW_YEAR__", str(now_utc.year))
        data = await _deepseek_json(
            client,
            system_prompt,
            user_content,
            "translate_review_event",
            TRANSLATE_REVIEW_EVENT_SCHEMA,
        )

    translations, reviews, events = _parse_combined_results(data)
    logger.debug(
        f"[Translate+Review+Event] {len(translations)} translated, "
        f"{len(reviews)} flagged, {len(events)} events in one call"
    )
    return translations, reviews, events


def _merge_processing_results(
    first: tuple[dict[str, str], dict[str, dict], list[dict]],
    second: tuple[dict[str, str], dict[str, dict], list[dict]],
) -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    translations = {**first[0], **second[0]}
    reviews = {**first[1], **second[1]}
    events_by_key = {
        str(event.get("event_key") or event.get("tid", "")): event
        for event in [*first[2], *second[2]]
        if event.get("event_key") or event.get("tid")
    }
    return translations, reviews, list(events_by_key.values())


def _partition_items_by_image_budget(
    items: list[tuple[str, str, bool]],
    images_by_id: dict[str, list[dict]],
) -> list[list[tuple[str, str, bool]]]:
    """Split items so each request stays under the image count/byte caps."""
    chunks: list[list[tuple[str, str, bool]]] = []
    current: list[tuple[str, str, bool]] = []
    image_count = 0
    total_bytes = 0
    for item in items:
        item_images = images_by_id.get(item[0], [])
        item_bytes = sum(int(image.get("size", 0)) for image in item_images)
        if current and (
            image_count + len(item_images) > XFETCH_VISION_MAX_IMAGES_PER_REQUEST
            or total_bytes + item_bytes > XFETCH_VISION_MAX_REQUEST_BYTES
        ):
            chunks.append(current)
            current = []
            image_count = 0
            total_bytes = 0
        current.append(item)
        image_count += len(item_images)
        total_bytes += item_bytes
    if current:
        chunks.append(current)
    return chunks


async def _process_translate_chunk(
    items: list[tuple[str, str, bool]],
    images_by_id: dict[str, list[dict]] | None = None,
    *,
    allow_transient_retry: bool = True,
) -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    images_by_id = images_by_id or {}
    try:
        return await _translate_and_review_once(items, images_by_id)
    except (DeepSeekContentFilterError, DeepSeekImageInputError) as e:
        if images_by_id:
            logger.warning(
                f"[Translate+Review+Event] {type(e).__name__}: {e}; "
                f"retrying {len(items)} items without images"
            )
            return await _process_translate_chunk(
                items,
                {},
                allow_transient_retry=False,
            )
        logger.error(
            f"[Translate+Review+Event] chunk failed ({len(items)} items): {e}"
        )
    except DeepSeekIncompleteError as e:
        if e.reason == "max_output_tokens" and len(items) > 1:
            midpoint = len(items) // 2
            logger.warning(
                f"[Translate+Review+Event] {e}; "
                f"splitting {len(items)} items into {midpoint}+{len(items) - midpoint}"
            )
            left = await _process_translate_chunk(
                items[:midpoint], images_by_id
            )
            right = await _process_translate_chunk(
                items[midpoint:], images_by_id
            )
            return _merge_processing_results(left, right)
        logger.error(
            f"[Translate+Review+Event] chunk failed ({len(items)} items): {e}"
        )
    except (
        httpx.RequestError,
        DeepSeekResponseError,
        DeepSeekServerError,
    ) as e:
        if allow_transient_retry:
            logger.warning(
                f"[Translate+Review+Event] transient failure: {e}; retrying once"
            )
            await asyncio.sleep(1)
            return await _process_translate_chunk(
                items,
                images_by_id,
                allow_transient_retry=False,
            )
        logger.error(
            f"[Translate+Review+Event] transient failure after retry: {e}"
        )
    except Exception as e:  # noqa: BLE001 - one bad model chunk must not stop polling
        logger.error(
            f"[Translate+Review+Event] chunk failed ({len(items)} items): "
            f"{type(e).__name__}: {e}"
        )
    return {}, {}, []


async def translate_and_review_batch(
    items: list[tuple[str, str, bool]],
    images_by_id: dict[str, list[dict]] | None = None,
) -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    """Process a batch, splitting on image budget or output token cap."""
    if not items:
        return {}, {}, []
    images_by_id = images_by_id or {}
    chunks = _partition_items_by_image_budget(items, images_by_id)
    if len(chunks) <= 1:
        return await _process_translate_chunk(items, images_by_id)
    merged: tuple[dict[str, str], dict[str, dict], list[dict]] = ({}, {}, [])
    for chunk in chunks:
        result = await _process_translate_chunk(chunk, images_by_id)
        merged = _merge_processing_results(merged, result)
    return merged


# ===== Standalone functions (own prompts, no overlap) =====

async def translate_batch(texts: list[tuple[str, str]]) -> dict[str, str]:
    """Translate-only batch. Input: [(id, text), ...] Output: {id: translated}."""
    if not texts:
        return {}
    items = [{"id": tid, "text": t} for tid, t in texts]
    user_prompt = "Translate:\n" + json.dumps(items, ensure_ascii=False)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        data = await _deepseek_json(
            client,
            TRANSLATE_SYSTEM_PROMPT,
            user_prompt,
            "translations",
            TRANSLATE_SCHEMA,
        )
        return {t["id"]: t["translated"] for t in data.get("translations", [])}


async def review_batch(texts: list[tuple[str, str]]) -> dict[str, dict]:
    """Review-only batch. Input: [(id, text), ...] Output: {id: {"flagged": bool, "reason": str}}."""
    _translations, reviews, _ = await translate_and_review_batch(
        [(tid, t, True) for tid, t in texts]
    )
    return reviews


async def match_events_batch(
    new_events: list[dict],
    calendar: list[dict],
) -> dict[str, str]:
    """Match new events against the DeepSeek translation endpoint.
    new_events: [{"event_key": "...", "event_title": "...", "event_start_utc": "...", "event_is_precise": bool}, ...]
    calendar: [{"event_id": "...", "title": "...", "start_time_utc": "...", "members": [...]}, ...]
    Returns: {event_key: matched_event_id or ""}
    """
    if not new_events or not calendar:
        return {ev["event_key"]: "" for ev in new_events}

    cal_entries = [
        {"event_id": e.get("event_id", ""), "title": e.get("title", ""),
         "start_time_utc": e.get("start_time_utc", ""), "members": e.get("members", [])}
        for e in calendar
    ]
    new_entries = [
        {"idx": i, "tid": ev["event_key"], "title": ev["event_title"],
         "start_time_utc": ev["event_start_utc"]}
        for i, ev in enumerate(new_events)
    ]

    user_prompt = (
        f"Calendar:\n{json.dumps(cal_entries, ensure_ascii=False)}\n\n"
        f"New events:\n{json.dumps(new_entries, ensure_ascii=False)}"
    )

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        try:
            data = await _deepseek_json(
                client,
                EVENT_MATCH_PROMPT,
                user_prompt,
                "event_matches",
                EVENT_MATCH_SCHEMA,
            )
        except Exception as e:  # noqa: BLE001 - event matching is best effort
            logger.warning(f"[EventMatch] DeepSeek call failed: {e}")
            return {ev["event_key"]: "" for ev in new_events}

    result = {ev["event_key"]: "" for ev in new_events}
    for m in data.get("matches", []):
        idx = m.get("new_idx", -1)
        match_id = str(m.get("match_event_id", ""))
        if 0 <= idx < len(new_events) and match_id:
            result[new_events[idx]["event_key"]] = match_id
            logger.info(
                f"[EventMatch] {new_events[idx]['event_title']} "
                f"matched to existing event {match_id}"
            )

    return result

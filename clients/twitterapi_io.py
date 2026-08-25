import asyncio
import random
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from nonebot import logger

from ..config import TWITTERAPI_IO_API_BASE, TWITTERAPI_IO_API_KEY

TWITTERAPI_IO_BATCH_SIZE = 8
TWITTERAPI_IO_MAX_PAGES = 3
TWITTERAPI_IO_MAX_TWEETS_PER_MEMBER = 2
TWITTERAPI_IO_MAX_ATTEMPTS = 3
TWITTERAPI_IO_REQUEST_TIMEOUT = 30.0
TWITTERAPI_IO_DEFAULT_RATE_LIMIT_DELAY = 5.0
TWITTERAPI_IO_SEARCH_WINDOW = timedelta(hours=1)

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_TWEET_ID_RE = re.compile(r"^[0-9]+$")


def _normalize_members(members: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_member in members:
        member = str(raw_member).strip().lstrip("@")
        key = member.casefold()
        if not _HANDLE_RE.fullmatch(member) or key in seen:
            continue
        seen.add(key)
        normalized.append(member)
    return normalized


def _build_query(members: list[str], now: datetime) -> str:
    current = now.astimezone(timezone.utc)
    since = current - TWITTERAPI_IO_SEARCH_WINDOW
    authors = " OR ".join(f"from:{member}" for member in members)
    return (
        f"({authors}) since_time:{int(since.timestamp())} "
        f"until_time:{int(current.timestamp())}"
    )


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (retry_at - datetime.now(timezone.utc)).total_seconds(),
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _transient_retry_delay(attempt: int) -> float:
    return (2 ** (attempt - 1)) + random.uniform(0.0, 0.25)


async def _request_page(
    client: httpx.AsyncClient,
    params: dict[str, str],
    *,
    batch_number: int,
    page_number: int,
) -> dict[str, Any] | None:
    url = f"{TWITTERAPI_IO_API_BASE}/twitter/tweet/advanced_search"
    headers = {"X-API-Key": TWITTERAPI_IO_API_KEY}

    for attempt in range(1, TWITTERAPI_IO_MAX_ATTEMPTS + 1):
        delay: float | None = None
        try:
            response = await client.get(url, headers=headers, params=params)
        except httpx.RequestError as exc:
            if attempt >= TWITTERAPI_IO_MAX_ATTEMPTS:
                logger.warning(
                    "[Discovery] source=twitterapi_io "
                    f"batch={batch_number} page={page_number} "
                    f"failed=network error={type(exc).__name__}"
                )
                return None
            delay = _transient_retry_delay(attempt)
            logger.warning(
                "[Discovery] source=twitterapi_io "
                f"batch={batch_number} page={page_number} "
                f"retry={attempt} reason=network delay={delay:.2f}s"
            )
        else:
            if response.status_code == 200:
                try:
                    data = response.json()
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "[Discovery] source=twitterapi_io "
                        f"batch={batch_number} page={page_number} "
                        f"failed=invalid_json error={type(exc).__name__}"
                    )
                    return None
                if not isinstance(data, dict) or not isinstance(
                    data.get("tweets"), list
                ):
                    logger.warning(
                        "[Discovery] source=twitterapi_io "
                        f"batch={batch_number} page={page_number} "
                        "failed=invalid_schema"
                    )
                    return None
                return data

            if response.status_code == 429:
                if attempt >= TWITTERAPI_IO_MAX_ATTEMPTS:
                    logger.warning(
                        "[Discovery] source=twitterapi_io "
                        f"batch={batch_number} page={page_number} "
                        "failed=http_429"
                    )
                    return None
                delay = _retry_after_seconds(
                    response.headers.get("Retry-After")
                )
                if delay is None:
                    delay = TWITTERAPI_IO_DEFAULT_RATE_LIMIT_DELAY
                logger.warning(
                    "[Discovery] source=twitterapi_io "
                    f"batch={batch_number} page={page_number} "
                    f"retry={attempt} reason=http_429 delay={delay:.2f}s"
                )
            elif 500 <= response.status_code < 600:
                if attempt >= TWITTERAPI_IO_MAX_ATTEMPTS:
                    logger.warning(
                        "[Discovery] source=twitterapi_io "
                        f"batch={batch_number} page={page_number} "
                        f"failed=http_{response.status_code}"
                    )
                    return None
                delay = _transient_retry_delay(attempt)
                logger.warning(
                    "[Discovery] source=twitterapi_io "
                    f"batch={batch_number} page={page_number} "
                    f"retry={attempt} reason=http_{response.status_code} "
                    f"delay={delay:.2f}s"
                )
            else:
                logger.warning(
                    "[Discovery] source=twitterapi_io "
                    f"batch={batch_number} page={page_number} "
                    f"failed=http_{response.status_code}"
                )
                return None

        if delay is not None:
            await asyncio.sleep(delay)

    return None


def _tweet_entry(
    raw: Any,
    members_by_key: dict[str, str],
) -> tuple[str, dict[str, str]] | None:
    if not isinstance(raw, dict):
        return None
    tweet_id = str(raw.get("id", "")).strip()
    author = raw.get("author")
    if not _TWEET_ID_RE.fullmatch(tweet_id) or not isinstance(author, dict):
        return None
    raw_handle = author.get("userName")
    if not isinstance(raw_handle, str):
        return None
    member = members_by_key.get(raw_handle.strip().lstrip("@").casefold())
    if not member:
        return None
    return tweet_id, {
        "member": member,
        "url": f"https://x.com/{member}/status/{tweet_id}",
    }


async def _fetch_batch(
    client: httpx.AsyncClient,
    members: list[str],
    now: datetime,
    seen_tweet_ids: set[str],
    *,
    batch_number: int,
) -> list[dict[str, str]]:
    members_by_key = {member.casefold(): member for member in members}
    counts = {member.casefold(): 0 for member in members}
    query = _build_query(members, now)
    cursor = ""
    entries: list[dict[str, str]] = []

    for page_number in range(1, TWITTERAPI_IO_MAX_PAGES + 1):
        params = {"query": query, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        data = await _request_page(
            client,
            params,
            batch_number=batch_number,
            page_number=page_number,
        )
        if data is None:
            break

        for raw_tweet in data["tweets"]:
            parsed = _tweet_entry(raw_tweet, members_by_key)
            if parsed is None:
                continue
            tweet_id, entry = parsed
            member_key = entry["member"].casefold()
            if (
                tweet_id in seen_tweet_ids
                or counts[member_key]
                >= TWITTERAPI_IO_MAX_TWEETS_PER_MEMBER
            ):
                continue
            seen_tweet_ids.add(tweet_id)
            counts[member_key] += 1
            entries.append(entry)

        if all(
            count >= TWITTERAPI_IO_MAX_TWEETS_PER_MEMBER
            for count in counts.values()
        ):
            break

        next_cursor = data.get("next_cursor")
        if not data.get("has_next_page") or not isinstance(
            next_cursor, str
        ) or not next_cursor:
            break
        cursor = next_cursor

    logger.info(
        "[Discovery] source=twitterapi_io "
        f"batch={batch_number} members={len(members)} "
        f"valid_urls={len(entries)}"
    )
    return entries


async def twitterapi_fetch_urls(members: list[str]) -> list[dict[str, str]]:
    """Discover recent tweet URLs using windowed Advanced Search."""
    normalized_members = _normalize_members(members)
    if not normalized_members:
        return []
    if not TWITTERAPI_IO_API_KEY:
        logger.error(
            "[Discovery] source=twitterapi_io failed=missing_api_key "
            "env=KABUBU_TWITTERAPI_IO_API_KEY"
        )
        return []

    now = datetime.now(timezone.utc)
    batches = [
        normalized_members[index:index + TWITTERAPI_IO_BATCH_SIZE]
        for index in range(0, len(normalized_members), TWITTERAPI_IO_BATCH_SIZE)
    ]
    seen_tweet_ids: set[str] = set()
    entries: list[dict[str, str]] = []

    async with httpx.AsyncClient(
        timeout=TWITTERAPI_IO_REQUEST_TIMEOUT,
        trust_env=False,
    ) as client:
        for batch_number, batch in enumerate(batches, start=1):
            try:
                entries.extend(await _fetch_batch(
                    client,
                    batch,
                    now,
                    seen_tweet_ids,
                    batch_number=batch_number,
                ))
            except Exception as exc:  # noqa: BLE001 - preserve other batches
                logger.warning(
                    "[Discovery] source=twitterapi_io "
                    f"batch={batch_number} failed=unexpected "
                    f"error={type(exc).__name__}"
                )

    logger.info(
        "[Discovery] source=twitterapi_io "
        f"batches={len(batches)} valid_urls={len(entries)}"
    )
    return entries

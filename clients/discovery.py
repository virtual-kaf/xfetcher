from typing import Any

from nonebot import logger

from . import remote_grok, twitterapi_io
from .remote_grok import RemoteDiscoveryError
from .tweet_urls import normalize_tweet_entries


def _normalize_entries(
    raw_entries: Any,
    members: list[str],
) -> list[dict[str, str]]:
    """Backward-compatible wrapper for existing tests and callers."""
    return normalize_tweet_entries(raw_entries, members)


async def discover_tweet_urls(members: list[str]) -> list[dict[str, str]]:
    """Use remote Grok and fall back to twitterapi.io only on failure."""
    try:
        raw_entries = await remote_grok.remote_fetch_urls(members)
        entries = _normalize_entries(raw_entries, members)
    except RemoteDiscoveryError as exc:
        entries = []
        failure_reason = exc.reason
    except Exception as exc:  # noqa: BLE001 - any remote outage must fall back
        entries = []
        failure_reason = f"{type(exc).__name__}"
    else:
        logger.info(
            "[Discovery] source=remote_grok "
            f"valid_urls={len(entries)} fallback=false"
        )
        return entries

    logger.warning(
        "[Discovery] source=remote_grok valid_urls=0 fallback=true "
        f"reason={failure_reason}"
    )
    try:
        fallback_entries = await twitterapi_io.twitterapi_fetch_urls(members)
    except Exception as exc:  # noqa: BLE001 - fallback must not crash polling
        logger.warning(
            "[Discovery] source=twitterapi_io failed=unexpected "
            f"error={type(exc).__name__}"
        )
        fallback_entries = []
    logger.info(
        "[Discovery] source=twitterapi_io "
        f"valid_urls={len(fallback_entries)} fallback_complete=true"
    )
    return fallback_entries

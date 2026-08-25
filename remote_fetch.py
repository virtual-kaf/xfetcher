from collections import Counter
from collections.abc import Sequence

import httpx
from nonebot import logger

from .clients.grok import GrokDiscoveryError, grok_fetch_urls
from .clients.tweet_urls import (
    HANDLE_RE,
    InvalidTweetEntriesError,
    RejectedTweetEntry,
    normalize_tweet_entries,
)
from .config import GLOBAL_MEMBER_LIMIT


class InvalidMembersError(ValueError):
    """Raised when B sends an invalid member collection."""


def normalize_requested_members(members: object) -> list[str]:
    """Validate and normalize the member list supplied by B."""
    if (
        isinstance(members, (str, bytes))
        or not isinstance(members, Sequence)
        or not 1 <= len(members) <= GLOBAL_MEMBER_LIMIT
    ):
        raise InvalidMembersError("members must contain 1 to 30 handles")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_member in members:
        if not isinstance(raw_member, str):
            raise InvalidMembersError("each member must be a string")
        member = raw_member.strip().lstrip("@")
        if not HANDLE_RE.fullmatch(member):
            raise InvalidMembersError("invalid member handle")
        key = member.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(member)

    if not normalized:
        raise InvalidMembersError("members cannot be empty")
    return normalized


def _log_rejected_entries(rejected_entries: list[RejectedTweetEntry]) -> None:
    if not rejected_entries:
        return

    reason_counts = Counter(entry.reason for entry in rejected_entries)
    reason_stats = ",".join(
        f"{reason}={count}"
        for reason, count in sorted(reason_counts.items())
    )
    logger.warning(
        "[RemoteFetch] validation "
        f"rejected_entries={len(rejected_entries)} reasons={reason_stats}"
    )
    for entry in rejected_entries:
        logger.warning(
            "[RemoteFetch] rejected "
            f"index={entry.index} reason={entry.reason} "
            f"member={entry.member!r} url={entry.url!r}"
        )


async def fetch_latest_urls(members: Sequence[str]) -> list[str]:
    """Fetch one stateless batch of latest tweet URLs using Grok only."""
    normalized_members = normalize_requested_members(members)
    try:
        raw_entries = await grok_fetch_urls(normalized_members)
        rejected_entries: list[RejectedTweetEntry] = []
        entries = normalize_tweet_entries(
            raw_entries,
            normalized_members,
            rejected_entries=rejected_entries,
        )
        _log_rejected_entries(rejected_entries)
        if not isinstance(raw_entries, list) or (raw_entries and not entries):
            raise InvalidTweetEntriesError("no_valid_tweet_urls")
    except GrokDiscoveryError:
        raise
    except (httpx.RequestError, InvalidTweetEntriesError) as exc:
        raise GrokDiscoveryError("grok_failed") from exc
    except Exception as exc:
        raise GrokDiscoveryError("grok_failed") from exc

    urls = [entry["url"] for entry in entries]
    logger.info(
        "[RemoteFetch] source=grok "
        f"members={len(normalized_members)} raw_urls={len(raw_entries)} "
        f"valid_urls={len(urls)} rejected_urls={len(rejected_entries)}"
    )
    return urls

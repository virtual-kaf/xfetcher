import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
TWEET_URL_RE = re.compile(
    r"^https?://(?:www\.)?"
    r"(?:twitter\.com|x\.com|fxtwitter\.com|fixupx\.com|twittpr\.com)"
    r"/([^/?#]+)/status/([0-9]+)(?:[/?#].*)?$",
    re.IGNORECASE,
)


class InvalidTweetEntriesError(ValueError):
    """Raised when a Grok result contains no usable tweet URL entries."""


@dataclass(frozen=True)
class RejectedTweetEntry:
    """One raw entry excluded during tweet URL normalization."""

    index: int
    reason: str
    member: str | None
    url: str | None


def normalize_member_handles(members: Sequence[object]) -> list[str]:
    """Normalize valid X handles and deduplicate them case-insensitively."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_member in members:
        member = str(raw_member).strip().lstrip("@")
        key = member.casefold()
        if not HANDLE_RE.fullmatch(member) or key in seen:
            continue
        seen.add(key)
        normalized.append(member)
    return normalized


def normalize_tweet_entries(
    raw_entries: Any,
    members: Sequence[str],
    *,
    strict: bool = False,
    rejected_entries: list[RejectedTweetEntry] | None = None,
) -> list[dict[str, str]]:
    """Validate, canonicalize, deduplicate, and optionally audit tweet URLs.

    Permissive mode preserves discovery behavior by dropping bad entries.
    Strict mode remains available for callers that need fail-fast validation.
    Remote batch validation supplies ``rejected_entries`` so one bad URL can be
    reported without discarding other valid URLs in the same Grok response.
    """

    def reject(
        index: int,
        reason: str,
        raw_member: Any = None,
        raw_url: Any = None,
    ) -> None:
        if rejected_entries is not None:
            rejected_entries.append(RejectedTweetEntry(
                index=index,
                reason=reason,
                member=raw_member if isinstance(raw_member, str) else None,
                url=raw_url if isinstance(raw_url, str) else None,
            ))
        if strict:
            raise InvalidTweetEntriesError(reason)

    if not isinstance(raw_entries, list):
        reject(-1, "entries_not_list")
        return []

    members_by_key = {
        member.strip().lstrip("@").casefold(): member.strip().lstrip("@")
        for member in members
        if isinstance(member, str) and member.strip().lstrip("@")
    }
    counts: dict[str, int] = {}
    seen_tweet_ids: set[str] = set()
    result: list[dict[str, str]] = []

    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            reject(index, "entry_not_object")
            continue
        raw_member = raw_entry.get("member")
        raw_url = raw_entry.get("url")
        if not isinstance(raw_member, str) or not isinstance(raw_url, str):
            reject(index, "entry_fields_invalid", raw_member, raw_url)
            continue

        member = members_by_key.get(raw_member.strip().lstrip("@").casefold())
        if member is None:
            reject(index, "member_not_requested", raw_member, raw_url)
            continue

        match = TWEET_URL_RE.fullmatch(raw_url.strip())
        if match is None:
            reject(index, "invalid_tweet_url", raw_member, raw_url)
            continue

        url_handle, tweet_id = match.groups()
        if url_handle.casefold() != member.casefold():
            reject(index, "url_handle_mismatch", raw_member, raw_url)
            continue

        member_key = member.casefold()
        if tweet_id in seen_tweet_ids:
            reject(index, "duplicate_tweet_id", raw_member, raw_url)
            continue
        if counts.get(member_key, 0) >= 2:
            reject(index, "member_url_limit", raw_member, raw_url)
            continue

        seen_tweet_ids.add(tweet_id)
        counts[member_key] = counts.get(member_key, 0) + 1
        result.append({
            "member": member,
            "url": f"https://x.com/{member}/status/{tweet_id}",
        })

    return result

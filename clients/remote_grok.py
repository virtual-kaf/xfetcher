import ipaddress
from typing import Any

import httpx
from nonebot import logger

from ..config import (
    REQUEST_TIMEOUT,
    XFETCH_REMOTE_ENABLED,
    XFETCH_REMOTE_HOST,
    XFETCH_REMOTE_PORT,
    XFETCH_REMOTE_TOKEN,
)
from .tweet_urls import TWEET_URL_RE

POLL_PATH = "/api/xfetch/poll"


class RemoteDiscoveryError(RuntimeError):
    """A remote Grok response that cannot be used for URL discovery."""

    def __init__(self, reason: str, status_code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def _remote_url() -> str:
    enabled = XFETCH_REMOTE_ENABLED.casefold()
    if enabled != "true":
        raise RemoteDiscoveryError(
            "remote_disabled" if enabled == "false" else "invalid_remote_config"
        )
    if not XFETCH_REMOTE_HOST or not XFETCH_REMOTE_TOKEN:
        raise RemoteDiscoveryError("invalid_remote_config")

    try:
        address = ipaddress.ip_address(XFETCH_REMOTE_HOST)
        port = int(XFETCH_REMOTE_PORT)
    except ValueError as exc:
        raise RemoteDiscoveryError("invalid_remote_config") from exc
    if not 1 <= port <= 65535:
        raise RemoteDiscoveryError("invalid_remote_config")

    host = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{host}:{port}{POLL_PATH}"


def _parse_urls(raw_urls: Any, members: list[str]) -> list[dict[str, str]]:
    if not isinstance(raw_urls, list):
        raise RemoteDiscoveryError("invalid_remote_response")

    members_by_key = {
        member.strip().lstrip("@").casefold(): member.strip().lstrip("@")
        for member in members
        if isinstance(member, str) and member.strip().lstrip("@")
    }
    entries: list[dict[str, str]] = []
    for raw_url in raw_urls:
        if not isinstance(raw_url, str):
            raise RemoteDiscoveryError("invalid_remote_response")
        match = TWEET_URL_RE.fullmatch(raw_url.strip())
        if match is None:
            raise RemoteDiscoveryError("invalid_remote_response")
        member = members_by_key.get(match.group(1).casefold())
        if member is None:
            raise RemoteDiscoveryError("invalid_remote_response")
        entries.append({"member": member, "url": raw_url})
    return entries


async def remote_fetch_urls(members: list[str]) -> list[dict[str, str]]:
    """Fetch Grok-discovered URLs from the stateless service running on A."""
    url = _remote_url()
    headers = {"Authorization": f"Bearer {XFETCH_REMOTE_TOKEN}"}
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            trust_env=False,
        ) as client:
            response = await client.post(
                url,
                headers=headers,
                json={"members": members},
            )
    except httpx.RequestError as exc:
        raise RemoteDiscoveryError("remote_request_failed") from exc

    if response.status_code != 200:
        raise RemoteDiscoveryError(
            f"http_{response.status_code}",
            status_code=response.status_code,
        )

    try:
        body = response.json()
    except (TypeError, ValueError) as exc:
        raise RemoteDiscoveryError("invalid_remote_response") from exc
    if not isinstance(body, dict):
        raise RemoteDiscoveryError("invalid_remote_response")
    if body.get("ok") is not True:
        error = body.get("error")
        reason = error if isinstance(error, str) and error else "remote_not_ok"
        raise RemoteDiscoveryError(reason)

    entries = _parse_urls(body.get("urls"), members)
    logger.info(
        "[Discovery] source=remote_grok "
        f"raw_urls={len(entries)} members={len(members)}"
    )
    return entries

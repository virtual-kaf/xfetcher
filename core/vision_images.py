"""Download and cache post photos for DeepSeek vision event extraction.

Images are written straight to a disk cache so the pipeline never holds more
than one compressed image in memory at a time.  The cached file path and byte
size are handed to the DeepSeek client, which only base64-encodes one request
chunk at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from nonebot import logger

from ..config import (
    DATA_DIR,
    XFETCH_RENDER_IMAGE_CONCURRENCY,
    XFETCH_RENDER_IMAGE_TIMEOUT,
    XFETCH_VISION_INCLUDE_VIDEO_THUMB,
    XFETCH_VISION_MAX_IMAGE_BYTES,
    XFETCH_VISION_MAX_IMAGES_PER_SOURCE,
)
from ..models.tweet import TweetItem

VISION_CACHE_DIR = DATA_DIR / "vision_cache" / "media"


def _vision_image_url(url: str) -> str:
    """Request the smallest Twitter rendition; other hosts are left as-is."""
    parts = urlsplit(url)
    if (
        parts.hostname or ""
    ).casefold() != "pbs.twimg.com" or not parts.path.startswith("/media/"):
        return url
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["name"] = "small"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _cache_path(url: str) -> Path:
    return VISION_CACHE_DIR / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.img"


def select_post_images(item: TweetItem) -> list[str]:
    """Return up to N unique photo (optionally video thumbnail) URLs."""
    urls: list[str] = []
    for media in item.media:
        if media.type == "video":
            if not XFETCH_VISION_INCLUDE_VIDEO_THUMB:
                continue
            url = media.thumbnail_url
        elif media.type == "photo":
            url = media.url
        else:
            continue
        if not url or url in urls:
            continue
        urls.append(url)
        if len(urls) >= XFETCH_VISION_MAX_IMAGES_PER_SOURCE:
            break
    return urls


async def _download(
    client: httpx.AsyncClient, url: str
) -> tuple[Path, int]:
    bounded = _vision_image_url(url)
    destination = _cache_path(bounded)
    if destination.exists():
        return destination, destination.stat().st_size

    VISION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".dl-{uuid.uuid4().hex}")
    total = 0
    try:
        async with client.stream("GET", bounded) as response:
            if not response.is_success:
                raise RuntimeError(f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if not content_type.casefold().startswith("image/"):
                raise RuntimeError(f"unexpected content type {content_type!r}")
            with temporary.open("wb") as stream:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > XFETCH_VISION_MAX_IMAGE_BYTES:
                        raise RuntimeError(
                            f"image exceeds vision cap "
                            f"({XFETCH_VISION_MAX_IMAGE_BYTES} bytes)"
                        )
                    stream.write(chunk)
        if total <= 0:
            raise RuntimeError("empty image body")
        temporary.replace(destination)
        return destination, total
    finally:
        temporary.unlink(missing_ok=True)


async def resolve_post_images(
    urls_by_id: dict[str, list[str]],
) -> dict[str, list[dict]]:
    """Download URLs by item id and return {id: [{"path", "size"}, ...]}.

    Duplicate URLs across items are downloaded once.  Individual failures are
    skipped so a single bad image cannot break event extraction.
    """
    unique = {url for urls in urls_by_id.values() for url in urls}
    if not unique:
        return {}

    gate = asyncio.Semaphore(XFETCH_RENDER_IMAGE_CONCURRENCY)
    resolved: dict[str, dict] = {}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(XFETCH_RENDER_IMAGE_TIMEOUT),
        follow_redirects=True,
        max_redirects=3,
        headers={"User-Agent": "nonebot-plugin-xfetch-vision/1"},
    ) as client:

        async def one(url: str) -> tuple[str, dict | None]:
            try:
                async with gate:
                    path, size = await _download(client, url)
                return url, {"path": path, "size": size}
            except Exception as exc:  # noqa: BLE001 - one image degrades alone
                label = url.split("?", 1)[0][-80:]
                logger.warning(
                    f"[Vision] Image skipped: {label} "
                    f"({type(exc).__name__}: {exc})"
                )
                return url, None

        for url, info in await asyncio.gather(*(one(url) for url in unique)):
            if info is not None:
                resolved[url] = info

    images_by_id: dict[str, list[dict]] = {}
    for item_id, urls in urls_by_id.items():
        images = [resolved[url] for url in urls if url in resolved]
        if images:
            images_by_id[item_id] = images
    return images_by_id

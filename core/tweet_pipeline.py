import asyncio
import re
from datetime import datetime, timezone

from nonebot import logger

from ..clients.deepseek import match_events_batch, translate_and_review_batch
from ..clients.discovery import discover_tweet_urls
from ..clients.fxtwitter import fetch_conversation
from ..config import (
    JST,
    MAX_POST_AGE,
    XFETCH_FETCH_CONCURRENCY,
    XFETCH_VISION_ENABLED,
    XFETCH_VISION_MAX_EVENTS_PER_POST,
)
from ..models.tweet import TweetConversation
from ..storage import get_live_events, is_duplicate
from ..utils import parse_time
from .event_media import attach_event_media
from .live_pipeline import merge_member_to_event, process_live_event
from .vision_images import resolve_post_images, select_post_images


def _parse_tweet_id(url: str) -> str | None:
    m = re.search(r"(?:twitter\.com|x\.com|fxtwitter\.com|fixupx\.com|twittpr\.com)/\w+/status/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else None


def _normalize_handle(handle: str) -> str:
    """Strip @ prefix from Grok handles for consistent key matching."""
    return handle.lstrip("@")


def _parse_tweet_created_at(raw: str) -> datetime | None:
    """Parse FxTwitter's display timestamp into an aware UTC datetime."""
    value = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S JST", "%Y-%m-%d %H:%M JST"):
        try:
            return (
                datetime.strptime(value, fmt)
                .replace(tzinfo=JST)
                .astimezone(timezone.utc)
            )
        except ValueError:
            continue
    parsed = parse_time(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _normalize_detected_events(events: list) -> list:
    """Deduplicate detected events within a tweet and cap how many are kept."""
    deduped: list = []
    seen: set[tuple[str, str, str]] = set()
    per_tweet: dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        tid = str(event.get("tid", ""))
        title = re.sub(
            r"\s+", "", str(event.get("event_title", ""))
        ).casefold()
        start = str(event.get("event_start_utc", "")).strip()
        if not tid or not title or not start:
            continue
        tweet_id = tid.split(":", 1)[0]
        key = (tweet_id, title, start)
        if key in seen:
            continue
        if per_tweet.get(tweet_id, 0) >= XFETCH_VISION_MAX_EVENTS_PER_POST:
            continue
        seen.add(key)
        per_tweet[tweet_id] = per_tweet.get(tweet_id, 0) + 1
        deduped.append(event)
    return deduped


async def run_tweet_pipeline(members: list[str]) -> list[TweetConversation]:
    """Discover tweet URLs, fetch conversations, review them, and emit results."""
    results: list[TweetConversation] = []

    # Step 1: Discover URLs with Grok primary and twitterapi.io fallback.
    url_entries = await discover_tweet_urls(members)
    if not url_entries:
        logger.warning("[Pipeline] URL discovery returned no results")
        return []

    # Dedup now; delivery is marked only after a group actually accepts it.
    # Normalize handles: strip @ prefix from Grok responses
    seen_ids = set()
    tasks = {}
    skipped_duplicate = 0
    skipped_bad_url = 0
    for entry in url_entries:
        tid = _parse_tweet_id(entry["url"])
        if not tid:
            logger.debug(f"[Pipeline] Cannot parse URL: {entry['url'][:100]}")
            skipped_bad_url += 1
            continue
        if tid in seen_ids:
            continue
        member = _normalize_handle(entry["member"])
        if is_duplicate(member, tid):
            skipped_duplicate += 1
            continue
        seen_ids.add(tid)
        tasks[tid] = member

    logger.info(
        f"[Pipeline] Discovery returned {len(url_entries)}, "
        f"parse failed {skipped_bad_url}, duplicate {skipped_duplicate}, "
        f"to fetch {len(tasks)}"
    )

    if not tasks:
        return []

    # Step 2: FxEmbed fetch. Do not fan out one blocking thread per URL: a
    # conversation response can be large and that used to create a substantial
    # transient memory spike on small servers.
    fetch_semaphore = asyncio.Semaphore(XFETCH_FETCH_CONCURRENCY)

    async def fetch_one(tid: str):
        try:
            async with fetch_semaphore:
                return tid, await asyncio.to_thread(fetch_conversation, tid)
        except Exception as e:  # noqa: BLE001 - isolate per-tweet fetch failures
            logger.warning(f"[Pipeline] FxEmbed fetch failed {tid}: {e}")
            return tid, None
    fetched = await asyncio.gather(*[fetch_one(tid) for tid in tasks])

    convs: dict[str, TweetConversation] = {}
    skipped_old = 0
    skipped_bad_time = 0
    now_utc = datetime.now(timezone.utc)
    for tid, conv in fetched:
        if conv is None or conv.target is None:
            continue
        created_at = _parse_tweet_created_at(conv.target.created_at)
        if created_at is None:
            skipped_bad_time += 1
            logger.warning(
                f"[Pipeline] Skip tweet {tid}: cannot parse created_at"
            )
            continue
        age = now_utc - created_at
        if age > MAX_POST_AGE:
            skipped_old += 1
            logger.info(
                f"[Pipeline] Skip tweet {tid}: age={age} "
                f"exceeds max_age={MAX_POST_AGE}"
            )
            continue
        convs[tid] = conv
        conv.source_member = tasks[tid]

    logger.info(
        f"[Pipeline] FxTwitter accepted={len(convs)}, "
        f"too_old={skipped_old}, invalid_time={skipped_bad_time}"
    )

    # Step 3: Collect texts (and target/quote photos) for the combined call.
    items: list[tuple[str, str, bool]] = []
    image_urls_by_id: dict[str, list[str]] = {}
    for tid, conv in convs.items():
        if conv.target:
            target_id = f"{tid}:target"
            items.append((target_id, conv.target.text, True))
            if XFETCH_VISION_ENABLED:
                urls = select_post_images(conv.target)
                if urls:
                    image_urls_by_id[target_id] = urls
        for i, anc in enumerate(conv.ancestors):
            if anc.id != (conv.target.id if conv.target else ""):
                items.append((f"{tid}:anc{i}", anc.text, True))
        if conv.quote:
            quote_id = f"{tid}:quote"
            items.append((quote_id, conv.quote.text, True))
            if XFETCH_VISION_ENABLED:
                urls = select_post_images(conv.quote)
                if urls:
                    image_urls_by_id[quote_id] = urls

    images_by_id: dict[str, list[dict]] = {}
    if image_urls_by_id:
        try:
            images_by_id = await resolve_post_images(image_urls_by_id)
        except Exception as e:  # noqa: BLE001 - vision is best effort
            logger.warning(f"[Pipeline] Vision image download failed: {e}")
            images_by_id = {}

    # Step 4: One API call for translate + review + event detection
    translations: dict = {}
    reviews: dict = {}
    events_detected: list = []
    if items:
        try:
            if images_by_id:
                translations, reviews, events_detected = (
                    await translate_and_review_batch(items, images_by_id)
                )
            else:
                translations, reviews, events_detected = (
                    await translate_and_review_batch(items)
                )
        except Exception as e:  # noqa: BLE001 - feed delivery can continue untranslated
            logger.warning(f"[Pipeline] Translate+Review+Event failed: {e}")

    events_detected = _normalize_detected_events(events_detected)

    # Log review results
    for rid, review in reviews.items():
        tweet_id = rid.split(":")[0]
        logger.warning(
            f"[Review] Sensitive content from @{tasks.get(tweet_id, '?')} "
            f"(tweet {tweet_id}): {review.get('reason', 'no reason')}"
        )

    # Step 5: Do not send a conversation if any displayed post was flagged.
    # Missing review results are allowed through so a review API outage does not
    # block normal feed delivery.
    blocked_tweet_ids = {
        review_id.split(":", 1)[0]
        for review_id in reviews
    }
    for tid in sorted(blocked_tweet_ids):
        logger.warning(
            f"[Review] Skip tweet {tid}: sensitive content detected"
        )

    # Step 6: Apply translations only to approved conversations.
    for tid, conv in convs.items():
        if tid in blocked_tweet_ids:
            continue
        if conv.target:
            conv.target.translated_text = translations.get(f"{tid}:target", "")
        for i, anc in enumerate(conv.ancestors):
            anc.translated_text = translations.get(f"{tid}:anc{i}", "")
        if conv.quote:
            conv.quote.translated_text = translations.get(f"{tid}:quote", "")
        results.append(conv)

    # Step 7: Never create events from rejected content.
    events_detected = [
        event for event in events_detected
        if event.get("tid", "").split(":", 1)[0] not in blocked_tweet_ids
    ]

    attach_event_media(events_detected, convs)

    # Step 8: Event processing — calendar matching with 24h window
    if events_detected:
        await _process_events(events_detected, tasks)

    return results


async def _process_events(events_detected: list, tasks: dict):
    """Process detected events: check calendar ±24h, match or create."""
    try:
        await _process_events_impl(events_detected, tasks)
    except Exception as e:  # noqa: BLE001 - event detection is best effort
        logger.error(f"[Event] _process_events crashed, {len(events_detected)} events lost: {e}", exc_info=True)


async def _process_events_impl(events_detected: list, tasks: dict):
    calendar = get_live_events()
    cal_list = [
        {
            "event_id": ev.event_id,
            "title": ev.title,
            "start_time_utc": ev.start_time_utc,
            "members": ev.members,
        }
        for ev in calendar
    ]

    new_for_match = []      # events that need model matching
    no_nearby = []          # events with no nearby calendar entries

    for ev in events_detected:
        ev_time = parse_time(ev["event_start_utc"])
        if ev_time is None:
            logger.warning(f"[Event] Cannot parse time: {ev['event_start_utc']}, skipping")
            continue

        # Find calendar entries within ±24h
        nearby = []
        for ce in cal_list:
            ct = parse_time(ce["start_time_utc"])
            if ct and abs((ct - ev_time).total_seconds()) <= 86400:
                nearby.append(ce)

        if nearby:
            new_for_match.append(ev)
        else:
            no_nearby.append(ev)

    # Events with no nearby calendar entries → create directly
    for ev in no_nearby:
        tid = ev["tid"]
        tweet_id = tid.split(":")[0]
        member = tasks.get(tweet_id, "")
        if member:
            await process_live_event(member, ev)
            logger.info(f"[Event] New event (no nearby): {ev['event_title']}")
        else:
            logger.warning(f"[Event] Dropped (no nearby): member not found for tid={tid!r}, tweet_id={tweet_id!r}")

    # Events with nearby entries → ask DeepSeek to match
    if new_for_match:
        matches = await match_events_batch(new_for_match, cal_list)
        for ev in new_for_match:
            tid = ev["tid"]
            tweet_id = tid.split(":")[0]
            member = tasks.get(tweet_id, "")
            if not member:
                logger.warning(f"[Event] Dropped (match): member not found for tid={tid!r}, tweet_id={tweet_id!r}")
                continue

            matched_id = matches.get(ev["event_key"], "")
            if matched_id:
                await merge_member_to_event(matched_id, member, ev)
            else:
                await process_live_event(member, ev)
                logger.info(f"[Event] New event (no match): {ev['event_title']}")

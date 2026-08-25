import hashlib
from typing import Dict, Any

from nonebot import logger

from ..config import CORE_MEMBERS
from ..models.live import LiveEvent
from ..storage import get_live_events, save_live_event, save_live_events
from ..utils import parse_time


async def merge_member_to_event(event_id: str, member: str, ev_data: dict = None):
    """Add a member to an existing event. Falls back to creating new event if target was archived."""
    events = get_live_events()
    for ev in events:
        if ev.event_id == event_id:
            changed = False
            if member not in ev.members:
                ev.members.append(member)
                ev.notified = False
                changed = True
            if ev_data and not ev.cover_url and ev_data.get("cover_url"):
                ev.cover_url = ev_data["cover_url"]
                changed = True
            if ev_data and not ev.source_tweet_id and ev_data.get("source_tweet_id"):
                ev.source_tweet_id = ev_data["source_tweet_id"]
                changed = True
            if changed:
                save_live_events(events)
                logger.info(f"[Live] Merged @{member} into existing event: {ev.title}")
            else:
                logger.debug(f"[Live] @{member} already in event: {ev.title}")
            return
    logger.warning(f"[Live] Event {event_id} not found for merge of @{member} — archived; creating new instead")
    if ev_data:
        await process_live_event(member, ev_data)
    else:
        logger.error(f"[Live] No ev_data for fallback, @{member} event lost")


async def process_live_event(member: str, ev_data: Dict[str, Any]):
    """Create or update a single-member live event."""
    title = ev_data.get("event_title", ev_data.get("title", ""))
    start_time = ev_data.get("event_start_utc", ev_data.get("start_time_utc", ""))
    is_precise = ev_data.get("event_is_precise", ev_data.get("is_precise", True))
    cover_url = str(ev_data.get("cover_url", ""))
    source_tweet_id = str(ev_data.get("source_tweet_id", ""))
    if not title or not start_time:
        logger.warning(f"[Live] Event dropped: empty title or start_time (title={title!r}, start_time={start_time!r})")
        return

    # Fix an incorrect model-supplied year (e.g. 2025 instead of 2026)
    from datetime import datetime, timedelta, timezone as _tz
    parsed = parse_time(start_time)
    if parsed and parsed < datetime.now(_tz.utc) - timedelta(hours=24):
        try:
            fixed = parsed.replace(year=parsed.year + 1)
            start_time = fixed.isoformat()
            logger.warning(f"[Live] Fixed wrong year: {ev_data.get('event_start_utc', start_time)} -> {start_time}")
        except Exception:
            pass

    existing = get_live_events()
    new_ev_id = hashlib.md5(f"{member}{start_time}".encode()).hexdigest()

    for ev in existing:
        if ev.members == [member] and ev.title == title:
            ev.start_time_utc = start_time
            ev.is_precise = is_precise
            ev.notified = False
            if not ev.cover_url and cover_url:
                ev.cover_url = cover_url
            if not ev.source_tweet_id and source_tweet_id:
                ev.source_tweet_id = source_tweet_id
            save_live_event(ev)
            logger.info(f"[Live] Updated event: {title}")
            return

    event = LiveEvent(
        event_id=new_ev_id,
        members=[member],
        title=title,
        start_time_utc=start_time,
        is_precise=is_precise,
        notified=False,
        cover_url=cover_url,
        source_tweet_id=source_tweet_id,
    )
    save_live_event(event)
    logger.info(f"[Live] New event: {title} @ {start_time}")

import json
from pathlib import Path

from nonebot import logger

from ..config import DATA_DIR, HISTORY_LIMIT
from ..models.group import GroupConfig
from ..models.live import LiveEvent


# 文件路径（与 V1 文件名兼容）
SUBS_FILE = DATA_DIR / "vwp_group_subs.json"
STATUS_FILE = DATA_DIR / "vwp_last_status.json"
LIVE_FILE = DATA_DIR / "vwp_live_schedule.json"
LIVE_ARCHIVE_FILE = DATA_DIR / "vwp_live_schedule_archive.json"


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data):
    """Atomically write JSON to file (temp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ===== 推文去重 =====

def is_duplicate(member: str, tweet_id: str) -> bool:
    status = _load_json(STATUS_FILE, {})
    ids = status.get(member, [])
    return tweet_id in ids


def mark_sent(member: str, tweet_id: str):
    status = _load_json(STATUS_FILE, {})
    ids = status.setdefault(member, [])
    if tweet_id not in ids:
        ids.append(tweet_id)
    if len(ids) > HISTORY_LIMIT:
        status[member] = ids[-HISTORY_LIMIT:]
    _save_json(STATUS_FILE, status)


# ===== 群配置 =====

def get_group_config(group_id: str) -> GroupConfig:
    subs = _load_json(SUBS_FILE, {})
    group_data = subs.get(group_id, {})

    return GroupConfig(
        group_id=group_id,
        subs=group_data.get("subs", []),
        unsubs=group_data.get("unsubs", []),
        filter_water=group_data.get("filter_water", True),
    )


def save_group_config(cfg: GroupConfig):
    subs = _load_json(SUBS_FILE, {})
    subs[cfg.group_id] = {
        "subs": cfg.subs,
        "unsubs": cfg.unsubs,
        "filter_water": cfg.filter_water,
    }
    _save_json(SUBS_FILE, subs)


def get_all_group_configs() -> list[GroupConfig]:
    subs = _load_json(SUBS_FILE, {})
    result = []
    for gid in subs:
        group_data = subs.get(gid, {})
        result.append(GroupConfig(
            group_id=gid,
            subs=group_data.get("subs", []),
            unsubs=group_data.get("unsubs", []),
            filter_water=group_data.get("filter_water", True),
        ))
    return result


# ===== 直播日程 =====

def get_live_events() -> list[LiveEvent]:
    data = _load_json(LIVE_FILE, [])
    events = []
    for ev in data:
        members = ev.get("members", [ev.get("member", "")])
        if isinstance(members, str):
            members = [members]
        events.append(LiveEvent(
            event_id=ev.get("event_id", ""),
            members=members,
            title=ev.get("title", ""),
            start_time_utc=ev.get("start_time_utc", ""),
            is_precise=ev.get("is_precise", True),
            notified=ev.get("notified", False),
            cover_url=ev.get("cover_url", ""),
            source_tweet_id=ev.get("source_tweet_id", ""),
        ))
    return events


def save_live_events(events: list[LiveEvent]):
    data = []
    for ev in events:
        data.append({
            "event_id": ev.event_id,
            "members": ev.members,
            "title": ev.title,
            "start_time_utc": ev.start_time_utc,
            "is_precise": ev.is_precise,
            "notified": ev.notified,
            "cover_url": ev.cover_url,
            "source_tweet_id": ev.source_tweet_id,
        })
    try:
        _save_json(LIVE_FILE, data)
        logger.debug(f"[Storage] Saved {len(events)} events to {LIVE_FILE.name}")
    except Exception as e:
        logger.error(f"[Storage] Failed to save {len(events)} events: {e}", exc_info=True)


def save_live_event(ev: LiveEvent):
    events = get_live_events()
    for i, existing in enumerate(events):
        if existing.event_id == ev.event_id:
            events[i] = ev
            logger.debug(f"[Storage] Updating event {ev.event_id}: {ev.title}")
            break
    else:
        events.append(ev)
        logger.info(f"[Storage] Adding new event {ev.event_id}: {ev.title} @ {ev.start_time_utc}")
    save_live_events(events)


def archive_event(ev: LiveEvent):
    events = get_live_events()
    events = [e for e in events if e.event_id != ev.event_id]
    save_live_events(events)

    archive = _load_json(LIVE_ARCHIVE_FILE, [])
    # Prevent duplicate archive entries (same event_id)
    if any(a.get("event_id") == ev.event_id for a in archive):
        logger.debug(f"[Storage] Event {ev.event_id} already archived, skipping")
        return
    archive.append({
        "event_id": ev.event_id,
        "members": ev.members,
        "title": ev.title,
        "start_time_utc": ev.start_time_utc,
        "is_precise": ev.is_precise,
        "notified": True,
        "cover_url": ev.cover_url,
        "source_tweet_id": ev.source_tweet_id,
    })
    _save_json(LIVE_ARCHIVE_FILE, archive)

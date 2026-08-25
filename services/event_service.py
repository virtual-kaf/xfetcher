import asyncio
import random
from datetime import datetime, timedelta, timezone

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

from ..config import CORE_MEMBERS, CST, JST
from ..models.group import GroupConfig
from ..storage import (
    archive_event,
    get_all_group_configs,
    get_live_events,
    save_live_events,
)
from ..utils import parse_time
from .switches import is_master_on

GROUP_PUSH_DELAY_MIN_SECONDS = 3.0
GROUP_PUSH_DELAY_MAX_SECONDS = 5.0


async def _wait_between_group_pushes() -> None:
    delay = random.uniform(
        GROUP_PUSH_DELAY_MIN_SECONDS,
        GROUP_PUSH_DELAY_MAX_SECONDS,
    )
    logger.info(f"[Live通知] 等待 {delay:.1f} 秒后推送下一个群")
    await asyncio.sleep(delay)



async def check_upcoming_lives(bot: Bot):
    """检查即将开始的直播活动，提前15分钟推送提醒。"""
    now_utc = datetime.now(timezone.utc)
    events = get_live_events()
    if not events:
        return

    changed = False
    for ev in events:
        if ev.notified or not ev.is_precise:
            continue

        start_time = parse_time(ev.start_time_utc)
        if start_time is None:
            continue

        delta = start_time - now_utc
        if not (timedelta(0) <= delta <= timedelta(minutes=15)):
            continue

        member_str = "、".join(f"@{m}" for m in ev.members)
        cst_time = start_time.astimezone(CST).strftime("%Y-%m-%d %H:%M CST")

        try:
            group_list = await bot.get_group_list()
            all_groups = [g["group_id"] for g in group_list]
        except Exception as e:
            logger.error(f"[Live通知] 获取群列表失败: {e}")
            continue

        configs = get_all_group_configs()
        has_previous_target = False
        for group_id in all_groups:

            gid = str(group_id)
            cfg = next((c for c in configs if c.group_id == gid), None)
            if not is_master_on(gid):
                continue
            if cfg is None:
                cfg = GroupConfig(group_id=gid)

            subscribed = any(
                (m in CORE_MEMBERS and m not in cfg.unsubs) or m in cfg.subs
                for m in ev.members
            )
            if not subscribed:
                continue

            if has_previous_target:
                await _wait_between_group_pushes()
            has_previous_target = True

            msg = (
                f"{member_str} 的直播活动即将开始！\n"
                f"{ev.title}\n"
                f"{cst_time}"
            )
            try:
                await bot.call_api("send_group_msg", group_id=group_id, message=msg)
            except Exception as e:
                logger.warning(f"[Live通知] 发送群 {gid} 失败: {e}")

        ev.notified = True
        changed = True

    # Archive past events (re-read fresh to preserve pipeline additions)
    today_jst = now_utc.astimezone(JST).date()
    archived_ids = set()
    for ev in events:
        start_time = parse_time(ev.start_time_utc)
        if not start_time:
            continue
        is_past = (
            start_time < now_utc
            if ev.is_precise
            else start_time.astimezone(JST).date() < today_jst
        )
        if is_past:
            archive_event(ev)
            archived_ids.add(ev.event_id)
            changed = True

    if changed:
        fresh = get_live_events()
        fresh = [e for e in fresh if e.event_id not in archived_ids]
        for e in events:
            if e.event_id in archived_ids:
                continue
            for fe in fresh:
                if fe.event_id == e.event_id:
                    fe.notified = e.notified
        save_live_events(fresh)
        logger.info(f"[Live通知] 日程已更新，剩余 {len(fresh)} 个活动")

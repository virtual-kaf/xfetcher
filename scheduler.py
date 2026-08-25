from datetime import datetime

from nonebot import get_bot, logger, require

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import CST
from .core import run_tweet_pipeline
from .renderer import cleanup_old_cards
from .services import broadcast_to_groups, get_all_members
from .services.event_service import check_upcoming_lives


# During the quiet period, automatic polling is reduced to the :02 run only.
# Adjust these values directly when the server's preferred schedule changes.
IDLE_START_HOUR = 1
IDLE_END_HOUR = 9


def _is_xfetch_idle_now() -> bool:
    """Return whether the current CST hour is inside the quiet period."""
    hour = datetime.now(CST).hour
    if IDLE_START_HOUR == IDLE_END_HOUR:
        return False
    if IDLE_START_HOUR < IDLE_END_HOUR:
        return IDLE_START_HOUR <= hour < IDLE_END_HOUR
    return hour >= IDLE_START_HOUR or hour < IDLE_END_HOUR


@scheduler.scheduled_job(
    "cron",
    minute="2,22,42",
    id="xfetch_monitor",
    max_instances=1,
    coalesce=True,
)
async def check_xfetch():
    """Poll X feeds every 20 minutes; quiet hours retain only the :02 run."""
    now = datetime.now(CST)
    if _is_xfetch_idle_now() and now.minute != 2:
        logger.info("[Scheduler] xfetch idle period, skip :22/:42 check")
        return

    try:
        bot = get_bot()
    except Exception:
        logger.warning("[Scheduler] Bot not available, skip xfetch check")
        return

    try:
        members = get_all_members()
        convs = await run_tweet_pipeline(members)
        if convs:
            await broadcast_to_groups(bot, convs)
    except Exception as e:
        logger.error(f"[Scheduler] check failed: {e}", exc_info=True)


@scheduler.scheduled_job("cron", minute="*/5", id="xfetch_live")
async def check_lives():
    """Check live reminders every 5 minutes."""
    try:
        bot = get_bot()
    except Exception:
        logger.warning("[Scheduler] Bot not available, skip live check")
        return

    try:
        await check_upcoming_lives(bot)
    except Exception as e:
        logger.error(f"[Scheduler] live check failed: {e}", exc_info=True)


@scheduler.scheduled_job("cron", hour="0", minute="0", id="xfetch_card_cleanup")
async def cleanup_cards():
    """Clean up old rendered card PNGs daily at 0 AM."""
    try:
        removed = cleanup_old_cards()
        if removed > 0:
            logger.info(f"[Scheduler] Card cleanup: removed {removed} old cards")
    except Exception as e:
        logger.error(f"[Scheduler] card cleanup failed: {e}", exc_info=True)
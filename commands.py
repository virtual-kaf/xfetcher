import asyncio
import time
from datetime import datetime, timezone

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import CommandArg

from .calendar_view import build_month_view
from .config import OPTIONAL_MEMBERS
from .core import run_tweet_pipeline
from .renderer import (
    render_calendar_card,
    render_sublist_card,
)
from .services import broadcast_to_groups, get_all_members, subscribe, unsubscribe
from .services.image_sender import image_cq_from_path, image_segment_from_path
from .services.switches import is_master_on
from .storage import STATUS_FILE, get_group_config, get_live_events, save_group_config


def _is_group_admin(event: Event) -> bool:
    """Return whether a group owner or administrator sent the event."""
    return (
        isinstance(event, GroupMessageEvent)
        and event.sender.role in {"owner", "admin"}
    )


# ===== 订阅 =====

sub_cmd = on_command("kabubu subscribe", aliases={"/kabubu subscribe", "kabubu 订阅", "订阅"},
                     priority=1, block=True)


@sub_cmd.handle()
async def handle_sub(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if not is_master_on(event.group_id):
        await sub_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")
    target = args.extract_plain_text().strip().lstrip("@")
    if not target:
        allowed = "、".join(m for m in OPTIONAL_MEMBERS if m != "Meeimme")
        await sub_cmd.finish(f"请提供要订阅的 ID，例如：/kabubu subscribe @id\n可选：{allowed}")
    msg = subscribe(str(event.group_id), target)
    await sub_cmd.finish(msg)


# ===== 取消订阅 =====

unsub_cmd = on_command("kabubu unsubscribe",
                       aliases={"/kabubu unsubscribe", "kabubu 取消订阅", "取消订阅"},
                       priority=1, block=True)


@unsub_cmd.handle()
async def handle_unsub(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if not is_master_on(event.group_id):
        await unsub_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")
    target = args.extract_plain_text().strip().lstrip("@")
    if not target:
        await unsub_cmd.finish("请提供要取消订阅的 ID")
    msg = unsubscribe(str(event.group_id), target)
    await unsub_cmd.finish(msg)



# ===== 订阅列表 =====

sublist_cmd = on_command("kabubu sublist", aliases={"/kabubu sublist", "sublist", "/sublist"},
                         priority=1, block=True)


@sublist_cmd.handle()
async def handle_sublist(bot: Bot, event: GroupMessageEvent):
    gid = str(event.group_id)
    if not is_master_on(gid):
        await sublist_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")
    cfg = get_group_config(gid)
    from .config import CORE_MEMBERS
    active_core = [m for m in CORE_MEMBERS if m not in cfg.unsubs]
    muted_core = [m for m in CORE_MEMBERS if m in cfg.unsubs]
    extra_subs = cfg.subs

    if not active_core and not muted_core and not extra_subs:
        await sublist_cmd.finish("暂无订阅")

    path = await render_sublist_card(active_core, muted_core, extra_subs)
    if path:
        image = image_segment_from_path(path)
        if image is None:
            await sublist_cmd.finish("读取图片失败")
        await sublist_cmd.finish(image)
    else:
        await sublist_cmd.finish("渲染失败")

# ===== 手动更新 =====

update_cmd = on_command("kabubu update", aliases={"/kabubu update", "updatex", "/updatex"},
                        priority=1, block=True)


@update_cmd.handle()
async def handle_update(bot: Bot, event: Event):
    if not _is_group_admin(event):
        await update_cmd.finish("只有群主或管理员可以手动更新 kabubu")

    if not is_master_on(event.group_id):
        await update_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")

    try:
        members = get_all_members()
        await update_cmd.send(f"? 正在检查 {len(members)} 个账号的动态...")
        convs = await run_tweet_pipeline(members)
        if convs:
            await broadcast_to_groups(bot, convs)
            await update_cmd.finish(f"? 检查完成，推送了 {len(convs)} 条动态")
        else:
            await update_cmd.finish("检查完成，暂无新动态")
    except Exception as e:
        logger.error(f"手动更新失败: {e}", exc_info=True)
        await update_cmd.finish(f"? 更新失败: {e}")



# ===== 手动拉取（本地缓存卡片） =====

# Per-group fetch cooldown: 120 seconds
_fetch_cooldown: dict[str, float] = {}

fetch_cmd = on_command("kabubu fetch", aliases={"/kabubu fetch", "fetchx", "/fetchx"},
                       priority=1, block=True)


@fetch_cmd.handle()
async def handle_fetch(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    gid = str(event.group_id)

    # Check cooldown
    now = time.time()
    last = _fetch_cooldown.get(gid, 0)
    if now - last < 120:
        remain = 120 - int(now - last)
        await fetch_cmd.finish(f"? 冷却中，请 {remain} 秒后再试")

    if not is_master_on(gid):
        await fetch_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")

    # Parse: @username [count]
    raw = args.extract_plain_text().strip()
    parts = raw.split()
    if not parts:
        await fetch_cmd.finish("用法：/kabubu fetch @用户名 [数量, 最多3]")
    target = parts[0].lstrip("@")
    count = 1
    if len(parts) > 1:
        try:
            count = int(parts[1])
        except ValueError:
            count = 1
    count = max(1, min(count, 3))

    # Verify member is in allowed list
    from .config import CARD_DIR, CORE_MEMBERS, OPTIONAL_MEMBERS
    if target not in CORE_MEMBERS and target not in OPTIONAL_MEMBERS:
        await fetch_cmd.finish(f"? @{target} 不在白名单内")

    _fetch_cooldown[gid] = now

    # Look up cached tweet IDs from STATUS_FILE
    import json
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    tweet_ids = status.get(target, [])
    if not tweet_ids:
        await fetch_cmd.finish(f"?? 暂无 @{target} 的缓存")

    # Find cached card PNGs (newest first)
    card_paths = []
    for tid in reversed(tweet_ids):
        for suffix in ["", "_0"]:
            card = CARD_DIR / f"{tid}{suffix}.png"
            if card.exists():
                card_paths.append(card)
                break
        if len(card_paths) >= count:
            break

    if not card_paths:
        await fetch_cmd.finish(f"?? @{target} 缓存已过期")

    # Merge forward (no extra text)
    nodes = []
    for p in card_paths:
        content = image_cq_from_path(p)
        if content is None:
            continue
        nodes.append({
            "type": "node",
            "data": {
                "name": f"@{target}",
                "uin": str(bot.self_id),
                "content": content,
            },
        })

    if not nodes:
        await fetch_cmd.finish("读取图片失败")

    try:
        await bot.call_api("send_group_forward_msg",
                           group_id=event.group_id, messages=nodes)
    except Exception as e:
        logger.warning(f"Fetch forward to {gid} failed: {e}")
        for node in nodes:
            try:
                await bot.call_api("send_group_msg", group_id=event.group_id,
                                   message=node["data"]["content"])
                await asyncio.sleep(1)
            except Exception:
                continue

    await fetch_cmd.finish()

# ===== 水帖过滤 =====

xfilter_cmd = on_command("kabubu xfilter", aliases={"/kabubu xfilter", "xfilter", "/xfilter"},
                         priority=1, block=True)


@xfilter_cmd.handle()
async def handle_xfilter(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    action = args.extract_plain_text().strip().lower()
    gid = str(event.group_id)
    if not is_master_on(gid):
        await xfilter_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")
    cfg = get_group_config(gid)

    if action in ("on", "开启"):
        cfg.filter_water = True
        save_group_config(cfg)
        await xfilter_cmd.finish("已开启水帖过滤（不推送回复/引用推文）")
    elif action in ("off", "关闭"):
        cfg.filter_water = False
        save_group_config(cfg)
        await xfilter_cmd.finish("已关闭水帖过滤（推送全部推文）")
    else:
        status = "?? 开启" if cfg.filter_water else "?? 关闭"
        await xfilter_cmd.finish(
            f"水帖过滤：{status}\n"
            "用法：/kabubu xfilter on | off\n"
            "开启后不推送回复和引用类型的推文。"
        )

# ===== 活动日程 =====

cal_cmd = on_command("kabubu calendar",
                     aliases={"/kabubu calendar", "/schedule", "日历", "/日历"},
                     priority=1, block=True)


@cal_cmd.handle()
async def handle_calendar(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if not is_master_on(event.group_id):
        await cal_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")
    page_str = args.extract_plain_text().strip()
    page = 1
    if page_str:
        try:
            page = int(page_str)
        except ValueError:
            page = 1
    page = max(page, 1)

    month_view = build_month_view(
        get_live_events(),
        page,
        datetime.now(timezone.utc),
    )
    if month_view is None:
        await cal_cmd.finish("暂无已解析的活动日程")

    path = await render_calendar_card(month_view)
    if path:
        image = image_segment_from_path(path)
        if image is None:
            await cal_cmd.finish("读取图片失败")
        await cal_cmd.finish(image)
    else:
        await cal_cmd.finish("渲染失败")

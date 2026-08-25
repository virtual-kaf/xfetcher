import asyncio
import random

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from ..config import CORE_MEMBERS
from ..models.group import GroupConfig
from ..models.tweet import TweetConversation
from ..storage import get_all_group_configs
from ..renderer import render_conversation_card
from .image_sender import image_cq_from_path, image_segment_from_path
from .switches import is_master_on


GROUP_PUSH_DELAY_MIN_SECONDS = 3.0
GROUP_PUSH_DELAY_MAX_SECONDS = 5.0


async def _wait_between_group_pushes() -> None:
    delay = random.uniform(
        GROUP_PUSH_DELAY_MIN_SECONDS,
        GROUP_PUSH_DELAY_MAX_SECONDS,
    )
    logger.info(f"Waiting {delay:.1f}s before pushing to the next group")
    await asyncio.sleep(delay)


def _is_water_post(conv: TweetConversation) -> bool:
    """Return whether a conversation is a short, unquoted reply."""
    return bool(
        conv.target
        and conv.target.is_reply
        and not conv.quote
        and len(conv.target.text) <= 100
    )


def _build_image_nodes(subscribed_infos: list[tuple], bot_id: str) -> list[dict]:
    """Build image-only merge-forward nodes."""
    nodes: list[dict] = []
    for _conv, paths, member_handle, _is_water in subscribed_infos:
        for path in paths:
            content = image_cq_from_path(path)
            if content is None:
                continue
            nodes.append({
                "type": "node",
                "data": {
                    "name": f"@{member_handle}",
                    "uin": bot_id,
                    "content": content,
                },
            })

    return nodes


async def broadcast_to_groups(bot: Bot, conversations: list[TweetConversation]):
    """Render cards and broadcast to all subscribed groups via merge forwarding."""
    if not conversations:
        return

    try:
        group_list = await bot.get_group_list()
        all_groups = [g["group_id"] for g in group_list]
    except Exception as e:
        logger.error(f"Get group list failed: {e}")
        return

    # Render serially. Chromium screenshots are memory-heavy and this plugin
    # shares a small host with the WebUI.
    all_card_paths = []
    for conv in conversations:
        try:
            all_card_paths.append(await render_conversation_card(conv))
        except Exception as e:
            logger.error(f"Render card failed: {e}")
            all_card_paths.append([])

    # Build conv->member mapping
    conv_infos = []
    for conv, paths in zip(conversations, all_card_paths):
        if not paths:
            continue
        target = conv.target
        member_handle = target.author.screen_name if target else "unknown"
        conv_infos.append((conv, paths, member_handle))

    if not conv_infos:
        return

    has_previous_target = False
    for group_id in all_groups:

        gid = str(group_id)
        if not is_master_on(gid):
            continue
        cfg_list = get_all_group_configs()
        group_cfg = next((g for g in cfg_list if g.group_id == gid), None)

        # 无配置记录的新群默认开启推送
        if group_cfg is None:
            group_cfg = GroupConfig(group_id=gid)

        # Filter: subscribed members + water post filter
        subscribed_infos = []
        for conv, paths, member_handle in conv_infos:
            if not ((member_handle in CORE_MEMBERS and member_handle not in group_cfg.unsubs)
                    or member_handle in group_cfg.subs):
                continue
            is_water = _is_water_post(conv)
            if group_cfg.filter_water and is_water:
                logger.debug(
                    f"[WaterFilter] Skipped water post from @{member_handle} "
                    f"(reply={conv.target.is_reply}, quote={bool(conv.quote)})"
                )
                continue
            subscribed_infos.append((conv, paths, member_handle, is_water))

        if not subscribed_infos:
            continue

        if has_previous_target:
            await _wait_between_group_pushes()
        has_previous_target = True

        if len(subscribed_infos) == 1 and not subscribed_infos[0][3]:
            _conv, paths, member_handle, _is_water = subscribed_infos[0]
            sent_count = 0
            for path in paths:
                image = image_segment_from_path(path)
                if image is None:
                    continue
                try:
                    await bot.call_api(
                        "send_group_msg",
                        group_id=group_id,
                        message=image,
                    )
                    sent_count += 1
                except Exception as e:
                    logger.warning(
                        f"Direct image send to group {gid} failed: {e}"
                    )
            logger.info(
                f"Sent {sent_count}/{len(paths)} direct image(s) "
                f"from @{member_handle} "
                f"to group {gid}"
            )
            continue

        # Multiple posts, or one allowed water post: merge images only.
        nodes = _build_image_nodes(subscribed_infos, str(bot.self_id))

        if not nodes:
            continue

        try:
            await bot.call_api("send_group_forward_msg",
                               group_id=group_id, messages=nodes)
            logger.info(f"Sent merge forward ({len(nodes)} nodes) to group {gid}")
        except Exception as e:
            logger.warning(
                f"Merge forward to group {gid} failed (likely too large): {e}. "
                f"Falling back to individual sends."
            )
            # Fallback: send images one by one
            for node in nodes:
                try:
                    await bot.call_api(
                        "send_group_msg", group_id=group_id,
                        message=node["data"]["content"]
                    )
                    await asyncio.sleep(2)
                except Exception as e2:
                    logger.warning(f"Fallback send to group {gid} failed: {e2}")
                    continue

import asyncio
import random
from urllib.parse import urlsplit

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from ..config import CORE_MEMBERS
from ..models.group import GroupConfig
from ..models.tweet import TweetConversation
from ..renderer import render_conversation_card
from ..storage import get_all_group_configs
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
    return [node for _conv, node in _build_image_node_pairs(subscribed_infos, bot_id)]


def _build_image_node_pairs(
    subscribed_infos: list[tuple], bot_id: str
) -> list[tuple[TweetConversation, dict]]:
    """Build image nodes together with their source conversation."""
    nodes: list[tuple[TweetConversation, dict]] = []
    for _conv, paths, member_handle, _is_water in subscribed_infos:
        for path in paths:
            content = image_cq_from_path(path)
            if content is None:
                continue
            nodes.append((_conv, {
                "type": "node",
                "data": {
                    "name": f"@{member_handle}",
                    "uin": bot_id,
                    "content": content,
                },
            }))

    return nodes


def _displayed_items(conv: TweetConversation):
    """Yield the same conversation items used by the conversation renderer."""
    seen: set[str] = set()
    for item in [*conv.ancestors, conv.target, conv.quote]:
        if item is not None and item.id not in seen:
            seen.add(item.id)
            yield item


def _fallback_media_content(media) -> str | None:
    """Create a OneBot image reference without refetching tweet data."""
    source = media.thumbnail_url if media.type == "video" else media.url
    parsed = urlsplit(source)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not (
        host == "pbs.twimg.com" or host.endswith(".twimg.com")
    ):
        return None
    try:
        return str(MessageSegment.image(source))
    except Exception as exc:  # noqa: BLE001 - one media must not abort a post
        logger.warning(
            "[XfetchBroadcast] fallback media skipped url={} error={}",
            source[:120],
            exc,
        )
        return None


def _fallback_nodes(conv: TweetConversation, bot_id: str) -> list[dict]:
    """Build text and remote-media nodes from an already prepared conversation."""
    nodes: list[dict] = []
    for item in _displayed_items(conv):
        handle = item.author.screen_name or "xfetch"
        parts = [item.text.strip()]
        if item.translated_text.strip():
            parts.extend(("\n\n译文：", item.translated_text.strip()))
        if item.url:
            parts.extend(("\n\n", item.url))
        for media in item.media:
            content = _fallback_media_content(media)
            if content is None:
                source = media.thumbnail_url if media.type == "video" else media.url
                logger.warning(
                    "[XfetchBroadcast] fallback media skipped tweet={} url={}",
                    item.id,
                    source[:120],
                )
                continue
            parts.extend(("\n", content))
        content = "".join(parts).strip()
        if not content:
            continue
        nodes.append({
            "type": "node",
            "data": {"name": f"@{handle}", "uin": bot_id, "content": content},
        })
    return nodes


async def _send_renderer_fallback(
    bot: Bot, group_id: int, conv: TweetConversation
) -> bool:
    nodes = _fallback_nodes(conv, str(bot.self_id))
    tweet_id = conv.target.id if conv.target is not None else "unknown"
    if not nodes:
        logger.warning(
            "[XfetchBroadcast] renderer fallback has no sendable nodes group={} tweet={}",
            group_id,
            tweet_id,
        )
        return False
    try:
        await bot.call_api(
            "send_group_forward_msg", group_id=group_id, messages=nodes
        )
    except Exception:  # noqa: BLE001 - delivery remains pending on failure
        logger.exception(
            "[XfetchBroadcast] renderer fallback forward failed group={} tweet={}",
            group_id,
            tweet_id,
        )
        return False
    logger.info(
        "[XfetchBroadcast] renderer fallback forward sent group={} tweet={} nodes={}",
        group_id,
        tweet_id,
        len(nodes),
    )
    return True


async def broadcast_to_groups(
    bot: Bot, conversations: list[TweetConversation]
) -> list[TweetConversation]:
    """Render cards and broadcast to all subscribed groups via merge forwarding."""
    if not conversations:
        return []

    try:
        group_list = await bot.get_group_list()
        all_groups = [g["group_id"] for g in group_list]
    except Exception as e:
        logger.error(f"Get group list failed: {e}")
        return []

    # Render serially so isolated Pillow workers cannot overlap on a small host.
    all_card_paths = []
    renderer_failed: set[int] = set()
    for conv in conversations:
        try:
            paths = await render_conversation_card(conv)
        except Exception as e:
            logger.exception(
                "[XfetchBroadcast] renderer failed tweet={} error={}",
                conv.target.id if conv.target else "unknown",
                e,
            )
            paths = []
        if not paths:
            renderer_failed.add(id(conv))
            logger.warning(
                "[XfetchBroadcast] renderer produced no cards tweet={}",
                conv.target.id if conv.target else "unknown",
            )
        all_card_paths.append(paths)

    has_previous_target = False
    delivered: dict[int, TweetConversation] = {}
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
        fallback_conversations = []
        for conv, paths in zip(conversations, all_card_paths):
            target = conv.target
            member_handle = target.author.screen_name if target else "unknown"
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
            if id(conv) in renderer_failed:
                fallback_conversations.append(conv)
            else:
                subscribed_infos.append((conv, paths, member_handle, is_water))

        if not subscribed_infos and not fallback_conversations:
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
            if sent_count:
                delivered[id(_conv)] = _conv
        elif subscribed_infos:
            # Multiple posts, or one allowed water post: merge images only.
            node_pairs = _build_image_node_pairs(subscribed_infos, str(bot.self_id))
            nodes = [node for _conv, node in node_pairs]
            if nodes:
                try:
                    await bot.call_api("send_group_forward_msg",
                                       group_id=group_id, messages=nodes)
                    logger.info(f"Sent merge forward ({len(nodes)} nodes) to group {gid}")
                    for conv, _node in node_pairs:
                        delivered[id(conv)] = conv
                except Exception as e:
                    logger.warning(
                        f"Merge forward to group {gid} failed (likely too large): {e}. "
                        f"Falling back to individual sends."
                    )
                    # Existing card-forward fallback: send images one by one.
                    for conv, node in node_pairs:
                        try:
                            await bot.call_api(
                                "send_group_msg", group_id=group_id,
                                message=node["data"]["content"]
                            )
                            delivered[id(conv)] = conv
                            await asyncio.sleep(2)
                        except Exception as e2:
                            logger.warning(f"Fallback send to group {gid} failed: {e2}")

        for conv in fallback_conversations:
            if await _send_renderer_fallback(bot, group_id, conv):
                delivered[id(conv)] = conv

    return list(delivered.values())

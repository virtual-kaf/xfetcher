"""Debug commands for nonebot_plugin_xfetch.

/kabubu generate <twitter_url>  — fetch + render a single tweet card
"""
import re

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg

from .clients.deepseek import translate_and_review_batch
from .clients.fxtwitter import fetch_conversation
from .renderer import render_conversation_card
from .services.image_sender import image_segment_from_path
from .services.switches import is_master_on

gen_cmd = on_command("kabubu generate", aliases={"/kabubu generate"}, priority=1, block=True)


@gen_cmd.handle()
async def handle_generate(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if not is_master_on(event.group_id):
        await gen_cmd.finish("本群未开启 xfetch，请先使用 /kabubu on")
    url = args.extract_plain_text().strip()
    if not url:
        await gen_cmd.finish("用法: /kabubu generate <推特链接>")

    tid = _parse_tweet_id(url)
    if not tid:
        await gen_cmd.finish("❌ 无法解析推特链接")

    await gen_cmd.send(f"🔍 正在获取 {tid} ...")

    try:
        conv = fetch_conversation(tid)
    except Exception as e:
        logger.error(f"[Debug] fetch failed: {e}")
        await gen_cmd.finish(f"❌ 获取失败: {e}")

    if not conv or not conv.target:
        await gen_cmd.finish("❌ 无法解析推文内容")

    # Translate
    items = []
    if conv.target:
        items.append((f"{tid}:target", conv.target.text, True))
    if conv.root and conv.root.id != (conv.target.id if conv.target else ""):
        items.append((f"{tid}:root", conv.root.text, False))
    if conv.quote:
        items.append((f"{tid}:quote", conv.quote.text, False))

    if items:
        try:
            translations, reviews, _ = (
                await translate_and_review_batch(items)
            )
        except Exception as e:
            logger.warning(f"[Debug] translate failed: {e}")
            translations, reviews = {}, {}

        if conv.target:
            conv.target.translated_text = translations.get(f"{tid}:target", "")

    # Render
    paths = await render_conversation_card(conv)
    if not paths:
        await gen_cmd.finish("❌ 渲染失败")

    # Send
    for path in paths:
        image = image_segment_from_path(path)
        if image is None:
            await gen_cmd.finish("❌ 读取图片失败")
        await gen_cmd.send(image)

    await gen_cmd.finish()


def _parse_tweet_id(url: str) -> str:
    m = re.search(r"(?:twitter\.com|x\.com|fxtwitter\.com)/\w+/status/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else ""

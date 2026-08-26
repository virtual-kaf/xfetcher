"""Generate the deterministic xfetch Pillow acceptance card for ASU."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from nonebot_plugin_xfetch.clients.fxtwitter import fetch_conversation
from nonebot_plugin_xfetch.renderer import engine as xfetch_renderer

TWEET_ID = "2091513722498105515"
TARGET_TRANSLATION = """╭────────────╮
  🌻 明透 五周年纪念 🌻
       官方周边
╰────v───────╯

✧  https://x.gd/rpLQ7
✧  预约截止：9月7日（周一）13:00

出道五年了，
真的非常感谢大家一直以来的支持……！

这次除了使用五周年 KV 制作的商品外，
还策划了许多能让大家感受到我一路走来音乐轨迹的周边🎈
（MV 主题周边!!!!!✨✨）

而且这次竟然还有我的【亲笔签名】✏️！
我要写啦写啦（数量限定，抱歉啦!!!!!!!!!!）

这些都是只有现在、只有在这里才能买到的商品！
请一定拿到手看看哦🌟

#明透5周年"""


def _use_acceptance_directories(root: Path) -> None:
    xfetch_renderer.CARD_DIR = root
    xfetch_renderer.RENDER_CACHE_DIR = root / ".cache" / "xfetch"
    xfetch_renderer.MEDIA_CACHE_DIR = xfetch_renderer.RENDER_CACHE_DIR / "media"
    xfetch_renderer.EMOJI_CACHE_DIR = xfetch_renderer.RENDER_CACHE_DIR / "emoji"
    xfetch_renderer.TEMP_DIR = xfetch_renderer.RENDER_CACHE_DIR / "tmp"


async def render(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    _use_acceptance_directories(output_dir)

    conversation = await asyncio.to_thread(fetch_conversation, TWEET_ID)
    if conversation.target is None:
        raise RuntimeError("FxTwitter did not return the acceptance target")

    conversation.target.translated_text = TARGET_TRANSLATION

    paths = await xfetch_renderer.render_conversation_card(conversation)
    if not paths:
        raise RuntimeError("xfetch acceptance render failed")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "acceptance",
    )
    args = parser.parse_args()
    paths = asyncio.run(render(args.output_dir.resolve()))
    print(*(str(path) for path in paths), sep="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

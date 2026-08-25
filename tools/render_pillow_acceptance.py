"""Generate the deterministic xfetch Pillow acceptance card for Sooda."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from nonebot_plugin_xfetch.clients.fxtwitter import fetch_conversation
from nonebot_plugin_xfetch.renderer import engine as xfetch_renderer

TWEET_ID = "2090751763116863742"
TARGET_TRANSLATION = """#V_UTAMATSURI_LIVE 终于就是明天了……现在已经既紧张又期待……！
会场票和线上直播票都仍在销售中！
无论是在现场还是通过直播，如果能和大家一起度过愉快的时光，我都会非常开心！！
我会全力以赴！！💪
准备来到现场的各位，也请别忘了防暑和补充水分，路上注意安全🫧
期待与大家见面☺️"""

QUOTE_TRANSLATION = """˗ˏˋ #V_UTAMATSURI_LIVE 艺人介绍 ˎˊ˗

🎤 空爽（@CIEL_VanillaSky @sooda_oda）
由虚拟歌手 CIEL 与创作歌手 Sooda 组成的新概念虚拟音乐组合。

🎧 PICK UP SONG
《透明流星狂想曲》
https://www.youtube.com/watch?v=cU9dcS-MXL4&list=RDcU9dcS-MXL4&start_radio=1

为大家介绍来自空爽的留言！
这是空爽的出道曲。副歌部分有朗朗上口的舞蹈，
希望大家能一起跟随节奏，享受这首歌。

🔽现场门票
日本国内：https://eplus.jp/vsingerutamatsuri/
海外：https://eplus.tickets/vsingerutamatsuri/
※达到规定数量后将结束销售。

🔽直播票
https://zan-live.com/ja/live/detail/10922

🔽活动详情
https://rkmusic.jp/info/843/

#V_UTAMATSURI_LIVE"""


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
    if conversation.quote is not None:
        conversation.quote.translated_text = QUOTE_TRANSLATION

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

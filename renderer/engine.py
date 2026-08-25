"""Jinja2 + Playwright HTML 截图渲染引擎。"""

import asyncio
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nonebot import logger

from ..config import CARD_DIR, XFETCH_RENDER_BROWSER_MAX_USES, XFETCH_RENDER_TIMEOUT
from ..models.tweet import TweetConversation


TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)

# Keep a single Chromium page active so full-page cards cannot starve SnowLuma.
_browser = None
_playwright = None
_browser_lock = asyncio.Lock()
_render_gate = asyncio.Semaphore(1)
_browser_uses = 0


# Full-page cards deliberately keep original remote media and natural height.

async def _allow_card_image_only(route) -> None:
    """Cards have inline CSS, so block every external resource but images."""
    if route.request.resource_type == "image":
        await route.continue_()
    else:
        await route.abort()


async def _close_browser(reason: str) -> None:
    """Drop both Chromium and its Playwright driver before a retry/recycle."""
    global _browser, _playwright, _browser_uses
    async with _browser_lock:
        browser = _browser
        playwright = _playwright
        _browser = None
        _playwright = None
        _browser_uses = 0

    if browser is not None or playwright is not None:
        logger.info(f"[Render] Releasing browser: {reason}")
    if browser is not None:
        try:
            await browser.close()
        except Exception as exc:  # Browser may already be disconnected.
            logger.warning(f"[Render] Browser close failed: {exc}")
    if playwright is not None:
        try:
            await playwright.stop()
        except Exception as exc:
            logger.warning(f"[Render] Playwright stop failed: {exc}")


async def _get_browser():
    global _browser, _playwright, _browser_uses
    async with _browser_lock:
        try:
            is_healthy = _browser is not None and _browser.is_connected()
        except Exception:
            is_healthy = False
        if is_healthy:
            return _browser

        stale_browser = _browser
        stale_playwright = _playwright
        _browser = None
        _playwright = None
        _browser_uses = 0
        if stale_browser is not None:
            try:
                await stale_browser.close()
            except Exception:
                pass
        if stale_playwright is not None:
            try:
                await stale_playwright.stop()
            except Exception:
                pass

        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--mute-audio",
                    # Use Chromium defaults for full-page cards.
                ],
            )
        except Exception:
            await playwright.stop()
            raise
        _browser = browser
        _playwright = playwright
        logger.info("[Render] Chromium started for full-page cards")
        return browser


async def _render_once(html: str, output_path: Path, width: int) -> None:
    browser = await _get_browser()
    page = None
    try:
        page = await browser.new_page(
            viewport={"width": width, "height": 900},
            device_scale_factor=1,
            java_script_enabled=False,
        )
        await page.route("**/*", _allow_card_image_only)
        await page.set_content(
            html,
            wait_until="networkidle",
            timeout=XFETCH_RENDER_TIMEOUT * 1000,
        )
        # A full-page screenshot is never shorter than the viewport. Keep the
        # initial 900px viewport while remote images load, then collapse its
        # height so short cards use their natural document height. Long cards
        # still expand normally through full_page=True.
        set_viewport_size = getattr(page, "set_viewport_size", None)
        if set_viewport_size is not None:
            await set_viewport_size({"width": width, "height": 1})
        await page.screenshot(path=str(output_path), full_page=True, type="png")
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception as exc:
                logger.debug(f"[Render] Page close failed: {exc}")


async def _html_to_png(html: str, output_path: Path, width: int = 600) -> None:
    """Render one full-page card and recreate Chromium after a broken session."""
    global _browser_uses
    async with _render_gate:
        try:
            await asyncio.wait_for(
                _render_once(html, output_path, width),
                timeout=XFETCH_RENDER_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            await _close_browser("render timeout")
            raise RuntimeError(
                f"card rendering exceeded {XFETCH_RENDER_TIMEOUT}s"
            ) from exc
        except Exception:
            # Playwright reports a WebSocket loss as a regular operation error.
            # Never reuse that browser for the next scheduled render.
            await _close_browser("render error or browser disconnect")
            raise

        _browser_uses += 1
        if _browser_uses >= XFETCH_RENDER_BROWSER_MAX_USES:
            await _close_browser("periodic memory recycle")



def _render_conversation_html(conv: TweetConversation) -> str:
    template = _env.get_template("conversation.html")
    return template.render(
        target=conv.target,
        ancestors=conv.ancestors,
        quote=conv.quote,
    )


def _render_live_html(title: str, members: list[str], start_time_display: str) -> str:
    template = _env.get_template("live_alert.html")
    return template.render(
        event={
            "title": title,
            "members": members,
            "start_time_display": start_time_display,
        }
    )


async def render_conversation_card(conv: TweetConversation) -> List[Path]:
    """将对话渲染为图片，返回 PNG 文件路径列表。"""
    CARD_DIR.mkdir(parents=True, exist_ok=True)

    if not conv.target:
        logger.warning("render_conversation_card: 无 target 推文")
        return []

    try:
        html = _render_conversation_html(conv)
        target_id = conv.target.id
        path = CARD_DIR / f"{target_id}.png"
        await _html_to_png(html, path, width=650)
        logger.info(f"渲染完成: {path}")
        return [path]
    except Exception as e:
        logger.error(f"渲染卡片失败: {e}", exc_info=True)
        return []




def _render_sublist_html(active_core: list, muted_core: list, extra_subs: list) -> str:
    template = _env.get_template("sublist.html")
    return template.render(
        active_core=active_core,
        muted_core=muted_core,
        extra_subs=extra_subs,
    )


def _render_calendar_html(month_view: dict) -> str:
    template = _env.get_template("calendar.html")
    return template.render(calendar=month_view)


async def render_sublist_card(active_core: list, muted_core: list, extra_subs: list):
    """Render subscription list as PNG."""
    import hashlib
    MISC_DIR = CARD_DIR.parent / "misc"
    MISC_DIR.mkdir(parents=True, exist_ok=True)
    try:
        html = _render_sublist_html(active_core, muted_core, extra_subs)
        key = hashlib.md5(html.encode()).hexdigest()[:8]
        path = MISC_DIR / f"sublist_{key}.png"
        await _html_to_png(html, path, width=520)
        return str(path).replace("\\", "/")
    except Exception as e:
        logger.error(f"Render sublist failed: {e}", exc_info=True)
        return None


async def render_calendar_card(month_view: dict):
    """Render calendar as PNG."""
    import hashlib
    MISC_DIR = CARD_DIR.parent / "misc"
    MISC_DIR.mkdir(parents=True, exist_ok=True)
    try:
        html = _render_calendar_html(month_view)
        key = hashlib.md5(html.encode()).hexdigest()[:8]
        path = MISC_DIR / f"calendar_{key}.png"
        await _html_to_png(html, path, width=1680)
        return str(path).replace("\\", "/")
    except Exception as e:
        logger.error(f"Render calendar failed: {e}", exc_info=True)
        return None


async def render_live_alert(title: str, members: list[str], start_time_display: str) -> Optional[Path]:
    """渲染直播通知卡片。"""
    CARD_DIR.mkdir(parents=True, exist_ok=True)

    try:
        html = _render_live_html(title, members, start_time_display)
        import hashlib
        key = hashlib.md5(f"{title}{start_time_display}".encode()).hexdigest()[:8]
        path = CARD_DIR / f"live_{key}.png"
        await _html_to_png(html, path, width=650)
        return path
    except Exception as e:
        logger.error(f"渲染直播卡片失败: {e}", exc_info=True)
        return None


async def shutdown():
    """Release Chromium and its Playwright driver on NoneBot shutdown."""
    async with _render_gate:
        await _close_browser("shutdown")

"""Isolated Pillow rendering with card cache management."""

import time
from pathlib import Path

from nonebot import logger

from .engine import render_conversation_card, render_live_alert, render_sublist_card, render_calendar_card, shutdown
from ..config import CARD_DIR


# Default card cache lifetime in seconds (24 hours)
_DEFAULT_CARD_MAX_AGE = 24 * 3600


def cleanup_old_cards(max_age_seconds: int = _DEFAULT_CARD_MAX_AGE) -> int:
    """Remove card PNGs older than max_age_seconds.
    Returns the number of files removed."""
    if not CARD_DIR.exists():
        return 0

    now = time.time()
    removed = 0
    kept = 0

    for png_file in CARD_DIR.glob("*.png"):
        try:
            age = now - png_file.stat().st_mtime
            if age > max_age_seconds:
                png_file.unlink()
                removed += 1
            else:
                kept += 1
        except Exception as e:
            logger.warning(f"[CardCleanup] Failed to remove {png_file.name}: {e}")

    if removed > 0:
        logger.info(f"[CardCleanup] Removed {removed} old cards, kept {kept}")
    return removed


__all__ = [
    "render_conversation_card",
    "render_live_alert",
    "render_sublist_card",
    "render_calendar_card",
    "shutdown",
    "cleanup_old_cards",
]

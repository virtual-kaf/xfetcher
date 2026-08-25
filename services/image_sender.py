from pathlib import Path

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment


def image_segment_from_path(path: str | Path) -> MessageSegment | None:
    """Read a local PNG and make a OneBot V11 base64 image segment."""
    image_path = Path(path)
    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        logger.warning(
            f"[ImageSend] Cannot read {image_path.name} for base64 delivery: {exc}"
        )
        return None

    if not image_bytes:
        logger.warning(
            f"[ImageSend] Refusing to send empty image file: {image_path.name}"
        )
        return None
    return MessageSegment.image(image_bytes)


def image_cq_from_path(path: str | Path) -> str | None:
    """Return a base64 CQ image string for merge-forward node content."""
    segment = image_segment_from_path(path)
    return str(segment) if segment is not None else None

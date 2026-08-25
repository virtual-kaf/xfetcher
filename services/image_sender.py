import hashlib
import os
import shutil
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

from ..config import (
    XFETCH_SHARED_IMAGE_CONTAINER_DIR,
    XFETCH_SHARED_IMAGE_HOST_DIR,
    XFETCH_SHARED_IMAGE_TTL_SECONDS,
)


_cleanup_worker_started = False
_cleanup_worker_lock = threading.Lock()
_SHARED_FILE_PREFIX = "xfetch-"


def _shared_image_uri(filename: str) -> str:
    container_path = PurePosixPath(
        XFETCH_SHARED_IMAGE_CONTAINER_DIR,
        filename,
    )
    encoded_path = quote(container_path.as_posix(), safe="/")
    return f"file://{encoded_path}"


def cleanup_expired_shared_images() -> int:
    """Remove shared copies after their configured grace period."""
    shared_dir = XFETCH_SHARED_IMAGE_HOST_DIR
    if not shared_dir.exists():
        return 0

    removed = 0
    now = time.time()
    try:
        files = list(shared_dir.iterdir())
    except OSError as exc:
        logger.warning(f"[ImageSend] Cannot scan shared image directory: {exc}")
        return 0

    owned_prefixes = (_SHARED_FILE_PREFIX, f".{_SHARED_FILE_PREFIX}")
    for shared_path in files:
        if not shared_path.is_file() or not shared_path.name.startswith(owned_prefixes):
            continue
        try:
            age = max(0.0, now - shared_path.stat().st_mtime)
            if age >= XFETCH_SHARED_IMAGE_TTL_SECONDS:
                shared_path.unlink(missing_ok=True)
                removed += 1
        except OSError as exc:
            logger.warning(
                f"[ImageSend] Cannot clean shared image {shared_path.name}: {exc}"
            )
    return removed


def _cleanup_worker() -> None:
    """Periodically sweep the shared directory using one daemon thread."""
    while True:
        interval = min(
            300.0,
            max(10.0, float(XFETCH_SHARED_IMAGE_TTL_SECONDS) / 4),
        )
        time.sleep(interval)
        removed = cleanup_expired_shared_images()
        if removed:
            logger.info(f"[ImageSend] Removed {removed} expired shared image(s)")


def _ensure_cleanup_worker() -> None:
    """Start delayed cleanup once, including recovery after process restarts."""
    global _cleanup_worker_started
    with _cleanup_worker_lock:
        if _cleanup_worker_started:
            return
        _cleanup_worker_started = True

    removed = cleanup_expired_shared_images()
    if removed:
        logger.info(f"[ImageSend] Removed {removed} expired shared image(s)")
    threading.Thread(
        target=_cleanup_worker,
        name="xfetch-shared-image-cleanup",
        daemon=True,
    ).start()


def _shared_filename(image_path: Path) -> str:
    """Avoid overwriting a delayed copy with another same-named image."""
    metadata = image_path.stat()
    source_key = os.fsencode(
        f"{image_path.resolve()}\0{metadata.st_mtime_ns}\0{metadata.st_size}"
    )
    fingerprint = hashlib.sha256(source_key).hexdigest()[:12]
    return f"{_SHARED_FILE_PREFIX}{image_path.stem}-{fingerprint}{image_path.suffix}"


def _copy_to_shared_directory(image_path: Path) -> Path | None:
    """Atomically copy one rendered image into SnowLuma's shared directory."""
    shared_dir = XFETCH_SHARED_IMAGE_HOST_DIR
    try:
        shared_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(f"[ImageSend] Cannot create shared image directory: {exc}")
        return None

    temporary_path: Path | None = None
    try:
        _ensure_cleanup_worker()
        shared_name = _shared_filename(image_path)
        shared_path = shared_dir / shared_name
        temporary_path = shared_dir / (
            f".{shared_name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        if image_path.resolve() == shared_path.resolve():
            os.utime(shared_path, None)
        else:
            shutil.copyfile(image_path, temporary_path)
            temporary_path.chmod(0o644)
            temporary_path.replace(shared_path)
    except OSError as exc:
        logger.warning(
            f"[ImageSend] Cannot copy {image_path.name} to shared directory: {exc}"
        )
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        return None

    return shared_path


def image_segment_from_path(path: str | Path) -> MessageSegment | None:
    """Copy a rendered image to SnowLuma and return its container file URI."""
    image_path = Path(path)
    try:
        is_valid = image_path.is_file() and image_path.stat().st_size > 0
    except OSError as exc:
        logger.warning(
            f"[ImageSend] Cannot inspect image {image_path.name}: {exc}"
        )
        return None

    if not is_valid:
        logger.warning(
            f"[ImageSend] Refusing to share missing or empty image: {image_path.name}"
        )
        return None

    shared_path = _copy_to_shared_directory(image_path)
    if shared_path is None:
        return None
    return MessageSegment.image(_shared_image_uri(shared_path.name))


def image_cq_from_path(path: str | Path) -> str | None:
    """Return a shared-file CQ image string for merge-forward content."""
    segment = image_segment_from_path(path)
    return str(segment) if segment is not None else None

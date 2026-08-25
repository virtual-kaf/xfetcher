import os

from nonebot_plugin_xfetch.services import image_sender


def _configure_shared_dir(monkeypatch, tmp_path):
    shared_dir = tmp_path / "shared"
    monkeypatch.setattr(
        image_sender,
        "XFETCH_SHARED_IMAGE_HOST_DIR",
        shared_dir,
    )
    monkeypatch.setattr(
        image_sender,
        "XFETCH_SHARED_IMAGE_CONTAINER_DIR",
        "/app/data/xfetch_cards",
    )
    monkeypatch.setattr(image_sender, "_ensure_cleanup_worker", lambda: None)
    return shared_dir


def test_image_sender_copies_png_and_uses_container_file_uri(monkeypatch, tmp_path):
    shared_dir = _configure_shared_dir(monkeypatch, tmp_path)
    path = tmp_path / "card.png"
    path.write_bytes(b"png-bytes")

    segment = image_sender.image_segment_from_path(path)
    content = image_sender.image_cq_from_path(path)

    assert segment is not None
    assert segment.data["file"].startswith(
        "file:///app/data/xfetch_cards/xfetch-card-"
    )
    assert segment.data["file"].endswith(".png")
    assert content is not None
    assert segment.data["file"] in content
    assert "base64://" not in content
    copies = list(shared_dir.glob("xfetch-card-*.png"))
    assert len(copies) == 1
    assert copies[0].read_bytes() == b"png-bytes"


def test_image_sender_skips_missing_file(monkeypatch, tmp_path):
    _configure_shared_dir(monkeypatch, tmp_path)
    missing = tmp_path / "missing.png"

    assert image_sender.image_segment_from_path(missing) is None
    assert image_sender.image_cq_from_path(missing) is None


def test_expired_shared_images_are_removed(monkeypatch, tmp_path):
    shared_dir = _configure_shared_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(image_sender, "XFETCH_SHARED_IMAGE_TTL_SECONDS", 60)
    shared_dir.mkdir()
    expired = shared_dir / "xfetch-expired.png"
    expired.write_bytes(b"old")
    old_timestamp = expired.stat().st_mtime - 120
    os.utime(expired, (old_timestamp, old_timestamp))

    assert image_sender.cleanup_expired_shared_images() == 1
    assert not expired.exists()

from nonebot_plugin_xfetch.services.image_sender import (
    image_cq_from_path,
    image_segment_from_path,
)


def test_image_sender_reads_png_as_base64(tmp_path):
    path = tmp_path / "card.png"
    path.write_bytes(b"png-bytes")

    segment = image_segment_from_path(path)
    content = image_cq_from_path(path)

    assert segment is not None
    assert segment.data["file"].startswith("base64://")
    assert content is not None
    assert "base64://" in content
    assert "file:///" not in content


def test_image_sender_skips_missing_file(tmp_path):
    missing = tmp_path / "missing.png"

    assert image_segment_from_path(missing) is None
    assert image_cq_from_path(missing) is None

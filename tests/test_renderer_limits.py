import asyncio
import io
import math
import sys
from pathlib import Path

import httpx
import pytest
from nonebot_plugin_xfetch.models.tweet import (
    TweetAuthor,
    TweetConversation,
    TweetItem,
)
from nonebot_plugin_xfetch.renderer import engine, pillow_worker
from PIL import Image, ImageDraw, ImageFont


def _pango_runtime_available() -> bool:
    try:
        import cairo  # noqa: F401
        import gi

        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo  # noqa: F401
    except (ImportError, ValueError, OSError):
        return False
    return True


requires_pango = pytest.mark.skipif(
    not _pango_runtime_available(),
    reason="PangoCairo/fontconfig integration tests require the target Linux runtime",
)


def _hybrid_backend(known_emojis: set[str] | None = None):
    return pillow_worker._HybridTextBackend(
        engine.FONT_PATH, known_emojis or set(), Image, ImageFont
    )


class _FakePangoBackend:
    def graphemes(self, text: str) -> list[str]:
        return pillow_worker._fallback_graphemes(text)

    def run_metrics(self, text: str, size: int, _component: str):
        advance = len(self.graphemes(text)) * size * 0.7
        return advance, (0.0, -size * 0.8, advance, size * 0.2)


def _tweet(tweet_id: str, text: str = "post") -> TweetItem:
    return TweetItem(
        id=tweet_id,
        url=f"https://x.com/test/status/{tweet_id}",
        author=TweetAuthor(name="测试作者", screen_name="tester"),
        text=text,
        translated_text="这是翻译",
        likes=3,
        retweets=2,
        replies=1,
        views=10,
    )


def _worker_spec(tmp_path: Path, *, max_height: int = 4096) -> dict:
    target = _tweet("target", "日文と中文 mixed text。" * 140)
    return {
        "kind": "conversation",
        "output_path": str(tmp_path / "card.png"),
        "format": "PNG",
        "font_path": str(engine.FONT_PATH),
        "emoji_paths": {},
        "memory_limit_mb": 384,
        "max_image_pixels": 12_000_000,
        "max_height": max_height,
        "ancestors": [],
        "target": engine._tweet_spec(target, {}),
        "quote": None,
    }


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(output, format="PNG")
    return output.getvalue()


def test_twitter_media_url_is_bounded_without_changing_other_hosts():
    source = "https://pbs.twimg.com/media/card.jpg?format=jpg&name=orig"

    bounded = engine._bounded_image_url(source)

    assert "format=jpg" in bounded
    assert "name=medium" in bounded
    assert "name=orig" not in bounded
    assert engine._bounded_image_url("https://example.com/a.jpg?name=orig") == (
        "https://example.com/a.jpg?name=orig"
    )


@pytest.mark.asyncio
async def test_remote_image_is_streamed_validated_and_cached(tmp_path):
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=_png_bytes()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await engine._download_image(
            client, "https://example.com/image", tmp_path
        )
        second = await engine._download_image(
            client, "https://example.com/image", tmp_path
        )

    assert first == second
    assert calls == 1
    engine._validate_image(first)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "content"),
    [
        ({"content-type": "text/html"}, b"no"),
        ({"content-type": "image/png"}, b"broken"),
        ({"content-type": "image/png", "content-length": str(30_000_000)}, b""),
    ],
)
async def test_remote_image_failures_degrade_to_empty_path(tmp_path, headers, content):
    def handler(_request):
        return httpx.Response(200, headers=headers, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        path = await engine._safe_download(
            client, "https://example.com/image", tmp_path
        )

    assert path == ""


@requires_pango
@pytest.mark.asyncio
async def test_isolated_worker_paginates_without_truncating(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "TEMP_DIR", tmp_path / "worker-temp")

    paths = await engine._run_worker(_worker_spec(tmp_path, max_height=900))

    assert len(paths) >= 2
    assert all(path.exists() for path in paths)
    assert all(Image.open(path).width == 800 for path in paths)
    assert all(Image.open(path).height <= 900 for path in paths)


@requires_pango
@pytest.mark.asyncio
async def test_worker_timeout_terminates_process_and_cleans_manifest(
    tmp_path, monkeypatch
):
    slow_worker = tmp_path / "slow_worker.py"
    slow_worker.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    worker_temp = tmp_path / "worker-temp"
    real_worker = engine.WORKER_PATH
    monkeypatch.setattr(engine, "WORKER_PATH", slow_worker)
    monkeypatch.setattr(engine, "TEMP_DIR", worker_temp)
    monkeypatch.setattr(engine, "XFETCH_RENDER_TIMEOUT", 0.05)

    with pytest.raises(asyncio.TimeoutError):
        await engine._run_worker(_worker_spec(tmp_path))

    assert not engine._active_processes
    assert not list(worker_temp.glob("*.json"))

    # A timed-out child has no reusable state; the next render starts cleanly.
    monkeypatch.setattr(engine, "WORKER_PATH", real_worker)
    monkeypatch.setattr(engine, "XFETCH_RENDER_TIMEOUT", 10)
    paths = await engine._run_worker(_worker_spec(tmp_path))
    assert paths and paths[0].is_file()


@pytest.mark.asyncio
async def test_shutdown_terminates_an_active_worker():
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    engine._active_processes.add(process)

    await engine.shutdown()

    engine._active_processes.discard(process)
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_conversation_spec_keeps_all_text_and_translation(monkeypatch, tmp_path):
    captured = {}
    ancestor = _tweet("ancestor", "ancestor text")
    target = _tweet("target", "x" * 2500)
    quote = _tweet("quote", "quoted text")

    async def resolve(_urls, _texts):
        return {}, {}

    async def run(spec):
        captured.update(spec)
        output = tmp_path / "captured.png"
        Image.new("RGB", (10, 10)).save(output)
        return [output]

    monkeypatch.setattr(engine, "CARD_DIR", tmp_path)
    monkeypatch.setattr(engine, "_resolve_assets", resolve)
    monkeypatch.setattr(engine, "_run_worker", run)

    paths = await engine.render_conversation_card(
        TweetConversation(ancestors=[ancestor], target=target, quote=quote)
    )

    assert paths
    assert captured["ancestors"][0]["text"] == "ancestor text"
    assert captured["target"]["text"] == "x" * 2500
    assert captured["target"]["translated_text"] == "这是翻译"
    assert captured["quote"]["translated_text"] == "这是翻译"


@requires_pango
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sublist", "live", "calendar"])
async def test_auxiliary_pillow_cards_render(kind, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "TEMP_DIR", tmp_path / "worker-temp")
    spec = engine._base_spec(kind, tmp_path / f"{kind}.png", {})
    if kind == "sublist":
        spec.update(active_core=["Sooda_oda"], muted_core=[], extra_subs=["other"])
    elif kind == "live":
        spec.update(
            title="测试直播", members=["Sooda_oda"], start_time_display="20:00 CST"
        )
    else:
        day = {"day": 1, "in_month": True, "is_today": True, "events": []}
        spec["calendar"] = {
            "month_label": "AUGUST 2026",
            "month_title": "2026年8月",
            "date_range": "08/01 - 08/31",
            "weeks": [[dict(day, day=index + 1) for index in range(7)]],
            "page": 1,
            "total_pages": 1,
            "month_event_count": 0,
            "total_event_count": 0,
        }

    paths = await engine._run_worker(spec)

    assert len(paths) == 1
    with Image.open(paths[0]) as rendered:
        assert rendered.width == (1680 if kind == "calendar" else 800)


def test_renderer_has_no_browser_or_template_dependency():
    source = Path(engine.__file__).read_text(encoding="utf-8")
    assert "playwright" not in source.casefold()
    assert "jinja" not in source.casefold()
    assert "wait_until" not in source


def test_render_spec_delegates_fallback_to_pango(tmp_path):
    spec = engine._base_spec("conversation", tmp_path / "card.png", {})

    assert spec["font_path"] == str(engine.FONT_PATH)
    assert "fallback_font_paths" not in spec
    assert not hasattr(engine, "FALLBACK_FONT_PATHS")

    renderer_source = Path(pillow_worker.__file__).read_text(encoding="utf-8")
    for category_font in (
        "NotoSansMath-Regular.ttf",
        "NotoSansGujarati-Regular.ttf",
        "NotoSerifTibetan-Regular.ttf",
        "NotoSansGeorgian-Regular.ttf",
    ):
        assert category_font not in renderer_source
        assert category_font not in Path(engine.__file__).read_text(encoding="utf-8")


def test_twemoji_fallback_keeps_complete_graphemes_and_ignores_plain_digits():
    values = engine._fallback_emoji_list(
        "release * 6 of 9; *️⃣ 6️⃣ 9️⃣ 👨🏽‍💻 🏳️‍🌈 🇨🇳"
    )

    assert "*" not in values
    assert "6" not in values
    assert "9" not in values
    assert {"*️⃣", "6️⃣", "9️⃣", "👨🏽‍💻", "🏳️‍🌈", "🇨🇳"} <= set(values)
    assert engine._twemoji_url("*️⃣").endswith("/2a-20e3.png")
    assert engine._twemoji_url("❤️").endswith("/2764.png")
    assert engine._twemoji_url("🏳️‍🌈").endswith(
        "/1f3f3-fe0f-200d-1f308.png"
    )


def test_renderer_size_budget_fails_before_allocation(capsys):
    with pytest.raises(pillow_worker.RenderSizeError, match="MAX_CANVAS_HEIGHT"):
        pillow_worker._validate_size(
            "test", "too-tall", 800, pillow_worker.MAX_CANVAS_HEIGHT + 1, 4
        )
    with pytest.raises(pillow_worker.RenderSizeError, match="MAX_CANVAS_PIXELS"):
        pillow_worker._validate_size(
            "test", "too-many-pixels", pillow_worker.MAX_CANVAS_PIXELS + 1, 1, 4
        )

    diagnostics = capsys.readouterr().err
    assert "width=800" in diagnostics
    assert "estimated_bytes=" in diagnostics
    assert pillow_worker._pixels_to_pango(688, 1024) == 688 * 1024
    assert pillow_worker._pango_to_pixels(688 * 1024, 1024) == 688

    class OutOfMemoryImage:
        @staticmethod
        def new(_mode, _size, _color):
            raise MemoryError

    with pytest.raises(pillow_worker.RenderSizeError, match="Pillow allocation"):
        pillow_worker._new_pillow_image(
            OutOfMemoryImage, "test.image", "RGB", (800, 100), "white"
        )


def test_stats_row_uses_twitter_style_outline_icons():
    image = Image.new("L", (92, 24), 0)
    draw = ImageDraw.Draw(image)

    for index, kind in enumerate(("reply", "repost", "like", "views")):
        pillow_worker._draw_stats_icon(draw, kind, (index * 23, 1), 255)
        assert image.crop((index * 23, 0, index * 23 + 22, 24)).getbbox()

    source = Path(pillow_worker.__file__).read_text(encoding="utf-8")
    for old_icon in ('("💬"', '("🔁"', '("❤️"', '("👁"'):
        assert old_icon not in source


def test_hybrid_fast_path_matches_original_pillow_metrics_and_wrap():
    backend = _hybrid_backend()
    text_font = backend.font(21)
    sample = "普通中文 English 日本語 mixed text"
    max_width = 150

    expected_lines = []
    for paragraph in sample.replace("\t", " ").split("\n"):
        current = []
        current_width = 0
        for value in backend.tokens(paragraph):
            width = backend.token_width(value, text_font)
            if current and current_width + width > max_width:
                expected_lines.append(current)
                current = []
                current_width = 0
            current.append(value)
            current_width += width
        if current:
            expected_lines.append(current)

    assert backend.primary_covers(sample)
    assert backend.measure(sample, 21) == text_font.primary.getlength(sample)
    assert backend.bbox(sample, 21) == text_font.primary.getbbox(sample)
    assert backend.wrap(sample, text_font, max_width) == expected_lines
    assert not backend.pango_initialized


def test_hybrid_twemoji_run_does_not_initialize_pango():
    backend = _hybrid_backend({"❤️"})

    line = backend.layout_line("中文 ❤️ English", 21)

    assert [run.renderer for run in line.runs] == ["pillow", "emoji", "pillow"]
    assert not backend.pango_initialized


def test_hybrid_mixed_layout_can_be_exercised_without_local_pango():
    backend = _hybrid_backend()
    backend._pango_backend = _FakePangoBackend()
    text_font = backend.font(24)

    line = backend.layout_line("中文 कि देवनागरी English", 24)
    wrapped = backend.wrap(
        "中文 कि देवनागरी English العربية end", text_font, 220
    )

    run_kinds = [run.renderer for run in line.runs]
    assert run_kinds[0] == "pillow"
    assert "pango" in run_kinds
    assert run_kinds[-1] == "pillow"
    assert any("कि" in run.text for run in line.runs if run.renderer == "pango")
    assert all(isinstance(value, pillow_worker._HybridLine) for value in wrapped)
    assert all(value.advance <= 220 for value in wrapped)


def test_hybrid_mixed_wrap_splits_an_oversized_ascii_token():
    backend = _hybrid_backend()
    backend._pango_backend = _FakePangoBackend()
    text_font = backend.font(24)

    lines = backend.wrap("中文 देवनागरी " + "longword" * 12, text_font, 120)

    assert len(lines) > 2
    assert all(isinstance(line, pillow_worker._HybridLine) for line in lines)
    assert all(line.advance <= 120 for line in lines)


def test_fallback_graphemes_keep_marks_zwj_and_flags_together():
    clusters = pillow_worker._fallback_graphemes("कि क्\u200dष 👨🏽\u200d💻 🇨🇳")

    assert "कि" in clusters
    assert "क्\u200dष" in clusters
    assert "👨🏽\u200d💻" in clusters
    assert "🇨🇳" in clusters


@requires_pango
def test_hybrid_line_interleaves_pillow_and_pango_runs():
    backend = _hybrid_backend()
    sample = "中文 कि देवनागरी ગુજરાતી བོད་ཡིག ქართული العربية English"

    line = backend.layout_line(sample, 24)
    run_kinds = [run.renderer for run in line.runs]

    assert run_kinds[0] == "pillow"
    assert "pango" in run_kinds
    assert run_kinds[-1] == "pillow"
    assert any("कि" in run.text for run in line.runs if run.renderer == "pango")
    assert backend._pango().unknown_glyphs(sample) == 0


@requires_pango
def test_hybrid_wrap_and_render_share_advance_baseline_and_bbox():
    backend = _hybrid_backend()
    text_font = backend.font(24)
    lines = backend.wrap(
        "中文 देवनागरी English العربية ગુજરાતી end" * 3,
        text_font,
        260,
        "test.hybrid-wrap",
    )

    assert lines
    assert all(isinstance(line, pillow_worker._HybridLine) for line in lines)
    assert all(line.advance <= 260 for line in lines)

    line = lines[0]
    image = Image.new("RGBA", (340, 90), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    backend.render_line(
        image,
        draw,
        line,
        (20, 18),
        text_font,
        (15, 20, 25),
        lambda _token, _size: None,
        line_height=36,
        component="test.hybrid-render",
    )
    actual = image.getchannel("A").getbbox()
    ascent, _descent = text_font.primary.getmetrics()
    expected = (
        20 + math.floor(line.bbox[0]),
        18 + ascent + math.floor(line.bbox[1]),
        20 + math.ceil(line.bbox[2]),
        18 + ascent + math.ceil(line.bbox[3]),
    )

    assert actual is not None
    assert expected[0] <= actual[0] <= actual[2] <= expected[2]
    assert expected[1] <= actual[1] <= actual[3] <= expected[3]


@requires_pango
def test_pango_fontconfig_covers_mixed_scripts():
    backend = pillow_worker._PangoTextBackend(
        engine.FONT_PATH, set(), Image, ImageFont
    )
    sample = "中文 Devanagari देवनागरी ગુજરાતી བོད་ཡིག ქართული ∑√∞ العربية"

    assert backend.unknown_glyphs(sample) == 0


@requires_pango
def test_pango_wrap_width_uses_pango_units_once():
    backend = pillow_worker._PangoTextBackend(
        engine.FONT_PATH, set(), Image, ImageFont
    )
    layout, _prepared, _slots = backend._layout(
        "Pango width 中文 العربية " * 20,
        21,
        688,
        "test.wrap-units",
    )

    assert layout.get_width() == 688 * backend._pango_scale
    pixel_width, pixel_height = layout.get_pixel_size()
    assert 0 < pixel_width <= 688
    assert 0 < pixel_height < pillow_worker.MAX_CANVAS_HEIGHT


@requires_pango
@pytest.mark.asyncio
async def test_pango_renders_mixed_scripts_with_existing_card_geometry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(engine, "TEMP_DIR", tmp_path / "worker-temp")
    spec = _worker_spec(tmp_path)
    spec["output_path"] = str(tmp_path / "pango-mixed.png")
    spec["target"]["name"] = "明透 ქართული"
    spec["target"]["text"] = (
        "देवनागरी ગુજરાતી བོད་ཡིག ქართული ∑√∞ العربية"
    )

    output_path = (await engine._run_worker(spec))[0]

    with Image.open(output_path) as rendered:
        assert rendered.width == 800
        assert rendered.height <= spec["max_height"]


def test_alinux3_installer_pins_runtime_and_font_packages():
    source = (
        Path(__file__).parents[1]
        / "tools"
        / "install_alinux3_renderer_deps.sh"
    ).read_text(encoding="utf-8")

    assert "pycairo==1.29.0" in source
    assert "PyGObject==3.44.2" in source
    for package in (
        "pango",
        "cairo-gobject",
        "gobject-introspection",
        "google-noto-sans-devanagari-fonts",
        "google-noto-sans-gujarati-fonts",
        "google-noto-sans-tibetan-fonts",
        "google-noto-sans-georgian-fonts",
        "google-noto-sans-symbols-fonts",
        "stix-math-fonts",
    ):
        assert package in source

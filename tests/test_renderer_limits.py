import asyncio
import io
import sys
from pathlib import Path

import httpx
import pytest
from nonebot_plugin_xfetch.models.tweet import (
    TweetAuthor,
    TweetConversation,
    TweetItem,
)
from nonebot_plugin_xfetch.renderer import engine
from PIL import Image


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


@pytest.mark.asyncio
async def test_isolated_worker_paginates_without_truncating(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "TEMP_DIR", tmp_path / "worker-temp")

    paths = await engine._run_worker(_worker_spec(tmp_path, max_height=900))

    assert len(paths) >= 2
    assert all(path.exists() for path in paths)
    assert all(Image.open(path).width == 800 for path in paths)
    assert all(Image.open(path).height <= 900 for path in paths)


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

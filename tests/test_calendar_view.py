from datetime import datetime, timezone

from nonebot_plugin_xfetch.calendar_view import build_month_view
from nonebot_plugin_xfetch.models.live import LiveEvent


def _find_event_date(view: dict, event_id: str) -> str | None:
    for week in view["weeks"]:
        for day in week:
            if any(event["event_id"] == event_id for event in day["events"]):
                return day["date"]
    return None


def test_imprecise_jst_midnight_stays_on_intended_date():
    event = LiveEvent(
        event_id="morning-stream",
        title="朝雑（8月7日）",
        start_time_utc="2026-08-06T15:00:00Z",
        is_precise=False,
    )

    view = build_month_view(
        [event],
        page=1,
        now=datetime(2026, 8, 6, 12, tzinfo=timezone.utc),
    )

    assert view is not None
    assert _find_event_date(view, event.event_id) == "2026-08-07"


def test_imprecise_event_remains_visible_during_its_jst_date():
    event = LiveEvent(
        event_id="date-only",
        start_time_utc="2026-08-06T15:00:00Z",
        is_precise=False,
    )

    view = build_month_view(
        [event],
        page=1,
        now=datetime(2026, 8, 7, 12, tzinfo=timezone.utc),
    )

    assert view is not None
    assert _find_event_date(view, event.event_id) == "2026-08-07"


def test_precise_event_still_uses_cst_date():
    event = LiveEvent(
        event_id="precise",
        start_time_utc="2026-08-06T16:30:00Z",
        is_precise=True,
    )

    view = build_month_view(
        [event],
        page=1,
        now=datetime(2026, 8, 6, 12, tzinfo=timezone.utc),
    )

    assert view is not None
    assert _find_event_date(view, event.event_id) == "2026-08-07"

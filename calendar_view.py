"""Build the presentation model for the monthly activity calendar."""

import calendar
from datetime import datetime
from typing import Iterable

from .config import CST, JST
from .models.live import LiveEvent
from .utils import parse_time


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + month - 1 + offset
    return absolute // 12, absolute % 12 + 1


def _month_distance(start: datetime, end: datetime) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def _format_member_mentions(members: Iterable[str]) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for member in members:
        value = str(member).strip().lstrip("@").strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    if not normalized:
        return ""
    first = f"@{normalized[0]}"
    return f"{first} +{len(normalized) - 1}" if len(normalized) > 1 else first


def build_month_view(
    events: Iterable[LiveEvent],
    page: int,
    now: datetime,
) -> dict | None:
    """Return a month-grid view, or ``None`` when no future event exists.

    Precise times use CST. Date-only events use their intended JST calendar
    date so the midnight placeholder cannot shift them to the previous day.
    """
    now_cst = now.astimezone(CST)
    today_jst = now.astimezone(JST).date()
    upcoming: list[tuple[datetime, LiveEvent]] = []

    for event in events:
        start_time = parse_time(event.start_time_utc)
        if start_time is None:
            continue
        if event.is_precise:
            if start_time <= now:
                continue
            view_time = start_time.astimezone(CST)
        else:
            view_time = start_time.astimezone(JST)
            if view_time.date() < today_jst:
                continue
        upcoming.append((view_time, event))

    if not upcoming:
        return None

    upcoming.sort(key=lambda item: (item[0].date(), item[0].time()))
    latest_time = upcoming[-1][0]
    total_pages = _month_distance(now_cst, latest_time) + 1
    page = min(max(page, 1), total_pages)
    year, month = _add_months(now_cst.year, now_cst.month, page - 1)

    month_events: dict[str, list[dict]] = {}
    for start_time, event in upcoming:
        if (start_time.year, start_time.month) != (year, month):
            continue
        date_key = start_time.date().isoformat()
        month_events.setdefault(date_key, []).append({
            "event_id": event.event_id,
            "title": event.title,
            "members": event.members,
            "member_disp": _format_member_mentions(event.members),
            "time_disp": (
                start_time.strftime("%H:%M") if event.is_precise else "时间未定"
            ),
            "cover_url": event.cover_url,
            "source_tweet_id": event.source_tweet_id,
            "sort_time": start_time.isoformat(),
        })

    weeks = calendar.Calendar(firstweekday=calendar.MONDAY).monthdatescalendar(
        year, month
    )
    if len(weeks) == 4:
        next_week_start = weeks[-1][-1].toordinal() + 1
        weeks.append([
            datetime.fromordinal(next_week_start + offset).date()
            for offset in range(7)
        ])

    week_views = []
    for week in weeks:
        day_views = []
        for day in week:
            in_month = day.year == year and day.month == month
            day_events = month_events.get(day.isoformat(), []) if in_month else []
            show_images = len(day_events) <= 2
            for item in day_events:
                item["show_image"] = bool(show_images and item["cover_url"])
                item.pop("sort_time", None)
            day_views.append({
                "day": day.day,
                "date": day.isoformat(),
                "in_month": in_month,
                "is_today": day == now_cst.date(),
                "events": day_events,
            })
        week_views.append(day_views)

    last_day = calendar.monthrange(year, month)[1]
    return {
        "year": year,
        "month": month,
        "month_title": f"{year}年{month}月",
        "month_label": datetime(year, month, 1).strftime("%B %Y").upper(),
        "date_range": f"{month:02d}/01 - {month:02d}/{last_day:02d}",
        "weeks": week_views,
        "page": page,
        "total_pages": total_pages,
        "month_event_count": sum(len(items) for items in month_events.values()),
        "total_event_count": len(upcoming),
    }

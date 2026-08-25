from pathlib import Path

import pytest
from nonebot_plugin_xfetch import config
from nonebot_plugin_xfetch.models.group import GroupConfig
from nonebot_plugin_xfetch.services import event_service, switches


def test_data_dir_is_inside_plugin_and_independent_from_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert config.DATA_DIR == config.PLUGIN_DIR / "data"
    assert config.DATA_DIR.is_absolute()
    assert config.DATA_DIR != Path.cwd() / "data"


def test_group_config_contains_only_xfetch_subscription_settings():
    group = GroupConfig(group_id="123")

    assert not hasattr(group, "master_on")
    assert not hasattr(group, "radio_on")


def test_optional_lock_defaults_to_enabled(monkeypatch):
    monkeypatch.setattr(switches, "_lock_reader", lambda: None)

    assert switches.is_master_on("123") is True


def test_optional_lock_result_is_respected(monkeypatch):
    monkeypatch.setattr(switches, "_lock_reader", lambda: lambda _gid: False)

    assert switches.is_master_on("123") is False


@pytest.mark.asyncio
async def test_live_events_default_to_core_subscriptions_without_xfetch_record(
    monkeypatch,
):
    class Bot:
        async def get_group_list(self):
            return [{"group_id": 123}]

        async def call_api(self, api, **kwargs):
            calls.append((api, kwargs))

    calls = []
    event = type(
        "Event",
        (),
        {
            "notified": False,
            "is_precise": True,
            "start_time_utc": "2026-08-23T04:10:00+00:00",
            "members": [config.CORE_MEMBERS[0]],
            "title": "test live",
            "event_id": "event-1",
        },
    )()
    real_datetime = event_service.datetime
    now = type(
        "FakeDateTime",
        (),
        {"now": staticmethod(lambda _tz: real_datetime.fromisoformat(
            "2026-08-23T04:00:00+00:00"
        ))},
    )
    monkeypatch.setattr(event_service, "datetime", now)
    monkeypatch.setattr(event_service, "get_live_events", lambda: [event])
    monkeypatch.setattr(event_service, "get_all_group_configs", lambda: [])
    monkeypatch.setattr(event_service, "is_master_on", lambda _gid: True)
    monkeypatch.setattr(event_service, "save_live_events", lambda _events: None)

    await event_service.check_upcoming_lives(Bot())

    assert calls and calls[0][0] == "send_group_msg"

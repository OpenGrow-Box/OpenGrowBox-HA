from datetime import datetime as real_datetime
from types import SimpleNamespace

import pytest

from custom_components.opengrowbox.OGBController.managers.core.OGBMainController import (
    OGBMainController,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _make_controller(data_store, event_manager):
    controller = OGBMainController.__new__(OGBMainController)
    controller.room = "dev_room"
    controller.data_store = data_store
    controller.event_manager = event_manager
    controller.device_manager = SimpleNamespace(devices={})
    return controller


def _patch_now(monkeypatch, year, month, day, hour, minute, second=0):
    class FakeDateTime:
        @classmethod
        def now(cls):
            return real_datetime(year, month, day, hour, minute, second)

        @classmethod
        def strptime(cls, value, fmt):
            return real_datetime.strptime(value, fmt)

    monkeypatch.setattr("datetime.datetime", FakeDateTime)


@pytest.mark.asyncio
async def test_update_light_state_normal_schedule(monkeypatch):
    store = FakeDataStore({"isPlantDay": {"lightOnTime": "08:00:00", "lightOffTime": "20:00:00"}})
    controller = _make_controller(store, FakeEventManager())

    _patch_now(monkeypatch, 2026, 1, 1, 10, 0)
    assert await controller.update_light_state() is True

    _patch_now(monkeypatch, 2026, 1, 1, 21, 0)
    assert await controller.update_light_state() is False


@pytest.mark.asyncio
async def test_update_light_state_over_midnight_schedule(monkeypatch):
    store = FakeDataStore({"isPlantDay": {"lightOnTime": "20:00:00", "lightOffTime": "08:00:00"}})
    controller = _make_controller(store, FakeEventManager())

    _patch_now(monkeypatch, 2026, 1, 2, 2, 0)
    assert await controller.update_light_state() is True

    _patch_now(monkeypatch, 2026, 1, 2, 12, 0)
    assert await controller.update_light_state() is False


@pytest.mark.asyncio
async def test_light_schedule_update_targets_only_normal_lights(monkeypatch):
    store = FakeDataStore(
        {
            "controlOptions": {"lightbyOGBControl": True},
            "isPlantDay": {"lightOnTime": "08:00:00", "lightOffTime": "20:00:00"},
        }
    )
    events = FakeEventManager()
    controller = _make_controller(store, events)
    controller.device_manager.devices = {
        "main_light": SimpleNamespace(deviceType="Light"),
        "uv_special": SimpleNamespace(deviceType="LightUV"),
    }

    _patch_now(monkeypatch, 2026, 1, 1, 9, 30)
    await controller.light_schedule_update(None)

    toggle_events = [e for e in events.emitted if e["event_name"] == "toggleLight"]
    assert toggle_events
    payload = toggle_events[-1]["data"]
    assert payload["state"] is True
    assert payload["target_devices"] == ["main_light"]


@pytest.mark.asyncio
async def test_light_schedule_update_disabled_does_not_emit(monkeypatch):
    store = FakeDataStore(
        {
            "controlOptions": {"lightbyOGBControl": False},
            "isPlantDay": {"lightOnTime": "08:00:00", "lightOffTime": "20:00:00"},
        }
    )
    events = FakeEventManager()
    controller = _make_controller(store, events)

    _patch_now(monkeypatch, 2026, 1, 1, 9, 30)
    await controller.light_schedule_update(None)

    assert events.emitted == []

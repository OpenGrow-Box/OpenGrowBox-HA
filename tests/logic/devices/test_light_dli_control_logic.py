from datetime import datetime as real_datetime, timedelta

import pytest

from custom_components.opengrowbox.OGBController.OGBDevices import Light as light_module
from custom_components.opengrowbox.OGBController.OGBDevices.Light import Light

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _patch_light_datetime(monkeypatch, now_dt):
    class FakeDateTime:
        @classmethod
        def now(cls):
            return now_dt

        @classmethod
        def strptime(cls, value, fmt):
            return real_datetime.strptime(value, fmt)

    monkeypatch.setattr(light_module, "datetime", FakeDateTime)


def _make_light(current_voltage=50.0, control_type="DLI"):
    now = real_datetime(2026, 4, 2)
    growstart = (now - timedelta(days=8)).strftime("%Y-%m-%d")

    store = FakeDataStore(
        {
            "plantType": "Cannabis",
            "plantStage": "EarlyVeg",
            "plantDates": {"growstartdate": growstart, "bloomswitchdate": "2026-03-20"},
            "controlOptions": {"lightControlType": control_type},
            "Light": {
                "plans": {
                    "cannabis": {
                        "veg": {
                            "curve": [
                                {"week": 1, "DLITarget": 20},
                                {"week": 2, "DLITarget": 25},
                            ]
                        }
                    }
                }
            },
            "DeviceMinMax": {
                "Light": {
                    "active": True,
                    "minVoltage": 20,
                    "maxVoltage": 100,
                }
            },
        }
    )

    light = Light.__new__(Light)
    light.deviceName = "main_light"
    light.inRoom = "dev_room"
    light.deviceType = "Light"
    light.event_manager = FakeEventManager()
    light.data_store = store
    light.dataStore = store
    light.sunrise_phase_active = False
    light.sunset_phase_active = False
    light.isDimmable = True
    light.islightON = True
    light.ogbLightControl = True
    light.voltage = current_voltage

    light.log_action = lambda *_args, **_kwargs: None

    called = {"brightness": None}

    async def fake_turn_on(**kwargs):
        called["brightness"] = kwargs.get("brightness_pct")

    light.turn_on = fake_turn_on

    return light, called


@pytest.mark.asyncio
async def test_update_light_ignores_non_dli_control(monkeypatch):
    light, called = _make_light(control_type="Default")
    _patch_light_datetime(monkeypatch, real_datetime(2026, 4, 2))

    await light.updateLight(type("Payload", (), {"DLI": 10})())
    assert called["brightness"] is None


@pytest.mark.asyncio
async def test_updated_light_voltage_by_dli_increases_when_below_target(monkeypatch):
    light, called = _make_light(current_voltage=50.0, control_type="DLI")
    _patch_light_datetime(monkeypatch, real_datetime(2026, 4, 2))

    await light.updated_light_voltage_by_dli(10)
    assert light.voltage == 51.0
    assert called["brightness"] == 51.0


@pytest.mark.asyncio
async def test_updated_light_voltage_by_dli_decreases_when_above_target(monkeypatch):
    light, called = _make_light(current_voltage=50.0, control_type="DLI")
    _patch_light_datetime(monkeypatch, real_datetime(2026, 4, 2))

    await light.updated_light_voltage_by_dli(40)
    assert light.voltage == 49.0
    assert called["brightness"] == 49.0


@pytest.mark.asyncio
async def test_updated_light_voltage_by_dli_skips_during_sunrise(monkeypatch):
    light, called = _make_light(current_voltage=50.0, control_type="DLI")
    light.sunrise_phase_active = True
    _patch_light_datetime(monkeypatch, real_datetime(2026, 4, 2))

    await light.updated_light_voltage_by_dli(10)
    assert light.voltage == 50.0
    assert called["brightness"] is None

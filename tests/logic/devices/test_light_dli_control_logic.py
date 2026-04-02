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


# ===== SUNRISE/SUNSET LOGIC TESTS =====

def _make_sunrise_light(plant_stage="MidFlower", user_minmax_active=False, user_min=25, user_max=85):
    """Helper function to create a Light device for sunrise/sunset testing."""
    
    store = FakeDataStore({
        "plantStage": plant_stage,
        "isPlantDay": {
            "lightOnTime": "08:00:00",
            "lightOffTime": "20:00:00",
            "sunRiseTime": "00:30:00",
            "sunSetTime": "00:30:00",
            "islightON": True
        },
        "DeviceMinMax": {
            "Light": {
                "active": user_minmax_active,
                "minVoltage": user_min,
                "maxVoltage": user_max
            }
        }
    })
    
    light = Light.__new__(Light)
    light.deviceName = "test_light"
    light.inRoom = "test_room"
    light.deviceType = "Light"
    light.event_manager = FakeEventManager()
    light.data_store = store
    light.dataStore = store
    light.minVoltage = None
    light.maxVoltage = None
    light.voltage = 0
    light.islightON = True
    light.isDimmable = True
    light.isInitialized = True
    light.isRunning = True
    light.ogbLightControl = True
    light.initVoltage = 20
    light.sun_phase_paused = False
    light.sunset_phase_active = False
    light.PlantStageMinMax = {
        "EarlyVeg": {"min": 20, "max": 35},
        "MidVeg": {"min": 35, "max": 50},
        "LateVeg": {"min": 50, "max": 70},
        "EarlyFlower": {"min": 50, "max": 70},
        "MidFlower": {"min": 70, "max": 90},
        "LateFlower": {"min": 70, "max": 100},
    }
    
    brightness_values = []
    async def fake_turn_on(**kwargs):
        brightness_values.append(kwargs.get("brightness_pct"))
    
    light.turn_on = fake_turn_on
    light.turn_off = lambda **kwargs: None
    light.log_action = lambda *_args, **_kwargs: None
    
    return light, brightness_values


@pytest.mark.asyncio
async def test_sunrise_with_user_minmax_active():
    """Test sunrise with user-defined min/max active."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="MidFlower",
        user_minmax_active=True,
        user_min=25,
        user_max=85
    )
    
    light.sunRiseDuration = 600  # 10 minutes in seconds
    light.sunrise_phase_active = True
    
    await light._run_sunrise()
    
    # Should start from user min (25%) and go to user max (85%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 25.0
    assert brightness_values[-1] == 85.0


@pytest.mark.asyncio
async def test_sunrise_with_plant_stage_minmax():
    """Test sunrise with plant stage min/max (no user minmax)."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="EarlyVeg",
        user_minmax_active=False
    )
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    await light._run_sunrise()
    
    # EarlyVeg: min=20%, max=35%
    assert len(brightness_values) == 10
    assert brightness_values[0] == 20.0
    assert brightness_values[-1] == 35.0


@pytest.mark.asyncio
async def test_sunrise_without_user_minmax_or_plant_stage():
    """Test sunrise without user minmax and without valid plant stage."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="UnknownStage",
        user_minmax_active=False
    )
    
    light.maxVoltage = 100
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    await light._run_sunrise()
    
    # Should start from default 20% and go to maxVoltage (100%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 20.0
    assert brightness_values[-1] == 100.0


@pytest.mark.asyncio
async def test_sunrise_with_flower_stage():
    """Test sunrise with flowering plant stage."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="LateFlower",
        user_minmax_active=False
    )
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    await light._run_sunrise()
    
    # LateFlower: min=70%, max=100%
    assert len(brightness_values) == 10
    assert brightness_values[0] == 70.0
    assert brightness_values[-1] == 100.0


@pytest.mark.asyncio
async def test_sunrise_respects_user_minmax_over_plant_stage():
    """Test that user minmax takes priority over plant stage when active."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="LateFlower",
        user_minmax_active=True,
        user_min=30,
        user_max=90
    )
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    await light._run_sunrise()
    
    # Should use user minmax (30-90%), NOT plant stage (70-100%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 30.0
    assert brightness_values[-1] == 90.0


@pytest.mark.asyncio
async def test_sunrise_paused():
    """Test that sunrise respects pause flag."""
    light, brightness_values = _make_sunrise_light(user_minmax_active=True)
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    light.sun_phase_paused = True
    
    await light._run_sunrise()
    
    # Should not execute any steps when paused
    assert len(brightness_values) == 0


@pytest.mark.asyncio
async def test_sunrise_light_turned_off():
    """Test that sunrise stops when light is turned off."""
    light, brightness_values = _make_sunrise_light(user_minmax_active=True)
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    async def fake_turn_on(**kwargs):
        brightness_values.append(kwargs.get("brightness_pct"))
        # Turn off light after first step
        light.islightON = False
    
    light.turn_on = fake_turn_on
    
    await light._run_sunrise()
    
    # Should only execute one step before stopping
    assert len(brightness_values) == 1


@pytest.mark.asyncio
async def test_sunset_with_user_minmax_active():
    """Test sunset with user-defined min/max active."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="MidFlower",
        user_minmax_active=True,
        user_min=25,
        user_max=85
    )
    
    light.sunSetDuration = 600
    light.sunset_phase_active = True
    
    await light._run_sunset()
    
    # Should start from user max (85%) and go to user min (25%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 85.0
    assert brightness_values[-1] == 25.0


@pytest.mark.asyncio
async def test_sunset_with_plant_stage_minmax():
    """Test sunset with plant stage min/max (no user minmax)."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="EarlyVeg",
        user_minmax_active=False
    )
    
    light.sunSetDuration = 600
    light.sunset_phase_active = True
    
    await light._run_sunset()
    
    # EarlyVeg: min=20%, max=35%
    assert len(brightness_values) == 10
    assert brightness_values[0] == 35.0
    assert brightness_values[-1] == 20.0


@pytest.mark.asyncio
async def test_sunset_without_user_minmax_or_plant_stage():
    """Test sunset without user minmax and without valid plant stage."""
    light, brightness_values = _make_sunrise_light(
        plant_stage="UnknownStage",
        user_minmax_active=False
    )
    
    light.maxVoltage = 100
    light.initVoltage = 20
    light.sunSetDuration = 600
    light.sunset_phase_active = True
    
    await light._run_sunset()
    
    # Should start from maxVoltage (100%) and go to initVoltage (20%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 100.0
    assert brightness_values[-1] == 20.0

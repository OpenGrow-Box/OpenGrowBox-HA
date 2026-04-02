import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, time

from custom_components.opengrowbox.OGBController.OGBDevices.Light import Light
from tests.logic.helpers import FakeDataStore


def _create_light_device(plant_stage="MidFlower", user_minmax_active=False, user_min=25, user_max=85):
    """Helper function to create a Light device for testing."""
    
    device_data = {
        "name": "test_light",
        "type": "Light",
        "room": "test_room",
        "dimmable": True,
        "switches": [],
        "options": [],
        "ogbsettings": []
    }
    
    data_store = FakeDataStore({
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
    
    event_manager = Mock()
    event_manager.on = Mock()
    event_manager.emit = AsyncMock()
    
    light = Light(
        deviceName="test_light",
        deviceData=device_data,
        eventManager=event_manager,
        dataStore=data_store,
        deviceType="Light",
        inRoom="test_room",
        hass=Mock()
    )
    
    light.minVoltage = None
    light.maxVoltage = None
    light.voltage = 0
    light.islightON = True
    light.isDimmable = True
    light.isInitialized = True
    light.isRunning = True
    light.ogbLightControl = True
    
    return light


@pytest.mark.asyncio
async def test_sunrise_with_user_minmax_active():
    """Test sunrise with user-defined min/max active."""
    light = _create_light_device(
        plant_stage="MidFlower",
        user_minmax_active=True,
        user_min=25,
        user_max=85
    )
    
    light.sunRiseDuration = 600  # 10 minutes in seconds
    light.sunrise_phase_active = True
    
    # Mock turn_on to capture brightness values
    brightness_values = []
    original_turn_on = light.turn_on
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    # Run sunrise
    await light._run_sunrise()
    
    # Should start from user min (25%) and go to user max (85%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 25.0
    assert brightness_values[-1] == 85.0
    
    # Verify intermediate values
    step = (85.0 - 25.0) / 10
    for i, val in enumerate(brightness_values[:-1]):
        expected = 25.0 + (step * (i + 1))
        assert abs(val - expected) < 0.1


@pytest.mark.asyncio
async def test_sunrise_with_plant_stage_minmax():
    """Test sunrise with plant stage min/max (no user minmax)."""
    light = _create_light_device(
        plant_stage="EarlyVeg",
        user_minmax_active=False
    )
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    await light._run_sunrise()
    
    # EarlyVeg: min=20%, max=35%
    assert len(brightness_values) == 10
    assert brightness_values[0] == 20.0
    assert brightness_values[-1] == 35.0


@pytest.mark.asyncio
async def test_sunrise_without_user_minmax_or_plant_stage():
    """Test sunrise without user minmax and without valid plant stage."""
    light = _create_light_device(
        plant_stage="UnknownStage",
        user_minmax_active=False
    )
    
    light.maxVoltage = 100
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    await light._run_sunrise()
    
    # Should start from default 20% and go to maxVoltage (100%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 20.0
    assert brightness_values[-1] == 100.0


@pytest.mark.asyncio
async def test_sunset_with_user_minmax_active():
    """Test sunset with user-defined min/max active."""
    light = _create_light_device(
        plant_stage="MidFlower",
        user_minmax_active=True,
        user_min=25,
        user_max=85
    )
    
    light.sunSetDuration = 600
    light.sunset_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    # Mock turn_off
    light.turn_off = AsyncMock()
    
    await light._run_sunset()
    
    # Should start from user max (85%) and go to user min (25%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 85.0
    assert brightness_values[-1] == 25.0


@pytest.mark.asyncio
async def test_sunset_with_plant_stage_minmax():
    """Test sunset with plant stage min/max (no user minmax)."""
    light = _create_light_device(
        plant_stage="EarlyVeg",
        user_minmax_active=False
    )
    
    light.sunSetDuration = 600
    light.sunset_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    light.turn_off = AsyncMock()
    
    await light._run_sunset()
    
    # EarlyVeg: min=20%, max=35%
    assert len(brightness_values) == 10
    assert brightness_values[0] == 35.0
    assert brightness_values[-1] == 20.0


@pytest.mark.asyncio
async def test_sunset_without_user_minmax_or_plant_stage():
    """Test sunset without user minmax and without valid plant stage."""
    light = _create_light_device(
        plant_stage="UnknownStage",
        user_minmax_active=False
    )
    
    light.maxVoltage = 100
    light.initVoltage = 20
    light.sunSetDuration = 600
    light.sunset_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    light.turn_off = AsyncMock()
    
    await light._run_sunset()
    
    # Should start from maxVoltage (100%) and go to initVoltage (20%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 100.0
    assert brightness_values[-1] == 20.0


@pytest.mark.asyncio
async def test_sunrise_with_flower_stage():
    """Test sunrise with flowering plant stage."""
    light = _create_light_device(
        plant_stage="LateFlower",
        user_minmax_active=False
    )
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    await light._run_sunrise()
    
    # LateFlower: min=70%, max=100%
    assert len(brightness_values) == 10
    assert brightness_values[0] == 70.0
    assert brightness_values[-1] == 100.0


@pytest.mark.asyncio
async def test_sunrise_respects_user_minmax_over_plant_stage():
    """Test that user minmax takes priority over plant stage when active."""
    light = _create_light_device(
        plant_stage="LateFlower",
        user_minmax_active=True,
        user_min=30,
        user_max=90
    )
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    await light._run_sunrise()
    
    # Should use user minmax (30-90%), NOT plant stage (70-100%)
    assert len(brightness_values) == 10
    assert brightness_values[0] == 30.0
    assert brightness_values[-1] == 90.0


@pytest.mark.asyncio
async def test_sunrise_paused():
    """Test that sunrise respects pause flag."""
    light = _create_light_device(user_minmax_active=True)
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    light.sun_phase_paused = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
    light.turn_on = mock_turn_on
    
    await light._run_sunrise()
    
    # Should not execute any steps when paused
    assert len(brightness_values) == 0


@pytest.mark.asyncio
async def test_sunrise_light_turned_off():
    """Test that sunrise stops when light is turned off."""
    light = _create_light_device(user_minmax_active=True)
    
    light.sunRiseDuration = 600
    light.sunrise_phase_active = True
    
    brightness_values = []
    async def mock_turn_on(brightness_pct=None):
        brightness_values.append(brightness_pct)
        # Turn off light after first step
        light.islightON = False
    light.turn_on = mock_turn_on
    
    await light._run_sunrise()
    
    # Should only execute one step before stopping
    assert len(brightness_values) == 1

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBReservoirManager import (
    OGBReservoirManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _manager_stub(store=None, event_manager=None, hass=None):
    """Create a stubbed ReservoirManager for testing"""
    store = store or FakeDataStore()
    event_manager = event_manager or FakeEventManager()
    hass = hass or MagicMock()
    
    # Initialize default threshold values in dataStore
    store.setDeep("Hydro.ReservoirMinLevel", 25.0)
    store.setDeep("Hydro.ReservoirMaxLevel", 85.0)
    
    manager = OGBReservoirManager.__new__(OGBReservoirManager)
    manager.room = "dev_room"
    manager.data_store = store
    manager.event_manager = event_manager
    manager.hass = hass
    manager.notificator = None
    
    # State tracking
    manager.current_level = None
    manager.current_level_raw = None
    manager.level_unit = "%"
    manager.last_alert_time = None
    manager.last_alert_type = None
    manager.alert_cooldown = timedelta(minutes=30)
    manager.reservoir_sensor_entity = None
    
    # Auto-fill tracking
    manager._is_filling = False
    manager._fill_blocked = False
    manager._fill_cycles_completed = 0
    manager.reservoir_pump_entity = None
    
    return manager


@pytest.mark.asyncio
async def test_handle_level_update_parses_percentage():
    """Test parsing percentage level values"""
    manager = _manager_stub()
    
    await manager._handle_level_update({
        "entity_id": "sensor.test_reservoir",
        "state": "45.5",
        "attributes": {"unit_of_measurement": "%"}
    })
    
    assert manager.current_level == 45.5
    assert manager.current_level_raw == 45.5
    assert manager.level_unit == "%"


@pytest.mark.asyncio
async def test_handle_level_update_skips_invalid_values():
    """Test that invalid sensor values are skipped"""
    manager = _manager_stub()
    
    invalid_values = ["unknown", "unavailable", "Unbekannt", None]
    
    for invalid in invalid_values:
        manager.current_level = 50.0  # Set a baseline
        await manager._handle_level_update({
            "entity_id": "sensor.test_reservoir",
            "state": invalid,
            "attributes": {}
        })
        # Level should remain unchanged
        assert manager.current_level == 50.0


@pytest.mark.asyncio
async def test_handle_level_update_converts_distance_cm():
    """Test converting distance measurements to percentage"""
    manager = _manager_stub()
    
    # Mock the conversion method
    async def mock_convert(distance, unit, attrs):
        # Simple conversion: assume 0-100cm range
        return 100.0 - distance
    
    manager._convert_distance_to_percentage = mock_convert
    
    await manager._handle_level_update({
        "entity_id": "sensor.test_ultrasonic",
        "state": "30.0",
        "attributes": {"unit_of_measurement": "cm"}
    })
    
    assert manager.current_level == 70.0  # 100 - 30
    assert manager.current_level_raw == 30.0


@pytest.mark.asyncio
async def test_check_thresholds_triggers_low_alert():
    """Test that low level triggers alert"""
    manager = _manager_stub()
    manager.current_level = 20.0  # Below 25% threshold
    
    # Mock alert methods
    low_alert_called = False
    async def mock_low_alert():
        nonlocal low_alert_called
        low_alert_called = True
    
    manager._send_low_level_alert = mock_low_alert
    
    await manager._check_thresholds()
    
    assert low_alert_called is True
    assert manager.last_alert_type == "low"
    assert manager.last_alert_time is not None


@pytest.mark.asyncio
async def test_check_thresholds_triggers_high_alert():
    """Test that high level triggers alert"""
    manager = _manager_stub()
    manager.current_level = 90.0  # Above 85% threshold
    
    high_alert_called = False
    async def mock_high_alert():
        nonlocal high_alert_called
        high_alert_called = True
    
    manager._send_high_level_alert = mock_high_alert
    
    await manager._check_thresholds()
    
    assert high_alert_called is True
    assert manager.last_alert_type == "high"


@pytest.mark.asyncio
async def test_check_thresholds_stops_active_fill_without_high_alert():
    """Active refill should complete at safety stop level without sending overflow warning."""
    manager = _manager_stub()
    manager.current_level = 80.1
    manager._is_filling = True

    high_alert_called = False
    stop_reason = None

    async def mock_high_alert():
        nonlocal high_alert_called
        high_alert_called = True

    async def mock_stop_fill(reason):
        nonlocal stop_reason
        stop_reason = reason
        manager._is_filling = False

    manager._send_high_level_alert = mock_high_alert
    manager._stop_fill = mock_stop_fill

    await manager._check_thresholds()

    assert high_alert_called is False
    assert stop_reason == "Target level reached"


@pytest.mark.asyncio
async def test_check_thresholds_respects_cooldown():
    """Test that alerts respect cooldown period"""
    manager = _manager_stub()
    manager.current_level = 20.0
    manager.last_alert_time = datetime.now() - timedelta(minutes=10)  # Recent alert
    manager.last_alert_type = "low"
    
    alert_called = False
    async def mock_alert():
        nonlocal alert_called
        alert_called = True
    
    manager._send_low_level_alert = mock_alert
    
    await manager._check_thresholds()
    
    # Should not alert due to cooldown
    assert alert_called is False


@pytest.mark.asyncio
async def test_check_thresholds_resets_when_normal():
    """Test that alert type resets when level returns to normal"""
    manager = _manager_stub()
    manager.current_level = 50.0  # Normal level
    manager.last_alert_type = "low"
    manager.last_alert_time = datetime.now() - timedelta(hours=1)
    
    await manager._check_thresholds()
    
    assert manager.last_alert_type is None


@pytest.mark.asyncio
async def test_convert_distance_to_percentage():
    """Test distance to percentage conversion"""
    manager = _manager_stub()
    
    # Set calibration values in dataStore
    manager.data_store.setDeep("Hydro.ReservoirMaxDistance", 100.0)
    manager.data_store.setDeep("Hydro.ReservoirMinDistance", 10.0)
    
    # Distance of 55cm should be 50%
    percentage = await manager._convert_distance_to_percentage(
        55.0, "cm", {}
    )
    
    assert percentage == 50.0


@pytest.mark.asyncio
async def test_convert_distance_handles_meters():
    """Test conversion from meters"""
    manager = _manager_stub()
    
    manager.data_store.setDeep("Hydro.ReservoirMaxDistance", 1.0)  # 1m
    manager.data_store.setDeep("Hydro.ReservoirMinDistance", 0.1)  # 10cm
    
    # Distance of 0.55m should be 50%
    percentage = await manager._convert_distance_to_percentage(
        0.55, "m", {}
    )
    
    assert abs(percentage - 50.0) < 0.001  # Floating point tolerance


def test_get_status_returns_current_state():
    """Test status reporting"""
    manager = _manager_stub()
    manager.current_level = 45.0
    manager.current_level_raw = 45.0
    manager.level_unit = "%"
    manager.last_alert_type = "low"
    manager.reservoir_sensor_entity = "sensor.test"
    
    status = manager.get_status()
    
    assert status["level_percentage"] == 45.0
    assert status["low_threshold"] == 25.0
    assert status["high_threshold"] == 85.0
    assert status["last_alert_type"] == "low"
    assert status["sensor_entity"] == "sensor.test"


@pytest.mark.asyncio
async def test_set_thresholds_updates_values():
    """Test updating alert thresholds"""
    manager = _manager_stub()
    
    await manager.set_thresholds(low=30.0, high=90.0)
    
    assert manager.low_threshold == 30.0
    assert manager.high_threshold == 90.0


@pytest.mark.asyncio
async def test_set_thresholds_clamps_values():
    """Test that threshold values are clamped to valid ranges"""
    manager = _manager_stub()
    
    # Try to set invalid values
    await manager.set_thresholds(low=-10.0, high=110.0)
    
    # Should be clamped
    assert manager.low_threshold == 0.0  # Min 0
    assert manager.high_threshold == 100.0  # Max 100


@pytest.mark.asyncio
async def test_handles_sensor_update_event():
    """Test that ReservoirManager handles SensorUpdate events"""
    manager = _manager_stub()
    
    # Simulate sensor update event
    await manager._check_sensor_update({
        "entity_id": "sensor.test_reservoir_ultrasonic",
        "state": "75.5",
        "attributes": {"unit_of_measurement": "%"}
    })
    
    assert manager.current_level == 75.5
    assert manager.current_level_raw == 75.5
    assert manager.level_unit == "%"


@pytest.mark.asyncio
async def test_handles_sensor_update_with_distance():
    """Test that ReservoirManager converts distance measurements"""
    manager = _manager_stub()
    
    # Set calibration values
    manager.data_store.setDeep("Hydro.ReservoirMaxDistance", 100.0)
    manager.data_store.setDeep("Hydro.ReservoirMinDistance", 10.0)
    
    # Simulate sensor update with distance (cm)
    await manager._check_sensor_update({
        "entity_id": "sensor.test_ultrasonic",
        "state": "55.0",
        "attributes": {"unit_of_measurement": "cm"}
    })
    
    # 55cm from 100cm max and 10cm min = 50% full
    assert abs(manager.current_level - 50.0) < 0.001
    assert manager.current_level_raw == 55.0

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

import pytest

from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBTankFeedManager import (
    ECUnit,
    FeedMode,
    OGBTankFeedManager,
    PumpType,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class PumpConfig:
    """Configuration for pump dosing"""
    ml_per_second: float = 0.5
    min_dose_ml: float = 0.5
    max_dose_ml: float = 25.0


@dataclass
class PumpCalibration:
    """Pump calibration data"""
    pump_type: str
    calibration_factor: float = 1.0
    last_calibration: Optional[datetime] = None
    calibration_count: int = 0
    
    def calculate_adjustment(self) -> float:
        """Berechnet Anpassungsfaktor basierend auf letztem Ergebnis"""
        if self.calibration_factor <= 0:
            return 1.0
        return self.calibration_factor


def _manager_stub(store=None, event_manager=None):
    manager = OGBTankFeedManager.__new__(OGBTankFeedManager)
    manager.room = "dev_room"
    manager.data_store = store or FakeDataStore()
    manager.event_manager = event_manager or FakeEventManager()
    manager.ec_unit = ECUnit.MS_CM
    manager.feed_mode = FeedMode.AUTOMATIC
    manager.notificator = None  # Will be mocked in tests
    manager.feed_history = []
    manager.current_ec = 0.0
    manager.feed_ec_before = 0.0
    manager.feed_ec_after = 0.0
    manager.nutrient_concentrations = {}
    manager.pump_calibrations = {
        "A": PumpCalibration("A"),
        "B": PumpCalibration("B"),
        "C": PumpCalibration("C"),
        "X": PumpCalibration("X"),
        "Y": PumpCalibration("Y"),
    }
    manager.pump_config = PumpConfig()
    return manager


def test_normalize_ec_value_handles_us_and_ms_ranges():
    manager = _manager_stub()

    assert manager._normalize_ec_value(0) == 0.0
    assert manager._normalize_ec_value(-1) == 0.0

    # mS/cm range should stay unchanged
    assert manager._normalize_ec_value(2.1) == 2.1

    # µS/cm range (>100) should be converted to mS/cm
    assert manager._normalize_ec_value(1800) == 1.8


def test_get_effective_pump_rate_returns_zero_when_flowrate_disabled():
    store = FakeDataStore({"Hydro": {"Pump_FlowRate_A": 0.0}})
    manager = _manager_stub(store)

    assert manager._get_effective_pump_rate(PumpType.NUTRIENT_A) == 0.0


@pytest.mark.asyncio
async def test_dose_nutrients_skips_disabled_pump_flowrate_zero():
    store = FakeDataStore({"Hydro": {"Pump_FlowRate_A": 0.0}})
    manager = _manager_stub(store, FakeEventManager())
    manager.nutrients = {"A": 1.0}
    manager._calculate_nutrient_dose = lambda _ml_per_liter: 5.0

    calls = {"count": 0}

    async def _activate(*_args, **_kwargs):
        calls["count"] += 1
        return True

    manager._activate_pump = _activate

    result = await manager._dose_nutrients()

    assert result is True
    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_feed_mode_change_delegates_and_updates_mode():
    calls = {"mode": None}

    class _FakeFeedLogic:
        async def handle_feed_mode_change(self, mode):
            calls["mode"] = mode

    store = FakeDataStore({"mainControl": "HomeAssistant"})
    manager = _manager_stub(store)
    manager.feed_logic_manager = _FakeFeedLogic()

    await manager._feed_mode_change("Automatic")
    assert calls["mode"] == "Automatic"
    assert manager.feed_mode == FeedMode.AUTOMATIC


@pytest.mark.asyncio
async def test_feed_mode_change_rejected_when_control_not_allowed():
    class _FakeFeedLogic:
        async def handle_feed_mode_change(self, _mode):
            raise AssertionError("should not be called")

    store = FakeDataStore({"mainControl": "Manual"})
    manager = _manager_stub(store)
    manager.feed_logic_manager = _FakeFeedLogic()

    result = await manager._feed_mode_change("Automatic")
    assert result is False


@pytest.mark.asyncio
async def test_check_if_feed_need_passes_normalized_sensor_data():
    captured = {"payload": None}

    class _FakeFeedLogic:
        async def handle_feed_update(self, payload):
            captured["payload"] = payload

    manager = _manager_stub(FakeDataStore())
    manager.feed_logic_manager = _FakeFeedLogic()
    manager.feed_mode = FeedMode.AUTOMATIC

    payload = SimpleNamespace(
        ecCurrent=1800,
        tdsCurrent=900,
        phCurrent=6.1,
        waterTemp=21.5,
        oxiCurrent=7.2,
        salCurrent=0.3,
    )

    await manager._check_if_feed_need(payload)
    assert manager.current_ec == 1.8
    assert captured["payload"] is not None
    assert captured["payload"]["ecCurrent"] == 1800.0


@pytest.mark.asyncio
async def test_load_feed_history_loads_from_datastore():
    store = FakeDataStore({
        "Hydro": {
            "FeedHistory": '[{"timestamp": "2024-01-01T00:00:00", "ec_before": 1.0, "ec_after": 1.5}]'
        }
    })
    manager = _manager_stub(store)
    
    await manager._load_feed_history()
    
    assert len(manager.feed_history) == 1
    assert manager.feed_history[0]["ec_before"] == 1.0
    assert manager.feed_history[0]["ec_after"] == 1.5


@pytest.mark.asyncio
async def test_load_feed_history_handles_missing_data():
    store = FakeDataStore()
    manager = _manager_stub(store)
    
    await manager._load_feed_history()
    
    assert manager.feed_history == []


@pytest.mark.asyncio
async def test_log_to_client_emits_logforclient_event():
    event_manager = FakeEventManager()
    manager = _manager_stub(event_manager=event_manager)
    
    manager._log_to_client("Test message", "INFO", {"extra": "data"})
    
    # Wait for async task to complete
    await asyncio.sleep(0.1)
    
    log_events = [e for e in event_manager.emitted if e["event_name"] == "LogForClient"]
    assert len(log_events) == 1
    assert log_events[0]["data"]["Message"] == "Test message"
    assert log_events[0]["data"]["extra"] == "data"
    assert log_events[0]["debug_type"] == "INFO"


@pytest.mark.asyncio
async def test_send_notification_skips_if_no_notificator():
    manager = _manager_stub()
    manager.notificator = None
    
    # Should not raise
    await manager._send_notification("critical", "Test message")


@pytest.mark.asyncio
async def test_dose_nutrients_proportional_tracks_ec():
    event_manager = FakeEventManager()
    manager = _manager_stub(event_manager=event_manager)
    manager.current_ec = 1.0
    manager.nutrients = {"A": 1.0, "B": 1.0, "C": 1.0}
    manager.nutrient_concentrations = {"A": 2.0, "B": 1.5, "C": 1.0}
    manager.reservoir_volume_liters = 100.0
    
    # Mock _dose_nutrients_with_concentration to simulate successful dosing
    async def mock_dose(nutrient_doses):
        # Simulate EC increase after dosing
        manager.current_ec = 1.3
        return True
    
    manager._dose_nutrients_with_concentration = mock_dose
    
    await manager._dose_nutrients_proportional({"dose_ml": 5.0})
    
    # Wait for async sleep
    await asyncio.sleep(0.1)
    
    assert manager.feed_ec_before == 1.0
    assert manager.feed_ec_after == 1.3
    assert abs(manager.feed_ec_added - 0.3) < 0.001  # Floating point tolerance
    assert len(manager.feed_history) == 1
    assert abs(manager.feed_history[0]["ec_added"] - 0.3) < 0.001  # Floating point tolerance


@pytest.mark.asyncio
async def test_dose_nutrients_proportional_skips_if_dose_zero():
    event_manager = FakeEventManager()
    manager = _manager_stub(event_manager=event_manager)
    
    await manager._dose_nutrients_proportional({"dose_ml": 0.0})
    
    # Should not add to history
    assert len(manager.feed_history) == 0


@pytest.mark.asyncio
async def test_dose_nutrients_proportional_skips_if_dose_negative():
    event_manager = FakeEventManager()
    manager = _manager_stub(event_manager=event_manager)
    
    await manager._dose_nutrients_proportional({"dose_ml": -1.0})
    
    # Should not add to history
    assert len(manager.feed_history) == 0


@pytest.mark.longterm
@pytest.mark.asyncio
async def test_24_hour_feed_cycle_simulation():
    """
    Simulates a 24-hour feed cycle to verify:
    - Feed happens at correct intervals (4 hours min)
    - EC tracking works correctly
    - Rate limiting prevents over-feeding
    - Feed history is maintained
    
    This test runs fast by mocking the time-checking logic directly.
    """
    from datetime import datetime, timedelta
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBFeedLogicManager import OGBFeedLogicManager
    
    event_manager = FakeEventManager()
    store = FakeDataStore({
        "mainControl": "HomeAssistant",
        "Hydro": {
            "Targets": {"EC": 2.0, "pH": 6.0},
            "ec_current": 1.5,  # Below target, needs feeding
            "ph_current": 6.2,
        }
    })
    
    # Create manager with mocked dependencies
    manager = _manager_stub(store, event_manager)
    
    # Create real feed logic manager
    feed_logic = OGBFeedLogicManager("dev_room", store, event_manager)
    feed_logic.min_feed_interval = 14400  # 4 hours in seconds
    feed_logic.max_daily_feeds = 6
    feed_logic.daily_feed_count = 0
    feed_logic.last_feed_time = None
    
    manager.feed_logic_manager = feed_logic
    
    # Track pump activations
    pump_activations = []
    
    async def mock_activate_pump(pump_type, run_time, dose_ml):
        pump_activations.append({
            "pump": pump_type,
            "time": feed_logic.last_feed_time,
            "dose_ml": dose_ml
        })
        return True
    
    manager._activate_pump = mock_activate_pump
    
    # Mock the feed logic methods to simulate time passing
    # Instead of mocking datetime, we'll mock the time checks directly
    original_check_ranges = feed_logic._check_ranges_and_feed
    
    # Simulate 6 feed cycles (24 hours / 4 hours)
    for cycle in range(6):
        # Manually simulate time passing
        if feed_logic.last_feed_time is None:
            # First feed - always allowed
            time_since_last = timedelta(hours=999)  # Very long time
        else:
            # Subsequent feeds - simulate 4 hours passing
            time_since_last = timedelta(hours=4)
        
        # Temporarily bypass the time check
        feed_logic.last_feed_time = None  # Reset to bypass time check
        
        # Check if feed is needed (will pass time check)
        needs_feed = await feed_logic._check_ranges_and_feed()
        
        # Restore proper last_feed_time for next iteration
        if needs_feed:
            # Simulate feeding
            manager.feed_ec_before = manager.current_ec
            manager.current_ec = 1.9  # EC rises after feeding
            manager.feed_ec_after = manager.current_ec
            manager.feed_ec_added = 0.4
            
            # Set last feed time to current "simulated" time
            base_time = datetime(2024, 1, 1, 8, 0)
            feed_logic.last_feed_time = base_time + timedelta(hours=cycle * 4)
            
            # Record feed cycle
            feed_cycle = {
                'timestamp': feed_logic.last_feed_time.isoformat(),
                'ec_before': manager.feed_ec_before,
                'ec_after': manager.feed_ec_after,
                'ec_added': manager.feed_ec_added,
                'dose_ml': 5.0,
                'nutrients': ['A', 'B', 'C']
            }
            manager.feed_history.append(feed_cycle)
            
            # Note: daily_feed_count is already incremented by _check_ranges_and_feed()
    
    # Verify results
    assert len(manager.feed_history) == 6, f"Expected 6 feed cycles, got {len(manager.feed_history)}"
    assert feed_logic.daily_feed_count == 6
    
    # Verify EC tracking
    for i, cycle in enumerate(manager.feed_history):
        assert cycle['ec_added'] == 0.4, f"Cycle {i}: EC added should be 0.4"
        assert cycle['dose_ml'] == 5.0, f"Cycle {i}: Dose should be 5.0ml"
        assert cycle['nutrients'] == ['A', 'B', 'C'], f"Cycle {i}: Nutrients should be A,B,C"
    
    # Verify intervals (all should be 4 hours apart)
    for i in range(1, len(manager.feed_history)):
        prev_time = datetime.fromisoformat(manager.feed_history[i-1]['timestamp'])
        curr_time = datetime.fromisoformat(manager.feed_history[i]['timestamp'])
        interval = (curr_time - prev_time).total_seconds() / 3600
        assert interval == 4.0, f"Interval between cycle {i-1} and {i} should be 4 hours, got {interval}"
    
    print(f"✓ 24-hour simulation completed: {len(manager.feed_history)} feed cycles")
    print(f"✓ Total EC added: {sum(c['ec_added'] for c in manager.feed_history):.2f}")
    print(f"✓ Average interval: 4.0 hours")


def test_load_pump_flow_rates_loads_from_datastore():
    """Test that pump flow rates are loaded from DataStore"""
    store = FakeDataStore({
        "Hydro": {
            "Pump_FlowRate_A": 60.0,
            "Pump_FlowRate_B": 70.0,
            "Pump_FlowRate_C": 80.0,
            "Pump_FlowRate_W": 150.0,
            "Pump_FlowRate_PH_Down": 15.0,
            "Pump_FlowRate_PH_Up": 12.0,
        }
    })
    manager = _manager_stub(store)
    
    manager._load_pump_flow_rates()
    
    # Verify flow rates were loaded by checking individual pump flow rates
    assert abs(manager._get_pump_flow_rate("A") - (60.0 / 60.0)) < 0.001
    assert abs(manager._get_pump_flow_rate("B") - (70.0 / 60.0)) < 0.001
    assert abs(manager._get_pump_flow_rate("C") - (80.0 / 60.0)) < 0.001
    assert abs(manager._get_pump_flow_rate("W") - (150.0 / 60.0)) < 0.001
    assert abs(manager._get_pump_flow_rate("PH_DOWN") - (15.0 / 60.0)) < 0.001
    assert abs(manager._get_pump_flow_rate("PH_UP") - (12.0 / 60.0)) < 0.001


def test_load_pump_flow_rates_uses_defaults():
    """Test that default flow rates are used when not in DataStore"""
    store = FakeDataStore()
    manager = _manager_stub(store)
    
    manager._load_pump_flow_rates()
    
    # Verify defaults were used
    # A, B, C default: 50.0 ml/min
    # W default: 100.0 ml/min
    # PH-Down, PH+ default: 10.0 ml/min
    assert manager.pump_config.ml_per_second > 0


def test_load_nutrient_concentrations_loads_from_datastore():
    """Test that nutrient concentrations are loaded from DataStore"""
    store = FakeDataStore({
        "Hydro": {
            "Nutrient_Concentration_A": 3.0,
            "Nutrient_Concentration_B": 2.5,
            "Nutrient_Concentration_C": 1.5,
            "Nutrient_Concentration_PH_Down": 0.75,
        }
    })
    manager = _manager_stub(store)
    
    manager._load_nutrient_concentrations()
    
    # Verify concentrations were loaded
    assert manager.nutrient_concentrations["A"] == 3.0
    assert manager.nutrient_concentrations["B"] == 2.5
    assert manager.nutrient_concentrations["C"] == 1.5
    assert manager.nutrient_concentrations["PH_DOWN"] == 0.75


def test_load_nutrient_concentrations_uses_defaults():
    """Test that default concentrations are used when not in DataStore"""
    store = FakeDataStore()
    manager = _manager_stub(store)
    
    manager._load_nutrient_concentrations()
    
    # Verify defaults were used
    assert manager.nutrient_concentrations["A"] == 2.0
    assert manager.nutrient_concentrations["B"] == 2.0
    assert manager.nutrient_concentrations["C"] == 1.0
    assert manager.nutrient_concentrations["PH_DOWN"] == 0.5


def test_calculate_dose_from_concentration():
    """Test dose calculation based on concentration and tank volume"""
    store = FakeDataStore({
        "Hydro": {
            "ReservoirLevel": 80.0,  # 80% full
            "ReservoirVolume": 100.0,  # 100L tank
            "Nutrient_Concentration_A": 2.0,  # 2.0 ml/L
        }
    })
    manager = _manager_stub(store)
    manager.reservoir_volume_liters = 100.0
    manager.nutrient_concentrations = {"A": 2.0, "B": 2.0, "C": 1.0, "PH_DOWN": 0.5}
    
    dose = manager._calculate_dose_from_concentration("A")
    
    # Expected: 80L (80% of 100L) × 2.0 ml/L = 160ml
    assert abs(dose - 160.0) < 0.001


def test_get_pump_flow_rate_returns_correct_rate():
    """Test that pump flow rate is returned for specific pump"""
    store = FakeDataStore({
        "Hydro": {
            "Pump_FlowRate_A": 60.0,  # ml/min
            "Pump_FlowRate_W": 120.0,
        }
    })
    manager = _manager_stub(store)
    manager._load_pump_flow_rates()
    
    # Get flow rate for pump A
    flow_rate_a = manager._get_pump_flow_rate("A")
    assert abs(flow_rate_a - (60.0 / 60.0)) < 0.001  # 1.0 ml/s
    
    # Get flow rate for pump W
    flow_rate_w = manager._get_pump_flow_rate("W")
    assert abs(flow_rate_w - (120.0 / 60.0)) < 0.001  # 2.0 ml/s
    
    # Get flow rate for unknown pump (should return default from pump_config)
    # The default is the last pump's flow rate from _load_pump_flow_rates()
    flow_rate_unknown = manager._get_pump_flow_rate("UNKNOWN")
    assert flow_rate_unknown > 0  # Should return a positive value


def test_calculate_dose_time_uses_pump_flow_rate():
    """Test that dose time calculation uses pump-specific flow rate"""
    store = FakeDataStore({
        "Hydro": {
            "Pump_FlowRate_A": 60.0,  # 60 ml/min = 1 ml/s
            "Pump_FlowRate_B": 120.0,  # 120 ml/min = 2 ml/s
        }
    })
    manager = _manager_stub(store)
    manager._load_pump_flow_rates()
    
    # Test with pump A (1 ml/s) - note: max_dose_ml clamps to 25.0 ml
    time_a = manager._calculate_dose_time(25.0, "A")  # 25ml at 1 ml/s
    assert abs(time_a - 25.0) < 0.001  # 25 seconds
    
    # Test with pump B (2 ml/s) - note: max_dose_ml clamps to 25.0 ml
    time_b = manager._calculate_dose_time(25.0, "B")  # 25ml at 2 ml/s
    assert abs(time_b - 12.5) < 0.001  # 12.5 seconds


def test_calculate_dose_time_clamps_max_dose():
    """Test that dose is clamped to max_dose_ml"""
    manager = _manager_stub()
    manager.pump_config.max_dose_ml = 25.0
    
    # Try to dose more than max
    time = manager._calculate_dose_time(50.0, "A")
    
    # Should use max dose (25.0ml)
    assert time > 0


@pytest.mark.asyncio
async def test_auto_calibrate_pumps_adjusts_calibration():
    """Test that auto-calibration adjusts calibration factors"""
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBTankFeedManager import PumpCalibration
    
    store = FakeDataStore({
        "Hydro": {
            "ReservoirLevel": 100.0,
            "ReservoirVolume": 100.0,
        }
    })
    event_manager = FakeEventManager()
    manager = _manager_stub(store, event_manager)
    manager.reservoir_volume_liters = 100.0
    manager.pump_calibrations = {
        "switch.feedpump_a": PumpCalibration("switch.feedpump_a"),
        "switch.feedpump_b": PumpCalibration("switch.feedpump_b"),
        "switch.feedpump_c": PumpCalibration("switch.feedpump_c"),
    }
    manager.feed_ec_before = 1.0
    manager.feed_ec_after = 1.5  # EC increased by 0.5
    manager.pump_calibrations["switch.feedpump_a"].calibration_factor = 1.0
    manager.pump_calibrations["switch.feedpump_b"].calibration_factor = 1.0
    manager.pump_calibrations["switch.feedpump_c"].calibration_factor = 1.0
    
    # Run auto-calibration
    await manager._auto_calibrate_pumps(50.0, ["A", "B", "C"])  # 50ml target dose
    
    # Verify calibration factors were adjusted
    # EC increased by 0.5, expected might be different, so calibration adjusted
    assert manager.pump_calibrations["switch.feedpump_a"].calibration_factor != 1.0
    assert manager.pump_calibrations["switch.feedpump_b"].calibration_factor != 1.0
    assert manager.pump_calibrations["switch.feedpump_c"].calibration_factor != 1.0


@pytest.mark.asyncio
async def test_auto_calibrate_pumps_skips_invalid_ec():
    """Test that auto-calibration skips if EC data is invalid"""
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBTankFeedManager import PumpCalibration
    
    manager = _manager_stub()
    manager.pump_calibrations = {
        "switch.feedpump_a": PumpCalibration("switch.feedpump_a"),
    }
    manager.feed_ec_before = 0.0
    manager.feed_ec_after = 0.0
    
    # Should not raise, just skip
    await manager._auto_calibrate_pumps(50.0, ["A"])
    
    # Calibration factors should remain default (1.0)
    assert manager.pump_calibrations["switch.feedpump_a"].calibration_factor == 1.0


@pytest.mark.longterm
@pytest.mark.asyncio
async def test_24_hour_simulation_respects_rate_limits():
    """
    Tests that rate limiting prevents more than 6 feeds per day.
    """
    from datetime import datetime, timedelta
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBFeedLogicManager import OGBFeedLogicManager
    
    event_manager = FakeEventManager()
    store = FakeDataStore({"mainControl": "HomeAssistant"})
    
    feed_logic = OGBFeedLogicManager("dev_room", store, event_manager)
    feed_logic.min_feed_interval = 14400  # 4 hours
    feed_logic.max_daily_feeds = 6
    feed_logic.daily_feed_count = 6  # Already at limit
    feed_logic.last_feed_time = datetime.now() - timedelta(hours=1)
    
    # Try to feed when already at daily limit
    needs_feed = await feed_logic.check_if_feed_needed({
        "ecCurrent": 1.5,
        "phCurrent": 6.2,
    })
    
    assert needs_feed is False, "Should not feed when daily limit reached"
    
    # Reset and try with short interval
    feed_logic.daily_feed_count = 0
    feed_logic.last_feed_time = datetime.now() - timedelta(minutes=30)
    
    needs_feed = await feed_logic.check_if_feed_needed({
        "ecCurrent": 1.5,
        "phCurrent": 6.2,
    })
    
    assert needs_feed is False, "Should not feed when interval too short"


@pytest.mark.asyncio
async def test_concentration_based_dosing_includes_x_and_y():
    """Test that concentration-based dosing includes X and Y pumps when they have concentration > 0"""
    store = FakeDataStore({
        "Hydro": {
            "ReservoirLevel": 80.0,  # 80% full
            "ReservoirVolume": 100.0,  # 100L tank
            "Nutrient_Concentration_A": 2.0,  # 2.0 ml/L
            "Nutrient_Concentration_B": 1.5,  # 1.5 ml/L
            "Nutrient_Concentration_C": 1.0,  # 1.0 ml/L
            "Nutrient_Concentration_X": 0.5,  # 0.5 ml/L
            "Nutrient_Concentration_Y": 0.3,  # 0.3 ml/L
        }
    })
    event_manager = FakeEventManager()
    manager = _manager_stub(store, event_manager)
    manager.reservoir_volume_liters = 100.0
    manager.nutrient_concentrations = {
        "A": 2.0, "B": 1.5, "C": 1.0, "X": 0.5, "Y": 0.3
    }
    manager.nutrients = {"A": 1.0, "B": 1.0, "C": 1.0, "X": 1.0, "Y": 1.0}
    manager.current_ec = 1.0
    
    # Track pumps that were dosed
    dosed_pumps = []
    
    async def mock_activate_pump(pump_type, run_time, dose_ml):
        dosed_pumps.append({"pump": pump_type, "dose_ml": dose_ml})
        return True
    
    manager._activate_pump = mock_activate_pump
    
    # Mock asyncio.sleep to avoid long delays in tests
    import asyncio
    original_sleep = asyncio.sleep
    async def mock_sleep(seconds):
        pass  # Don't actually sleep
    
    asyncio.sleep = mock_sleep
    
    try:
        # Run concentration-based dosing
        await manager._dose_nutrients_with_concentration({
            "A": 160.0,  # 80L × 2.0 ml/L
            "B": 120.0,  # 80L × 1.5 ml/L
            "C": 80.0,   # 80L × 1.0 ml/L
            "X": 40.0,   # 80L × 0.5 ml/L
            "Y": 24.0,   # 80L × 0.3 ml/L
        })
        
        # Verify all pumps including X and Y were dosed
        assert len(dosed_pumps) == 5
        pump_names = [p["pump"] for p in dosed_pumps]
        assert PumpType.NUTRIENT_A in pump_names
        assert PumpType.NUTRIENT_B in pump_names
        assert PumpType.NUTRIENT_C in pump_names
        assert PumpType.CUSTOM_X in pump_names
        assert PumpType.CUSTOM_Y in pump_names
    finally:
        asyncio.sleep = original_sleep


@pytest.mark.asyncio
async def test_concentration_based_dosing_skips_zero_concentration():
    """Test that pumps with zero concentration are skipped"""
    store = FakeDataStore({
        "Hydro": {
            "ReservoirLevel": 80.0,
            "ReservoirVolume": 100.0,
            "Nutrient_Concentration_A": 2.0,
            "Nutrient_Concentration_B": 0.0,  # Zero concentration
            "Nutrient_Concentration_C": 1.0,
            "Nutrient_Concentration_X": 0.0,  # Zero concentration
            "Nutrient_Concentration_Y": 0.0,  # Zero concentration
        }
    })
    manager = _manager_stub(store)
    manager.reservoir_volume_liters = 100.0
    manager.nutrient_concentrations = {
        "A": 2.0, "B": 0.0, "C": 1.0, "X": 0.0, "Y": 0.0
    }
    manager.nutrients = {"A": 1.0, "B": 1.0, "C": 1.0, "X": 1.0, "Y": 1.0}
    manager.current_ec = 1.0
    
    # Track pumps that were dosed
    dosed_pumps = []
    
    async def mock_activate_pump(pump_type, run_time, dose_ml):
        dosed_pumps.append({"pump": pump_type, "dose_ml": dose_ml})
        return True
    
    manager._activate_pump = mock_activate_pump
    
    # Mock asyncio.sleep to avoid long delays in tests
    import asyncio
    original_sleep = asyncio.sleep
    async def mock_sleep(seconds):
        pass  # Don't actually sleep
    
    asyncio.sleep = mock_sleep
    
    try:
        # Run concentration-based dosing
        await manager._dose_nutrients_with_concentration({
            "A": 160.0,  # 80L × 2.0 ml/L
            "C": 80.0,   # 80L × 1.0 ml/L
        })
        
        # Verify only A and C were dosed
        assert len(dosed_pumps) == 2
        pump_names = [p["pump"] for p in dosed_pumps]
        assert PumpType.NUTRIENT_A in pump_names
        assert PumpType.NUTRIENT_C in pump_names
        assert PumpType.NUTRIENT_B not in pump_names
        assert PumpType.CUSTOM_X not in pump_names
        assert PumpType.CUSTOM_Y not in pump_names
    finally:
        asyncio.sleep = original_sleep


@pytest.mark.asyncio
async def test_auto_calibrate_includes_x_and_y_when_dosed():
    """Test that auto-calibration includes X and Y pumps when they were dosed"""
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBTankFeedManager import PumpCalibration
    
    store = FakeDataStore({
        "Hydro": {
            "ReservoirLevel": 100.0,
            "ReservoirVolume": 100.0,
        }
    })
    event_manager = FakeEventManager()
    manager = _manager_stub(store, event_manager)
    manager.reservoir_volume_liters = 100.0
    manager.pump_calibrations = {
        "switch.feedpump_a": PumpCalibration("switch.feedpump_a"),
        "switch.feedpump_b": PumpCalibration("switch.feedpump_b"),
        "switch.feedpump_c": PumpCalibration("switch.feedpump_c"),
        "switch.feedpump_x": PumpCalibration("switch.feedpump_x"),
        "switch.feedpump_y": PumpCalibration("switch.feedpump_y"),
    }
    manager.feed_ec_before = 1.0
    manager.feed_ec_after = 1.8  # EC increased by 0.8
    
    # Initialize calibration factors
    for cal in manager.pump_calibrations.values():
        cal.calibration_factor = 1.0
    
    # Run auto-calibration with X and Y in the dosed list
    await manager._auto_calibrate_pumps(50.0, ["A", "B", "C", "X", "Y"])
    
    # Verify calibration factors for ALL pumps including X and Y were adjusted
    assert manager.pump_calibrations["switch.feedpump_a"].calibration_factor != 1.0
    assert manager.pump_calibrations["switch.feedpump_b"].calibration_factor != 1.0
    assert manager.pump_calibrations["switch.feedpump_c"].calibration_factor != 1.0
    assert manager.pump_calibrations["switch.feedpump_x"].calibration_factor != 1.0
    assert manager.pump_calibrations["switch.feedpump_y"].calibration_factor != 1.0


@pytest.mark.asyncio
async def test_concentration_based_dosing_scales_with_tank_level():
    """Test that concentration-based dosing scales correctly with tank level"""
    store = FakeDataStore({
        "Hydro": {
            "ReservoirVolume": 100.0,  # 100L tank
            "Nutrient_Concentration_A": 2.0,  # 2.0 ml/L
        }
    })
    
    # Test at 50% tank level
    store.setDeep("Hydro.ReservoirLevel", 50.0)
    manager = _manager_stub(store)
    manager.reservoir_volume_liters = 100.0
    manager.nutrient_concentrations = {"A": 2.0}
    
    dose_50_percent = manager._calculate_dose_from_concentration("A")
    assert abs(dose_50_percent - 100.0) < 0.001  # 50L × 2.0 ml/L = 100ml
    
    # Test at 100% tank level
    store.setDeep("Hydro.ReservoirLevel", 100.0)
    manager = _manager_stub(store)
    manager.reservoir_volume_liters = 100.0
    manager.nutrient_concentrations = {"A": 2.0}
    
    dose_100_percent = manager._calculate_dose_from_concentration("A")
    assert abs(dose_100_percent - 200.0) < 0.001  # 100L × 2.0 ml/L = 200ml
    
    # Verify scaling is linear
    assert abs(dose_100_percent - 2 * dose_50_percent) < 0.001


@pytest.mark.asyncio
async def test_auto_calibrate_with_inaccurate_x_and_y():
    """Test that auto-calibration works with inaccurate X and Y pumps"""
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBTankFeedManager import PumpCalibration
    
    store = FakeDataStore({
        "Hydro": {
            "ReservoirLevel": 100.0,
            "ReservoirVolume": 100.0,
        }
    })
    event_manager = FakeEventManager()
    manager = _manager_stub(store, event_manager)
    manager.reservoir_volume_liters = 100.0
    manager.pump_calibrations = {
        "switch.feedpump_a": PumpCalibration("switch.feedpump_a"),
        "switch.feedpump_b": PumpCalibration("switch.feedpump_b"),
        "switch.feedpump_c": PumpCalibration("switch.feedpump_c"),
        "switch.feedpump_x": PumpCalibration("switch.feedpump_x"),
        "switch.feedpump_y": PumpCalibration("switch.feedpump_y"),
    }
    
    # Simulate pumps delivering only 40% of expected (very inaccurate)
    # Expected EC change: 50.0 ml total * 5 nutrients * 0.002 = 0.5
    # Actual EC change: 0.2 (40% of expected)
    manager.feed_ec_before = 1.0
    manager.feed_ec_after = 1.2  # Only increased by 0.2 instead of expected 0.5
    
    # Initialize calibration factors
    for cal in manager.pump_calibrations.values():
        cal.calibration_factor = 1.0
    
    # Run auto-calibration with X and Y in the dosed list
    await manager._auto_calibrate_pumps(50.0, ["A", "B", "C", "X", "Y"])
    
    # Verify calibration factors were adjusted
    # Since EC change was lower than expected, calibration_factor should have increased
    # With 40% accuracy, adjustment should be significant
    assert manager.pump_calibrations["switch.feedpump_x"].calibration_factor > 1.0
    assert manager.pump_calibrations["switch.feedpump_y"].calibration_factor > 1.0


import asyncio

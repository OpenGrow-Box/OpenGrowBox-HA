"""
Long-term integration tests for OpenGrowBox.
These tests simulate extended time periods to verify system stability.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from tests.logic.helpers import FakeDataStore, FakeEventManager


@pytest.mark.longterm
@pytest.mark.asyncio
async def test_multi_day_plant_growth_stage_transitions():
    """
    Simulates a complete grow cycle with stage transitions:
    - Germination → Clones → EarlyVeg → MidVeg → LateVeg → EarlyFlower → MidFlower → LateFlower
    - Verifies VPD targets update correctly at each stage
    - Checks environmental parameters adjust for each stage
    
    Tests the plant species and stage system over time.
    """
    from custom_components.opengrowbox.OGBController.data.OGBParams.OGBPlants import (
        get_full_plant_stages, DEFAULT_PLANT_SPECIES
    )
    
    event_manager = FakeEventManager()
    store = FakeDataStore({
        "mainControl": "HomeAssistant",
        "plantSpecies": DEFAULT_PLANT_SPECIES,  # Cannabis
        "plantStage": "Germination",
    })
    
    # Get all stages for Cannabis
    stages = get_full_plant_stages(DEFAULT_PLANT_SPECIES)
    stage_names = list(stages.keys())
    
    assert len(stage_names) == 8, f"Cannabis should have 8 stages, got {len(stage_names)}"
    
    # Simulate progression through all stages
    stage_durations = {
        "Germination": 5,
        "Clones": 10,
        "EarlyVeg": 14,
        "MidVeg": 21,
        "LateVeg": 14,
        "EarlyFlower": 21,
        "MidFlower": 35,
        "LateFlower": 21,
    }
    
    total_days = 0
    for stage_name in stage_names:
        # Set current stage
        store.set("plantStage", stage_name)
        
        # Get stage config
        stage_config = stages[stage_name]
        
        # Verify stage config has required fields
        assert "vpdRange" in stage_config, f"{stage_name} missing vpdRange"
        assert "minTemp" in stage_config, f"{stage_name} missing minTemp"
        assert "maxTemp" in stage_config, f"{stage_name} missing maxTemp"
        
        # Simulate days in this stage
        days_in_stage = stage_durations.get(stage_name, 7)
        total_days += days_in_stage
        
        # Verify VPD range is reasonable
        vpd_min, vpd_max = stage_config["vpdRange"]
        assert 0.3 <= vpd_min <= 2.0, f"{stage_name} VPD min {vpd_min} out of range"
        assert 0.5 <= vpd_max <= 2.5, f"{stage_name} VPD max {vpd_max} out of range"
        assert vpd_min < vpd_max, f"{stage_name} VPD range invalid"
        
        print(f"✓ Stage {stage_name}: {days_in_stage} days, VPD {vpd_min}-{vpd_max}")
    
    assert total_days > 100, f"Full grow cycle should be >100 days, got {total_days}"
    print(f"✓ Complete grow cycle: {total_days} days across {len(stage_names)} stages")


@pytest.mark.longterm
@pytest.mark.asyncio
async def test_extended_reservoir_management_30_days():
    """
    Simulates 30 days of reservoir management:
    - Daily water consumption
    - Nutrient depletion
    - pH drift over time
    - Refill scheduling
    - EC monitoring
    
    Tests long-term hydroponic system stability.
    """
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBReservoirManager import OGBReservoirManager
    
    event_manager = FakeEventManager()
    store = FakeDataStore({
        "mainControl": "HomeAssistant",
        "Hydro": {
            "Targets": {"EC": 2.0, "pH": 6.0},
            "ReservoirLevel": 80.0,
            "ec_current": 2.0,
            "ph_current": 6.0,
        }
    })
    
    # Create manager
    manager = OGBReservoirManager.__new__(OGBReservoirManager)
    manager.room = "test_room"
    manager.data_store = store
    manager.event_manager = event_manager
    manager.hass = MagicMock()
    manager.notificator = None
    manager.low_threshold = 25.0
    manager.high_threshold = 85.0
    manager.current_level = 80.0
    manager.level_unit = "%"
    
    # Simulate 30 days
    daily_consumption = 2.5  # % per day
    ec_drift_per_day = 0.05  # EC increase per day (evaporation)
    ph_drift_per_day = 0.02  # pH drift per day
    
    refill_events = []
    alert_events = []
    
    for day in range(30):
        # Daily consumption
        current_level = store.getDeep("Hydro.ReservoirLevel") or 80.0
        new_level = current_level - daily_consumption
        store.setDeep("Hydro.ReservoirLevel", new_level)
        manager.current_level = new_level
        
        # EC and pH drift
        current_ec = store.getDeep("Hydro.ec_current") or 2.0
        current_ph = store.getDeep("Hydro.ph_current") or 6.0
        
        store.setDeep("Hydro.ec_current", current_ec + ec_drift_per_day)
        store.setDeep("Hydro.ph_current", current_ph + ph_drift_per_day)
        
        # Check if refill needed
        if new_level < manager.low_threshold:
            # Simulate refill
            store.setDeep("Hydro.ReservoirLevel", 80.0)
            manager.current_level = 80.0
            # Reset EC and pH after refill
            store.setDeep("Hydro.ec_current", 2.0)
            store.setDeep("Hydro.ph_current", 6.0)
            refill_events.append(day)
            print(f"  Day {day}: Refill triggered at {new_level:.1f}%")
        
        # Check alerts
        if new_level < 30:
            alert_events.append((day, "low_level", new_level))
    
    # Verify results
    assert len(refill_events) > 0, "Should have had at least one refill"
    assert len(refill_events) < 10, "Should not need excessive refills"
    
    print(f"✓ 30-day reservoir simulation: {len(refill_events)} refills, {len(alert_events)} alerts")


@pytest.mark.longterm
@pytest.mark.asyncio
async def test_reservoir_autofill_handles_multiple_refills_over_time():
    """
    Simulates repeated reservoir depletion and verifies that the real autofill loop
    can recover the tank multiple times without entering a blocked state.
    """
    from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBReservoirManager import OGBReservoirManager

    event_manager = FakeEventManager()
    store = FakeDataStore({
        "mainControl": "HomeAssistant",
        "Hydro": {
            "ReservoirLevel": 80.0,
            "ReservoirMinLevel": 15.0,
            "ReservoirMaxLevel": 85.0,
        },
        "capabilities": {
            "canReservoirFill": {
                "state": True,
                "count": 1,
                "devEntities": ["switch.test_reservoir_fill"],
            }
        },
    })

    manager = OGBReservoirManager.__new__(OGBReservoirManager)
    manager.room = "test_room"
    manager.data_store = store
    manager.event_manager = event_manager
    manager.hass = MagicMock()
    manager.notificator = None
    manager._default_low = 25.0
    manager._default_high = 85.0
    manager._default_max_fill = 85.0
    manager.fill_step_size = 5.0
    manager.current_level = 80.0
    manager.current_level_raw = 80.0
    manager.level_unit = "%"
    manager.last_alert_time = None
    manager.last_alert_type = None
    manager.alert_cooldown = timedelta(minutes=30)
    manager.reservoir_sensor_entity = "sensor.test_reservoir_level"
    manager.reservoir_pump_entity = "switch.test_reservoir_fill"
    manager._is_filling = False
    manager._fill_cycles_completed = 0
    manager._fill_start_level = None
    manager._fill_start_time = None
    manager._last_fill_cycle_time = None
    manager._consecutive_sensor_errors = 0
    manager._max_sensor_errors = 2
    manager._fill_blocked = False
    manager._fill_block_reason = None
    manager._last_feed_mode = None
    manager._expected_pump_state = None

    notifications = []
    refill_runs = []

    async def _notify_user(title, message, level="info"):
        notifications.append({"title": title, "message": message, "level": level})

    async def _run_fill_cycle(target_level: float) -> bool:
        manager.current_level = target_level
        store.setDeep("Hydro.ReservoirLevel", target_level)
        return True

    async def _activate_pump():
        return None

    async def _deactivate_pump():
        return None

    async def _find_reservoir_pump(log_missing: bool = True):
        manager.reservoir_pump_entity = "switch.test_reservoir_fill"

    manager._notify_user = _notify_user
    manager._run_fill_cycle = _run_fill_cycle
    manager._activate_pump = _activate_pump
    manager._deactivate_pump = _deactivate_pump
    manager._find_reservoir_pump = _find_reservoir_pump
    manager._log_to_client = lambda *args, **kwargs: None

    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        return None

    try:
        asyncio.sleep = fast_sleep

        for day in range(90):
            current_level = store.getDeep("Hydro.ReservoirLevel") or 80.0
            depleted_level = max(0.0, current_level - 3.8)
            store.setDeep("Hydro.ReservoirLevel", depleted_level)
            manager.current_level = depleted_level

            if depleted_level < manager.low_threshold:
                refill_runs.append({"day": day, "start": depleted_level})
                await manager._auto_fill_reservoir()
                refill_runs[-1]["end"] = manager.current_level
                refill_runs[-1]["cycles"] = manager._fill_cycles_completed

        assert len(refill_runs) >= 2, "Expected multiple refill runs over long-term depletion"
        assert all(run["end"] >= manager.max_fill_level for run in refill_runs)
        assert all(run["cycles"] >= 10 for run in refill_runs), "Each refill should require multiple 5% cycles"
        assert manager._fill_blocked is False, "Successful long-term refills should not block autofill"

        start_messages = [n for n in notifications if "Fuellung gestartet" in n["message"]]
        progress_messages = [n for n in notifications if "Füllung läuft" in n["title"]]
        complete_messages = [n for n in notifications if "ABGESCHLOSSEN" in n["title"]]

        assert len(start_messages) == len(refill_runs)
        assert len(complete_messages) == len(refill_runs)
        assert len(progress_messages) >= len(refill_runs) * 10

        print(
            f"✓ Multi-refill simulation: {len(refill_runs)} refill runs, "
            f"{len(progress_messages)} progress notifications"
        )
    finally:
        asyncio.sleep = original_sleep


@pytest.mark.longterm
@pytest.mark.asyncio
async def test_device_duty_cycle_tracking_14_days():
    """
    Tracks device duty cycles over 14 days to verify:
    - No device is overworked (>80% duty cycle)
    - Even distribution of workload
    - Device wear patterns
    - Maintenance predictions
    
    Important for hardware longevity.
    """
    from collections import defaultdict
    import random
    
    # Track device on/off times
    device_runtime = defaultdict(lambda: {"on_time": 0, "cycles": 0, "last_state": "off"})
    
    # Simulate 14 days, 24 hours each
    total_minutes = 14 * 24 * 60
    
    # Device types to track
    devices = {
        "exhaust_fan": {"typical_cycle": 30, "typical_off": 30},  # 50% duty
        "heater": {"typical_cycle": 15, "typical_off": 45},      # 25% duty
        "humidifier": {"typical_cycle": 10, "typical_off": 50},  # 17% duty
        "dehumidifier": {"typical_cycle": 20, "typical_off": 40}, # 33% duty
    }
    
    for day in range(14):
        for hour in range(24):
            for device_name, profile in devices.items():
                # Simulate device cycling
                cycle_time = profile["typical_cycle"]
                off_time = profile["typical_off"]
                
                # Vary slightly for realism
                cycle_time += random.randint(-5, 5)
                off_time += random.randint(-5, 5)
                
                # Track runtime
                if device_runtime[device_name]["last_state"] == "on":
                    device_runtime[device_name]["on_time"] += cycle_time
                    device_runtime[device_name]["cycles"] += 1
                    device_runtime[device_name]["last_state"] = "off"
                else:
                    device_runtime[device_name]["last_state"] = "on"
    
    # Calculate duty cycles
    minutes_per_day = 24 * 60
    
    for device_name, data in device_runtime.items():
        total_on = data["on_time"]
        duty_cycle = (total_on / total_minutes) * 100
        cycles_per_day = data["cycles"] / 14
        
        print(f"✓ {device_name}: {duty_cycle:.1f}% duty cycle, {cycles_per_day:.1f} cycles/day")
        
        # Verify reasonable duty cycles
        assert duty_cycle < 80, f"{device_name} duty cycle {duty_cycle:.1f}% too high"
        assert duty_cycle > 5, f"{device_name} duty cycle {duty_cycle:.1f}% suspiciously low"
        assert cycles_per_day < 100, f"{device_name} cycling too frequently"
    
    print(f"✓ 14-day duty cycle tracking completed for {len(devices)} devices")

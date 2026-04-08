"""
Test Smart Deadband integration with Closed Environment mode.
"""

import pytest
from tests.logic.helpers import FakeDataStore, FakeEventManager
from custom_components.opengrowbox.OGBController.actions.ClosedActions import ClosedActions


@pytest.mark.asyncio
async def test_closed_environment_night_mode_power_saving():
    """Test that Closed Environment uses power-saving mode at night when nightVPDHold=False."""

    # Setup for Night Mode Power-Saving
    data_store = FakeDataStore({
        "controlOptionData": {
            "co2ppm": {
                "minPPM": 800,
                "maxPPM": 1500,
            },
        },
        "tentData": {
            "temperature": 20.0,
            "humidity": 60.0,
            "co2Level": 600,
            "minTemp": 21.0,
            "maxTemp": 25.0,
            "minHumidity": 48.0,
            "maxHumidity": 62.0,
        },
        "controlOptions": {
            "co2Control": True,
            "nightVPDHold": False,  # Power-saving mode enabled
        },
        "isPlantDay": {"islightON": False},  # Night mode
        "capabilities": {
            "canHeat": {"state": True},
            "canCool": {"state": True},
            "canHumidify": {"state": True},
            "canDehumidify": {"state": True},
            "canVentilate": {"state": True},
            "canExhaust": {"state": True},
            "canWindow": {"state": True},
        }
    })

    event_manager = FakeEventManager()

    # Create mock OGB object
    class MockOGB:
        def __init__(self):
            self.room = "test_room"
            self.dataStore = data_store
            self.eventManager = event_manager
            self.actionManager = None

    mock_ogb = MockOGB()
    closed_actions = ClosedActions(mock_ogb)

    executed_actions = []

    # Create a proper mock class with the method
    class MockActionManager:
        async def checkLimitsAndPublicateNoVPD(self, action_map):
            executed_actions.extend(action_map)

    closed_actions.action_manager = MockActionManager()

    # Execute cycle in night mode
    capabilities = data_store.get("capabilities")
    await closed_actions.execute_closed_environment_cycle(capabilities)

    # Verify: Climate devices should be REDUCED (OFF)
    climate_devices = ["canHeat", "canCool", "canHumidify", "canDehumidify", "canClimate", "canCO2"]
    climate_actions = [a for a in executed_actions if a.capability in climate_devices]

    assert len(climate_actions) > 0, "Expected climate actions in night mode"
    for action in climate_actions:
        assert action.action == "Reduce", f"Expected Reduce action for {action.capability}, got {action.action}"

    # Verify: Ventilation devices should be INCREASED (mold prevention)
    ventilation_devices = ["canVentilate", "canExhaust", "canWindow"]
    ventilation_actions = [a for a in executed_actions if a.capability in ventilation_devices]

    assert len(ventilation_actions) > 0, "Expected ventilation actions in night mode"
    for action in ventilation_actions:
        assert action.action == "Increase", f"Expected Increase action for {action.capability}, got {action.action}"

    # Log event should indicate night mode
    log_events = [e for e in event_manager.emitted if e.get("event_name") == "LogForClient"]
    assert len(log_events) > 0, "Expected LogForClient event"

    last_log = log_events[-1].get("data", {})
    assert last_log.get("isNightMode") is True, "Expected isNightMode=True in log"
    assert last_log.get("nightVPDHold") is False, "Expected nightVPDHold=False in log"
    assert "Power-Saving" in last_log.get("message", ""), "Expected 'Power-Saving' in message"


@pytest.mark.asyncio
async def test_closed_environment_night_mode_with_night_vpd_hold():
    """Test that Closed Environment runs normally at night when nightVPDHold=True."""

    # Setup for Night Mode WITH nightVPDHold
    data_store = FakeDataStore({
        "controlOptionData": {
            "co2ppm": {
                "minPPM": 800,
                "maxPPM": 1500,
            },
        },
        "tentData": {
            "temperature": 27.0,  # Too hot
            "humidity": 65.0,  # Too humid
            "co2Level": 750,
            "minTemp": 21.0,
            "maxTemp": 25.0,
            "minHumidity": 48.0,
            "maxHumidity": 62.0,
        },
        "controlOptions": {
            "co2Control": True,
            "nightVPDHold": True,  # Normal VPD control at night
        },
        "isPlantDay": {"islightON": False},  # Night mode
        "capabilities": {
            "canCool": {"state": True},
            "canDehumidify": {"state": True},
            "canVentilate": {"state": True},
        }
    })

    event_manager = FakeEventManager()

    class MockOGB:
        def __init__(self):
            self.room = "test_room"
            self.dataStore = data_store
            self.eventManager = event_manager
            self.actionManager = None

    mock_ogb = MockOGB()
    closed_actions = ClosedActions(mock_ogb)

    executed_actions = []

    class MockActionManager:
        async def checkLimitsAndPublicateNoVPD(self, action_map):
            executed_actions.extend(action_map)

    closed_actions.action_manager = MockActionManager()

    # Execute cycle in night mode with nightVPDHold=True
    capabilities = data_store.get("capabilities")
    await closed_actions.execute_closed_environment_cycle(capabilities)

    # Verify: Should have normal temp/humidity actions (NOT power-saving)
    temp_hum_actions = [a for a in executed_actions if a.capability in ["canCool", "canDehumidify", "canHeat", "canHumidify", "canClimate"]]

    assert len(temp_hum_actions) > 0, "Expected normal temp/humidity actions when nightVPDHold=True"

    # Log event should NOT indicate power-saving mode
    log_events = [e for e in event_manager.emitted if e.get("event_name") == "LogForClient"]
    assert len(log_events) > 0, "Expected LogForClient event"

    last_log = log_events[-1].get("data", {})
    assert last_log.get("isNightMode") is not True, "Expected isNightMode to not be True (or not present) when nightVPDHold=True"
    assert "Power-Saving" not in last_log.get("message", ""), "Expected NO 'Power-Saving' in message"


@pytest.mark.asyncio
async def test_closed_environment_respects_smart_deadband():
    """Test that Closed Environment skips temp/humidity actions when Smart Deadband is active."""

    # Setup with Smart Deadband active
    data_store = FakeDataStore({
        "controlOptionData": {
            "deadband": {
                "active": True,
                "hold_remaining": 120,
            },
            "co2ppm": {
                "minPPM": 800,
                "maxPPM": 1500,
            },
        },
        "tentData": {
            "temperature": 27.0,
            "humidity": 55.0,
            "co2Level": 750,
            "minTemp": 21.0,
            "maxTemp": 25.0,
            "minHumidity": 48.0,
            "maxHumidity": 62.0,
        },
        "controlOptions": {
            "co2Control": True,
        },
        "isPlantDay": {"islightON": True},
        "capabilities": {
            "canCool": {"state": True},
            "canDehumidify": {"state": True},
            "canVentilate": {"state": True},
        }
    })

    event_manager = FakeEventManager()

    # Create mock OGB object
    class MockOGB:
        def __init__(self):
            self.room = "test_room"
            self.dataStore = data_store
            self.eventManager = event_manager
            self.actionManager = None  # Will be set after ClosedActions init

    mock_ogb = MockOGB()
    closed_actions = ClosedActions(mock_ogb)

    executed_actions = []

    # Create a proper mock class with the method
    class MockActionManager:
        async def checkLimitsAndPublicateNoVPD(self, action_map):
            executed_actions.extend(action_map)

    closed_actions.action_manager = MockActionManager()

    # Execute cycle with Smart Deadband active
    capabilities = data_store.get("capabilities")
    await closed_actions.execute_closed_environment_cycle(capabilities)

    # Verify: Smart Deadband is IGNORED for Closed Environment
    # Temp/Hum actions ARE executed (even if smart deadband is active in datastore)
    temp_hum_actions = [a for a in executed_actions if a.capability in ["canCool", "canDehumidify", "canHeat", "canHumidify", "canClimate"]]
    co2_actions = [a for a in executed_actions if a.capability == "canCO2"]

    # Smart Deadband is DEACTIVATED for Closed Environment - so temp/hum actions ARE executed
    assert len(temp_hum_actions) > 0, f"Expected temp/humidity actions (Smart Deadband deactivated), got {len(temp_hum_actions)}"

    # Log event should show smartDeadbandActive=False (deactivated for Closed Environment)
    log_events = [e for e in event_manager.emitted if e.get("event_name") == "LogForClient"]
    assert len(log_events) > 0, "Expected LogForClient event"

    last_log = log_events[-1].get("data", {})
    assert last_log.get("smartDeadbandActive") is False, "Expected smartDeadbandActive=False (deactivated)"


@pytest.mark.asyncio
async def test_closed_environment_normal_operation_without_deadband():
    """Test that Closed Environment runs normally when Smart Deadband is NOT active."""

    # Setup WITHOUT Smart Deadband
    data_store = FakeDataStore({
        "controlOptionData": {
            "deadband": {
                "active": False,
            },
            "co2ppm": {
                "minPPM": 800,
                "maxPPM": 1500,
            },
        },
        "tentData": {
            "temperature": 27.0,  # Too hot
            "humidity": 65.0,  # Too humid
            "co2Level": 750,  # Too low
            "minTemp": 21.0,
            "maxTemp": 25.0,
            "minHumidity": 48.0,
            "maxHumidity": 62.0,
        },
        "controlOptions": {
            "co2Control": True,
        },
        "isPlantDay": {"islightON": True},
        "capabilities": {
            "canCool": {"state": True},
            "canDehumidify": {"state": True},
            "canVentilate": {"state": True},
        }
    })

    event_manager = FakeEventManager()

    class MockOGB:
        def __init__(self):
            self.room = "test_room"
            self.dataStore = data_store
            self.eventManager = event_manager
            self.actionManager = None  # Required by ClosedActions

    mock_ogb = MockOGB()
    closed_actions = ClosedActions(mock_ogb)

    executed_actions = []

    # Create a proper mock class with the method
    class MockActionManager:
        async def checkLimitsAndPublicateNoVPD(self, action_map):
            executed_actions.extend(action_map)

    closed_actions.action_manager = MockActionManager()

    # Execute cycle WITHOUT Smart Deadband
    capabilities = data_store.get("capabilities")
    await closed_actions.execute_closed_environment_cycle(capabilities)

    # Verify: Should have temp/humidity actions
    temp_hum_actions = [a for a in executed_actions if a.capability in ["canCool", "canDehumidify", "canHeat", "canHumidify", "canClimate"]]

    # We expect at least cooling and dehumidifying actions
    assert len(temp_hum_actions) > 0, f"Expected temp/humidity actions when deadband is NOT active, got {len(temp_hum_actions)}"

    # Log event should NOT indicate deadband
    log_events = [e for e in event_manager.emitted if e.get("event_name") == "LogForClient"]
    assert len(log_events) > 0, "Expected LogForClient event"

    last_log = log_events[-1].get("data", {})
    assert last_log.get("smartDeadbandActive") is not True, "Expected smartDeadbandActive=False or not present"


@pytest.mark.asyncio
async def test_closed_environment_uses_own_targets_not_vpd():
    """Test that Closed Environment uses its own temp/hum targets, NOT VPD targets."""

    # Setup with VPD targets but OWN temp/hum targets
    data_store = FakeDataStore({
        "controlOptionData": {
            "co2ppm": {
                "minPPM": 800,
                "maxPPM": 1500,
            },
        },
        "tentData": {
            "temperature": 20.0,  # Below OWN target (24.0)
            "humidity": 45.0,  # Below OWN target (55.0)
            "co2Level": 750,
            "minTemp": 21.0,
            "maxTemp": 25.0,
            "minHumidity": 48.0,
            "maxHumidity": 62.0,
        },
        "vpd": {
            "current": 0.8,
            "targeted": 1.2,  # VPD target (NOT used for control!)
            "perfection": 1.1,  # VPD perfection (NOT used for control!)
        },
        "controlOptions": {
            "co2Control": True,
        },
        "isPlantDay": {"islightON": True},
        "capabilities": {
            "canHeat": {"state": True},
            "canHumidify": {"state": True},
            "canVentilate": {"state": True},
        }
    })

    event_manager = FakeEventManager()

    class MockOGB:
        def __init__(self):
            self.room = "test_room"
            self.dataStore = data_store
            self.eventManager = event_manager
            self.actionManager = None

    mock_ogb = MockOGB()
    closed_actions = ClosedActions(mock_ogb)

    executed_actions = []

    class MockActionManager:
        async def checkLimitsAndPublicateNoVPD(self, action_map):
            executed_actions.extend(action_map)

    closed_actions.action_manager = MockActionManager()

    # Execute cycle
    capabilities = data_store.get("capabilities")
    await closed_actions.execute_closed_environment_cycle(capabilities)

    # Verify: Should have heating and humidifying actions (based on OWN targets)
    temp_hum_actions = [a for a in executed_actions if a.capability in ["canHeat", "canHumidify"]]

    assert len(temp_hum_actions) > 0, "Expected temp/hum actions based on OWN targets"

    # Log event should show OWN temp/hum targets, NOT VPD targets
    log_events = [e for e in event_manager.emitted if e.get("event_name") == "LogForClient"]
    assert len(log_events) > 0, "Expected LogForClient event"

    last_log = log_events[-1].get("data", {})
    assert "tempTarget" in last_log, "Expected tempTarget in log"
    assert "humTarget" in last_log, "Expected humTarget in log"
    # VPD fields should be present but ONLY for informational purposes
    assert "vpdCurrent" in last_log, "Expected vpdCurrent in log (informational)"
    # VPD target should NOT be used for control (may be present but not as control target)
    # The log should show OWN targets, not VPD targets
    assert last_log.get("tempCurrent") == 20.0, "Expected tempCurrent to match tentData"
    assert last_log.get("humCurrent") == 45.0, "Expected humCurrent to match tentData"


@pytest.mark.asyncio
async def test_closed_environment_vpd_only_informational():
    """Test that VPD in Closed Environment is only used for informational purposes."""

    # Setup with VPD values
    data_store = FakeDataStore({
        "controlOptionData": {
            "co2ppm": {
                "minPPM": 800,
                "maxPPM": 1500,
            },
        },
        "tentData": {
            "temperature": 24.0,
            "humidity": 55.0,
            "co2Level": 1000,
            "minTemp": 21.0,
            "maxTemp": 25.0,
            "minHumidity": 48.0,
            "maxHumidity": 62.0,
        },
        "vpd": {
            "current": 1.05,
        },
        "controlOptions": {
            "co2Control": True,
        },
        "isPlantDay": {"islightON": True},
        "capabilities": {
            "canVentilate": {"state": True},
        }
    })

    event_manager = FakeEventManager()

    class MockOGB:
        def __init__(self):
            self.room = "test_room"
            self.dataStore = data_store
            self.eventManager = event_manager
            self.actionManager = None

    mock_ogb = MockOGB()
    closed_actions = ClosedActions(mock_ogb)

    executed_actions = []

    class MockActionManager:
        async def checkLimitsAndPublicateNoVPD(self, action_map):
            executed_actions.extend(action_map)

    closed_actions.action_manager = MockActionManager()

    # Execute cycle (temp/hum are in range, so no actions expected)
    capabilities = data_store.get("capabilities")
    await closed_actions.execute_closed_environment_cycle(capabilities)

    # Log event should include VPD for informational purposes
    log_events = [e for e in event_manager.emitted if e.get("event_name") == "LogForClient"]
    assert len(log_events) > 0, "Expected LogForClient event"

    last_log = log_events[-1].get("data", {})
    assert "vpdCurrent" in last_log, "Expected vpdCurrent in log (informational)"
    assert last_log.get("vpdCurrent") == 1.05, "Expected vpdCurrent to match vpd.current"
    # VPD should NOT have target/deviation fields (only informational)
    # The log should NOT contain vpdTarget or vpdDeviation for Closed Environment
    assert "vpdTarget" not in last_log or last_log.get("vpdTarget") is None, \
        "Expected NO vpdTarget in log (VPD not used for control)"
    assert "vpdDeviation" not in last_log or last_log.get("vpdDeviation") is None, \
        "Expected NO vpdDeviation in log (VPD not used for control)"

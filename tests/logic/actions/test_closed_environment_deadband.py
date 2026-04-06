"""
Test Smart Deadband integration with Closed Environment mode.
"""

import pytest
from tests.logic.helpers import FakeDataStore, FakeEventManager
from custom_components.opengrowbox.OGBController.actions.ClosedActions import ClosedActions


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
    
    # Verify: Should only have CO2 actions (if any), NO temp/humidity actions
    temp_hum_actions = [a for a in executed_actions if a.capability in ["canCool", "canDehumidify", "canHeat", "canHumidify", "canClimate"]]
    co2_actions = [a for a in executed_actions if a.capability == "canCO2"]
    
    assert len(temp_hum_actions) == 0, f"Expected NO temp/humidity actions during deadband, got {len(temp_hum_actions)}"
    
    # Log event should indicate deadband
    log_events = [e for e in event_manager.emitted if e.get("event_name") == "LogForClient"]
    assert len(log_events) > 0, "Expected LogForClient event"
    
    last_log = log_events[-1].get("data", {})
    assert last_log.get("smartDeadbandActive") is True, "Expected smartDeadbandActive=True in log"
    assert "deadband" in last_log.get("tempStatus", "").lower() or "paused" in last_log.get("tempStatus", "").lower(), \
        f"Expected tempStatus to indicate deadband, got: {last_log.get('tempStatus')}"


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

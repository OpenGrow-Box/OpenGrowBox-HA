import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
    OGBActionManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


@pytest.mark.asyncio
async def test_humidity_too_high_dehumidify_wins_conflict():
    """
    Test that when humidity is too high, BOTH actions pass through:
    - canDehumidify:Increase (to lower humidity)
    - canHumidify:Reduce (to stop a running humidifier)
    """
    room = "test_room"
    
    # Set humidity too high (above maxHumidity)
    data_store = FakeDataStore({
        "tentData": {
            "temperature": 23.0,
            "humidity": 60.0,  # Too high
            "minTemp": 20.0,
            "maxTemp": 24.0,
            "minHumidity": 45.0,
            "maxHumidity": 55.0,  # 60 > 55 = too high
            "vpd": 1.2,
            "targetVpd": 1.4,
        },
        "capabilities": {
            "canHumidify": {"state": True, "count": 1},
            "canDehumidify": {"state": True, "count": 1},
        },
        "tentMode": "VPD Perfection",
    })
    
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, room)

    # Create actions: Increase vs Reduce (opposite actions)
    actions = [
        OGBActionPublication(
            capability="canHumidify",
            action="Reduce",  # To stop humidifier
            Name=room,
            message="Test action",
            priority="medium"
        ),
        OGBActionPublication(
            capability="canDehumidify",
            action="Increase",  # To lower humidity
            Name=room,
            message="Test action",
            priority="medium"
        ),
    ]

    # Resolve conflicts
    resolved = manager._remove_conflicting_actions(actions)

    # Both should pass through (humidity status triggers bypass)
    assert len(resolved) == 2
    capabilities = [a.capability for a in resolved]
    assert "canHumidify" in capabilities
    assert "canDehumidify" in capabilities


@pytest.mark.asyncio
async def test_humidity_too_low_humidify_wins_conflict():
    """
    Test that when humidity is too low:
    - canHumidify:Increase should be allowed (to raise humidity)
    - canDehumidify:Increase should be blocked (would make it worse)
    - But canDehumidify:Reduce should ALSO be allowed (to stop a running dehumidifier)
    
    The correct behavior is BOTH canHumidify:Increase AND canDehumidify:Reduce
    should pass through when humidity is too low!
    """
    room = "test_room"
    
    # Set humidity too low (below minHumidity)
    data_store = FakeDataStore({
        "tentData": {
            "temperature": 23.0,
            "humidity": 40.0,  # Too low
            "minTemp": 20.0,
            "maxTemp": 24.0,
            "minHumidity": 45.0,  # 40 < 45 = too low
            "maxHumidity": 55.0,
            "vpd": 1.2,
            "targetVpd": 1.4,
        },
        "capabilities": {
            "canHumidify": {"state": True, "count": 1},
            "canDehumidify": {"state": True, "count": 1},
        },
        "tentMode": "VPD Perfection",
    })

    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, room)

    # Create the correct action combination for low humidity:
    # canHumidify:Increase (to raise humidity) + canDehumidify:Reduce (to stop dehumidifier)
    actions = [
        OGBActionPublication(
            capability="canHumidify",
            action="Increase",  # Would help!
            Name=room,
            message="Test action",
            priority="medium"
        ),
        OGBActionPublication(
            capability="canDehumidify",
            action="Reduce",  # To stop running dehumidifier
            Name=room,
            message="Test action",
            priority="medium"
        ),
    ]

    # Resolve conflicts
    resolved = manager._remove_conflicting_actions(actions)

    # BOTH should pass through! (this is the correct behavior for low humidity)
    # canHumidify:Increase raises humidity
    # canDehumidify:Reduce stops the running dehumidifier
    assert len(resolved) == 2
    capabilities = [a.capability for a in resolved]
    assert "canHumidify" in capabilities
    assert "canDehumidify" in capabilities
    
    # Verify the actions are correct
    actions_dict = {a.capability: a.action for a in resolved}
    assert actions_dict["canHumidify"] == "Increase"
    assert actions_dict["canDehumidify"] == "Reduce"


@pytest.mark.asyncio
async def test_reduce_actions_always_pass_through():
    """
    Test that Reduce actions ALWAYS pass through buffer zones regardless of humidity status.
    This is critical to allow devices to stop when needed.
    """
    room = "test_room"

    # Test with both too_high and too_low
    for humidity, min_h, max_h in [(60.0, 45.0, 55.0), (40.0, 45.0, 55.0)]:
        data_store = FakeDataStore({
            "tentData": {
                "temperature": 23.0,
                "humidity": humidity,
                "minTemp": 20.0,
                "maxTemp": 24.0,
                "minHumidity": min_h,
                "maxHumidity": max_h,
                "vpd": 1.2,
                "targetVpd": 1.4,
            },
            "capabilities": {
                "canHumidify": {"state": True, "count": 1},
                "canDehumidify": {"state": True, "count": 1},
            },
            "tentMode": "VPD Perfection",
        })

        event_manager = FakeEventManager()
        manager = OGBActionManager(None, data_store, event_manager, room)

        # Create Reduce actions - buffer zones should NEVER block Reduce actions
        actions = [
            OGBActionPublication(
                capability="canHumidify",
                action="Reduce",
                Name=room,
                message="Test action",
                priority="medium"
            ),
            OGBActionPublication(
                capability="canDehumidify",
                action="Reduce",
                Name=room,
                message="Test action",
                priority="medium"
            ),
        ]

        # Buffer zones should not block Reduce actions
        from custom_components.opengrowbox.OGBController.actions.OGBDampeningActions import OGBDampeningActions

        fake_ogb = type('FakeOGB', (), {
            'room': room,
            'dataStore': data_store,
            'actionManager': manager,
            'eventManager': event_manager,
        })()

        dampening = OGBDampeningActions(fake_ogb)

        tent_data = data_store.get("tentData")
        buffered = dampening._apply_buffer_zones(actions, tent_data)

        # BOTH Reduce actions should pass through buffer zones!
        assert len(buffered) == 2
        capabilities = [a.capability for a in buffered]
        assert "canHumidify" in capabilities
        assert "canDehumidify" in capabilities


@pytest.mark.asyncio
async def test_humidity_too_low_humidify_wins_conflict():
    """
    Test that when humidity is too low, BOTH actions pass through:
    - canHumidify:Increase (to raise humidity)
    - canDehumidify:Reduce (to stop a running dehumidifier)
    """
    room = "test_room"
    
    # Set humidity too low (below minHumidity)
    data_store = FakeDataStore({
        "tentData": {
            "temperature": 23.0,
            "humidity": 40.0,  # Too low
            "minTemp": 20.0,
            "maxTemp": 24.0,
            "minHumidity": 45.0,  # 40 < 45 = too low
            "maxHumidity": 55.0,
            "vpd": 1.2,
            "targetVpd": 1.4,
        },
        "capabilities": {
            "canHumidify": {"state": True, "count": 1},
            "canDehumidify": {"state": True, "count": 1},
        },
        "tentMode": "VPD Perfection",
    })
    
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, room)

    # Create actions: Increase vs Reduce (opposite actions)
    actions = [
        OGBActionPublication(
            capability="canHumidify",
            action="Increase",  # To raise humidity
            Name=room,
            message="Test action",
            priority="medium"
        ),
        OGBActionPublication(
            capability="canDehumidify",
            action="Reduce",  # To stop dehumidifier
            Name=room,
            message="Test action",
            priority="medium"
        ),
    ]

    # Resolve conflicts
    resolved = manager._remove_conflicting_actions(actions)

    # Both should pass through (humidity status triggers bypass)
    assert len(resolved) == 2
    capabilities = [a.capability for a in resolved]
    assert "canHumidify" in capabilities
    assert "canDehumidify" in capabilities


@pytest.mark.asyncio
async def test_humidity_in_range_priority_still_decides():
    """
    Test that when humidity is in range, priority-based conflict resolution still works.
    """
    room = "test_room"
    
    # Set humidity in range
    data_store = FakeDataStore({
        "tentData": {
            "temperature": 23.0,
            "humidity": 50.0,  # In range (45-55)
            "minTemp": 20.0,
            "maxTemp": 24.0,
            "minHumidity": 45.0,
            "maxHumidity": 55.0,
            "vpd": 1.2,
            "targetVpd": 1.4,
        },
        "capabilities": {
            "canHumidify": {"state": True, "count": 1},
            "canDehumidify": {"state": True, "count": 1},
        },
        "tentMode": "VPD Perfection",
    })
    
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, room)

    # Create conflicting actions with different priorities
    actions = [
        OGBActionPublication(
            capability="canHumidify",
            action="Increase",
            Name=room,
            message="Test action",
            priority="high"  # Higher priority
        ),
        OGBActionPublication(
            capability="canDehumidify",
            action="Increase",
            Name=room,
            message="Test action",
            priority="medium"
        ),
    ]

    # Resolve conflicts
    resolved = manager._remove_conflicting_actions(actions)

    # canHumidify should win (higher priority, humidity in range)
    assert len(resolved) == 1
    assert resolved[0].capability == "canHumidify"
    assert resolved[0].action == "Increase"

import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
    OGBActionManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


@pytest.mark.asyncio
async def test_remove_duplicate_actions_keeps_last_occurrence():
    """
    Test that duplicate actions with the same capability are removed,
    keeping the LAST occurrence.
    """
    room = "test_room"
    
    data_store = FakeDataStore({"tentMode": "VPD Perfection"})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, room)

    # Create duplicate actions with same capability but different priorities
    actions = [
        OGBActionPublication(
            capability="canExhaust",
            action="Increase",
            Name=room,
            message="First action",
            priority="low"
        ),
        OGBActionPublication(
            capability="canVentilate",
            action="Increase",
            Name=room,
            message="Ventilation action",
            priority="medium"
        ),
        OGBActionPublication(
            capability="canExhaust",
            action="Increase",
            Name=room,
            message="Second action (should be kept)",
            priority="high"
        ),
        OGBActionPublication(
            capability="canHeat",
            action="Increase",
            Name=room,
            message="Heat action",
            priority="medium"
        ),
    ]

    # Remove duplicates
    unique_actions = manager._remove_duplicate_actions(actions)

    # Should keep the LAST canExhaust (higher priority)
    assert len(unique_actions) == 3
    capabilities = [a.capability for a in unique_actions]
    assert capabilities == ["canExhaust", "canVentilate", "canHeat"]
    
    # Verify the kept action has higher priority
    exhaust_action = next(a for a in unique_actions if a.capability == "canExhaust")
    assert exhaust_action.priority == "high"
    assert "Second action" in exhaust_action.message


@pytest.mark.asyncio
async def test_remove_duplicate_actions_with_all_duplicates():
    """
    Test that when all actions have the same capability, only one is kept.
    """
    room = "test_room"
    
    data_store = FakeDataStore({"tentMode": "VPD Perfection"})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, room)

    # Create multiple duplicate actions
    actions = [
        OGBActionPublication(
            capability="canDehumidify",
            action="Increase",
            Name=room,
            message=f"Action {i}",
            priority="medium"
        )
        for i in range(5)
    ]

    # Remove duplicates
    unique_actions = manager._remove_duplicate_actions(actions)

    # Should keep only the last one
    assert len(unique_actions) == 1
    assert unique_actions[0].capability == "canDehumidify"
    assert "Action 4" in unique_actions[0].message


@pytest.mark.asyncio
async def test_remove_duplicate_actions_preserves_order():
    """
    Test that the order of unique capabilities is preserved.
    """
    room = "test_room"
    
    data_store = FakeDataStore({"tentMode": "VPD Perfection"})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, room)

    # Create actions with some duplicates
    actions = [
        OGBActionPublication(capability="canExhaust", action="Increase", Name=room, message="1", priority="medium"),
        OGBActionPublication(capability="canVentilate", action="Increase", Name=room, message="2", priority="medium"),
        OGBActionPublication(capability="canExhaust", action="Increase", Name=room, message="3 (duplicate)", priority="medium"),
        OGBActionPublication(capability="canHeat", action="Increase", Name=room, message="4", priority="medium"),
        OGBActionPublication(capability="canVentilate", action="Increase", Name=room, message="5 (duplicate)", priority="medium"),
    ]

    # Remove duplicates
    unique_actions = manager._remove_duplicate_actions(actions)

    # Should preserve order of unique capabilities
    assert len(unique_actions) == 3
    capabilities = [a.capability for a in unique_actions]
    assert capabilities == ["canExhaust", "canVentilate", "canHeat"]
    
    # Verify the kept messages are the LAST occurrences
    assert "3 (duplicate)" in unique_actions[0].message
    assert "5 (duplicate)" in unique_actions[1].message

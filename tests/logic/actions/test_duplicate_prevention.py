import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import OGBActionPublication


def test_humidity_correction_skips_existing_actions():
    """Ensure _add_deviation_actions_with_context does not duplicate existing humidity correction actions."""
    # We need to mock the dampening actions class minimally
    from custom_components.opengrowbox.OGBController.actions.OGBDampeningActions import OGBDampeningActions
    
    class FakeDataStore:
        def get(self, key, default=None):
            if key == "capabilities":
                return {"canDehumidify": {"state": True}}
            return default
    
    class FakeOGB:
        room = "test_room"
        dataStore = FakeDataStore()
    
    class FakeActionManager:
        pass
    
    class FakeEventManager:
        pass
    
    dampening = OGBDampeningActions.__new__(OGBDampeningActions)
    dampening.ogb = FakeOGB()
    dampening.action_manager = FakeActionManager()
    dampening.event_manager = FakeEventManager()
    
    # Pre-populate with existing canDehumidify:Increase
    existing_actions = [
        OGBActionPublication(
            Name="test_room",
            capability="canDehumidify",
            action="Increase",
            message="VPD-Target Increase Action",
            priority="medium"
        )
    ]
    
    # Call _add_deviation_actions_with_context with high humidity deviation
    # temp_dev=0 (no temp correction), hum_dev=5 (high humidity)
    result = dampening._add_deviation_actions_with_context(
        existing_actions,
        temp_dev=0,
        hum_dev=5,
        vpd_status="high"
    )
    
    # Count occurrences
    dehumidify_increases = [
        a for a in result
        if getattr(a, "capability", "") == "canDehumidify" and getattr(a, "action", "") == "Increase"
    ]
    
    assert len(dehumidify_increases) == 1, (
        f"Expected 1 canDehumidify:Increase, got {len(dehumidify_increases)}. "
        f"Actions: {[(getattr(a, 'capability', ''), getattr(a, 'action', ''), getattr(a, 'message', '')) for a in result]}"
    )


def test_humidity_correction_adds_when_not_existing():
    """Ensure _add_deviation_actions_with_context adds humidity correction when not already present."""
    from custom_components.opengrowbox.OGBController.actions.OGBDampeningActions import OGBDampeningActions
    
    class FakeDataStore:
        def get(self, key, default=None):
            if key == "capabilities":
                return {"canDehumidify": {"state": True}}
            return default
    
    class FakeOGB:
        room = "test_room"
        dataStore = FakeDataStore()
    
    class FakeActionManager:
        pass
    
    class FakeEventManager:
        pass
    
    dampening = OGBDampeningActions.__new__(OGBDampeningActions)
    dampening.ogb = FakeOGB()
    dampening.action_manager = FakeActionManager()
    dampening.event_manager = FakeEventManager()
    
    # Empty action_map
    existing_actions = []
    
    # Call with high humidity deviation
    result = dampening._add_deviation_actions_with_context(
        existing_actions,
        temp_dev=0,
        hum_dev=5,
        vpd_status="high"
    )
    
    dehumidify_increases = [
        a for a in result
        if getattr(a, "capability", "") == "canDehumidify" and getattr(a, "action", "") == "Increase"
    ]
    
    assert len(dehumidify_increases) == 1, (
        f"Expected 1 canDehumidify:Increase, got {len(dehumidify_increases)}"
    )

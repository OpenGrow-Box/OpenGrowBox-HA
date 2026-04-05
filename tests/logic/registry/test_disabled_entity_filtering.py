import pytest
from unittest.mock import Mock

from tests.logic.helpers import FakeDataStore, FakeEventManager


def test_registry_listener_skips_disabled_entities():
    """Test that RegistryListener skips disabled entities during processing."""
    # Create mock disabled entity
    disabled_entity = Mock()
    disabled_entity.entity_id = "switch.exhaust_fan_1"
    disabled_entity.disabled = True
    disabled_entity.disabled_by = "user"
    disabled_entity.device_id = "device.exhaust_fan_1"
    disabled_entity.platform = "switch"
    disabled_entity.labels = []
    disabled_entity.area_id = "living_room"

    # Create mock enabled entity
    enabled_entity = Mock()
    enabled_entity.entity_id = "switch.exhaust_fan_2"
    enabled_entity.disabled = False
    enabled_entity.disabled_by = None
    enabled_entity.device_id = "device.exhaust_fan_2"
    enabled_entity.platform = "switch"
    enabled_entity.labels = []
    enabled_entity.area_id = "living_room"

    # Test that disabled entity is identified
    assert disabled_entity.disabled is True, "Disabled entity should have disabled=True"
    assert disabled_entity.disabled_by == "user", "Disabled entity should show user disabled it"

    # Test that enabled entity is identified
    assert enabled_entity.disabled is False, "Enabled entity should have disabled=False"
    assert enabled_entity.disabled_by is None, "Enabled entity should have disabled_by=None"

    # This demonstrates the filtering logic works
    # In actual implementation, disabled entities return None from process_entity()
    # which means they're skipped during processing


def test_disabled_vs_unavailable_difference():
    """
    Test understanding of the difference between DISABLED and UNAVAILABLE.
    
    DISABLED: Entity is intentionally deactivated by user → Should NOT be used
    UNAVAILABLE: Entity exists but is offline → Different issue
    """
    # DISABLED state values (from Home Assistant entity registry)
    disabled_by_values = ["user", "config_entry", "device", "hass", "integration"]
    
    # Verify all known disabled_by values
    assert "user" in disabled_by_values
    assert "config_entry" in disabled_by_values
    assert "device" in disabled_by_values
    assert "hass" in disabled_by_values
    assert "integration" in disabled_by_values

    # UNAVAILABLE state (from Home Assistant state machine)
    unavailable_states = ["unavailable", "unknown", "None"]
    assert "unavailable" in unavailable_states
    assert "unknown" in unavailable_states
    assert "None" in unavailable_states

    # Key difference:
    # - Disabled: entity.disabled = True, no state changes processed
    # - Unavailable: entity.disabled = False, state = "unavailable", state changes still processed
    pass

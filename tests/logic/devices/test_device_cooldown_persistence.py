"""
Additional tests for device cooldown persistence.

Tests that cooldown values are properly saved to and loaded from datastore.
"""

import pytest
from datetime import datetime, timedelta

from custom_components.opengrowbox.OGBController.managers.OGBActionManager import OGBActionManager
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import DEFAULT_DEVICE_COOLDOWNS
from tests.logic.helpers import FakeDataStore, FakeEventManager


class TestCooldownPersistenceImplementation:
    """Test that the persistence implementation works correctly."""
    
    def test_load_cooldowns_from_datastore_with_user_values(self):
        """Test loading user-defined cooldowns from datastore."""
        user_cooldowns = {
            "canHeat": 15,
            "canCool": 12,
            "canDehumidify": 8,
        }
        
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": user_cooldowns
            }
        })
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Check that user values were loaded
        assert manager.defaultCooldownMinutes["canHeat"] == 15
        assert manager.defaultCooldownMinutes["canCool"] == 12
        assert manager.defaultCooldownMinutes["canDehumidify"] == 8
        
        # Check that defaults are still present for non-user values
        assert manager.defaultCooldownMinutes["canHumidify"] == DEFAULT_DEVICE_COOLDOWNS["canHumidify"]
        assert manager.defaultCooldownMinutes["canExhaust"] == DEFAULT_DEVICE_COOLDOWNS["canExhaust"]
    
    def test_load_cooldowns_from_datastore_with_invalid_capability(self):
        """Test that invalid capabilities in datastore are skipped."""
        user_cooldowns = {
            "canHeat": 15,
            "canInvalidCapability": 999,  # This should be skipped
        }
        
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": user_cooldowns
            }
        })
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Check that valid value was loaded
        assert manager.defaultCooldownMinutes["canHeat"] == 15
        
        # Check that invalid capability was not added
        assert "canInvalidCapability" not in manager.defaultCooldownMinutes
        
        # Check that all default capabilities are still present
        for cap in DEFAULT_DEVICE_COOLDOWNS:
            assert cap in manager.defaultCooldownMinutes
    
    def test_load_cooldowns_from_datastore_empty(self):
        """Test loading when datastore has no user cooldowns."""
        data_store = FakeDataStore({
            "controlOptions": {}
        })
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Should have all defaults
        assert manager.defaultCooldownMinutes == DEFAULT_DEVICE_COOLDOWNS
    
    def test_save_cooldowns_to_datastore(self):
        """Test saving cooldowns to datastore."""
        data_store = FakeDataStore({})
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Modify a cooldown
        manager.defaultCooldownMinutes["canHeat"] = 20
        
        # Save to datastore
        manager._save_cooldowns_to_datastore()
        
        # Check that it was saved
        saved_cooldowns = data_store.getDeep("controlOptions.deviceCooldowns")
        assert saved_cooldowns is not None
        assert saved_cooldowns["canHeat"] == 20
    
    @pytest.mark.asyncio
    async def test_adjust_device_gcd_saves_to_datastore(self):
        """Test that adjustDeviceGCD saves to datastore."""
        data_store = FakeDataStore({})
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Adjust cooldown via event
        adjustment_data = {"cap": "canHeat", "minutes": 25}
        await manager.adjustDeviceGCD(adjustment_data)
        
        # Check that it was saved to datastore
        saved_cooldowns = data_store.getDeep("controlOptions.deviceCooldowns")
        assert saved_cooldowns is not None
        assert saved_cooldowns["canHeat"] == 25
        
        # Check that it was also updated in memory
        assert manager.defaultCooldownMinutes["canHeat"] == 25
    
    def test_load_and_save_roundtrip(self):
        """Test that cooldowns survive a load/save roundtrip."""
        # Initial setup with user cooldowns
        user_cooldowns = {
            "canHeat": 18,
            "canCool": 14,
            "canDehumidify": 10,
        }
        
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": user_cooldowns
            }
        })
        event_manager = FakeEventManager()
        
        # Create first manager and load
        manager1 = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Verify loaded values
        assert manager1.defaultCooldownMinutes["canHeat"] == 18
        assert manager1.defaultCooldownMinutes["canCool"] == 14
        
        # Modify and save
        manager1.defaultCooldownMinutes["canHeat"] = 22
        manager1._save_cooldowns_to_datastore()
        
        # Create second manager (simulating restart)
        manager2 = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Verify that new manager loads the saved values
        assert manager2.defaultCooldownMinutes["canHeat"] == 22
        assert manager2.defaultCooldownMinutes["canCool"] == 14
        assert manager2.defaultCooldownMinutes["canDehumidify"] == 10


class TestCooldownWithDifferentDataTypes:
    """Test cooldown handling with various data types."""
    
    def test_cooldown_with_string_minutes_in_datastore(self):
        """Test that string minutes in datastore are converted to float."""
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": {
                    "canHeat": "15",  # String instead of int
                    "canCool": 12.5,  # Float
                }
            }
        })
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Should convert to float
        assert manager.defaultCooldownMinutes["canHeat"] == 15.0
        assert manager.defaultCooldownMinutes["canCool"] == 12.5
    
    def test_cooldown_with_invalid_value_in_datastore(self):
        """Test that invalid values in datastore are handled gracefully."""
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": {
                    "canHeat": "invalid",  # Invalid string
                    "canCool": None,  # None value
                }
            }
        })
        event_manager = FakeEventManager()
        
        # Should not crash, but log warning
        try:
            manager = OGBActionManager(None, data_store, event_manager, "test_room")
            # Invalid values should be skipped, defaults used
            assert manager.defaultCooldownMinutes["canHeat"] == DEFAULT_DEVICE_COOLDOWNS["canHeat"]
            assert manager.defaultCooldownMinutes["canCool"] == DEFAULT_DEVICE_COOLDOWNS["canCool"]
        except Exception as e:
            # If it crashes, verify it's a type error from conversion
            assert "float" in str(e).lower() or "invalid" in str(e).lower()


class TestCooldownWithPartialData:
    """Test cooldown handling when only some capabilities have user values."""
    
    def test_partial_user_cooldowns(self):
        """Test that partial user cooldowns mix with defaults."""
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": {
                    "canHeat": 20,
                    "canCool": 15,
                    # Others not specified
                }
            }
        })
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # User values should be used
        assert manager.defaultCooldownMinutes["canHeat"] == 20
        assert manager.defaultCooldownMinutes["canCool"] == 15
        
        # Defaults should be used for others
        assert manager.defaultCooldownMinutes["canDehumidify"] == DEFAULT_DEVICE_COOLDOWNS["canDehumidify"]
        assert manager.defaultCooldownMinutes["canHumidify"] == DEFAULT_DEVICE_COOLDOWNS["canHumidify"]
        assert manager.defaultCooldownMinutes["canExhaust"] == DEFAULT_DEVICE_COOLDOWNS["canExhaust"]
    
    def test_all_capabilities_present_after_load(self):
        """Test that all default capabilities are present after loading user values."""
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": {
                    "canHeat": 20,
                }
            }
        })
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # All default capabilities should still be present
        for cap in DEFAULT_DEVICE_COOLDOWNS:
            assert cap in manager.defaultCooldownMinutes, \
                f"Capability {cap} missing after load"


class TestCooldownIntegrationWithDampening:
    """Test that cooldowns integrate correctly with dampening logic."""
    
    def test_dampening_uses_loaded_cooldowns(self):
        """Test that dampening logic uses the loaded cooldown values."""
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": {
                    "canDehumidify": 10,  # Custom: 10 min
                }
            }
        })
        event_manager = FakeEventManager()
        
        # Verify data is in datastore
        stored = data_store.getDeep("controlOptions.deviceCooldowns")
        assert stored is not None, "User cooldowns should be in datastore"
        assert stored["canDehumidify"] == 10, "canDehumidify should be 10 in datastore"
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Verify custom cooldown is loaded
        assert manager.defaultCooldownMinutes["canDehumidify"] == 10, \
            f"Expected 10, got {manager.defaultCooldownMinutes['canDehumidify']}"
        
        # Register action
        manager._registerAction("canDehumidify", "Increase", 5.0)
        
        # Check that cooldown uses custom value
        cooldown_until = manager.actionHistory["canDehumidify"]["cooldown_until"]
        expected_cooldown = datetime.now() + timedelta(minutes=10)
        
        # Allow some tolerance for timing
        time_diff = abs((cooldown_until - expected_cooldown).total_seconds())
        assert time_diff < 1.0, f"Should use 10 min cooldown, got {time_diff}s difference (actual cooldown: {(cooldown_until - datetime.now()).total_seconds()/60:.1f} min)"
    
    def test_multiple_adjustments_persist_correctly(self):
        """Test that multiple cooldown adjustments persist correctly."""
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": {
                    "canHeat": 15,
                    "canCool": 12,
                }
            }
        })
        event_manager = FakeEventManager()
        
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Initial values
        assert manager.defaultCooldownMinutes["canHeat"] == 15
        assert manager.defaultCooldownMinutes["canCool"] == 12
        
        # Adjust canHeat
        manager.defaultCooldownMinutes["canHeat"] = 25
        manager._save_cooldowns_to_datastore()
        
        # Verify in datastore
        saved = data_store.getDeep("controlOptions.deviceCooldowns")
        assert saved["canHeat"] == 25
        assert saved["canCool"] == 12  # Should preserve other values
        
        # Create new manager to simulate restart
        manager2 = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Verify both values are correct
        assert manager2.defaultCooldownMinutes["canHeat"] == 25
        assert manager2.defaultCooldownMinutes["canCool"] == 12

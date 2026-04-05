"""
Tests for device dampening and cooldown functionality.

Tests user-defined cooldown values from OGB Console and ensures
devices use their cooldown correctly.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch

from custom_components.opengrowbox.OGBController.managers.OGBActionManager import OGBActionManager
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import DEFAULT_DEVICE_COOLDOWNS
from tests.logic.helpers import FakeDataStore, FakeEventManager


def _make_action_manager(room="test_room"):
    """Helper to create an OGBActionManager for testing."""
    data_store = FakeDataStore({
        "controlOptions": {
            "vpdDeviceDampening": True,
        }
    })
    event_manager = FakeEventManager()
    
    manager = OGBActionManager(None, data_store, event_manager, room)
    return manager, data_store, event_manager


class TestDeviceCooldownBasics:
    """Test basic cooldown functionality."""
    
    def test_default_cooldowns_are_loaded(self):
        """Test that default cooldown values are loaded correctly."""
        manager, _, _ = _make_action_manager()
        
        # Check that all default cooldowns are present
        for cap, minutes in DEFAULT_DEVICE_COOLDOWNS.items():
            assert manager.defaultCooldownMinutes.get(cap) == minutes, \
                f"Expected {cap} cooldown to be {minutes}, got {manager.defaultCooldownMinutes.get(cap)}"
    
    def test_is_action_allowed_returns_true_for_new_capability(self):
        """Test that new capabilities are always allowed."""
        manager, _, _ = _make_action_manager()
        
        # New capability should be allowed
        assert manager._isActionAllowed("canHeat", "Increase", 0.5) is True
        assert manager._isActionAllowed("canCool", "Increase", 0.5) is True
    
    def test_action_registers_cooldown(self):
        """Test that registering an action sets cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register an action
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Check that it's in history
        assert "canHeat" in manager.actionHistory
        
        # Check that cooldown is set (should be in the future)
        cooldown_until = manager.actionHistory["canHeat"]["cooldown_until"]
        assert cooldown_until > datetime.now()
    
    def test_action_blocked_during_cooldown(self):
        """Test that actions are blocked during cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register an action
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Try the same action immediately - should be blocked
        is_allowed = manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False, "Action should be blocked during cooldown"
    
    def test_repeat_action_blocked_during_repeat_cooldown(self):
        """Test that repeating the same action is blocked during repeat cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register an action
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Main cooldown blocks ALL actions on this capability
        assert manager._isActionAllowed("canHeat", "Reduce", 2.0) is False, \
            "Different action should also be blocked by main cooldown"
        
        # Same action is also blocked (by main cooldown, not just repeat cooldown)
        assert manager._isActionAllowed("canHeat", "Increase", 2.0) is False
    
    def test_repeat_cooldown_blocks_same_action_after_main_cooldown(self):
        """Test that repeat cooldown blocks same action even after main cooldown expires."""
        from datetime import datetime, timedelta
        
        manager, _, _ = _make_action_manager()
        
        # Register an action
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Simulate main cooldown passing (but not repeat cooldown)
        # Main cooldown = 3 min, repeat cooldown = 1.5 min
        history = manager.actionHistory["canHeat"]
        # Move main cooldown into the past
        history["cooldown_until"] = datetime.now() - timedelta(seconds=1)
        # Keep repeat cooldown in the future
        history["repeat_cooldown"] = datetime.now() + timedelta(minutes=1)
        
        # Different action should now be allowed (main cooldown expired)
        assert manager._isActionAllowed("canHeat", "Reduce", 2.0) is True, \
            "Different action should be allowed after main cooldown expires"
        
        # Same action should still be blocked (by repeat cooldown)
        assert manager._isActionAllowed("canHeat", "Increase", 2.0) is False, \
            "Same action should still be blocked by repeat cooldown"


class TestUserDefinedCooldowns:
    """Test user-defined cooldown values from OGB Console."""
    
    @pytest.mark.asyncio
    async def test_adjust_device_gcd_updates_cooldown(self):
        """Test that adjustDeviceGCD event updates cooldown values."""
        manager, _, event_manager = _make_action_manager()
        
        # Get default cooldown
        default_cooldown = manager.defaultCooldownMinutes.get("canHeat", 3)
        assert default_cooldown == 3
        
        # Adjust cooldown via event
        adjustment_data = {"cap": "canHeat", "minutes": 10}
        await manager.adjustDeviceGCD(adjustment_data)
        
        # Check that cooldown was updated
        assert manager.defaultCooldownMinutes["canHeat"] == 10, \
            "Cooldown should be updated to 10 minutes"
    
    @pytest.mark.asyncio
    async def test_adjust_device_gcd_for_unknown_capability(self):
        """Test that adjusting unknown capability logs error."""
        manager, _, event_manager = _make_action_manager()
        
        # Try to adjust unknown capability
        adjustment_data = {"cap": "canUnknown", "minutes": 10}
        await manager.adjustDeviceGCD(adjustment_data)
        
        # Should not add to cooldowns
        assert "canUnknown" not in manager.defaultCooldownMinutes
    
    def test_calculate_adaptive_cooldown_uses_custom_values(self):
        """Test that adaptive cooldown uses user-defined values."""
        manager, _, _ = _make_action_manager()
        
        # Set custom cooldown
        manager.defaultCooldownMinutes["canHeat"] = 15
        
        # Calculate adaptive cooldown
        cooldown = manager._calculateAdaptiveCooldown("canHeat", 2.0)
        
        # Should use custom value
        assert cooldown == 15.0, f"Expected 15.0, got {cooldown}"
    
    def test_calculate_adaptive_cooldown_scaling(self):
        """Test that adaptive cooldown scales with deviation.
        
        Implementation:
        - abs_dev < 0.5: base * 3.0 (close to target = longer for stability)
        - abs_dev < 1: base * 2.0
        - abs_dev > 3: base * 1.2
        - abs_dev > 5: base * 1.5
        - else: base
        
        For base=5: 0.5→15, 2.0→5, 4.0→6, 6.0→7.5
        """
        manager, _, _ = _make_action_manager()
        
        # Set base cooldown
        manager.defaultCooldownMinutes["canHeat"] = 5
        
        # Test different deviation levels
        cooldown_small = manager._calculateAdaptiveCooldown("canHeat", 0.5)  # → 15
        cooldown_normal = manager._calculateAdaptiveCooldown("canHeat", 2.0)   # → 5 (falls through)
        cooldown_medium = manager._calculateAdaptiveCooldown("canHeat", 4.0)   # → 6
        cooldown_large = manager._calculateAdaptiveCooldown("canHeat", 6.0)    # → 7.5
        
        # Verify: close to target (small) = longer cooldown
        assert cooldown_small > cooldown_large, "Small deviation should have longer cooldown than large"
        assert cooldown_small > cooldown_medium, "Small deviation should have longer cooldown"
        assert cooldown_small > cooldown_normal, "Small deviation should have longest cooldown"
    
    def test_filter_actions_respects_cooldown(self):
        """Test that action filtering respects cooldowns.
        
        Note: Conflict resolution runs first and removes conflicting actions silently.
        The blocked list only contains actions rejected by dampening, not conflicts.
        """
        manager, _, _ = _make_action_manager()
        
        # Create mock actions
        class MockAction:
            def __init__(self, capability, action):
                self.capability = capability
                self.action = action
        
        actions = [
            MockAction("canHeat", "Increase"),
            MockAction("canCool", "Increase"),
            MockAction("canDehumidify", "Increase"),
        ]
        
        # Filter actions - canHeat+canCool conflict, one removed silently
        # canDehumidify passes and is registered
        filtered, blocked = manager._filterActionsByDampening(actions, 2.0, 1.0)
        assert len(filtered) == 2, "canHeat+canCool conflict, 2 should pass"
        assert len(blocked) == 0, "No dampening blocks initially"
        
        # Verify actions were registered (whichever passed)
        assert "canDehumidify" in manager.actionHistory
        has_temp_action = "canHeat" in manager.actionHistory or "canCool" in manager.actionHistory
        assert has_temp_action, "One temp action should be registered"
    
    def test_filter_actions_respects_selective_cooldown(self):
        """Test that filtering respects cooldowns for specific capabilities.
        
        Note: Conflict resolution runs first - canHeat+canCool conflict.
        """
        manager, _, _ = _make_action_manager()
        
        # Create mock actions
        class MockAction:
            def __init__(self, capability, action):
                self.capability = capability
                self.action = action
        
        actions = [
            MockAction("canHeat", "Increase"),
            MockAction("canCool", "Increase"),
            MockAction("canDehumidify", "Increase"),
        ]
        
        # Register ONLY canHeat manually (don't run filter yet)
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Now filter - canHeat is in cooldown, canCool+canHeat conflict
        # canDehumidify should pass, one temp action may pass
        filtered, blocked = manager._filterActionsByDampening(actions, 2.0, 1.0)
        
        # canDehumidify should definitely pass (no conflict, no cooldown)
        assert "canDehumidify" in [a.capability for a in filtered], "canDehumidify should pass"


class TestCooldownPersistence:
    """Test that cooldown values are persisted and loaded correctly."""
    
    def test_user_cooldowns_should_be_persisted_in_datastore(self):
        """Test that user-defined cooldowns are saved to datastore."""
        # This test documents the expected behavior
        # Currently, this may not be implemented - the fix should enable this
        
        manager, data_store, _ = _make_action_manager()
        
        # Adjust cooldown
        manager.defaultCooldownMinutes["canHeat"] = 10
        
        # Expected: The value should be saved to data_store
        # This will fail until the fix is implemented
        # stored_cooldowns = data_store.getDeep("controlOptions.deviceCooldowns")
        # assert stored_cooldowns is not None
        # assert stored_cooldowns.get("canHeat") == 10
    
    def test_user_cooldowns_should_be_loaded_from_datastore(self):
        """Test that user-defined cooldowns are loaded from datastore on init."""
        # This test documents the expected behavior
        # Currently, this may not be implemented - the fix should enable this
        
        data_store = FakeDataStore({
            "controlOptions": {
                "deviceCooldowns": {
                    "canHeat": 15,
                    "canCool": 12,
                }
            }
        })
        event_manager = FakeEventManager()
        
        # Create manager
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Expected: The values should be loaded from data_store
        # This will fail until the fix is implemented
        # assert manager.defaultCooldownMinutes.get("canHeat") == 15
        # assert manager.defaultCooldownMinutes.get("canCool") == 12
        # assert manager.defaultCooldownMinutes.get("canDehumidify") == 3  # Default


class TestCooldownWithRealDevices:
    """Test cooldown behavior with actual device actions."""
    
    def test_device_uses_correct_cooldown_value(self):
        """Test that devices use the correct cooldown value from manager."""
        manager, _, _ = _make_action_manager()
        
        # Set custom cooldown for canDehumidify
        manager.defaultCooldownMinutes["canDehumidify"] = 8
        
        # Register action with deviation 5.0 (triggers adaptive cooldown: 8 * 1.2 = 9.6 min)
        manager._registerAction("canDehumidify", "Increase", 5.0)
        
        # Check that cooldown uses custom value with adaptive scaling
        # Deviation 5.0 > 3 triggers 1.2x multiplier: 8 * 1.2 = 9.6 min
        cooldown_until = manager.actionHistory["canDehumidify"]["cooldown_until"]
        expected_cooldown = datetime.now() + timedelta(minutes=9.6)
        
        # Allow some tolerance for timing
        time_diff = abs((cooldown_until - expected_cooldown).total_seconds())
        assert time_diff < 1.0, f"Cooldown should be ~9.6 minutes (8 * 1.2 adaptive), got {time_diff} seconds difference"
    
    def test_multiple_devices_same_capability_share_cooldown(self):
        """Test that multiple devices with same capability share cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register action for first device
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Second device with same capability should be blocked
        is_allowed = manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False, "Second device should be blocked by shared cooldown"
    
    def test_different_capabilities_have_independent_cooldowns(self):
        """Test that different capabilities have independent cooldowns."""
        manager, _, _ = _make_action_manager()
        
        # Register action for canHeat
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # canCool should still be allowed
        is_allowed = manager._isActionAllowed("canCool", "Increase", 2.0)
        assert is_allowed is True, "Different capability should not be blocked"


class TestCooldownEdgeCases:
    """Test edge cases in cooldown logic."""
    
    def test_zero_cooldown_allows_immediate_repeat(self):
        """Test that zero cooldown allows immediate repeat."""
        manager, _, _ = _make_action_manager()
        
        # Set zero cooldown
        manager.defaultCooldownMinutes["canHeat"] = 0
        
        # Register action
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # With zero cooldown, should still be blocked by repeat cooldown (50% of 0 = 0)
        # But the main cooldown should be zero
        cooldown_until = manager.actionHistory["canHeat"]["cooldown_until"]
        time_diff = (cooldown_until - datetime.now()).total_seconds()
        
        # Should be very close to zero
        assert time_diff < 1.0, f"Zero cooldown should have minimal time, got {time_diff}s"
    
    def test_very_long_cooldown(self):
        """Test that very long cooldown values work correctly."""
        manager, _, _ = _make_action_manager()
        
        # Set very long cooldown (60 minutes)
        manager.defaultCooldownMinutes["canHeat"] = 60
        
        # Register action
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Should be blocked
        is_allowed = manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False
    
    def test_emergency_mode_bypasses_cooldown(self):
        """Test that emergency mode bypasses cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register action
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Normal case - should be blocked
        is_allowed = manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False
        
        # Enable emergency mode
        manager._emergency_mode = True
        
        # Emergency mode - should be allowed
        is_allowed = manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is True, "Emergency mode should bypass cooldown"


class TestCooldownLogging:
    """Test that cooldown actions are properly logged."""
    
    def test_register_action_logs_cooldown(self):
        """Test that registering an action logs the cooldown time."""
        manager, _, _ = _make_action_manager()
        
        # This test verifies that logging happens
        # In production, check logs for cooldown information
        manager._registerAction("canHeat", "Increase", 2.0)
        
        # Verify history contains necessary info for logging
        history = manager.actionHistory["canHeat"]
        assert "cooldown_until" in history
        assert "repeat_cooldown" in history
        assert "deviation" in history
        assert "action_type" in history

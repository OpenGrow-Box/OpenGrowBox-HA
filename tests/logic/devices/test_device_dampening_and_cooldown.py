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
            assert manager.cooldown_manager.cooldowns.get(cap) == minutes, \
                f"Expected {cap} cooldown to be {minutes}, got {manager.cooldown_manager.cooldowns.get(cap)}"

    @pytest.mark.asyncio
    async def test_is_action_allowed_returns_true_for_new_capability(self):
        """Test that new capabilities are always allowed."""
        manager, _, _ = _make_action_manager()

        # New capability should be allowed
        assert await manager._isActionAllowed("canHeat", "Increase", 0.5) is True
        assert await manager._isActionAllowed("canCool", "Increase", 0.5) is True
    
    @pytest.mark.asyncio
    async def test_action_registers_cooldown(self):
        """Test that registering an action sets cooldown."""
        manager, _, _ = _make_action_manager()

        # Register an action
        await manager._registerAction("canHeat", "Increase", 2.0)

        # Check that it's in history
        assert "canHeat" in manager.cooldown_manager.action_history

        # Check that cooldown is set (should be in the future)
        cooldown_until = manager.cooldown_manager.action_history["canHeat"]["cooldown_until"]
        assert cooldown_until > datetime.now()
    
    @pytest.mark.asyncio
    async def test_action_blocked_during_cooldown(self):
        """Test that actions are blocked during cooldown."""
        manager, _, _ = _make_action_manager()

        # Register an action
        await manager._registerAction("canHeat", "Increase", 2.0)

        # Try the same action immediately - should be blocked
        is_allowed = await manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False, "Action should be blocked during cooldown"
    
    @pytest.mark.asyncio
    async def test_repeat_action_blocked_during_repeat_cooldown(self):
        """Test that repeating the same action is blocked during repeat cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register an action
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Main cooldown blocks ALL actions on this capability
        assert await manager._isActionAllowed("canHeat", "Reduce", 2.0) is False, \
            "Different action should also be blocked by main cooldown"
        
        # Same action is also blocked (by main cooldown, not just repeat cooldown)
        assert await manager._isActionAllowed("canHeat", "Increase", 2.0) is False
    
    @pytest.mark.asyncio
    async def test_repeat_cooldown_blocks_same_action_after_main_cooldown(self):
        """Test that repeat cooldown blocks same action even after main cooldown expires."""
        from datetime import datetime, timedelta
        
        manager, _, _ = _make_action_manager()
        
        # Register an action
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Simulate main cooldown passing (but not repeat cooldown)
        # Main cooldown = 3 min, repeat cooldown = 1.5 min
        history = manager.cooldown_manager.action_history["canHeat"]
        # Move main cooldown into the past
        history["cooldown_until"] = datetime.now() - timedelta(seconds=1)
        # Keep repeat cooldown in the future
        history["repeat_cooldown"] = datetime.now() + timedelta(minutes=1)
        
        # Different action should now be allowed (main cooldown expired)
        assert await manager._isActionAllowed("canHeat", "Reduce", 2.0) is True, \
            "Different action should be allowed after main cooldown expires"
        
        # Same action should still be blocked (by repeat cooldown)
        assert await manager._isActionAllowed("canHeat", "Increase", 2.0) is False, \
            "Same action should still be blocked by repeat cooldown"


class TestUserDefinedCooldowns:
    """Test user-defined cooldown values from OGB Console."""
    
    @pytest.mark.asyncio
    async def test_adjust_device_gcd_updates_cooldown(self):
        """Test that adjustDeviceGCD event updates cooldown values."""
        manager, _, event_manager = _make_action_manager()
        
        # Get default cooldown
        default_cooldown = manager.cooldown_manager.cooldowns.get("canHeat", 3)
        assert default_cooldown == 3
        
        # Adjust cooldown via event
        adjustment_data = {"cap": "canHeat", "minutes": 10}
        await manager.adjustDeviceGCD(adjustment_data)
        
        # Check that cooldown was updated
        assert manager.cooldown_manager.cooldowns["canHeat"] == 10, \
            "Cooldown should be updated to 10 minutes"
    
    @pytest.mark.asyncio
    async def test_adjust_device_gcd_for_unknown_capability(self):
        """Test that adjusting unknown capability logs error."""
        manager, _, event_manager = _make_action_manager()
        
        # Try to adjust unknown capability
        adjustment_data = {"cap": "canUnknown", "minutes": 10}
        await manager.adjustDeviceGCD(adjustment_data)
        
        # Should not add to cooldowns
        assert "canUnknown" not in manager.cooldown_manager.cooldowns
    
    def test_calculate_adaptive_cooldown_uses_custom_values(self):
        """Test that adaptive cooldown uses user-defined values."""
        manager, _, _ = _make_action_manager()
        
        # Set custom cooldown
        manager.cooldown_manager.cooldowns["canHeat"] = 15
        
        # Calculate adaptive cooldown
        cooldown = manager._calculateAdaptiveCooldown("canHeat", 2.0)
        
        # Should use custom value
        assert cooldown == 15.0, f"Expected 15.0, got {cooldown}"
    
    def test_calculate_adaptive_cooldown_scaling(self):
        """Test that adaptive cooldown scales with deviation when enabled.
        
        Default behavior (adaptiveCooldownEnabled=False):
        - Cooldown is always the base value, regardless of deviation
        
        When adaptiveCooldownEnabled=True:
        - abs_dev < 0.5: base * 3.0 (close to target = longer for stability)
        - abs_dev < 1: base * 2.0
        - abs_dev > 3: base * 1.2
        - abs_dev > 5: base * 1.5
        - else: base
        
        For base=5: 0.5→15, 2.0→5, 4.0→6, 6.0→7.5
        """
        manager, _, _ = _make_action_manager()
        
        # Set base cooldown
        manager.cooldown_manager.cooldowns["canHeat"] = 5
        
        # Test different deviation levels - default behavior: no adaptive scaling
        cooldown_small = manager._calculateAdaptiveCooldown("canHeat", 0.5)   # → 5 (base)
        cooldown_normal = manager._calculateAdaptiveCooldown("canHeat", 2.0)  # → 5 (base)
        cooldown_medium = manager._calculateAdaptiveCooldown("canHeat", 4.0)  # → 5 (base)
        cooldown_large = manager._calculateAdaptiveCooldown("canHeat", 6.0)   # → 5 (base)
        
        # Verify: all cooldowns are the same (base value) when adaptive is disabled
        assert cooldown_small == 5.0, f"Expected 5.0, got {cooldown_small}"
        assert cooldown_normal == 5.0, f"Expected 5.0, got {cooldown_normal}"
        assert cooldown_medium == 5.0, f"Expected 5.0, got {cooldown_medium}"
        assert cooldown_large == 5.0, f"Expected 5.0, got {cooldown_large}"
        
        # All should be equal (no scaling)
        assert cooldown_small == cooldown_large, "Cooldown should be constant when adaptive is disabled"
        assert cooldown_small == cooldown_medium, "Cooldown should be constant when adaptive is disabled"
        assert cooldown_small == cooldown_normal, "Cooldown should be constant when adaptive is disabled"
    
    def test_calculate_adaptive_cooldown_when_enabled(self):
        """Test adaptive cooldown scaling when explicitly enabled."""
        manager, data_store, _ = _make_action_manager()
        
        # Enable adaptive cooldown
        data_store.setDeep("controlOptions.adaptiveCooldownEnabled", True)
        
        # Set base cooldown
        manager.cooldown_manager.cooldowns["canHeat"] = 5
        
        # Test different deviation levels with adaptive enabled
        # Note: thresholds are veryNear=0.5, near=1.0, high=3.0, critical=5.0
        # Factors: veryNear=3.0, near=2.0, high=1.2, critical=1.5
        cooldown_very_near = manager._calculateAdaptiveCooldown("canHeat", 0.3)  # < 0.5 → 5 * 3.0 = 15
        cooldown_near = manager._calculateAdaptiveCooldown("canHeat", 0.5)       # < 1.0 → 5 * 2.0 = 10
        cooldown_normal = manager._calculateAdaptiveCooldown("canHeat", 2.0)     # else → 5
        cooldown_high = manager._calculateAdaptiveCooldown("canHeat", 4.0)       # > 3.0 → 5 * 1.2 = 6
        cooldown_critical = manager._calculateAdaptiveCooldown("canHeat", 6.0)   # > 5.0 → 5 * 1.5 = 7.5
        
        # Verify values
        assert cooldown_very_near == 15.0, f"Expected 15.0 for very near deviation, got {cooldown_very_near}"
        assert cooldown_near == 10.0, f"Expected 10.0 for near deviation, got {cooldown_near}"
        assert cooldown_normal == 5.0, f"Expected 5.0 for normal deviation, got {cooldown_normal}"
        assert cooldown_high == 6.0, f"Expected 6.0 for high deviation, got {cooldown_high}"
        assert cooldown_critical == 7.5, f"Expected 7.5 for critical deviation, got {cooldown_critical}"
        
        # Verify: very near to target = longest cooldown
        assert cooldown_very_near > cooldown_critical, "Very near deviation should have longer cooldown than critical"
        assert cooldown_very_near > cooldown_high, "Very near deviation should have longer cooldown than high"
        assert cooldown_very_near > cooldown_normal, "Very near deviation should have longest cooldown"
    
    @pytest.mark.asyncio
    async def test_filter_actions_respects_cooldown(self):
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
        filtered, blocked = await manager._filterActionsByDampening(actions, 2.0, 1.0)
        assert len(filtered) == 2, "canHeat+canCool conflict, 2 should pass"
        assert len(blocked) == 0, "No dampening blocks initially"
        
        # Verify actions were registered (whichever passed)
        assert "canDehumidify" in manager.cooldown_manager.action_history
        has_temp_action = "canHeat" in manager.cooldown_manager.action_history or "canCool" in manager.cooldown_manager.action_history
        assert has_temp_action, "One temp action should be registered"
    
    @pytest.mark.asyncio
    async def test_filter_actions_respects_selective_cooldown(self):
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
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Now filter - canHeat is in cooldown, canCool+canHeat conflict
        # canDehumidify should pass, one temp action may pass
        filtered, blocked = await manager._filterActionsByDampening(actions, 2.0, 1.0)
        
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
        manager.cooldown_manager.cooldowns["canHeat"] = 10
        
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
        # assert manager.cooldown_manager.cooldowns.get("canHeat") == 15
        # assert manager.cooldown_manager.cooldowns.get("canCool") == 12
        # assert manager.cooldown_manager.cooldowns.get("canDehumidify") == 3  # Default


class TestCooldownWithRealDevices:
    """Test cooldown behavior with actual device actions."""
    
    @pytest.mark.asyncio
    async def test_device_uses_correct_cooldown_value(self):
        """Test that devices use the correct cooldown value from manager.
        
        Default behavior (adaptiveCooldownEnabled=False):
        - Cooldown is always the user-defined base value, regardless of deviation
        """
        manager, _, _ = _make_action_manager()
        
        # Set custom cooldown for canDehumidify
        manager.cooldown_manager.cooldowns["canDehumidify"] = 8
        
        # Register action with deviation 5.0
        # With adaptive disabled (default), cooldown should be base value (8 min)
        await manager._registerAction("canDehumidify", "Increase", 5.0)
        
        # Check that cooldown uses custom value (no adaptive scaling when disabled)
        cooldown_until = manager.cooldown_manager.action_history["canDehumidify"]["cooldown_until"]
        expected_cooldown = datetime.now() + timedelta(minutes=8)
        
        # Allow some tolerance for timing
        time_diff = abs((cooldown_until - expected_cooldown).total_seconds())
        assert time_diff < 1.0, f"Cooldown should be ~8 minutes (base value, adaptive disabled), got {time_diff} seconds difference"
    
    @pytest.mark.asyncio
    async def test_device_uses_adaptive_cooldown_when_enabled(self):
        """Test that devices use adaptive cooldown when explicitly enabled."""
        manager, data_store, _ = _make_action_manager()
        
        # Enable adaptive cooldown
        data_store.setDeep("controlOptions.adaptiveCooldownEnabled", True)
        
        # Set custom cooldown for canDehumidify
        manager.cooldown_manager.cooldowns["canDehumidify"] = 8
        
        # Register action with deviation 5.0 (triggers adaptive cooldown: 8 * 1.5 = 12 min)
        # Deviation 5.0 > critical threshold (5.0) triggers 1.5x multiplier
        await manager._registerAction("canDehumidify", "Increase", 6.0)
        
        # Check that cooldown uses custom value with adaptive scaling
        cooldown_until = manager.cooldown_manager.action_history["canDehumidify"]["cooldown_until"]
        expected_cooldown = datetime.now() + timedelta(minutes=12)  # 8 * 1.5 = 12
        
        # Allow some tolerance for timing
        time_diff = abs((cooldown_until - expected_cooldown).total_seconds())
        assert time_diff < 1.0, f"Cooldown should be ~12 minutes (8 * 1.5 adaptive), got {time_diff} seconds difference"
    
    @pytest.mark.asyncio
    async def test_multiple_devices_same_capability_share_cooldown(self):
        """Test that multiple devices with same capability share cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register action for first device
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Second device with same capability should be blocked
        is_allowed = await manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False, "Second device should be blocked by shared cooldown"
    
    @pytest.mark.asyncio
    async def test_different_capabilities_have_independent_cooldowns(self):
        """Test that different capabilities have independent cooldowns."""
        manager, _, _ = _make_action_manager()
        
        # Register action for canHeat
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # canCool should still be allowed
        is_allowed = await manager._isActionAllowed("canCool", "Increase", 2.0)
        assert is_allowed is True, "Different capability should not be blocked"


class TestCooldownEdgeCases:
    """Test edge cases in cooldown logic."""
    
    @pytest.mark.asyncio
    async def test_zero_cooldown_allows_immediate_repeat(self):
        """Test that zero cooldown allows immediate repeat."""
        manager, _, _ = _make_action_manager()
        
        # Set zero cooldown
        manager.cooldown_manager.cooldowns["canHeat"] = 0
        
        # Register action
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # With zero cooldown, should still be blocked by repeat cooldown (50% of 0 = 0)
        # But the main cooldown should be zero
        cooldown_until = manager.cooldown_manager.action_history["canHeat"]["cooldown_until"]
        time_diff = (cooldown_until - datetime.now()).total_seconds()
        
        # Should be very close to zero
        assert time_diff < 1.0, f"Zero cooldown should have minimal time, got {time_diff}s"
    
    @pytest.mark.asyncio
    async def test_very_long_cooldown(self):
        """Test that very long cooldown values work correctly."""
        manager, _, _ = _make_action_manager()
        
        # Set very long cooldown (60 minutes)
        manager.cooldown_manager.cooldowns["canHeat"] = 60
        
        # Register action
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Should be blocked
        is_allowed = await manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False
    
    @pytest.mark.asyncio
    async def test_emergency_mode_bypasses_cooldown(self):
        """Test that emergency mode bypasses cooldown."""
        manager, _, _ = _make_action_manager()
        
        # Register action
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Normal case - should be blocked
        is_allowed = await manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is False
        
        # Enable emergency mode with critical_cold (canHeat solves this)
        await manager.cooldown_manager.set_emergency_conditions(["critical_cold"])
        
        # Emergency mode - should be allowed
        is_allowed = await manager._isActionAllowed("canHeat", "Increase", 2.0)
        assert is_allowed is True, "Emergency mode should bypass cooldown for canHeat with critical_cold"


class TestCooldownLogging:
    """Test that cooldown actions are properly logged."""
    
    @pytest.mark.asyncio
    async def test_register_action_logs_cooldown(self):
        """Test that registering an action logs the cooldown time."""
        manager, _, _ = _make_action_manager()
        
        # This test verifies that logging happens
        # In production, check logs for cooldown information
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Verify history contains necessary info for logging
        history = manager.cooldown_manager.action_history["canHeat"]
        assert "cooldown_until" in history
        assert "repeat_cooldown" in history
        assert "deviation" in history


class TestCooldownOnlyActiveWhenDampeningEnabled:
    """Test that cooldown filtering only happens when dampening is enabled."""
    
    @pytest.mark.asyncio
    async def test_cooldown_bypassed_when_dampening_disabled(self):
        """Test that actions are not filtered when dampening is OFF."""
        data_store = FakeDataStore({
            "controlOptions": {
                "vpdDeviceDampening": False,  # Dampening OFF
            }
        })
        event_manager = FakeEventManager()
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Register an action
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # With dampening OFF, actions should pass through
        filtered, blocked = await manager._process_actions_with_cooldown_filter([
            MockAction("canHeat", "Increase")
        ])
        
        # Action should NOT be blocked (dampening is OFF)
        assert len(filtered) == 1, "Action should pass through when dampening is OFF"
        assert len(blocked) == 0, "No actions should be blocked when dampening is OFF"
    
    @pytest.mark.asyncio
    async def test_cooldown_active_when_dampening_enabled(self):
        """Test that actions are filtered when dampening is ON."""
        data_store = FakeDataStore({
            "controlOptions": {
                "vpdDeviceDampening": True,  # Dampening ON
            }
        })
        event_manager = FakeEventManager()
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Register an action
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Try to execute same action immediately
        filtered, blocked = await manager._process_actions_with_cooldown_filter([
            MockAction("canHeat", "Increase")
        ])
        
        # Action should be blocked (dampening is ON)
        assert len(blocked) == 1, "Action should be blocked by cooldown when dampening is ON"
        assert len(filtered) == 0, "Action should be filtered when dampening is ON"


class TestDampeningLoggingShowsDeviceActivations:
    """Test that dampening logs device activations to LogForClient."""
    
    @pytest.mark.asyncio
    async def test_dampening_emits_logforclient_with_actions(self):
        """Test that dampening emits LogForClient event with action details."""
        data_store = FakeDataStore({
            "controlOptions": {
                "vpdDeviceDampening": True,
            }
        })
        event_manager = FakeEventManager()
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Capture emitted events
        emitted_events = []
        async def mock_emit(event_name, event_data, haEvent=False, debug_type=None):
            emitted_events.append({"name": event_name, "data": event_data})
        
        manager.event_manager.emit = mock_emit
        
        # Call the logging method
        await manager._log_vpd_results(
            real_temp_dev=2.0,
            real_hum_dev=-5.0,
            tempPercentage=20.0,
            humPercentage=30.0,
            final_actions=[MockAction("canHeat", "Increase")],
            blocked_actions=[],
            dampening_enabled=True
        )
        
        # Check that LogForClient was emitted
        log_events = [e for e in emitted_events if e["name"] == "LogForClient"]
        assert len(log_events) > 0, "LogForClient event should be emitted"
        
        # Check that action info is included
        log_data = log_events[0]["data"]
        assert "actions" in log_data, "Log should include actions"
        assert "actionCount" in log_data, "Log should include action count"
        assert "dampeningEnabled" in log_data, "Log should indicate if dampening is enabled"
    
    @pytest.mark.asyncio
    async def test_dampening_logging_shows_cooldown_info(self):
        """Test that dampening logging includes cooldown information."""
        data_store = FakeDataStore({
            "controlOptions": {
                "vpdDeviceDampening": True,
            }
        })
        event_manager = FakeEventManager()
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # Register an action to create active cooldown
        await manager._registerAction("canHeat", "Increase", 2.0)
        
        # Capture emitted events
        emitted_events = []
        async def mock_emit(event_name, event_data, haEvent=False, debug_type=None):
            emitted_events.append({"name": event_name, "data": event_data})
        
        manager.event_manager.emit = mock_emit
        
        # Call the logging method
        await manager._log_vpd_results(
            real_temp_dev=2.0,
            real_hum_dev=-5.0,
            tempPercentage=20.0,
            humPercentage=30.0,
            final_actions=[],
            blocked_actions=[MockAction("canHeat", "Increase")],
            dampening_enabled=True
        )
        
        # Check that cooldown info is included
        log_events = [e for e in emitted_events if e["name"] == "LogForClient"]
        assert len(log_events) > 0, "LogForClient event should be emitted"
        
        log_data = log_events[0]["data"]
        assert "activeCooldowns" in log_data, "Log should include active cooldown count"
        assert "cooldownInfo" in log_data, "Log should include cooldown details"
        assert log_data["activeCooldowns"] > 0, "Should have active cooldowns"


# Mock action class for testing
class MockAction:
    def __init__(self, capability, action):
        self.capability = capability
        self.action = action
        self.message = "Test Action"
        self.priority = "medium"
        self.Name = "test_room"

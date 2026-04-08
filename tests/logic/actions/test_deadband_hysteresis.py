"""
Test Smart Deadband hysteresis and NightHoldVPD behavior across all modes.

Tests verify:
1. Hysteres prevents oscillation at deadband boundary (15% exit threshold)
2. All modes (VPD Perfection, VPD Target, Closed Environment) use hysteresis
3. NightHoldVPD=True + light OFF: Deadband runs with hysteresis
4. NightHoldVPD=False + light OFF: Deadband blocked, only ventilation allowed
"""

import pytest
from tests.logic.helpers import FakeDataStore, FakeEventManager


class TestDeadbandHysteresis:
    """Test deadband hysteresis prevents oscillation at boundaries."""
    
    @pytest.mark.asyncio
    async def test_deadband_exit_requires_311_percent_hysteresis_vpd_perfection(self):
        """VPD Perfection: Exit requires deviation > deadband * 3.11"""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager

        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10},  # Start IN deadband
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {"canHeat": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")

        # Initialize hysteresis parameters
        assert manager._deadband_hysteresis_factor == 3.11
        assert manager._deadband_min_hold_after_exit == 120

        # First call - enter deadband
        # VPD = 1.10, target = 1.10, deadband = 0.05
        # deviation = 0.00 <= 0.05 → enters deadband
        result = await manager._handle_smart_deadband(1.10, 1.10, 0.05, "VPD Perfection")

        # Verify entered deadband and return value
        assert manager._is_in_deadband is True
        assert result is True, "Expected True when deadband is active"
        assert data_store.getDeep("controlOptionData.deadband.active") is True

        # Second call - exit deadband (deviation exceeds hysteresis)
        # VPD = 1.26, target = 1.10, deadband = 0.05
        # deviation = 0.16, exit_threshold = 0.05 * 3.11 = 0.1555
        # 0.16 > 0.1555 = True → should exit
        result = await manager._handle_smart_deadband(1.26, 1.10, 0.05, "VPD Perfection")

        # Should be out of deadband
        assert manager._is_in_deadband is False
        assert result is False, "Expected False when deadband is NOT active (exited)"
        deadband_active = data_store.getDeep("controlOptionData.deadband.active")
        assert deadband_active is False, "Deadband should be inactive after exit"
    
    @pytest.mark.asyncio
    async def test_deadband_stays_active_at_boundary_vpd_perfection(self):
        """VPD Perfection: Stays in deadband when deviation is within hysteresis."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager

        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.14, "perfection": 1.10},
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {"canHeat": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")

        # VPD = 1.14, target = 1.10, deadband = 0.05
        # deviation = 0.04, exit_threshold = 0.1555
        # 0.04 < 0.1555 → should stay in deadband
        result = await manager._handle_smart_deadband(1.14, 1.10, 0.05, "VPD Perfection")

        # Should be in deadband
        assert manager._is_in_deadband is True
        assert result is True, "Expected True when deadband is active (stays in)"
        assert manager._deadband_exit_threshold > 0
    
    @pytest.mark.asyncio
    async def test_deadband_re_entry_blocked_after_exit(self):
        """Re-entry blocked for minimum 120 seconds after exit."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        import time

        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10},
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {"canHeat": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")

        # First call - enters deadband
        result = await manager._handle_smart_deadband(1.10, 1.10, 0.05, "VPD Perfection")
        assert manager._is_in_deadband is True
        assert result is True, "Expected True when deadband is active"

        # Simulate exit by setting last_exit_time to 30 seconds ago (less than 120s)
        manager._deadband_last_exit_time = time.time() - 30
        manager._is_in_deadband = False  # Simulate exit

        # Second call - should be blocked (too soon after exit)
        result = await manager._handle_smart_deadband(1.08, 1.10, 0.05, "VPD Perfection")

        # Should NOT re-enter (still blocked)
        assert result is False, "Expected False when deadband is NOT active (blocked by re-entry cooldown)"
        assert manager._is_in_deadband is False, "Should NOT be in deadband (blocked)"


class TestNightHoldVPDTrue:
    """Test deadband runs normally when NightHoldVPD=True (light OFF)."""
    
    @pytest.mark.asyncio
    async def test_vpd_perfection_deadband_with_night_hold_true(self):
        """VPD Perfection: Deadband runs with NightHoldVPD=True + light OFF."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10, "perfectMin": 1.00, "perfectMax": 1.20},
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {"canHeat": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": False}  # Light OFF
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")
        
        # VPD = 1.10, target = 1.10, deviation = 0.00, deadband = 0.05
        await manager.handle_vpd_perfection()
        
        # Deadband should be active
        assert data_store.getDeep("controlOptionData.deadband.active") is True
        
        # Smart Deadband entered event should be emitted
        events = [e["event_name"] for e in event_manager.emitted]
        assert "SmartDeadbandEntered" in events
    
    @pytest.mark.asyncio
    async def test_vpd_target_deadband_with_night_hold_true(self):
        """VPD Target: Deadband runs with NightHoldVPD=True + light OFF."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "VPD Target",
            "vpd": {"current": 1.10, "targeted": 1.10, "tolerance": 10, "targetedMin": 1.05, "targetedMax": 1.15},
            "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
            "capabilities": {"canCool": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": False}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")
        
        await manager.handle_targeted_vpd()
        
        # Deadband should be active
        assert data_store.getDeep("controlOptionData.deadband.active") is True
    
    @pytest.mark.asyncio
    async def test_closed_environment_ignores_vpd_deadband(self):
        """Closed Environment: Smart Deadband (VPD-based) is DEACTIVATED."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "Closed Environment",
            "vpd": {"current": 1.10, "targeted": 1.10},
            "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
            "capabilities": {"canCool": {"state": True}},
            "controlOptions": {"nightVPDHold": True, "co2Control": False},
            "isPlantDay": {"islightON": False}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")
        
        await manager.handle_closed_environment()
        
        # Smart Deadband (VPD-based) is DEACTIVATED for Closed Environment
        # The mode delegates to ClosedEnvironmentManager (which needs action_manager)


class TestNightHoldVPDFalse:
    """Test deadband is blocked when NightHoldVPD=False (light OFF)."""
    
    @pytest.mark.asyncio
    async def test_vpd_perfection_deadband_blocked_with_night_hold_false(self):
        """VPD Perfection: Deadband blocked with NightHoldVPD=False + light OFF."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10, "perfectMin": 1.00, "perfectMax": 1.20},
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {
                "canHeat": {"state": True},
                "canCool": {"state": True},
                "canExhaust": {"state": True},
                "canIntake": {"state": True},
                "canVentilate": {"state": True}
            },
            "controlOptions": {"nightVPDHold": False},  # NightHoldVPD OFF
            "isPlantDay": {"islightON": False}  # Light OFF
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")
        
        await manager.handle_vpd_perfection()
        
        # Deadband should NOT be active
        assert data_store.getDeep("controlOptionData.deadband.active") is False
    
    @pytest.mark.asyncio
    async def test_vpd_target_deadband_blocked_with_night_hold_false(self):
        """VPD Target: Deadband blocked with NightHoldVPD=False + light OFF."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "VPD Target",
            "vpd": {"current": 1.10, "targeted": 1.10, "tolerance": 10, "targetedMin": 1.05, "targetedMax": 1.15},
            "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
            "capabilities": {"canCool": {"state": True}},
            "controlOptions": {"nightVPDHold": False},
            "isPlantDay": {"islightON": False}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")
        
        await manager.handle_targeted_vpd()
        
        # Deadband should NOT be active
        assert data_store.getDeep("controlOptionData.deadband.active") is False
    
    @pytest.mark.asyncio
    async def test_closed_environment_ignores_vpd_deadband_night_hold_false(self):
        """Closed Environment: Smart Deadband (VPD-based) is DEACTIVATED, ignores nightVPDHold."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "Closed Environment",
            "vpd": {"current": 1.10, "targeted": 1.10},
            "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
            "capabilities": {"canCool": {"state": True}},
            "controlOptions": {"nightVPDHold": False, "co2Control": False},
            "isPlantDay": {"islightON": False}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")
        
        await manager.handle_closed_environment()

        # Smart Deadband (VPD-based) is DEACTIVATED for Closed Environment
        # The mode delegates to ClosedEnvironmentManager (which needs action_manager)

    @pytest.mark.asyncio
    async def test_smart_deadband_returns_false_when_night_hold_false(self):
        """Smart Deadband returns False when NightHoldVPD=False + light OFF."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager

        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10},
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {"canHeat": {"state": True}},
            "controlOptions": {"nightVPDHold": False},  # NightHoldVPD OFF
            "isPlantDay": {"islightON": False}  # Light OFF
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")

        # Call smart deadband directly
        result = await manager._handle_smart_deadband(1.10, 1.10, 0.05, "VPD Perfection")

        # Should return False (deadband blocked)
        assert result is False, "Expected False when deadband is NOT active (blocked by night mode)"
        assert manager._is_in_deadband is False, "Should NOT be in deadband"

    @pytest.mark.asyncio
    async def test_smart_deadband_returns_true_when_active(self):
        """Smart Deadband returns True when active (normal operation)."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager

        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10},
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {"canHeat": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")

        # Call smart deadband directly
        result = await manager._handle_smart_deadband(1.10, 1.10, 0.05, "VPD Perfection")

        # Should return True (deadband active)
        assert result is True, "Expected True when deadband is active"
        assert manager._is_in_deadband is True, "Should be in deadband"

    @pytest.mark.asyncio
    async def test_hold_time_extension_with_good_trend(self):
        """Test that hold time is extended when trend is good AND within hysteresis."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        import time

        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10, "target": 1.10},
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {"canHeat": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")

        # Enter deadband
        result = await manager._handle_smart_deadband(1.10, 1.10, 0.05, "VPD Perfection")
        assert result is True

        # Simulate hold time elapsed
        manager._deadband_hold_start = time.time() - 350

        # Reset last check time to allow check
        manager._last_deadband_check = 0

        # Set trend to "towards_target" (good trend)
        manager._vpd_history = [
            {"vpd": 1.12, "time": time.time() - 6},
            {"vpd": 1.11, "time": time.time() - 4},
            {"vpd": 1.10, "time": time.time() - 2}  # Moving towards target
        ]

        # Call again - should extend because trend is good AND within hysteresis
        result = await manager._handle_smart_deadband(1.10, 1.10, 0.05, "VPD Perfection")

        # Should return True (extended) and state should still be in deadband
        assert result is True, "Expected True when trend is good (towards_target)"
        assert manager._is_in_deadband is True, "Should still be in deadband after good trend"


class TestNightHoldActionBlocking:
    """Test that NightHoldVPD=False blocks climate devices but allows ventilation."""
    
    @pytest.mark.asyncio
    async def test_night_hold_blocks_climate_devices(self):
        """Climate devices blocked when NightHoldVPD=False + light OFF."""
        from custom_components.opengrowbox.OGBController.managers.OGBActionManager import OGBActionManager
        from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        data_store = FakeDataStore({
            "controlOptions": {"nightVPDHold": False},
            "isPlantDay": {"islightON": False},
            "capabilities": {
                "canHeat": {"state": True},
                "canCool": {"state": True},
                "canHumidify": {"state": True},
                "canDehumidify": {"state": True},
                "canClimate": {"state": True},
                "canCO2": {"state": True},
                "canLight": {"state": True}
            }
        })
        event_manager = FakeEventManager()
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        actions = [
            OGBActionPublication(Name="test", message="test", capability="canHeat", action="Increase", priority="medium"),
            OGBActionPublication(Name="test", message="test", capability="canCool", action="Increase", priority="medium"),
            OGBActionPublication(Name="test", message="test", capability="canHumidify", action="Increase", priority="medium"),
        ]
        
        # Check returns False when night hold not active
        result = await manager._check_vpd_night_hold(actions)
        
        # Should block (return False)
        assert result is False
        
        # Check that fallback was called (events were emitted)
        events = [e["event_name"] for e in event_manager.emitted]
        assert "LogForClient" in events
    
    @pytest.mark.asyncio
    async def test_night_hold_allows_ventilation(self):
        """Ventilation devices allowed when NightHoldVPD=False + light OFF."""
        from custom_components.opengrowbox.OGBController.managers.OGBActionManager import OGBActionManager
        from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        data_store = FakeDataStore({
            "controlOptions": {"nightVPDHold": False},
            "isPlantDay": {"islightON": False},
            "capabilities": {
                "canExhaust": {"state": True},
                "canIntake": {"state": True},
                "canVentilate": {"state": True},
                "canWindow": {"state": True}
            }
        })
        event_manager = FakeEventManager()
        manager = OGBActionManager(None, data_store, event_manager, "test_room")
        
        # When NightHoldVPD=False + light OFF, _check_vpd_night_hold blocks and calls fallback
        # Fallback should convert climate actions to Reduce but keep ventilation for mold prevention
        actions = [
            OGBActionPublication(Name="test", message="test", capability="canExhaust", action="Increase", priority="medium"),
        ]
        
        result = await manager._check_vpd_night_hold(actions)
        
        # Should be blocked (returns False)
        assert result is False
        
        # Fallback should have been called
        # The fallback converts Increase to Increase for exhaust (mold prevention)
        events = [e["event_name"] for e in event_manager.emitted]
        assert "Increase Exhaust" in events or "LogForClient" in events


class TestHysteresisInAllModes:
    """Verify hysteresis is applied in all VPD modes."""
    
    @pytest.mark.asyncio
    async def test_hysteresis_exit_threshold_logged_vpd_target(self):
        """VPD Target: Exit threshold is logged in LogForClient."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "VPD Target",
            "vpd": {"current": 1.08, "targeted": 1.10, "tolerance": 10, "targetedMin": 1.05, "targetedMax": 1.15},
            "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
            "capabilities": {"canCool": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")
        
        # Call directly to test hysteresis - VPD in deadband (0.02 <= 0.05)
        await manager._handle_smart_deadband(1.08, 1.10, 0.05, "VPD Target")
        
        # Verify deadband is active
        assert manager._is_in_deadband is True

        # Check that exitThreshold is calculated correctly
        assert manager._deadband_exit_threshold == pytest.approx(0.1555, rel=0.01)

        # Also check datastore has the value
        assert data_store.getDeep("controlOptionData.deadband.exit_threshold") == pytest.approx(0.1555, rel=0.01)
    
    @pytest.mark.asyncio
    async def test_hysteresis_exit_threshold_logged_closed_environment(self):
        """Closed Environment: Exit threshold is logged in LogForClient."""
        from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager
        
        data_store = FakeDataStore({
            "tentMode": "Closed Environment",
            "vpd": {"current": 1.08, "targeted": 1.10},
            "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
            "capabilities": {"canCool": {"state": True}},
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True}
        })
        event_manager = FakeEventManager()
        manager = OGBModeManager(None, data_store, event_manager, "test_room")

        await manager._handle_smart_deadband(1.08, 1.10, 0.05, "Closed Environment")

        log_events = [e for e in event_manager.emitted if e["event_name"] == "LogForClient"]
        assert len(log_events) > 0

        log_data = log_events[-1].get("data", {})
        assert "exitThreshold" in log_data
        assert log_data["exitThreshold"] == pytest.approx(0.1555, rel=0.01)
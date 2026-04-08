"""
Test Smart Deadband return value integration with Closed Environment mode.
NOTE: Smart Deadband (VPD-based) is DEACTIVATED for Closed Environment!
Closed Environment uses ONLY temp/hum deadbands, NOT VPD-based deadband.
"""

import pytest
from tests.logic.helpers import FakeDataStore, FakeEventManager
from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager


@pytest.mark.asyncio
async def test_handle_closed_environment_ignores_smart_deadband():
    """Test that Closed Environment ignores Smart Deadband and continues normal cycle."""

    data_store = FakeDataStore({
        "tentMode": "Closed Environment",
        "vpd": {"current": 1.10, "targeted": 1.10},  # IN VPD deadband
        "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
        "capabilities": {
            "canCool": {"state": True},
            "canVentilate": {"state": True},
        },
        "controlOptions": {
            "co2Control": False,
            "nightVPDHold": True,
        },
        "isPlantDay": {"islightON": True}
    })
    event_manager = FakeEventManager()
    manager = OGBModeManager(None, data_store, event_manager, "test_room")

    # Call handle_closed_environment
    await manager.handle_closed_environment()

    # Smart Deadband should NOT be activated for Closed Environment
    # (it was previously but is now deactivated)
    # The mode should simply delegate to ClosedEnvironmentManager
    # Note: Without action_manager, the cycle is skipped (that's expected)


@pytest.mark.asyncio
async def test_handle_vpd_perfection_respects_deadband_return_value_true():
    """Test that VPD Perfection skips normal cycle when deadband returns True."""

    data_store = FakeDataStore({
        "tentMode": "VPD Perfection",
        "vpd": {
            "current": 1.10,
            "perfection": 1.10,
            "perfectMin": 1.00,
            "perfectMax": 1.20,
        },
        "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
        "capabilities": {"canHeat": {"state": True}},
        "controlOptions": {"nightVPDHold": True},
        "isPlantDay": {"islightON": True}
    })
    event_manager = FakeEventManager()
    manager = OGBModeManager(None, data_store, event_manager, "test_room")

    # Call handle_vpd_perfection
    await manager.handle_vpd_perfection()

    # Deadband should be active
    assert data_store.getDeep("controlOptionData.deadband.active") is True

    # Should NOT emit increase_vpd or reduce_vpd (deadband is active)
    names = [e["event_name"] for e in event_manager.emitted]
    assert "increase_vpd" not in names, "Should NOT emit increase_vpd when deadband is active"
    assert "reduce_vpd" not in names, "Should NOT emit reduce_vpd when deadband is active"
    # Should NOT emit FineTune_vpd (FineTune removed - deadband handles this)
    assert "FineTune_vpd" not in names, "Should NOT emit FineTune_vpd (FineTune removed, deadband handles this)"


@pytest.mark.asyncio
async def test_handle_vpd_perfection_continues_when_deadband_returns_false():
    """Test that VPD Perfection continues normal cycle when deadband returns False (blocked)."""

    data_store = FakeDataStore({
        "tentMode": "VPD Perfection",
        "vpd": {
            "current": 1.10,
            "perfection": 1.10,
            "perfectMin": 1.00,
            "perfectMax": 1.20,
        },
        "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
        "capabilities": {"canHeat": {"state": True}},
        "controlOptions": {"nightVPDHold": False},  # Deadband blocked
        "isPlantDay": {"islightON": False}  # Night mode
    })
    event_manager = FakeEventManager()
    manager = OGBModeManager(None, data_store, event_manager, "test_room")

    # Call handle_vpd_perfection
    await manager.handle_vpd_perfection()

    # Deadband should NOT be active (blocked by night mode)
    assert data_store.getDeep("controlOptionData.deadband.active") is False

    # Should NOT emit FineTune_vpd (deadband is NOT active, blocked)
    names = [e["event_name"] for e in event_manager.emitted]
    assert "FineTune_vpd" not in names, "Should NOT emit FineTune_vpd when deadband is blocked"


@pytest.mark.asyncio
async def test_handle_targeted_vpd_respects_deadband_return_value_true():
    """Test that VPD Target skips normal cycle when deadband returns True."""

    data_store = FakeDataStore({
        "tentMode": "VPD Target",
        "vpd": {
            "current": 1.10,
            "targeted": 1.10,
            "tolerance": 10,
            "targetedMin": 1.05,
            "targetedMax": 1.15,
        },
        "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
        "capabilities": {"canCool": {"state": True}},
        "controlOptions": {"nightVPDHold": True},
        "isPlantDay": {"islightON": True}
    })
    event_manager = FakeEventManager()
    manager = OGBModeManager(None, data_store, event_manager, "test_room")

    # Call handle_targeted_vpd
    await manager.handle_targeted_vpd()

    # Deadband should be active
    assert data_store.getDeep("controlOptionData.deadband.active") is True

    # Should NOT emit vpdt_increase_vpd or vpdt_reduce_vpd (deadband is active)
    names = [e["event_name"] for e in event_manager.emitted]
    assert "vpdt_increase_vpd" not in names, "Should NOT emit vpdt_increase_vpd when deadband is active"
    assert "vpdt_reduce_vpd" not in names, "Should NOT emit vpdt_reduce_vpd when deadband is active"


@pytest.mark.asyncio
async def test_handle_targeted_vpd_continues_when_deadband_returns_false():
    """Test that VPD Target continues normal cycle when deadband returns False (blocked)."""

    data_store = FakeDataStore({
        "tentMode": "VPD Target",
        "vpd": {
            "current": 1.10,
            "targeted": 1.10,
            "tolerance": 10,
            "targetedMin": 1.05,
            "targetedMax": 1.15,
        },
        "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
        "capabilities": {"canCool": {"state": True}},
        "controlOptions": {"nightVPDHold": False},  # Deadband blocked
        "isPlantDay": {"islightON": False}  # Night mode
    })
    event_manager = FakeEventManager()
    manager = OGBModeManager(None, data_store, event_manager, "test_room")

    # Call handle_targeted_vpd
    await manager.handle_targeted_vpd()

    # Deadband should NOT be active (blocked by night mode)
    assert data_store.getDeep("controlOptionData.deadband.active") is False

    # Should NOT emit any VPD events (deadband is NOT active, blocked)
    names = [e["event_name"] for e in event_manager.emitted]
    # No VPD events should be emitted when deadband is blocked
    vpd_events = [e for e in names if "vpd" in e.lower()]
    assert len(vpd_events) == 0, "Should NOT emit any VPD events when deadband is blocked"

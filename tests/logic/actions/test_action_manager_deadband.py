import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
    OGBActionManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


@pytest.mark.asyncio
async def test_vpd_in_deadband_returns_true_perfection_mode():
    """Test that VPD within deadband is detected correctly."""
    data_store = FakeDataStore(
        {
            "tentMode": "VPD Perfection",
            "vpd": {
                "current": 1.08,
                "perfection": 1.10,
            },
            "controlOptionData": {
                "deadband": {
                    "vpdDeadband": 0.05,
                }
            }
        }
    )
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    in_deadband, reason = manager._is_vpd_in_deadband()

    assert in_deadband is True
    assert "within deadband" in reason.lower()
    assert "1.08" in reason


@pytest.mark.asyncio
async def test_vpd_outside_deadband_returns_false():
    """Test that VPD outside deadband is detected correctly."""
    data_store = FakeDataStore(
        {
            "selectedMode": "VPD Perfection",
            "vpd": {
                "current": 1.00,
                "perfection": 1.10,
            },
            "controlOptionData": {
                "deadband": {
                    "vpdDeadband": 0.05,
                }
            }
        }
    )
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    in_deadband, reason = manager._is_vpd_in_deadband()

    assert in_deadband is False
    assert reason == ""


@pytest.mark.asyncio
async def test_vpd_target_deadband():
    """Test that VPD Target mode deadband works correctly."""
    data_store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "vpd": {
                "current": 1.12,
                "targeted": 1.10,
            },
            "controlOptionData": {
                "deadband": {
                    "vpdTargetDeadband": 0.05,
                }
            }
        }
    )
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    in_deadband, reason = manager._is_vpd_in_deadband()

    assert in_deadband is True
    assert "within deadband" in reason.lower()


@pytest.mark.asyncio
async def test_closed_environment_no_deadband():
    """Test that Closed Environment has no VPD deadband."""
    data_store = FakeDataStore(
        {
            "selectedMode": "Closed Environment",
        }
    )
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    in_deadband, reason = manager._is_vpd_in_deadband()

    assert in_deadband is False
    assert reason == ""


@pytest.mark.asyncio
async def test_conflicting_actions_removed_humidify_dehumidify():
    """Test that humidify and dehumidify conflicts are resolved based on humidity status."""
    # Need tentData with humidity to trigger the conflict resolution bypass
    data_store = FakeDataStore({
        "tentData": {
            "humidity": 50.0,  # In range - no humidity status override
            "minHumidity": 45.0,
            "maxHumidity": 55.0,
        }
    })
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    # Test with Increase vs Increase (both want different outcomes)
    actions = [
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canHumidify",
            action="Increase",
            priority="medium",
        ),
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canDehumidify",
            action="Increase",  # Both Increase = conflict
            priority="high",  # Higher priority wins
        ),
    ]

    filtered = manager._remove_conflicting_actions(actions)

    # With no humidity status set, priority resolution should apply
    assert len(filtered) == 1
    assert filtered[0].capability == "canDehumidify"
    assert filtered[0].priority == "high"


@pytest.mark.asyncio
async def test_humidify_dehumidify_both_pass_when_humidity_status_active():
    """Test that when humidity is too low or too high, BOTH actions pass through."""
    # Set humidity too low to trigger bypass
    data_store = FakeDataStore({
        "tentData": {
            "humidity": 40.0,  # Too low!
            "minHumidity": 45.0,
            "maxHumidity": 55.0,
        }
    })
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    # Test with Increase (humidify) vs Reduce (dehumidify) - opposite actions
    actions = [
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canHumidify",
            action="Increase",  # To raise humidity
            priority="medium",
        ),
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canDehumidify",
            action="Reduce",  # To stop running dehumidifier
            priority="high",
        ),
    ]

    # With humidity too low, both should pass through!
    filtered = manager._remove_conflicting_actions(actions)

    # Both should pass through (humidity status triggers bypass)
    assert len(filtered) == 2


@pytest.mark.asyncio
async def test_conflicting_actions_removed_heat_cool():
    """Test that heat and cool conflicts are resolved for same-direction actions."""
    data_store = FakeDataStore({})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    actions = [
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canHeat",
            action="Increase",
            priority="high",  # Higher priority wins
        ),
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canCool",
            action="Increase",
            priority="medium",
        ),
    ]

    filtered = manager._remove_conflicting_actions(actions)

    assert len(filtered) == 1
    assert filtered[0].capability == "canHeat"
    assert filtered[0].priority == "high"


@pytest.mark.asyncio
async def test_conflicting_actions_removed_exhaust_humidify():
    """Test that exhaust and humidify conflicts are resolved."""
    data_store = FakeDataStore({})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    actions = [
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canExhaust",
            action="Increase",
            priority="medium",
        ),
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canHumidify",
            action="Increase",
            priority="high",
        ),
    ]

    filtered = manager._remove_conflicting_actions(actions)

    assert len(filtered) == 1
    assert filtered[0].capability == "canHumidify"


@pytest.mark.asyncio
async def test_no_conflict_with_different_capabilities():
    """Test that non-conflicting capabilities are not removed."""
    data_store = FakeDataStore({})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    actions = [
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canExhaust",
            action="Increase",
            priority="medium",
        ),
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canHeat",
            action="Reduce",
            priority="medium",
        ),
    ]

    filtered = manager._remove_conflicting_actions(actions)

    assert len(filtered) == 2


@pytest.mark.asyncio
async def test_conflicting_actions_exhaust_intake_reduce():
    """Test that exhaust Increase + intake Reduce conflict is resolved."""
    data_store = FakeDataStore({})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    actions = [
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canExhaust",
            action="Increase",
            priority="medium",
        ),
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canIntake",
            action="Reduce",
            priority="high",
        ),
    ]

    filtered = manager._remove_conflicting_actions(actions)

    assert len(filtered) == 1
    assert filtered[0].capability == "canIntake"


@pytest.mark.asyncio
async def test_adaptive_cooldown_disabled_by_default():
    """Test that adaptive cooldown is disabled by default - user gets what they set."""
    data_store = FakeDataStore({
        "controlOptions": {
            "adaptiveCooldownEnabled": False  # Default behavior
        }
    })
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    base_cooldown = manager.cooldown_manager.cooldowns.get("canExhaust", 1.0)

    # With adaptive disabled, user gets exactly what they set
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.3)  # Very close
    assert cooldown == base_cooldown  # User says 1, gets 1

    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.7)  # Close
    assert cooldown == base_cooldown  # User says 1, gets 1

    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 4.0)  # Far
    assert cooldown == base_cooldown  # User says 1, gets 1

    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 6.0)  # Very far
    assert cooldown == base_cooldown  # User says 1, gets 1


@pytest.mark.asyncio
async def test_adaptive_cooldown_emergency_override():
    """Test that cooldown is reduced in emergency mode when adaptive is disabled."""
    data_store = FakeDataStore({
        "controlOptions": {
            "adaptiveCooldownEnabled": False,
            "emergencyCooldownFactor": 0.5
        }
    })
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    base_cooldown = manager.cooldown_manager.cooldowns.get("canExhaust", 1.0)

    # Normal mode: user gets what they set
    manager.cooldown_manager._emergency_conditions = []
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.3)
    assert cooldown == base_cooldown  # 1.0

    # Emergency mode: cooldown is reduced
    manager.cooldown_manager._emergency_conditions = ["critical_overheat"]
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.3)
    assert cooldown == base_cooldown * 0.5  # 0.5 (50% reduction)


@pytest.mark.asyncio
async def test_adaptive_cooldown_enabled_by_user():
    """Test that adaptive cooldown works when user explicitly enables it."""
    data_store = FakeDataStore({
        "controlOptions": {
            "adaptiveCooldownEnabled": True,
            "adaptiveCooldownThresholds": {
                "critical": 5.0, "high": 3.0, "near": 1.0, "veryNear": 0.5
            },
            "adaptiveCooldownFactors": {
                "critical": 1.5, "high": 1.2, "near": 2.0, "veryNear": 3.0
            }
        }
    })
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    base_cooldown = manager.cooldown_manager.cooldowns.get("canExhaust", 1.0)

    # Very close to target
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.3)
    assert cooldown == base_cooldown * 3.0  # 3.0x for deviation < 0.5

    # Close to target
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.7)
    assert cooldown == base_cooldown * 2.0  # 2.0x for deviation < 1.0

    # Far from target
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 4.0)
    assert cooldown == base_cooldown * 1.2  # 1.2x for deviation > 3

    # Very far from target
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 6.0)
    assert cooldown == base_cooldown * 1.5  # 1.5x for deviation > 5


@pytest.mark.asyncio
async def test_check_limits_and_publicate_early_exit_deadband(monkeypatch):
    """Test that checkLimitsAndPublicate exits early when VPD in deadband."""
    data_store = FakeDataStore(
        {
            "tentMode": "VPD Perfection",
            "vpd": {
                "current": 1.10,  # Within deadband of perfection
                "perfection": 1.10,
            },
            "capabilities": {"canVentilate": {"state": True}},
            "controlOptions": {"nightVPDHold": False},
            "isPlantDay": {"islightON": True},
            "tentData": {
                "temperature": 25.0,
                "humidity": 60.0,
                "minTemp": 20.0,
                "maxTemp": 28.0,
                "minHumidity": 50.0,
                "maxHumidity": 70.0,
            },
            "controlOptionData": {
                "deadband": {"vpdDeadband": 0.05},
                "weights": {"temp": 1, "hum": 1, "defaultValue": 1},
            }
        }
    )
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    called = {"quiet_zone": 0, "publication": 0}

    async def fake_quiet_zone():
        called["quiet_zone"] += 1

    async def fake_publication(_action_map):
        called["publication"] += 1

    monkeypatch.setattr(manager, "_emit_quiet_zone_idle", fake_quiet_zone)
    monkeypatch.setattr(manager, "publicationActionHandler", fake_publication)

    actions = [
        OGBActionPublication(
            Name="test_room",
            message="test",
            capability="canVentilate",
            action="Increase",
            priority="medium",
        )
    ]
    await manager.checkLimitsAndPublicate(actions)

    assert called["quiet_zone"] == 1
    assert called["publication"] == 0  # Should NOT process actions


@pytest.mark.asyncio
async def test_mode_manager_emits_smart_deadband_events():
    """Test that ModeManager emits Smart Deadband events when VPD is in deadband."""
    from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager

    data_store = FakeDataStore(
        {
            "tentMode": "VPD Perfection",
            "vpd": {
                "current": 1.10,  # Exactly at perfection - triggers smart deadband
                "perfection": 1.10,
                "perfectMin": 1.00,
                "perfectMax": 1.20,
            },
            "capabilities": {
                "canExhaust": {"state": True, "isDimmable": False},
                "canHeat": {"state": True, "isDimmable": True},
            },
            "controlOptionData": {
                "deadband": {"vpdDeadband": 0.05},
            },
        }
    )
    event_manager = FakeEventManager()

    emitted_events = []

    async def capture_event(event_name, _data, **kwargs):
        emitted_events.append(event_name)

    event_manager.on = lambda name, handler: None  # Simplified for test
    event_manager.emit = capture_event

    manager = OGBModeManager(None, data_store, event_manager, "test_room")

    await manager.handle_vpd_perfection()

    # ModeManager should emit LogForClient for Smart Deadband
    assert "LogForClient" in emitted_events
    # Should emit SmartDeadbandEntered events for climate devices
    assert "SmartDeadbandEntered" in emitted_events


@pytest.mark.asyncio
async def test_mode_manager_skips_ambient_room():
    """Test that ModeManager skips ambient room in all VPD modes."""
    from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager

    data_store = FakeDataStore(
        {
            "selectedMode": "VPD Perfection",
            "vpd": {
                "current": 1.00,
                "perfection": 1.10,
                "perfectMin": 1.00,
                "perfectMax": 1.20,
            },
            "capabilities": {
                "canExhaust": {"state": True},
            },
        }
    )
    event_manager = FakeEventManager()

    emitted_events = []

    def capture_event(event_name, _data):
        emitted_events.append(event_name)

    event_manager.on = lambda name, handler: None
    event_manager.emit = capture_event

    # Test with "ambient" room name
    manager = OGBModeManager(None, data_store, event_manager, "ambient")

    await manager.handle_vpd_perfection()

    # No events should be emitted for ambient room
    assert len(emitted_events) == 0

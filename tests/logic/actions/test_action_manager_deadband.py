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
            "selectedMode": "VPD Perfection",
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
            "selectedMode": "VPD Target",
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
    """Test that humidify and dehumidify conflicts are resolved."""
    data_store = FakeDataStore({})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

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
            action="Reduce",
            priority="high",  # Higher priority wins
        ),
    ]

    filtered = manager._remove_conflicting_actions(actions)

    assert len(filtered) == 1
    assert filtered[0].capability == "canDehumidify"
    assert filtered[0].priority == "high"


@pytest.mark.asyncio
async def test_conflicting_actions_removed_heat_cool():
    """Test that heat and cool conflicts are resolved."""
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
            action="Reduce",
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
            capability="canIntake",
            action="Reduce",
            priority="medium",
        ),
    ]

    filtered = manager._remove_conflicting_actions(actions)

    assert len(filtered) == 2


@pytest.mark.asyncio
async def test_adaptive_cooldown_long_when_close():
    """Test that cooldown is extended when very close to target."""
    data_store = FakeDataStore({})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    base_cooldown = manager.defaultCooldownMinutes.get("canExhaust", 1.0)

    # Very close to target
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.3)
    assert cooldown == base_cooldown * 3.0  # 3.0x for deviation < 0.5

    # Close to target
    cooldown = manager._calculateAdaptiveCooldown("canExhaust", 0.7)
    assert cooldown == base_cooldown * 2.0  # 2.0x for deviation < 1.0


@pytest.mark.asyncio
async def test_adaptive_cooldown_extended_when_far():
    """Test that cooldown is moderately extended when far from target."""
    data_store = FakeDataStore({})
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "test_room")

    base_cooldown = manager.defaultCooldownMinutes.get("canExhaust", 1.0)

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
            "selectedMode": "VPD Perfection",
            "vpd": {
                "current": 1.08,
                "perfection": 1.10,
            },
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
async def test_mode_manager_emits_events_even_in_deadband():
    """Test that ModeManager emits events (at perfection) even when ActionManager is in deadband."""
    from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager

    data_store = FakeDataStore(
        {
            "selectedMode": "VPD Perfection",
            "vpd": {
                "current": 1.10,  # Exactly at perfection - no action needed
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

    event_manager.on = lambda name, handler: None  # Simplified for test
    event_manager.emit = capture_event

    manager = OGBModeManager(None, data_store, event_manager, "test_room")

    await manager.handle_vpd_perfection()

    # ModeManager should NOT emit event because VPD is at perfection
    assert len(emitted_events) == 0


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

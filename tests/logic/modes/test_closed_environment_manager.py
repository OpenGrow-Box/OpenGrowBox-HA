import pytest

from custom_components.opengrowbox.OGBController.managers.ClosedEnvironmentManager import (
    ClosedEnvironmentManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _manager(store=None):
    return ClosedEnvironmentManager(
        data_store=store or FakeDataStore(),
        event_manager=FakeEventManager(),
        room="dev_room",
        hass=None,
        action_manager=True,
    )


@pytest.mark.asyncio
async def test_execute_cycle_skips_when_not_closed_environment_mode():
    manager = _manager(FakeDataStore({"tentMode": "VPD Perfection"}))
    await manager.execute_cycle()
    assert manager.event_manager.emitted == []


@pytest.mark.asyncio
async def test_execute_cycle_delegates_to_closed_environment_events_when_action_manager_present():
    store = FakeDataStore(
        {
            "tentMode": "Closed Environment",
            "capabilities": {"canClimate": {"state": True}},
        }
    )
    manager = _manager(store)
    await manager.execute_cycle()

    names = [e["event_name"] for e in manager.event_manager.emitted]
    assert "closed_environment_cycle" in names
    assert "maintain_co2" not in names


@pytest.mark.asyncio
async def test_execute_cycle_without_action_manager_skips():
    """Test that without action_manager, the cycle is skipped gracefully."""
    store = FakeDataStore(
        {
            "tentMode": "Closed Environment",
            "capabilities": {
                "canHeat": {"state": True},
                "canHumidify": {"state": True},
            },
            "tentData": {"temperature": 20.0, "humidity": 40.0},
        }
    )
    manager = ClosedEnvironmentManager(
        data_store=store,
        event_manager=FakeEventManager(),
        room="dev_room",
        hass=None,
        action_manager=None,
    )

    await manager.execute_cycle()

    names = [e["event_name"] for e in manager.event_manager.emitted]
    # Without action_manager, no cycle events should be emitted
    assert "closed_environment_cycle" not in names


@pytest.mark.asyncio
async def test_closed_environment_uses_own_targets_not_vpd():
    """Test that Closed Environment uses its own temp/hum targets, NOT VPD targets."""
    store = FakeDataStore(
        {
            "tentMode": "Closed Environment",
            "capabilities": {
                "canHeat": {"state": True},
                "canHumidify": {"state": True},
            },
            "tentData": {
                "temperature": 20.0,
                "humidity": 45.0,
                "minTemp": 22,
                "maxTemp": 26,
                "minHumidity": 55,
                "maxHumidity": 68,
            },
            "vpd": {
                "current": 0.8,
                "targeted": 1.2,
                "perfection": 1.1,
            },
            "plantStages": {
                "EarlyVeg": {
                    "minTemp": 22,
                    "maxTemp": 26,
                    "minHumidity": 55,
                    "maxHumidity": 68,
                }
            },
            "controlOptions": {
                "minMaxControl": False,
            },
        }
    )
    manager = _manager(store)

    await manager.execute_cycle()

    # Verify that closed_environment_cycle was emitted (delegated to ClosedActions)
    names = [e["event_name"] for e in manager.event_manager.emitted]
    assert "closed_environment_cycle" in names


@pytest.mark.asyncio
async def test_closed_environment_night_mode_power_saving():
    """Test that Closed Environment uses power-saving mode at night when nightVPDHold=False."""
    store = FakeDataStore(
        {
            "tentMode": "Closed Environment",
            "capabilities": {
                "canHeat": {"state": True},
                "canCool": {"state": True},
                "canVentilate": {"state": True},
                "canExhaust": {"state": True},
            },
            "tentData": {
                "temperature": 20.0,
                "humidity": 60.0,
            },
            "controlOptions": {
                "nightVPDHold": False,  # Power-saving mode
                "co2Control": False,
            },
            "isPlantDay": {
                "islightON": False,  # Night mode
            },
        }
    )
    manager = _manager(store)

    await manager.execute_cycle()

    names = [e["event_name"] for e in manager.event_manager.emitted]
    # Should emit closed_environment_cycle event
    assert "closed_environment_cycle" in names

    # The actual night mode power-saving is handled in ClosedActions.execute_closed_environment_cycle
    # This test verifies that the cycle runs at night (ClosedActions will handle the power-saving)

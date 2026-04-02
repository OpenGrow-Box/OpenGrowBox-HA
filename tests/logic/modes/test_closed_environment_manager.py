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
async def test_execute_cycle_fallback_emits_core_closed_events_without_action_manager(monkeypatch):
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

    async def fixed_temp_target():
        return 24.0

    async def fixed_hum_target():
        return 55.0

    monkeypatch.setattr(manager.control_logic, "calculate_optimal_temperature_target", fixed_temp_target)
    monkeypatch.setattr(manager.control_logic, "calculate_optimal_humidity_target", fixed_hum_target)

    await manager.execute_cycle()
    names = [e["event_name"] for e in manager.event_manager.emitted]
    assert "maintain_co2" in names
    assert "monitor_o2_safety" in names
    assert "optimize_air_recirculation" in names

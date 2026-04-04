import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
    OGBActionManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


@pytest.mark.asyncio
async def test_check_limits_and_publicate_blocks_when_night_hold_disabled(monkeypatch):
    data_store = FakeDataStore(
        {
            "controlOptions": {"nightVPDHold": False},
            "isPlantDay": {"islightON": False},
        }
    )
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "dev_room")

    called = {"fallback": 0, "publication": 0}

    async def fake_fallback(_action_map):
        called["fallback"] += 1

    async def fake_publication(_action_map):
        called["publication"] += 1

    monkeypatch.setattr(manager, "_night_hold_fallback", fake_fallback)
    monkeypatch.setattr(manager, "publicationActionHandler", fake_publication)

    actions = [
        OGBActionPublication(
            Name="dev_room",
            message="test",
            capability="canVentilate",
            action="Increase",
            priority="medium",
        )
    ]
    await manager.checkLimitsAndPublicate(actions)

    assert called["fallback"] == 1
    assert called["publication"] == 0


@pytest.mark.asyncio
async def test_publication_action_handler_emits_expected_events(monkeypatch):
    data_store = FakeDataStore(
        {
            "tentMode": "VPD Perfection",
            "mainControl": "Premium",
            "previousActions": [],
        }
    )
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "dev_room")

    async def passthrough(actions):
        return actions

    monkeypatch.setattr(manager, "_apply_environment_guard", passthrough)

    actions = [
        OGBActionPublication(
            Name="dev_room",
            message="window open",
            capability="canWindow",
            action="Increase",
            priority="high",
        ),
        OGBActionPublication(
            Name="dev_room",
            message="heater down",
            capability="canHeat",
            action="Reduce",
            priority="medium",
        ),
    ]
    await manager.publicationActionHandler(actions)

    names = [e["event_name"] for e in event_manager.emitted]
    assert "Increase Ventilation" in names
    assert "Reduce Heater" in names
    assert "DataRelease" in names

    action_data = data_store.get("actionData")
    assert action_data["commandCount"] == 2
    assert action_data["controllerType"] == "VPD-P"


@pytest.mark.asyncio
async def test_apply_environment_guard_rewrites_increase(monkeypatch):
    data_store = FakeDataStore()
    event_manager = FakeEventManager()
    manager = OGBActionManager(None, data_store, event_manager, "dev_room")

    import custom_components.opengrowbox.OGBController.actions.OGBEnvironmentGuard as guard

    def fake_guard(*_args, **_kwargs):
        return True, {"reason": "temp_risk_cold_source", "source": "test", "selectedSource": "ambient"}

    monkeypatch.setattr(guard, "evaluate_environment_guard", fake_guard)

    action = OGBActionPublication(
        Name="dev_room",
        message="increase airflow",
        capability="canVentilate",
        action="Increase",
        priority="medium",
    )
    result = await manager._apply_environment_guard([action])

    assert result[0].action == "Reduce"
    assert "EnvironmentGuard" in result[0].message
    assert any(e["event_name"] == "LogForClient" for e in event_manager.emitted)

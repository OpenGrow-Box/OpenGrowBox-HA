import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
    OGBActionManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _mk_action(capability: str, action: str = "Increase"):
    return OGBActionPublication(
        Name="dev_room",
        message="test",
        capability=capability,
        action=action,
        priority="medium",
    )


def test_map_tentmode_to_controller_type_covers_supported_modes():
    manager = OGBActionManager(None, FakeDataStore(), FakeEventManager(), "dev_room")

    assert manager._map_tentmode_to_controller_type("VPD Perfection") == "VPD-P"
    assert manager._map_tentmode_to_controller_type("VPD Target") == "VPD-T"
    assert manager._map_tentmode_to_controller_type("Closed Environment") == "CLOSED"
    assert manager._map_tentmode_to_controller_type("AI Control") == "AI"
    assert manager._map_tentmode_to_controller_type("PID Control") == "PID"
    assert manager._map_tentmode_to_controller_type("MPC Control") == "MPC"
    assert manager._map_tentmode_to_controller_type("Disabled") == "OFF"


@pytest.mark.asyncio
async def test_check_limits_no_vpd_uses_conflict_resolver_when_available():
    store = FakeDataStore({
        "tentData": {
            "temperature": 25.0,
            "humidity": 60.0,
            "minTemp": 20.0,
            "maxTemp": 30.0,
            "minHumidity": 40.0,
            "maxHumidity": 80.0,
        },
        "controlOptionData": {"weights": {"defaultValue": 1.0}}
    })
    events = FakeEventManager()
    manager = OGBActionManager(None, store, events, "dev_room")

    called = {"resolve": 0, "published": 0}

    class FakeDampening:
        @staticmethod
        def _resolve_action_conflicts(action_map):
            called["resolve"] += 1
            return action_map[:1]

    async def fake_publication(action_map):
        called["published"] += len(action_map)

    manager.dampening_actions = FakeDampening()
    manager.publicationActionHandler = fake_publication

    await manager.checkLimitsAndPublicateNoVPD([
        _mk_action("canExhaust"),
        _mk_action("canVentilate"),
    ])

    assert called["resolve"] == 1
    assert called["published"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability,action,expected_event",
    [
        ("canExhaust", "Increase", "Increase Exhaust"),
        ("canIntake", "Reduce", "Reduce Intake"),
        ("canVentilate", "Increase", "Increase Ventilation"),
        ("canWindow", "Increase", "Increase Ventilation"),
        ("canHumidify", "Reduce", "Reduce Humidifier"),
        ("canDehumidify", "Increase", "Increase Dehumidifier"),
        ("canHeat", "Reduce", "Reduce Heater"),
        ("canCool", "Increase", "Increase Cooler"),
        ("canClimate", "Eval", "Eval Climate"),
        ("canCO2", "Increase", "Increase CO2"),
        ("canLight", "Reduce", "Reduce Light"),
    ],
)
async def test_publication_dispatch_for_core_capabilities(capability, action, expected_event, monkeypatch):
    store = FakeDataStore({"tentMode": "VPD Perfection", "mainControl": "HomeAssistant"})
    events = FakeEventManager()
    manager = OGBActionManager(None, store, events, "dev_room")

    async def passthrough(action_map):
        return action_map

    monkeypatch.setattr(manager, "_apply_environment_guard", passthrough)

    await manager.publicationActionHandler([_mk_action(capability, action)])

    event_names = [item["event_name"] for item in events.emitted]
    assert expected_event in event_names


@pytest.mark.asyncio
async def test_publication_skips_all_actions_when_mode_disabled(monkeypatch):
    store = FakeDataStore({"tentMode": "Disabled", "mainControl": "Premium"})
    events = FakeEventManager()
    manager = OGBActionManager(None, store, events, "dev_room")

    async def passthrough(action_map):
        return action_map

    monkeypatch.setattr(manager, "_apply_environment_guard", passthrough)

    await manager.publicationActionHandler([
        _mk_action("canHeat", "Increase"),
        _mk_action("canCO2", "Reduce"),
    ])

    # No device events and no DataRelease when mode is disabled
    assert events.emitted == []

import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBModeRunPublication,
)
from custom_components.opengrowbox.OGBController.managers.OGBModeManager import (
    OGBModeManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _make_mode_manager(store, events):
    manager = OGBModeManager.__new__(OGBModeManager)
    manager.name = "OGB Mode Manager"
    manager.room = "dev_room"
    manager.data_store = store
    manager.event_manager = events
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,handler_name",
    [
        ("VPD Perfection", "handle_vpd_perfection"),
        ("VPD Target", "handle_targeted_vpd"),
        ("Drying", "handle_drying"),
        ("MPC Control", "handle_premium_mode_cycle"),
        ("PID Control", "handle_premium_mode_cycle"),
        ("AI Control", "handle_premium_mode_cycle"),
        ("Closed Environment", "handle_closed_environment"),
        ("Script Mode", "handle_script_mode"),
        ("Disabled", "handle_disabled_mode"),
    ],
)
async def test_select_action_mode_routes_to_expected_handler(mode, handler_name, monkeypatch):
    store = FakeDataStore({"mainControl": "HomeAssistant"})
    events = FakeEventManager()
    manager = _make_mode_manager(store, events)

    called = {"name": None, "arg": None}

    async def make_handler(name):
        async def _handler(*args):
            called["name"] = name
            called["arg"] = args[0] if args else None

        return _handler

    for candidate in [
        "handle_vpd_perfection",
        "handle_targeted_vpd",
        "handle_drying",
        "handle_premium_mode_cycle",
        "handle_closed_environment",
        "handle_script_mode",
        "handle_disabled_mode",
    ]:
        monkeypatch.setattr(manager, candidate, await make_handler(candidate))

    await manager.selectActionMode(OGBModeRunPublication(currentMode=mode))

    assert called["name"] == handler_name
    if handler_name == "handle_premium_mode_cycle":
        assert called["arg"] == mode


@pytest.mark.asyncio
async def test_select_action_mode_ignores_when_control_not_allowed(monkeypatch):
    store = FakeDataStore({"mainControl": "Manual"})
    events = FakeEventManager()
    manager = _make_mode_manager(store, events)

    called = {"count": 0}

    async def handler():
        called["count"] += 1

    monkeypatch.setattr(manager, "handle_vpd_perfection", handler)
    await manager.selectActionMode(OGBModeRunPublication(currentMode="VPD Perfection"))
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_handle_vpd_perfection_emits_correct_vpd_action_and_co2_maintenance():
    store = FakeDataStore(
        {
            "vpd": {
                "current": 0.8,
                "perfection": 1.2,
                "perfectMin": 1.0,
                "perfectMax": 1.4,
            },
            "capabilities": {"canExhaust": {"state": True}, "canCO2": {"state": True}},
            "controlOptions": {"co2Control": True},
        }
    )
    events = FakeEventManager()
    manager = _make_mode_manager(store, events)

    await manager.handle_vpd_perfection()

    names = [e["event_name"] for e in events.emitted]
    assert "increase_vpd" in names
    assert "maintain_co2" in names


@pytest.mark.asyncio
async def test_handle_targeted_vpd_uses_tolerance_when_min_max_missing():
    store = FakeDataStore(
        {
            "vpd": {
                "current": 0.9,
                "targeted": 1.2,
                "targetedMin": None,
                "targetedMax": None,
                "tolerance": 10,
            },
            "capabilities": {"canExhaust": {"state": True}},
        }
    )
    events = FakeEventManager()
    manager = _make_mode_manager(store, events)

    await manager.handle_targeted_vpd()

    # tolerance=10% around 1.2 => min=1.08, max=1.32; current=0.9 => increase
    assert store.getDeep("vpd.targetedMin") == 1.08
    assert store.getDeep("vpd.targetedMax") == 1.32
    assert any(e["event_name"] == "vpdt_increase_vpd" for e in events.emitted)


@pytest.mark.asyncio
async def test_handle_vpd_perfection_reduce_and_finetune_paths():
    # Reduce path
    store_reduce = FakeDataStore(
        {
            "vpd": {
                "current": 1.6,
                "perfection": 1.2,
                "perfectMin": 1.0,
                "perfectMax": 1.4,
            },
            "capabilities": {"canExhaust": {"state": True}},
            "controlOptions": {"co2Control": False},
        }
    )
    events_reduce = FakeEventManager()
    manager_reduce = _make_mode_manager(store_reduce, events_reduce)
    await manager_reduce.handle_vpd_perfection()
    assert any(e["event_name"] == "reduce_vpd" for e in events_reduce.emitted)

    # Fine-tune path
    store_tune = FakeDataStore(
        {
            "vpd": {
                "current": 1.21,
                "perfection": 1.2,
                "perfectMin": 1.0,
                "perfectMax": 1.4,
            },
            "capabilities": {"canExhaust": {"state": True}},
            "controlOptions": {"co2Control": False},
        }
    )
    events_tune = FakeEventManager()
    manager_tune = _make_mode_manager(store_tune, events_tune)
    await manager_tune.handle_vpd_perfection()
    assert any(e["event_name"] == "FineTune_vpd" for e in events_tune.emitted)


@pytest.mark.asyncio
async def test_handle_premium_mode_cycle_feature_gate_and_datarelease(monkeypatch):
    store = FakeDataStore(
        {
            "mainControl": "Premium",
            "subscriptionData": {"features": {"pidControllers": False, "mpcControllers": True, "aiControllers": True}},
        }
    )
    events = FakeEventManager()
    manager = _make_mode_manager(store, events)
    manager._ai_bridge_started = False

    started = {"ai": 0}

    async def fake_start_ai_bridge():
        started["ai"] += 1

    monkeypatch.setattr(manager, "start_ai_data_bridge", fake_start_ai_bridge)

    await manager.handle_premium_mode_cycle("PID Control")
    assert any(e["event_name"] == "LogForClient" for e in events.emitted)
    assert not any(e["event_name"] == "DataRelease" for e in events.emitted)

    events.emitted.clear()
    await manager.handle_premium_mode_cycle("AI Control")
    assert any(e["event_name"] == "DataRelease" for e in events.emitted)
    assert started["ai"] == 1


@pytest.mark.asyncio
async def test_handle_premium_modes_dispatches_pid_mpc_ai(monkeypatch):
    store = FakeDataStore(
        {
            "subscriptionData": {"features": {"pidControllers": True, "mpcControllers": True, "aiControllers": True}},
        }
    )
    events = FakeEventManager()
    manager = _make_mode_manager(store, events)
    manager._ai_bridge_started = False

    started = {"ai": 0}

    async def fake_start_ai_bridge():
        started["ai"] += 1

    monkeypatch.setattr(manager, "start_ai_data_bridge", fake_start_ai_bridge)

    await manager.handle_premium_modes({"controllerType": "PID", "actionData": {"x": 1}})
    await manager.handle_premium_modes({"controllerType": "MPC", "actionData": {"x": 1}})
    await manager.handle_premium_modes({"controllerType": "AI", "actionData": {"x": 1}})

    names = [e["event_name"] for e in events.emitted]
    assert "PIDActions" in names
    assert "MPCActions" in names
    assert "AIActions" in names
    assert started["ai"] == 1

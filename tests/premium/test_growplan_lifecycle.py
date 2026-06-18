"""Tests for the OGBGrowPlanManager pause/stop/resume lifecycle."""

import asyncio
import pytest

from custom_components.opengrowbox.OGBController.premium.growplans.OGBGrowPlanManager import (
    OGBGrowPlanManager,
)
from tests.logic.helpers import FakeDataStore, FakeEventManager


class FakeHassBus:
    def __init__(self):
        self.listeners = {}
        self.fired = []

    def async_listen(self, event_type, callback):
        self.listeners.setdefault(event_type, []).append(callback)

    def async_fire(self, event_type, data):
        self.fired.append((event_type, data))


class FakeHass:
    def __init__(self):
        self.bus = FakeHassBus()
        self.data = {}


def _build_manager(room: str = "test_room"):
    data_store = FakeDataStore(
        {
            "tentMode": "VPD Perfection",
            "plantStage": "EarlyVeg",
            "growManagerActive": False,
            "growPlan": {
                "currentWeekData": None,
                "currentWeek": None,
            },
            "growPlans": [
                {
                    "id": "plan-1",
                    "name": "Test Plan",
                    "startDate": "2026-01-01T00:00:00Z",
                    "weeks": [
                        {
                            "week": 1,
                            "stage": "Veg",
                            "tentMode": "VPD Target",
                            "environment": {
                                "temperature": {"day": {"max": 26, "min": 22}, "night": {"min": 20}},
                                "humidity": {"day": {"max": 70}},
                                "vpd": {"optimal": 1.0},
                                "co2": {"optimal": 1000, "max": 1500, "min": 600, "enabled": True},
                                "lightCycle": {"startTime": 6, "on": 18, "sunrise": "00:30:00", "sunset": "00:30:00"},
                                "lightIntensity": {"min": 20, "max": 50},
                            },
                            "tentControls": {
                                "nightVpdHold": True,
                                "deviceDampening": False,
                                "vpdDetermination": "advanced",
                            },
                        }
                    ],
                }
            ],
        }
    )
    event_manager = FakeEventManager()
    hass = FakeHass()
    manager = OGBGrowPlanManager(hass, data_store, event_manager, room)
    return manager, data_store, event_manager, hass


def _make_week_data(week: int = 4, stage: str = "MidVeg", tent_mode: str = "VPD Target"):
    return {
        "week": week,
        "stage": stage,
        "tentMode": tent_mode,
        "environment": {
            "temperature": {"day": {"max": 26, "min": 18}, "night": {"max": 20, "min": 20}},
            "humidity": {"day": {"max": 60, "min": 50, "optimal": 57}, "night": {"max": 65, "min": 65, "optimal": 65}},
            "vpd": {"max": 0.8, "min": 0.4, "optimal": 0.6},
            "co2": {"max": 1500, "min": 300, "enabled": "NO", "optimal": 800},
            "lightCycle": {"on": 18, "off": 6, "sunset": "01:00:00", "sunrise": "00:30:00", "startTime": 11},
            "temperature": {"day": {"max": 26, "min": 18, "optimal": 22}, "night": {"max": 20, "min": 20, "optimal": 20}},
            "lightIntensity": {"max": 50, "min": 20},
        },
        "tentControls": {
            "drying": {"mode": "5DayDry", "enabled": "NO"},
            "tankFeed": {"enabled": "NO"},
            "nightVpdHold": {"enabled": "NO"},
            "deviceDampening": {"enabled": "YES"},
            "vpdDetermination": {"mode": "LIVE", "enabled": "YES"},
        },
    }


@pytest.mark.asyncio
async def test_activate_then_deactivate_clears_state():
    manager, data_store, event_manager, _ = _build_manager()
    # Allow the init() task to register listeners
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    ok = await manager.activate_grow_plan_by_id("plan-1", plan)
    assert ok is True
    assert manager.managerActive is True
    assert data_store.get("growManagerActive") is True
    assert manager.active_grow_plan_id == "plan-1"

    ok = await manager.deactivate_grow_plan(clear_plan_data=True)
    assert ok is True
    assert manager.managerActive is False
    assert data_store.get("growManagerActive") is False
    assert manager.active_grow_plan is None
    assert manager.active_grow_plan_id is None
    assert data_store.getDeep("growPlan.currentWeekData") is None
    assert data_store.getDeep("growPlan.currentWeek") is None


@pytest.mark.asyncio
async def test_deactivate_when_inactive_is_idempotent():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    # No plan active — should still return True and not crash
    ok = await manager.deactivate_grow_plan(clear_plan_data=True)
    assert ok is True


@pytest.mark.asyncio
async def test_pause_keeps_plan_data_but_disables_flag():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)
    assert manager.managerActive is True

    # Simulate a pause event from the webapp
    await manager._on_grow_plan_status_change(
        {"room": "test_room", "action": "paused", "plan_id": "plan-1", "status": "paused"}
    )

    # Flag flipped off, plan data preserved for potential resume
    assert manager.managerActive is False
    assert data_store.get("growManagerActive") is False
    assert manager.active_grow_plan_id == "plan-1"
    assert manager.active_grow_plan is not None


@pytest.mark.asyncio
async def test_status_change_stop_clears_plan_data():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)

    await manager._on_grow_plan_status_change(
        {"room": "test_room", "action": "stopped", "plan_id": "plan-1", "status": "stopped"}
    )

    assert manager.managerActive is False
    assert manager.active_grow_plan is None
    assert manager.active_grow_plan_id is None
    assert data_store.getDeep("growPlan.currentWeekData") is None


@pytest.mark.asyncio
async def test_status_change_resume_reactivates():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)
    # Pause first
    await manager._on_grow_plan_status_change(
        {"room": "test_room", "action": "paused", "plan_id": "plan-1"}
    )
    assert manager.managerActive is False
    assert manager.active_grow_plan_id == "plan-1"

    # Now resume — should re-enable without re-running full activation
    await manager._on_grow_plan_status_change(
        {"room": "test_room", "action": "resumed", "plan_id": "plan-1", "status": "active"}
    )
    assert manager.managerActive is True
    assert data_store.get("growManagerActive") is True


@pytest.mark.asyncio
async def test_status_change_wrong_room_ignored():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)

    await manager._on_grow_plan_status_change(
        {"room": "OTHER_ROOM", "action": "stopped", "plan_id": "plan-1"}
    )

    # Plan must remain active because event was for a different room
    assert manager.managerActive is True
    assert manager.active_grow_plan_id == "plan-1"


@pytest.mark.asyncio
async def test_growplan_command_stop():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)

    await manager._on_growplan_command(
        {"room": "test_room", "action": "stop"}
    )

    assert manager.managerActive is False
    assert manager.active_grow_plan is None


@pytest.mark.asyncio
async def test_growplan_command_pause_then_resume():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)

    await manager._on_growplan_command({"room": "test_room", "action": "pause"})
    assert manager.managerActive is False
    assert manager.active_grow_plan_id == "plan-1"

    await manager._on_growplan_command(
        {"room": "test_room", "action": "resume", "plan_id": "plan-1"}
    )
    assert manager.managerActive is True


@pytest.mark.asyncio
async def test_status_change_resume_after_restart_uses_stored_active_plan():
    """Simulate HA restart while plan is paused, then resume from webapp.

    After a restart the manager has no active_grow_plan in memory, but the
    activePlan snapshot was persisted by _on_new_grow_plans while the plan was
    paused. The resume_plan event from the webapp only carries plan_id/name, so
    the manager must fall back to the stored snapshot to keep the original
    startDate.
    """
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    # Plan was paused before restart; manager lost in-memory plan data
    manager.active_grow_plan_id = None
    manager.active_grow_plan = None
    manager.plan_start_date = None
    manager.managerActive = False
    data_store.set("growManagerActive", False)

    # Snapshot persisted by API while paused
    stored_plan = {
        "id": "plan-1",
        "name": "Test Plan",
        "startDate": "2026-01-01T00:00:00+00:00",
        "weeks": data_store.get("growPlans")[0]["weeks"],
    }
    data_store.setDeep("growPlans", [stored_plan])
    data_store.setDeep("growPlan.activePlan", stored_plan)

    # Webapp resume event only carries id/name, no startDate
    await manager._on_grow_plan_status_change(
        {
            "room": "test_room",
            "action": "resume_plan",
            "plan_id": "plan-1",
            "plan_name": "Test Plan",
        }
    )

    assert manager.managerActive is True
    assert data_store.get("growManagerActive") is True
    assert manager.active_grow_plan_id == "plan-1"
    assert manager.plan_start_date is not None
    assert manager.plan_start_date.year == 2026


@pytest.mark.asyncio
async def test_activate_idempotent_when_same_plan():
    manager, data_store, _, _ = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)
    first_plan_ref = manager.active_grow_plan

    # Activating the same plan a second time should not blow away state
    ok = await manager.activate_grow_plan_by_id("plan-1", plan)
    assert ok is True
    assert manager.active_grow_plan is first_plan_ref
    assert manager.managerActive is True


@pytest.mark.asyncio
async def test_update_entities_from_week_data_skipped_when_paused():
    manager, data_store, event_manager, hass = _build_manager()
    await asyncio.sleep(0)

    # Simulate a paused plan with stale week data still in the data store
    # (e.g. after a HA restart while the plan was paused).
    week_data = _make_week_data()
    data_store.setDeep("growPlan.currentWeekData", week_data)
    data_store.setDeep("growPlan.currentWeek", 4)
    manager.current_week_data = week_data
    manager.current_week = 4
    manager.active_grow_plan_id = "plan-1"
    manager.active_grow_plan = data_store.get("growPlans")[0]
    manager.managerActive = False
    data_store.set("growManagerActive", False)
    manager._is_system_ready = True

    # Pre-plan snapshot exists so pause could restore it; put a known value in
    # the data store tentMode so we can verify it is NOT overwritten.
    data_store.set("tentMode", "VPD Perfection")

    await manager._update_entities_from_week_data()

    # Entities must not be updated while paused. Since FakeHass has no real
    # services, any attempted service call would be logged as a failure, but the
    # important thing is that the method returns early and leaves tentMode alone.
    assert data_store.get("tentMode") == "VPD Perfection"


@pytest.mark.asyncio
async def test_system_ready_clears_pending_updates_when_paused():
    manager, data_store, event_manager, hass = _build_manager()
    await asyncio.sleep(0)

    # Queue an entity update as if it happened before the system was ready
    manager._pending_updates.append("_update_entities_from_week_data")

    # Plan is paused (e.g. user paused before restart)
    manager.managerActive = False
    data_store.set("growManagerActive", False)
    manager.active_grow_plan_id = "plan-1"

    await manager._on_system_ready({"room": "test_room"})

    assert manager._is_system_ready is True
    assert manager._pending_updates == []


@pytest.mark.asyncio
async def test_system_ready_processes_pending_updates_when_active():
    manager, data_store, event_manager, hass = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)

    # Queue an update, mark system not ready, then fire SystemReady
    manager._pending_updates.append("_update_entities_from_week_data")
    manager._is_system_ready = False

    await manager._on_system_ready({"room": "test_room"})

    assert manager._is_system_ready is True
    assert "_update_entities_from_week_data" not in manager._pending_updates


@pytest.mark.asyncio
async def test_daily_update_loop_skips_ambient():
    manager, data_store, event_manager, hass = _build_manager(room="ambient")
    await asyncio.sleep(0)

    # Replace sleep so the loop would exit quickly if it did not return early
    sleeps = []
    manager.ws_client = object()

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError()

    original_sleep = asyncio.sleep
    asyncio.sleep = fake_sleep
    try:
        await manager._daily_update_loop()
    except asyncio.CancelledError:
        pass
    finally:
        asyncio.sleep = original_sleep

    # ambient must return immediately without sleeping
    assert sleeps == []


@pytest.mark.asyncio
async def test_daily_update_loop_refreshes_active_plan():
    manager, data_store, event_manager, hass = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)
    manager._is_system_ready = True

    requested = []
    updated_week = []
    evaluated = []
    updated_entities = []

    class FakeWsClient:
        async def request_grow_plans_week(self):
            requested.append(True)

    manager.ws_client = FakeWsClient()
    manager._update_current_week = lambda: updated_week.append(True)
    manager._eval_plan_settings = lambda plan: evaluated.append(plan)
    manager._update_entities_from_week_data = lambda: updated_entities.append(True)

    async def fake_sleep(seconds):
        # Cancel after the first scheduled wait to end the loop
        raise asyncio.CancelledError()

    original_sleep = asyncio.sleep
    asyncio.sleep = fake_sleep
    try:
        await manager._daily_update_loop()
    except asyncio.CancelledError:
        pass
    finally:
        asyncio.sleep = original_sleep

    assert requested == [True]
    assert updated_week == [True]
    assert evaluated == [manager.active_grow_plan]
    assert updated_entities == [True]


@pytest.mark.asyncio
async def test_daily_update_loop_skips_when_paused():
    manager, data_store, event_manager, hass = _build_manager()
    await asyncio.sleep(0)

    plan = data_store.get("growPlans")[0]
    await manager.activate_grow_plan_by_id("plan-1", plan)
    manager.managerActive = False
    data_store.set("growManagerActive", False)
    manager._is_system_ready = True

    requested = []

    class FakeWsClient:
        async def request_grow_plans_week(self):
            requested.append(True)

    manager.ws_client = FakeWsClient()

    async def fake_sleep(seconds):
        raise asyncio.CancelledError()

    original_sleep = asyncio.sleep
    asyncio.sleep = fake_sleep
    try:
        await manager._daily_update_loop()
    except asyncio.CancelledError:
        pass
    finally:
        asyncio.sleep = original_sleep

    assert requested == []


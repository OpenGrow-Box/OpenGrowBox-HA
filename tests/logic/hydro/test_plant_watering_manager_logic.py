from datetime import datetime, timedelta
from types import SimpleNamespace

from custom_components.opengrowbox.OGBController.managers.hydro.plant_watering.OGBPlantWateringManager import (
    OGBPlantWateringManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _manager(mediums=None, initial=None):
    medium_manager = SimpleNamespace(get_mediums=lambda: mediums or [])
    return OGBPlantWateringManager(
        hass=None,
        data_store=FakeDataStore(initial or {}),
        event_manager=FakeEventManager(),
        room="dev_room",
        medium_manager=medium_manager,
        cast_manager=None,
    )


def _medium(name, moisture, moisture_min=None, moisture_max=None):
    thresholds = SimpleNamespace(moisture_min=moisture_min, moisture_max=moisture_max)
    return SimpleNamespace(name=name, current_moisture=moisture, thresholds=thresholds)


def test_get_active_pumps_filters_for_water_and_cast():
    manager = _manager()
    pumps = {"devEntities": ["switch.feedpump_w", "switch.cast_main", "switch.light"]}
    active = manager._get_active_pumps(pumps)
    assert "switch.feedpump_w" in active
    assert "switch.cast_main" in active
    assert "switch.light" not in active


def test_should_water_when_below_threshold_and_no_cooldown():
    mediums = [_medium("m1", moisture=20, moisture_min=30, moisture_max=60)]
    manager = _manager(mediums)
    snapshot = manager._get_moisture_snapshot()
    should, reason = manager._should_water(snapshot, cooldown_minutes=30)
    assert should is True
    assert "Bewaesserung" in reason


def test_should_not_water_when_max_reached_and_lock_active():
    mediums = [_medium("m1", moisture=65, moisture_min=30, moisture_max=60)]
    manager = _manager(mediums)
    snapshot = manager._get_moisture_snapshot()
    should, reason = manager._should_water(snapshot, cooldown_minutes=30)
    assert should is False
    assert "Maximum" in reason


def test_should_not_water_when_cooldown_active():
    last = (datetime.now() - timedelta(minutes=5)).isoformat()
    mediums = [_medium("m1", moisture=20, moisture_min=30, moisture_max=60)]
    manager = _manager(mediums, initial={"Hydro": {"PlantWatering": {"lastWatering": last}}})
    snapshot = manager._get_moisture_snapshot()
    should, reason = manager._should_water(snapshot, cooldown_minutes=30)
    assert should is False
    assert "Cooldown" in reason


def test_resolve_cooldown_uses_param_then_store_then_default():
    manager = _manager(initial={"Hydro": {"Intervall": 45}})
    assert manager._resolve_cooldown_minutes(20) == 20.0
    assert manager._resolve_cooldown_minutes(None) == 45.0

    manager2 = _manager(initial={"Hydro": {"Intervall": "invalid"}})
    assert manager2._resolve_cooldown_minutes(None) == 30.0


def test_cooldown_elapsed_handles_invalid_and_old_timestamps():
    manager = _manager()
    assert manager._cooldown_elapsed("invalid", 30) is True
    old = (datetime.now() - timedelta(minutes=31)).isoformat()
    assert manager._cooldown_elapsed(old, 30) is True

import asyncio

import pytest

from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBPumpControlManager import (
    OGBPumpControlManager,
    PumpType,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


class _FakeCalibration:
    def __init__(self, factors=None):
        self.factors = factors or {}

    def get_pump_calibration_factor(self, pump_entity: str) -> float:
        return self.factors.get(pump_entity, 1.0)


class _FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data):
        self.calls.append((domain, service, data))


class _FakeHass:
    def __init__(self):
        self.services = _FakeServices()


def _manager(calibration_factors=None):
    return OGBPumpControlManager(
        room="dev_room",
        data_store=FakeDataStore(),
        event_manager=FakeEventManager(),
        hass=_FakeHass(),
        calibration_manager=_FakeCalibration(calibration_factors),
    )


def test_calculate_dose_time_uses_calibration_and_limits():
    manager = _manager({PumpType.WATER.value: 2.0})

    # 10 ml at 2 ml/s -> 5s
    assert manager.calculate_dose_time(10.0, PumpType.WATER) == 5.0

    # Negative/too small should clamp to min 0.5s
    assert manager.calculate_dose_time(0.01, PumpType.WATER) == 0.5

    # Very large should clamp to max runtime
    assert manager.calculate_dose_time(999999, PumpType.WATER) == manager.max_pump_runtime


@pytest.mark.asyncio
async def test_activate_pump_rejects_invalid_runtime_and_concurrency(monkeypatch):
    manager = _manager()

    assert await manager.activate_pump(PumpType.WATER, 0, 1.0) is False
    assert await manager.activate_pump(PumpType.WATER, manager.max_pump_runtime + 1, 1.0) is False

    # Fill concurrency slots
    manager.active_pumps = {PumpType.WATER.value, PumpType.NUTRIENT_A.value}
    assert await manager.activate_pump(PumpType.PH_UP, 1.0, 1.0) is False


@pytest.mark.asyncio
async def test_dose_nutrients_maps_known_types_and_fails_unknown(monkeypatch):
    manager = _manager({
        PumpType.NUTRIENT_A.value: 1.0,
        PumpType.NUTRIENT_B.value: 1.0,
        PumpType.NUTRIENT_C.value: 1.0,
    })

    activated = []

    async def fake_activate(pump_type, run_time, dose_ml, is_emergency=False):
        activated.append((pump_type, run_time, dose_ml, is_emergency))
        return True

    monkeypatch.setattr(manager, "activate_pump", fake_activate)

    # avoid real waiting
    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    ok = await manager.dose_nutrients({"A": 2.0, "B": 1.0, "X": 3.0})
    assert ok is False  # unknown X should mark failure
    assert any(item[0] == PumpType.NUTRIENT_A for item in activated)
    assert any(item[0] == PumpType.NUTRIENT_B for item in activated)


@pytest.mark.asyncio
async def test_dilute_ec_adds_water_when_current_above_target(monkeypatch):
    manager = _manager({PumpType.WATER.value: 2.0})

    called = {"args": None}

    async def fake_activate(pump_type, run_time, dose_ml, is_emergency=False):
        called["args"] = (pump_type, run_time, dose_ml, is_emergency)
        return True

    monkeypatch.setattr(manager, "activate_pump", fake_activate)

    ok = await manager.dilute_ec(target_ec=1.5, current_ec=2.0, reservoir_volume=50.0)
    assert ok is True
    assert called["args"] is not None
    assert called["args"][0] == PumpType.WATER
    assert called["args"][2] > 0


@pytest.mark.asyncio
async def test_emergency_stop_all_pumps_clears_and_logs(monkeypatch):
    manager = _manager()
    manager.active_pumps = {PumpType.WATER.value, PumpType.NUTRIENT_A.value}

    stopped = []

    async def fake_off(pump_entity):
        stopped.append(pump_entity)

    monkeypatch.setattr(manager, "_turn_off_pump", fake_off)

    await manager.emergency_stop_all_pumps()
    assert manager.active_pumps == set()
    assert set(stopped) == {PumpType.WATER.value, PumpType.NUTRIENT_A.value}
    assert any(e["event_name"] == "LogForClient" for e in manager.event_manager.emitted)

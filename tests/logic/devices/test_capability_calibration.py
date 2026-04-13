"""Tests for capability calibration manager (OGBCalibManager)."""

import pytest
import asyncio
from datetime import datetime

from custom_components.opengrowbox.OGBController.managers.OGBCalibManager import (
    OGBCalibManager,
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
        self.states = {}


class FakeDevice:
    def __init__(self, name):
        self.deviceName = name
        self._on = False
        self.turn_on_calls = 0
        self.turn_off_calls = 0

    async def turn_on(self):
        self._on = True
        self.turn_on_calls += 1

    async def turn_off(self):
        self._on = False
        self.turn_off_calls += 1


@pytest.fixture
def setup_manager():
    data_store = FakeDataStore(
        {
            "tentMode": "VPD Perfection",
            "capabilities": {
                "canHeat": {
                    "state": True,
                    "count": 1,
                    "devEntities": ["climate.heater"],
                },
                "canCool": {
                    "state": True,
                    "count": 1,
                    "devEntities": ["climate.cooler"],
                },
                "canEmpty": {"state": True, "count": 0, "devEntities": []},
                "canClimate": {
                    "state": True,
                    "count": 1,
                    "devEntities": ["climate.main"],
                },
            },
            "devices": [],
            "capCalibration": {"active": None, "results": {}},
            "tentData": {
                "temperature": 22.0,
                "humidity": 60.0,
                "co2Level": 400,
            },
        }
    )
    event_manager = FakeEventManager()
    hass = FakeHass()
    manager = OGBCalibManager(hass, data_store, event_manager, "test_room")
    return manager, data_store, event_manager, hass


@pytest.mark.asyncio
async def test_start_calibration_unknown_cap(setup_manager):
    manager, _, _, hass = setup_manager
    await manager.start_calibration("canUnknown")
    assert manager._calibration_task is None
    assert any("Unknown capability" in str(f[1]["message"]) for f in hass.bus.fired)


@pytest.mark.asyncio
async def test_start_calibration_no_devices(setup_manager):
    manager, _, _, hass = setup_manager
    await manager.start_calibration("canEmpty")
    assert manager._calibration_task is None
    assert any(
        "No devices found" in str(f[1]["message"]) for f in hass.bus.fired
    )


@pytest.mark.asyncio
async def test_start_calibration_starts_task(setup_manager):
    manager, data_store, _, _ = setup_manager
    heater = FakeDevice("climate.heater")
    data_store.data["devices"] = [heater]

    # Patch internal run so it finishes immediately
    ran = []

    async def _fake_run(cap, devices):
        ran.append((cap, devices))

    manager._run_calibration = _fake_run
    await manager.start_calibration("canHeat")
    assert manager._calibration_task is not None
    await manager._calibration_task
    assert ran[0][0] == "canHeat"
    assert ran[0][1][0].deviceName == "climate.heater"


@pytest.mark.asyncio
async def test_stop_calibration_restores_mode(setup_manager):
    manager, data_store, event_manager, _ = setup_manager
    manager._original_tent_mode = "VPD Target"
    data_store.setDeep("capCalibration.active", {"cap": "canHeat"})

    # Simulate running task
    async def _infinite():
        await asyncio.sleep(10)

    manager._calibration_task = asyncio.create_task(_infinite())
    await manager.stop_calibration()

    assert data_store.get("tentMode") == "VPD Target"
    assert data_store.getDeep("capCalibration.active") is None
    assert any(e["event_name"] == "SaveState" for e in event_manager.emitted)


@pytest.mark.asyncio
async def test_run_calibration_full_flow(setup_manager):
    manager, data_store, event_manager, hass = setup_manager
    heater = FakeDevice("climate.heater")
    cooler = FakeDevice("climate.cooler")
    data_store.data["devices"] = [heater, cooler]

    async def _fake_measure(metrics, duration, cap):
        # Return deterministic fake readings
        return {
            "temperature": [22.0, 22.0] if duration == manager.BASELINE_DURATION else [25.0, 25.0],
            "humidity": [60.0, 60.0] if duration == manager.BASELINE_DURATION else [55.0, 55.0],
        }

    manager._measure_phase = _fake_measure
    manager.EFFECT_DURATIONS = {"canHeat": 180}  # 3 min for calculation

    await manager._run_calibration("canHeat", [heater])

    # Check events were emitted correctly
    emitted_names = [e["event_name"] for e in event_manager.emitted]
    assert "CalibOff" in emitted_names
    assert "CalibStart" in emitted_names
    assert "SaveState" in emitted_names

    # Mode restored
    assert data_store.get("tentMode") == "VPD Perfection"
    assert manager._original_tent_mode is None

    # Results stored
    results = data_store.getDeep("capCalibration.results.canHeat")
    assert results is not None
    assert results.get("isDimmable") is False
    assert results["temperature"]["delta_per_min"] == pytest.approx(1.0, rel=1e-3)  # 3.0 / 3 min
    assert results["humidity"]["delta_per_min"] == pytest.approx(-1.667, rel=1e-3)  # -5.0 / 3 min

    # SaveState emitted
    assert any(e["event_name"] == "SaveState" for e in event_manager.emitted)


@pytest.mark.asyncio
async def test_run_calibration_multi_mode_note(setup_manager):
    manager, data_store, event_manager, _ = setup_manager
    device = FakeDevice("climate.main")
    data_store.data["devices"] = [device]

    async def _fake_measure(metrics, duration, cap):
        return {
            "temperature": [22.0] if duration == manager.BASELINE_DURATION else [24.0],
            "humidity": [60.0] if duration == manager.BASELINE_DURATION else [58.0],
        }

    manager._measure_phase = _fake_measure
    manager.EFFECT_DURATIONS = {"canClimate": 300}

    await manager._run_calibration("canClimate", [device])

    results = data_store.getDeep("capCalibration.results.canClimate")
    assert "note" in results
    assert "Multi-mode device" in results["note"]
    assert "CalibStart" in [e["event_name"] for e in event_manager.emitted]


@pytest.mark.asyncio
async def test_safety_violation_temperature(setup_manager):
    manager, data_store, _, _ = setup_manager
    data_store.data["tentData"]["temperature"] = 36.0
    violated = await manager._safety_violated(["temperature"])
    assert violated is True


@pytest.mark.asyncio
async def test_safety_no_violation(setup_manager):
    manager, data_store, _, _ = setup_manager
    data_store.data["tentData"]["temperature"] = 24.0
    data_store.data["tentData"]["humidity"] = 55.0
    violated = await manager._safety_violated(["temperature", "humidity"])
    assert violated is False


def test_compute_results(setup_manager):
    manager, _, _, _ = setup_manager
    baseline = {"temperature": [20.0, 21.0], "humidity": [50.0, 51.0]}
    effect = {"temperature": [23.0, 24.0], "humidity": [48.0, 47.0]}
    results = manager._compute_results(
        "canHeat", baseline, effect, ["temperature", "humidity"], 300
    )

    # temperature delta = (23.5 - 20.5) = 3.0 -> 3.0/5 = 0.6 per min
    assert results["temperature"]["delta_per_min"] == pytest.approx(0.6, rel=1e-3)
    # humidity delta = (47.5 - 50.5) = -3.0 -> -3.0/5 = -0.6 per min
    assert results["humidity"]["delta_per_min"] == pytest.approx(-0.6, rel=1e-3)
    assert results["temperature"]["confidence"] == pytest.approx(0.067, abs=0.01)


def test_compute_results_insufficient_data(setup_manager):
    manager, _, _, _ = setup_manager
    baseline = {"temperature": []}
    effect = {"temperature": [25.0]}
    results = manager._compute_results(
        "canHeat", baseline, effect, ["temperature"], 300
    )
    assert results["temperature"]["note"] == "insufficient_data"


def test_format_results(setup_manager):
    manager, _, _, _ = setup_manager
    results = {
        "timestamp": "2026-01-01T12:00:00",
        "temperature": {
            "delta": 2.5,
            "delta_per_min": 0.5,
            "confidence": 0.95,
        },
    }
    text = manager._format_results("canHeat", results, ["temperature"])
    assert "Calibration complete for 'canHeat'" in text
    assert "0.500/min" in text
    assert "confidence=0.95" in text


def test_compute_response_curve_results(setup_manager):
    manager, _, _, _ = setup_manager
    baseline = {
        "temperature": [20.0, 20.0],
        "humidity": [50.0, 50.0],
    }
    step_results = {
        25: {"temperature": [20.5, 20.5], "humidity": [49.5, 49.5]},
        50: {"temperature": [21.5, 21.5], "humidity": [48.5, 48.5]},
        75: {"temperature": [23.0, 23.0], "humidity": [47.0, 47.0]},
        100: {"temperature": [25.0, 25.0], "humidity": [45.0, 45.0]},
    }
    results = manager._compute_response_curve_results(
        "canLight", baseline, step_results, ["temperature", "humidity"]
    )

    assert results["isDimmable"] is True
    temp_curve = results["temperature"]["response_curve"]
    assert 25 in temp_curve
    assert 100 in temp_curve
    assert temp_curve[100]["delta_per_min"] > temp_curve[25]["delta_per_min"]
    assert results["temperature"]["slope_per_percent"] > 0


@pytest.mark.asyncio
async def test_handle_command_event_wrong_room(setup_manager):
    manager, _, _, _ = setup_manager
    called = []
    manager.start_calibration = lambda cap: called.append(cap)

    class FakeEvent:
        data = {"room": "other_room", "action": "start", "cap": "canHeat"}

    await manager._handle_command(FakeEvent())
    assert called == []


@pytest.mark.asyncio
async def test_handle_command_event_start(setup_manager):
    manager, _, _, _ = setup_manager
    called = []

    async def _fake_start(cap):
        called.append(cap)

    manager.start_calibration = _fake_start

    class FakeEvent:
        data = {"room": "test_room", "action": "start", "cap": "canHeat"}

    await manager._handle_command(FakeEvent())
    assert called == ["canHeat"]


@pytest.mark.asyncio
async def test_handle_command_event_stop(setup_manager):
    manager, _, _, _ = setup_manager
    called = []

    async def _fake_stop():
        called.append(1)

    manager.stop_calibration = _fake_stop

    class FakeEvent:
        data = {"room": "test_room", "action": "stop"}

    await manager._handle_command(FakeEvent())
    assert called == [1]


@pytest.mark.asyncio
async def test_get_active_calibration_and_results(setup_manager):
    manager, data_store, _, _ = setup_manager
    data_store.setDeep("capCalibration.active", {"cap": "canHeat"})
    data_store.setDeep("capCalibration.results.canHeat", {"temperature": {"delta_per_min": 0.5}})

    assert manager.get_active_calibration()["cap"] == "canHeat"
    assert manager.get_calibration_results("canHeat")["temperature"]["delta_per_min"] == 0.5
    assert "canHeat" in manager.get_calibration_results()


@pytest.mark.asyncio
async def test_console_resolve_cap_case_insensitive():
    from custom_components.opengrowbox.OGBController.managers.OGBConsoleManager import (
        OGBConsoleManager,
    )

    data_store = FakeDataStore(
        {
            "capabilities": {
                "canHeat": {"state": True, "count": 1, "devEntities": []},
                "canCO2": {"state": True, "count": 1, "devEntities": []},
            }
        }
    )
    event_manager = FakeEventManager()
    hass = FakeHass()
    console = OGBConsoleManager(hass, data_store, event_manager, "test_room")
    # give init() task a chance to run
    await asyncio.sleep(0)

    assert console._resolve_cap("canheat") == "canHeat"
    assert console._resolve_cap("CANHEAT") == "canHeat"
    assert console._resolve_cap("canHeat") == "canHeat"
    assert console._resolve_cap("canUnknown") == ""

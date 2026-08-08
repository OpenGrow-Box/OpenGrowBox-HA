from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.opengrowbox.OGBController.managers.hydro.crop_steering.OGBCSConfigurationManager import (
    OGBCSConfigurationManager,
)
from custom_components.opengrowbox.OGBController.managers.hydro.crop_steering.OGBCSManager import (
    CSMode,
    OGBCSManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _cs_manager(initial=None):
    manager = OGBCSManager.__new__(OGBCSManager)
    manager.room = "dev_room"
    manager.data_store = FakeDataStore(initial or {})
    manager.event_manager = FakeEventManager()
    manager.hass = None
    manager.config_manager = OGBCSConfigurationManager(manager.data_store, manager.room, None)
    return manager


async def _noop_coroutine(*args, **kwargs):
    pass


def test_extract_phase_from_mode_and_value():
    manager = _cs_manager()

    assert manager._extract_phase_from_mode(CSMode.MANUAL_P2) == "p2"
    assert manager._extract_phase_from_value("P1") == "p1"
    assert manager._extract_phase_from_value("manual_p3") == "p3"
    assert manager._extract_phase_from_value(None) == "p0"


def test_get_drippers_filters_only_dripper_devices():
    manager = _cs_manager(
        {
            "capabilities": {
                "canPump": {
                    "devEntities": [
                        "switch.dripper_front",
                        "switch.water_pump",
                        "switch.dripper_back",
                    ]
                }
            }
        }
    )

    drippers = manager._get_drippers()
    assert "switch.dripper_front" in drippers
    assert "switch.dripper_back" in drippers
    assert "switch.water_pump" not in drippers


def test_get_automatic_timing_settings_reads_numeric_values():
    manager = _cs_manager(
        {
            "CropSteering": {
                "Substrate": {
                    "p1": {
                        "Shot_Duration_Sec": "45",
                        "Shot_Intervall": "30.5",
                        "Shot_Sum": "7",
                    }
                }
            }
        }
    )

    settings = manager._get_automatic_timing_settings("p1")
    assert settings["ShotDuration"] == 45
    assert settings["ShotIntervall"] == 30.5
    assert settings["ShotSum"] == 7


def test_get_manual_phase_settings_prefers_new_paths_and_converts_types():
    manager = _cs_manager(
        {
            "CropSteering": {
                "Substrate": {
                    "p2": {
                        "Shot_Intervall": "25",
                        "Shot_Duration_Sec": "40",
                        "Shot_Sum": "6",
                        "VWC_Target": "62.5",
                    }
                }
            }
        }
    )

    settings = manager._get_manual_phase_settings("p2")
    assert settings["ShotIntervall"]["value"] == 25.0
    assert settings["ShotDuration"]["value"] == 40
    assert settings["ShotSum"]["value"] == 6
    assert settings["VWCTarget"]["value"] == 62.5


@pytest.mark.asyncio
async def test_p0_transitions_to_p1_when_vwc_below_min_and_in_window():
    """Regression test: P0 → P1 was unreachable due to indentation bug."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "CropPhase": "p0",
                "Substrate": {
                    "p0": {
                        "VWC_Min": "50",
                        "VWC_Max": "70",
                        "VWC_Target": "60",
                    }
                },
            },
        }
    )
    manager._is_in_irrigation_window = lambda: True

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    await manager._handle_phase_p0_auto(
        vwc=48.0,
        ec=0.0,
        preset={"VWCMin": 50.0, "VWCMax": 70.0, "VWCTarget": 60.0},
    )

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p1"


@pytest.mark.asyncio
async def test_p0_stays_in_p0_when_vwc_above_min():
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "CropPhase": "p0",
            },
        }
    )
    manager._is_in_irrigation_window = lambda: True

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    await manager._handle_phase_p0_auto(
        vwc=55.0,
        ec=0.0,
        preset={"VWCMin": 50.0, "VWCMax": 70.0, "VWCTarget": 60.0},
    )

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_p0_transitions_to_p3_when_lights_off():
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {
                "CropPhase": "p0",
            },
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    await manager._handle_phase_p0_auto(
        vwc=55.0,
        ec=0.0,
        preset={"VWCMin": 50.0, "VWCMax": 70.0, "VWCTarget": 60.0},
    )

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_p2_transitions_to_p3_when_lights_off():
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {
                "CropPhase": "p2",
            },
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    await manager._handle_phase_p2_auto(
        vwc=55.0,
        ec=0.0,
        is_light_on=False,
        preset={"VWCMin": 50.0, "VWCMax": 70.0, "VWCTarget": 60.0},
    )

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_p3_transitions_to_p0_when_lights_on():
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "CropPhase": "p3",
                "phaseStartTime": datetime.now() - timedelta(hours=5),
            },
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    await manager._handle_phase_p3_auto(
        vwc=55.0,
        ec=0.0,
        is_light_on=True,
        preset={
            "VWCMin": 50.0,
            "VWCMax": 70.0,
            "VWCTarget": 60.0,
            "target_dryback_percent": 10.0,
            "min_dryback_percent": 8.0,
            "max_dryback_percent": 12.0,
            "emergency_threshold": 0.90,
            "ec_increase_step": 0.1,
            "ec_decrease_step": 0.1,
            "ECTarget": 2.0,
        },
    )

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_manual_phase_change_event_signals_running_cycle():
    """Manual phase change should set the asyncio event to restart the cycle."""
    import asyncio

    manager = _cs_manager()
    manager._manual_phase_changed_event = asyncio.Event()
    manager._main_task = SimpleNamespace(done=lambda: False)

    await manager._on_manual_phase_changed({"phase": "p2"})

    assert manager._manual_phase_changed_event.is_set() is True


class FakeNotificator:
    def __init__(self):
        self.criticals = []

    async def critical(self, message, title="OGB Critical Alert"):
        self.criticals.append({"title": title, "message": message})


def _cs_manager_with_failsafe(initial=None):
    manager = _cs_manager(initial)
    manager._init_failsafe_state()
    manager.notificator = FakeNotificator()
    manager._turn_off_all_drippers = lambda: _noop_coroutine()
    return manager


def test_bulletproof_preset_ignores_user_settings():
    """Automatic bulletproof preset must use base + medium offset, not user overrides."""
    manager = _cs_manager(
        {
            "CropSteering": {
                "Substrate": {
                    "p1": {
                        "VWC_Target": "99",
                        "VWC_Min": "1",
                        "VWC_Max": "100",
                        "Shot_Duration_Sec": "999",
                        "Shot_Intervall": "999",
                        "Shot_Sum": "99",
                    }
                }
            }
        }
    )
    manager.medium_type = "rockwool"
    preset = manager._get_automatic_bulletproof_preset("p1")
    # Default plant stage is veg (+2% VWC modifier), so base values are shifted.
    # Raw p1 base: VWCMax=70, VWCTarget=68 -> shifted by +2.
    assert preset["VWCMax"] == 72.0
    assert preset["VWCTarget"] == 70.0
    assert preset["VWCTarget"] <= preset["VWCMax"]
    assert preset["irrigation_duration"] == 45
    assert preset["max_cycles"] == 10


def test_safe_vwc_bounds_clamp_by_learned_max_saturation():
    manager = _cs_manager_with_failsafe()
    manager.data_store.setDeep("CropSteering.Learned.max_saturation_vwc", 65.0)
    preset = {"VWCMin": 55.0, "VWCMax": 70.0, "VWCTarget": 68.0}
    bounds = manager._get_safe_vwc_bounds(preset)
    # Hard max should be capped at learned max + 5 (and never exceed 90)
    assert bounds["VWCMax"] <= 70.0
    assert bounds["VWCMax"] <= 70.0
    assert bounds["VWCTarget"] <= bounds["VWCMax"]
    assert bounds["VWCMin"] >= manager._ABSOLUTE_VWC_MIN


@pytest.mark.asyncio
async def test_flood_guard_stops_irrigation_and_notifies():
    manager = _cs_manager_with_failsafe()
    manager._stop_all_irrigation = lambda reason: _noop_coroutine()

    safe, reason = await manager._run_failsafe_checks(95.0, source="automatic")
    assert safe is False
    assert "flood_guard" in reason
    assert len(manager.notificator.criticals) == 1
    assert "FLOOD GUARD" in manager.notificator.criticals[0]["title"]


@pytest.mark.asyncio
async def test_dryout_guard_stops_irrigation_and_notifies():
    manager = _cs_manager_with_failsafe()
    manager._stop_all_irrigation = lambda reason: _noop_coroutine()

    safe, reason = await manager._run_failsafe_checks(2.0, source="automatic")
    assert safe is False
    assert "dryout_guard" in reason
    assert len(manager.notificator.criticals) == 1
    assert "DRYOUT GUARD" in manager.notificator.criticals[0]["title"]


@pytest.mark.asyncio
async def test_sensor_stuck_guard_stops_irrigation_and_notifies():
    manager = _cs_manager_with_failsafe()
    manager._stop_all_irrigation = lambda reason: _noop_coroutine()

    # First validation call detects stuck but allows it once (stuck_counter=1)
    for _ in range(11):
        manager._record_sensor_reading(55.0)
    safe, reason = await manager._run_failsafe_checks(55.0, source="automatic")
    assert safe is True

    # Second call with still-stuck sensor triggers the guard
    manager._record_sensor_reading(55.0)
    safe, reason = await manager._run_failsafe_checks(55.0, source="automatic")
    assert safe is False
    assert "stuck" in reason.lower()
    assert len(manager.notificator.criticals) == 1


@pytest.mark.asyncio
async def test_sensor_jump_rejects_reading():
    manager = _cs_manager_with_failsafe()
    manager._stop_all_irrigation = lambda reason: _noop_coroutine()

    manager._record_sensor_reading(55.0)
    manager._record_sensor_reading(55.0)
    valid, reason = manager._validate_sensor_reading(90.0)
    assert valid is False
    assert "jump" in reason.lower()


def test_ineffective_irrigation_guard_triggers():
    manager = _cs_manager_with_failsafe()
    # Simulate 3 irrigation shots that did not raise VWC
    manager._record_irrigation(30, 50.0)
    manager._record_irrigation(30, 50.0)
    manager._record_irrigation(30, 50.0)
    assert manager._is_irrigation_ineffective(50.2) is True


@pytest.mark.asyncio
async def test_ineffective_irrigation_guard_stops_and_notifies():
    manager = _cs_manager_with_failsafe()
    manager._stop_all_irrigation = lambda reason: _noop_coroutine()

    manager._record_irrigation(30, 50.0)
    manager._record_irrigation(30, 50.0)
    manager._record_irrigation(30, 50.0)
    safe, reason = await manager._run_failsafe_checks(50.2, source="automatic")
    assert safe is False
    assert "irrigation_ineffective" in reason
    assert len(manager.notificator.criticals) == 1
    assert "Irrigation Ineffective" in manager.notificator.criticals[0]["title"]


@pytest.mark.asyncio
async def test_max_runtime_guard_stops_and_notifies():
    manager = _cs_manager_with_failsafe()
    manager._stop_all_irrigation = lambda reason: _noop_coroutine()

    manager._irrigation_total_seconds = manager._MAX_TOTAL_PUMP_SECONDS_PER_CYCLE + 1
    safe, reason = await manager._run_failsafe_checks(55.0, source="automatic")
    assert safe is False
    assert "max_runtime" in reason
    assert len(manager.notificator.criticals) == 1


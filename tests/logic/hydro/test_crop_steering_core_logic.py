from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

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


def test_parse_mode_distinguishes_manual_and_manual_transition():
    manager = _cs_manager()

    assert manager._parse_mode("Automatic") == CSMode.AUTOMATIC
    assert manager._parse_mode("Manual-Transition") == CSMode.MANUAL_TRANSITION

    manager.data_store.setDeep("CropSteering.CropPhase", "p2")
    assert manager._parse_mode("Manual") == CSMode.MANUAL_P2


def test_use_auto_transitions_only_for_manual_transition():
    manager = _cs_manager()
    assert manager._use_auto_transitions() is False

    manager.data_store.setDeep("CropSteering.ActiveMode", "Manual")
    assert manager._use_auto_transitions() is False

    manager.data_store.setDeep("CropSteering.ActiveMode", "Manual-Transition")
    assert manager._use_auto_transitions() is True


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
async def test_p3_stays_in_p3_during_ramp_down_when_light_near_off():
    """Regression: P3 entered early (near light-off) must NOT bounce to P0
    while the light entity still reports ON during the end-of-day ramp-down."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "CropPhase": "p3",
                "phaseStartTime": datetime.now() - timedelta(minutes=10),
                "startNightMoisture": 62.0,
            },
        }
    )
    manager._is_near_light_off = lambda buffer_minutes=120: True

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

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_forced_transition_p3_to_p0_when_lights_on_day():
    """Forced transition must still leave P3 when lights are ON during the day."""
    manager = _cs_manager(
        {"isPlantDay": {"islightON": True}, "CropSteering": {"CropPhase": "p3"}}
    )
    manager._is_near_light_off = lambda buffer_minutes=120: False

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    transitioned = await manager._check_forced_light_phase_transition("p3", True, 55.0)

    assert transitioned is True
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_forced_transition_keeps_p3_during_ramp_down():
    """Regression: the forced light transition must NOT undo the early P1/P2 -> P3
    transition during the end-of-day ramp-down (light still ON, going off soon)."""
    manager = _cs_manager(
        {"isPlantDay": {"islightON": True}, "CropSteering": {"CropPhase": "p3"}}
    )
    manager._is_near_light_off = lambda buffer_minutes=120: True

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    transitioned = await manager._check_forced_light_phase_transition("p3", True, 55.0)

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_forced_transition_p0_to_p3_when_lights_off():
    """Forced transition must enter P3 when lights are OFF and P0 is stuck."""
    manager = _cs_manager(
        {"isPlantDay": {"islightON": False}, "CropSteering": {"CropPhase": "p0"}}
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    transitioned = await manager._check_forced_light_phase_transition("p0", False, 55.0)

    assert transitioned is True
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_forced_transition_no_op_when_phase_matches_light():
    """No forced transition when the phase already matches the light state."""
    manager = _cs_manager(
        {"isPlantDay": {"islightON": True}, "CropSteering": {"CropPhase": "p1"}}
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    transitioned = await manager._check_forced_light_phase_transition("p1", True, 55.0)

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p1"


@pytest.mark.asyncio
async def test_manual_phase_change_event_signals_running_cycle():
    """Manual phase change should set the asyncio event to restart the cycle."""
    import asyncio

    manager = _cs_manager()
    manager._manual_phase_changed_event = asyncio.Event()
    manager._main_task = SimpleNamespace(done=lambda: False)

    await manager._on_manual_phase_changed({"phase": "p2"})

    assert manager._manual_phase_changed_event.is_set() is True


@pytest.mark.asyncio
async def test_manual_p0_transitions_to_p1_uses_own_vwc_min_in_window():
    """Manual-Transition P0→P1 must use P0's own VWC_Min (like automatic), not P1's."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "ActiveMode": "Manual-Transition",
                "CropPhase": "p0",
                "Substrate": {
                    "p0": {"VWC_Min": "50"},
                    "p1": {"VWC_Min": "30"},  # must NOT be used for P0→P1
                },
            },
        }
    )
    manager._is_in_irrigation_window = lambda: True

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p0")
    transitioned = await manager._manual_phase_light_transition(
        "p0", vwc=40.0, settings=settings
    )

    assert transitioned is True
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p1"


@pytest.mark.asyncio
async def test_pure_manual_p0_stays_p0_when_dry_in_window():
    """Pure Manual: P0 stays P0 even when VWC is below VWC_Min in the window."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "ActiveMode": "Manual",
                "CropPhase": "p0",
                "Substrate": {"p0": {"VWC_Min": "50"}},
            },
        }
    )
    manager._is_in_irrigation_window = lambda: True

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p0")
    transitioned = await manager._manual_phase_light_transition(
        "p0", vwc=40.0, settings=settings
    )

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_manual_p0_stays_in_p0_outside_irrigation_window():
    """Manual-Transition P0→P1 must only happen inside the irrigation window (like automatic)."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "ActiveMode": "Manual-Transition",
                "CropPhase": "p0",
                "Substrate": {"p0": {"VWC_Min": "50"}},
            },
        }
    )
    manager._is_in_irrigation_window = lambda: False

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p0")
    transitioned = await manager._manual_phase_light_transition(
        "p0", vwc=40.0, settings=settings
    )

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_manual_p0_transitions_to_p3_when_lights_off():
    """Manual-Transition: P0 at night must go to P3 (night dryback)."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {"ActiveMode": "Manual-Transition", "CropPhase": "p0"},
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p0")
    transitioned = await manager._manual_phase_light_transition(
        "p0", vwc=55.0, settings=settings
    )

    assert transitioned is True
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_pure_manual_p0_stays_p0_at_night():
    """Pure Manual: P0 stays P0 at night - no forced P3 transition."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {"ActiveMode": "Manual", "CropPhase": "p0"},
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p0")
    transitioned = await manager._manual_phase_light_transition(
        "p0", vwc=55.0, settings=settings
    )

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_manual_p2_transitions_to_p3_when_lights_off():
    """Manual-Transition: P2 at night must go to P3."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {"ActiveMode": "Manual-Transition", "CropPhase": "p2"},
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p2")
    transitioned = await manager._manual_phase_light_transition(
        "p2", vwc=60.0, settings=settings
    )

    assert transitioned is True
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_pure_manual_p2_stays_p2_at_night():
    """Pure Manual: P2 stays P2 at night - no forced P3 transition."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {"ActiveMode": "Manual", "CropPhase": "p2"},
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p2")
    transitioned = await manager._manual_phase_light_transition(
        "p2", vwc=60.0, settings=settings
    )

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p2"


@pytest.mark.asyncio
async def test_manual_p2_stays_in_p2_when_lights_on():
    """Manual-Transition: P2 stays in P2 during the day."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {"ActiveMode": "Manual-Transition", "CropPhase": "p2"},
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p2")
    transitioned = await manager._manual_phase_light_transition(
        "p2", vwc=60.0, settings=settings
    )

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p2"


@pytest.mark.asyncio
async def test_pure_manual_p3_stays_p3_at_day():
    """Pure Manual: P3 stays P3 during the day - no forced P0 transition."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {"ActiveMode": "Manual", "CropPhase": "p3"},
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p3")
    transitioned = await manager._manual_phase_light_transition(
        "p3", vwc=55.0, settings=settings
    )

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_manual_p3_transitions_to_p0_when_lights_on():
    """Manual-Transition: P3 goes to P0 when lights turn on."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {"ActiveMode": "Manual-Transition", "CropPhase": "p3"},
        }
    )

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._calibrate_p3_vwc_min = lambda vwc: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p3")
    transitioned = await manager._manual_phase_light_transition(
        "p3", vwc=55.0, settings=settings
    )

    assert transitioned is True
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_manual_p3_stays_in_p3_during_ramp_down():
    """Regression: Manual-Transition P3 must not bounce to P0 while the light
    is still ON during the end-of-day ramp-down (P3 was entered early)."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {"ActiveMode": "Manual-Transition", "CropPhase": "p3"},
        }
    )
    manager._is_near_light_off = lambda buffer_minutes=120: True

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._calibrate_p3_vwc_min = lambda vwc: _noop_coroutine()

    settings = manager._get_manual_phase_settings("p3")
    transitioned = await manager._manual_phase_light_transition(
        "p3", vwc=55.0, settings=settings
    )

    assert transitioned is False
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


def _manual_cycle_manager(initial=None, near_light_off=False, mode="Manual"):
    """Build a manager whose _manual_cycle can run a single iteration."""
    initial = initial or {
        "isPlantDay": {"islightON": True},
        "CropSteering": {"CropPhase": "p1"},
    }
    if "CropSteering" not in initial:
        initial["CropSteering"] = {}
    initial["CropSteering"].setdefault("ActiveMode", mode)
    manager = _cs_manager(initial)

    async def _get_sensor_averages():
        return {
            "vwc": 55.0,
            "ec": 1.0,
            "pore_ec": 1.0,
            "temperature": 25.0,
            "bulk_ec": 1.0,
        }

    async def _failsafe(vwc, source):
        return True, None

    async def _set_phase(phase):
        manager.data_store.setDeep("CropSteering.CropPhase", phase)

    manager._irrigate_calls = []

    async def _irrigate(duration=None, target_vwc=None, max_vwc=None, **kwargs):
        manager._irrigate_calls.append(
            {"duration": duration, "target_vwc": target_vwc, "max_vwc": max_vwc}
        )

    manager._get_sensor_averages = _get_sensor_averages
    manager._run_failsafe_checks = _failsafe
    manager._evaluate_failsafe_condition = lambda vwc: None
    manager._record_sensor_reading = lambda vwc: None
    manager._is_near_light_off = lambda buffer_minutes=120: near_light_off
    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._irrigate = _irrigate
    manager._emit_state_heartbeat = lambda force=False: _noop_coroutine()
    manager._calibrate_p1_vwc_max = lambda vwc, cap=None: _noop_coroutine()
    manager._calibrate_p3_vwc_min = lambda vwc: _noop_coroutine()
    manager._update_number_entity = lambda *a, **kw: _noop_coroutine()
    return manager


async def _run_manual_cycle_once(manager, phase):
    """Run a single _manual_cycle iteration.

    _manual_cycle loops forever (sleeping 10s per iteration); the sleep is
    patched so the run exits after the first full iteration for testing.
    """
    manager._turn_off_all_drippers = lambda: _noop_coroutine()
    manager._emergency_stop = lambda: _noop_coroutine()

    class _StopCycle(Exception):
        pass

    async def _sleep(seconds):
        raise _StopCycle()

    with patch("asyncio.sleep", _sleep):
        await manager._manual_cycle(phase)


@pytest.mark.asyncio
async def test_manual_p1_transitions_to_p3_when_lights_off():
    """Manual-Transition: P1 must stop irrigating at night and go to P3."""
    manager = _manual_cycle_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {"CropPhase": "p1"},
        },
        mode="Manual-Transition",
    )

    await manager._manual_cycle("p1")

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_manual_p1_transitions_to_p3_when_lights_near_off():
    """Manual-Transition: P1 must stop early when lights are about to turn off (2h buffer)."""
    manager = _manual_cycle_manager(near_light_off=True, mode="Manual-Transition")

    await manager._manual_cycle("p1")

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


@pytest.mark.asyncio
async def test_pure_manual_p1_stays_p1_at_night():
    """Pure Manual: P1 stays P1 at night - no automatic transition to P3."""
    manager = _manual_cycle_manager(
        {
            "isPlantDay": {"islightON": False},
            "CropSteering": {"CropPhase": "p1"},
        },
        mode="Manual",
    )

    await _run_manual_cycle_once(manager, "p1")

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p1"


@pytest.mark.asyncio
async def test_pure_manual_p1_stays_p1_when_near_light_off():
    """Pure Manual: P1 stays P1 even shortly before lights off."""
    manager = _manual_cycle_manager(near_light_off=True, mode="Manual")

    await _run_manual_cycle_once(manager, "p1")

    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p1"


@pytest.mark.asyncio
async def test_manual_p3_emergency_irrigation_when_vwc_too_low():
    """Manual P3 must run conservative emergency irrigation at night, like automatic."""
    manager = _cs_manager(
        {
            "CropSteering": {
                "CropPhase": "p3",
                "phaseStartTime": datetime.now() - timedelta(hours=6),
                "Substrate": {"p3": {"VWC_Min": "52"}},
            },
        }
    )
    manager._ABSOLUTE_VWC_MIN = 5.0
    manager._load_learned_values = lambda: {"min_dryback_vwc": None}

    irrigated = []

    async def _irrigate(duration=None, is_emergency=False, target_vwc=None, max_vwc=None):
        irrigated.append(duration)

    manager._irrigate = _irrigate

    settings = manager._get_manual_phase_settings("p3")
    await manager._manual_p3_emergency(vwc=30.0, settings=settings)

    assert irrigated == [15]  # duration capped at 15s
    assert manager.data_store.getDeep("CropSteering.p3_emergency_count") == 1


@pytest.mark.asyncio
async def test_manual_p3_no_emergency_when_vwc_ok():
    manager = _cs_manager(
        {
            "CropSteering": {
                "CropPhase": "p3",
                "phaseStartTime": datetime.now() - timedelta(hours=6),
                "Substrate": {"p3": {"VWC_Min": "52"}},
            },
        }
    )
    manager._ABSOLUTE_VWC_MIN = 5.0
    manager._load_learned_values = lambda: {"min_dryback_vwc": None}

    irrigated = []

    async def _irrigate(duration=None, is_emergency=False, target_vwc=None, max_vwc=None):
        irrigated.append(duration)

    manager._irrigate = _irrigate

    settings = manager._get_manual_phase_settings("p3")
    await manager._manual_p3_emergency(vwc=55.0, settings=settings)

    assert irrigated == []


@pytest.mark.asyncio
async def test_manual_p3_emergency_fires_immediately_below_threshold():
    """Manual P3 fires a conservative emergency shot immediately when VWC drops
    below the emergency level - no grace period, because leaving a critically
    dry medium unwatered would be unsafe (matches Automatic P3 behavior).
    Subsequent shots are gated by a 5-minute interval."""
    manager = _cs_manager(
        {
            "CropSteering": {
                "CropPhase": "p3",
                "phaseStartTime": datetime.now() - timedelta(minutes=30),
                "Substrate": {"p3": {"VWC_Min": "52"}},
            },
        }
    )
    manager._ABSOLUTE_VWC_MIN = 5.0
    manager._load_learned_values = lambda: {"min_dryback_vwc": None}

    irrigated = []

    async def _irrigate(duration=None, is_emergency=False, target_vwc=None, max_vwc=None):
        irrigated.append(duration)

    manager._irrigate = _irrigate

    settings = manager._get_manual_phase_settings("p3")

    # VWC 30 < 52 -> immediate conservative 15s shot
    await manager._manual_p3_emergency(vwc=30.0, settings=settings)
    assert irrigated == [15]
    assert manager.data_store.getDeep("CropSteering.p3_emergency_count") == 1

    # Immediate second call -> 5-minute interval gate, no additional shot
    await manager._manual_p3_emergency(vwc=30.0, settings=settings)
    assert irrigated == [15]
    assert manager.data_store.getDeep("CropSteering.p3_emergency_count") == 1


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
    preset =     manager._get_automatic_preset("p1")
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
    # Hard max is capped at the observed P1 peak (no +5 headroom)
    assert bounds["VWCMax"] == 65.0
    assert bounds["VWCMax"] <= 70.0
    assert bounds["VWCTarget"] <= bounds["VWCMax"]
    assert bounds["VWCMin"] >= manager._ABSOLUTE_VWC_MIN


def test_safe_vwc_bounds_clamp_by_field_capacity():
    manager = _cs_manager_with_failsafe()
    manager.data_store.setDeep("CropSteering.Learned.max_saturation_vwc", 66.0)
    manager.data_store.setDeep("CropSteering.Learned.field_capacity_vwc", 63.0)
    preset = {"VWCMin": 55.0, "VWCMax": 70.0, "VWCTarget": 68.0}
    bounds = manager._get_safe_vwc_bounds(preset)
    # Field capacity (plateau) is the day-to-day capacity reference
    assert bounds["VWCMax"] == 63.0
    assert bounds["VWCTarget"] <= bounds["VWCMax"]


def test_learned_field_capacity_ratchets_up_only():
    manager = _cs_manager()
    manager.data_store.setDeep("CropSteering.Learned.field_capacity_vwc", 64.0)

    # A lower plateau reading must NOT drag field capacity down
    manager._update_learned_field_capacity(62.0)
    assert manager.data_store.getDeep("CropSteering.Learned.field_capacity_vwc") == 64.0

    # A higher reading ratchets it up
    manager._update_learned_field_capacity(66.0)
    assert manager.data_store.getDeep("CropSteering.Learned.field_capacity_vwc") == 64.6


def test_learned_field_capacity_init_and_bounds():
    manager = _cs_manager()
    manager.data_store.setDeep("CropSteering.Learned.max_saturation_vwc", 68.0)

    manager._update_learned_field_capacity(60.0)
    assert manager.data_store.getDeep("CropSteering.Learned.field_capacity_vwc") == 60.0

    # Values above the observed saturation are rejected
    manager._update_learned_field_capacity(70.0)
    assert manager.data_store.getDeep("CropSteering.Learned.field_capacity_vwc") == 60.0


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

    # _SENSOR_STUCK_THRESHOLD (15) identical readings -> check runs but allows once (stuck_counter=1)
    for _ in range(manager._SENSOR_STUCK_THRESHOLD):
        manager._record_sensor_reading(55.0)
    safe, reason = await manager._run_failsafe_checks(55.0, source="automatic")
    assert safe is True

    # Still-stuck sensor on the next cycle triggers the guard
    manager._record_sensor_reading(55.0)
    safe, reason = await manager._run_failsafe_checks(55.0, source="automatic")
    assert safe is False
    assert reason == "sensor_invalid"
    assert "Sensor stuck" in manager.notificator.criticals[0]["message"]
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


@pytest.mark.asyncio
async def test_manual_non_critical_failsafe_warns_but_does_not_block():
    """In Manual mode non-critical guards must warn once (cooldown) and NOT block irrigation."""
    manager = _cs_manager_with_failsafe()

    calls = []

    async def _send_critical_notification(title, message):
        calls.append({"title": title, "message": message})

    manager._send_critical_notification = _send_critical_notification

    await manager._warn_manual_failsafe("sensor_invalid", 55.0, "p1")
    await manager._warn_manual_failsafe("sensor_invalid", 55.0, "p1")

    assert len(calls) == 1
    assert "NOT stopped" in calls[0]["message"]

    await manager._warn_manual_failsafe("dryout_guard", 4.0, "p1")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_manual_cycle_non_critical_failsafe_still_irrigates():
    """In pure Manual a non-critical failsafe must not skip the scheduled shot."""
    manager = _manual_cycle_manager(mode="Manual")
    manager._evaluate_failsafe_condition = lambda vwc: "sensor_invalid"
    manager._warn_manual_failsafe = lambda reason, vwc, phase: _noop_coroutine()

    irrigated = []

    async def _irrigate(duration=None, target_vwc=None, max_vwc=None, **kwargs):
        irrigated.append(duration)

    manager._irrigate = _irrigate
    manager.data_store.setDeep("CropSteering.lastIrrigationTime", None)
    manager.data_store.setDeep("CropSteering.shotCounter", 0)

    await _run_manual_cycle_once(manager, "p1")

    assert irrigated, "Manual irrigation must run despite non-critical failsafe"


# --- Adaptive VWCTarget (p1_peak_vwc) ---


def test_update_learned_p1_peak_first_value():
    manager = _cs_manager()

    manager._update_learned_p1_peak(58.0)

    assert manager.data_store.getDeep("CropSteering.Learned.p1_peak_vwc") == 58.0


def test_update_learned_p1_peak_uses_ema():
    manager = _cs_manager()
    manager._update_learned_p1_peak(60.0)

    manager._update_learned_p1_peak(50.0)

    # 60.0 * 0.7 + 50.0 * 0.3 = 57.0 (EMA smooths, does not jump to the raw value)
    assert manager.data_store.getDeep("CropSteering.Learned.p1_peak_vwc") == 57.0


def test_update_learned_p1_peak_ignores_out_of_range():
    manager = _cs_manager()
    manager._update_learned_p1_peak(58.0)

    manager._update_learned_p1_peak(manager._ABSOLUTE_VWC_MIN - 1)
    manager._update_learned_p1_peak(manager._ABSOLUTE_VWC_MAX + 1)

    assert manager.data_store.getDeep("CropSteering.Learned.p1_peak_vwc") == 58.0


@pytest.mark.asyncio
async def test_complete_p1_saturation_success_updates_p1_peak():
    manager = _cs_manager_with_failsafe()
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._set_crop_phase_and_update_selector = lambda phase: _noop_coroutine()

    await manager._complete_p1_saturation(58.0, 58.0, success=True)

    assert manager.data_store.getDeep("CropSteering.Learned.p1_peak_vwc") == 58.0


@pytest.mark.asyncio
async def test_complete_p1_saturation_failure_keeps_p1_peak():
    """A bad day (max attempts, VWC far below minimum) must not ratchet the target down."""
    manager = _cs_manager_with_failsafe()
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._set_crop_phase_and_update_selector = lambda phase: _noop_coroutine()
    manager.data_store.setDeep("CropSteering.Learned.p1_peak_vwc", 58.0)

    await manager._complete_p1_saturation(40.0, 68.0, success=False)

    assert manager.data_store.getDeep("CropSteering.Learned.p1_peak_vwc") == 58.0


def _p1_target_manager():
    """Manager with _is_lights_on forced and _irrigate captured for P1 target tests."""
    manager = _cs_manager_with_failsafe({"isPlantDay": {"islightON": True}})
    manager._irrigate_calls = []

    async def _irrigate(duration=None, target_vwc=None, max_vwc=None, **kwargs):
        manager._irrigate_calls.append(
            {"duration": duration, "target_vwc": target_vwc, "max_vwc": max_vwc}
        )

    manager._irrigate = _irrigate
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._set_crop_phase_and_update_selector = lambda phase: _noop_coroutine()
    return manager


@pytest.mark.asyncio
async def test_p1_auto_target_clamped_by_learned_peak():
    """P1 must irrigate towards min(preset, learned p1_peak_vwc), not the raw preset."""
    manager = _p1_target_manager()
    manager.data_store.setDeep("CropSteering.Learned.p1_peak_vwc", 60.0)
    preset = {
        "VWCMin": 55.0,
        "VWCMax": 70.0,
        "VWCTarget": 68.0,
        "irrigation_duration": 45,
        "wait_between": 180,
        "max_cycles": 10,
    }

    await manager._handle_phase_p1_auto(55.0, 1.0, preset)

    assert manager._irrigate_calls, "P1 should have irrigated on first entry"
    # target_vwc passed to _irrigate is the effective saturation target
    assert manager._irrigate_calls[0]["target_vwc"] == 60.0
    assert manager._irrigate_calls[0]["target_vwc"] != 68.0


@pytest.mark.asyncio
async def test_p1_auto_target_uses_preset_when_no_peak_learned():
    """Without learned data the preset acts as fallback target."""
    manager = _p1_target_manager()
    preset = {
        "VWCMin": 55.0,
        "VWCMax": 70.0,
        "VWCTarget": 68.0,
        "irrigation_duration": 45,
        "wait_between": 180,
        "max_cycles": 10,
    }

    await manager._handle_phase_p1_auto(55.0, 1.0, preset)

    assert manager._irrigate_calls
    assert manager._irrigate_calls[0]["target_vwc"] == 68.0


# --- Next-day EC target (P3 dryback adjustment) ---


def test_adjust_ec_for_dryback_writes_next_ec_target():
    manager = _cs_manager()

    async def _call():
        await manager._adjust_ec_for_dryback(
            2.0, increase=True, step=0.1, min_ec=1.8, max_ec=2.2
        )

    import asyncio
    asyncio.run(_call())

    assert manager.data_store.getDeep("CropSteering.Learned.next_ec_target") == 2.1
    assert manager.data_store.getDeep("CropSteering.p3_ec_adjusted") is True


def test_adjust_ec_for_dryback_clamps_to_band():
    manager = _cs_manager()

    async def _call():
        await manager._adjust_ec_for_dryback(
            2.1, increase=True, step=0.5, min_ec=1.8, max_ec=2.2
        )

    import asyncio
    asyncio.run(_call())

    # 2.1 + 0.5 = 2.6 -> clamped to 2.2
    assert manager.data_store.getDeep("CropSteering.Learned.next_ec_target") == 2.2


def test_adjust_ec_for_dryback_only_once_per_night():
    manager = _cs_manager()
    manager.data_store.setDeep("CropSteering.p3_ec_adjusted", True)

    async def _call():
        await manager._adjust_ec_for_dryback(
            2.0, increase=True, step=0.1, min_ec=1.8, max_ec=2.2
        )

    import asyncio
    asyncio.run(_call())

    assert manager.data_store.getDeep("CropSteering.Learned.next_ec_target") is None


def test_reset_p3_state_tracking_clears_ec_adjustment_flag():
    manager = _cs_manager()
    manager.data_store.setDeep("CropSteering.p3_ec_adjusted", True)

    manager._reset_p3_state_tracking()

    assert manager.data_store.getDeep("CropSteering.p3_ec_adjusted") is False


def test_p1_automatic_default_wait_between_is_15_minutes():
    """P1 shots must follow the reference 15-30 min cadence by default."""
    manager = _cs_manager()

    base = manager.config_manager.get_raw_base_presets()

    assert base["p1"]["wait_between"] == 900
    assert 15 * 60 <= base["p1"]["wait_between"] <= 30 * 60
    assert base["p1"]["irrigation_duration"] == 45
    assert base["p1"]["max_cycles"] == 10


def test_get_effective_ec_target_uses_next_ec_target():
    manager = _cs_manager()
    manager.data_store.setDeep("CropSteering.Learned.next_ec_target", 2.3)
    preset = {"ECTarget": 2.0, "MinEC": 1.8, "MaxEC": 2.2}

    # 2.3 > MaxEC 2.2 -> clamped to 2.2
    assert manager._get_effective_ec_target(preset) == 2.2


def test_get_effective_ec_target_falls_back_to_preset():
    manager = _cs_manager()
    preset = {"ECTarget": 2.0, "MinEC": 1.8, "MaxEC": 2.2}

    assert manager._get_effective_ec_target(preset) == 2.0


# --- Manual P2 auto-calibration (p2_shot_count) ---


def test_record_p2_irrigation_increments_p2_shot_count():
    manager = _cs_manager()
    from datetime import datetime

    manager._record_p2_irrigation(datetime.now())
    manager._record_p2_irrigation(datetime.now())

    assert manager.data_store.getDeep("CropSteering.p2_shot_count") == 2
    assert manager.data_store.getDeep("CropSteering.p2_last_irrigation_time") is not None


@pytest.mark.asyncio
async def test_track_p2_vwc_peak_calibrates_after_3_consistent_peaks():
    """P2 VWCMax auto-calibration must fire once enough cycles are recorded."""
    manager = _cs_manager()
    manager.data_store.setDeep("CropSteering.p2_shot_count", 3)

    await manager._track_p2_vwc_peak(68.0)
    await manager._track_p2_vwc_peak(68.2)
    await manager._track_p2_vwc_peak(68.1)

    assert manager.data_store.getDeep("CropSteering.Calibration.p2.VWCMax") == pytest.approx(68.1, abs=0.1)


@pytest.mark.asyncio
async def test_track_p2_vwc_peak_requires_minimum_cycles():
    """With fewer than 3 recorded irrigation cycles no calibration may happen."""
    manager = _cs_manager()
    manager.data_store.setDeep("CropSteering.p2_shot_count", 1)

    await manager._track_p2_vwc_peak(68.0)
    await manager._track_p2_vwc_peak(68.1)
    await manager._track_p2_vwc_peak(68.0)

    assert manager.data_store.getDeep("CropSteering.Calibration.p2.VWCMax") is None


# --- P2 introduction (reference practice: P1+P3 only in early veg) ---


def test_p2_introduction_mode_defaults_to_auto():
    manager = _cs_manager()
    assert manager._get_p2_introduction_mode() == "auto"

    manager.data_store.setDeep("CropSteering.P2_Introduction", "enabled")
    assert manager._get_p2_introduction_mode() == "enabled"

    manager.data_store.setDeep("CropSteering.P2_Introduction", "DISABLED")
    assert manager._get_p2_introduction_mode() == "disabled"

    manager.data_store.setDeep("CropSteering.P2_Introduction", "garbage")
    assert manager._get_p2_introduction_mode() == "auto"


def test_p2_introduction_threshold_defaults_to_25():
    manager = _cs_manager()
    assert manager._get_p2_introduction_threshold() == 25.0

    manager.data_store.setDeep("CropSteering.P2_Intro_Dryback_Threshold", "30")
    assert manager._get_p2_introduction_threshold() == 30.0

    manager.data_store.setDeep("CropSteering.P2_Intro_Dryback_Threshold", "0")
    assert manager._get_p2_introduction_threshold() == 25.0


def test_should_use_p2_by_mode_and_introduction_flag():
    manager = _cs_manager()

    # default: auto, not introduced yet -> no P2
    assert manager._should_use_p2() is False

    manager.data_store.setDeep("CropSteering.P2_Introduction", "enabled")
    assert manager._should_use_p2() is True

    manager.data_store.setDeep("CropSteering.P2_Introduction", "disabled")
    assert manager._should_use_p2() is False

    manager.data_store.setDeep("CropSteering.P2_Introduction", "auto")
    manager.data_store.setDeep("CropSteering.p2_introduced", True)
    assert manager._should_use_p2() is True


@pytest.mark.asyncio
async def test_p2_disabled_after_saturation_goes_to_p0():
    """Early veg (P2 disabled) must monitor (P0) after P1 saturation, not run P2."""
    manager = _cs_manager({"CropSteering": {"P2_Introduction": "disabled"}})
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    phases = []

    async def _set_phase(phase):
        phases.append(phase)

    manager._set_crop_phase_and_update_selector = _set_phase

    await manager._complete_p1_saturation(65.0, 65.0, success=True)

    assert phases == ["p0"]


@pytest.mark.asyncio
async def test_p2_auto_introduced_when_daily_dryback_exceeds_threshold():
    """In 'auto' mode P2 is introduced once the day's dryback hits the threshold."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "CropPhase": "p0",
                "P2_Introduction": "auto",
                "P2_Intro_Dryback_Threshold": 25.0,
                "day_peak_vwc": 65.0,
                "Substrate": {"p0": {"VWC_Min": "50"}},
            },
        }
    )
    manager._is_lights_on = lambda: True
    manager._set_crop_phase_and_update_selector = lambda phase: _noop_coroutine()
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    # vwc=48 -> dryback (65-48)/65 = 26.2% >= 25% -> introduced
    await manager._handle_phase_p0_auto(48.0, 1.0, {"VWCMin": 50.0})

    assert manager.data_store.getDeep("CropSteering.p2_introduced") is True
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p0"


@pytest.mark.asyncio
async def test_p2_auto_not_introduced_below_threshold():
    """Below the threshold the introduction must not fire."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "CropPhase": "p0",
                "P2_Introduction": "auto",
                "day_peak_vwc": 65.0,
                "Substrate": {"p0": {"VWC_Min": "50"}},
            },
        }
    )
    manager._is_lights_on = lambda: True
    manager._set_crop_phase_and_update_selector = lambda phase: _noop_coroutine()
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    # vwc=60 -> dryback (65-60)/65 = 7.7% < 25% -> not introduced
    await manager._handle_phase_p0_auto(60.0, 1.0, {"VWCMin": 50.0})

    assert manager.data_store.getDeep("CropSteering.p2_introduced") is None


@pytest.mark.asyncio
async def test_p2_introduced_after_saturation_goes_to_p2():
    """Once introduced, P1 saturation must run P2 maintenance again."""
    manager = _cs_manager(
        {
            "CropSteering": {
                "P2_Introduction": "auto",
                "p2_introduced": True,
            }
        }
    )
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    phases = []

    async def _set_phase(phase):
        phases.append(phase)

    manager._set_crop_phase_and_update_selector = _set_phase

    await manager._complete_p1_saturation(65.0, 65.0, success=True)

    assert phases == ["p2"]


# --- Initial Soak (veg start, one-shot saturation to capacity) ---


def test_initial_soak_armed_reads_flag():
    manager = _cs_manager()
    assert manager._is_initial_soak_armed() is False

    manager.data_store.setDeep("CropSteering.InitialSoak", True)
    assert manager._is_initial_soak_armed() is True


@pytest.mark.asyncio
async def test_p0_initial_soak_bypasses_buffer_and_starts_p1():
    """With an armed Initial Soak, P0 must start P1 immediately (no buffer wait)."""
    manager = _cs_manager(
        {
            "isPlantDay": {"islightON": True},
            "CropSteering": {
                "CropPhase": "p0",
                "InitialSoak": True,
                "Substrate": {"p0": {"VWC_Min": "50"}},
            },
        }
    )
    manager._is_lights_on = lambda: True

    phases = []

    async def _set_phase(phase):
        phases.append(phase)

    manager._set_crop_phase_and_update_selector = _set_phase
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()

    await manager._handle_phase_p0_auto(58.0, 1.0, {"VWCMin": 50.0})

    assert phases == ["p1"]


@pytest.mark.asyncio
async def test_p1_soak_targets_capacity_not_learned_peak():
    """During an Initial Soak P1 must saturate to full capacity, ignoring learned peaks."""
    manager = _p1_target_manager()
    manager.data_store.setDeep("CropSteering.InitialSoak", True)
    manager.data_store.setDeep("CropSteering.Learned.p1_peak_vwc", 60.0)
    preset = {
        "VWCMin": 55.0,
        "VWCMax": 70.0,
        "VWCTarget": 68.0,
        "irrigation_duration": 45,
        "wait_between": 180,
        "max_cycles": 10,
    }

    await manager._handle_phase_p1_auto(55.0, 1.0, preset)

    assert manager._irrigate_calls
    # Capacity (VWCMax = 70.0) wins over the learned peak (60.0)
    assert manager._irrigate_calls[0]["target_vwc"] == 70.0
    assert manager._irrigate_calls[0]["max_vwc"] == 70.0


@pytest.mark.asyncio
async def test_initial_soak_disarmed_after_successful_completion():
    """A successful soak must disarm the flag and record the day peak."""
    manager = _cs_manager(
        {
            "CropSteering": {
                "InitialSoak": True,
                "P2_Introduction": "enabled",
            }
        }
    )
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._set_crop_phase_and_update_selector = lambda phase: _noop_coroutine()

    await manager._complete_p1_saturation(70.0, 70.0, success=True)

    assert manager.data_store.getDeep("CropSteering.InitialSoak") is False
    assert manager.data_store.getDeep("CropSteering.day_peak_vwc") == 70.0


@pytest.mark.asyncio
async def test_initial_soak_stays_armed_after_failed_completion():
    """A failed soak (pump/sensor issue) must stay armed so it retries next day."""
    manager = _cs_manager({"CropSteering": {"InitialSoak": True}})
    manager._log_phase_change = lambda a, b, reason: _noop_coroutine()
    manager._set_crop_phase_and_update_selector = lambda phase: _noop_coroutine()

    await manager._complete_p1_saturation(30.0, 68.0, success=False)

    assert manager.data_store.getDeep("CropSteering.InitialSoak") is True


@pytest.mark.asyncio
async def test_day_peak_cleared_on_p3_entry():
    """Entering P3 (lights off) must clear the day's peak reference."""
    manager = _cs_manager({"CropSteering": {"day_peak_vwc": 68.0}})
    manager.hass = None
    manager._clear_failsafe = lambda reason: None

    await manager._set_crop_phase_and_update_selector("p3")

    assert manager.data_store.getDeep("CropSteering.day_peak_vwc") is None
    assert manager.data_store.getDeep("CropSteering.CropPhase") == "p3"


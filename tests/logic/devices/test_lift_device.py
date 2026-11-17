"""Behaviour tests for the Lift actuator (Lift device class)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.opengrowbox.OGBController.OGBDevices.Lift import Lift
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
    CAP_MAPPING,
    DEVICE_TYPE_MAPPING,
)
from custom_components.opengrowbox.OGBController.managers.OGBDeviceManager import (
    OGBDeviceManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


class DummyDevice:
    def __init__(self, *args, **kwargs):
        self.deviceType = args[4] if len(args) > 4 else None


class FakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class FakeStates:
    def __init__(self, states=None):
        self._states = states or {}

    def get(self, entity_id):
        return self._states.get(entity_id)


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, service_data=None, **kwargs):
        self.calls.append(
            {"domain": domain, "service": service, "service_data": service_data}
        )


class FakeHass:
    def __init__(self, states=None):
        self.states = FakeStates(states)
        self.services = FakeServices()


def _make_lift(entities=None, hass_states=None, minmax=None, position=None):
    lift = Lift.__new__(Lift)
    lift.deviceName = "Plant Lift"
    lift.deviceType = "Lift"
    lift.inRoom = "TentRoom"
    lift.deviceLabel = "lift"
    lift.labelMap = []
    lift.isRunning = False
    lift.isAcInfinDev = False
    lift.isSpecialDevice = False
    lift.isDimmable = False
    lift._in_smart_deadband = False
    lift._in_active_control = False
    lift._control_lock_until = 0
    lift._commanded_state = None

    lift.dataStore = FakeDataStore({})
    lift.eventManager = FakeEventManager()
    lift.event_manager = lift.eventManager
    lift.hass = FakeHass(hass_states or {})

    lift.minPosition = Lift.DEFAULT_MIN_POSITION
    lift.maxPosition = Lift.DEFAULT_MAX_POSITION
    lift.positionStep = Lift.DEFAULT_STEP
    lift.settleTimeSec = Lift.DEFAULT_SETTLE_TIME
    lift.pulseDurationSec = Lift.DEFAULT_PULSE_DURATION
    lift.toleranceCM = Lift.DEFAULT_TOLERANCE_CM
    lift.minDistanceCM = Lift.DEFAULT_MIN_DISTANCE_CM
    lift.maxDistanceCM = Lift.DEFAULT_MAX_DISTANCE_CM
    lift.fullTravelTimeSec = Lift.DEFAULT_FULL_TRAVEL_TIME
    lift.maxRuntimeSec = Lift.DEFAULT_MAX_RUNTIME
    lift.maxIterations = Lift.DEFAULT_MAX_ITERATIONS
    lift.noProgressLimit = Lift.DEFAULT_NO_PROGRESS_LIMIT
    lift.sensorMaxAgeSec = Lift.DEFAULT_SENSOR_MAX_AGE
    lift._move_in_progress = False
    lift.position = None

    if minmax:
        lift.dataStore.setDeep("DeviceMinMax.Lift", minmax)
    lift._load_position_config()
    lift._load_closed_loop_config()

    lift.switches = []
    lift.options = []
    lift.sensors = []
    lift.ogbsettings = []
    for entity in entities or []:
        if entity["entity_id"].startswith("number."):
            lift.options.append(entity)
        else:
            lift.switches.append(entity)

    lift.position = position if position is not None else lift._read_current_position()
    return lift


def test_lift_label_resolves_to_lift_device_type():
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    assert manager._determine_device_type_from_labels([{"name": "lift"}]) == "Lift"
    assert manager._determine_device_type_from_labels([{"name": "hublift"}]) == "Lift"
    assert manager._determine_device_type_from_labels([{"name": "lamplift"}]) == "Lift"


def test_lift_is_registered_in_type_and_capability_maps():
    assert "Lift" in DEVICE_TYPE_MAPPING
    assert "lift" in DEVICE_TYPE_MAPPING["Lift"]
    assert CAP_MAPPING["canLift"] == ["lift"]


def test_get_device_class_returns_lift():
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    assert manager.get_device_class("Lift") is Lift


def test_identify_device_by_lift_label(monkeypatch):
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    manager.event_manager = object()
    manager.data_store = object()
    manager.room = "TentRoom"
    manager.hass = None
    monkeypatch.setattr(manager, "get_device_class", lambda _dtype: DummyDevice)

    detected = asyncio.run(
        manager.identify_device(
            "lamp_lifter",
            [{"entity_id": "cover.lamp_lift", "value": "open"}],
            [{"name": "lift"}],
        )
    )

    assert detected.deviceType == "Lift"


def test_resolve_actuator_prefers_cover_over_number():
    lift = _make_lift(
        entities=[
            {"entity_id": "number.lift_position"},
            {"entity_id": "cover.lift_lift"},
        ]
    )
    assert lift._resolve_actuator() == ("cover", "cover.lift_lift")


def test_resolve_actuator_falls_back_to_number():
    lift = _make_lift(entities=[{"entity_id": "number.lift_position"}])
    assert lift._resolve_actuator() == ("number", "number.lift_position")


def test_resolve_actuator_without_entity():
    lift = _make_lift(entities=[])
    assert lift._resolve_actuator() == (None, None)


def test_read_current_position_from_cover_attribute():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}],
        hass_states={"cover.lift_lift": FakeState("open", {"current_position": 30})},
    )
    assert lift.position == 30.0


def test_read_current_position_unknown_without_attribute():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}],
        hass_states={"cover.lift_lift": FakeState("open", {})},
    )
    assert lift.position is None


def test_position_config_loaded_from_device_min_max():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}],
        minmax={"minPosition": 20, "maxPosition": 80, "positionStep": 15},
    )
    assert lift.minPosition == 20.0
    assert lift.maxPosition == 80.0
    assert lift.positionStep == 15.0


def test_position_config_falls_back_to_duty_keys():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}],
        minmax={"minDuty": 10, "maxDuty": 90},
    )
    assert lift.minPosition == 10.0
    assert lift.maxPosition == 90.0


@pytest.mark.asyncio
async def test_increase_lift_raises_position_and_calls_cover():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}], position=50.0
    )
    await lift.increaseAction()
    assert lift.position == 55.0
    call = lift.hass.services.calls[-1]
    assert call["domain"] == "cover"
    assert call["service"] == "set_cover_position"
    assert call["service_data"]["position"] == 55


@pytest.mark.asyncio
async def test_reduce_lift_lowers_position_and_calls_cover():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}], position=50.0
    )
    await lift.reduceAction()
    assert lift.position == 45.0
    assert lift.hass.services.calls[-1]["service_data"]["position"] == 45


@pytest.mark.asyncio
async def test_increase_lift_clamps_at_maximum():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}],
        minmax={"minPosition": 0, "maxPosition": 90},
        position=88.0,
    )
    await lift.increaseAction()
    assert lift.position == 90.0


@pytest.mark.asyncio
async def test_reduce_lift_clamps_at_minimum():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}],
        minmax={"minPosition": 20, "maxPosition": 100},
        position=22.0,
    )
    await lift.reduceAction()
    assert lift.position == 20.0


@pytest.mark.asyncio
async def test_number_fallback_used_when_no_cover():
    lift = _make_lift(
        entities=[{"entity_id": "number.lift_position"}], position=50.0
    )
    await lift.increaseAction()
    call = lift.hass.services.calls[-1]
    assert call["domain"] == "number"
    assert call["service"] == "set_value"
    assert call["service_data"]["value"] == 55.0


@pytest.mark.asyncio
async def test_set_position_without_actuator_does_not_call_services():
    lift = _make_lift(entities=[], position=50.0)
    await lift.set_position(60.0)
    assert lift.hass.services.calls == []
    assert lift.position == 50.0


@pytest.mark.asyncio
async def test_set_position_skips_when_already_there():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}], position=50.0
    )
    await lift.set_position(50.0)
    assert lift.hass.services.calls == []


@pytest.mark.asyncio
async def test_set_position_action_reads_value_from_event_data():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}], position=50.0
    )
    await lift.setPositionAction({"value": 70})
    assert lift.position == 70.0
    assert lift.hass.services.calls[-1]["service_data"]["position"] == 70


@pytest.mark.asyncio
async def test_turn_on_raises_and_turn_off_lowers():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}], position=50.0
    )
    await lift.turn_on()
    assert lift.position == 55.0
    await lift.turn_off()
    assert lift.position == 50.0


def test_closed_loop_config_loaded_from_device_min_max():
    lift = _make_lift(
        entities=[{"entity_id": "cover.lift_lift"}],
        minmax={
            "settleTimeSec": 3,
            "pulseDurationSec": 0.5,
            "toleranceCM": 2,
            "minDistanceCM": 7,
            "maxDistanceCM": 150,
            "fullTravelTimeSec": 20,
            "maxRuntimeSec": 15,
            "maxIterations": 4,
            "noProgressLimit": 5,
            "sensorMaxAgeSec": 60,
        },
    )
    assert lift.settleTimeSec == 3.0
    assert lift.pulseDurationSec == 0.5
    assert lift.toleranceCM == 2.0
    assert lift.minDistanceCM == 7.0
    assert lift.maxDistanceCM == 150.0
    assert lift.fullTravelTimeSec == 20.0
    assert lift.maxRuntimeSec == 15.0
    assert lift.maxIterations == 4
    assert lift.noProgressLimit == 5
    assert lift.sensorMaxAgeSec == 60.0


def test_read_plant_distance_converts_meters_to_cm():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=50.0)
    lift.dataStore.setDeep("tentData.plantDistance", 0.4)
    lift.dataStore.setDeep("tentData.plantDistanceUnit", "m")
    assert lift._read_plant_distance_cm() == 40.0


def test_read_plant_distance_without_value_returns_none():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=50.0)
    assert lift._read_plant_distance_cm() is None


@pytest.mark.asyncio
async def test_reduce_refused_when_at_minimum_distance():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=50.0)
    lift.minDistanceCM = 10.0
    lift.dataStore.setDeep("tentData.plantDistance", 8)
    lift.dataStore.setDeep("tentData.plantDistanceUnit", "cm")
    await lift.reduceAction()
    assert lift.hass.services.calls == []
    assert lift.position == 50.0


@pytest.mark.asyncio
async def test_move_to_distance_steps_until_target_reached():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=80.0)
    lift.settleTimeSec = 0
    readings = iter([60.0, 60.0, 50.0, 40.0])
    lift._read_plant_distance_cm = lambda: next(readings)

    await lift.move_to_distance(40)

    positions = [c["service_data"]["position"] for c in lift.hass.services.calls]
    assert positions == [75, 70]
    assert lift.position == 70.0


@pytest.mark.asyncio
async def test_move_to_distance_clamps_target_to_minimum_distance():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=80.0)
    lift.settleTimeSec = 0
    lift.minDistanceCM = 10.0
    readings = iter([30.0, 30.0, 20.0, 10.0])
    lift._read_plant_distance_cm = lambda: next(readings)

    await lift.move_to_distance(5)

    positions = [c["service_data"]["position"] for c in lift.hass.services.calls]
    assert positions == [75, 70]


@pytest.mark.asyncio
async def test_move_to_distance_respects_max_iterations():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=80.0)
    lift.settleTimeSec = 0
    lift.maxIterations = 3
    lift._read_plant_distance_cm = lambda: 60.0

    await lift.move_to_distance(40)

    assert len(lift.hass.services.calls) == 3


@pytest.mark.asyncio
async def test_move_to_distance_without_sensor_does_nothing():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=80.0)
    await lift.move_to_distance(40)
    assert lift.hass.services.calls == []


@pytest.mark.asyncio
async def test_relay_pulse_used_when_no_cover_or_number():
    lift = _make_lift(
        entities=[
            {"entity_id": "switch.lift_up"},
            {"entity_id": "switch.lift_down"},
        ],
        position=None,
    )
    lift.settleTimeSec = 0
    lift.pulseDurationSec = 0
    readings = iter([60.0, 60.0, 50.0])
    lift._read_plant_distance_cm = lambda: next(readings)

    await lift.move_to_distance(50)

    calls = lift.hass.services.calls
    assert calls[0]["domain"] == "switch"
    assert calls[0]["service"] == "turn_on"
    assert calls[0]["service_data"]["entity_id"] == "switch.lift_down"
    assert any(c["service"] == "turn_off" for c in calls)


@pytest.mark.asyncio
async def test_set_position_action_with_distance_uses_closed_loop():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=80.0)
    lift.settleTimeSec = 0
    readings = iter([60.0, 60.0, 50.0])
    lift._read_plant_distance_cm = lambda: next(readings)

    await lift.setPositionAction({"distance": 50})

    positions = [c["service_data"]["position"] for c in lift.hass.services.calls]
    assert positions == [75]


def test_clamp_distance_target_respects_bounds():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=50.0)
    lift.minDistanceCM = 10.0
    lift.maxDistanceCM = 100.0
    assert lift._clamp_distance_target(5) == 10.0
    assert lift._clamp_distance_target(200) == 100.0
    assert lift._clamp_distance_target(60) == 60.0


@pytest.mark.asyncio
async def test_increase_refused_at_max_distance():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=50.0)
    lift.maxDistanceCM = 100.0
    lift.dataStore.setDeep("tentData.plantDistance", 120)
    lift.dataStore.setDeep("tentData.plantDistanceUnit", "cm")

    await lift.increaseAction()

    assert lift.hass.services.calls == []
    assert lift.position == 50.0


@pytest.mark.asyncio
async def test_increase_allowed_without_sensor_reading():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=50.0)
    lift.maxDistanceCM = 100.0

    await lift.increaseAction()

    assert lift.position == 55.0
    assert lift.hass.services.calls[-1]["service_data"]["position"] == 55


@pytest.mark.asyncio
async def test_move_to_distance_clamps_target_to_max_distance():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=80.0)
    lift.settleTimeSec = 0
    lift.maxDistanceCM = 100.0
    readings = iter([95.0, 95.0, 100.0])
    lift._read_plant_distance_cm = lambda: next(readings)

    await lift.move_to_distance(200)

    positions = [c["service_data"]["position"] for c in lift.hass.services.calls]
    assert positions == [85]


@pytest.mark.asyncio
async def test_move_to_distance_stops_on_no_progress():
    lift = _make_lift(entities=[{"entity_id": "cover.lift_lift"}], position=80.0)
    lift.settleTimeSec = 0
    lift.noProgressLimit = 3
    lift._read_plant_distance_cm = lambda: 60.0

    await lift.move_to_distance(40)

    assert len(lift.hass.services.calls) == 3



"""Tests for the plant/lamp distance sensor (plantdist / ultrasonic)."""

from __future__ import annotations

import asyncio

from custom_components.opengrowbox.OGBController.OGBDevices.Sensor import Sensor
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
    extract_context_from_entity,
    get_sensor_config,
)
from custom_components.opengrowbox.OGBController.utils.sensor_identification import (
    resolve_sensor_types,
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


class FakeHass:
    def __init__(self, states=None):
        self.states = FakeStates(states)


def test_resolve_plant_distance_by_label():
    assert resolve_sensor_types("sensor.tent_probe", [{"name": "plantdist"}]) == [
        "plant_distance"
    ]
    assert resolve_sensor_types("sensor.tent_probe", [{"name": "ultrasonic"}]) == [
        "plant_distance"
    ]
    assert resolve_sensor_types("sensor.tent_probe", [{"name": "distance"}]) == [
        "plant_distance"
    ]


def test_resolve_plant_distance_by_entity_name():
    assert resolve_sensor_types("sensor.tent_ultrasonic")[0] == "plant_distance"
    assert resolve_sensor_types("sensor.plantdist")[0] == "plant_distance"
    assert resolve_sensor_types("sensor.lamp_distance")[0] == "plant_distance"
    assert resolve_sensor_types("sensor.planttolight")[0] == "plant_distance"
    assert resolve_sensor_types("sensor.plantdistancetolight")[0] == "plant_distance"
    assert resolve_sensor_types("sensor.plant_distance_to_light")[0] == "plant_distance"


def test_reservoir_sensors_stay_water_level():
    assert resolve_sensor_types("sensor.reservoir_ultrasonic")[0] == "water_level"
    assert resolve_sensor_types("sensor.water_reservoir_distance")[0] == "water_level"


def test_plant_distance_context_is_other():
    assert extract_context_from_entity("sensor.tent_ultrasonic", "plant_distance") == "other"
    assert extract_context_from_entity("sensor.plantdist", "plant_distance") == "other"


def test_plant_distance_sensor_config():
    config = get_sensor_config("plant_distance", "other")
    assert config is not None
    assert config["unit"] == "cm"
    assert config["device_class"] == "distance"
    assert config["max_value"] >= 100


def test_plantdist_label_identifies_sensor_device(monkeypatch):
    """A device carrying the plantdist label must be recognized as a Sensor."""
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    manager.event_manager = object()
    manager.data_store = object()
    manager.room = "TentRoom"
    manager.hass = None
    monkeypatch.setattr(manager, "get_device_class", lambda _dtype: DummyDevice)

    detected = asyncio.run(
        manager.identify_device(
            "plant_distance_sensor",
            [{"entity_id": "sensor.plantdist", "value": "42"}],
            [{"name": "plantdist"}],
        )
    )

    assert detected.deviceType == "Sensor"


def _sensor_stub(entity_id, ha_states=None):
    sensor = Sensor.__new__(Sensor)
    sensor.room = "TentRoom"
    sensor.deviceName = "Tent Sensor"
    sensor.data_store = FakeDataStore({})
    sensor.event_manager = FakeEventManager()
    sensor.hass = FakeHass(ha_states or {})
    return sensor


def test_store_plant_distance_writes_datastore_and_emits():
    entity_id = "sensor.tent_ultrasonic"
    sensor = _sensor_stub(
        entity_id,
        {entity_id: FakeState("42.5", {"unit_of_measurement": "cm"})},
    )

    asyncio.run(sensor._store_plant_distance(entity_id, 42.5, {"unit": "cm"}))

    assert sensor.data_store.getDeep("tentData.plantDistance") == 42.5
    assert sensor.data_store.getDeep("tentData.plantDistanceUnit") == "cm"
    assert sensor.data_store.getDeep("tentData.plantDistanceLastUpdate") is not None

    events = [e for e in sensor.event_manager.emitted if e["event_name"] == "PlantDistanceUpdate"]
    assert len(events) == 1
    assert events[0]["data"]["value"] == 42.5
    assert events[0]["data"]["unit"] == "cm"
    assert events[0]["data"]["room"] == "TentRoom"


def test_store_plant_distance_uses_config_unit_without_hass():
    entity_id = "sensor.plantdist"
    sensor = _sensor_stub(entity_id, {})

    asyncio.run(sensor._store_plant_distance(entity_id, 30.0, {"unit": "cm"}))

    assert sensor.data_store.getDeep("tentData.plantDistanceUnit") == "cm"

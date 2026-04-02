from custom_components.opengrowbox.OGBController.utils.sensor_identification import (
    resolve_remappable_sensor_type,
    resolve_sensor_types,
)


def test_resolve_sensor_types_prefers_entity_suffix_over_labels():
    labels = [{"name": "ventilation"}, {"name": "camera"}]
    resolved = resolve_sensor_types("sensor.room_temperature", labels)
    assert resolved == ["temperature"]


def test_resolve_sensor_types_from_multilingual_labels():
    # German translation for humidity
    labels = [{"name": "Luftfeuchtigkeit"}]
    resolved = resolve_sensor_types("sensor.ambient_probe", labels)
    assert "humidity" in resolved


def test_resolve_sensor_types_from_entity_tokens_when_no_labels():
    resolved = resolve_sensor_types("sensor.main_tent_relative_humidity")
    assert resolved
    assert resolved[0] == "humidity"


def test_resolve_remappable_sensor_type_keeps_legacy_suffix_behavior():
    assert resolve_remappable_sensor_type("sensor.box_dew_point") == "dewpoint"
    assert resolve_remappable_sensor_type("sensor.box_co2") == "co2"
    assert resolve_remappable_sensor_type("sensor.box_temperature") == "temperature"


def test_resolve_remappable_sensor_type_returns_none_for_non_remappable():
    assert resolve_remappable_sensor_type("sensor.box_pressure") is None

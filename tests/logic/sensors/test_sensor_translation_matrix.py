import pytest

from custom_components.opengrowbox.OGBController.utils.sensor_identification import (
    resolve_sensor_types,
)


@pytest.mark.parametrize(
    "label,expected",
    [
        ("temperature", "temperature"),
        ("humidity", "humidity"),
        ("dewpoint", "dewpoint"),
        ("moisture", "moisture"),
        ("light", "light"),
        ("ppfd", "ppfd"),
        ("dli", "dli"),
        ("ec", "ec"),
        ("ph", "ph"),
        ("co2", "co2"),
        ("tds", "tds"),
        ("oxidation", "oxidation"),
        ("salinity", "salinity"),
        ("pressure", "pressure"),
        ("vpd", "vpd"),
        ("battery", "battery"),
        ("weight", "weight"),
        ("water_level", "water_level"),
        ("flow", "flow"),
        ("water_temperature", "water_temperature"),
        ("voltage", "voltage"),
        ("current", "current"),
        ("power", "power"),
        ("energy", "energy"),
    ],
)
def test_sensor_translation_matrix(label, expected):
    resolved = resolve_sensor_types("sensor.any_probe", [{"name": label}])
    assert resolved
    assert resolved[0] == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("temperatur", "temperature"),
        ("luftfeuchtigkeit", "humidity"),
        ("feuchte", "humidity"),
        ("co2", "co2"),
    ],
)
def test_sensor_translation_multilingual_subset(label, expected):
    resolved = resolve_sensor_types("sensor.any_probe", [{"name": label}])
    assert resolved
    assert expected in resolved

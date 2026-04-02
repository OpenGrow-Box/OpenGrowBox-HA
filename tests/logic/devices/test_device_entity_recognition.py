import asyncio

import pytest

from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
    RELEVANT_PREFIXES,
)
from custom_components.opengrowbox.OGBController.managers.OGBDeviceManager import (
    OGBDeviceManager,
)


class DummyDevice:
    def __init__(
        self,
        device_name,
        device_data,
        event_manager,
        data_store,
        detected_type,
        room,
        hass,
        detected_label,
        device_labels,
    ):
        self.deviceName = device_name
        self.deviceType = detected_type
        self.deviceLabel = detected_label
        self.deviceLabels = device_labels


def test_relevant_prefixes_include_window_and_door_entities():
    assert "cover." in RELEVANT_PREFIXES
    assert "binary_sensor." in RELEVANT_PREFIXES


def test_determine_device_type_from_labels_for_new_types():
    manager = OGBDeviceManager.__new__(OGBDeviceManager)

    assert manager._determine_device_type_from_labels([{"name": "window"}]) == "Window"
    assert manager._determine_device_type_from_labels([{"name": "fenster"}]) == "Window"
    assert manager._determine_device_type_from_labels([{"name": "door"}]) == "Door"


def test_determine_device_type_from_labels_for_special_lights():
    manager = OGBDeviceManager.__new__(OGBDeviceManager)

    assert manager._determine_device_type_from_labels([{"name": "light_uv"}]) == "LightUV"
    assert manager._determine_device_type_from_labels([{"name": "light_blue"}]) == "LightBlue"
    assert manager._determine_device_type_from_labels([{"name": "light_red"}]) == "LightRed"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("light_uv", "LightUV"),
        ("uv-light", "LightUV"),
        ("light_blue", "LightBlue"),
        ("blue_led", "LightBlue"),
        ("light_red", "LightRed"),
        ("red_led", "LightRed"),
        ("window", "Window"),
        ("fenster", "Window"),
        ("door", "Door"),
        ("contact", "Door"),
    ],
)
def test_determine_device_type_from_labels_parametrized(label, expected):
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    assert manager._determine_device_type_from_labels([{"name": label}]) == expected


def test_normalize_device_label_for_compare_stable_mapping():
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    assert manager._normalize_device_label_for_compare("light_uv") == "LightUV"
    assert manager._normalize_device_label_for_compare("uv-light") == "LightUV"
    assert manager._normalize_device_label_for_compare("blue_led") == "LightBlue"
    assert manager._normalize_device_label_for_compare("red_led") == "LightRed"
    assert manager._normalize_device_label_for_compare("window") == "Window"
    assert manager._normalize_device_label_for_compare("unknown_custom_label") == "EMPTY"


def test_identify_device_prefers_fridgegrow_label(monkeypatch):
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    manager.event_manager = object()
    manager.data_store = object()
    manager.room = "dev_room"
    manager.hass = None
    monkeypatch.setattr(manager, "get_device_class", lambda _dtype: DummyDevice)

    detected = asyncio.run(
        manager.identify_device(
            "my_device",
            [{"entity_id": "switch.any", "value": "off"}],
            [{"name": "fridgegrow"}, {"name": "light_uv"}],
        )
    )

    assert detected.deviceType == "FridgeGrow"


def test_identify_device_name_fallback_window(monkeypatch):
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    manager.event_manager = object()
    manager.data_store = object()
    manager.room = "dev_room"
    manager.hass = None
    monkeypatch.setattr(manager, "get_device_class", lambda _dtype: DummyDevice)

    detected = asyncio.run(
        manager.identify_device(
            "greenhouse_window_motor",
            [{"entity_id": "cover.greenhouse_window", "value": "closed"}],
            [],
        )
    )

    assert detected.deviceType == "Window"

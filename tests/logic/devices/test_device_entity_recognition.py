import asyncio

import pytest

from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
    RELEVANT_PREFIXES,
    DEVICE_TYPE_MAPPING,
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


# ── Camera device identification tests ──────────────────────────────────────


@pytest.mark.parametrize(
    "label_name,expected",
    [
        ("camera", "Camera"),
        ("cam", "Camera"),
        ("webcam", "Camera"),
        ("ipcam", "Camera"),
        ("kamera", "Camera"),
        ("video", "Camera"),
        ("surveillance", "Camera"),
    ],
)
def test_identify_device_camera_by_label(label_name, expected, monkeypatch):
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    manager.event_manager = object()
    manager.data_store = object()
    manager.room = "dev_room"
    manager.hass = None
    monkeypatch.setattr(manager, "get_device_class", lambda _dtype: DummyDevice)

    detected = asyncio.run(
        manager.identify_device(
            "growcam",
            [{"entity_id": "switch.any", "value": "off"}],
            [{"name": label_name}],
        )
    )
    assert detected.deviceType == expected


def test_identify_device_camera_by_name_fallback(monkeypatch):
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    manager.event_manager = object()
    manager.data_store = object()
    manager.room = "dev_room"
    manager.hass = None
    monkeypatch.setattr(manager, "get_device_class", lambda _dtype: DummyDevice)

    detected = asyncio.run(
        manager.identify_device(
            "ipcam_grow",
            [{"entity_id": "switch.any", "value": "off"}],
            [],
        )
    )
    assert detected.deviceType == "Camera"


def test_identify_device_camera_by_label_name_not_just_id(monkeypatch):
    """Label name 'Camera' (capitalized) should match even if label id differs."""
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    manager.event_manager = object()
    manager.data_store = object()
    manager.room = "dev_room"
    manager.hass = None
    monkeypatch.setattr(manager, "get_device_class", lambda _dtype: DummyDevice)

    detected = asyncio.run(
        manager.identify_device(
            "growcam",
            [{"entity_id": "switch.any", "value": "off"}],
            [{"id": "my_custom_cam_label", "name": "Camera"}],
        )
    )
    assert detected.deviceType == "Camera"


def test_determine_device_type_from_labels_camera():
    manager = OGBDeviceManager.__new__(OGBDeviceManager)
    assert manager._determine_device_type_from_labels([{"name": "camera"}]) == "Camera"
    assert manager._determine_device_type_from_labels([{"name": "cam"}]) == "Camera"
    assert manager._determine_device_type_from_labels([{"name": "webcam"}]) == "Camera"
    assert manager._determine_device_type_from_labels([{"name": "ipcam"}]) == "Camera"
    assert manager._determine_device_type_from_labels([{"name": "kamera"}]) == "Camera"


# ── Camera deviceInit label detection tests ──────────────────────────────────


def _make_camera_mock(**overrides):
    """Build a minimal Camera mock for deviceInit testing."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Camera import Camera

    mock_event_manager = type("EM", (), {"on": lambda *a: None, "emit": lambda *a: None})()
    mock_data_store = type("DS", (), {
        "get": lambda self, k: None,
        "set": lambda self, k, v: None,
    })()

    cam = Camera.__new__(Camera)
    cam.deviceName = overrides.get("deviceName", "growcam")
    cam.deviceType = overrides.get("deviceType", "Camera")
    cam.inRoom = overrides.get("inRoom", "test_room")
    cam.hass = overrides.get("hass", None)
    cam.eventManager = mock_event_manager
    cam.event_manager = mock_event_manager
    cam.dataStore = mock_data_store
    cam.camera_entity_id = overrides.get("camera_entity_id", "camera.growcam")
    cam.options = []
    cam.initialization = False
    cam.isInitialized = False
    cam.labelMap = overrides.get("labelMap", [])
    cam.switches = []
    cam.deviceLabel = "EMPTY"
    cam.ogbsettings = []
    cam.sensors = []
    cam.isRunning = False
    cam.isDimmable = False
    cam.voltage = None
    cam.dutyCycle = None
    cam.minDuty = None
    cam.maxDuty = None
    cam.is_minmax_active = False
    cam.voltageFromNumber = False
    cam.isSpecialDevice = False
    cam.isAcInfinDev = False
    cam.inWorkMode = False
    cam._is_entity_enabled = lambda eid: True
    cam._get_dim_value = lambda: 0
    cam.identifyCapabilities = lambda: None

    return cam


def test_camera_deviceinit_prefers_camera_entity_over_label():
    """camera.* entity should always be preferred over label-only match."""
    cam = _make_camera_mock()

    entities = [
        {
            "entity_id": "switch.growcam_power",
            "value": "on",
            "labels": [{"id": "camera", "name": "Camera", "scope": "entity"}],
        },
        {
            "entity_id": "camera.growcam",
            "value": "idle",
            "labels": [],
        },
    ]

    cam.deviceInit(entities)

    assert cam.camera_entity_id == "camera.growcam"
    assert len(cam.options) == 1
    assert cam.options[0]["entity_id"] == "camera.growcam"


def test_camera_deviceinit_label_only_detection():
    """When no camera.* entity exists, label-matched entity should be used."""
    cam = _make_camera_mock()

    entities = [
        {
            "entity_id": "switch.growcam_power",
            "value": "on",
            "labels": [{"id": "camera", "name": "Camera", "scope": "entity"}],
        },
    ]

    cam.deviceInit(entities)

    assert cam.camera_entity_id == "switch.growcam_power"
    assert len(cam.options) == 1


def test_camera_deviceinit_label_name_detection():
    """Label name 'Camera' should match via name."""
    cam = _make_camera_mock()

    entities = [
        {
            "entity_id": "image.growcam",
            "value": "ok",
            "labels": [{"id": "my_custom_label", "name": "Camera", "scope": "entity"}],
        },
    ]

    cam.deviceInit(entities)

    assert cam.camera_entity_id == "image.growcam"
    assert len(cam.options) == 1


def test_camera_deviceinit_no_match():
    """Entity with no camera prefix and no camera label should NOT be matched."""
    cam = _make_camera_mock()

    entities = [
        {
            "entity_id": "switch.growcam_power",
            "value": "on",
            "labels": [{"id": "temperature", "name": "Temperature", "scope": "entity"}],
        },
    ]

    cam.deviceInit(entities)

    assert cam.camera_entity_id == "camera.growcam"
    assert len(cam.options) == 0


# ── Camera _is_device_for_event tests ────────────────────────────────────────


def test_camera_is_device_for_event_standard():
    from custom_components.opengrowbox.OGBController.OGBDevices.Camera import Camera

    cam = Camera.__new__(Camera)
    cam.deviceName = "growcam"
    cam.camera_entity_id = "camera.growcam"
    cam.labelMap = []

    assert cam._is_device_for_event("growcam") is True
    assert cam._is_device_for_event("camera.growcam") is True
    assert cam._is_device_for_event("other_device") is False


def test_camera_is_device_for_event_label_fallback():
    from custom_components.opengrowbox.OGBController.OGBDevices.Camera import Camera

    cam = Camera.__new__(Camera)
    cam.deviceName = "growcam"
    cam.camera_entity_id = "switch.growcam_power"
    cam.labelMap = [{"id": "camera", "name": "Camera", "scope": "entity"}]

    assert cam._is_device_for_event("growcam") is True
    assert cam._is_device_for_event("switch.growcam_power") is True
    assert cam._is_device_for_event("camera") is True
    assert cam._is_device_for_event("other") is False

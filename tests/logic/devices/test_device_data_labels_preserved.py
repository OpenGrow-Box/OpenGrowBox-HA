"""Regression: capability deviceData must keep the labels field.

identifyCapabilities() writes deviceData["labels"], then deviceInit() calls
_update_deviceData_in_capabilities() which used to overwrite the dict without
labels -> _get_drippers() fell back to name matching even though the device
carried a 'dripper' label.
"""

from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import CAP_MAPPING
from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device

from tests.logic.helpers import FakeDataStore


def _device():
    device = Device.__new__(Device)
    device.deviceName = "pumpdripper"
    device.deviceType = "Pump"
    device.isRunning = False
    device.isDimmable = False
    device.dutyCycle = 0
    device.voltage = None
    device.minDuty = None
    device.maxDuty = None
    device.is_minmax_active = False
    device.labelMap = [{"id": "dripper", "name": "dripper"}]
    device.dataStore = FakeDataStore()
    return device


def test_device_data_labels_preserved_after_update():
    device = _device()

    currentCap = {
        "state": True,
        "count": 1,
        "devEntities": ["pumpdripper"],
        "deviceData": {"pumpdripper": {}},
    }
    device.dataStore.setDeep(
        f"capabilities.{CAP_MAPPING.get('Pump', 'canPump')}", currentCap
    )
    cap_path = f"capabilities.{CAP_MAPPING.get('Pump', 'canPump')}"

    device._update_deviceData_in_capabilities()

    device_data = device.dataStore.getDeep(f"{cap_path}.deviceData.pumpdripper")
    assert device_data["labels"] == ["dripper"]


def test_device_data_labels_empty_when_no_labels():
    device = _device()
    device.labelMap = []

    currentCap = {
        "state": True,
        "count": 1,
        "devEntities": ["pumpdripper"],
        "deviceData": {"pumpdripper": {}},
    }
    device.dataStore.setDeep(
        f"capabilities.{CAP_MAPPING.get('Pump', 'canPump')}", currentCap
    )
    cap_path = f"capabilities.{CAP_MAPPING.get('Pump', 'canPump')}"

    device._update_deviceData_in_capabilities()

    device_data = device.dataStore.getDeep(f"{cap_path}.deviceData.pumpdripper")
    assert device_data["labels"] == []
from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device

from tests.logic.helpers import FakeDataStore


def _device_stub(device_type="Ventilation"):
    device = Device.__new__(Device)
    device.deviceName = "dev_vent"
    device.deviceType = device_type
    device.isDimmable = True
    device.isInitialized = True
    device.dataStore = FakeDataStore(
        {
            "DeviceMinMax": {
                device_type: {
                    "active": True,
                    "minVoltage": 20,
                    "maxVoltage": 80,
                    "minDuty": 15,
                    "maxDuty": 70,
                }
            }
        }
    )
    device.minVoltage = 0
    device.maxVoltage = 100
    device.minDuty = 0
    device.maxDuty = 100
    device.voltage = 90
    device.dutyCycle = 10
    return device


def test_check_minmax_loads_and_clamps_values():
    device = _device_stub()
    device.checkMinMax("Init")

    assert device.is_minmax_active is True
    assert device.minVoltage == 20.0
    assert device.maxVoltage == 80.0
    assert device.minDuty == 15.0
    assert device.maxDuty == 70.0
    assert device.voltage == 80.0
    assert device.dutyCycle == 15


def test_clamp_voltage_and_duty_cycle_helpers():
    device = _device_stub()
    device.minVoltage = 30
    device.maxVoltage = 70
    device.minDuty = 10
    device.maxDuty = 60

    assert device.clamp_voltage(0) == 30.0
    assert device.clamp_voltage(99) == 70.0
    assert device.clamp_voltage(50) == 50.0

    assert device.clamp_duty_cycle(0) == 10
    assert device.clamp_duty_cycle(99) == 60
    assert device.clamp_duty_cycle(33) == 33


def test_is_tent_mode_disabled_reads_datastore():
    device = _device_stub()
    device.dataStore.setDeep("tentMode", "Disabled")
    assert device.is_tent_mode_disabled() is True

    device.dataStore.setDeep("tentMode", "VPD Perfection")
    assert device.is_tent_mode_disabled() is False

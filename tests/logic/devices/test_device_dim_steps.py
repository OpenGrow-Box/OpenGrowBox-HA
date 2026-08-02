from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device

from tests.logic.helpers import FakeDataStore


def _device_stub(device_type="Ventilation", is_ac_infinity=False, initial_steps=5):
    device = Device.__new__(Device)
    device.deviceName = "dev_test"
    device.deviceType = device_type
    device.isDimmable = True
    device.isAcInfinDev = is_ac_infinity
    device.isInitialized = True
    device.dataStore = FakeDataStore(
        {
            "DeviceMinMax": {
                device_type: {
                    "active": True,
                    "minVoltage": 0,
                    "maxVoltage": 100,
                    "minDuty": 0,
                    "maxDuty": 100,
                }
            },
            "DeviceSteps": {
                device_type: initial_steps,
            },
        }
    )
    device.minVoltage = 0
    device.maxVoltage = 100
    device.minDuty = 0
    device.maxDuty = 100
    device.voltage = 50
    device.dutyCycle = 50
    device.steps = 5
    return device


def test_dim_step_loaded_from_datastore():
    device = _device_stub(device_type="Exhaust", initial_steps=3)
    assert device.steps == 5

    device._load_dim_step()

    assert device.steps == 3


def test_ac_infinity_keeps_fixed_ten_percent_step():
    device = _device_stub(device_type="Exhaust", is_ac_infinity=True, initial_steps=7)
    device.steps = 10

    device._load_dim_step()

    assert device.steps == 10


def test_out_of_range_dim_step_is_ignored():
    device = _device_stub(device_type="Exhaust", initial_steps=15)
    assert device.steps == 5

    device._load_dim_step()

    assert device.steps == 5

from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device


def _device_stub():
    device = Device.__new__(Device)
    device.deviceName = "dev"
    device.deviceType = "Exhaust"
    device.deviceLabel = "Abluft"
    device.inRoom = "Ambient"
    device.switches = []
    device.options = [{"entity_id": "select.dev_mode", "platform": "tasmota"}]
    device.sensors = [{"entity_id": "sensor.dev_pm25", "platform": "tasmota"}]
    device.ogbsettings = [{"entity_id": "number.ogb_feed_nutrient_a_ambient", "platform": "opengrowbox"}]
    device.skipped_entities = [{"entity_id": "switch.dev_relay", "platform": "tasmota"}]
    device.isAcInfinDev = False
    device.isSpecialDevice = True
    device.isDimmable = False
    device.voltageFromNumber = False
    device.voltage = None
    device.dutyCycle = 50
    device.isRunning = True
    device.isInitialized = True
    return device


def test_diagnostic_message_exposes_type_platform_and_dropped_entities():
    """A switch-less device must reveal type, platform and the reason.

    The short-form name 'dev' (from a 'dev_*' entity prefix) gives no clue
    about the device, so the log has to carry the identifying details.
    """
    message = _device_stub()._describe_missing_switches()

    assert "has no switch entity" in message
    assert "device='dev'" in message
    assert "type='Exhaust'" in message
    assert "room='Ambient'" in message
    # ogbsettings are OGB's own config entities, so they must not pollute the
    # platform detection of the physical device
    assert "platforms=['tasmota']" in message
    assert "flags=special" in message
    assert "skipped_invalid_value=['switch.dev_relay']" in message


def test_diagnostic_message_handles_device_without_any_entity():
    device = _device_stub()
    device.options = []
    device.sensors = []
    device.ogbsettings = []
    device.skipped_entities = []
    device.isSpecialDevice = False

    message = device._describe_missing_switches()

    assert "platforms=['unknown']" in message
    assert "flags=none" in message
    assert "skipped_invalid_value=[]" in message
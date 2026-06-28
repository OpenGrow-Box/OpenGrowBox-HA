import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBMedium import (
    GrowMedium,
    MediumType,
)
from custom_components.opengrowbox.OGBController.managers.OGBConsoleManager import (
    OGBConsoleManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


class FakeBus:
    def __init__(self):
        self.last_fired = None

    def async_listen(self, event_type, handler):
        pass

    def async_fire(self, event_type, data):
        self.last_fired = {"event_type": event_type, "data": data}


class FakeHass:
    def __init__(self):
        self.bus = FakeBus()


class CapturingEventManager(FakeEventManager):
    pass


def _last_response(hass: FakeHass):
    if hass.bus.last_fired and hass.bus.last_fired["event_type"] == "ogb_console_response":
        return hass.bus.last_fired["data"].get("message")
    return None


def _console(data):
    event_manager = CapturingEventManager()
    hass = FakeHass()
    console = OGBConsoleManager(hass, FakeDataStore(data), event_manager, "test_room")
    # Prevent background init task from running in tests
    console.is_initialized = True
    return console, hass


@pytest.mark.asyncio
async def test_medium_sensors_fallback_cropsteering():
    console, hass = _console({
        "growMediums": [],
        "CropSteering": {
            "vwc_current": 45.5,
            "ec_current": 1.23,
            "weight_current": 12.34,
        },
    })

    await console.cmd_medium_sensors([])
    response = _last_response(hass)
    assert "45.5%" in response
    assert "1.23" in response
    assert "12.34" in response


@pytest.mark.asyncio
async def test_medium_sensors_no_data():
    console, hass = _console({"growMediums": [], "CropSteering": {}})

    await console.cmd_medium_sensors([])
    response = _last_response(hass)
    assert "No grow mediums" in response


@pytest.mark.asyncio
async def test_medium_sensors_with_medium_objects():
    medium = GrowMedium(
        eventManager=CapturingEventManager(),
        dataStore=FakeDataStore({}),
        room="test_room",
        medium_type=MediumType.COCO,
        name="coco_1",
    )
    medium.current_moisture = 55.0
    medium.current_ec = 1.5
    medium.current_ph = 6.0
    medium.current_temp = 22.5

    console, hass = _console({"growMediums": [medium]})

    await console.cmd_medium_sensors([])
    response = _last_response(hass)
    assert "COCO" in response
    assert "55.0%" in response
    assert "1.500" in response
    assert "6.00" in response
    assert "22.5" in response


@pytest.mark.asyncio
async def test_medium_sensors_filter_by_name():
    medium1 = GrowMedium(
        eventManager=CapturingEventManager(),
        dataStore=FakeDataStore({}),
        room="test_room",
        medium_type=MediumType.COCO,
        name="coco_1",
    )
    medium1.current_moisture = 55.0

    medium2 = GrowMedium(
        eventManager=CapturingEventManager(),
        dataStore=FakeDataStore({}),
        room="test_room",
        medium_type=MediumType.SOIL,
        name="soil_1",
    )
    medium2.current_moisture = 30.0

    console, hass = _console({"growMediums": [medium1, medium2]})

    await console.cmd_medium_sensors(["soil_1"])
    response = _last_response(hass)
    assert "SOIL" in response
    assert "30.0%" in response
    assert "55.0%" not in response


@pytest.mark.asyncio
async def test_medium_sensors_unknown_name():
    medium = GrowMedium(
        eventManager=CapturingEventManager(),
        dataStore=FakeDataStore({}),
        room="test_room",
        medium_type=MediumType.COCO,
        name="coco_1",
    )
    console, hass = _console({"growMediums": [medium]})

    await console.cmd_medium_sensors(["unknown"])
    response = _last_response(hass)
    assert "not found" in response
    assert "coco_1" in response

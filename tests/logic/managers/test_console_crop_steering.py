import pytest

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
    console.is_initialized = True
    return console, hass


# --- cs_soak ---


@pytest.mark.asyncio
async def test_cs_soak_arms_and_shows_status():
    console, hass = _console({})
    await console.cmd_cs_soak([])
    assert "disarmed" in _last_response(hass)

    await console.cmd_cs_soak(["on"])
    assert console.data_store.getDeep("CropSteering.InitialSoak") is True
    assert "ARMED" in _last_response(hass)

    await console.cmd_cs_soak(["status"])
    assert "ARMED" in _last_response(hass)


@pytest.mark.asyncio
async def test_cs_soak_disarms():
    console, hass = _console({"CropSteering": {"InitialSoak": True}})
    await console.cmd_cs_soak(["off"])
    assert console.data_store.getDeep("CropSteering.InitialSoak") is False


@pytest.mark.asyncio
async def test_cs_soak_rejects_unknown_action():
    console, hass = _console({})
    await console.cmd_cs_soak(["garbage"])
    assert "Unknown action" in _last_response(hass)
    assert console.data_store.getDeep("CropSteering.InitialSoak") is None


# --- cs_p2 ---


@pytest.mark.asyncio
async def test_cs_p2_status_defaults():
    console, hass = _console({})
    await console.cmd_cs_p2([])
    response = _last_response(hass)
    assert "auto" in response
    assert "25.0%" in response
    assert "Introduced: no" in response


@pytest.mark.asyncio
async def test_cs_p2_mode_sets_and_validates():
    console, hass = _console({})
    await console.cmd_cs_p2(["mode", "disabled"])
    assert console.data_store.getDeep("CropSteering.P2_Introduction") == "disabled"

    await console.cmd_cs_p2(["mode", "ENABLED"])
    assert console.data_store.getDeep("CropSteering.P2_Introduction") == "enabled"

    await console.cmd_cs_p2(["mode", "garbage"])
    assert "Invalid mode" in _last_response(hass)
    assert console.data_store.getDeep("CropSteering.P2_Introduction") == "enabled"


@pytest.mark.asyncio
async def test_cs_p2_threshold_sets_and_validates():
    console, hass = _console({})
    await console.cmd_cs_p2(["threshold", "30"])
    assert console.data_store.getDeep("CropSteering.P2_Intro_Dryback_Threshold") == 30.0

    await console.cmd_cs_p2(["threshold", "0"])
    assert "greater than 0" in _last_response(hass)

    await console.cmd_cs_p2(["threshold", "abc"])
    assert "Invalid threshold" in _last_response(hass)


@pytest.mark.asyncio
async def test_cs_p2_reset_clears_introduced():
    console, hass = _console({"CropSteering": {"p2_introduced": True}})
    await console.cmd_cs_p2(["reset"])
    assert console.data_store.getDeep("CropSteering.p2_introduced") is False


@pytest.mark.asyncio
async def test_cs_p2_status_reflects_in_use():
    console, hass = _console(
        {
            "CropSteering": {
                "P2_Introduction": "auto",
                "p2_introduced": True,
                "P2_Intro_Dryback_Threshold": 30.0,
                "day_peak_vwc": 68.5,
            }
        }
    )
    await console.cmd_cs_p2(["status"])
    response = _last_response(hass)
    assert "30.0%" in response
    assert "Introduced: yes" in response
    assert "P2 in use: yes" in response
    assert "68.5%" in response


@pytest.mark.asyncio
async def test_cs_p2_status_shows_current_dryback_vs_threshold():
    console, hass = _console(
        {
            "CropSteering": {
                "day_peak_vwc": 60.0,
                "vwc_current": 45.0,
            }
        }
    )
    await console.cmd_cs_p2(["status"])
    response = _last_response(hass)
    assert "Current dryback vs peak: 25.0%" in response
    assert "needs ≥ 25.0% in 'auto' mode" in response


@pytest.mark.asyncio
async def test_cs_p2_status_clamps_dryback():
    console, hass = _console(
        {
            "CropSteering": {
                "day_peak_vwc": 60.0,
                "vwc_current": 80.0,
            }
        }
    )
    await console.cmd_cs_p2(["status"])
    assert "Current dryback vs peak: 0.0%" in _last_response(hass)


@pytest.mark.asyncio
async def test_cs_p2_disabled_mode_reports_not_in_use():
    console, hass = _console({"CropSteering": {"P2_Introduction": "disabled"}})
    await console.cmd_cs_p2(["status"])
    assert "P2 in use: no" in _last_response(hass)


# --- cs_status includes steering settings ---


@pytest.mark.asyncio
async def test_cs_status_shows_steering_settings():
    console, hass = _console(
        {
            "CropSteering": {
                "InitialSoak": True,
                "P2_Introduction": "auto",
                "p2_introduced": True,
                "day_peak_vwc": 68.5,
                "vwc_current": 40.0,
                "Learned": {"p1_peak_vwc": 61.5, "next_ec_target": 2.3},
            }
        }
    )
    await console.cmd_cs_status([])
    response = _last_response(hass)
    assert "Steering Settings" in response
    assert "Initial Soak: ARMED" in response
    assert "P2 Mode: auto" in response
    assert "Learned P1 peak: 61.5%" in response
    assert "Next-day EC target: 2.30" in response
    assert "Current dryback vs peak: 41.6%" in response

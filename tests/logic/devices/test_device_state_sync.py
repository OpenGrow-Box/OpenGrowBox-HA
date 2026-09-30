"""State synchronisation between OGB's internal flags and Home Assistant.

Regression coverage for the case where a device command is lost on a flaky
(WiFi) integration: OGB kept isRunning=True while the device was actually off,
so increase/reduce skipped it as "already in desired state" until the user
intervened manually.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.opengrowbox.OGBController.OGBDevices.Dehumidifier import Dehumidifier
from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device
from custom_components.opengrowbox.OGBController.OGBDevices.Humidifier import Humidifier
from custom_components.opengrowbox.OGBController.OGBOrchestrator import OGBOrchestrator

from tests.logic.helpers import FakeDataStore

SWITCH = "switch.shelly_humidifier"


class FakeStates:
    def __init__(self, states: dict | None = None):
        self._states = states or {}

    def get(self, entity_id):
        value = self._states.get(entity_id)
        if value is None:
            return None
        return MagicMock(state=str(value))


def _device_stub(device_type="Humidifier", ha_state="off", is_running=True,
                 dimmable=False, duty=0, voltage=None):
    device = Device.__new__(Device)
    device.deviceName = "Shelly Humidifier"
    device.deviceType = device_type
    device.inRoom = "TestRoom"
    device.isDimmable = dimmable
    device.isAcInfinDev = False
    device.isSpecialDevice = False
    device.isInitialized = True
    device.isRunning = is_running
    device.dutyCycle = duty
    device.voltage = voltage
    device.switches = [{"entity_id": SWITCH, "value": None}]
    device.options = []
    device.sensors = []
    device.ogbsettings = []
    device.dataStore = FakeDataStore({"capabilities": {}})
    device.hass = MagicMock()
    device.hass.states = FakeStates({SWITCH: ha_state} if ha_state is not None else {})
    device._state_resync_pending = False
    device._control_lock_until = 0
    device._in_active_control = False
    return device


# ── get_actual_state ────────────────────────────────────────────────────────

def test_get_actual_state_reports_live_ha_state():
    assert _device_stub(ha_state="on").get_actual_state() == "on"
    assert _device_stub(ha_state="off").get_actual_state() == "off"


def test_get_actual_state_unknown_when_unavailable():
    for value in ("unavailable", "unknown"):
        assert _device_stub(ha_state=value).get_actual_state() is None


def test_get_actual_state_none_without_switches():
    device = _device_stub()
    device.switches = []
    assert device.get_actual_state() is None


def test_get_actual_state_treats_active_duty_value_as_on():
    """A dimmable device switched off but with a duty value is still running."""
    device = _device_stub(ha_state="off", dimmable=True, duty=40)
    assert device.get_actual_state() == "on"


def test_get_actual_state_prefers_explicit_on():
    device = _device_stub(ha_state="on", dimmable=True, duty=0)
    assert device.get_actual_state() == "on"


# ── is_already_in_state ─────────────────────────────────────────────────────

def test_is_already_in_state_ignores_stale_is_running():
    """The reported failure: isRunning says on, HA says off -> not already on."""
    device = _device_stub(ha_state="off", is_running=True)
    assert device.is_already_in_state("on") is False


def test_is_already_in_state_corrects_stale_is_running():
    device = _device_stub(ha_state="off", is_running=True)
    device.is_already_in_state("on")
    assert device.isRunning is False


def test_is_already_in_state_reports_on_for_on_device():
    device = _device_stub(ha_state="on", is_running=False)
    assert device.is_already_in_state("on") is True
    assert device.isRunning is True


def test_is_already_in_state_falls_back_to_is_running_when_unreachable():
    device = _device_stub(ha_state="unavailable", is_running=True)
    assert device.is_already_in_state("on") is True
    assert device.isRunning is True

    device = _device_stub(ha_state="unavailable", is_running=False)
    assert device.is_already_in_state("on") is False


# ── get_expected_state ──────────────────────────────────────────────────────

def test_get_expected_state_mirrors_is_running():
    assert _device_stub(is_running=True).get_expected_state() == "on"
    assert _device_stub(is_running=False).get_expected_state() == "off"
    assert _device_stub(is_running=None).get_expected_state() is None


def test_get_expected_state_uses_duty_value_when_dimmable():
    device = _device_stub(dimmable=True, is_running=False, duty=30)
    assert device.get_expected_state() == "on"


# ── deferred adoption behind the control lock ───────────────────────────────

def test_identify_if_running_state_defers_during_control_lock():
    device = _device_stub(ha_state="off", is_running=True)
    device.switches[0]["value"] = "off"
    device._control_lock_until = time.time() + 5.0

    device.identifyIfRunningState()

    # stale on purpose - the lock suppresses adoption
    assert device.isRunning is True
    assert device._state_resync_pending is True


def test_identify_if_running_state_clears_pending_once_lock_expired():
    device = _device_stub(ha_state="off", is_running=True)
    device.switches[0]["value"] = "off"
    device._state_resync_pending = True

    device.identifyIfRunningState()

    assert device.isRunning is False
    assert device._state_resync_pending is False


def test_adopt_actual_state_recovers_stale_true():
    device = _device_stub(ha_state="off", is_running=True)
    assert device.adopt_actual_state() is True
    assert device.isRunning is False


def test_adopt_actual_state_no_change_when_in_sync():
    device = _device_stub(ha_state="on", is_running=True)
    assert device.adopt_actual_state() is False
    assert device.isRunning is True


def test_adopt_actual_state_marks_unknown_when_offline():
    device = _device_stub(ha_state="unavailable", is_running=True)
    assert device.adopt_actual_state() is True
    assert device.isRunning is None


# ── Humidifier / Dehumidifier action gate ───────────────────────────────────

def _humidifier_stub(ha_state="off", is_running=True, cls=Humidifier):
    device = cls.__new__(cls)
    device.deviceName = "Shelly Humidifier"
    device.deviceType = "Humidifier"
    device.inRoom = "TestRoom"
    device.isDimmable = False
    device.isAcInfinDev = False
    device.isSpecialDevice = False
    device.isInitialized = True
    device.isRunning = is_running
    device.realHumidifierClass = False
    device.hasModes = False
    device.modes = {}
    device._in_smart_deadband = False
    device.switches = [{"entity_id": SWITCH, "value": None}]
    device.options = []
    device.sensors = []
    device.ogbsettings = []
    device.dataStore = FakeDataStore({"capabilities": {}})
    device.hass = MagicMock()
    device.hass.states = FakeStates({SWITCH: ha_state} if ha_state is not None else {})
    device._state_resync_pending = False
    device._control_lock_until = 0
    device._in_active_control = False
    device.turn_on = AsyncMock()
    device.turn_off = AsyncMock()
    return device


@pytest.mark.asyncio
async def test_humidifier_increase_turns_on_despite_stale_is_running():
    device = _humidifier_stub(ha_state="off", is_running=True)

    await device.increaseAction(None)

    device.turn_on.assert_awaited_once()
    assert device.isRunning is False


@pytest.mark.asyncio
async def test_humidifier_increase_skips_when_really_on():
    device = _humidifier_stub(ha_state="on", is_running=False)

    await device.increaseAction(None)

    device.turn_on.assert_not_awaited()
    assert device.isRunning is True


@pytest.mark.asyncio
async def test_humidifier_reduce_turns_off_when_really_on():
    device = _humidifier_stub(ha_state="on", is_running=False)

    await device.reduceAction(None)

    device.turn_off.assert_awaited_once()


@pytest.mark.asyncio
async def test_humidifier_reduce_skips_when_really_off():
    device = _humidifier_stub(ha_state="off", is_running=True)

    await device.reduceAction(None)

    device.turn_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_dehumidifier_increase_turns_on_despite_stale_is_running():
    device = _humidifier_stub(ha_state="off", is_running=True, cls=Dehumidifier)

    await device.increaseAction(None)

    device.turn_on.assert_awaited_once()


# ── periodic sync ───────────────────────────────────────────────────────────

def _orchestrator_stub(devices):
    orch = OGBOrchestrator.__new__(OGBOrchestrator)
    orch.room = "TestRoom"
    orch.hass = MagicMock()
    orch.data_store = FakeDataStore({"tentMode": "Live", "devices": devices})
    orch.device_manager = MagicMock()
    orch.event_manager = MagicMock()
    orch.event_manager.emit = AsyncMock()
    return orch


@pytest.mark.asyncio
async def test_sync_detects_and_corrects_drift():
    device = _device_stub(ha_state="off", is_running=True)
    orch = _orchestrator_stub([device])

    await orch._sync_device_states()

    assert device.isRunning is False
    orch.event_manager.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_applies_deferred_resync():
    """A state change lost inside the control lock is picked up by the sync."""
    device = _device_stub(ha_state="on", is_running=False)
    device.switches[0]["value"] = "on"
    device._state_resync_pending = True
    device._control_lock_until = time.time() + 5.0

    orch = _orchestrator_stub([device])
    await orch._sync_device_states()

    # Lock still active -> must not be forced
    assert device.isRunning is False
    assert device._state_resync_pending is True

    device._control_lock_until = 0
    await orch._sync_device_states()

    assert device.isRunning is True
    assert device._state_resync_pending is False


@pytest.mark.asyncio
async def test_sync_skips_when_tent_mode_disabled():
    device = _device_stub(ha_state="off", is_running=True)
    orch = _orchestrator_stub([device])
    orch.data_store.set("tentMode", "Disabled")

    await orch._sync_device_states()

    assert device.isRunning is True


@pytest.mark.asyncio
async def test_sync_ignores_non_device_entries():
    orch = _orchestrator_stub([{"name": "not a device"}])

    await orch._sync_device_states()

    orch.event_manager.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_does_not_issue_switch_commands():
    """Drift correction must not re-actuate the device."""
    device = _device_stub(ha_state="off", is_running=True)
    device.turn_on = AsyncMock()
    device.turn_off = AsyncMock()
    orch = _orchestrator_stub([device])

    await orch._sync_device_states()

    device.turn_on.assert_not_awaited()
    device.turn_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_respects_control_lock_for_drift():
    """A just-issued command must not be overruled by the not-yet-flipped state."""
    device = _device_stub(ha_state="on", is_running=False)
    device._control_lock_until = time.time() + 5.0
    orch = _orchestrator_stub([device])

    await orch._sync_device_states()

    assert device.isRunning is False


# ── turn_off hardening ──────────────────────────────────────────────────────

def _turn_off_stub(ha_state="on"):
    device = _device_stub(ha_state=ha_state, is_running=True)
    device.reliability_manager = None
    device._get_current_power = AsyncMock(return_value=None)
    device.hass.services.async_call = AsyncMock()
    return device


@pytest.mark.asyncio
async def test_turn_off_does_not_command_offline_device():
    device = _turn_off_stub(ha_state="unavailable")

    await device.turn_off()

    device.hass.services.async_call.assert_not_awaited()
    # state stays untouched instead of being optimistically flipped
    assert device.isRunning is True
    assert device._in_active_control is False


@pytest.mark.asyncio
async def test_turn_off_resets_active_control_flag():
    device = _turn_off_stub(ha_state="off")
    device.switches = []  # nothing to switch -> early return inside the loop

    await device.turn_off()

    assert device._in_active_control is False

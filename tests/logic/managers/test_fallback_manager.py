from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.opengrowbox.OGBController.managers.OGBFallBackManager import (
    OGBFallBackManager,
)
from tests.logic.helpers import FakeDataStore


class FakeHASS:
    """Minimal Home Assistant stub for fallback tests."""

    def __init__(self, states: dict | None = None):
        self.states = MagicMock()
        self.states.get = lambda entity_id: states.get(entity_id) if states else None


class FakeState:
    def __init__(self, state):
        self.state = str(state)


class FakeDevice:
    def __init__(self, name, device_type, state="off", power=0.0):
        self.deviceName = name
        self.deviceType = device_type
        self.switches = [{"entity_id": f"switch.{name.lower()}"}]
        self._commanded_state = state
        self._power = power
        self.reliability_manager = None
        self.turn_on = AsyncMock()
        self.turn_off = AsyncMock()

    async def _get_current_power(self):
        return self._power


class FakeNotificator:
    def __init__(self):
        self.warning = AsyncMock()
        self.critical = AsyncMock()


def _make_manager(states=None, initial_data=None):
    hass = FakeHASS(states=states)
    data_store = FakeDataStore(initial_data or {"controlOptions": {"lightbyOGBControl": True}})
    event_manager = MagicMock()
    event_manager.on = MagicMock()
    notificator = FakeNotificator()
    manager = OGBFallBackManager(
        hass=hass,
        dataStore=data_store,
        eventManager=event_manager,
        room="TestTent",
        regListener=None,
        notificator=notificator,
    )
    manager._is_running = True
    return manager


@pytest.mark.asyncio
async def test_validate_on_passes_when_ha_state_is_on():
    """If HA reports the light is on, validation must pass even if power sensor is 0."""
    states = {
        "switch.light": FakeState("on"),
        "sensor.light_energy_power": FakeState("0"),
    }
    manager = _make_manager(states=states)
    device = FakeDevice("light", "Light", state="on", power=0.0)

    # Shorten delays for testing
    manager.RELIABILITY_CHECK_DELAY_SECONDS = 0.1
    manager.RELIABILITY_RETRY_INTERVAL_SECONDS = 0.1

    result = await manager.validate_device_state("light", device, "on")

    assert result is True
    device.turn_on.assert_not_awaited()
    device.turn_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_on_retriggers_when_ha_state_is_off_and_power_low():
    """If HA reports off and power is low, validation should retrigger."""
    states = {
        "switch.light": FakeState("off"),
        "sensor.light_energy_power": FakeState("0"),
    }
    manager = _make_manager(states=states)
    device = FakeDevice("light", "Light", state="on", power=0.0)

    manager.RELIABILITY_CHECK_DELAY_SECONDS = 0.05
    manager.RELIABILITY_RETRY_INTERVAL_SECONDS = 0.05
    manager.RELIABILITY_MAX_RETRIES = 1

    result = await manager.validate_device_state("light", device, "on")

    assert result is False
    # Retrigger for expected_state="on" does turn_off then turn_on
    device.turn_off.assert_awaited()
    device.turn_on.assert_awaited()


@pytest.mark.asyncio
async def test_validation_storm_protection_single_in_flight():
    """Only one validation per device should run at a time."""
    states = {
        "switch.light": FakeState("on"),
        "sensor.light_energy_power": FakeState("0"),
    }
    manager = _make_manager(states=states)
    device = FakeDevice("light", "Light", state="on", power=0.0)
    manager.RELIABILITY_CHECK_DELAY_SECONDS = 0.2

    first = asyncio.create_task(manager.validate_device_state("light", device, "on"))
    await asyncio.sleep(0.05)  # let first task enter the delay
    second = await manager.validate_device_state("light", device, "on")

    assert await first is True
    assert second is True
    # Only the first validation should have reached the retrigger path
    device.turn_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_light_schedule_debounce_emits_once():
    """Multiple rapid room updates should only emit one toggleLight event."""
    # Bootstrap additional stubs required by OGBMainController that are not in conftest
    sys.modules.setdefault("socketio", types.ModuleType("socketio"))
    sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))

    from custom_components.opengrowbox.OGBController.managers.core.OGBMainController import (
        OGBMainController,
    )
    ogb_main_module = sys.modules[
        "custom_components.opengrowbox.OGBController.managers.core.OGBMainController"
    ]

    class DummyHASS:
        def __init__(self):
            self.bus = MagicMock()
            self.states = MagicMock()
            self.states.get = MagicMock(return_value=None)
            self.config = MagicMock()
            self.config.path = MagicMock(return_value="/tmp/ogb_data")

    hass = DummyHASS()
    controller = OGBMainController(hass, "TestTent")
    controller.config_manager = None

    # Configure light schedule so lights should be on
    controller.data_store.setDeep("isPlantDay.lightOnTime", "08:00:00")
    controller.data_store.setDeep("isPlantDay.lightOffTime", "20:00:00")
    controller.data_store.setDeep("controlOptions.lightbyOGBControl", True)

    # Patch current time inside lights-on window
    import datetime as _dt

    fixed_now = _dt.datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    controller.event_manager = MagicMock()
    controller.event_manager.emit = AsyncMock()

    entity = SimpleNamespace(Name="sensor.temp")

    controller._light_schedule_min_interval_seconds = 0.05

    with patch.object(ogb_main_module, "_datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.strptime = _dt.datetime.strptime

        await controller._debounced_light_schedule_update(entity)
        await controller._debounced_light_schedule_update(entity)
        await controller._debounced_light_schedule_update(entity)
        await asyncio.sleep(0.15)

    toggle_events = [call for call in controller.event_manager.emit.await_args_list if call.args[0] == "toggleLight"]
    assert len(toggle_events) == 1

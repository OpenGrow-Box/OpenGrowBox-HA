"""
Tests for the "lift" console command.

Covers:
- Listing the Lift configuration with effective (default) values
- Setting individual Lift keys, including integer keys
- Validation of scope, key and value
- Reset back to defaults
- Persistence through DeviceMinMax.Lift
- Command registration
"""

import pytest

from custom_components.opengrowbox.OGBController.managers.OGBConsoleManager import (
    OGBConsoleManager,
    _LIFT_CONFIG_KEYS,
)

from tests.logic.helpers import FakeDataStore


class FakeEventManager:
    def __init__(self):
        self.emitted = []

    async def emit(self, name, data=None, **kwargs):
        self.emitted.append((name, data))


class Harness:
    def __init__(self, lift=None):
        store = FakeDataStore({"DeviceMinMax": {"Lift": dict(lift or {})}})
        self.store = store
        self.manager = OGBConsoleManager.__new__(OGBConsoleManager)
        self.manager.name = "OGB Console Manager"
        self.manager.hass = None
        self.manager.room = "dev_room"
        self.manager.data_store = store
        self.manager.event_manager = FakeEventManager()
        self.manager.commands = {}
        self.responses = []
        self.manager._send_response = self.send

    async def send(self, message, *args, **kwargs):
        self.responses.append(str(message))

    async def run(self, params):
        await self.manager.cmd_lift(params)
        return self.responses[-1] if self.responses else ""


@pytest.fixture
def harness():
    return Harness()


class TestListing:
    @pytest.mark.asyncio
    async def test_list_shows_all_keys(self, harness):
        out = await harness.run([])
        for _alias, (key, _default) in _LIFT_CONFIG_KEYS.items():
            assert key in out

    @pytest.mark.asyncio
    async def test_list_shows_defaults(self, harness):
        out = await harness.run([])
        assert "maxPosition" in out
        assert "100" in out

    @pytest.mark.asyncio
    async def test_list_reflects_stored_values(self, harness):
        harness.store.setDeep("DeviceMinMax.Lift.maxDistanceCM", 150)
        out = await harness.run([])
        assert "150" in out

    @pytest.mark.asyncio
    async def test_missing_dict_still_lists(self, harness):
        harness.store.setDeep("DeviceMinMax.Lift", {})
        out = await harness.run([])
        assert "minDistanceCM" in out


class TestSetting:
    @pytest.mark.asyncio
    async def test_set_float_value(self, harness):
        out = await harness.run(["maxDistanceCM", "150"])
        assert harness.store.getDeep("DeviceMinMax.Lift.maxDistanceCM") == 150.0
        assert "150" in out

    @pytest.mark.asyncio
    async def test_set_float_with_decimal(self, harness):
        await harness.run(["settleTimeSec", "2.5"])
        assert harness.store.getDeep("DeviceMinMax.Lift.settleTimeSec") == 2.5

    @pytest.mark.asyncio
    async def test_integer_key_stored_as_int(self, harness):
        await harness.run(["maxIterations", "7"])
        stored = harness.store.getDeep("DeviceMinMax.Lift.maxIterations")
        assert stored == 7
        assert isinstance(stored, int)

    @pytest.mark.asyncio
    async def test_case_insensitive_key(self, harness):
        await harness.run(["MAXDISTANCECM", "120"])
        assert harness.store.getDeep("DeviceMinMax.Lift.maxDistanceCM") == 120.0

    @pytest.mark.asyncio
    async def test_set_does_not_clobber_other_keys(self, harness):
        await harness.run(["minDistanceCM", "20"])
        await harness.run(["maxDistanceCM", "150"])
        assert harness.store.getDeep("DeviceMinMax.Lift.minDistanceCM") == 20.0
        assert harness.store.getDeep("DeviceMinMax.Lift.maxDistanceCM") == 150.0

    @pytest.mark.asyncio
    async def test_response_reports_transition(self, harness):
        out = await harness.run(["maxDistanceCM", "150"])
        assert "0" in out and "150" in out


class TestReset:
    @pytest.mark.asyncio
    async def test_reset_clears_stored_values(self, harness):
        await harness.run(["maxDistanceCM", "150"])
        await harness.run(["reset"])
        assert harness.store.getDeep("DeviceMinMax.Lift") == {}

    @pytest.mark.asyncio
    async def test_reset_reports_success(self, harness):
        out = await harness.run(["reset"])
        assert "reset" in out.lower()


class TestValidation:
    @pytest.mark.asyncio
    async def test_unknown_key_rejected(self, harness):
        before = harness.store.getDeep("DeviceMinMax.Lift")
        out = await harness.run(["bogus", "1"])
        assert harness.store.getDeep("DeviceMinMax.Lift") == before
        assert "Unknown key" in out

    @pytest.mark.asyncio
    async def test_non_numeric_rejected(self, harness):
        out = await harness.run(["maxDistanceCM", "abc"])
        assert harness.store.getDeep("DeviceMinMax.Lift.maxDistanceCM") is None
        assert "must be a number" in out

    @pytest.mark.asyncio
    async def test_wrong_arity_rejected(self, harness):
        out = await harness.run(["maxDistanceCM"])
        assert "Invalid arguments" in out

    @pytest.mark.asyncio
    async def test_help_does_not_write(self, harness):
        before = harness.store.getDeep("DeviceMinMax.Lift")
        out = await harness.run(["-h"])
        assert harness.store.getDeep("DeviceMinMax.Lift") == before
        assert "usage" in out.lower()


class TestRegistration:
    @staticmethod
    def _registered_manager():
        store = FakeDataStore({"DeviceMinMax": {"Lift": {}}})
        manager = OGBConsoleManager.__new__(OGBConsoleManager)
        manager.name = "OGB Console Manager"
        manager.hass = None
        manager.room = "dev_room"
        manager.data_store = store
        manager.event_manager = FakeEventManager()
        manager.commands = {}
        manager._register_commands()
        return manager

    def test_lift_command_is_registered(self):
        assert self._registered_manager().command_exists("lift")

    def test_lift_listed_in_command_list(self):
        assert "lift" in self._registered_manager().get_command_list()

    def test_lift_has_usage_metadata(self):
        manager = self._registered_manager()
        info = manager.commands["lift"]
        assert "reset" in info.usage
        assert manager.commands["lift"].examples

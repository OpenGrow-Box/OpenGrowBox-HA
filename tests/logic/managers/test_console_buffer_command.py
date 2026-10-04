"""
Tests for the "buffer" console command.

Covers:
- Listing all buffers with effective values
- Setting dampening buffers per device type
- Setting VPD Perfection bounds buffers per channel
- Validation of scope, target, value and negativity
- Persistence through the datastore
"""

import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBData import OGBConf
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
    BUFFER_KEY_BY_PERFECTION_CHANNEL,
    BUFFER_KEY_BY_TYPE,
    DEFAULT_BUFFERS,
)
from custom_components.opengrowbox.OGBController.managers.OGBConsoleManager import (
    OGBConsoleManager,
)

from tests.logic.helpers import FakeDataStore


class FakeEventManager:
    def __init__(self):
        self.emitted = []

    async def emit(self, name, data=None, **kwargs):
        self.emitted.append((name, data))


class Harness:
    def __init__(self, buffers=None):
        store = FakeDataStore(
            {
                "controlOptionData": {
                    "buffers": dict(DEFAULT_BUFFERS if buffers is None else buffers)
                }
            }
        )
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
        await self.manager.cmd_buffer(params)
        return self.responses[-1] if self.responses else ""


@pytest.fixture
def harness():
    return Harness()


class TestDefaults:
    def test_datastore_default_matches_single_source(self):
        conf = OGBConf(hass=None)
        assert conf.controlOptionData["buffers"] == DEFAULT_BUFFERS

    def test_datastore_default_is_a_copy(self):
        conf = OGBConf(hass=None)
        conf.controlOptionData["buffers"]["coolerBuffer"] = 99.0
        assert DEFAULT_BUFFERS["coolerBuffer"] == 2.0

    def test_perfection_buffers_keep_previous_values(self):
        assert DEFAULT_BUFFERS["vpdPerfectionTempBuffer"] == 1.5
        assert DEFAULT_BUFFERS["vpdPerfectionHumBuffer"] == 3.0

    def test_dampening_buffers_keep_previous_values(self):
        assert DEFAULT_BUFFERS["heaterBuffer"] == 2.0
        assert DEFAULT_BUFFERS["coolerBuffer"] == 2.0
        assert DEFAULT_BUFFERS["humidifierBuffer"] == 5.0
        assert DEFAULT_BUFFERS["dehumidifierBuffer"] == 5.0


class TestListing:
    @pytest.mark.asyncio
    async def test_list_shows_all_buffers(self, harness):
        out = await harness.run([])
        for key in DEFAULT_BUFFERS:
            assert key in out

    @pytest.mark.asyncio
    async def test_list_shows_current_values(self, harness):
        out = await harness.run([])
        assert "1.5" in out
        assert "5.0" in out

    @pytest.mark.asyncio
    async def test_list_reflects_stored_values(self, harness):
        harness.store.setDeep("controlOptionData.buffers.coolerBuffer", 7.5)
        out = await harness.run([])
        assert "7.5" in out

    @pytest.mark.asyncio
    async def test_missing_buffers_dict_still_lists(self, harness):
        harness.store.setDeep("controlOptionData.buffers", {})
        out = await harness.run([])
        assert "coolerBuffer" in out


class TestDampening:
    @pytest.mark.asyncio
    async def test_set_damper_buffer(self, harness):
        out = await harness.run(["damper", "cooler", "1.5"])
        assert harness.store.getDeep("controlOptionData.buffers.coolerBuffer") == 1.5
        assert "1.5" in out

    @pytest.mark.asyncio
    @pytest.mark.parametrize("type_name,key", sorted(BUFFER_KEY_BY_TYPE.items()))
    async def test_all_damper_types_writable(self, harness, type_name, key):
        await harness.run(["damper", type_name, "4.25"])
        assert harness.store.getDeep(f"controlOptionData.buffers.{key}") == 4.25

    @pytest.mark.asyncio
    async def test_response_reports_transition(self, harness):
        out = await harness.run(["damper", "heater", "3.5"])
        assert "2.0" in out and "3.5" in out


class TestPerfection:
    @pytest.mark.asyncio
    async def test_set_perfection_temp_buffer(self, harness):
        await harness.run(["perfection", "temp", "2.5"])
        assert harness.store.getDeep("controlOptionData.buffers.vpdPerfectionTempBuffer") == 2.5

    @pytest.mark.asyncio
    async def test_set_perfection_humidity_buffer(self, harness):
        await harness.run(["perfection", "humidity", "4.0"])
        assert harness.store.getDeep("controlOptionData.buffers.vpdPerfectionHumBuffer") == 4.0

    @pytest.mark.asyncio
    async def test_channel_aliases(self, harness):
        await harness.run(["perfection", "temperature", "1.0"])
        assert harness.store.getDeep("controlOptionData.buffers.vpdPerfectionTempBuffer") == 1.0
        await harness.run(["perfection", "hum", "2.0"])
        assert harness.store.getDeep("controlOptionData.buffers.vpdPerfectionHumBuffer") == 2.0

    @pytest.mark.asyncio
    async def test_case_insensitive(self, harness):
        await harness.run(["PERFECTION", "TEMP", "1.25"])
        assert harness.store.getDeep("controlOptionData.buffers.vpdPerfectionTempBuffer") == 1.25

    @pytest.mark.asyncio
    async def test_damper_and_perfection_are_independent(self, harness):
        await harness.run(["damper", "cooler", "2.0"])
        await harness.run(["perfection", "temp", "2.0"])
        buffers = harness.store.getDeep("controlOptionData.buffers")
        assert buffers["coolerBuffer"] == 2.0
        assert buffers["vpdPerfectionTempBuffer"] == 2.0


class TestValidation:
    @pytest.mark.asyncio
    async def test_unknown_scope_rejected(self, harness):
        before = harness.store.getDeep("controlOptionData.buffers.coolerBuffer")
        out = await harness.run(["bogus", "cooler", "1"])
        assert harness.store.getDeep("controlOptionData.buffers.coolerBuffer") == before
        assert "Unknown scope" in out

    @pytest.mark.asyncio
    async def test_unknown_type_rejected(self, harness):
        before = harness.store.getDeep("controlOptionData.buffers.coolerBuffer")
        out = await harness.run(["damper", "bogus", "1"])
        assert harness.store.getDeep("controlOptionData.buffers.coolerBuffer") == before
        assert "Unknown type" in out

    @pytest.mark.asyncio
    async def test_unknown_channel_rejected(self, harness):
        before = harness.store.getDeep("controlOptionData.buffers.vpdPerfectionTempBuffer")
        out = await harness.run(["perfection", "bogus", "1"])
        assert harness.store.getDeep("controlOptionData.buffers.vpdPerfectionTempBuffer") == before
        assert "Unknown channel" in out

    @pytest.mark.asyncio
    async def test_non_numeric_rejected(self, harness):
        before = harness.store.getDeep("controlOptionData.buffers.coolerBuffer")
        out = await harness.run(["damper", "cooler", "abc"])
        assert harness.store.getDeep("controlOptionData.buffers.coolerBuffer") == before
        assert "must be a number" in out

    @pytest.mark.asyncio
    async def test_negative_rejected(self, harness):
        before = harness.store.getDeep("controlOptionData.buffers.coolerBuffer")
        out = await harness.run(["damper", "cooler", "-1"])
        assert harness.store.getDeep("controlOptionData.buffers.coolerBuffer") == before
        assert "zero or positive" in out

    @pytest.mark.asyncio
    async def test_zero_allowed(self, harness):
        await harness.run(["damper", "cooler", "0"])
        assert harness.store.getDeep("controlOptionData.buffers.coolerBuffer") == 0.0

    @pytest.mark.asyncio
    async def test_wrong_arity_rejected(self, harness):
        before = harness.store.getDeep("controlOptionData.buffers.coolerBuffer")
        out = await harness.run(["damper", "cooler"])
        assert harness.store.getDeep("controlOptionData.buffers.coolerBuffer") == before
        assert "Invalid arguments" in out

    @pytest.mark.asyncio
    async def test_help_does_not_write(self, harness):
        before = dict(harness.store.getDeep("controlOptionData.buffers"))
        out = await harness.run(["-h"])
        assert harness.store.getDeep("controlOptionData.buffers") == before
        assert "usage" in out.lower()


class TestRegistration:
    @staticmethod
    def _registered_manager():
        store = FakeDataStore({"controlOptionData": {"buffers": dict(DEFAULT_BUFFERS)}})
        manager = OGBConsoleManager.__new__(OGBConsoleManager)
        manager.name = "OGB Console Manager"
        manager.hass = None
        manager.room = "dev_room"
        manager.data_store = store
        manager.event_manager = FakeEventManager()
        manager.commands = {}
        manager._register_commands()
        return manager

    def test_buffer_command_is_registered(self):
        assert self._registered_manager().command_exists("buffer")

    def test_buffer_listed_in_command_list(self):
        assert "buffer" in self._registered_manager().get_command_list()

    def test_buffer_has_usage_metadata(self):
        manager = self._registered_manager()
        info = manager.commands["buffer"]
        assert "damper" in info.usage
        assert "perfection" in info.usage
        assert manager.commands["buffer"].examples
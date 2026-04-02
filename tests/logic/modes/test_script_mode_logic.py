import pytest

from custom_components.opengrowbox.OGBController.managers.OGBScriptMode import OGBScriptMode

from tests.logic.helpers import FakeDataStore, FakeEventManager


class _FakeDSManager:
    def __init__(self, config):
        self.config = config

    async def load_script(self, _room):
        return self.config


class _FakeOGB:
    def __init__(self, config):
        self.room = "dev_room"
        self.dataStore = FakeDataStore()
        self.eventManager = FakeEventManager()
        self.actionManager = object()
        self.data_storeManager = _FakeDSManager(config)


@pytest.mark.asyncio
async def test_execute_returns_false_when_script_disabled():
    mode = OGBScriptMode(_FakeOGB({"enabled": False, "type": "dsl", "script": "LOG test"}))
    assert await mode.execute() is False


@pytest.mark.asyncio
async def test_execute_dsl_emits_event_command(monkeypatch):
    ogb = _FakeOGB({"enabled": True, "type": "dsl", "script": "EMIT TestEvent WITH {'ok': true}"})
    mode = OGBScriptMode(ogb)

    # simplify parser behavior for test determinism
    async def fake_execute_dsl(_code):
        await ogb.eventManager.emit("TestEvent", {"ok": True})

    monkeypatch.setattr(mode, "_execute_dsl", fake_execute_dsl)

    assert await mode.execute() is True
    assert any(e["event_name"] == "TestEvent" for e in ogb.eventManager.emitted)


@pytest.mark.asyncio
async def test_execute_python_path_called(monkeypatch):
    ogb = _FakeOGB({"enabled": True, "type": "python", "script": "x = 1"})
    mode = OGBScriptMode(ogb)

    called = {"python": 0}

    async def fake_execute_python(_code):
        called["python"] += 1

    monkeypatch.setattr(mode, "_execute_python", fake_execute_python)

    assert await mode.execute() is True
    assert called["python"] == 1

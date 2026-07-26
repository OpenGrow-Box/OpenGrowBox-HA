import pytest

from custom_components.opengrowbox.OGBController.managers.OGBScriptMode import OGBScriptMode

from tests.logic.helpers import FakeDataStore, FakeEventManager


class _FakeDSManager:
    def __init__(self, config):
        self.config = config

    async def load_script(self, _room):
        return self.config


class _FakeActionManager:
    def __init__(self):
        self.publications = []

    async def checkLimitsAndPublicate(self, publications):
        self.publications.extend(publications)


class _FakeOGB:
    def __init__(self, config, data=None):
        self.room = "dev_room"
        self.dataStore = FakeDataStore(data)
        self.eventManager = FakeEventManager()
        self.actionManager = _FakeActionManager()
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


@pytest.mark.asyncio
async def test_dsl_if_false_skips_to_else_branch():
    script = """
    READ temp FROM sensors.temperature
    IF temp > 30 THEN
      EMIT TooHot WITH {'branch': 'if'}
    ELSE
      EMIT TempOk WITH {'branch': 'else'}
    ENDIF
    """
    ogb = _FakeOGB({"enabled": True, "type": "dsl", "script": script}, {"sensors": {"temperature": 24}})
    mode = OGBScriptMode(ogb)

    assert await mode.execute() is True

    assert [event["event_name"] for event in ogb.eventManager.emitted] == ["TempOk"]
    assert ogb.eventManager.emitted[0]["data"] == {"branch": "else"}


@pytest.mark.asyncio
async def test_python_call_helper_executes_device_action():
    script = "CALL('exhaust', 'on', priority='high')"
    ogb = _FakeOGB(
        {"enabled": True, "type": "python", "script": script},
        {"capabilities": {"canExhaust": {"state": True}}},
    )
    mode = OGBScriptMode(ogb)

    assert await mode.execute() is True

    assert len(ogb.actionManager.publications) == 1
    action = ogb.actionManager.publications[0]
    assert action.capability == "canExhaust"
    assert action.action == "On"
    assert action.priority == "high"


@pytest.mark.asyncio
async def test_python_emit_helper_executes_event():
    script = "EMIT('ScriptEvent', {'ok': True})"
    ogb = _FakeOGB({"enabled": True, "type": "python", "script": script})
    mode = OGBScriptMode(ogb)

    assert await mode.execute() is True

    assert ogb.eventManager.emitted == [
        {"event_name": "ScriptEvent", "data": {"ok": True}, "haEvent": False, "debug_type": None}
    ]

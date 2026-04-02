from types import SimpleNamespace

import pytest

from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBTankFeedManager import (
    ECUnit,
    FeedMode,
    OGBTankFeedManager,
)

from tests.logic.helpers import FakeDataStore


def _manager_stub(store=None):
    manager = OGBTankFeedManager.__new__(OGBTankFeedManager)
    manager.room = "dev_room"
    manager.data_store = store or FakeDataStore()
    manager.ec_unit = ECUnit.MS_CM
    manager.feed_mode = FeedMode.AUTOMATIC
    return manager


def test_normalize_ec_value_handles_us_and_ms_ranges():
    manager = _manager_stub()

    assert manager._normalize_ec_value(0) == 0.0
    assert manager._normalize_ec_value(-1) == 0.0

    # mS/cm range should stay unchanged
    assert manager._normalize_ec_value(2.1) == 2.1

    # µS/cm range (>100) should be converted to mS/cm
    assert manager._normalize_ec_value(1800) == 1.8


@pytest.mark.asyncio
async def test_feed_mode_change_delegates_and_updates_mode():
    calls = {"mode": None}

    class _FakeFeedLogic:
        async def handle_feed_mode_change(self, mode):
            calls["mode"] = mode

    store = FakeDataStore({"mainControl": "HomeAssistant"})
    manager = _manager_stub(store)
    manager.feed_logic_manager = _FakeFeedLogic()

    await manager._feed_mode_change("Automatic")
    assert calls["mode"] == "Automatic"
    assert manager.feed_mode == FeedMode.AUTOMATIC


@pytest.mark.asyncio
async def test_feed_mode_change_rejected_when_control_not_allowed():
    class _FakeFeedLogic:
        async def handle_feed_mode_change(self, _mode):
            raise AssertionError("should not be called")

    store = FakeDataStore({"mainControl": "Manual"})
    manager = _manager_stub(store)
    manager.feed_logic_manager = _FakeFeedLogic()

    result = await manager._feed_mode_change("Automatic")
    assert result is False


@pytest.mark.asyncio
async def test_check_if_feed_need_passes_normalized_sensor_data():
    captured = {"payload": None}

    class _FakeFeedLogic:
        async def handle_feed_update(self, payload):
            captured["payload"] = payload

    manager = _manager_stub(FakeDataStore())
    manager.feed_logic_manager = _FakeFeedLogic()
    manager.feed_mode = FeedMode.AUTOMATIC

    payload = SimpleNamespace(
        ecCurrent=1800,
        tdsCurrent=900,
        phCurrent=6.1,
        waterTemp=21.5,
        oxiCurrent=7.2,
        salCurrent=0.3,
    )

    await manager._check_if_feed_need(payload)
    assert manager.current_ec == 1.8
    assert captured["payload"] is not None
    assert captured["payload"]["ecCurrent"] == 1800.0

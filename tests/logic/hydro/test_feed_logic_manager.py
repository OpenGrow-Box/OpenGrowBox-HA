from datetime import datetime, timedelta

import pytest

from custom_components.opengrowbox.OGBController.managers.hydro.tank.OGBFeedLogicManager import (
    FeedMode,
    OGBFeedLogicManager,
)

from tests.logic.helpers import FakeDataStore, FakeEventManager


def _manager(initial=None):
    return OGBFeedLogicManager(
        room="dev_room",
        data_store=FakeDataStore(initial or {}),
        event_manager=FakeEventManager(),
    )


@pytest.mark.asyncio
async def test_handle_feed_mode_change_invalid_defaults_to_disabled():
    manager = _manager()
    await manager.handle_feed_mode_change("NotARealMode")
    assert manager.feed_mode == FeedMode.DISABLED  # Security: default to DISABLED
    assert any(e["event_name"] == "LogForClient" for e in manager.event_manager.emitted)


def test_calculate_ec_adjustment_deadzone_and_cap():
    manager = _manager()

    small = manager._calculate_ec_adjustment(current_ec=1.97, target_ec=2.0)
    assert small["nutrients_needed"] is False

    large = manager._calculate_ec_adjustment(current_ec=1.0, target_ec=2.0)
    assert large["nutrients_needed"] is True
    assert 0 < large["nutrient_dose_ml"] <= 5.0


def test_calculate_ph_adjustment_up_and_down():
    manager = _manager()

    down = manager._calculate_ph_adjustment(current_ph=6.4, target_ph=5.8)
    assert down["ph_down_needed"] is True
    assert down["ph_up_needed"] is False
    assert down["ph_down_dose_ml"] > 0

    up = manager._calculate_ph_adjustment(current_ph=5.2, target_ph=5.8)
    assert up["ph_up_needed"] is True
    assert up["ph_down_needed"] is False
    assert up["ph_up_dose_ml"] > 0


@pytest.mark.asyncio
async def test_check_if_feed_needed_respects_rate_limits(monkeypatch):
    manager = _manager()

    # block by daily feed count
    manager.daily_feed_count = manager.max_daily_feeds
    assert await manager.check_if_feed_needed({}) is False

    # allow count, block by min interval
    manager.daily_feed_count = 0
    manager.last_feed_time = datetime.now() - timedelta(seconds=10)
    assert await manager.check_if_feed_needed({}) is False

    # allow all + mock range check true
    manager.last_feed_time = datetime.now() - timedelta(seconds=manager.min_feed_interval + 5)

    async def needs_feed():
        return True

    monkeypatch.setattr(manager, "_check_ranges_and_feed", needs_feed)
    assert await manager.check_if_feed_needed({}) is True


def test_should_helpers_for_ec_and_ph():
    manager = _manager()
    assert manager.should_dose_ph_down(6.1, 5.8) is True
    assert manager.should_dose_ph_up(5.5, 5.8) is True
    assert manager.should_dose_nutrients(1.7, 2.0) is True
    assert manager.should_dilute_ec(2.2, 2.0) is True


@pytest.mark.asyncio
async def test_handle_feed_update_increments_count_and_logs():
    manager = _manager()
    before = manager.daily_feed_count
    await manager.handle_feed_update({"ec_before": 1.8, "ec_after": 2.0, "ph_before": 6.0, "ph_after": 5.8})
    assert manager.daily_feed_count == before + 1
    assert any(e["event_name"] == "LogForClient" for e in manager.event_manager.emitted)

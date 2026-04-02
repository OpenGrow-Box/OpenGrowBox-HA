from datetime import datetime as real_datetime

import pytest

from custom_components.opengrowbox.OGBController.utils import lightTimeHelpers
from custom_components.opengrowbox.OGBController.utils.lightTimeHelpers import (
    hours_between,
    update_light_state,
)


def _patch_now(monkeypatch, hour, minute, second=0):
    class FakeDateTime:
        @classmethod
        def now(cls):
            return real_datetime(2026, 1, 1, hour, minute, second)

        @classmethod
        def strptime(cls, value, fmt):
            return real_datetime.strptime(value, fmt)

    monkeypatch.setattr(lightTimeHelpers, "datetime", FakeDateTime)


@pytest.mark.asyncio
async def test_update_light_state_normal_cycle(monkeypatch):
    _patch_now(monkeypatch, 10, 0)
    result = await update_light_state("08:00:00", "20:00:00", False, "dev_room")
    assert result is True

    _patch_now(monkeypatch, 21, 0)
    result = await update_light_state("08:00:00", "20:00:00", True, "dev_room")
    assert result is False


@pytest.mark.asyncio
async def test_update_light_state_over_midnight_cycle(monkeypatch):
    _patch_now(monkeypatch, 2, 0)
    result = await update_light_state("20:00:00", "08:00:00", False, "dev_room")
    assert result is True

    _patch_now(monkeypatch, 12, 0)
    result = await update_light_state("20:00:00", "08:00:00", True, "dev_room")
    assert result is False


@pytest.mark.asyncio
async def test_update_light_state_returns_none_for_missing_times():
    assert await update_light_state(None, "08:00:00", False, "dev_room") is None
    assert await update_light_state("08:00:00", "", False, "dev_room") is None


def test_hours_between_normal_and_midnight_ranges():
    assert hours_between("08:00:00", "20:00:00") == 12.0
    assert hours_between("20:00:00", "08:00:00") == 12.0

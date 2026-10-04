import logging

import pytest

from tests.logic.managers.test_configuration_manager_day_night import (
    OGBConfigurationManager,
    _event_pub,
)


def _make_config_manager(room="dev_room"):
    from tests.logic.helpers import FakeDataStore, FakeEventManager

    return OGBConfigurationManager(
        data_store=FakeDataStore({}),
        event_manager=FakeEventManager(),
        room=room,
        hass=None,
    )


@pytest.mark.asyncio
async def test_unhandled_own_room_entity_warns(caplog):
    """A genuinely unhandled config entity of this room must still warn."""
    mgr = _make_config_manager(room="dev_room")

    with caplog.at_level(logging.WARNING):
        handled = mgr.handle_configuration_update(
            "select.ogb_bogus_setting_dev_room", _event_pub("x")
        )

    assert handled is False
    assert any(
        "Unhandled entity update: select.ogb_bogus_setting_dev_room" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_other_room_entity_does_not_warn(caplog):
    """Every room manager sees every update - foreign rooms must stay quiet.

    Before the fix the ambient manager warned about dev_room entities (and vice
    versa), doubling the log noise for each single update.
    """
    mgr = _make_config_manager(room="ambient")

    with caplog.at_level(logging.WARNING):
        handled = mgr.handle_configuration_update(
            "sensor.ogb_ambienttemperature_dev_room", _event_pub("21.5")
        )

    assert handled is False
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_room_with_space_is_normalized(caplog):
    """Room names may be stored with spaces - the suffix check must cope."""
    mgr = _make_config_manager(room="Dev Room")

    with caplog.at_level(logging.WARNING):
        handled = mgr.handle_configuration_update(
            "select.ogb_bogus_setting_dev_room", _event_pub("x")
        )

    assert handled is False
    assert any(
        "Unhandled entity update: select.ogb_bogus_setting_dev_room" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_non_ogb_entity_is_never_reported(caplog):
    mgr = _make_config_manager(room="dev_room")

    with caplog.at_level(logging.WARNING):
        mgr.handle_configuration_update("sensor.some_random_sensor", _event_pub("1"))

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
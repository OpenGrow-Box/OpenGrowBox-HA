"""Tests for the generic orphaned-entity cleanup.

The decision logic lives in ``OGBController/utils/entity_cleanup.py`` so it can
be imported without the integration's HA-heavy ``__init__.py``. These tests
cover the pure decision rule; the registry wiring in ``_cleanup_orphaned_entities``
calls ``should_remove_entity`` with the entity's disabled flag, config entry,
and whether it has a live state.
"""

import pytest

from custom_components.opengrowbox.OGBController.utils.entity_cleanup import (
    is_vanished,
    should_remove_entity,
)

ACTIVE_ENTRY = {"entry-1", "entry-2"}


@pytest.mark.parametrize(
    "disabled,config_entry_id,has_state",
    [
        # Leftover entities without a live state -> remove
        (False, "entry-1", False),
        # No config binding and no state -> registry leftover -> remove
        (False, None, False),
        # Bound to a deleted config entry -> remove even with a state
        (False, "old-entry", True),
        (False, "old-entry", False),
    ],
)
def test_removes_orphaned(disabled, config_entry_id, has_state):
    assert should_remove_entity(
        disabled=disabled,
        config_entry_id=config_entry_id,
        active_entry_ids=ACTIVE_ENTRY,
        has_state=has_state,
    )


@pytest.mark.parametrize(
    "disabled,config_entry_id,has_state",
    [
        # Disabled by the user - never auto-remove
        (True, "entry-1", False),
        (True, "old-entry", False),
        # Live entity on an active entry - keep
        (False, "entry-1", True),
        # No config binding but present in the state machine - keep
        (False, None, True),
    ],
)
def test_keeps_entity(disabled, config_entry_id, has_state):
    assert not should_remove_entity(
        disabled=disabled,
        config_entry_id=config_entry_id,
        active_entry_ids=ACTIVE_ENTRY,
        has_state=has_state,
    )

def test_detects_entity_removed_mid_processing():
    """The listener caches its candidate list, then retries for seconds.

    Entities the orphan cleanup removes in that window must be detected as
    vanished so no value error is reported for them.
    """
    known = {
        "number.ogb_feed_nutrient_a_ambient": object(),
        "date.ogb_bloomswitchdate_ambient": object(),
    }

    assert not is_vanished("number.ogb_feed_nutrient_a_ambient", known)
    assert is_vanished("number.ogb_feed_tolerance_ph_ambient", known)
    assert is_vanished("date.ogb_growstartdate_ambient", known)

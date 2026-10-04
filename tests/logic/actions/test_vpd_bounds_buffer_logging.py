"""
Tests for VPD Perfection bounds correction buffers.

Covers:
- Buffers come from the datastore (single source of truth), not hardcoded
- The reported comparison uses the effective (buffered) threshold
- The buffer value is visible in the client facing message
"""

import pytest

from custom_components.opengrowbox.OGBController.actions.OGBVPDActions import OGBVPDActions
from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import DEFAULT_BUFFERS

from tests.logic.helpers import FakeDataStore


class FakeOGB:
    def __init__(self, data_store):
        self.dataStore = data_store
        self.room = "dev_room"


def make_actions(temp, humidity, min_temp=23.0, max_temp=27.0, min_hum=55.0, max_hum=70.0, buffers=None):
    data = {
        "tentData": {
            "temperature": temp,
            "humidity": humidity,
            "minTemp": min_temp,
            "maxTemp": max_temp,
            "minHumidity": min_hum,
            "maxHumidity": max_hum,
        },
        "controlOptionData": {
            "ownWeights": False,
            "buffers": dict(DEFAULT_BUFFERS if buffers is None else buffers),
        },
    }
    store = FakeDataStore(data)
    vpd = OGBVPDActions.__new__(OGBVPDActions)
    vpd.ogb = FakeOGB(store)
    vpd.room = "dev_room"
    return vpd, store


def caps(**kwargs):
    base = {"canHeat": {"state": False}, "canCool": {"state": False},
            "canHumidify": {"state": False}, "canDehumidify": {"state": False}}
    base.update(kwargs)
    return base


def messages(actions):
    return [getattr(a, "message", "") for a in actions]


def find(actions, capability):
    return [a for a in actions if getattr(a, "capability", "") == capability]


class TestReportedThreshold:
    """The logged comparison must be the comparison that actually happened."""

    def test_temp_high_reports_buffered_threshold(self):
        # 26.2 > 27.0 is false, 26.2 > 25.5 is true -> must report 25.5
        vpd, _ = make_actions(26.2, 60.0)
        result = vpd._add_bounds_correction_actions([], caps(canCool={"state": True}))

        acts = find(result, "canCool")
        assert len(acts) == 1
        msg = getattr(acts[0], "message")
        assert "26.2 > 25.5" in msg, msg
        assert "26.2 > 27.0" not in msg, msg

    def test_temp_high_message_carries_buffer_and_limit(self):
        vpd, _ = make_actions(26.2, 60.0)
        result = vpd._add_bounds_correction_actions([], caps(canCool={"state": True}))
        msg = getattr(find(result, "canCool")[0], "message")

        assert "buffer=1.5" in msg, msg
        assert "max_temp=27.0" in msg, msg

    def test_temp_low_reports_buffered_threshold(self):
        vpd, _ = make_actions(24.0, 60.0, min_temp=23.0)
        result = vpd._add_bounds_correction_actions([], caps(canHeat={"state": True}))
        msg = getattr(find(result, "canHeat")[0], "message")

        # 24.0 < 23.0 is false, 24.0 < 24.5 is true
        assert "24.0 < 24.5" in msg, msg
        assert "24.0 < 23.0" not in msg, msg
        assert "buffer=1.5" in msg, msg

    def test_humidity_high_reports_buffered_threshold(self):
        vpd, _ = make_actions(25.0, 68.0, max_hum=70.0)
        result = vpd._add_bounds_correction_actions([], caps(canDehumidify={"state": True}))
        msg = getattr(find(result, "canDehumidify")[0], "message")

        # 68.0 > 70.0 is false, 68.0 > 67.0 is true
        assert "68.0 > 67.0" in msg, msg
        assert "68.0 > 70.0" not in msg, msg
        assert "buffer=3.0" in msg, msg

    def test_humidity_low_reports_buffered_threshold(self):
        vpd, _ = make_actions(25.0, 57.0, min_hum=55.0)
        result = vpd._add_bounds_correction_actions([], caps(canHumidify={"state": True}))
        msg = getattr(find(result, "canHumidify")[0], "message")

        # 57.0 < 55.0 is false, 57.0 < 58.0 is true
        assert "57.0 < 58.0" in msg, msg
        assert "57.0 < 55.0" not in msg, msg
        assert "buffer=3.0" in msg, msg

    def test_context_prefix_is_preserved(self):
        vpd, _ = make_actions(26.2, 60.0)
        result = vpd._add_bounds_correction_actions(
            [], caps(canCool={"state": True}), context="Perfection-"
        )
        assert getattr(find(result, "canCool")[0], "message").startswith(
            "Perfection-Bounds: Temp high"
        )


class TestNoHardcodedBuffer:
    """Buffers must be read from the datastore, not baked into the module."""

    def test_custom_temp_buffer_changes_threshold(self):
        """buffer=0.0 raises the threshold to maxTemp, so 26.2 must NOT trigger."""
        buffers = dict(DEFAULT_BUFFERS)
        buffers["vpdPerfectionTempBuffer"] = 0.0
        vpd, _ = make_actions(26.2, 60.0, buffers=buffers)
        result = vpd._add_bounds_correction_actions([], caps(canCool={"state": True}))
        assert find(result, "canCool") == []

    def test_wider_temp_buffer_triggers_earlier(self):
        """A wider buffer must fire the action at a lower temperature."""
        buffers = dict(DEFAULT_BUFFERS)
        buffers["vpdPerfectionTempBuffer"] = 3.0
        vpd, _ = make_actions(26.2, 60.0, buffers=buffers)
        result = vpd._add_bounds_correction_actions([], caps(canCool={"state": True}))

        msg = getattr(find(result, "canCool")[0], "message")
        assert "26.2 > 24.0" in msg, msg
        assert "buffer=3.0" in msg, msg

    def test_custom_hum_buffer_changes_threshold(self):
        buffers = dict(DEFAULT_BUFFERS)
        buffers["vpdPerfectionHumBuffer"] = 0.0
        vpd, _ = make_actions(25.0, 68.0, max_hum=70.0, buffers=buffers)
        result = vpd._add_bounds_correction_actions([], caps(canDehumidify={"state": True}))
        assert find(result, "canDehumidify") == []

    def test_missing_buffer_key_falls_back_to_default(self):
        vpd, _ = make_actions(26.2, 60.0, buffers={})
        result = vpd._add_bounds_correction_actions([], caps(canCool={"state": True}))
        msg = getattr(find(result, "canCool")[0], "message")
        assert "buffer=1.5" in msg, msg

    def test_default_buffers_unchanged_behaviour(self):
        """Guard against accidentally changing the shipped buffer values."""
        assert DEFAULT_BUFFERS["vpdPerfectionTempBuffer"] == 1.5
        assert DEFAULT_BUFFERS["vpdPerfectionHumBuffer"] == 3.0


class TestThresholdBoundaries:
    @pytest.mark.parametrize(
        "temp,expect_action",
        [
            (26.4, True),  # 26.4 > 25.5 -> action
            (26.5, True),
            (25.6, True),
            (25.5, False),  # 25.5 > 25.5 -> no action
            (25.4, False),
        ],
    )
    def test_temp_high_boundary(self, temp, expect_action):
        vpd, _ = make_actions(temp, 60.0)
        result = vpd._add_bounds_correction_actions([], caps(canCool={"state": True}))
        assert bool(find(result, "canCool")) is expect_action

    def test_no_action_when_inside_buffered_band(self):
        vpd, _ = make_actions(25.0, 60.0)
        result = vpd._add_bounds_correction_actions([], caps(canCool={"state": True}))
        assert find(result, "canCool") == []

    def test_missing_limits_produce_no_crash(self):
        store = FakeDataStore({"tentData": {"temperature": None, "humidity": None}})
        vpd = OGBVPDActions.__new__(OGBVPDActions)
        vpd.ogb = FakeOGB(store)
        vpd.room = "dev_room"
        result = vpd._add_bounds_correction_actions([], caps(**{"canCool": {"state": True}}))
        assert result == []
import pytest

from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
    OGBActionManager,
)
from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from tests.logic.helpers import FakeDataStore, FakeEventManager


def make_action(capability, action, message="Test", priority="medium"):
    return OGBActionPublication(
        capability=capability,
        action=action,
        Name="TestRoom",
        message=message,
        priority=priority,
    )


def action_names(action_map):
    return {(a.capability, a.action) for a in action_map}


def make_manager(capabilities, guard_enabled=True):
    data = {
        "capabilities": capabilities,
        "controlOptions": {"negativePressureGuardEnabled": guard_enabled},
    }
    ds = FakeDataStore(data)
    em = FakeEventManager()
    return OGBActionManager(hass=None, data_store=ds, event_manager=em, room="TestRoom")


class TestNegativePressureGuard:
    """Tests for _apply_negative_pressure_guard."""

    def test_dangerous_combo_corrected_when_both_caps_present(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": True}}
        )
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canIntake", "Increase"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canExhaust", "Reduce"), ("canIntake", "Reduce")}
        intake_action = [a for a in result if a.capability == "canIntake"][0]
        assert "NegativePressureGuard" in intake_action.message

    def test_safe_combo_unchanged(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": True}}
        )
        actions = [
            make_action("canExhaust", "Increase"),
            make_action("canIntake", "Reduce"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canExhaust", "Increase"), ("canIntake", "Reduce")}

    def test_both_increase_unchanged(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": True}}
        )
        actions = [
            make_action("canExhaust", "Increase"),
            make_action("canIntake", "Increase"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canExhaust", "Increase"), ("canIntake", "Increase")}

    def test_both_reduce_unchanged(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": True}}
        )
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canIntake", "Reduce"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canExhaust", "Reduce"), ("canIntake", "Reduce")}

    def test_only_exhaust_no_guard(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": False}}
        )
        actions = [
            make_action("canExhaust", "Reduce"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canExhaust", "Reduce")}

    def test_only_intake_no_guard(self):
        mgr = make_manager(
            {"canExhaust": {"state": False}, "canIntake": {"state": True}}
        )
        actions = [
            make_action("canIntake", "Increase"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canIntake", "Increase")}

    def test_no_caps_no_guard(self):
        mgr = make_manager({})
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canIntake", "Increase"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canExhaust", "Reduce"), ("canIntake", "Increase")}

    def test_guard_disabled(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": True}},
            guard_enabled=False,
        )
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canIntake", "Increase"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        assert action_names(result) == {("canExhaust", "Reduce"), ("canIntake", "Increase")}

    def test_empty_action_map(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": True}}
        )
        result = mgr._apply_negative_pressure_guard([])

        assert result == []

    def test_logs_guard_correction(self):
        mgr = make_manager(
            {"canExhaust": {"state": True}, "canIntake": {"state": True}}
        )
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canIntake", "Increase"),
        ]
        result = mgr._apply_negative_pressure_guard(actions)

        intake_action = [a for a in result if a.capability == "canIntake"][0]
        assert "NegativePressureGuard" in intake_action.message
        assert intake_action.action == "Reduce"

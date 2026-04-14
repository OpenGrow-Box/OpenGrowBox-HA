import pytest

from custom_components.opengrowbox.OGBController.actions.OGBDampeningActions import OGBDampeningActions
from custom_components.opengrowbox.OGBController.managers.OGBActionManager import OGBActionManager
from tests.logic.helpers import FakeDataStore, FakeEventManager


class FakeActionManager:
    def __init__(self):
        pass


class FakeOGB:
    def __init__(self, data_store):
        self.room = "TestRoom"
        self.dataStore = data_store
        self.actionManager = FakeActionManager()
        self.eventManager = FakeEventManager()


def make_action(capability, action, message="Test", priority="medium"):
    from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import OGBActionPublication
    return OGBActionPublication(
        capability=capability,
        action=action,
        Name="TestRoom",
        message=message,
        priority=priority,
    )


def action_names(action_map):
    return {(a.capability, a.action) for a in action_map}


class TestDynamicFanLogic:
    """Tests for _apply_dynamic_fan_logic temperature-aware fan control."""

    @pytest.fixture
    def dampening(self):
        ds = FakeDataStore({
            "capabilities": {
                "canExhaust": {"state": True},
                "canIntake": {"state": True},
                "canVentilate": {"state": True},
            }
        })
        ogb = FakeOGB(ds)
        return OGBDampeningActions(ogb)

    def test_reduce_vpd_temp_ok_both_fans_unchanged(self, dampening):
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canIntake", "Increase"),
        ]
        tent_data = {"temperature": 24, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canExhaust", "Reduce"), ("canIntake", "Increase")}

    def test_reduce_vpd_temp_high_only_exhaust_switches_to_increase(self, dampening):
        dampening.ogb.dataStore.data["capabilities"]["canIntake"]["state"] = False
        actions = [make_action("canExhaust", "Reduce")]
        tent_data = {"temperature": 27.5, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canExhaust", "Increase")}
        assert "DynamicFan" in result[0].message

    def test_reduce_vpd_temp_high_both_fans_exhaust_increases(self, dampening):
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canIntake", "Increase"),
        ]
        tent_data = {"temperature": 27.5, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canExhaust", "Increase"), ("canIntake", "Increase")}

    def test_increase_vpd_temp_ok_only_exhaust_unchanged(self, dampening):
        dampening.ogb.dataStore.data["capabilities"]["canIntake"]["state"] = False
        actions = [make_action("canExhaust", "Increase")]
        tent_data = {"temperature": 24, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canExhaust", "Increase")}

    def test_increase_vpd_temp_low_only_exhaust_switches_to_reduce(self, dampening):
        dampening.ogb.dataStore.data["capabilities"]["canIntake"]["state"] = False
        actions = [make_action("canExhaust", "Increase")]
        tent_data = {"temperature": 20.5, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canExhaust", "Reduce")}
        assert "DynamicFan" in result[0].message

    def test_increase_vpd_temp_low_both_fans_both_reduce(self, dampening):
        actions = [
            make_action("canExhaust", "Increase"),
            make_action("canIntake", "Reduce"),
        ]
        tent_data = {"temperature": 20.5, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canExhaust", "Reduce"), ("canIntake", "Reduce")}

    def test_reduce_vpd_temp_high_ventilation_also_increases(self, dampening):
        dampening.ogb.dataStore.data["capabilities"]["canIntake"]["state"] = False
        actions = [
            make_action("canExhaust", "Reduce"),
            make_action("canVentilate", "Reduce"),
        ]
        tent_data = {"temperature": 27.5, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canExhaust", "Increase"), ("canVentilate", "Increase")}

    def test_no_fan_actions_returns_unchanged(self, dampening):
        actions = [
            make_action("canCool", "Increase"),
            make_action("canHumidify", "Increase"),
        ]
        tent_data = {"temperature": 27.5, "minTemp": 20, "maxTemp": 28}
        result = dampening._apply_dynamic_fan_logic(actions, tent_data)
        assert action_names(result) == {("canCool", "Increase"), ("canHumidify", "Increase")}

    def test_missing_tent_data_returns_unchanged(self, dampening):
        actions = [make_action("canExhaust", "Reduce")]
        result = dampening._apply_dynamic_fan_logic(actions, None)
        assert action_names(result) == {("canExhaust", "Reduce")}

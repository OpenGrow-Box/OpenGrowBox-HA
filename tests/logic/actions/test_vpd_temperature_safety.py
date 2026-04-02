from custom_components.opengrowbox.OGBController.actions.OGBVPDActions import OGBVPDActions

from tests.logic.helpers import FakeDataStore, action_names


class FakeActionManager:
    async def checkLimitsAndPublicate(self, _action_map):
        return None


class FakeOGB:
    def __init__(self, data_store):
        self.room = "dev_room"
        self.dataStore = data_store
        self.actionManager = FakeActionManager()


def _base_action_map(vpd_actions):
    return [
        vpd_actions._create_action("canHeat", "Reduce", "base"),
        vpd_actions._create_action("canCool", "Increase", "base"),
        vpd_actions._create_action("canVentilate", "Increase", "base"),
        vpd_actions._create_action("canExhaust", "Increase", "base"),
        vpd_actions._create_action("canIntake", "Increase", "base"),
    ]


def test_temperature_safety_cold_overrides_actions():
    store = FakeDataStore(
        {
            "tentData": {"temperature": 18.0, "minTemp": 18.0, "maxTemp": 28.0},
            "controlOptions": {"heaterBuffer": 2.0, "coolerBuffer": 2.0},
        }
    )
    ogb = FakeOGB(store)
    vpd_actions = OGBVPDActions(ogb)

    capabilities = {
        "canHeat": {"state": True},
        "canCool": {"state": True},
        "canVentilate": {"state": True},
        "canExhaust": {"state": True},
        "canIntake": {"state": True},
    }

    result = vpd_actions._apply_temperature_safety_overrides(
        _base_action_map(vpd_actions), capabilities, "VPD"
    )
    names = action_names(result)

    assert ("canHeat", "Increase") in names
    assert ("canCool", "Reduce") in names
    assert ("canVentilate", "Reduce") in names
    assert ("canExhaust", "Reduce") in names
    assert ("canIntake", "Reduce") in names


def test_temperature_safety_hot_overrides_actions():
    store = FakeDataStore(
        {
            "tentData": {"temperature": 30.0, "minTemp": 18.0, "maxTemp": 28.0},
            "controlOptions": {"heaterBuffer": 2.0, "coolerBuffer": 2.0},
        }
    )
    ogb = FakeOGB(store)
    vpd_actions = OGBVPDActions(ogb)

    capabilities = {
        "canHeat": {"state": True},
        "canCool": {"state": True},
        "canVentilate": {"state": True},
        "canExhaust": {"state": True},
        "canIntake": {"state": True},
    }

    result = vpd_actions._apply_temperature_safety_overrides(
        _base_action_map(vpd_actions), capabilities, "VPD"
    )
    names = action_names(result)

    assert ("canHeat", "Reduce") in names
    assert ("canCool", "Increase") in names
    assert ("canVentilate", "Increase") in names
    assert ("canExhaust", "Increase") in names
    assert ("canIntake", "Increase") in names

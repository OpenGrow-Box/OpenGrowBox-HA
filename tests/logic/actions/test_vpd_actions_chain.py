import pytest

from custom_components.opengrowbox.OGBController.actions.OGBVPDActions import OGBVPDActions

from tests.logic.helpers import FakeDataStore, action_names


class FakeActionManager:
    def __init__(self):
        self.basic = None
        self.target = None

    async def checkLimitsAndPublicate(self, action_map):
        self.basic = action_map

    async def checkLimitsAndPublicateTarget(self, action_map):
        self.target = action_map


class FakeCO2Manager:
    def __init__(self, data_store):
        self.data_store = data_store
    
    async def decide_co2_action(self, mode, capabilities):
        # Simulate CO2 actions based on conditions
        is_light_on = self.data_store.getDeep("isPlantDay.islightON", False)
        co2_control = self.data_store.getDeep("controlOptions.co2Control", False)
        
        if not co2_control:
            return []
        
        from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        actions = []
        if is_light_on:
            # Day mode - increase CO2
            actions.append(OGBActionPublication(
                Name="dev_room",
                capability="canCO2",
                action="Increase",
                message="CO2 test",
                priority="medium"
            ))
        else:
            # Night mode - reduce CO2
            actions.append(OGBActionPublication(
                Name="dev_room",
                capability="canCO2",
                action="Reduce",
                message="CO2 test night",
                priority="medium"
            ))
        return actions

class FakeOGB:
    def __init__(self, data_store):
        self.room = "dev_room"
        self.dataStore = data_store
        self.actionManager = FakeActionManager()
        self.co2_manager = FakeCO2Manager(data_store)


def test_is_light_on_reads_isplantday_state():
    on_store = FakeDataStore({"isPlantDay": {"islightON": True}})
    off_store = FakeDataStore({"isPlantDay": {"islightON": False}})

    actions_on = OGBVPDActions(FakeOGB(on_store))
    actions_off = OGBVPDActions(FakeOGB(off_store))

    assert actions_on._is_light_on() is True
    assert actions_off._is_light_on() is False


@pytest.mark.asyncio
async def test_increase_vpd_builds_expected_chain_night_mode_co2_reduce():
    data_store = FakeDataStore(
        {
            "controlOptions": {
                "vpdLightControl": True,
                "co2Control": True,
            },
            "isPlantDay": {"islightON": False},
            "vpd": {"current": 1.5, "perfection": 1.1},
        }
    )
    ogb = FakeOGB(data_store)
    actions = OGBVPDActions(ogb)

    capabilities = {
        "canExhaust": {"state": True},
        "canVentilate": {"state": True},
        "canHumidify": {"state": True},
        "canCO2": {"state": True},
        "canLight": {"state": True},
    }

    await actions.increase_vpd(capabilities)
    generated = action_names(ogb.actionManager.basic)

    assert ("canExhaust", "Increase") in generated
    assert ("canVentilate", "Increase") in generated
    assert ("canHumidify", "Reduce") in generated
    assert ("canCO2", "Reduce") in generated
    assert ("canLight", "Increase") in generated


@pytest.mark.asyncio
async def test_reduce_vpd_target_chain_contains_expected_actions():
    data_store = FakeDataStore(
        {
            "controlOptions": {
                "vpdLightControl": False,
                "co2Control": True,
            },
            "isPlantDay": {"islightON": True},
            "vpd": {"current": 1.5, "targeted": 1.1},
        }
    )
    ogb = FakeOGB(data_store)
    actions = OGBVPDActions(ogb)

    capabilities = {
        "canExhaust": {"state": True},
        "canIntake": {"state": True},
        "canVentilate": {"state": True},
        "canCool": {"state": True},
        "canCO2": {"state": True},
    }

    await actions.reduce_vpd_target(capabilities)
    generated = action_names(ogb.actionManager.target)

    assert ("canExhaust", "Reduce") in generated
    assert ("canIntake", "Increase") in generated
    assert ("canVentilate", "Reduce") in generated
    assert ("canCool", "Increase") in generated
    # CO2 is now managed autonomously by CO2Manager, not directly coupled to VPD
    assert ("canCO2", "Increase") in generated  # Day mode = increase CO2


@pytest.mark.asyncio
async def test_increase_vpd_day_mode_increases_co2_when_enabled():
    data_store = FakeDataStore(
        {
            "controlOptions": {
                "vpdLightControl": False,
                "co2Control": True,
            },
            "isPlantDay": {"islightON": True},
            "vpd": {"current": 1.5, "perfection": 1.1},
        }
    )
    ogb = FakeOGB(data_store)
    actions = OGBVPDActions(ogb)

    capabilities = {
        "canCO2": {"state": True},
        "canVentilate": {"state": True},
    }

    await actions.increase_vpd(capabilities)
    generated = action_names(ogb.actionManager.basic)

    assert ("canCO2", "Increase") in generated


@pytest.mark.asyncio
async def test_increase_vpd_skips_co2_when_control_disabled():
    data_store = FakeDataStore(
        {
            "controlOptions": {
                "vpdLightControl": False,
                "co2Control": True,
            },
            "isPlantDay": {"islightON": True},
            "vpd": {"current": 1.5, "targeted": 1.1},
        }
    )
    ogb = FakeOGB(data_store)
    actions = OGBVPDActions(ogb)

    capabilities = {
        "canCO2": {"state": True},
        "canVentilate": {"state": True},
    }

    await actions.increase_vpd(capabilities)
    generated = action_names(ogb.actionManager.basic)

    assert ("canCO2", "Increase") not in generated
    assert ("canCO2", "Reduce") not in generated


@pytest.mark.asyncio
async def test_increase_vpd_target_night_forces_co2_reduce():
    data_store = FakeDataStore(
        {
            "controlOptions": {
                "vpdLightControl": False,
                "co2Control": True,
            },
            "isPlantDay": {"islightON": False},
            "vpd": {"current": 1.5, "targeted": 1.1},
        }
    )
    ogb = FakeOGB(data_store)
    actions = OGBVPDActions(ogb)

    capabilities = {
        "canCO2": {"state": True},
        "canVentilate": {"state": True},
    }

    await actions.increase_vpd_target(capabilities)
    generated = action_names(ogb.actionManager.target)

    assert ("canCO2", "Reduce") in generated
    assert ("canCO2", "Increase") not in generated


def test_bounds_correction_skips_existing_actions():
    """Ensure _add_bounds_correction_actions does not duplicate existing actions."""
    from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import OGBActionPublication
    
    data_store = FakeDataStore(
        {
            "tentData": {
                "temperature": 25.0,
                "humidity": 65.0,
                "minTemp": 20.0,
                "maxTemp": 30.0,
                "minHumidity": 40.0,
                "maxHumidity": 50.0,
            },
            "controlOptions": {
                "ownWeights": False,
            },
        }
    )
    ogb = FakeOGB(data_store)
    actions = OGBVPDActions(ogb)

    # Pre-populate action_map with canDehumidify:Increase (as main VPD logic would)
    existing = [
        OGBActionPublication(
            Name="dev_room",
            capability="canDehumidify",
            action="Increase",
            message="VPD-Target Increase Action",
            priority="medium"
        )
    ]

    capabilities = {
        "canDehumidify": {"state": True},
    }

    result = actions._add_bounds_correction_actions(existing, capabilities, "Target-")

    # Count occurrences of canDehumidify:Increase
    dehumidify_increases = [
        a for a in result
        if getattr(a, "capability", "") == "canDehumidify" and getattr(a, "action", "") == "Increase"
    ]

    assert len(dehumidify_increases) == 1, (
        f"Expected 1 canDehumidify:Increase, got {len(dehumidify_increases)}. "
        f"Actions: {[(a.capability, a.action, a.message) for a in result]}"
    )


def test_bounds_correction_adds_when_not_existing():
    """Ensure _add_bounds_correction_actions adds action when not already present."""
    from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import OGBActionPublication
    
    data_store = FakeDataStore(
        {
            "tentData": {
                "temperature": 25.0,
                "humidity": 65.0,
                "minTemp": 20.0,
                "maxTemp": 30.0,
                "minHumidity": 40.0,
                "maxHumidity": 50.0,
            },
            "controlOptions": {
                "ownWeights": False,
            },
        }
    )
    ogb = FakeOGB(data_store)
    actions = OGBVPDActions(ogb)

    # Empty action_map - bounds correction should add the action
    existing = []

    capabilities = {
        "canDehumidify": {"state": True},
    }

    result = actions._add_bounds_correction_actions(existing, capabilities, "Target-")

    dehumidify_increases = [
        a for a in result
        if getattr(a, "capability", "") == "canDehumidify" and getattr(a, "action", "") == "Increase"
    ]

    assert len(dehumidify_increases) == 1, (
        f"Expected 1 canDehumidify:Increase, got {len(dehumidify_increases)}"
    )

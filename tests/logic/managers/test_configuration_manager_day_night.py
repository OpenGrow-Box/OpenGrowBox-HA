import importlib.util
import sys
import types
from pathlib import Path

import pytest

from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBEventPublication,
)
from tests.logic.helpers import FakeDataStore, FakeEventManager


def _bootstrap_relative_imports():
    """Create the minimal package hierarchy so OGBConfigurationManager's
    relative imports resolve without importing the heavy core __init__.py."""
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))

    # custom_components.opengrowbox
    ogb = sys.modules.get("custom_components.opengrowbox")
    if ogb is None:
        ogb = types.ModuleType("custom_components.opengrowbox")
        ogb.__path__ = [str(root / "custom_components" / "opengrowbox")]
        sys.modules["custom_components.opengrowbox"] = ogb

    # ...data.OGBDataClasses
    data_pkg = types.ModuleType("custom_components.opengrowbox.OGBController.data")
    data_pkg.__path__ = [str(root / "custom_components" / "opengrowbox" / "OGBController" / "data")]
    sys.modules["custom_components.opengrowbox.OGBController.data"] = data_pkg

    dc_pkg = types.ModuleType("custom_components.opengrowbox.OGBController.data.OGBDataClasses")
    dc_pkg.__path__ = [str(root / "custom_components" / "opengrowbox" / "OGBController" / "data" / "OGBDataClasses")]
    sys.modules["custom_components.opengrowbox.OGBController.data.OGBDataClasses"] = dc_pkg

    # ...utils
    utils_pkg = types.ModuleType("custom_components.opengrowbox.OGBController.utils")
    utils_pkg.__path__ = [str(root / "custom_components" / "opengrowbox" / "OGBController" / "utils")]
    sys.modules["custom_components.opengrowbox.OGBController.utils"] = utils_pkg


def _load_configuration_manager_class():
    _bootstrap_relative_imports()

    repo_root = Path(__file__).resolve().parents[3]
    file_path = (
        repo_root
        / "custom_components"
        / "opengrowbox"
        / "OGBController"
        / "managers"
        / "core"
        / "OGBConfigurationManager.py"
    )
    spec = importlib.util.spec_from_file_location(
        "custom_components.opengrowbox.OGBController.managers.core.OGBConfigurationManager",
        file_path,
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "custom_components.opengrowbox.OGBController.managers.core"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.OGBConfigurationManager


OGBConfigurationManager = _load_configuration_manager_class()


def _make_config_manager(data_store):
    return OGBConfigurationManager(
        data_store=data_store,
        event_manager=FakeEventManager(),
        room="dev_room",
        hass=None,
    )


def _event_pub(value):
    return OGBEventPublication(Name="test_entity", newState=[value])


@pytest.fixture
def base_store():
    return FakeDataStore(
        {
            "isPlantDay": {"islightON": True},
            "controlOptions": {
                "minMaxControl": False,
                "nightSetControl": False,
            },
            "controlOptionData": {
                "minmax": {
                    "minTemp": 20.0,
                    "maxTemp": 28.0,
                    "minHum": 45.0,
                    "maxHum": 65.0,
                },
                "nightMinmax": {
                    "minTemp": 18.0,
                    "maxTemp": 24.0,
                    "minHum": 50.0,
                    "maxHum": 70.0,
                },
            },
            "tentData": {
                "minTemp": 20.0,
                "maxTemp": 28.0,
                "minHumidity": 45.0,
                "maxHumidity": 65.0,
            },
        }
    )


@pytest.mark.asyncio
async def test_apply_day_limits_during_day(base_store):
    mgr = _make_config_manager(base_store)
    await mgr._apply_day_night_limits()

    assert base_store.getDeep("tentData.minTemp") == 20.0
    assert base_store.getDeep("tentData.maxTemp") == 28.0
    assert base_store.getDeep("tentData.minHumidity") == 45.0
    assert base_store.getDeep("tentData.maxHumidity") == 65.0


@pytest.mark.asyncio
async def test_apply_night_limits_when_night_set_enabled(base_store):
    base_store.setDeep("isPlantDay.islightON", False)
    base_store.setDeep("controlOptions.nightSetControl", True)

    mgr = _make_config_manager(base_store)
    await mgr._apply_day_night_limits()

    assert base_store.getDeep("tentData.minTemp") == 18.0
    assert base_store.getDeep("tentData.maxTemp") == 24.0
    assert base_store.getDeep("tentData.minHumidity") == 50.0
    assert base_store.getDeep("tentData.maxHumidity") == 70.0


@pytest.mark.asyncio
async def test_apply_day_limits_when_night_set_disabled(base_store):
    base_store.setDeep("isPlantDay.islightON", False)
    base_store.setDeep("controlOptions.nightSetControl", False)

    mgr = _make_config_manager(base_store)
    await mgr._apply_day_night_limits()

    assert base_store.getDeep("tentData.minTemp") == 20.0
    assert base_store.getDeep("tentData.maxTemp") == 28.0
    assert base_store.getDeep("tentData.minHumidity") == 45.0
    assert base_store.getDeep("tentData.maxHumidity") == 65.0


@pytest.mark.asyncio
async def test_night_values_ignored_when_night_set_disabled(base_store):
    base_store.setDeep("isPlantDay.islightON", False)
    base_store.setDeep("controlOptions.nightSetControl", False)

    mgr = _make_config_manager(base_store)
    await mgr._update_night_min_temp(_event_pub(17.0))

    assert base_store.getDeep("controlOptionData.nightMinmax.minTemp") == 18.0
    assert base_store.getDeep("tentData.minTemp") == 20.0


@pytest.mark.asyncio
async def test_night_values_stored_when_night_set_enabled(base_store):
    base_store.setDeep("isPlantDay.islightON", False)
    base_store.setDeep("controlOptions.nightSetControl", True)

    mgr = _make_config_manager(base_store)
    await mgr._update_night_min_temp(_event_pub(17.0))

    assert base_store.getDeep("controlOptionData.nightMinmax.minTemp") == 17.0
    assert base_store.getDeep("tentData.minTemp") == 17.0


@pytest.mark.asyncio
async def test_day_value_updates_tentData_during_day(base_store):
    mgr = _make_config_manager(base_store)
    await mgr._update_min_temp(_event_pub(21.5))

    assert base_store.getDeep("controlOptionData.minmax.minTemp") == 21.5
    assert base_store.getDeep("tentData.minTemp") == 21.5


@pytest.mark.asyncio
async def test_day_value_does_not_update_tentData_at_night(base_store):
    base_store.setDeep("isPlantDay.islightON", False)
    base_store.setDeep("controlOptions.nightSetControl", True)

    mgr = _make_config_manager(base_store)
    await mgr._update_min_temp(_event_pub(21.5))

    assert base_store.getDeep("controlOptionData.minmax.minTemp") == 21.5
    # tentData stays at existing value because night limits are active
    assert base_store.getDeep("tentData.minTemp") == 20.0


@pytest.mark.asyncio
async def test_night_set_control_switch_applies_limits(base_store):
    base_store.setDeep("isPlantDay.islightON", False)

    mgr = _make_config_manager(base_store)
    await mgr._update_night_set_control(_event_pub("YES"))

    assert base_store.getDeep("controlOptions.nightSetControl") is True
    assert base_store.getDeep("tentData.minTemp") == 18.0
    assert base_store.getDeep("tentData.maxHumidity") == 70.0

    await mgr._update_night_set_control(_event_pub("NO"))

    assert base_store.getDeep("controlOptions.nightSetControl") is False
    assert base_store.getDeep("tentData.minTemp") == 20.0
    assert base_store.getDeep("tentData.maxHumidity") == 65.0


@pytest.mark.asyncio
async def test_day_to_night_transition_switches_limits():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": True},
            "controlOptions": {"nightSetControl": True},
            "controlOptionData": {
                "minmax": {
                    "minTemp": 20.0,
                    "maxTemp": 28.0,
                    "minHum": 45.0,
                    "maxHum": 65.0,
                },
                "nightMinmax": {
                    "minTemp": 18.0,
                    "maxTemp": 24.0,
                    "minHum": 50.0,
                    "maxHum": 70.0,
                },
            },
            "vpd": {
                "targeted": 1.1,
                "targetedMin": 0.99,
                "targetedMax": 1.21,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
            "tentData": {
                "minTemp": 20.0,
                "maxTemp": 28.0,
                "minHumidity": 45.0,
                "maxHumidity": 65.0,
            },
        }
    )
    mgr = _make_config_manager(store)

    # Day values initially active
    assert store.getDeep("tentData.minTemp") == 20.0
    assert store.getDeep("vpd.targeted") == 1.1

    # Simulate light turning off (like OGBMainController.light_schedule_update)
    store.setDeep("isPlantDay.islightON", False)
    await mgr._apply_day_night_limits()
    await mgr._apply_vpd_target_day_night()

    # Night values should now be active
    assert store.getDeep("tentData.minTemp") == 18.0
    assert store.getDeep("tentData.maxHumidity") == 70.0
    assert store.getDeep("vpd.targeted") == 1.3
    assert store.getDeep("vpd.targetedMin") == 1.17
    assert store.getDeep("vpd.targetedMax") == 1.43


@pytest.mark.asyncio
async def test_night_to_day_transition_restores_limits():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": False},
            "controlOptions": {"nightSetControl": True},
            "controlOptionData": {
                "minmax": {
                    "minTemp": 20.0,
                    "maxTemp": 28.0,
                    "minHum": 45.0,
                    "maxHum": 65.0,
                },
                "nightMinmax": {
                    "minTemp": 18.0,
                    "maxTemp": 24.0,
                    "minHum": 50.0,
                    "maxHum": 70.0,
                },
            },
            "vpd": {
                "targeted": 1.3,
                "targetedMin": 1.17,
                "targetedMax": 1.43,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
            "tentData": {
                "minTemp": 18.0,
                "maxTemp": 24.0,
                "minHumidity": 50.0,
                "maxHumidity": 70.0,
            },
        }
    )
    mgr = _make_config_manager(store)

    # Night values initially active
    assert store.getDeep("tentData.minTemp") == 18.0
    assert store.getDeep("vpd.targeted") == 1.3

    # Simulate light turning on
    store.setDeep("isPlantDay.islightON", True)
    await mgr._apply_day_night_limits()
    await mgr._apply_vpd_target_day_night()

    # Day values should be restored
    assert store.getDeep("tentData.minTemp") == 20.0
    assert store.getDeep("tentData.maxHumidity") == 65.0
    assert store.getDeep("vpd.targeted") == 1.1
    assert store.getDeep("vpd.targetedMin") == 0.99
    assert store.getDeep("vpd.targetedMax") == 1.21


@pytest.mark.asyncio
async def test_minmax_change_during_night_stored_but_not_active():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": False},
            "controlOptions": {"nightSetControl": True},
            "controlOptionData": {
                "minmax": {
                    "minTemp": 20.0,
                    "maxTemp": 28.0,
                    "minHum": 45.0,
                    "maxHum": 65.0,
                },
                "nightMinmax": {
                    "minTemp": 18.0,
                    "maxTemp": 24.0,
                    "minHum": 50.0,
                    "maxHum": 70.0,
                },
            },
            "vpd": {
                "targeted": 1.3,
                "targetedMin": 1.17,
                "targetedMax": 1.43,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
            "tentData": {
                "minTemp": 18.0,
                "maxTemp": 24.0,
                "minHumidity": 50.0,
                "maxHumidity": 70.0,
            },
        }
    )
    mgr = _make_config_manager(store)

    # Change day min/max while night is active
    await mgr._update_min_temp(_event_pub(22.0))
    await mgr._update_max_temp(_event_pub(30.0))
    await mgr._update_min_humidity(_event_pub(48.0))
    await mgr._update_max_humidity(_event_pub(68.0))

    # Day setters should be updated
    assert store.getDeep("controlOptionData.minmax.minTemp") == 22.0
    assert store.getDeep("controlOptionData.minmax.maxTemp") == 30.0
    assert store.getDeep("controlOptionData.minmax.minHum") == 48.0
    assert store.getDeep("controlOptionData.minmax.maxHum") == 68.0

    # But tentData should still show night values
    assert store.getDeep("tentData.minTemp") == 18.0
    assert store.getDeep("tentData.maxTemp") == 24.0
    assert store.getDeep("tentData.minHumidity") == 50.0
    assert store.getDeep("tentData.maxHumidity") == 70.0

    # After night ends, new day values should be active
    store.setDeep("isPlantDay.islightON", True)
    await mgr._apply_day_night_limits()

    assert store.getDeep("tentData.minTemp") == 22.0
    assert store.getDeep("tentData.maxTemp") == 30.0
    assert store.getDeep("tentData.minHumidity") == 48.0
    assert store.getDeep("tentData.maxHumidity") == 68.0


@pytest.mark.asyncio
async def test_night_value_change_during_day_stored_but_not_active():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": True},
            "controlOptions": {"nightSetControl": True},
            "controlOptionData": {
                "minmax": {
                    "minTemp": 20.0,
                    "maxTemp": 28.0,
                    "minHum": 45.0,
                    "maxHum": 65.0,
                },
                "nightMinmax": {
                    "minTemp": 18.0,
                    "maxTemp": 24.0,
                    "minHum": 50.0,
                    "maxHum": 70.0,
                },
            },
            "vpd": {
                "targeted": 1.1,
                "targetedMin": 0.99,
                "targetedMax": 1.21,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
            "tentData": {
                "minTemp": 20.0,
                "maxTemp": 28.0,
                "minHumidity": 45.0,
                "maxHumidity": 65.0,
            },
        }
    )
    mgr = _make_config_manager(store)

    # Change night values while day is active
    await mgr._update_night_min_temp(_event_pub(17.0))
    await mgr._update_night_max_temp(_event_pub(23.0))
    await mgr._update_night_min_humidity(_event_pub(52.0))
    await mgr._update_night_max_humidity(_event_pub(72.0))
    await mgr._update_night_vpd(_event_pub(1.4))

    # Night setters should be updated
    assert store.getDeep("controlOptionData.nightMinmax.minTemp") == 17.0
    assert store.getDeep("controlOptionData.nightMinmax.maxTemp") == 23.0
    assert store.getDeep("controlOptionData.nightMinmax.minHum") == 52.0
    assert store.getDeep("controlOptionData.nightMinmax.maxHum") == 72.0
    assert store.getDeep("vpd.NightVPD") == 1.4

    # But tentData should still show day values
    assert store.getDeep("tentData.minTemp") == 20.0
    assert store.getDeep("tentData.maxTemp") == 28.0
    assert store.getDeep("tentData.minHumidity") == 45.0
    assert store.getDeep("tentData.maxHumidity") == 65.0
    assert store.getDeep("vpd.targeted") == 1.1

    # After night starts, new night values should be active
    store.setDeep("isPlantDay.islightON", False)
    await mgr._apply_day_night_limits()
    await mgr._apply_vpd_target_day_night()

    assert store.getDeep("tentData.minTemp") == 17.0
    assert store.getDeep("tentData.maxTemp") == 23.0
    assert store.getDeep("tentData.minHumidity") == 52.0
    assert store.getDeep("tentData.maxHumidity") == 72.0
    assert store.getDeep("vpd.targeted") == 1.4
    assert store.getDeep("vpd.targetedMin") == 1.26
    assert store.getDeep("vpd.targetedMax") == 1.54


@pytest.mark.asyncio
async def test_apply_limits_unknown_light_state(base_store):
    base_store.delete("isPlantDay.islightON")

    mgr = _make_config_manager(base_store)
    await mgr._apply_day_night_limits()

    # tentData should remain untouched when light state is unknown
    assert base_store.getDeep("tentData.minTemp") == 20.0
    assert base_store.getDeep("tentData.maxTemp") == 28.0
    assert base_store.getDeep("tentData.minHumidity") == 45.0
    assert base_store.getDeep("tentData.maxHumidity") == 65.0


@pytest.mark.asyncio
async def test_night_vpd_ignored_when_night_set_disabled(base_store):
    base_store.setDeep("controlOptions.nightSetControl", False)

    mgr = _make_config_manager(base_store)
    await mgr._update_night_vpd(_event_pub(1.2))

    assert base_store.getDeep("vpd.NightVPD") is None


@pytest.mark.asyncio
async def test_night_vpd_stored_when_night_set_enabled(base_store):
    base_store.setDeep("controlOptions.nightSetControl", True)

    mgr = _make_config_manager(base_store)
    await mgr._update_night_vpd(_event_pub(1.2))

    assert base_store.getDeep("vpd.NightVPD") == 1.2


@pytest.mark.asyncio
async def test_vpd_target_applies_night_value_at_night():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": False},
            "controlOptions": {"nightSetControl": True},
            "vpd": {
                "targeted": 1.1,
                "targetedMin": 0.99,
                "targetedMax": 1.21,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
        }
    )
    mgr = _make_config_manager(store)
    await mgr._apply_vpd_target_day_night()

    assert store.getDeep("vpd.targeted") == 1.3
    assert store.getDeep("vpd.targetedMin") == 1.17
    assert store.getDeep("vpd.targetedMax") == 1.43


@pytest.mark.asyncio
async def test_vpd_target_restores_day_value_during_day():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": True},
            "controlOptions": {"nightSetControl": True},
            "vpd": {
                "targeted": 1.3,
                "targetedMin": 1.17,
                "targetedMax": 1.43,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
        }
    )
    mgr = _make_config_manager(store)
    await mgr._apply_vpd_target_day_night()

    assert store.getDeep("vpd.targeted") == 1.1
    assert store.getDeep("vpd.targetedMin") == 0.99
    assert store.getDeep("vpd.targetedMax") == 1.21


@pytest.mark.asyncio
async def test_vpd_target_day_snapshot_preserved_when_setting_at_night():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": False},
            "controlOptions": {"nightSetControl": True},
            "vpd": {
                "targeted": 1.3,
                "targetedMin": 1.17,
                "targetedMax": 1.43,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
        }
    )
    mgr = _make_config_manager(store)
    await mgr._update_vpd_target(_event_pub(1.15))

    assert store.getDeep("vpd.dayTargeted") == 1.15
    assert store.getDeep("vpd.dayTargetedMin") == 1.03
    assert store.getDeep("vpd.dayTargetedMax") == 1.26
    # Active target stays at night value
    assert store.getDeep("vpd.targeted") == 1.3


@pytest.mark.asyncio
async def test_vpd_target_tolerance_recalculates_night_bounds():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": False},
            "controlOptions": {"nightSetControl": True},
            "vpd": {
                "targeted": 1.3,
                "targetedMin": 1.17,
                "targetedMax": 1.43,
                "dayTargeted": 1.1,
                "dayTargetedMin": 0.99,
                "dayTargetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
        }
    )
    mgr = _make_config_manager(store)
    await mgr._update_vpd_tolerance(_event_pub(20.0))

    assert store.getDeep("vpd.targeted") == 1.3
    assert store.getDeep("vpd.targetedMin") == 1.04
    assert store.getDeep("vpd.targetedMax") == 1.56


@pytest.mark.asyncio
async def test_vpd_target_ignored_in_perfection_mode():
    store = FakeDataStore(
        {
            "tentMode": "VPD Perfection",
            "isPlantDay": {"islightON": False},
            "controlOptions": {"nightSetControl": True},
            "vpd": {
                "targeted": 1.1,
                "targetedMin": 0.99,
                "targetedMax": 1.21,
                "NightVPD": 1.3,
                "tolerance": 10.0,
            },
        }
    )
    mgr = _make_config_manager(store)
    await mgr._apply_vpd_target_day_night()

    assert store.getDeep("vpd.targeted") == 1.1
    assert store.getDeep("vpd.targetedMin") == 0.99
    assert store.getDeep("vpd.targetedMax") == 1.21


@pytest.mark.asyncio
async def test_plant_stage_sets_night_defaults():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": True},
            "controlOptions": {"minMaxControl": False, "nightSetControl": False},
            "plantStage": "EarlyVeg",
            "plantStages": {
                "EarlyVeg": {
                    "vpdRange": [0.6, 1.0],
                    "minTemp": 20,
                    "maxTemp": 28,
                    "minHumidity": 50,
                    "maxHumidity": 70,
                    "nightMinTemp": 18,
                    "nightMaxTemp": 26,
                    "nightMinHumidity": 55,
                    "nightMaxHumidity": 75,
                    "nightVpdRange": [0.5, 0.85],
                }
            },
            "vpd": {"tolerance": 10.0},
            "tentData": {},
        }
    )
    mgr = _make_config_manager(store)
    await mgr._plant_stage_to_vpd()

    assert store.getDeep("tentData.minTemp") == 20
    assert store.getDeep("tentData.maxTemp") == 28
    assert store.getDeep("tentData.minHumidity") == 50
    assert store.getDeep("tentData.maxHumidity") == 70

    assert store.getDeep("controlOptionData.nightMinmax.minTemp") == 18.0
    assert store.getDeep("controlOptionData.nightMinmax.maxTemp") == 26.0
    assert store.getDeep("controlOptionData.nightMinmax.minHum") == 55.0
    assert store.getDeep("controlOptionData.nightMinmax.maxHum") == 75.0
    assert store.getDeep("vpd.NightVPD") == 0.68


@pytest.mark.asyncio
async def test_plant_stage_applies_night_limits_at_night():
    store = FakeDataStore(
        {
            "tentMode": "VPD Target",
            "isPlantDay": {"islightON": False},
            "controlOptions": {"minMaxControl": False, "nightSetControl": True},
            "plantStage": "EarlyVeg",
            "plantStages": {
                "EarlyVeg": {
                    "vpdRange": [0.6, 1.0],
                    "minTemp": 20,
                    "maxTemp": 28,
                    "minHumidity": 50,
                    "maxHumidity": 70,
                    "nightMinTemp": 18,
                    "nightMaxTemp": 26,
                    "nightMinHumidity": 55,
                    "nightMaxHumidity": 75,
                    "nightVpdRange": [0.5, 0.85],
                }
            },
            "vpd": {"tolerance": 10.0},
            "tentData": {},
        }
    )
    mgr = _make_config_manager(store)
    await mgr._plant_stage_to_vpd()

    assert store.getDeep("tentData.minTemp") == 18
    assert store.getDeep("tentData.maxTemp") == 26
    assert store.getDeep("tentData.minHumidity") == 55
    assert store.getDeep("tentData.maxHumidity") == 75

    assert store.getDeep("vpd.NightVPD") == 0.68
    assert store.getDeep("vpd.targeted") == 0.68
    assert store.getDeep("vpd.targetedMin") == 0.61
    assert store.getDeep("vpd.targetedMax") == 0.75
  # midpoint of [0.5, 0.85] with 10% tolerance center

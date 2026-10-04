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
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))

    ogb = sys.modules.get("custom_components.opengrowbox")
    if ogb is None:
        ogb = types.ModuleType("custom_components.opengrowbox")
        ogb.__path__ = [str(root / "custom_components" / "opengrowbox")]
        sys.modules["custom_components.opengrowbox"] = ogb

    data_pkg = types.ModuleType("custom_components.opengrowbox.OGBController.data")
    data_pkg.__path__ = [str(root / "custom_components" / "opengrowbox" / "OGBController" / "data")]
    sys.modules["custom_components.opengrowbox.OGBController.data"] = data_pkg

    dc_pkg = types.ModuleType("custom_components.opengrowbox.OGBController.data.OGBDataClasses")
    dc_pkg.__path__ = [str(root / "custom_components" / "opengrowbox" / "OGBController" / "data" / "OGBDataClasses")]
    sys.modules["custom_components.opengrowbox.OGBController.data.OGBDataClasses"] = dc_pkg

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


def _make_config_manager(data_store, room):
    return OGBConfigurationManager(
        data_store=data_store,
        event_manager=FakeEventManager(),
        room=room,
        hass=None,
    )


def _event_pub(entity, value):
    return OGBEventPublication(Name=f"number.{entity}", newState=[value])


@pytest.fixture
def ambient_store():
    return FakeDataStore(
        {
            "vpd": {"tolerance": None, "targeted": 0.6},
            "DeviceMinMax": {
                "Light": {"minVoltage": 20.0, "maxVoltage": 0.0, "active": True},
                "Intake": {"minDuty": 0.0, "maxDuty": 0.0, "active": True},
            },
        }
    )


@pytest.fixture
def grow_store():
    return FakeDataStore(
        {
            "vpd": {"tolerance": 10.0, "targeted": 0.6},
            "DeviceMinMax": {
                "Light": {"minVoltage": 20.0, "maxVoltage": 90.0, "active": True},
                "Intake": {"minDuty": 10.0, "maxDuty": 90.0, "active": True},
            },
        }
    )


@pytest.mark.asyncio
async def test_ambient_skips_device_min_max_setter(ambient_store):
    mgr = _make_config_manager(ambient_store, "ambient")
    await mgr._device_min_max_setter(_event_pub("ogb_intake_duty_min_ambient", 5.0))

    assert ambient_store.getDeep("DeviceMinMax.Intake.minDuty") == 0.0


@pytest.mark.asyncio
async def test_grow_room_processes_device_min_max_setter(grow_store):
    mgr = _make_config_manager(grow_store, "dev_room")
    await mgr._device_min_max_setter(_event_pub("ogb_intake_duty_min_dev_room", 5.0))

    assert grow_store.getDeep("DeviceMinMax.Intake.minDuty") == 5.0


@pytest.mark.asyncio
async def test_ambient_skips_device_self_min_max(ambient_store):
    mgr = _make_config_manager(ambient_store, "ambient")
    await mgr._device_self_min_max(_event_pub("ogb_intake_minmax_ambient", "YES"))

    assert ambient_store.getDeep("DeviceMinMax.Intake.active") is True


@pytest.mark.asyncio
async def test_ambient_skips_device_dimm_step_setter(ambient_store):
    mgr = _make_config_manager(ambient_store, "ambient")
    await mgr._device_dimm_step_setter(_event_pub("ogb_light_dimm_steps_ambient", 5))

    assert ambient_store.getDeep("DeviceMinMax.Light.minVoltage") == 20.0


@pytest.mark.asyncio
async def test_ambient_skips_medium_type(ambient_store):
    mgr = _make_config_manager(ambient_store, "ambient")
    await mgr._update_medium_type(_event_pub("ogb_mediumtype_ambient", ""))

    assert not ambient_store.data.get("growMediums")
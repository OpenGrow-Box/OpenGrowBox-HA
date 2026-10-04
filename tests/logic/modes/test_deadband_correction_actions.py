"""
Tests for Smart Deadband correction actions.

Two defects are covered:
- Correction devices are only added when a value is out of band in the
  direction that device corrects, so every correction must Increase.
  Heater/Humidifier previously mapped to "Reduce" and switched the device off.
- checkLimitsAndPublicate re-evaluated the very deadband that produced the
  correction actions and silently discarded all of them.
"""

import pytest

from tests.logic.helpers import FakeDataStore, FakeEventManager
from custom_components.opengrowbox.OGBController.managers.OGBModeManager import OGBModeManager


class FakeActionManager:
    def __init__(self):
        self.calls = []

    async def checkLimitsAndPublicate(self, action_map, from_deadband_correction=False):
        self.calls.append((action_map, from_deadband_correction))

    async def checkLimitsAndPublicateTarget(self, action_map, from_deadband_correction=False):
        self.calls.append((action_map, from_deadband_correction))


def make_manager():
    data_store = FakeDataStore({
        "tentMode": "VPD Perfection",
        "tentData": {
            "temperature": 25.0,
            "humidity": 60.0,
            "minTemp": 23.0,
            "maxTemp": 27.0,
            "minHumidity": 55.0,
            "maxHumidity": 70.0,
        },
        "capabilities": {
            "canHeat": {"state": True},
            "canCool": {"state": True},
            "canHumidify": {"state": True},
            "canDehumidify": {"state": True},
        },
    })
    manager = OGBModeManager(None, data_store, FakeEventManager(), "test_room")
    manager.action_manager = FakeActionManager()
    return manager, data_store


class TestCorrectionActionDirection:
    """_create_correction_action must always Increase."""

    @pytest.mark.parametrize("device_type", ["Heater", "Cooler", "Humidifier", "Dehumidifier", "Exhaust"])
    def test_always_increases(self, device_type):
        manager, _ = make_manager()
        caps = {
            "canHeat": {"state": True},
            "canCool": {"state": True},
            "canHumidify": {"state": True},
            "canDehumidify": {"state": True},
            "canExhaust": {"state": True},
        }
        action = manager._create_correction_action(device_type, caps, 20.0, 23.0, 27.0)
        assert action is not None
        assert action.action == "Increase", f"{device_type} must Increase"

    def test_heater_below_min_increases(self):
        manager, _ = make_manager()
        action = manager._create_correction_action(
            "Heater", {"canHeat": {"state": True}}, 20.0, 23.0, 27.0
        )
        assert action.action == "Increase"
        assert action.capability == "canHeat"

    def test_humidifier_below_min_increases(self):
        manager, _ = make_manager()
        action = manager._create_correction_action(
            "Humidifier", {"canHumidify": {"state": True}}, 40.0, 55.0, 70.0
        )
        assert action.action == "Increase"
        assert action.capability == "canHumidify"

    def test_dehumidifier_above_max_increases(self):
        manager, _ = make_manager()
        action = manager._create_correction_action(
            "Dehumidifier", {"canDehumidify": {"state": True}}, 85.0, 55.0, 70.0
        )
        assert action.action == "Increase"

    def test_returns_none_inside_band(self):
        manager, _ = make_manager()
        action = manager._create_correction_action(
            "Heater", {"canHeat": {"state": True}}, 25.0, 23.0, 27.0
        )
        assert action is None

    def test_unavailable_capability_returns_none(self):
        manager, _ = make_manager()
        action = manager._create_correction_action("Heater", {"canHeat": {"state": False}}, 20.0, 23.0, 27.0)
        assert action is None


class TestDeadbandCorrectionPassThrough:
    """The correction actions must survive the action chain."""

    @pytest.mark.asyncio
    async def test_perfection_marks_deadband_correction(self):
        data_store = FakeDataStore({
            "tentMode": "VPD Perfection",
            "vpd": {"current": 1.10, "perfection": 1.10, "perfectMin": 1.00, "perfectMax": 1.20},
            "tentData": {
                "temperature": 20.0,  # below minTemp -> Heater correction
                "humidity": 60.0,
                "minTemp": 23.0,
                "maxTemp": 27.0,
                "minHumidity": 55.0,
                "maxHumidity": 70.0,
            },
            "controlOptionData": {"deadband": {"vpdDeadband": 0.05}},
            "capabilities": {
                "canHeat": {"state": True},
                "canCool": {"state": True},
                "canHumidify": {"state": True},
                "canDehumidify": {"state": True},
            },
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True},
        })
        manager = OGBModeManager(None, data_store, FakeEventManager(), "test_room")
        manager.action_manager = FakeActionManager()

        await manager.handle_vpd_perfection()

        assert data_store.getDeep("controlOptionData.deadband.active") is True
        assert manager.action_manager.calls, "correction actions should be forwarded"
        for action_map, flag in manager.action_manager.calls:
            assert flag is True, "must be marked as deadband correction"

    @pytest.mark.asyncio
    async def test_target_marks_deadband_correction(self):
        data_store = FakeDataStore({
            "tentMode": "VPD Target",
            "vpd": {
                "current": 1.10,
                "targeted": 1.10,
                "targetedMin": 1.00,
                "targetedMax": 1.20,
            },
            "tentData": {
                "temperature": 30.0,  # above maxTemp -> Cooler correction
                "humidity": 60.0,
                "minTemp": 23.0,
                "maxTemp": 27.0,
                "minHumidity": 55.0,
                "maxHumidity": 70.0,
            },
            "controlOptionData": {"deadband": {"vpdTargetDeadband": 0.05}},
            "capabilities": {
                "canHeat": {"state": True},
                "canCool": {"state": True},
                "canHumidify": {"state": True},
                "canDehumidify": {"state": True},
            },
            "controlOptions": {"nightVPDHold": True},
            "isPlantDay": {"islightON": True},
        })
        manager = OGBModeManager(None, data_store, FakeEventManager(), "test_room")
        manager.action_manager = FakeActionManager()

        await manager.handle_targeted_vpd()

        assert manager.action_manager.calls, "correction actions should be forwarded"
        for action_map, flag in manager.action_manager.calls:
            assert flag is True

    def test_regular_calls_still_use_default_flag(self):
        """Other callers must keep the default so the deadband check still applies."""
        import inspect

        from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
            OGBActionManager,
        )

        sig = inspect.signature(OGBActionManager.checkLimitsAndPublicate)
        assert sig.parameters["from_deadband_correction"].default is False
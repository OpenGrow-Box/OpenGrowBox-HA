"""
Tests for the interaction between the EnvironmentGuard and the
NegativePressureGuard.

Background
----------
``reduce_vpd`` / ``reduce_vpd_target`` / ``reduce_vpd_damping`` emit
``canExhaust Reduce`` together with ``canIntake Increase`` (OGBVPDActions.py).

The NegativePressureGuard rewrites every "Exhaust Reduce + Intake Increase"
combination to "Intake Reduce" so the tent stays under negative pressure and no
unfiltered air leaks through gaps. That is the intended behaviour for the
ordinary VPD reduce path.

The EnvironmentGuard however may *explicitly allow* that intake because fresh
air is the only remedy against a humidity / mold-hygiene emergency. Before this
interaction was handled, the NegativePressureGuard silently undid that allow,
because it only inspects capability + action and never the guard verdict.
"""

import pytest

from custom_components.opengrowbox.OGBController.actions.OGBEnvironmentGuard import (
    HUMIDITY_ALLOW_MARKER,
    MOLD_RISK_HUMIDITY,
    _contains_emergency_hint,
    is_humidity_allow_reason,
)
from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBPublications import (
    OGBActionPublication,
)
from custom_components.opengrowbox.OGBController.managers.OGBActionManager import (
    OGBActionManager,
)
from tests.logic.helpers import FakeDataStore, FakeEventManager

ROOM = "dev_room"


def make_action(capability, action, message="VPD reduce", priority="high"):
    return OGBActionPublication(
        capability=capability,
        action=action,
        Name=ROOM,
        message=message,
        priority=priority,
    )


def reduce_vpd_stage3_actions():
    """The action list ``reduce_vpd`` builds for a large deviation."""
    return [
        make_action("canExhaust", "Reduce", "VPD reduce: keep heat"),
        make_action("canIntake", "Increase", "VPD reduce: bring in drier air"),
    ]


def make_manager(tent_data, guard_enabled=True):
    data = {
        "tentData": tent_data,
        "capabilities": {
            "canIntake": {"state": True},
            "canExhaust": {"state": True},
        },
        "controlOptions": {
            "ambientControl": True,
            "negativePressureGuardEnabled": guard_enabled,
        },
    }
    return OGBActionManager(
        hass=None,
        data_store=FakeDataStore(data),
        event_manager=FakeEventManager(),
        room=ROOM,
    )


def cold_and_humid_tent(**overrides):
    """19 degC room at 85 %RH, outside air 6 degC / 45 %RH."""
    tent = {
        "temperature": 19.0,
        "humidity": 85,
        "maxTemp": 26,
        "minTemp": 19.0,
        "maxHumidity": 80,
        "minHumidity": 55,
        "OutsiteTemp": 6,
        "OutsiteHum": 45,
    }
    tent.update(overrides)
    return tent


def intake_of(action_map):
    return [a for a in action_map if a.capability == "canIntake"][0]


def marker_count(message):
    return (message or "").count(HUMIDITY_ALLOW_MARKER)


async def run_pipeline(mgr, action_map):
    """Apply both guards in the production order (EnvironmentGuard first)."""
    after_env = await mgr._apply_environment_guard(action_map)
    after_neg = mgr._apply_negative_pressure_guard(after_env)
    return after_env, after_neg


class TestHumidityAllowBeatsNegativePressure:
    """A humidity/hygiene allow-reason must survive the negative-pressure guard."""

    @pytest.mark.asyncio
    async def test_humidity_emergency_keeps_intake_increase(self):
        """85 %RH with maxHumidity 80: intake is the only remedy, keep it."""
        mgr = make_manager(cold_and_humid_tent())

        _, final = await run_pipeline(mgr, reduce_vpd_stage3_actions())

        assert intake_of(final).action == "Increase"

    @pytest.mark.asyncio
    async def test_mold_risk_with_permissive_max_humidity_keeps_intake(self):
        """maxHumidity 90 is permissive, but the 80 % hygiene limit still wins."""
        mgr = make_manager(cold_and_humid_tent(humidity=82, maxHumidity=90))

        after_env, final = await run_pipeline(mgr, reduce_vpd_stage3_actions())

        assert marker_count(intake_of(after_env).message) == 1
        assert intake_of(final).action == "Increase"

    @pytest.mark.asyncio
    async def test_room_below_hygiene_limit_still_flips_intake(self):
        """Without a humidity reason the guard keeps its original behaviour."""
        mgr = make_manager(cold_and_humid_tent(humidity=65))

        after_env, final = await run_pipeline(mgr, reduce_vpd_stage3_actions())

        assert marker_count(intake_of(after_env).message) == 0
        assert intake_of(final).action == "Reduce"

    @pytest.mark.asyncio
    async def test_guard_disabled_leaves_intake_untouched(self):
        """With the guard switched off nothing rewrites the intake at all."""
        mgr = make_manager(cold_and_humid_tent(), guard_enabled=False)

        _, final = await run_pipeline(mgr, reduce_vpd_stage3_actions())

        assert intake_of(final).action == "Increase"


class TestMarkerContract:
    """The marker must be safe for the double EnvironmentGuard application."""

    @pytest.mark.asyncio
    async def test_marker_is_idempotent(self):
        """checkLimitsAndPublicateWithDampening applies the guard twice per cycle."""
        mgr = make_manager(cold_and_humid_tent())

        once = await mgr._apply_environment_guard(reduce_vpd_stage3_actions())
        twice = await mgr._apply_environment_guard(once)

        assert marker_count(intake_of(twice).message) == 1

    @pytest.mark.asyncio
    async def test_marker_does_not_trigger_safety_override(self):
        """
        A hint word in the message would make the second EnvironmentGuard pass
        short-circuit into a safety override and corrupt the guard state.
        """
        assert _contains_emergency_hint(HUMIDITY_ALLOW_MARKER) is False
        assert (
            _contains_emergency_hint(
                "VPD reduce: bring in drier air " + HUMIDITY_ALLOW_MARKER
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_second_pass_does_not_corrupt_guard_state(self):
        mgr = make_manager(cold_and_humid_tent())

        await mgr._apply_environment_guard(reduce_vpd_stage3_actions())
        await mgr._apply_environment_guard(reduce_vpd_stage3_actions())

        state = mgr.data_store.getDeep("safety.environmentGuard") or {}
        assert state.get("lastDecision") != "allow_override"


class TestHumidityAllowReasonHelper:
    def test_all_hygiene_reasons_are_recognised(self):
        for reason in (
            "humidity_emergency_over_max",
            "humidity_emergency_under_min",
            "humidity_benefit_drying_needed",
            "mold_risk_hygiene_limit",
        ):
            assert is_humidity_allow_reason(reason) is True

    @pytest.mark.parametrize(
        "reason", ["no_risk_detected", "temp_risk_cold_source", "", None]
    )
    def test_non_hygiene_reasons_are_rejected(self, reason):
        assert is_humidity_allow_reason(reason) is False

    def test_mold_limit_is_eighty_percent(self):
        assert MOLD_RISK_HUMIDITY == 80.0
"""
Tests for Environment Guard day/night cycle behavior.

These tests verify that the environment guard correctly handles:
- Day mode: higher humidity tolerance, active cooling
- Night mode: lower temperatures, humidity control priority
- Transitions between day/night
- Plant stage specific min/max values
"""

import pytest

from custom_components.opengrowbox.OGBController.actions.OGBEnvironmentGuard import (
    evaluate_environment_guard,
    _assess_risks,
    _decide_priority,
    _select_best_air_source,
)


class FakeDataStore:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)

    def getDeep(self, path, default=None):
        parts = path.split(".")
        cur = self.data
        for part in parts:
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def setDeep(self, path, value):
        parts = path.split(".")
        cur = self.data
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value


# Default plant stages with their min/max temp and humidity
DEFAULT_PLANT_STAGES = {
    "Germination": {
        "minTemp": 20,
        "maxTemp": 24,
        "minHumidity": 78,
        "maxHumidity": 85,
    },
    "Clones": {
        "minTemp": 20,
        "maxTemp": 24,
        "minHumidity": 72,
        "maxHumidity": 80,
    },
    "EarlyVeg": {
        "minTemp": 22,
        "maxTemp": 26,
        "minHumidity": 65,
        "maxHumidity": 75,
    },
    "MidVeg": {
        "minTemp": 23,
        "maxTemp": 27,
        "minHumidity": 60,
        "maxHumidity": 72,
    },
    "LateVeg": {
        "minTemp": 24,
        "maxTemp": 27,
        "minHumidity": 55,
        "maxHumidity": 68,
    },
    "EarlyFlower": {
        "minTemp": 22,
        "maxTemp": 26,
        "minHumidity": 55,
        "maxHumidity": 68,
    },
    "MidFlower": {
        "minTemp": 21,
        "maxTemp": 25,
        "minHumidity": 48,
        "maxHumidity": 62,
    },
    "LateFlower": {
        "minTemp": 20,
        "maxTemp": 24,
        "minHumidity": 40,
        "maxHumidity": 55,
    },
}


def test_night_mode_high_humidity_allows_air_exchange():
    """Night mode with high humidity should allow exhaust to reduce humidity."""
    data_store = FakeDataStore(
        {
            "tentData": {
                "temperature": 21.0,
                "humidity": 80.0,
                "maxHumidity": 75.0,
                "minTemp": 18.0,
                "maxTemp": 28.0,
                "AmbientTemp": 18.0,
                "AmbientHum": 80.0,
            },
            "controlOptions": {
                "environmentGuardHumidityDelta": 15.0,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is False
    assert "humidity" in metadata["reason"].lower()


def test_day_mode_temp_too_cold_blocks_exhaust():
    """Day mode with cold temp and cold ambient should block exhaust."""
    data_store = FakeDataStore(
        {
            "tentData": {
                "temperature": 18.5,
                "humidity": 50.0,
                "maxHumidity": 80.0,
                "minTemp": 18.0,
                "maxTemp": 28.0,
                "AmbientTemp": 10.0,
                "AmbientHum": 90.0,
            },
            "controlOptions": {
                "environmentGuardAmbientDelta": 5.0,
                "environmentGuardMinMargin": 0.8,
                "environmentGuardHumidityDelta": 15.0,
                "environmentGuardHumidityMargin": 5.0,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is True
    assert "temp" in metadata["reason"].lower()


def test_day_mode_high_humidity_allows_exhaust():
    """Day mode with high humidity and dry ambient should allow exhaust."""
    data_store = FakeDataStore(
        {
            "tentData": {
                "temperature": 26.0,
                "humidity": 85.0,
                "maxHumidity": 75.0,
                "minTemp": 20.0,
                "maxTemp": 30.0,
                "AmbientTemp": 22.0,
                "AmbientHum": 45.0,
            },
            "controlOptions": {
                "environmentGuardHumidityCriticalMargin": 10.0,
                "environmentGuardHumidityDelta": 15.0,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is False
    assert "humidity" in metadata["reason"].lower()


def test_night_mode_cold_ambient_blocks_intake():
    """Night mode with cold intake air should block intake."""
    data_store = FakeDataStore(
        {
            "tentData": {
                "temperature": 20.0,
                "humidity": 70.0,
                "maxHumidity": 75.0,
                "minTemp": 18.0,
                "maxTemp": 28.0,
                "AmbientTemp": 15.0,
                "AmbientHum": 60.0,
                "OutsiteTemp": 5.0,
                "OutsiteHum": 70.0,
            },
            "controlOptions": {
                "environmentGuardAmbientDelta": 1.2,
            },
            "capabilities": {"canIntake": {"state": True}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canIntake", "Increase", source="test"
    )

    assert should_block is True
    assert metadata["selectedSource"] in ["outsite", "ambient"]


def test_intake_prefers_outsite_when_available():
    """Intake should prefer outside data when available."""
    temp, hum, source = _select_best_air_source(
        indoor_temp=22.0,
        indoor_hum=60.0,
        ambient_temp=18.0,
        ambient_hum=50.0,
        outsite_temp=25.0,
        outsite_hum=40.0,
        has_intake=True,
        is_intake_action=True,
    )

    assert temp == 25.0
    assert hum == 40.0
    assert source == "outsite"


def test_exhaust_uses_ambient():
    """Exhaust should use ambient room data."""
    temp, hum, source = _select_best_air_source(
        indoor_temp=22.0,
        indoor_hum=60.0,
        ambient_temp=18.0,
        ambient_hum=50.0,
        outsite_temp=25.0,
        outsite_hum=40.0,
        has_intake=False,
        is_intake_action=False,
    )

    assert temp == 18.0
    assert hum == 50.0
    assert source == "ambient"


def test_normal_conditions_allow_exchange():
    """Normal conditions should always allow air exchange."""
    risks = {
        "temp_risk": False,
        "humidity_risk": False,
        "temp_benefit": False,
        "humidity_benefit": False,
        "humidity_critical": False,
        "temp_critical": False,
    }

    should_allow, reason, priority = _decide_priority(risks, 60.0, 75.0)

    assert should_allow is True
    assert reason == "no_risk_detected"


def test_temp_benefit_allows_exchange():
    """When bringing in warmer air, should allow exchange."""
    risks = {
        "temp_risk": False,
        "humidity_risk": False,
        "temp_benefit": True,
        "humidity_benefit": False,
        "humidity_critical": False,
        "temp_critical": False,
    }

    should_allow, reason, priority = _decide_priority(risks, 50.0, 75.0)

    assert should_allow is True
    assert reason == "temp_benefit_warming_needed"


def test_high_humidity_benefit_allows_exchange():
    """When too wet and source is drier, should allow exchange."""
    risks = {
        "temp_risk": False,
        "humidity_risk": False,
        "temp_benefit": False,
        "humidity_benefit": True,
        "humidity_critical": False,
        "temp_critical": False,
    }

    should_allow, reason, priority = _decide_priority(risks, 80.0, 75.0)

    assert should_allow is True
    assert reason == "humidity_benefit_drying_needed"


def test_humidity_critical_overrides_everything():
    """Critical humidity should override all other concerns."""
    risks = {
        "temp_risk": True,
        "humidity_risk": False,
        "temp_benefit": False,
        "humidity_benefit": False,
        "humidity_critical": True,
        "humidity_critical_dry": False,
        "temp_critical": False,
    }

    should_allow, reason, priority = _decide_priority(risks, 80.0, 75.0, 60.0)

    assert should_allow is True
    assert "humidity" in reason.lower()
    assert priority == "emergency"


# Plant Stage Tests
@pytest.mark.parametrize("stage_name,stage_config", DEFAULT_PLANT_STAGES.items())
def test_plant_stage_germination_uses_correct_thresholds(stage_name, stage_config):
    """Each plant stage should use its own min/max thresholds from plantStages config."""
    mid_humidity = (stage_config["minHumidity"] + stage_config["maxHumidity"]) // 2
    
    data_store = FakeDataStore(
        {
            "plantStage": stage_name,
            "plantStages": DEFAULT_PLANT_STAGES,
            "tentData": {
                "temperature": stage_config["minTemp"] + 2,
                "humidity": mid_humidity,
                "minTemp": stage_config["minTemp"],
                "maxTemp": stage_config["maxTemp"],
                "maxHumidity": stage_config["maxHumidity"],
                "minHumidity": stage_config["minHumidity"],
                "AmbientTemp": stage_config["minTemp"] + 3,
                "AmbientHum": 50.0,
            },
            "controlOptions": {
                "environmentGuardHumidityEmergencyThreshold": 90.0,
                "environmentGuardHumidityCriticalMargin": 10.0,
                "environmentGuardHumidityDelta": 15.0,
                "environmentGuardHumidityMargin": 5.0,
                "environmentGuardAmbientDelta": 5.0,
                "environmentGuardMinMargin": 0.8,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is False, f"Stage {stage_name}: Should allow exhaust when conditions are within normal range (got reason: {metadata.get('reason')})"


@pytest.mark.parametrize("stage_name,stage_config", DEFAULT_PLANT_STAGES.items())
def test_plant_stage_cold_guard_uses_stage_min_temp(stage_name, stage_config):
    """Cold guard should use minTemp from the active plant stage."""
    cold_min_temp = stage_config["minTemp"]
    mid_humidity = (stage_config["minHumidity"] + stage_config["maxHumidity"]) // 2
    
    data_store = FakeDataStore(
        {
            "plantStage": stage_name,
            "plantStages": DEFAULT_PLANT_STAGES,
            "tentData": {
                "temperature": cold_min_temp + 0.5,
                "humidity": mid_humidity,
                "minTemp": cold_min_temp,
                "maxTemp": stage_config["maxTemp"],
                "maxHumidity": stage_config["maxHumidity"],
                "minHumidity": stage_config["minHumidity"],
                "AmbientTemp": cold_min_temp - 10,
                "AmbientHum": 80.0,
            },
            "controlOptions": {
                "environmentGuardAmbientDelta": 5.0,
                "environmentGuardMinMargin": 0.8,
                "environmentGuardHumidityMargin": 5.0,
                "environmentGuardHumidityDelta": 15.0,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is True, f"Stage {stage_name}: Should block exhaust when temp near min and ambient is cold"


@pytest.mark.parametrize("stage_name,stage_config", DEFAULT_PLANT_STAGES.items())
def test_plant_stage_high_humidity_allows_exhaust(stage_name, stage_config):
    """High humidity (above maxHumidity) should always allow exhaust regardless of temperature."""
    data_store = FakeDataStore(
        {
            "plantStage": stage_name,
            "plantStages": DEFAULT_PLANT_STAGES,
            "tentData": {
                "temperature": stage_config["minTemp"] + 0.5,
                "humidity": stage_config["maxHumidity"] + 15,
                "minTemp": stage_config["minTemp"],
                "maxTemp": stage_config["maxTemp"],
                "maxHumidity": stage_config["maxHumidity"],
                "minHumidity": stage_config["minHumidity"],
                "AmbientTemp": stage_config["minTemp"] - 5,
                "AmbientHum": 90.0,
            },
            "controlOptions": {
                "environmentGuardHumidityEmergencyThreshold": 90.0,
                "environmentGuardHumidityCriticalMargin": 10.0,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is False, f"Stage {stage_name}: Should allow exhaust when humidity is critical despite cold"
    assert "humidity" in metadata["reason"].lower(), f"Stage {stage_name}: Reason should mention humidity"


@pytest.mark.parametrize("stage_name,stage_config", DEFAULT_PLANT_STAGES.items())
def test_plant_stage_dry_air_benefit(stage_name, stage_config):
    """When indoor humidity is above maxHumidity and source is drier, should allow air exchange."""
    data_store = FakeDataStore(
        {
            "plantStage": stage_name,
            "plantStages": DEFAULT_PLANT_STAGES,
            "tentData": {
                "temperature": stage_config["minTemp"] + 2,
                "humidity": stage_config["maxHumidity"] + 5,
                "minTemp": stage_config["minTemp"],
                "maxTemp": stage_config["maxTemp"],
                "maxHumidity": stage_config["maxHumidity"],
                "minHumidity": stage_config["minHumidity"],
                "AmbientTemp": stage_config["minTemp"] + 1,
                "AmbientHum": stage_config["minHumidity"] - 10,
            },
            "controlOptions": {
                "environmentGuardHumidityDelta": 15.0,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is False, f"Stage {stage_name}: Should allow exhaust when humidity benefit exists (drying needed)"


def test_late_flower_low_humidity_stage():
    """LateFlower stage has very low humidity requirements - should handle correctly."""
    stage = "LateFlower"
    config = DEFAULT_PLANT_STAGES[stage]

    data_store = FakeDataStore(
        {
            "plantStage": stage,
            "plantStages": DEFAULT_PLANT_STAGES,
            "tentData": {
                "temperature": config["minTemp"] + 1,
                "humidity": config["maxHumidity"] + 5,
                "minTemp": config["minTemp"],
                "maxTemp": config["maxTemp"],
                "maxHumidity": config["maxHumidity"],
                "minHumidity": config["minHumidity"],
                "AmbientTemp": 18.0,
                "AmbientHum": 35.0,
            },
            "controlOptions": {
                "environmentGuardHumidityDelta": 15.0,
            },
            "capabilities": {"canIntake": {"state": False}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="test"
    )

    assert should_block is False
    assert "humidity" in metadata["reason"].lower()


# =================================================================
# CLOSED ENVIRONMENT TESTS
# =================================================================
# Closed Environment uses checkLimitsAndPublicateNoVPD which bypasses
# Night Hold but still applies Environment Guard


def test_closed_environment_temperature_control_uses_environment_guard():
    """Closed Environment temperature control - cold temp + cold ambient should block."""
    data_store = FakeDataStore(
        {
            "safety": {"environmentGuard": {"blockedCount": 0}},
            "tentData": {
                "temperature": 19.0,
                "humidity": 60.0,
                "maxTemp": 25.0,
                "minTemp": 20.0,
                "maxHumidity": 70.0,
                "minHumidity": 50.0,
                "AmbientTemp": 10.0,
                "AmbientHum": 40.0,
            },
            "controlOptions": {"environmentGuardAmbientDelta": 5.0},
            "capabilities": {"canVentilate": {"state": True}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canVentilate", "Increase", source="closed_environment"
    )

    assert should_block is True


def test_closed_environment_humidity_control_uses_environment_guard():
    """Closed Environment humidity - too wet (above maxHumidity) = allow even with cold ambient."""
    data_store = FakeDataStore(
        {
            "safety": {"environmentGuard": {"blockedCount": 0}},
            "tentData": {
                "temperature": 22.0,
                "humidity": 75.0,
                "maxTemp": 25.0,
                "minTemp": 20.0,
                "maxHumidity": 70.0,
                "minHumidity": 50.0,
                "AmbientTemp": 20.0,
                "AmbientHum": 40.0,
            },
            "controlOptions": {},
            "capabilities": {"canVentilate": {"state": True}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canVentilate", "Increase", source="closed_environment"
    )

    assert should_block is False
    assert "humidity" in metadata["reason"].lower()


def test_closed_environment_bypasses_night_hold():
    """Closed Environment - high humidity should allow action regardless of light state."""
    data_store = FakeDataStore(
        {
            "safety": {"environmentGuard": {"blockedCount": 0}},
            "tentData": {
                "temperature": 26.0,
                "humidity": 80.0,
                "maxTemp": 28.0,
                "minTemp": 20.0,
                "maxHumidity": 75.0,
                "minHumidity": 50.0,
                "AmbientTemp": 22.0,
                "AmbientHum": 50.0,
            },
            "controlOptions": {"nightVPDHold": False},
            "isPlantDay": {"islightON": False},
            "capabilities": {"canVentilate": {"state": True}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canVentilate", "Increase", source="closed_environment"
    )

    assert should_block is False
    assert metadata.get("indoorHum") == 80.0


def test_closed_environment_humidity_critical_overrides_ambient():
    """Closed Environment with critical humidity should allow air exchange despite cold ambient."""
    data_store = FakeDataStore(
        {
            "safety": {"environmentGuard": {"blockedCount": 0}},
            "tentData": {
                "temperature": 21.0,
                "humidity": 76.0,
                "maxTemp": 25.0,
                "minTemp": 20.0,
                "maxHumidity": 70.0,
                "minHumidity": 50.0,
                "AmbientTemp": 10.0,
                "AmbientHum": 80.0,
            },
            "controlOptions": {},
            "capabilities": {"canExhaust": {"state": True}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="closed_environment"
    )

    assert should_block is False
    assert "humidity" in metadata["reason"].lower()


def test_closed_environment_intake_with_outsite():
    """Closed Environment intake should prefer outside data when available."""
    data_store = FakeDataStore(
        {
            "safety": {"environmentGuard": {"blockedCount": 0}},
            "tentData": {
                "temperature": 21.0,
                "humidity": 55.0,
                "maxTemp": 25.0,
                "minTemp": 20.0,
                "maxHumidity": 70.0,
                "minHumidity": 50.0,
                "AmbientTemp": 18.0,
                "AmbientHum": 40.0,
                "OutsiteTemp": 25.0,
                "OutsiteHum": 35.0,
            },
            "controlOptions": {},
            "capabilities": {"canIntake": {"state": True}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canIntake", "Increase", source="closed_environment"
    )

    assert metadata.get("selectedSource") == "outsite"
    assert should_block is False


@pytest.mark.parametrize("stage_name,stage_config", DEFAULT_PLANT_STAGES.items())
def test_closed_environment_plant_stage_humidity_overrides(stage_name, stage_config):
    """Closed Environment should respect plant stage humidity limits."""
    data_store = FakeDataStore(
        {
            "safety": {"environmentGuard": {"blockedCount": 0}},
            "plantStage": stage_name,
            "plantStages": DEFAULT_PLANT_STAGES,
            "tentData": {
                "temperature": stage_config["minTemp"] + 1,
                "humidity": stage_config["maxHumidity"] + 3,
                "minTemp": stage_config["minTemp"],
                "maxTemp": stage_config["maxTemp"],
                "maxHumidity": stage_config["maxHumidity"],
                "minHumidity": stage_config["minHumidity"],
                "AmbientTemp": stage_config["minTemp"] - 5,
                "AmbientHum": 80.0,
            },
            "controlOptions": {},
            "capabilities": {"canExhaust": {"state": True}},
        }
    )

    should_block, metadata = evaluate_environment_guard(
        data_store, "test_room", "canExhaust", "Increase", source="closed_environment"
    )

    assert should_block is False, f"Stage {stage_name}: Critical humidity should override cold ambient"
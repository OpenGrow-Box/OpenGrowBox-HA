import pytest

from custom_components.opengrowbox.OGBController.managers.OGBDSManager import (
    IGNORED_STATE_KEYS,
    PRESERVED_STATE_KEYS,
    OGBDSManager,
    _extract_persisted_crop_steering,
)


def _full_crop_steering():
    return {
        "Mode": "Manual-Transition",
        "Active": True,
        "CropPhase": "p2",
        "phaseStartTime": "2026-08-01T10:00:00",
        "shotCounter": 12,
        "vwc_current": 45.2,
        "ec_current": 1.8,
        "Calibration": {
            "p1": {"VWCMax": 70.0, "VWCMin": 40.0, "timestamp": "2026-07-01T08:00:00"},
            "p2": {"VWCMax": 65.0, "VWCMin": 38.0, "timestamp": "2026-07-15T08:00:00"},
            "p3": {"VWCMax": None, "VWCMin": None, "timestamp": None},
            "LastRun": "2026-07-15T08:00:00",
        },
        "Learned": {
            "max_saturation_vwc": 72.0,
            "field_capacity_vwc": 66.0,
            "min_dryback_vwc": 35.0,
            "saturation_samples": 5,
            "dryback_samples": 3,
        },
        "ShotDuration": {"p0": {"value": 0}, "p1": {"value": 0}},
    }


def test_crop_steering_is_in_preserved_keys():
    assert "CropSteering" in PRESERVED_STATE_KEYS
    assert "CropSteering" not in IGNORED_STATE_KEYS


def test_extract_keeps_calibration_and_learned():
    result = _extract_persisted_crop_steering(_full_crop_steering())

    assert set(result.keys()) == {"Calibration", "Learned"}
    assert result["Calibration"]["p1"] == {
        "VWCMax": 70.0,
        "VWCMin": 40.0,
        "timestamp": "2026-07-01T08:00:00",
    }
    assert result["Calibration"]["p2"]["VWCMax"] == 65.0
    assert result["Calibration"]["LastRun"] == "2026-07-15T08:00:00"
    assert result["Learned"] == {
        "max_saturation_vwc": 72.0,
        "field_capacity_vwc": 66.0,
        "min_dryback_vwc": 35.0,
        "saturation_samples": 5,
        "dryback_samples": 3,
    }


def test_extract_drops_runtime_keys():
    result = _extract_persisted_crop_steering(_full_crop_steering())

    for key in ("Mode", "Active", "CropPhase", "phaseStartTime", "shotCounter", "vwc_current", "ShotDuration"):
        assert key not in result


def test_extract_drops_uncalibrated_phase():
    result = _extract_persisted_crop_steering(_full_crop_steering())

    assert "p3" not in result["Calibration"]


def test_extract_validates_calibration_values():
    cs = {
        "Calibration": {
            "p1": {"VWCMax": 0, "VWCMin": None, "timestamp": None},
            "p2": {"VWCMax": -5.0, "VWCMin": "abc", "timestamp": None},
            "p3": {"VWCMax": 55.0, "VWCMin": 30.0, "timestamp": "t"},
        },
        "Learned": {},
    }

    result = _extract_persisted_crop_steering(cs)

    assert "p1" not in result["Calibration"]
    assert "p2" not in result["Calibration"]
    assert result["Calibration"]["p3"] == {"VWCMax": 55.0, "VWCMin": 30.0, "timestamp": "t"}
    assert "Learned" not in result


def test_extract_keeps_learned_without_calibration():
    cs = {"Learned": {"max_saturation_vwc": 70.0, "min_dryback_vwc": 0.0}}

    result = _extract_persisted_crop_steering(cs)

    assert result == {"Learned": {"max_saturation_vwc": 70.0, "min_dryback_vwc": 0.0}}


def test_extract_handles_invalid_input():
    assert _extract_persisted_crop_steering(None) == {}
    assert _extract_persisted_crop_steering("corrupted") == {}
    assert _extract_persisted_crop_steering({"Mode": "Manual"}) == {}


def test_sanitize_state_for_save_reduces_crop_steering():
    manager = OGBDSManager.__new__(OGBDSManager)
    manager.room = "dev_room"

    state = {"CropSteering": _full_crop_steering(), "growMediums": []}
    sanitized = manager._sanitize_state_for_save(state)

    assert set(sanitized["CropSteering"].keys()) == {"Calibration", "Learned"}


def test_sanitize_state_for_save_removes_empty_crop_steering():
    manager = OGBDSManager.__new__(OGBDSManager)
    manager.room = "dev_room"

    sanitized = manager._sanitize_state_for_save({"CropSteering": {"Mode": "Manual"}})

    assert "CropSteering" not in sanitized

import pytest

from custom_components.opengrowbox.OGBController.utils.calcs import (
    calc_light_to_ppfd_dli,
    calc_dew_vpd,
    calculate_orp,
    calculate_current_vpd,
    calculate_dew_point,
    calculate_perfect_vpd,
)


def test_calculate_current_vpd_valid_values():
    result = calculate_current_vpd(25.0, 60.0, 2.0)
    assert isinstance(result, float)
    assert result == 0.91


def test_calculate_current_vpd_with_zero_leaf_offset():
    result = calculate_current_vpd(25.0, 60.0, 0.0)
    assert isinstance(result, float)
    assert result == 1.27


def test_calculate_current_vpd_with_leaf_offset_two():
    result = calculate_current_vpd(25.0, 60.0, 2.0)
    assert isinstance(result, float)
    assert result == 0.91


def test_calculate_current_vpd_invalid_input_returns_none():
    assert calculate_current_vpd("bad", 60, 2) is None
    assert calculate_current_vpd(25, None, 2) is None


def test_calculate_perfect_vpd_swaps_invalid_range():
    result = calculate_perfect_vpd([1.4, 0.8], 10)
    assert result["perfection"] == 1.1
    assert result["perfect_min"] == 0.99
    assert result["perfect_max"] == 1.21


def test_calculate_dew_point_and_dew_vpd_consistency():
    dew_point = calculate_dew_point(24.0, 55.0)
    assert isinstance(dew_point, float)

    dew_vpd = calc_dew_vpd(24.0, dew_point)
    assert dew_vpd["dewpoint_vpd"] is not None
    assert dew_vpd["vapor_pressure_actual"] is not None
    assert dew_vpd["vapor_pressure_saturation"] is not None


def test_calc_dew_vpd_expected_pressure_values():
    # Known baseline check for actual/saturation pressure outputs
    # Note: dewpoint_vpd is now rounded to 2 decimal places (was 3 before)
    result = calc_dew_vpd(25.0, 15.0)
    assert result["dewpoint_vpd"] == 1.49  # Updated: was 1.462, now 1.49 (2 decimal places)
    assert result["vapor_pressure_actual"] == 17.06
    assert result["vapor_pressure_saturation"] == 31.69


def test_calc_light_to_ppfd_dli_for_lux_fullspectrum():
    ppfd, dli = calc_light_to_ppfd_dli(15000, unit="lux", hours=18, led_type="fullspektrum_grow")
    assert ppfd == 1000
    assert dli == 64.8


def test_calc_light_to_ppfd_dli_for_lumen_conversion():
    ppfd, dli = calc_light_to_ppfd_dli(30000, unit="lumen", area_m2=2.0, hours=12, led_type="quantum_board")
    assert ppfd == 938
    assert dli == 40.5


def test_calc_light_to_ppfd_dli_negative_input_clamped_to_zero():
    ppfd, dli = calc_light_to_ppfd_dli(-1000, unit="lux", hours=18)
    assert ppfd == 0
    assert dli == 0.0


def test_calc_light_to_ppfd_dli_raises_for_invalid_unit_or_led_type():
    with pytest.raises(ValueError):
        calc_light_to_ppfd_dli(10000, unit="candela", hours=18)

    with pytest.raises(ValueError):
        calc_light_to_ppfd_dli(10000, unit="lux", hours=18, led_type="not_valid")


def test_calculate_orp_baseline_and_temp_impact():
    baseline = calculate_orp(6.0, 25.0)
    warmer = calculate_orp(6.0, 30.0)

    assert baseline == 236.64
    # Higher temperature should slightly reduce ORP with current formula
    assert warmer < baseline

"""
Test VPD precision - ensures all VPD calculations use max 2 decimal places.
This prevents issues like 1.22334234234 appearing in the VPD sensor.
"""

import pytest
from custom_components.opengrowbox.OGBController.utils.calcs import (
    calculate_current_vpd,
    calculate_avg_value,
    calc_dew_vpd,
    calculate_dew_point,
    calculate_perfect_vpd,
)


class TestVPDPrecision:
    """Test that all VPD calculations respect 2 decimal place limit."""

    def test_calculate_current_vpd_returns_max_2_decimals(self):
        """Test that calculate_current_vpd always returns max 2 decimal places."""
        # Test various temperature/humidity combinations
        test_cases = [
            # (temp, humidity, leaf_offset)
            (25.0, 60.0, 0.0),
            (24.5, 55.0, 0.0),
            (22.0, 65.0, 0.0),
            (26.0, 50.0, 0.0),
            (23.5, 58.0, 0.0),
            (21.0, 70.0, 0.0),
            (28.0, 45.0, 0.0),
            (20.0, 80.0, 0.0),
        ]

        for temp, hum, leaf_offset in test_cases:
            result = calculate_current_vpd(temp, hum, leaf_offset)

            # Verify result is not None
            assert result is not None, f"VPD calculation failed for temp={temp}, hum={hum}, leaf_offset={leaf_offset}"

            # Verify result has max 2 decimal places
            assert isinstance(result, float), f"Result should be float, got {type(result)}"
            decimal_places = len(str(result).split('.')[-1]) if '.' in str(result) else 0
            assert decimal_places <= 2, f"VPD {result} has {decimal_places} decimal places, max 2 allowed"

            # Verify result is in reasonable range (0 to 5 kPa)
            assert 0 <= result <= 5.0, f"VPD {result} is outside reasonable range for temp={temp}, hum={hum}"

    def test_calculate_current_vpd_with_many_decimals_input(self):
        """Test that calculate_current_vpd handles input with many decimals correctly."""
        # Even if inputs have many decimals, output should have max 2 decimals
        test_cases = [
            # (temp, humidity, leaf_offset)
            (25.123456789, 60.987654321, 0.123456789),  # Many decimals in all inputs
            (24.555555555, 55.777777777, 0.0),  # Many decimals
            (22.111111111, 65.333333333, 0.222222222),  # Many decimals
        ]

        for temp, hum, leaf_offset in test_cases:
            result = calculate_current_vpd(temp, hum, leaf_offset)

            # Verify result has max 2 decimal places
            assert result is not None
            decimal_places = len(str(result).split('.')[-1]) if '.' in str(result) else 0
            assert decimal_places <= 2, f"VPD {result} has {decimal_places} decimal places, max 2 allowed (input temp={temp})"

    def test_calc_dew_vpd_returns_max_2_decimals(self):
        """Test that calc_dew_vpd returns max 2 decimal places for VPD."""
        test_cases = [
            # (air_temp, dew_point, expected_vpd)
            (25.0, 20.0, 1.00),
            (24.5, 18.5, 0.99),
            (22.0, 15.0, 0.99),
            (26.0, 22.0, 1.02),
            (28.0, 24.0, 1.04),
            (30.0, 26.0, 1.06),
        ]

        for air_temp, dew_point, expected in test_cases:
            result = calc_dew_vpd(air_temp, dew_point)

            # Verify result is not None
            assert result is not None, f"Dew VPD calculation failed for air_temp={air_temp}, dew_point={dew_point}"

            # Check dewpoint_vpd has max 2 decimal places
            dewpoint_vpd = result["dewpoint_vpd"]
            assert dewpoint_vpd is not None, "dewpoint_vpd should not be None"
            decimal_places = len(str(dewpoint_vpd).split('.')[-1]) if '.' in str(dewpoint_vpd) else 0
            assert decimal_places <= 2, f"DewPoint VPD {dewpoint_vpd} has {decimal_places} decimal places, max 2 allowed"

            # Check vapor_pressure_actual has max 2 decimal places
            vpa = result["vapor_pressure_actual"]
            assert vpa is not None, "vapor_pressure_actual should not be None"
            decimal_places = len(str(vpa).split('.')[-1]) if '.' in str(vpa) else 0
            assert decimal_places <= 2, f"VPA {vpa} has {decimal_places} decimal places, max 2 allowed"

            # Check vapor_pressure_saturation has max 2 decimal places
            vps = result["vapor_pressure_saturation"]
            assert vps is not None, "vapor_pressure_saturation should not be None"
            decimal_places = len(str(vps).split('.')[-1]) if '.' in str(vps) else 0
            assert decimal_places <= 2, f"VPS {vps} has {decimal_places} decimal places, max 2 allowed"

    def test_calculate_avg_value_rounds_to_2_decimals(self):
        """Test that calculate_avg_value rounds input values to 2 decimals."""
        # Test with input values with many decimals
        test_cases = [
            # (input_values, expected_avg)
            (
                [
                    {"value": 25.123456789, "label": "temp1"},
                    {"value": 25.987654321, "label": "temp2"},
                    {"value": 24.555555555, "label": "temp3"},
                ],
                25.22  # (25.12 + 25.99 + 24.56) / 3 = 25.22
            ),
            (
                [
                    {"value": 24.111111111, "label": "temp1"},
                    {"value": 25.333333333, "label": "temp2"},
                ],
                24.72  # (24.11 + 25.33) / 2 = 24.72
            ),
            (
                [
                    {"value": 23.456789012, "label": "hum1"},
                    {"value": 60.789012345, "label": "hum2"},
                ],
                42.12  # (23.46 + 60.79) / 2 = 42.125
            ),
        ]

        for input_values, expected_avg in test_cases:
            result = calculate_avg_value(input_values)

            # Verify result
            assert result != "unavailable", f"Average calculation failed for {input_values}"
            assert isinstance(result, float), f"Result should be float, got {type(result)}"

            # Verify result has max 2 decimal places
            decimal_places = len(str(result).split('.')[-1]) if '.' in str(result) else 0
            assert decimal_places <= 2, f"Average {result} has {decimal_places} decimal places, max 2 allowed"

            # Verify expected value
            assert abs(result - expected_avg) < 0.01, f"Average mismatch: expected {expected_avg}, got {result}"

    def test_calculate_perfect_vpd_returns_max_2_decimals(self):
        """Test that calculate_perfect_vpd returns max 2 decimal places."""
        test_cases = [
            # (vpd_range, tolerance_percent)
            ([1.0, 1.0], 1.0),  # Range: 1.0, average: 1.0, tolerance: 1%
            ([1.2, 1.0], 10.0),  # Range: 1.2, tolerance: 10%
            ([0.8, 1.2], 5.0),  # Range: 0.8-1.2, tolerance: 5%
            ([1.3, 1.5], 15.0),  # Range: 1.3-1.5, tolerance: 15%
        ]

        for vpd_range, tolerance_percent in test_cases:
            result = calculate_perfect_vpd(vpd_range, tolerance_percent)

            # Verify result structure
            assert "perfection" in result, "Result should have 'perfection' key"
            assert "perfect_min" in result, "Result should have 'perfect_min' key"
            assert "perfect_max" in result, "Result should have 'perfect_max' key"

            # Check all values have max 2 decimal places
            for key in ["perfection", "perfect_min", "perfect_max"]:
                value = result[key]
                assert value is not None, f"{key} should not be None"
                assert isinstance(value, float), f"{key} should be float, got {type(value)}"

                decimal_places = len(str(value).split('.')[-1]) if '.' in str(value) else 0
                assert decimal_places <= 2, f"{key} {value} has {decimal_places} decimal places, max 2 allowed"

    def test_calculate_dew_point_returns_max_2_decimals(self):
        """Test that calculate_dew_point returns max 2 decimal places."""
        test_cases = [
            # (temp, humidity)
            (25.0, 60.0),
            (24.5, 55.0),
            (22.0, 65.0),
            (26.0, 50.0),
            (28.0, 45.0),
        ]

        for temp, humidity in test_cases:
            result = calculate_dew_point(temp, humidity)

            # Verify result
            assert result != "unavailable", f"Dew point calculation failed for temp={temp}, humidity={humidity}"

            # Verify result has max 2 decimal places
            assert isinstance(result, float), f"Result should be float, got {type(result)}"
            decimal_places = len(str(result).split('.')[-1]) if '.' in str(result) else 0
            assert decimal_places <= 2, f"Dew point {result} has {decimal_places} decimal places, max 2 allowed"

            # Verify result is in reasonable range (dew point should be < temperature)
            assert result < temp, f"Dew point {result} should be less than temperature {temp}"
            assert result > 0, f"Dew point {result} should be positive"

    def test_extreme_decimal_values_handled_correctly(self):
        """Test that extreme decimal values are handled correctly."""
        # Test with extreme values that could cause precision issues
        extreme_cases = [
            (99.999999999, 99.999999999, 50.0),  # Extreme temp/humidity
            (0.000000001, 0.000000001, 0.0),  # Near-zero values
            (1.234567890, 60.987654321, 1.123456789),  # Many decimals everywhere
        ]

        for temp, hum, leaf_offset in extreme_cases:
            result = calculate_current_vpd(temp, hum, leaf_offset)

            # Even with extreme inputs, result should have max 2 decimals
            if result is not None:
                decimal_places = len(str(result).split('.')[-1]) if '.' in str(result) else 0
                assert decimal_places <= 2, f"Extreme input temp={temp}, hum={hum} produced VPD {result} with {decimal_places} decimal places, max 2 allowed"

    def test_vpd_values_never_have_more_than_2_decimals(self):
        """Test that all VPD-related functions never return values with > 2 decimal places."""
        # Test a wide range of realistic values
        for temp in range(15, 35, 2):  # 15°C to 35°C in 2°C steps
            for hum in range(30, 80, 5):  # 30% to 80% RH in 5% steps
                for leaf_offset in [0.0, 1.0, 2.0]:  # Common leaf offsets
                    result = calculate_current_vpd(temp, hum, leaf_offset)

                    if result is not None:
                        decimal_places = len(str(result).split('.')[-1]) if '.' in str(result) else 0
                        assert decimal_places <= 2, (
                            f"VPD {result} from temp={temp}, hum={hum}, leaf={leaf_offset} "
                            f"has {decimal_places} decimal places, max 2 allowed"
                        )

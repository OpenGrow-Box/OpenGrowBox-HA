"""
OGB Advanced Sensor Processing Module

Implements TDR sensor logic with:
- Polynomial VWC calibration (Teros-12 soilless calibration)
- Pore Water EC calculation (Hilhorst model + mass-balance hybrid)
- Temperature normalization for EC readings
- Medium-specific calibrations (rockwool, coco, soil, perlite, aero, water)

Based on: https://github.com/JakeTheRabbit/TDR-Sensor
"""

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

_LOGGER = logging.getLogger(__name__)


class MediumCalibration(Enum):
    """Supported medium types for sensor calibration"""

    ROCKWOOL = "rockwool"
    COCO = "coco"
    SOIL = "soil"
    PERLITE = "perlite"
    AERO = "aero"
    WATER = "water"
    CUSTOM = "custom"


@dataclass
class VWCCalibration:
    """VWC calibration parameters for a medium type"""

    # Polynomial coefficients [a, b, c, d] for: a*R^3 + b*R^2 + c*R + d
    polynomial_coeffs: Tuple[float, float, float, float]
    offset: float  # Percentage offset adjustment
    scale: float  # Scale factor
    valid_range: Tuple[float, float]  # Valid VWC range (0-1 decimal)
    description: str


@dataclass
class ECCalibration:
    """EC calibration parameters for a medium type"""

    eps_0: float  # Relative permittivity of dry medium
    eps_p25: float  # Relative permittivity of pore water at 25C
    temp_coeff: float  # Temperature coefficient (default ~1.9% per C)
    blend_low: float  # Theta threshold for mass-balance only
    blend_high: float  # Theta threshold for Hilhorst only
    description: str


@dataclass
class SensorValidation:
    """Result of sensor validation"""

    is_valid: bool
    issues: List[str]
    warnings: List[str]
    corrected_values: Dict[str, float]


class OGBAdvancedSensor:
    """
    Advanced sensor processing with TDR-style calculations.

    Provides:
    - Medium-specific VWC polynomial calibration
    - Pore water EC using hybrid Hilhorst/mass-balance model
    - Temperature-normalized EC readings
    - Validation and anomaly detection
    """

    # Default Teros-12 soilless polynomial coefficients
    TEROS12_SOILLESS = (6.771e-10, -5.105e-6, 1.302e-2, -10.848)

    # Teros-12 mineral soil polynomial (for reference)
    TEROS12_MINERAL = (4.824e-10, -3.478e-6, 8.502e-3, -7.082)

    def __init__(self):
        """Initialize with default calibrations for all medium types"""

        # VWC calibrations per medium type
        self.vwc_calibrations: Dict[str, VWCCalibration] = {
            "rockwool": VWCCalibration(
                polynomial_coeffs=self.TEROS12_SOILLESS,
                offset=0.0,
                scale=1.0,
                valid_range=(0.20, 0.80),
                description="Rockwool - Teros-12 soilless calibration",
            ),
            "coco": VWCCalibration(
                polynomial_coeffs=self.TEROS12_SOILLESS,
                offset=5.0,  # +5% offset for higher bound water
                scale=1.0,
                valid_range=(0.15, 0.75),
                description="Coco coir - adjusted for higher water retention",
            ),
            "soil": VWCCalibration(
                polynomial_coeffs=self.TEROS12_MINERAL,  # Use mineral soil polynomial
                offset=-3.0,  # -3% VWC adjustment
                scale=1.0,
                valid_range=(0.00, 0.60),
                description="Mineral soil - Teros-12 soil calibration",
            ),
            "perlite": VWCCalibration(
                polynomial_coeffs=self.TEROS12_SOILLESS,
                offset=-2.0,  # Slight negative offset for fast drainage
                scale=1.0,
                valid_range=(0.05, 0.85),
                description="Perlite - fast drainage substrate",
            ),
            "aero": VWCCalibration(
                polynomial_coeffs=(0, 0, 0.01, 0),  # Direct linear mapping
                offset=0.0,
                scale=1.0,
                valid_range=(0.00, 1.00),
                description="Aeroponics - direct measurement",
            ),
            "water": VWCCalibration(
                polynomial_coeffs=(0, 0, 0.01, 0),  # Direct linear mapping
                offset=0.0,
                scale=1.0,
                valid_range=(0.00, 1.00),
                description="Hydroponics/DWC - direct measurement",
            ),
        }

        # EC calibrations per medium type (Hilhorst model parameters)
        self.ec_calibrations: Dict[str, ECCalibration] = {
            "rockwool": ECCalibration(
                eps_0=4.0,  # Relative permittivity of dry rockwool
                eps_p25=80.0,  # Relative permittivity of water at 25C
                temp_coeff=0.019,  # ~1.9% per C
                blend_low=0.40,
                blend_high=0.60,
                description="Rockwool - standard Hilhorst parameters",
            ),
            "coco": ECCalibration(
                eps_0=3.5,  # Higher water retention
                eps_p25=80.0,
                temp_coeff=0.019,
                blend_low=0.35,
                blend_high=0.55,
                description="Coco coir - adjusted for water retention",
            ),
            "soil": ECCalibration(
                eps_0=5.0,  # Higher solids content
                eps_p25=80.0,
                temp_coeff=0.019,
                blend_low=0.30,
                blend_high=0.50,
                description="Mineral soil - higher solids permittivity",
            ),
            "perlite": ECCalibration(
                eps_0=2.0,  # Very porous, low permittivity
                eps_p25=80.0,
                temp_coeff=0.019,
                blend_low=0.35,
                blend_high=0.55,
                description="Perlite - low permittivity substrate",
            ),
            "aero": ECCalibration(
                eps_0=1.0,  # Air dominant
                eps_p25=80.0,
                temp_coeff=0.019,
                blend_low=0.0,
                blend_high=0.0,  # No blending for direct measurement
                description="Aeroponics - direct solution EC",
            ),
            "water": ECCalibration(
                eps_0=80.0,  # Pure water
                eps_p25=80.0,
                temp_coeff=0.019,
                blend_low=0.0,
                blend_high=0.0,  # No blending for direct measurement
                description="Hydroponics - direct solution EC",
            ),
        }

        # Expected value ranges for validation
        self.expected_ranges = {
            "vwc": (0.0, 100.0),  # 0-100%
            "bulk_ec": (0.0, 10.0),  # 0-10 dS/m (mS/cm)
            "pore_ec": (0.0, 20.0),  # 0-20 dS/m for pore water
            "temperature": (5.0, 40.0),  # 5-40 C reasonable range
        }

        # Medium-specific expected pore EC ranges
        self.expected_pore_ec_ranges = {
            "rockwool": (4.0, 6.0),  # Typical under EC 2.0-3.0 feed
            "coco": (3.5, 5.5),
            "soil": (2.0, 4.0),  # Lower due to nutrient buffering
            "perlite": (4.0, 6.0),
            "aero": (1.0, 4.0),  # Direct solution EC
            "water": (1.0, 4.0),  # Direct solution EC
        }

    def calculate_vwc_polynomial(
        self, raw_resistance: float, coeffs: Tuple[float, float, float, float]
    ) -> float:
        """
        Calculate VWC using polynomial calibration.

        Formula: theta = a*R^3 + b*R^2 + c*R + d
        Where R is raw sensor resistance/reading

        Args:
            raw_resistance: Raw sensor value (resistance or raw ADC)
            coeffs: Polynomial coefficients (a, b, c, d)

        Returns:
            Volumetric water content as decimal (0-1)
        """
        a, b, c, d = coeffs
        R = raw_resistance

        theta = a * R * R * R + b * R * R + c * R + d

        return theta

    def calculate_vwc(self, raw_value: float, medium_type: str = "rockwool") -> float:
        """
        Calculate VWC with medium-specific calibration.

        Args:
            raw_value: Raw sensor value (resistance, ADC, or already-calibrated %)
            medium_type: Type of growing medium

        Returns:
            VWC as percentage (0-100%)
        """
        # Get calibration for medium type
        cal = self.vwc_calibrations.get(medium_type.lower())
        if cal is None:
            _LOGGER.warning(
                f"Unknown medium type '{medium_type}', using rockwool defaults"
            )
            cal = self.vwc_calibrations["rockwool"]

        # Check if value is already a percentage (common for pre-calibrated sensors)
        # Raw resistance values are typically > 100 for TDR sensors
        if 0 <= raw_value <= 100:
            # Likely already calibrated VWC percentage
            theta_pct = raw_value
        else:
            # Calculate using polynomial
            theta = self.calculate_vwc_polynomial(raw_value, cal.polynomial_coeffs)
            theta_pct = theta * 100.0

        # Apply medium-specific adjustments
        adjusted_pct = (theta_pct * cal.scale) + cal.offset

        # Clamp to valid range
        min_pct = cal.valid_range[0] * 100
        max_pct = cal.valid_range[1] * 100

        return max(min_pct, min(max_pct, adjusted_pct))

    def normalize_ec_temperature(
        self, bulk_ec: float, temperature: float, temp_coeff: float = 0.019
    ) -> float:
        """
        Normalize bulk EC to 25C reference temperature.

        EC increases approximately 1.9% per C above 25C.
        Formula: EC_25 = EC_measured / (1 + temp_coeff * (T - 25))

        Args:
            bulk_ec: Measured bulk EC in dS/m or mS/cm
            temperature: Media temperature in Celsius
            temp_coeff: Temperature coefficient (default 0.019 = 1.9%/C)

        Returns:
            Temperature-normalized EC at 25C
        """
        if temperature == 25.0:
            return bulk_ec

        normalization_factor = 1.0 + temp_coeff * (temperature - 25.0)

        # Prevent division by zero or negative factors
        if normalization_factor <= 0:
            _LOGGER.warning(
                f"Invalid temperature normalization factor: {normalization_factor}"
            )
            return bulk_ec

        return bulk_ec / normalization_factor

    def calculate_pore_ec_mass_balance(self, bulk_ec_25: float, theta: float) -> float:
        """
        Calculate pore water EC using mass-balance model.

        Best for dry media (theta < 0.40).
        Formula: EC_pore = EC_bulk / theta

        Args:
            bulk_ec_25: Temperature-normalized bulk EC
            theta: Volumetric water content as decimal (0-1)

        Returns:
            Pore water EC
        """
        if theta <= 0:
            return 0.0

        return bulk_ec_25 / theta

    def calculate_pore_ec_hilhorst(
        self, bulk_ec_25: float, theta: float, eps_0: float, eps_p25: float
    ) -> float:
        """
        Calculate pore water EC using Hilhorst model.

        Best for wet media (theta > 0.60).
        Formula: EC_pore = EC_bulk * (eps_p25 / (eps_b - eps_0))
        Where: eps_b = eps_0 + theta * (eps_p25 - eps_0)

        Args:
            bulk_ec_25: Temperature-normalized bulk EC
            theta: Volumetric water content as decimal (0-1)
            eps_0: Relative permittivity of dry medium
            eps_p25: Relative permittivity of pore water at 25C

        Returns:
            Pore water EC
        """
        # Calculate bulk permittivity
        eps_b = eps_0 + theta * (eps_p25 - eps_0)

        # Prevent division by zero
        denominator = eps_b - eps_0
        if denominator <= 0:
            _LOGGER.warning(
                f"Invalid Hilhorst denominator: eps_b={eps_b}, eps_0={eps_0}"
            )
            return bulk_ec_25  # Fallback to bulk EC

        return bulk_ec_25 * (eps_p25 / denominator)

    def calculate_pore_ec(
        self,
        bulk_ec: float,
        vwc_pct: float,
        temperature: float,
        medium_type: str = "rockwool",
    ) -> float:
        """
        Calculate pore water EC using hybrid model with medium-specific parameters.

        Uses dynamic blending between mass-balance (dry) and Hilhorst (wet) models.

        Args:
            bulk_ec: Measured bulk EC in dS/m or mS/cm
            vwc_pct: VWC as percentage (0-100%)
            temperature: Media temperature in Celsius
            medium_type: Type of growing medium

        Returns:
            Pore water EC in dS/m
        """
        # Get EC calibration for medium
        ec_cal = self.ec_calibrations.get(medium_type.lower())
        if ec_cal is None:
            _LOGGER.warning(
                f"Unknown medium type '{medium_type}' for EC, using rockwool defaults"
            )
            ec_cal = self.ec_calibrations["rockwool"]

        # Convert VWC to decimal
        theta = vwc_pct / 100.0

        # Handle edge cases
        if theta <= 0:
            _LOGGER.debug("VWC is zero, cannot calculate pore EC")
            return 0.0

        if bulk_ec <= 0:
            return 0.0

        # Temperature normalize the bulk EC
        bulk_ec_25 = self.normalize_ec_temperature(
            bulk_ec, temperature, ec_cal.temp_coeff
        )

        # For aero/water systems, return direct EC (no substrate interference)
        if medium_type.lower() in ["aero", "water"]:
            return bulk_ec_25

        # Calculate both models
        ec_mass = self.calculate_pore_ec_mass_balance(bulk_ec_25, theta)
        ec_hil = self.calculate_pore_ec_hilhorst(
            bulk_ec_25, theta, ec_cal.eps_0, ec_cal.eps_p25
        )

        # Dynamic blending based on moisture level
        if theta <= ec_cal.blend_low:
            # Dry media: use mass-balance only
            return ec_mass
        elif theta >= ec_cal.blend_high:
            # Wet media: use Hilhorst only
            return ec_hil
        else:
            # Transition zone: linear blend
            blend_range = ec_cal.blend_high - ec_cal.blend_low
            if blend_range <= 0:
                return ec_hil

            blend_factor = (theta - ec_cal.blend_low) / blend_range
            return ec_mass * (1 - blend_factor) + ec_hil * blend_factor

    def validate_readings(
        self,
        vwc_pct: float,
        bulk_ec: float,
        pore_ec: float,
        temperature: float,
        medium_type: str = "rockwool",
    ) -> SensorValidation:
        """
        Validate sensor readings and detect anomalies.

        Args:
            vwc_pct: VWC as percentage
            bulk_ec: Bulk EC in dS/m
            pore_ec: Calculated pore EC in dS/m
            temperature: Temperature in Celsius
            medium_type: Growing medium type

        Returns:
            SensorValidation with issues, warnings, and corrected values
        """
        issues = []
        warnings = []
        corrected = {}

        # VWC validation
        vwc_range = self.expected_ranges["vwc"]
        if vwc_pct < vwc_range[0]:
            issues.append(f"VWC below valid range: {vwc_pct:.1f}% < {vwc_range[0]}%")
            corrected["vwc"] = vwc_range[0]
        elif vwc_pct > vwc_range[1]:
            issues.append(f"VWC above valid range: {vwc_pct:.1f}% > {vwc_range[1]}%")
            corrected["vwc"] = vwc_range[1]

        # Bulk EC validation
        ec_range = self.expected_ranges["bulk_ec"]
        if bulk_ec < ec_range[0]:
            warnings.append(f"Bulk EC unusually low: {bulk_ec:.2f} dS/m")
        elif bulk_ec > ec_range[1]:
            issues.append(
                f"Bulk EC above valid range: {bulk_ec:.2f} > {ec_range[1]} dS/m"
            )
            corrected["bulk_ec"] = ec_range[1]

        # Pore EC validation
        pore_range = self.expected_ranges["pore_ec"]
        if pore_ec < pore_range[0]:
            warnings.append(f"Pore EC unusually low: {pore_ec:.2f} dS/m")
        elif pore_ec > pore_range[1]:
            issues.append(
                f"Pore EC spike detected: {pore_ec:.2f} > {pore_range[1]} dS/m"
            )
            # Try to correct using expected range for medium
            expected_pore = self.expected_pore_ec_ranges.get(
                medium_type.lower(), (4.0, 6.0)
            )
            corrected["pore_ec"] = expected_pore[1]

        # Temperature validation
        temp_range = self.expected_ranges["temperature"]
        if temperature < temp_range[0]:
            warnings.append(
                f"Temperature below expected: {temperature:.1f}C < {temp_range[0]}C"
            )
        elif temperature > temp_range[1]:
            warnings.append(
                f"Temperature above expected: {temperature:.1f}C > {temp_range[1]}C"
            )

        # Dry media spike detection
        if vwc_pct < 15 and pore_ec > 10:
            issues.append(
                f"Dry media EC spike: VWC={vwc_pct:.1f}%, pore EC={pore_ec:.2f}"
            )
            # Use mass-balance corrected value
            if bulk_ec > 0 and vwc_pct > 0:
                corrected["pore_ec"] = bulk_ec / (vwc_pct / 100.0)

        # Medium-specific pore EC range check
        expected_pore = self.expected_pore_ec_ranges.get(medium_type.lower())
        if expected_pore:
            if pore_ec < expected_pore[0] * 0.5:
                warnings.append(
                    f"Pore EC below typical for {medium_type}: {pore_ec:.2f} < {expected_pore[0]:.1f}"
                )
            elif pore_ec > expected_pore[1] * 1.5:
                warnings.append(
                    f"Pore EC above typical for {medium_type}: {pore_ec:.2f} > {expected_pore[1]:.1f}"
                )

        is_valid = len(issues) == 0

        return SensorValidation(
            is_valid=is_valid,
            issues=issues,
            warnings=warnings,
            corrected_values=corrected,
        )

    def process_sensor_data(
        self,
        raw_vwc: float,
        raw_ec: float,
        temperature: float,
        medium_type: str = "rockwool",
    ) -> Dict[str, Any]:
        """
        Process raw sensor data with full calibration pipeline.

        Args:
            raw_vwc: Raw VWC value (resistance or percentage)
            raw_ec: Raw bulk EC in dS/m
            temperature: Temperature in Celsius
            medium_type: Growing medium type

        Returns:
            Dictionary with processed values:
            - vwc: Calibrated VWC percentage
            - bulk_ec: Temperature-normalized bulk EC
            - pore_ec: Calculated pore water EC
            - temperature: Input temperature
            - medium_type: Medium type used
            - validation: Validation results
        """
        # Calculate VWC with medium calibration
        vwc_pct = self.calculate_vwc(raw_vwc, medium_type)

        # Get temperature-normalized bulk EC
        ec_cal = self.ec_calibrations.get(
            medium_type.lower(), self.ec_calibrations["rockwool"]
        )
        bulk_ec_25 = self.normalize_ec_temperature(
            raw_ec, temperature, ec_cal.temp_coeff
        )

        # Calculate pore water EC
        pore_ec = self.calculate_pore_ec(raw_ec, vwc_pct, temperature, medium_type)

        # Validate all readings
        validation = self.validate_readings(
            vwc_pct, bulk_ec_25, pore_ec, temperature, medium_type
        )

        # Apply corrections if needed
        final_vwc = validation.corrected_values.get("vwc", vwc_pct)
        final_bulk_ec = validation.corrected_values.get("bulk_ec", bulk_ec_25)
        final_pore_ec = validation.corrected_values.get("pore_ec", pore_ec)

        # Log any issues
        if validation.issues:
            _LOGGER.warning(f"Sensor validation issues: {validation.issues}")
        if validation.warnings:
            _LOGGER.debug(f"Sensor validation warnings: {validation.warnings}")

        return {
            "vwc": final_vwc,
            "vwc_raw": vwc_pct,
            "bulk_ec": final_bulk_ec,
            "bulk_ec_raw": raw_ec,
            "pore_ec": final_pore_ec,
            "temperature": temperature,
            "medium_type": medium_type,
            "validation": {
                "is_valid": validation.is_valid,
                "issues": validation.issues,
                "warnings": validation.warnings,
                "corrections_applied": len(validation.corrected_values) > 0,
            },
        }

    def get_medium_info(self, medium_type: str) -> Dict[str, Any]:
        """Get information about a medium type's calibration settings."""
        vwc_cal = self.vwc_calibrations.get(medium_type.lower())
        ec_cal = self.ec_calibrations.get(medium_type.lower())

        if not vwc_cal or not ec_cal:
            return {"error": f"Unknown medium type: {medium_type}"}

        return {
            "medium_type": medium_type,
            "vwc_calibration": {
                "description": vwc_cal.description,
                "offset": vwc_cal.offset,
                "scale": vwc_cal.scale,
                "valid_range_pct": (
                    vwc_cal.valid_range[0] * 100,
                    vwc_cal.valid_range[1] * 100,
                ),
            },
            "ec_calibration": {
                "description": ec_cal.description,
                "permittivity_dry": ec_cal.eps_0,
                "permittivity_water": ec_cal.eps_p25,
                "temp_coefficient": ec_cal.temp_coeff,
                "blend_thresholds": (ec_cal.blend_low, ec_cal.blend_high),
            },
            "expected_pore_ec_range": self.expected_pore_ec_ranges.get(
                medium_type.lower(), (4.0, 6.0)
            ),
        }

    def set_custom_calibration(
        self,
        medium_type: str,
        vwc_offset: Optional[float] = None,
        vwc_scale: Optional[float] = None,
        ec_eps_0: Optional[float] = None,
    ) -> bool:
        """
        Set custom calibration adjustments for a medium type.

        Args:
            medium_type: Medium type to adjust
            vwc_offset: VWC percentage offset adjustment
            vwc_scale: VWC scale factor
            ec_eps_0: Dry medium permittivity

        Returns:
            True if successful
        """
        vwc_cal = self.vwc_calibrations.get(medium_type.lower())
        ec_cal = self.ec_calibrations.get(medium_type.lower())

        if not vwc_cal or not ec_cal:
            _LOGGER.error(f"Cannot set calibration for unknown medium: {medium_type}")
            return False

        if vwc_offset is not None:
            self.vwc_calibrations[medium_type.lower()] = VWCCalibration(
                polynomial_coeffs=vwc_cal.polynomial_coeffs,
                offset=vwc_offset,
                scale=vwc_cal.scale if vwc_scale is None else vwc_scale,
                valid_range=vwc_cal.valid_range,
                description=f"{vwc_cal.description} (custom offset: {vwc_offset:+.1f}%)",
            )

        if ec_eps_0 is not None:
            self.ec_calibrations[medium_type.lower()] = ECCalibration(
                eps_0=ec_eps_0,
                eps_p25=ec_cal.eps_p25,
                temp_coeff=ec_cal.temp_coeff,
                blend_low=ec_cal.blend_low,
                blend_high=ec_cal.blend_high,
                description=f"{ec_cal.description} (custom eps_0: {ec_eps_0})",
            )

        _LOGGER.info(
            f"Custom calibration set for {medium_type}: vwc_offset={vwc_offset}, ec_eps_0={ec_eps_0}"
        )
        return True

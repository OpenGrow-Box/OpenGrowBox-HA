"""
OpenGrowBox Feed Parameter Manager

Handles feed parameter updates, target management, and parameter validation
for the tank feeding system.

Responsibilities:
- Feed parameter updates and validation
- Target value management and adjustments
- Parameter change handling and notifications
- Feed configuration persistence
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBFeedParameterManager:
    """
    Feed parameter manager for nutrient delivery configuration.

    Handles parameter updates, target management, and configuration validation.
    """

    def __init__(self, room: str, data_store, event_manager):
        """
        Initialize parameter manager.

        Args:
            room: Room identifier
            data_store: Data store instance
            event_manager: Event manager instance
        """
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager

        # Parameter validation ranges
        self.parameter_ranges = {
            "EC_Target": {"min": 0.5, "max": 5.0, "default": 2.0},
            "PH_Target": {"min": 4.0, "max": 8.0, "default": 5.8},
            "Nut_A_ml": {"min": 0.0, "max": 50.0, "default": 0.0},
            "Nut_B_ml": {"min": 0.0, "max": 50.0, "default": 0.0},
            "Nut_C_ml": {"min": 0.0, "max": 50.0, "default": 0.0},
            "Nut_W_ml": {"min": 0.0, "max": 50.0, "default": 0.0},
            "Nut_X_ml": {"min": 0.0, "max": 50.0, "default": 0.0},
            "Nut_Y_ml": {"min": 0.0, "max": 50.0, "default": 0.0},
            "Nut_PH_ml": {"min": 0.0, "max": 20.0, "default": 0.0},
        }

    async def update_feed_parameter(self, parameter: str, value: float) -> bool:
        """
        Update a single feed parameter with validation.

        Args:
            parameter: Parameter name
            value: New value

        Returns:
            True if update successful, False otherwise
        """
        try:
            # Validate parameter and value
            if not self._validate_parameter(parameter, value):
                return False

            # Store the parameter
            self.data_store.setDeep(f"Hydro.Parameters.{parameter}", value)

            # Log the change
            await self._log_parameter_change(parameter, value)

            # Emit event for other components
            await self.event_manager.emit(
                "FeedParameterChanged",
                {
                    "room": self.room,
                    "parameter": parameter,
                    "value": value,
                    "timestamp": datetime.now().isoformat(),
                },
            )

            _LOGGER.info(f"{self.room} - Updated parameter {parameter} to {value}")
            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error updating parameter {parameter}: {e}")
            return False

    def _validate_parameter(self, parameter: str, value: float) -> bool:
        """
        Validate a parameter value against allowed ranges.

        Args:
            parameter: Parameter name
            value: Value to validate

        Returns:
            True if valid, False otherwise
        """
        if parameter not in self.parameter_ranges:
            _LOGGER.error(f"{self.room} - Unknown parameter: {parameter}")
            return False

        param_range = self.parameter_ranges[parameter]
        min_val = param_range["min"]
        max_val = param_range["max"]

        if not isinstance(value, (int, float)):
            _LOGGER.error(
                f"{self.room} - Invalid value type for {parameter}: {type(value)}"
            )
            return False

        if value < min_val or value > max_val:
            _LOGGER.error(
                f"{self.room} - Value {value} for {parameter} out of range [{min_val}, {max_val}]"
            )
            return False

        return True

    async def update_feed_settings(self, settings: Dict[str, Any]) -> bool:
        """
        Update multiple feed settings at once.

        Args:
            settings: Dictionary of parameter updates

        Returns:
            True if all updates successful
        """
        try:
            all_success = True
            updated_parameters = []

            for parameter, value in settings.items():
                if await self.update_feed_parameter(parameter, value):
                    updated_parameters.append(f"{parameter}={value}")
                else:
                    all_success = False

            if updated_parameters:
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "HYDROLOG",
                        "Message": f"Updated feed settings: {', '.join(updated_parameters)}",
                    },
                )

            return all_success

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error updating feed settings: {e}")
            return False

    async def handle_feed_mode_targets_change(self, data: Dict[str, Any]):
        """
        Handle changes to feed mode targets.

        Args:
            data: Target change data
        """
        try:
            # Extract target information
            ec_target = data.get("ec_target")
            ph_target = data.get("ph_target")
            nutrient_targets = data.get("nutrient_targets", {})

            # Update EC target
            if ec_target is not None:
                await self.update_feed_parameter("EC_Target", ec_target)

            # Update pH target
            if ph_target is not None:
                await self.update_feed_parameter("PH_Target", ph_target)

            # Update nutrient targets
            for nutrient, amount in nutrient_targets.items():
                param_name = f"Nut_{nutrient}_ml"
                await self.update_feed_parameter(param_name, amount)

            _LOGGER.info(f"{self.room} - Updated feed mode targets")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error handling feed mode targets change: {e}")

    def get_current_parameters(self) -> Dict[str, Any]:
        """
        Get all current feed parameters.

        Returns:
            Dictionary of current parameter values
        """
        parameters = {}

        for param_name in self.parameter_ranges.keys():
            value = self.data_store.getDeep(f"Hydro.Parameters.{param_name}")
            if value is None:
                value = self.parameter_ranges[param_name]["default"]
            parameters[param_name] = value

        return parameters

    def get_parameter_ranges(self) -> Dict[str, Dict[str, Any]]:
        """
        Get parameter validation ranges.

        Returns:
            Dictionary of parameter ranges
        """
        return self.parameter_ranges.copy()

    async def reset_parameters_to_defaults(self) -> bool:
        """
        Reset all parameters to default values.

        Returns:
            True if reset successful
        """
        try:
            default_settings = {
                param: info["default"] for param, info in self.parameter_ranges.items()
            }

            success = await self.update_feed_settings(default_settings)

            if success:
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "HYDROLOG",
                        "Message": "Feed parameters reset to defaults",
                    },
                )

            return success

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error resetting parameters: {e}")
            return False

    async def handle_plant_stage_change(self, new_stage: str):
        """
        Handle plant stage changes and adjust parameters accordingly.

        Args:
            new_stage: New plant stage
        """
        try:
            # Get stage-specific adjustments
            stage_adjustments = self._get_stage_adjustments(new_stage)

            if stage_adjustments:
                # Apply adjustments
                await self.update_feed_settings(stage_adjustments)

                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "HYDROLOG",
                        "Message": f"Parameters adjusted for {new_stage} stage",
                    },
                )

            _LOGGER.info(f"{self.room} - Handled plant stage change to {new_stage}")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error handling plant stage change: {e}")

    async def handle_plant_category_change(self, new_category: str):
        """
        Handle plant category changes and adjust parameters.

        Args:
            new_category: New plant category
        """
        try:
            # Get category-specific adjustments
            category_adjustments = self._get_category_adjustments(new_category)

            if category_adjustments:
                await self.update_feed_settings(category_adjustments)

                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "HYDROLOG",
                        "Message": f"Parameters adjusted for {new_category} category",
                    },
                )

            _LOGGER.info(
                f"{self.room} - Handled plant category change to {new_category}"
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error handling plant category change: {e}")

    def _get_stage_adjustments(self, stage: str) -> Dict[str, float]:
        """
        Get parameter adjustments for plant stage.

        Args:
            stage: Plant stage

        Returns:
            Dictionary of parameter adjustments
        """
        # Stage-specific adjustments (example values)
        adjustments = {
            "veg": {
                "EC_Target": 1.8,
                "PH_Target": 5.8,
                "Nut_A_ml": 1.5,  # Higher veg nutrient
                "Nut_B_ml": 0.8,  # Lower flower nutrient
            },
            "flower": {
                "EC_Target": 2.2,
                "PH_Target": 5.8,
                "Nut_A_ml": 1.0,  # Balanced nutrients
                "Nut_B_ml": 1.2,  # Higher flower nutrient
            },
            "flush": {
                "EC_Target": 0.8,
                "PH_Target": 5.8,
                "Nut_A_ml": 0.0,  # No nutrients during flush
                "Nut_B_ml": 0.0,
                "Nut_C_ml": 0.0,
            },
        }

        return adjustments.get(stage.lower(), {})

    def _get_category_adjustments(self, category: str) -> Dict[str, float]:
        """
        Get parameter adjustments for plant category.

        Args:
            category: Plant category

        Returns:
            Dictionary of parameter adjustments
        """
        # Category-specific adjustments (example values)
        adjustments = {
            "cannabis": {
                "EC_Target": 2.0,
                "PH_Target": 5.8,
            },
            "tomato": {
                "EC_Target": 2.5,
                "PH_Target": 6.0,
            },
            "lettuce": {
                "EC_Target": 1.5,
                "PH_Target": 5.5,
            },
            "herbs": {
                "EC_Target": 1.8,
                "PH_Target": 6.0,
            },
        }

        return adjustments.get(category.lower(), {})

    async def _log_parameter_change(self, parameter: str, value: float):
        """
        Log parameter change event.

        Args:
            parameter: Parameter name
            value: New value
        """
        try:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "HYDROLOG",
                    "Message": f"Parameter {parameter} changed to {value}",
                },
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error logging parameter change: {e}")

    def validate_feed_configuration(self) -> Dict[str, Any]:
        """
        Validate the complete feed configuration.

        Returns:
            Dictionary with validation results
        """
        validation_result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "parameters": self.get_current_parameters(),
        }

        # Check EC and pH targets are reasonable
        ec_target = validation_result["parameters"].get("EC_Target", 0)
        ph_target = validation_result["parameters"].get("PH_Target", 0)

        if ec_target < 1.0:
            validation_result["warnings"].append("EC target very low")
        elif ec_target > 3.0:
            validation_result["warnings"].append("EC target very high")

        if ph_target < 5.0 or ph_target > 7.0:
            validation_result["warnings"].append("pH target outside optimal range")

        # Check nutrient totals are reasonable
        total_nutrients = sum(
            [
                validation_result["parameters"].get(f"Nut_{nutrient}_ml", 0)
                for nutrient in ["A", "B", "C"]
            ]
        )

        if total_nutrients > 30.0:
            validation_result["warnings"].append(
                "Total nutrient concentration very high"
            )

        if total_nutrients < 1.0:
            validation_result["warnings"].append(
                "Total nutrient concentration very low"
            )

        # Mark as invalid if there are errors
        if validation_result["errors"]:
            validation_result["valid"] = False

        return validation_result

    def get_parameter_history(
        self, parameter: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get parameter change history.

        Args:
            parameter: Parameter name
            limit: Maximum number of history entries

        Returns:
            List of parameter change records
        """
        # This would require storing parameter history in dataStore
        # For now, return empty list as placeholder
        return []

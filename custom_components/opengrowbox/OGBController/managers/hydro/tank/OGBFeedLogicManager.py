"""
OpenGrowBox Feed Logic Manager

Handles automatic and manual feeding decisions, nutrient dosing logic,
and feeding schedule management for the tank feeding system.

Responsibilities:
- Automatic feeding cycle coordination
- Manual feeding mode handling
- Nutrient requirement calculations
- pH and EC adjustment decisions
- Feeding schedule and timing logic
"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class FeedMode(Enum):
    """Feeding operation modes."""

    DISABLED = "Disabled"
    AUTOMATIC = "Automatic"
    OWN_PLAN = "Own-Plan"
    CONFIG = "Config"


class OGBFeedLogicManager:
    """
    Feed logic manager for nutrient delivery decisions.

    Handles automatic/manual feeding decisions, nutrient dosing calculations,
    and feeding schedule management.
    """

    def __init__(self, room: str, data_store, event_manager):
        """
        Initialize feed logic manager.

        Args:
            room: Room identifier
            data_store: Data store instance
            event_manager: Event manager instance
        """
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager

        # Feeding settings - adjusted for smaller, more frequent doses
        self.feed_check_interval = 300  # 5 minutes
        self.emergency_feed_threshold = 0.15  # 15% deviation triggers emergency
        self.max_daily_feeds = 12  # Maximum feeds per day (increased)
        self.min_feed_interval = 1800  # 30 minutes between feeds (reduced from 2 hours)

        # Feed state tracking
        self.last_feed_time = None
        self.daily_feed_count = 0
        self.feed_mode = FeedMode.AUTOMATIC

    async def handle_feed_mode_change(self, feed_mode: str):
        """
        Handle changes in feeding mode.

        Args:
            feed_mode: New feeding mode string
        """
        try:
            # Parse mode
            try:
                self.feed_mode = FeedMode(feed_mode)
            except ValueError:
                _LOGGER.warning(
                    f"{self.room} - Invalid feed mode: {feed_mode}, defaulting to AUTOMATIC"
                )
                self.feed_mode = FeedMode.AUTOMATIC

            # Handle mode-specific initialization
            if self.feed_mode == FeedMode.AUTOMATIC:
                await self._handle_automatic_mode()
            elif self.feed_mode == FeedMode.OWN_PLAN:
                await self._handle_own_plan_mode()
            elif self.feed_mode == FeedMode.DISABLED:
                await self._handle_disabled_mode()
            elif self.feed_mode == FeedMode.CONFIG:
                # Config mode - no active feeding
                pass

            _LOGGER.info(f"{self.room} - Feed mode changed to: {self.feed_mode.value}")

            # Emit event
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "HYDROLOG",
                    "Message": f"Feed mode changed to {self.feed_mode.value}",
                },
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error handling feed mode change: {e}")

    async def _handle_automatic_mode(self):
        """
        Initialize automatic feeding mode.
        """
        # Update automatic targets based on current plant stage
        await self._update_automatic_targets()

        # Start feed checking task if not already running
        # (This would be handled by the main manager)

        _LOGGER.debug(f"{self.room} - Automatic feeding mode initialized")

    async def _handle_own_plan_mode(self):
        """
        Initialize own plan feeding mode.
        """
        # Load custom feeding plan from dataStore
        custom_plan = self.data_store.getDeep("Hydro.Feeding.OwnPlan")

        if custom_plan:
            _LOGGER.info(
                f"{self.room} - Custom feeding plan loaded: {len(custom_plan)} entries"
            )
        else:
            _LOGGER.warning(
                f"{self.room} - No custom feeding plan found, using defaults"
            )

        _LOGGER.debug(f"{self.room} - Own plan feeding mode initialized")

    async def _handle_disabled_mode(self):
        """
        Handle disabled feeding mode.
        """
        # Stop any active feeding tasks
        # (This would be handled by the main manager)

        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "HYDROLOG",
                "Message": "Automatic feeding disabled",
            },
        )

        _LOGGER.debug(f"{self.room} - Feeding disabled")

    async def _update_automatic_targets(self):
        """
        Update automatic feeding targets based on plant stage and category.
        """
        try:
            # Get current plant stage and category
            plant_stage = self.data_store.getDeep("Plant.stage") or "veg"
            plant_category = self.data_store.getDeep("Plant.category") or "General"

            # Get plant stage configuration
            stage_config = self._get_plant_stage_config(plant_stage, plant_category)

            if stage_config:
                # Update targets in dataStore
                self.data_store.setDeep(
                    "Hydro.Targets.EC", stage_config.get("ec_target", 2.0)
                )
                self.data_store.setDeep(
                    "Hydro.Targets.pH", stage_config.get("ph_target", 5.8)
                )

                # Update nutrient ratios
                nutrient_ratios = stage_config.get("nutrient_ratios", {})
                for nutrient, ratio in nutrient_ratios.items():
                    self.data_store.setDeep(f"Hydro.Nutrients.{nutrient}", ratio)

                _LOGGER.debug(
                    f"{self.room} - Automatic targets updated for {plant_stage}/{plant_category}"
                )
            else:
                _LOGGER.warning(
                    f"{self.room} - No stage config found for {plant_stage}/{plant_category}"
                )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error updating automatic targets: {e}")

    def _get_plant_stage_config(
        self, stage: str, category: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get plant stage configuration for specific category.

        Args:
            stage: Plant stage (veg, flower, etc.)
            category: Plant category

        Returns:
            Stage configuration dictionary or None
        """
        # This would contain the plant stage configurations
        # For now, return basic defaults
        base_configs = {
            "veg": {
                "ec_target": 1.8,
                "ph_target": 5.8,
                "nutrient_ratios": {"A": 1.0, "B": 0.5, "micro": 0.3},
            },
            "flower": {
                "ec_target": 2.2,
                "ph_target": 5.8,
                "nutrient_ratios": {"A": 0.7, "B": 1.0, "micro": 0.4},
            },
        }

        return base_configs.get(stage.lower())

    async def check_if_feed_needed(self, sensor_data: Dict[str, Any]) -> bool:
        """
        Check if feeding is needed based on sensor data.

        Args:
            sensor_data: Current sensor readings

        Returns:
            True if feeding is needed
        """
        try:
            # Check daily feed limit
            if self.daily_feed_count >= self.max_daily_feeds:
                _LOGGER.debug(
                    f"{self.room} - Daily feed limit reached ({self.max_daily_feeds})"
                )
                return False

            # Check minimum interval between feeds
            if self.last_feed_time:
                time_since_last_feed = datetime.now() - self.last_feed_time
                if time_since_last_feed.seconds < self.min_feed_interval:
                    _LOGGER.debug(
                        f"{self.room} - Too soon since last feed ({time_since_last_feed.seconds}s < {self.min_feed_interval}s)"
                    )
                    return False

            # Check sensor data for feeding need
            return await self._check_ranges_and_feed()

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error checking if feed needed: {e}")
            return False

    async def _check_ranges_and_feed(self) -> bool:
        """
        Check if nutrient levels are within acceptable ranges and determine feeding needs.
        Uses proportional dosing based on deviation from target.

        Returns:
            True if feeding action was performed
        """
        try:
            current_ec = self.data_store.getDeep("Hydro.ec_current")
            current_ph = self.data_store.getDeep("Hydro.ph_current")

            target_ec = self.data_store.getDeep("Hydro.Targets.EC") or 2.0
            target_ph = self.data_store.getDeep("Hydro.Targets.pH") or 5.8

            # Calculate proportional adjustments needed
            ec_adjustment = self._calculate_ec_adjustment(current_ec, target_ec)
            ph_adjustment = self._calculate_ph_adjustment(current_ph, target_ph)

            # Check if any adjustment is needed
            needs_feeding = (
                ec_adjustment.get('nutrients_needed', False) or
                ph_adjustment.get('ph_down_needed', False) or
                ph_adjustment.get('ph_up_needed', False)
            )

            if needs_feeding:
                # Check rate limiting
                if self.last_feed_time:
                    time_since_last_feed = datetime.now() - self.last_feed_time
                    if time_since_last_feed.seconds < self.min_feed_interval:
                        _LOGGER.debug(
                            f"{self.room} - Skipping feed: too soon since last feed "
                            f"({time_since_last_feed.seconds}s < {self.min_feed_interval}s)"
                        )
                        return False

                # Perform the feeding action
                success = await self._perform_proportional_feeding(ec_adjustment, ph_adjustment)
                if success:
                    self.last_feed_time = datetime.now()
                    self.daily_feed_count += 1
                    _LOGGER.info(
                        f"{self.room} - Proportional feeding completed: "
                        f"EC nutrients={ec_adjustment.get('nutrient_dose_ml', 0):.2f}ml, "
                        f"pH down={ph_adjustment.get('ph_down_dose_ml', 0):.2f}ml, "
                        f"pH up={ph_adjustment.get('ph_up_dose_ml', 0):.2f}ml"
                    )
                    return True

            return False

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error in proportional feeding: {e}")
            return False

    def _calculate_ec_adjustment(self, current_ec: Optional[float], target_ec: float) -> Dict[str, Any]:
        """
        Calculate proportional nutrient adjustment needed for EC correction.

        Returns:
            Dict with 'nutrients_needed' and 'nutrient_dose_ml' keys
        """
        if current_ec is None:
            return {'nutrients_needed': False, 'nutrient_dose_ml': 0.0}

        deviation = abs(current_ec - target_ec) / target_ec

        # Dead zone - don't adjust for small deviations
        if deviation < 0.03:  # 3% tolerance
            return {'nutrients_needed': False, 'nutrient_dose_ml': 0.0}

        # Proportional dosing: more deviation = more nutrients
        # Base dose for 5% deviation = 2.5ml per nutrient type
        base_dose_per_5_percent = 2.5
        dose_multiplier = deviation / 0.05  # Normalize to 5% deviation
        nutrient_dose_ml = min(base_dose_per_5_percent * dose_multiplier, 10.0)  # Cap at 10ml

        return {
            'nutrients_needed': True,
            'nutrient_dose_ml': nutrient_dose_ml
        }

    def _calculate_ph_adjustment(self, current_ph: Optional[float], target_ph: float) -> Dict[str, Any]:
        """
        Calculate proportional pH adjustment needed.

        Returns:
            Dict with 'ph_down_needed', 'ph_up_needed', and dose amounts
        """
        if current_ph is None:
            return {
                'ph_down_needed': False,
                'ph_up_needed': False,
                'ph_down_dose_ml': 0.0,
                'ph_up_dose_ml': 0.0
            }

        deviation = current_ph - target_ph

        # Dead zone - don't adjust for small deviations
        if abs(deviation) < 0.1:  # 0.1 pH tolerance
            return {
                'ph_down_needed': False,
                'ph_up_needed': False,
                'ph_down_dose_ml': 0.0,
                'ph_up_dose_ml': 0.0
            }

        # Proportional dosing: more deviation = more pH adjustment
        # Base dose for 0.2 pH deviation = 1.0ml
        base_dose_per_0_2_ph = 1.0
        dose_multiplier = abs(deviation) / 0.2
        ph_dose_ml = min(base_dose_per_0_2_ph * dose_multiplier, 3.0)  # Cap at 3ml

        if deviation > 0:  # pH too high, need pH down
            return {
                'ph_down_needed': True,
                'ph_up_needed': False,
                'ph_down_dose_ml': ph_dose_ml,
                'ph_up_dose_ml': 0.0
            }
        else:  # pH too low, need pH up
            return {
                'ph_down_needed': False,
                'ph_up_needed': True,
                'ph_down_dose_ml': 0.0,
                'ph_up_dose_ml': ph_dose_ml
            }

    async def _perform_proportional_feeding(self, ec_adjustment: Dict[str, Any], ph_adjustment: Dict[str, Any]) -> bool:
        """
        Perform the actual proportional feeding based on calculated adjustments.
        """
        try:
            # Feed nutrients if needed
            if ec_adjustment.get('nutrients_needed', False):
                nutrient_dose = ec_adjustment['nutrient_dose_ml']
                await self.event_manager.emit("DoseNutrients", {'dose_ml': nutrient_dose})
                _LOGGER.debug(f"{self.room} - Dosed {nutrient_dose:.2f}ml nutrients")

            # Adjust pH if needed
            if ph_adjustment.get('ph_down_needed', False):
                ph_down_dose = ph_adjustment['ph_down_dose_ml']
                await self.event_manager.emit("DosePHDown", {'dose_ml': ph_down_dose})
                _LOGGER.debug(f"{self.room} - Dosed {ph_down_dose:.2f}ml pH down")

            if ph_adjustment.get('ph_up_needed', False):
                ph_up_dose = ph_adjustment['ph_up_dose_ml']
                await self.event_manager.emit("DosePHUp", {'dose_ml': ph_up_dose})
                _LOGGER.debug(f"{self.room} - Dosed {ph_up_dose:.2f}ml pH up")

            return True

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error performing proportional feeding: {e}")
            return False

    def _check_ec_needs_adjustment(
        self, current_ec: Optional[float], target_ec: float
    ) -> bool:
        """
        Check if EC needs adjustment.

        Args:
            current_ec: Current EC reading
            target_ec: Target EC value

        Returns:
            True if EC adjustment needed
        """
        if current_ec is None:
            return False

        deviation = abs(current_ec - target_ec) / target_ec

        # Emergency threshold (10% deviation)
        if deviation >= self.emergency_feed_threshold:
            return True

        # Normal adjustment threshold (5% deviation)
        if deviation >= 0.05:
            return True

        return False

    def _check_ph_needs_adjustment(
        self, current_ph: Optional[float], target_ph: float
    ) -> bool:
        """
        Check if pH needs adjustment.

        Args:
            current_ph: Current pH reading
            target_ph: Target pH value

        Returns:
            True if pH adjustment needed
        """
        if current_ph is None:
            return False

        deviation = abs(current_ph - target_ph)

        # pH tolerance is tighter (±0.2)
        if deviation >= 0.2:
            return True

        return False

    def calculate_nutrient_dose(
        self, nutrient_ml_per_liter: float, reservoir_volume: float = 50.0
    ) -> float:
        """
        Calculate nutrient dose based on concentration and reservoir volume.

        Args:
            nutrient_ml_per_liter: Desired ml per liter
            reservoir_volume: Reservoir volume in liters

        Returns:
            Dose in ml
        """
        return nutrient_ml_per_liter * reservoir_volume

    def should_dose_ph_down(
        self, current_ph: Optional[float], target_ph: float
    ) -> bool:
        """
        Determine if pH down dosing is needed.

        Args:
            current_ph: Current pH reading
            target_ph: Target pH value

        Returns:
            True if pH down dosing needed
        """
        if current_ph is None:
            return False

        return current_ph > target_ph + 0.1  # Small hysteresis

    def should_dose_ph_up(self, current_ph: Optional[float], target_ph: float) -> bool:
        """
        Determine if pH up dosing is needed.

        Args:
            current_ph: Current pH reading
            target_ph: Target pH value

        Returns:
            True if pH up dosing needed
        """
        if current_ph is None:
            return False

        return current_ph < target_ph - 0.1  # Small hysteresis

    def should_dose_nutrients(
        self, current_ec: Optional[float], target_ec: float
    ) -> bool:
        """
        Determine if nutrient dosing is needed.

        Args:
            current_ec: Current EC reading
            target_ec: Target EC value

        Returns:
            True if nutrient dosing needed
        """
        if current_ec is None:
            return False

        return current_ec < target_ec * 0.95  # 5% below target

    def should_dilute_ec(self, current_ec: Optional[float], target_ec: float) -> bool:
        """
        Determine if EC dilution (water addition) is needed.

        Args:
            current_ec: Current EC reading
            target_ec: Target EC value

        Returns:
            True if dilution needed
        """
        if current_ec is None:
            return False

        return current_ec > target_ec * 1.05  # 5% above target

    async def handle_feed_update(self, payload: Dict[str, Any]):
        """
        Handle feed update events.

        Args:
            payload: Feed update payload
        """
        try:
            # Update last feed time and daily count
            self.last_feed_time = datetime.now()
            self.daily_feed_count += 1

            # Reset daily count at midnight
            await self._check_daily_reset()

            # Log the feeding event
            await self._log_feed_event(payload)

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error handling feed update: {e}")

    async def _check_daily_reset(self):
        """
        Check if daily feed count should be reset (midnight).
        """
        try:
            now = datetime.now()
            if now.hour == 0 and now.minute < 5:  # Reset around midnight
                self.daily_feed_count = 0
                _LOGGER.debug(f"{self.room} - Daily feed count reset")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error checking daily reset: {e}")

    async def _log_feed_event(self, payload: Dict[str, Any]):
        """
        Log feeding event.

        Args:
            payload: Feed event payload
        """
        try:
            # Extract feed details
            ec_before = payload.get("ec_before")
            ec_after = payload.get("ec_after")
            ph_before = payload.get("ph_before")
            ph_after = payload.get("ph_after")
            nutrients_dosed = payload.get("nutrients", {})

            # Format values safely, handling None values
            ec_before_str = f"{ec_before:.2f}" if ec_before is not None else "N/A"
            ec_after_str = f"{ec_after:.2f}" if ec_after is not None else "N/A"
            ph_before_str = f"{ph_before:.1f}" if ph_before is not None else "N/A"
            ph_after_str = f"{ph_after:.1f}" if ph_after is not None else "N/A"

            message = f"Feed completed - EC: {ec_before_str}→{ec_after_str}, pH: {ph_before_str}→{ph_after_str}"

            if nutrients_dosed:
                nutrient_str = ", ".join(
                    [f"{k}:{v:.1f}ml" if v is not None else f"{k}:N/A" for k, v in nutrients_dosed.items()]
                )
                message += f", Nutrients: {nutrient_str}"

            await self.event_manager.emit(
                "LogForClient",
                {"Name": self.room, "Type": "HYDROLOG", "Message": message},
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error logging feed event: {e}")

    def get_feeding_status(self) -> Dict[str, Any]:
        """
        Get comprehensive feeding status.

        Returns:
            Dictionary with feeding status information
        """
        return {
            "feed_mode": self.feed_mode.value if self.feed_mode else "Unknown",
            "last_feed_time": (
                self.last_feed_time.isoformat() if self.last_feed_time else None
            ),
            "daily_feed_count": self.daily_feed_count,
            "max_daily_feeds": self.max_daily_feeds,
            "feed_needed": None,  # Would be calculated based on sensor data
            "targets": {
                "ec": self.data_store.getDeep("Hydro.Targets.EC"),
                "ph": self.data_store.getDeep("Hydro.Targets.pH"),
            },
            "current_values": {
                "ec": self.data_store.getDeep("Hydro.ec_current"),
                "ph": self.data_store.getDeep("Hydro.ph_current"),
            },
        }

    def reset_daily_feed_count(self):
        """
        Manually reset the daily feed count.
        """
        self.daily_feed_count = 0
        _LOGGER.info(f"{self.room} - Daily feed count manually reset")

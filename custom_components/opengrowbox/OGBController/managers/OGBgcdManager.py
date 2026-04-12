"""
OpenGrowBox Global Cooldown Manager (GCD)

Centralized management of device cooldowns and action history.
Handles all cooldown logic, persistence, and status queries.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..data.OGBParams.OGBParams import DEFAULT_DEVICE_COOLDOWNS

if TYPE_CHECKING:
    from ...OGBDataStore.OGBDataStore import OGBDataStore

_LOGGER = logging.getLogger(__name__)


class OGBgcdManager:
    """
    Manages global cooldowns for all device capabilities.

    Features:
    - Load/save cooldowns from datastore
    - Check if actions are allowed based on cooldown rules
    - Calculate adaptive and fixed cooldowns
    - Register actions in history
    - Adjust cooldowns at runtime
    - Get cooldown status for UI
    - Emergency mode support
    """

    def __init__(self, hass, data_store: 'OGBDataStore', room: str):
        """
        Initialize the cooldown manager.

        Args:
            hass: Home Assistant instance
            data_store: Data store instance
            room: Room identifier
        """
        self.hass = hass
        self.data_store = data_store
        self.room = room

        self.cooldowns: Dict[str, float] = self.load_from_datastore()
        self.action_history: Dict[str, Dict[str, Any]] = {}
        self._emergency_mode = False

        self._lock = asyncio.Lock()

    def load_from_datastore(self) -> Dict[str, float]:
        """
        Load user-defined cooldowns from datastore, falling back to defaults.

        Returns:
            Dictionary of capability -> cooldown in minutes
        """
        cooldowns = DEFAULT_DEVICE_COOLDOWNS.copy()

        try:
            user_cooldowns = self.data_store.getDeep("controlOptions.deviceCooldowns")

            if user_cooldowns and isinstance(user_cooldowns, dict):
                updated_count = 0
                for capability, minutes in user_cooldowns.items():
                    if capability in cooldowns:
                        cooldowns[capability] = float(minutes)
                        updated_count += 1
                    else:
                        _LOGGER.warning(
                            f"{self.room}: Unknown capability '{capability}' in user cooldowns, skipping"
                        )

                if updated_count > 0:
                    _LOGGER.info(
                        f"{self.room}: Loaded {updated_count} user-defined cooldown(s) from datastore: {user_cooldowns}"
                    )
            else:
                _LOGGER.debug(
                    f"{self.room}: No user cooldowns found in datastore (user_cooldowns={user_cooldowns})"
                )
        except Exception as e:
            _LOGGER.warning(
                f"{self.room}: Failed to load user cooldowns from datastore: {e}. Using defaults."
            )

        return cooldowns

    def save_to_datastore(self):
        """
        Save current cooldowns to datastore for persistence.
        """
        try:
            self.data_store.setDeep("controlOptions.deviceCooldowns", self.cooldowns)
            _LOGGER.info(
                f"{self.room}: Saved {len(self.cooldowns)} cooldown(s) to datastore"
            )
        except Exception as e:
            _LOGGER.error(
                f"{self.room}: Failed to save cooldowns to datastore: {e}"
            )

    async def is_allowed(self, capability: str, action: str, deviation: float = 0) -> bool:
        """
        Check if an action is allowed based on cooldown rules.

        Args:
            capability: Device capability
            action: Action type
            deviation: Current deviation from target

        Returns:
            True if action is allowed
        """
        now = datetime.now()

        if capability not in self.action_history:
            return True

        history = self.action_history[capability]

        if self._emergency_mode:
            _LOGGER.warning(
                f"{self.room}: Emergency mode - bypassing cooldown for {capability}"
            )
            return True

        if now < history.get("cooldown_until", now):
            _LOGGER.debug(
                f"{self.room}: {capability} still in cooldown until {history['cooldown_until']}"
            )
            return False

        if history.get("action_type") == action and now < history.get(
            "repeat_cooldown", now
        ):
            _LOGGER.debug(
                f"{self.room}: {capability} repeat of '{action}' still blocked"
            )
            return False

        return True

    def calculate(self, capability: str, deviation: float = 0, adaptive: bool = False) -> float:
        """
        Calculate cooldown time.

        Args:
            capability: Device capability
            deviation: Current deviation from target
            adaptive: If True, use adaptive cooldown logic from Dampening

        Returns:
            Cooldown time in minutes
        """
        base_cooldown = self.cooldowns.get(capability, 2.0)

        if adaptive:
            return self._calculate_adaptive_dampening(base_cooldown, deviation)

        adaptive_enabled = self.data_store.getDeep("controlOptions.adaptiveCooldownEnabled", False)
        if not adaptive_enabled:
            if self._emergency_mode:
                emergency_factor = self.data_store.getDeep("controlOptions.emergencyCooldownFactor", 0.5)
                return base_cooldown * emergency_factor
            return base_cooldown

        thresholds = self.data_store.getDeep("controlOptions.adaptiveCooldownThresholds", {
            "critical": 5.0, "high": 3.0, "near": 1.0, "veryNear": 0.5
        })
        factors = self.data_store.getDeep("controlOptions.adaptiveCooldownFactors", {
            "critical": 1.5, "high": 1.2, "near": 2.0, "veryNear": 3.0
        })

        abs_dev = abs(deviation)

        if abs_dev > thresholds["critical"]:
            return base_cooldown * factors["critical"]
        elif abs_dev > thresholds["high"]:
            return base_cooldown * factors["high"]
        elif abs_dev < thresholds["veryNear"]:
            return base_cooldown * factors["veryNear"]
        elif abs_dev < thresholds["near"]:
            return base_cooldown * factors["near"]

        return base_cooldown

    def _calculate_adaptive_dampening(self, base_cooldown: float, deviation: float) -> float:
        """
        Calculate adaptive cooldown based on deviation severity (from DampeningActions).

        Larger deviations get longer cooldowns to allow time for effect.
        Smaller deviations get shorter cooldowns for more responsive control.

        Args:
            base_cooldown: Base cooldown in minutes
            deviation: Current deviation from target

        Returns:
            Cooldown time in minutes
        """
        abs_deviation = abs(deviation)

        if abs_deviation > 5:
            return base_cooldown * 1.5
        elif abs_deviation > 3:
            return base_cooldown * 1.2
        elif abs_deviation < 1:
            return base_cooldown * 0.8

        return base_cooldown

    async def register(self, capability: str, action: str, deviation: float = 0, adaptive: bool = False):
        """
        Register an action in the history system.

        Args:
            capability: Device capability
            action: Action type
            deviation: Current deviation from target
            adaptive: If True, use adaptive cooldown logic
        """
        async with self._lock:
            now = datetime.now()

            cooldown_minutes = self.calculate(capability, deviation, adaptive)
            cooldown_until = now + timedelta(minutes=cooldown_minutes)

            repeat_cooldown = now + timedelta(minutes=cooldown_minutes * 0.5)

            self.action_history[capability] = {
                "last_action": now,
                "action_type": action,
                "cooldown_until": cooldown_until,
                "repeat_cooldown": repeat_cooldown,
                "deviation": deviation,
            }

            _LOGGER.debug(
                f"{self.room}: {capability} '{action}' registered, cooldown until {cooldown_until}"
            )

    async def adjust(self, capability: str, minutes: float):
        """
        Adjust device cooldown settings.

        Args:
            capability: Device capability
            minutes: New cooldown in minutes
        """
        if capability in self.cooldowns:
            self.cooldowns[capability] = minutes
            _LOGGER.warning(
                f"Cooldown for {capability} set to {minutes} minutes. GCDS: {self.cooldowns}"
            )
            self.save_to_datastore()
        else:
            _LOGGER.error(f"Unknown capability: {capability}")

    def get_status(self) -> Dict[str, Any]:
        """
        Get current cooldown status for UI.

        Returns:
            Dictionary with cooldown status information
        """
        now = datetime.now()

        active_cooldowns = [
            cap for cap, data in self.action_history.items()
            if now < data.get("cooldown_until", now)
        ]

        status = {
            "cooldowns": self.cooldowns.copy(),
            "active_count": len(active_cooldowns),
            "active_cooldowns": active_cooldowns,
            "emergency_mode": self._emergency_mode,
        }

        for cap, data in self.action_history.items():
            if cap in status["cooldowns"]:
                cooldown_remaining = data.get("cooldown_until", now) - now
                status[cap] = {
                    "cooldown_remaining_seconds": max(0, cooldown_remaining.total_seconds()),
                    "is_blocked": now < data.get("cooldown_until", now),
                }

        return status

    async def set_emergency_mode(self, enabled: bool):
        """
        Set emergency mode state.

        Args:
            enabled: True to enable emergency mode
        """
        self._emergency_mode = enabled

        if enabled:
            await self._clear_all()
            _LOGGER.warning(f"{self.room}: Emergency mode enabled, all cooldowns cleared")

    async def _clear_all(self):
        """
        Clear all cooldowns during emergencies.
        """
        now = datetime.now()

        for capability in self.action_history:
            self.action_history[capability]["cooldown_until"] = now

        _LOGGER.info(f"{self.room}: All cooldowns cleared")

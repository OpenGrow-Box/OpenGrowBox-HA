"""CO2 aggregation manager for OpenGrowBox.

Handles CO2 sensor aggregation and writes averaged values to datastore.
Includes CRITICAL SAFETY CHECKS for max CO2 limits.

PID-like CO2 control with:
- Rate-of-Change calculation (trend detection)
- Predictive stopping (stop before overshoot)
- Asymmetric hysteresis (faster stop than start)
- Cooldown logic (prevent rapid switching)
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from collections import deque

from ..utils.ambient import is_ambient_room

_LOGGER = logging.getLogger(__name__)


class OGBCO2Manager:
    """Manages CO2 sensor data aggregation and SAFETY CHECKS."""

    def __init__(self, hass, data_store, event_manager, room, notificator=None):
        """
        Initialize CO2 Manager.

        Args:
            hass: Home Assistant instance
            data_store: Datastore for storing CO2 values
            event_manager: Event manager for pub/sub
            room: Room name for logging
            notificator: OGBNotificator instance for mobile push notifications
        """
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.notificator = notificator
        self.co2_sensors: List[float] = []
        self.max_sensor_count = 5  # Keep last N readings
        self.last_write_time: Optional[datetime] = None

        # Safety tracking
        self._co2_emergency_active = False
        self._last_co2_value = 0.0
        self._emergency_start_time = None

        # History for rate-of-change calculation
        self._co2_history = deque(maxlen=10)  # (timestamp, value) pairs
        self._last_action_time = None
        self._last_action = None  # 'Increase' or 'Reduce'
        self._cooldown_seconds = 30  # Minimum time between actions
        self._predictive_factor = 1.5  # How aggressive to stop before target
        
        # Asymmetric hysterese
        self._increase_hysterese = 50  # Start below target
        self._reduce_hysterese = 20    # Stop earlier (closer to target)
        
        # Register for CO2 update events from sensors
        self.event_manager.on("CO2Check", self.handle_co2_update)

    def _calculate_rate_of_change(self) -> float:
        """
        Calculate CO2 rate of change in ppm per minute.
        
        Returns:
            Rate of change (positive = rising, negative = falling)
        """
        if len(self._co2_history) < 3:
            return 0.0
        
        # Use last 3 readings for trend
        recent = list(self._co2_history)[-3:]
        
        # Calculate average rate over these readings
        total_rate = 0.0
        count = 0
        
        for i in range(1, len(recent)):
            time_diff = (recent[i][0] - recent[i-1][0]).total_seconds()
            if time_diff > 0:
                value_diff = recent[i][1] - recent[i-1][1]
                rate = (value_diff / time_diff) * 60  # ppm per minute
                total_rate += rate
                count += 1
        
        return total_rate / count if count > 0 else 0.0

    def _get_predictive_target(self, target: float, current_rate: float) -> float:
        """
        Calculate predictive target based on rate of change.
        
        If CO2 is rising fast, stop earlier to prevent overshoot.
        
        Args:
            target: Target CO2 value
            current_rate: Current rate of change (ppm/min)
            
        Returns:
            Adjusted target value
        """
        if current_rate <= 0:
            return target  # Not rising or falling, use normal target
        
        # Calculate how much CO2 will rise in the next cycle (15 seconds)
        cycle_time = 15  # seconds
        predicted_rise = (current_rate / 60) * cycle_time
        
        # Adjust target: stop earlier if rising fast
        adjusted_target = target - (predicted_rise * self._predictive_factor)
        
        _LOGGER.debug(
            f"{self.room}: Predictive control - rate={current_rate:.1f}ppm/min, "
            f"predicted_rise={predicted_rise:.1f}ppm, "
            f"adjusted_target={adjusted_target:.1f}ppm"
        )
        
        return adjusted_target

    def _is_cooldown_active(self) -> bool:
        """Check if cooldown period is active."""
        if self._last_action_time is None:
            return False
        
        elapsed = (datetime.now() - self._last_action_time).total_seconds()
        return elapsed < self._cooldown_seconds

    def _update_history(self, value: float):
        """Update CO2 history with timestamp."""
        self._co2_history.append((datetime.now(), float(value)))

    def handle_co2_update(self, value: float):
        """
        Handle CO2 sensor updates and aggregate values.

        Args:
            value: CO2 reading in ppm
        """
        try:
            # Validate CO2 value
            if not isinstance(value, (int, float)) or value < 0 or value > 5000:
                _LOGGER.warning(
                    f"{self.room}: Invalid CO2 value received: {value}, ignoring"
                )
                return

            # Add to sensor list
            self.co2_sensors.append(float(value))

            # Keep only last N values
            if len(self.co2_sensors) > self.max_sensor_count:
                self.co2_sensors = self.co2_sensors[-self.max_sensor_count:]

            # Calculate average
            avg_co2 = sum(self.co2_sensors) / len(self.co2_sensors)
            self._last_co2_value = avg_co2

            # Update history for rate-of-change calculation
            self._update_history(avg_co2)

            # Write to datastore (same path as hardcoded in Sensor.py)
            self.data_store.setDeep("tentData.co2Level", avg_co2)
            self.data_store.setDeep("tentData.co2", avg_co2)  # Compatibility

            _LOGGER.debug(
                f"{self.room}: CO2 aggregated - count={len(self.co2_sensors)}, "
                f"avg={avg_co2:.1f} ppm"
            )

            # CRITICAL SAFETY CHECKS - Run for EVERY CO2 reading
            asyncio.create_task(self._check_co2_safety(avg_co2))

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error handling CO2 update: {e}")

    async def _check_co2_safety(self, current_co2: float):
        """CRITICAL SAFETY CHECKS for CO2 levels."""
        try:
            co2_control = self.data_store.getDeep("controlOptions.co2Control", False)
            if not co2_control:
                return

            max_co2 = self.data_store.getDeep("controlOptionData.co2ppm.maxPPM", 1300)
            critical_threshold = self.data_store.getDeep("controlOptionData.co2ppm.criticalPPM", 2000)

            max_co2 = float(max_co2) if max_co2 else 1300
            critical_threshold = float(critical_threshold) if critical_threshold else 2000

            # SAFETY CHECK 1: Max CO2 limit (1300ppm) - WARNING
            if current_co2 >= max_co2 and current_co2 <= critical_threshold:
                if not self._co2_emergency_active:
                    _LOGGER.warning(
                        f"{self.room}: CO2 max limit reached: {current_co2:.0f}ppm >= "
                        f"Max {max_co2:.0f}ppm. CO2 pump stopped."
                    )
                    self._co2_emergency_active = True
                    self._emergency_start_time = datetime.now()

                    await self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "message": f"CO2 max limit reached: {current_co2:.0f}ppm. CO2 pump stopped.",
                            "co2Level": "WARNING",
                            "alertType": "CO2_SAFETY",
                            "current_co2": self._last_co2_value,
                            "timestamp": datetime.now().isoformat(),
                        },
                        haEvent=True,
                        debug_type="WARNING",
                    )

                    await self.event_manager.emit(
                        "EmergencyCO2Stop",
                        {"room": self.room, "current_co2": current_co2, "max_co2": max_co2, "reason": "max_limit_exceeded"}
                    )

            # SAFETY CHECK 2: Critical threshold (>2000ppm) - CRITICAL with mobile notification
            elif current_co2 > critical_threshold:
                if not self._co2_emergency_active:
                    _LOGGER.error(
                        f"{self.room}: CO2 CRITICAL EMERGENCY - {current_co2:.0f}ppm "
                        f"exceeds critical threshold ({critical_threshold:.0f}ppm)!"
                    )
                    self._co2_emergency_active = True
                    self._emergency_start_time = datetime.now()

                    await self._emit_co2_alert(
                        "CRITICAL",
                        f"CO2 at {current_co2:.0f}ppm exceeds CRITICAL threshold! "
                        f"CO2 pump OFF! Check system IMMEDIATELY!",
                        send_notification=True
                    )

                    await self.event_manager.emit(
                        "EmergencyCO2Stop",
                        {"room": self.room, "current_co2": current_co2, "critical_threshold": critical_threshold, "reason": "critical_threshold_exceeded"}
                    )

            # SAFETY CHECK 3: CO2 returned to safe levels
            elif self._co2_emergency_active and current_co2 < (max_co2 * 0.9):
                _LOGGER.warning(f"{self.room}: CO2 returned to safe levels: {current_co2:.0f}ppm. Emergency cleared.")
                self._co2_emergency_active = False
                self._emergency_start_time = None

                await self._emit_co2_alert(
                    "INFO",
                    f"CO2 safety alert cleared. Current: {current_co2:.0f}ppm. System returned to normal.",
                    send_notification=False
                )

                await self.event_manager.emit("CO2Safe", {"room": self.room, "current_co2": current_co2, "max_co2": max_co2})

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error in CO2 safety check: {e}")

    async def _emit_co2_alert(self, level: str, message: str, send_notification: bool = False):
        """Emit CO2 alert to UI and optionally mobile."""
        try:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "message": message,
                    "co2Level": level,
                    "alertType": "CO2_SAFETY",
                    "current_co2": self._last_co2_value,
                    "timestamp": datetime.now().isoformat(),
                },
                haEvent=True,
                debug_type="ERROR" if level == "CRITICAL" else "WARNING",
            )

            if send_notification and self.notificator:
                await self.notificator.critical(message=message, title=f"OGB {self.room}: CO2 EMERGENCY")

            if level == "CRITICAL":
                _LOGGER.error(f"{self.room}: {message}")
            else:
                _LOGGER.warning(f"{self.room}: {message}")

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error emitting CO2 alert: {e}")

    def reset_sensor_data(self):
        """Reset sensor data buffer."""
        self.co2_sensors = []
        self._co2_history.clear()
        self._co2_emergency_active = False
        self._emergency_start_time = None
        self._last_action_time = None
        self._last_action = None
        _LOGGER.debug(f"{self.room}: CO2 sensor data and control state reset")

    def is_co2_emergency(self) -> bool:
        """Check if CO2 is currently in emergency state."""
        return self._co2_emergency_active

    def get_last_co2(self) -> float:
        """Get last aggregated CO2 value."""
        return self._last_co2_value

    # =================================================================
    # CENTRALIZED CO2 ACTION DECISION
    # =================================================================

    async def decide_co2_action(self, mode: str, capabilities: Dict[str, Any]) -> List[Any]:
        """
        Zentrale CO2-Aktions-Entscheidung mit PID-ähnlicher Steuerung.
        
        Features:
        - Rate-of-Change Berechnung (Trend)
        - Predictive Stopping (früher stoppen bei schnellem Anstieg)
        - Asymmetrische Hysterese (schneller stoppen als starten)
        - Cooldown Logik (keine schnellen Schaltvorgänge)
        
        Args:
            mode: "VPD" oder "CLOSED" 
            capabilities: Alle verfuegbaren Capabilities
            
        Returns:
            Liste von Action-Maps (leere Liste wenn keine Aktion noetig)
        """
        # Skip for ambient room - ambient has no CO2 control
        if is_ambient_room(self.room):
            _LOGGER.debug(f"{self.room}: Ambient room - skipping CO2 control")
            return []
        
        action_map = []
        
        try:
            # 1. Pruefen ob CO2-Control aktiviert
            co2_control_enabled = self.data_store.getDeep("controlOptions.co2Control", False)
            if not co2_control_enabled:
                _LOGGER.debug(f"{self.room}: CO2 control disabled, skipping")
                return []
            
            # 2. Aktuellen CO2-Wert aus DataStore holen (wie andere Manager auch)
            current_co2 = self.data_store.getDeep("tentData.co2Level", 0)
            if current_co2 is None or current_co2 == 0:
                _LOGGER.debug(f"{self.room}: No CO2 reading available")
                return []
            
            # Update history
            self._update_history(current_co2)
            
            # 3. Calculate rate of change
            rate = self._calculate_rate_of_change()
            
            # 4. Targets (min/max ppm) holen
            co2_target_min = self.data_store.getDeep("controlOptionData.co2ppm.minPPM", 800)
            co2_target_max = self.data_store.getDeep("controlOptionData.co2ppm.maxPPM", 1500)
            co2_target = self.data_store.getDeep("controlOptionData.co2ppm.target", 800)
            
            co2_target_min = float(co2_target_min) if co2_target_min else 800
            co2_target_max = float(co2_target_max) if co2_target_max else 1500
            co2_target = float(co2_target) if co2_target else 800
            
            # 5. Licht-Status pruefen
            is_light_on = bool(self.data_store.getDeep("isPlantDay.islightON", False))
            
            # 6. Safety-Checks durchfuehren (immer zuerst!)
            if self._co2_emergency_active:
                _LOGGER.warning(f"{self.room}: CO2 emergency active - blocking all CO2 injection")
                return []
            
            # 7. Cooldown check
            if self._is_cooldown_active():
                _LOGGER.debug(
                    f"{self.room}: CO2 cooldown active ({self._cooldown_seconds}s), "
                    f"skipping action decision"
                )
                return []
            
            # 8. Predictive target calculation
            predictive_target = self._get_predictive_target(co2_target, rate)
            
            # Asymmetric hysterese limits
            lower_limit = predictive_target - self._increase_hysterese
            upper_limit = predictive_target + self._reduce_hysterese
            
            _LOGGER.info(
                f"{self.room}: CO2 PRO control - current={current_co2:.0f}ppm, "
                f"target={co2_target:.0f}ppm, predictive={predictive_target:.0f}ppm, "
                f"rate={rate:.1f}ppm/min, zone=[{lower_limit:.0f}-{upper_limit:.0f}]"
            )
            
            # 9. Target-Logik basierend auf Modus
            if mode == "CLOSED":
                action_map = await self._decide_closed_environment_action(
                    current_co2, co2_target_min, co2_target_max, co2_target,
                    lower_limit, upper_limit, is_light_on, capabilities, rate
                )
            elif mode == "VPD":
                action_map = await self._decide_vpd_mode_action(
                    current_co2, co2_target_max, co2_target,
                    lower_limit, upper_limit, is_light_on, capabilities, rate
                )
            else:
                _LOGGER.warning(f"{self.room}: Unknown CO2 mode: {mode}")
                return []
            
            # 10. Update action tracking
            if action_map:
                for action in action_map:
                    if action.capability == "canCO2":
                        self._last_action_time = datetime.now()
                        self._last_action = action.action
                        break
                
                _LOGGER.info(
                    f"{self.room}: CO2 action executed - mode={mode}, "
                    f"actions={[a.capability + ':' + a.action for a in action_map]}"
                )
            
            return action_map
            
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error in decide_co2_action: {e}")
            return []
    
    async def _decide_closed_environment_action(
        self, current_co2: float, co2_target_min: float, co2_target_max: float,
        co2_target: float, lower_limit: float, upper_limit: float,
        is_light_on: bool, capabilities: Dict[str, Any], rate: float
    ) -> List[Any]:
        """
        Entscheidet CO2-Action fuer Closed Environment Modus.
        
        Logik:
        - Predictive Stopping bei schnellem Anstieg
        - Asymmetrische Hysterese
        - Emergency Handling
        """
        action_map = []
        action_message = "Closed Environment CO2 PRO"
        
        # Emergency high CO2 overrides everything
        co2_emergency_high = self.data_store.getDeep("controlOptionData.co2ppm.criticalPPM", 2000)
        if current_co2 > float(co2_emergency_high):
            _LOGGER.warning(f"{self.room}: CO2 EMERGENCY: {current_co2} > {co2_emergency_high}")
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Reduce", action_message + " [EMERGENCY]"))
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Increase", action_message + " [EMERGENCY]"))
            return action_map
        
        # Below minimum - must increase (if light is on)
        if current_co2 < co2_target_min:
            if is_light_on:
                _LOGGER.info(f"{self.room}: CO2 {current_co2:.0f} < min {co2_target_min:.0f}, injecting")
                if capabilities.get("canCO2", {}).get("state", False):
                    action_map.append(self._create_action("canCO2", "Increase", action_message + " [Below Min]"))
            else:
                _LOGGER.debug(f"{self.room}: CO2 below min but lights OFF")
                if capabilities.get("canCO2", {}).get("state", False):
                    action_map.append(self._create_action("canCO2", "Reduce", action_message + " [Night]"))
            return action_map
        
        # Above maximum - must reduce immediately
        if current_co2 > co2_target_max:
            _LOGGER.info(f"{self.room}: CO2 {current_co2:.0f} > max {co2_target_max:.0f}, reducing")
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Reduce", action_message + " [Above Max]"))
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Increase", action_message + " [Above Max]"))
            return action_map
        
        # TARGET ZONE LOGIC with predictive control
        if current_co2 < lower_limit and is_light_on:
            # Below predictive lower limit - increase
            _LOGGER.info(
                f"{self.room}: CO2 {current_co2:.0f} < {lower_limit:.0f} "
                f"(predictive, rate={rate:.1f}), injecting"
            )
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Increase", action_message + " [Target]"))
                
        elif current_co2 > upper_limit:
            # Above predictive upper limit - reduce
            _LOGGER.info(
                f"{self.room}: CO2 {current_co2:.0f} > {upper_limit:.0f} "
                f"(predictive, rate={rate:.1f}), reducing"
            )
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Reduce", action_message + " [Target]"))
                
        else:
            # In target zone - maintain
            _LOGGER.debug(
                f"{self.room}: CO2 {current_co2:.0f} in zone [{lower_limit:.0f}-{upper_limit:.0f}] "
                f"-> STABLE, rate={rate:.1f}ppm/min"
            )
        
        return action_map
    
    async def _decide_vpd_mode_action(
        self, current_co2: float, co2_target_max: float, co2_target: float,
        lower_limit: float, upper_limit: float,
        is_light_on: bool, capabilities: Dict[str, Any], rate: float
    ) -> List[Any]:
        action_map = []
        action_message = "VPD Perfection CO2 PRO"
        
        # Night mode - always off
        if not is_light_on:
            _LOGGER.info(f"{self.room}: Night mode - CO2 off")
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Reduce", action_message + " [Night]"))
            return action_map
        
        # Above max - safety stop
        if current_co2 > co2_target_max:
            _LOGGER.warning(f"{self.room}: CO2 {current_co2:.0f} > max {co2_target_max:.0f}, stopping")
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Reduce", action_message + " [Safety]"))
            return action_map
        
        # Below predictive lower limit - increase
        if current_co2 < lower_limit:
            _LOGGER.info(
                f"{self.room}: CO2 {current_co2:.0f} < {lower_limit:.0f} "
                f"(predictive, rate={rate:.1f}), injecting"
            )
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Increase", action_message + " [Target]"))
                
        # Above predictive upper limit - reduce
        elif current_co2 > upper_limit:
            _LOGGER.info(
                f"{self.room}: CO2 {current_co2:.0f} > {upper_limit:.0f} "
                f"(predictive, rate={rate:.1f}), reducing"
            )
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Reduce", action_message + " [Target]"))
                
        else:
            # In target zone
            _LOGGER.debug(
                f"{self.room}: CO2 {current_co2:.0f} in zone [{lower_limit:.0f}-{upper_limit:.0f}] "
                f"-> STABLE, rate={rate:.1f}ppm/min"
            )
        
        return action_map
    
    def _create_action(self, capability: str, action: str, message: str):
        """Erstellt eine Action-Publikation (konsistent mit VPDActions)."""
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        return OGBActionPublication(
            Name=self.room,
            capability=capability,
            action=action,
            message=message,
            priority="medium"
        )
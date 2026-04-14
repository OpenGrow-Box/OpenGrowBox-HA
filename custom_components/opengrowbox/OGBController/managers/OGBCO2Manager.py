"""CO2 aggregation manager for OpenGrowBox.

Handles CO2 sensor aggregation and writes averaged values to datastore.
Includes CRITICAL SAFETY CHECKS for max CO2 limits.
"""

import asyncio
import logging
from typing import List, Optional
from datetime import datetime

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

        # Register for CO2 update events from sensors
        self.event_manager.on("CO2Check", self.handle_co2_update)

        _LOGGER.info(f"{self.room}: OGBCO2Manager initialized with SAFETY CHECKS")

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
        self._co2_emergency_active = False
        self._emergency_start_time = None
        _LOGGER.debug(f"{self.room}: CO2 sensor data and emergency state reset")

    def is_co2_emergency(self) -> bool:
        """Check if CO2 is currently in emergency state."""
        return self._co2_emergency_active

    def get_last_co2(self) -> float:
        """Get last aggregated CO2 value."""
        return self._last_co2_value

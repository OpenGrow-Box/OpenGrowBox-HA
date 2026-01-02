import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

_LOGGER = logging.getLogger(__name__)


class OGBNotificator:
    def __init__(
        self, hass, room: str, service: str = "persistent_notification.create"
    ):
        """
        OGB Notificator for critical and info messages.

        :param hass: Home Assistant core object
        :param room: Room/Context name (z. B. "FlowerTent")
        :param service: Default notification service (z. B. "persistent_notification.create" oder "notify.mobile_app_xyz")
        """
        self.hass = hass
        self.room = room
        self.service = service  # Standard-Service

        # Rate limiting configuration
        self.rate_limits = {
            "critical": {"max_per_hour": 5, "cooldown_minutes": 10},
            "warning": {"max_per_hour": 10, "cooldown_minutes": 5},
            "info": {"max_per_hour": 30, "cooldown_minutes": 1},
        }

        # Track notifications for rate limiting
        self._notification_history: Dict[str, list] = {
            "critical": [],
            "warning": [],
            "info": [],
        }

        _LOGGER.info(
            f"[{self.room}] OGB Notificator initialized with service '{self.service}'"
        )

    async def _send(
        self,
        title: str,
        message: str,
        level: str = "info",
        service: Optional[str] = None,
    ):
        """
        Internal notification sender with rate limiting

        :param title: Notification title
        :param message: Notification message
        :param level: Notification level (critical/warning/info)
        :param service: Override default service
        """
        try:
            # Check rate limits before sending
            if not await self._check_rate_limit(level, title):
                _LOGGER.warning(
                    f"[{self.room}] Rate limit exceeded for {level} notification: {title}"
                )
                return

            svc = service or self.service
            domain, srv = svc.split(".")
            service_data = {}

            if svc == "persistent_notification.create":
                service_data = {"title": title, "message": message}
            elif svc.startswith("notify."):
                service_data = {"title": title, "message": message}
                # Set appropriate priority based on level
                if level == "critical":
                    service_data["data"] = {"ttl": 0, "priority": "high"}
                elif level == "warning":
                    service_data["data"] = {"priority": "default"}
            else:
                _LOGGER.error(f"[{self.room}] Unsupported notification service '{svc}'")
                return

            await self.hass.services.async_call(
                domain, srv, service_data, blocking=True
            )

            # Record the notification for rate limiting
            self._record_notification(level)

            _LOGGER.info(f"[{self.room}] {level.title()} notification sent: {title}")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to send {level} notification: {e}")

    async def _check_rate_limit(self, level: str, title: str) -> bool:
        """
        Check if notification is within rate limits

        :param level: Notification level
        :param title: Notification title (used as identifier)
        :return: True if notification can be sent
        """
        now = datetime.now()
        config = self.rate_limits.get(level, self.rate_limits["info"])
        history = self._notification_history[level]

        # Clean old entries (older than 1 hour)
        cutoff = now - timedelta(hours=1)
        history[:] = [t for t in history if t > cutoff]

        # Check hourly limit
        if len(history) >= config["max_per_hour"]:
            return False

        # Check cooldown period for same title
        cooldown_cutoff = now - timedelta(minutes=config["cooldown_minutes"])
        recent_same_title = [t for t in history if t > cooldown_cutoff]

        # Simple heuristic: if we've sent something recently, check if it's the same issue
        # This prevents spam but allows different issues
        if recent_same_title and len(recent_same_title) > 2:
            _LOGGER.debug(f"[{self.room}] Cooldown active for {level} notifications")
            return False

        return True

    def _record_notification(self, level: str):
        """Record notification timestamp for rate limiting"""
        self._notification_history[level].append(datetime.now())

    def get_notification_stats(self) -> dict:
        """Get notification statistics for debugging"""
        now = datetime.now()
        stats = {}

        for level in ["critical", "warning", "info"]:
            history = self._notification_history[level]
            cutoff_1h = now - timedelta(hours=1)
            cutoff_24h = now - timedelta(hours=24)

            stats[level] = {
                "last_hour": len([t for t in history if t > cutoff_1h]),
                "last_24h": len([t for t in history if t > cutoff_24h]),
                "rate_limit": self.rate_limits[level]["max_per_hour"],
            }

        return stats

    # =================================================================
    # Emergency Alert System
    # =================================================================

    async def alert_pump_overrun(
        self, pump_type: str, expected_duration: float, actual_duration: float
    ):
        """Alert when pump runs longer than expected."""
        overrun_minutes = (actual_duration - expected_duration) / 60

        await self.critical(
            message=f"Pump {pump_type} ran {overrun_minutes:.1f} minutes longer than expected "
            f"(expected: {expected_duration/60:.1f}min, actual: {actual_duration/60:.1f}min). "
            f"Pump may be stuck or sensor failed.",
            title=f"OGB {self.room}: Pump Overrun Alert",
        )

    async def alert_sensor_failure(
        self, sensor_type: str, device_name: str, last_value: any = None
    ):
        """Alert when sensor stops reporting."""
        message = f"Sensor '{sensor_type}' on device '{device_name}' has stopped reporting data."
        if last_value is not None:
            message += f" Last known value: {last_value}"

        await self.critical(message=message, title=f"OGB {self.room}: Sensor Failure")

    async def alert_calibration_failure(self, pump_type: str, error: str):
        """Alert when calibration fails."""
        await self.warning(
            message=f"Pump calibration failed for {pump_type}: {error}. "
            f"Dosing accuracy may be affected.",
            title=f"OGB {self.room}: Calibration Failure",
        )

    async def alert_low_water_level(self, current_level: float, threshold: float):
        """Alert when water level is too low."""
        await self.critical(
            message=f"Water level is critically low: {current_level:.1f}L "
            f"(threshold: {threshold:.1f}L). Add water immediately to prevent pump damage.",
            title=f"OGB {self.room}: Low Water Alert",
        )

    async def alert_ph_calibration_issue(
        self, current_ph: float, target_ph: float, time_since_adjustment: float
    ):
        """Alert when pH adjustment is not working."""
        hours_since = time_since_adjustment / 3600

        await self.warning(
            message=f"pH adjustment ineffective. Current pH: {current_ph:.2f}, "
            f"Target: {target_ph:.2f}. Last adjustment {hours_since:.1f} hours ago. "
            f"Check pH solution levels and pump operation.",
            title=f"OGB {self.room}: pH Adjustment Issue",
        )

    async def alert_ec_calibration_issue(
        self, current_ec: float, target_ec: float, time_since_adjustment: float
    ):
        """Alert when EC adjustment is not working."""
        hours_since = time_since_adjustment / 3600

        await self.warning(
            message=f"EC adjustment ineffective. Current EC: {current_ec:.2f} mS/cm, "
            f"Target: {target_ec:.2f} mS/cm. Last adjustment {hours_since:.1f} hours ago. "
            f"Check nutrient solution and pump calibration.",
            title=f"OGB {self.room}: EC Adjustment Issue",
        )

    async def alert_system_overload(
        self, active_operations: int, max_concurrent: int = 3
    ):
        """Alert when too many operations are running simultaneously."""
        await self.warning(
            message=f"System overload: {active_operations} operations running "
            f"(max recommended: {max_concurrent}). Performance may be degraded.",
            title=f"OGB {self.room}: System Overload",
        )

    async def alert_power_failure(self, affected_devices: list):
        """Alert when power-related issues detected."""
        device_list = ", ".join(affected_devices[:5])  # Limit to first 5
        if len(affected_devices) > 5:
            device_list += f" (+{len(affected_devices)-5} more)"

        await self.critical(
            message=f"Power failure detected affecting: {device_list}. "
            f"Check power supply and electrical connections.",
            title=f"OGB {self.room}: Power Failure",
        )

    async def critical(
        self,
        message: str,
        title: str = "OGB Critical Alert",
        service: Optional[str] = None,
    ):
        """Send a critical notification (highest priority, strict rate limiting)"""
        await self._send(
            title=title, message=message, level="critical", service=service
        )

    async def warning(
        self, message: str, title: str = "OGB Warning", service: Optional[str] = None
    ):
        """Send a warning notification (medium priority, moderate rate limiting)"""
        await self._send(title=title, message=message, level="warning", service=service)

    async def info(
        self, message: str, title: str = "OGB Info", service: Optional[str] = None
    ):
        """Send an info notification (low priority, lenient rate limiting)"""
        await self._send(title=title, message=message, level="info", service=service)

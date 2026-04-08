import logging
from datetime import datetime, time, timedelta
from typing import Dict, Optional

from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class OGBNotificator:
    def __init__(
        self, hass, room: str, service: str = "persistent_notification.create",
        critical_service: Optional[str] = None, notification_enabled: bool = True
    ):
        """
        OGB Notificator for critical and info messages.

        :param hass: Home Assistant core object
        :param room: Room/Context name (z. B. "FlowerTent")
        :param service: Default notification service (z. B. "persistent_notification.create" oder "notify.mobile_app_xyz")
        :param critical_service: Override service for critical notifications (defaults to mobile app if configured)
        :param notification_enabled: Global notification on/off switch
        """
        self.hass = hass
        self.room = room
        self.service = service  # Standard-Service
        self.critical_service = critical_service  # Service für Critical Notifications
        self.notification_enabled = notification_enabled  # Globaler An/Aus-Schalter

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
        self._dst_unsub = None

        self._ensure_dst_monitor_started()

        _LOGGER.info(
            f"[{self.room}] OGB Notificator initialized with service '{self.service}'"
        )

    def _ensure_dst_monitor_started(self):
        """Start one DST monitor per room instance."""
        if not hasattr(self.hass, "data"):
            return

        ogb_notify_data = self.hass.data.setdefault("opengrowbox_notify", {})
        dst_monitors = ogb_notify_data.setdefault("dst_monitors", {})

        if self.room in dst_monitors:
            self._dst_unsub = dst_monitors[self.room]
            return

        self._schedule_next_dst_check()
        dst_monitors[self.room] = self._dst_unsub
        self.hass.loop.create_task(self.check_daylight_saving_change())

    def _schedule_next_dst_check(self):
        """Schedule the next midday local-time DST check."""
        now = dt_util.now()
        next_run = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        if self._dst_unsub:
            self._dst_unsub()

        self._dst_unsub = async_track_point_in_time(
            self.hass, self._handle_scheduled_dst_check, next_run
        )

    async def _handle_scheduled_dst_check(self, _now):
        """Run DST check and reschedule the next one."""
        try:
            await self.check_daylight_saving_change()
        finally:
            self._schedule_next_dst_check()
            self.hass.data.setdefault("opengrowbox_notify", {}).setdefault(
                "dst_monitors", {}
            )[self.room] = self._dst_unsub

    async def async_shutdown(self):
        """Cleanup scheduled callbacks to avoid restart-time leaks."""
        if self._dst_unsub:
            try:
                self._dst_unsub()
            except Exception:
                pass
            self._dst_unsub = None

        if hasattr(self.hass, "data"):
            ogb_notify_data = self.hass.data.setdefault("opengrowbox_notify", {})
            dst_monitors = ogb_notify_data.setdefault("dst_monitors", {})
            dst_monitors.pop(self.room, None)

    def _find_next_dst_transition(self):
        """Return the next local DST offset change and offsets."""
        now_local = dt_util.as_local(dt_util.now())
        tzinfo = now_local.tzinfo
        if tzinfo is None:
            return None

        current_offset = now_local.utcoffset()
        for day_offset in range(1, 370):
            probe = datetime.combine(
                now_local.date() + timedelta(days=day_offset),
                time(hour=12),
                tzinfo=tzinfo,
            )
            probe_offset = probe.utcoffset()
            if probe_offset != current_offset:
                transition_day = probe.date()
                transition_dt = datetime.combine(
                    transition_day,
                    time(hour=2),
                    tzinfo=tzinfo,
                )
                return transition_dt, current_offset, probe_offset

        return None

    async def check_daylight_saving_change(self):
        """Notify once when the next DST change is about one day away."""
        transition = self._find_next_dst_transition()
        if not transition:
            return

        transition_dt, old_offset, new_offset = transition
        now_local = dt_util.as_local(dt_util.now())
        remaining = transition_dt - now_local
        if remaining <= timedelta(0) or remaining > timedelta(days=1, hours=12):
            return

        notify_state = self.hass.data.setdefault("opengrowbox_notify", {}).setdefault(
            "dst_notifications", {}
        )
        transition_key = f"{self.room}:{transition_dt.date().isoformat()}"
        if notify_state.get(transition_key):
            return

        direction = "vor" if new_offset > old_offset else "zurueck"
        hours = max(1, round(remaining.total_seconds() / 3600))
        await self.warning(
            message=(
                f"Die Zeitumstellung erfolgt in ca. {hours} Stunde(n). "
                f"Die Uhr wird {direction} gestellt. Bitte pruefe Licht- und Zeitplaene."
            ),
            title=f"OGB Zeitumstellung - {self.room}",
        )
        notify_state[transition_key] = True

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
            # Check global notification enabled switch
            # NOTE: Critical notifications are ALWAYS sent regardless of global setting
            if not self.notification_enabled and level != "critical":
                _LOGGER.debug(
                    f"[{self.room}] Notifications disabled globally, skipping {level}: {title}"
                )
                return

            # Check rate limits before sending
            if not await self._check_rate_limit(level, title):
                _LOGGER.warning(
                    f"[{self.room}] Rate limit exceeded for {level} notification: {title}"
                )
                return

            # Determine which service to use
            svc = service or self.service
            
            # For critical notifications, use critical_service if configured
            if level == "critical":
                if self.critical_service:
                    svc = self.critical_service
                elif svc == "persistent_notification.create":
                    # Try to find a mobile app service for critical notifications
                    mobile_service = await self._find_mobile_notification_service()
                    if mobile_service:
                        svc = mobile_service
                        _LOGGER.info(
                            f"[{self.room}] Using mobile notification service for critical alert: {svc}"
                        )

            domain, srv = svc.split(".")
            service_data = {}

            if svc == "persistent_notification.create":
                service_data = {"title": title, "message": message}
            elif svc.startswith("notify."):
                service_data = {"title": title, "message": message}
                # Set appropriate priority based on level
                if level == "critical":
                    service_data["data"] = {"ttl": 0, "priority": "high", "push": {"sound": "default"}}
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

            _LOGGER.info(f"[{self.room}] {level.title()} notification sent via {svc}: {title}")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to send {level} notification: {e}")
    
    async def _find_mobile_notification_service(self) -> Optional[str]:
        """
        Find the first available mobile app notification service.
        
        :return: Service name (e.g., "notify.mobile_app_iphone") or None
        """
        try:
            # Get all registered services
            services = self.hass.services.async_services()
            
            # Look for mobile app notification services
            if "notify" in services:
                for service_name in services["notify"]:
                    if service_name.startswith("mobile_app_"):
                        return f"notify.{service_name}"
            
            _LOGGER.warning(f"[{self.room}] No mobile app notification service found for critical alerts")
            return None
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error finding mobile notification service: {e}")
            return None

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

    # =================================================================
    # Premium Subscription Notifications
    # =================================================================

    async def notify_plan_changed(self, old_plan: str, new_plan: str, features: dict = None, limits: dict = None):
        """Notify user about plan change."""
        await self.info(
            message=f"OpenGrowBox plan changed from {old_plan} to {new_plan}",
            title=f"OGB Plan Changed - {self.room}"
        )

    async def notify_subscription_expiring_soon(
        self,
        plan_name: str,
        expires_in_seconds: int,
        current_period_end: str = None,
        features: dict = None,
        limits: dict = None
    ):
        """Notify user that subscription is expiring soon."""
        expires_in_hours = expires_in_seconds // 3600
        expires_in_days = expires_in_seconds // 86400

        if expires_in_days > 0:
            message = f"Your {plan_name} subscription expires in {expires_in_days} day(s)"
        else:
            message = f"Your {plan_name} subscription expires in {expires_in_hours} hour(s)"

        await self.warning(
            message=message,
            title=f"OGB Subscription Expiring - {self.room}"
        )

    async def notify_subscription_expired(
        self,
        previous_plan: str,
        new_plan: str = "free",
        expired_at: str = None,
        features: dict = None,
        limits: dict = None
    ):
        """Notify user that subscription has expired."""
        await self.critical(
            message=f"Your OpenGrowBox {previous_plan} subscription has expired. Downgraded to {new_plan}.",
            title=f"OGB Subscription Expired - {self.room}"
        )

    # =================================================================
    # Storage Notifications
    # =================================================================

    async def notify_storage_limit_reached(
        self,
        used_gb: float,
        limit_gb: float,
        percent: float = 100,
        upgrade_url: str = "/settings/upgrade"
    ):
        """Notify user that storage limit is reached - data storage has stopped."""
        await self.critical(
            message=f"Storage limit REACHED ({used_gb:.2f}/{limit_gb}GB = {percent:.0f}%). Data storage has stopped. Upgrade your plan to continue.",
            title=f"OGB Storage Full - {self.room}"
        )

    async def notify_storage_warning(
        self,
        used_gb: float,
        limit_gb: float,
        percent: float
    ):
        """Notify user that storage is nearly full (80-95%)."""
        await self.warning(
            message=f"Storage usage: {percent:.0f}% ({used_gb:.2f}/{limit_gb}GB). Consider upgrading your plan to avoid interruption.",
            title=f"OGB Storage Warning - {self.room}"
        )

    # =================================================================
    # API Call Limit Notifications
    # =================================================================

    async def notify_api_limit_reached(
        self,
        used: int,
        limit: int,
        percent: float = 100,
        upgrade_url: str = "/settings/upgrade"
    ):
        """Notify user that API call limit is reached - data processing has stopped."""
        if limit <= 0:
            _LOGGER.warning(
                f"[{self.room}] Skip API limit notification because limit is invalid: used={used}, limit={limit}"
            )
            return
        await self.critical(
            message=f"API limit REACHED ({used}/{limit} calls = {percent:.0f}%). Data processing has stopped. Upgrade your plan to continue.",
            title=f"OGB API Limit Reached - {self.room}"
        )

    async def notify_api_warning(
        self,
        used: int,
        limit: int,
        percent: float
    ):
        """Notify user that API calls are nearly exhausted (80-95%)."""
        if limit <= 0:
            _LOGGER.warning(
                f"[{self.room}] Skip API warning notification because limit is invalid: used={used}, limit={limit}"
            )
            return
        await self.warning(
            message=f"API usage: {percent:.0f}% ({used}/{limit} calls). Consider upgrading your plan to avoid interruption.",
            title=f"OGB API Warning - {self.room}"
        )

    # =================================================================
    # Maintenance Notifications
    # =================================================================

    async def notify_maintenance_alert(
        self,
        title: str = "Scheduled Maintenance",
        message: str = "System maintenance in progress",
        level: str = "info",
        start_time: int = None,
        end_time: int = None,
        requires_action: bool = False,
        service: str = None
    ):
        """
        Notify user about scheduled maintenance from the API.
        
        Args:
            title: Alert title
            message: Alert message describing the maintenance
            level: Alert level - 'info', 'warning', or 'critical'
            start_time: Unix timestamp when maintenance starts
            end_time: Unix timestamp when maintenance is expected to end
            requires_action: Whether user needs to take action
            service: Override notification service
        """
        level_methods = {
            "info": self.info,
            "warning": self.warning,
            "critical": self.critical
        }
        
        notify_method = level_methods.get(level, self.info)
        
        # Format time info if provided
        time_info = ""
        if start_time:
            from datetime import datetime
            start_dt = datetime.fromtimestamp(start_time)
            time_info = f" | Started: {start_dt.strftime('%H:%M')}"
        
        if end_time:
            from datetime import datetime
            end_dt = datetime.fromtimestamp(end_time)
            time_info += f" | Expected end: {end_dt.strftime('%H:%M')}"
        
        action_info = " | Action required!" if requires_action else ""
        
        formatted_message = f"{message}{time_info}{action_info}"
        
        await notify_method(
            message=formatted_message,
            title=f"🔧 {title} - {self.room}",
            service=service
        )

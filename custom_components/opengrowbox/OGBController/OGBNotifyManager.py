import logging
import asyncio
from typing import Optional

_LOGGER = logging.getLogger(__name__)


class OGBNotificator:
    def __init__(self, hass, room: str, service: str = "persistent_notification.create"):
        """
        OGB Notificator for critical and info messages.

        :param hass: Home Assistant core object
        :param room: Room/Context name (z. B. "FlowerTent")
        :param service: Default notification service (z. B. "persistent_notification.create" oder "notify.mobile_app_xyz")
        """
        self.hass = hass
        self.room = room
        self.service = service  # Standard-Service
        _LOGGER.info(f"[{self.room}] OGB Notificator initialized with service '{self.service}'")

    async def _send(self, title: str, message: str, critical: bool = False, service: Optional[str] = None):
        """
        Internal notification sender
        """
        try:
            svc = service or self.service
            domain, srv = svc.split(".")
            service_data = {}

            if svc == "persistent_notification.create":
                service_data = {
                    "title": title,
                    "message": message
                }
            elif svc.startswith("notify."):
                service_data = {
                    "title": title,
                    "message": message
                }
                if critical:
                    # Optional: bei mobilen Notify-Services "critical" Flag setzen
                    service_data["data"] = {"ttl": 0, "priority": "high"}
            else:
                _LOGGER.error(f"[{self.room}] Unsupported notification service '{svc}'")
                return

            await self.hass.services.async_call(
                domain,
                srv,
                service_data,
                blocking=True
            )

            _LOGGER.info(f"[{self.room}] Notification sent: {title} - {message}")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to send notification: {e}")

    async def critical(self, message: str, title: str = "OGB Critical Alert"):
        """Send a critical notification"""
        await self._send(title=title, message=message, critical=True)

    async def info(self, message: str, title: str = "OGB Info"):
        """Send an info notification"""
        await self._send(title=title, message=message, critical=False)

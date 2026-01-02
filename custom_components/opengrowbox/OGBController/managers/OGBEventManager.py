import asyncio
import inspect
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class OGBEventManager:
    def __init__(self, hass, ogb_model):
        self.name = "OGB Event Manager"
        self.hass = hass
        self.ogb_model = ogb_model
        self.listeners = {}
        self.notifications_enabled = False
        # MEMORY FIX: Track background tasks to prevent orphaned tasks
        self._background_tasks: set = set()
        self._shutdown = False

    def __repr__(self):
        return f"Current Listeners: {self.listeners}"

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """Create and track a background task for proper cleanup."""
        if self._shutdown:
            _LOGGER.debug("EventManager shutdown, not creating new tasks")
            return None
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def on(self, event_name, callback):
        """Registriere einen Listener (synchron oder asynchron) für ein spezifisches Event."""
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        # MEMORY FIX: Prevent duplicate listeners
        if callback not in self.listeners[event_name]:
            self.listeners[event_name].append(callback)

    def remove(self, event_name, callback):
        """Entferne einen spezifischen Listener."""
        if event_name in self.listeners and callback in self.listeners[event_name]:
            self.listeners[event_name].remove(callback)

    def remove_all(self, event_name=None):
        """Entferne alle Listener für ein Event oder alle Events."""
        if event_name:
            self.listeners.pop(event_name, None)
        else:
            self.listeners.clear()

    async def _call_listener(self, callback, data):
        """Rufe einen Listener auf, synchron oder asynchron."""
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            _LOGGER.error(f"Fehler beim Aufruf des Listeners für '{callback}': {e}")

    async def emit(self, event_name, data, haEvent=False):
        """Event auslösen, inkl. optionalem HA-Event und Notification."""
        
        # Don't emit events during shutdown
        if self._shutdown:
            return
        
        # Debug log for medium-related events
        if "Medium" in event_name or "Plant" in event_name:
            _LOGGER.warning(f"📢 EMIT: {event_name} - listeners: {len(self.listeners.get(event_name, []))}, haEvent: {haEvent}")

        if haEvent:
            # MEMORY FIX: Track the task
            self._create_tracked_task(self.emit_to_home_assistant(event_name, data))
            if self.notifications_enabled:
                await self.send_notification(event_name, data)

        if event_name in self.listeners:
            listener_count = len(self.listeners[event_name])
            if "Medium" in event_name or "Plant" in event_name:
                _LOGGER.warning(f"📢 Calling {listener_count} listeners for {event_name}")
            for callback in self.listeners[event_name]:
                if inspect.iscoroutinefunction(callback):
                    # MEMORY FIX: Track the task
                    self._create_tracked_task(callback(data))
                else:
                    try:
                        callback(data)
                    except Exception as e:
                        _LOGGER.error(f"Fehler beim synchronen Listener: {e}")
        elif "Medium" in event_name or "Plant" in event_name:
            _LOGGER.warning(f"⚠️ No listeners registered for {event_name}")

    def emit_sync(self, event_name, data, haEvent=False):
        """Synchrones Event auslösen (für synchrone Kontexte).
        Wenn haEvent=True, wird das Event auch an Home Assistant gesendet."""
        asyncio.create_task(self.emit(event_name, data, haEvent))

    async def emit_to_home_assistant(self, event_name, event_data):
        """Sende ein Event an Home Assistant über den Event-Bus."""
        try:
            # Wenn event_data ein Dataclass-Objekt ist, in ein Dictionary umwandeln
            if is_dataclass(event_data):
                event_data = asdict(event_data)

            if hasattr(self.hass, "bus"):
                self.hass.bus.fire(event_name, event_data)
                _LOGGER.info(f"Event-Bus Event '{event_name}' erfolgreich gesendet.")
            else:
                _LOGGER.error(
                    f"Kein gültiger Event-Kanal für '{event_name}' verfügbar!"
                )
        except Exception as e:
            _LOGGER.error(f"Fehler beim Senden des Events '{event_name}': {e}")

    def make_json_serializable(self, obj):
        """
        Recursively traverse the object and convert non-serializable types like datetime.
        """
        if isinstance(obj, dict):
            return {k: self.make_json_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.make_json_serializable(i) for i in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return obj

    async def send_notification(self, title: str, data):
        """
        Sende eine Push-Notification via notify.notify an alle konfigurierten Notifier.
        """
        try:
            serializable_data = self.make_json_serializable(data)
            message = (
                json.dumps(serializable_data, indent=2)
                if isinstance(serializable_data, dict)
                else str(serializable_data)
            )

            await self.hass.services.async_call(
                domain="notify",
                service="notify",
                service_data={
                    "title": title,
                    "message": message,
                },
                blocking=False,
            )
            _LOGGER.info(f"Push-Notification für '{title}' gesendet.")
        except Exception as e:
            _LOGGER.error(f"Fehler beim Senden der Push-Notification: {e}")

    def change_notify_set(self, state):
        self.notifications_enabled = state
        _LOGGER.info(f"Notify State jetzt: {self.notifications_enabled}")

    async def async_shutdown(self):
        """Shutdown event manager and cleanup all resources."""
        _LOGGER.info("🛑 Shutting down EventManager")
        self._shutdown = True
        
        # Cancel all background tasks
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete with timeout
        if self._background_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._background_tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("⚠️ Some EventManager tasks did not complete within timeout")
        
        self._background_tasks.clear()
        
        # Clear all listeners to prevent memory leaks
        listener_count = sum(len(v) for v in self.listeners.values())
        self.listeners.clear()
        _LOGGER.info(f"✅ EventManager shutdown complete, cleared {listener_count} listeners")

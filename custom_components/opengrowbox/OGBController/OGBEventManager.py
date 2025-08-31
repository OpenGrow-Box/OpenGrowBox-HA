from dataclasses import is_dataclass, asdict
import asyncio
import logging
import inspect
import json
from datetime import datetime
    
_LOGGER = logging.getLogger(__name__)

class OGBEventManager:
    def __init__(self, hass, ogb_model):
        self.name = "OGB Event Manager"
        self.hass = hass
        self.ogb_model = ogb_model
        self.listeners = {}  
        self.notifications_enabled = False
        
    def __repr__(self):
        return f"Current Listeners: {self.listeners}"

    def on(self, event_name, callback):
        """Registriere einen Listener (synchron oder asynchron) für ein spezifisches Event."""
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        self.listeners[event_name].append(callback)

    def remove(self, event_name, callback):
        """Entferne einen spezifischen Listener."""
        if event_name in self.listeners and callback in self.listeners[event_name]:
            self.listeners[event_name].remove(callback)

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
        
        if haEvent:
            asyncio.create_task(self.emit_to_home_assistant(event_name, data))
            if self.notifications_enabled:
                await self.send_notification(event_name, data)


        if event_name in self.listeners:
            for callback in self.listeners[event_name]:
                if inspect.iscoroutinefunction(callback):
                    asyncio.create_task(callback(data))
                else:
                    try:
                        callback(data)
                    except Exception as e:
                        _LOGGER.error(f"Fehler beim synchronen Listener: {e}")

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
                _LOGGER.error(f"Kein gültiger Event-Kanal für '{event_name}' verfügbar!")
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
            message = json.dumps(serializable_data, indent=2) if isinstance(serializable_data, dict) else str(serializable_data)

            await self.hass.services.async_call(
                domain="notify",
                service="notify", 
                service_data={
                    "title": title,
                    "message": message,
                },
                blocking=False
            )
            _LOGGER.info(f"Push-Notification für '{title}' gesendet.")
        except Exception as e:
            _LOGGER.error(f"Fehler beim Senden der Push-Notification: {e}")
   
    def change_notify_set(self,state):
        self.notifications_enabled = state
        _LOGGER.info(f"Notify State jetzt: {self.notifications_enabled}")
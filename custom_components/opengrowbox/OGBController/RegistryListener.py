import logging
import asyncio
import json
from homeassistant.helpers.area_registry import async_get as async_get_area_registry
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .OGBDataClasses.OGBPublications import OGBEventPublication,OGBVPDPublication

from .utils.lightTimeHelpers import update_light_state

_LOGGER = logging.getLogger(__name__)

class OGBRegistryEvenListener:
    def __init__(self, hass,dataStore,eventManager,room):
        self.name = "OGB Registry Listener"
        self.hass = hass
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.room_name = room

    async def get_entities_by_room_async(self, room_name):
        """Hole alle Entitäten nach Raum."""
        entities_by_room = {}

        # Debug: Zeige alle Entitäten, bevor die Schleife beginnt
        all_entities = self.hass.states.async_all()
        _LOGGER.warn(f"Alle Entitäten: {all_entities}")

        # Hole alle aktuellen Zustände aus Home Assistant
        for entity in all_entities:
            # Prüfe, ob die Entität einem Raum (area_id) zugeordnet ist
            area = entity.attributes.get("area_id")
            _LOGGER.warn(f"Entity: {entity.entity_id}, Area: {area}")

            if area and area == room_name:
                entities_by_room[entity.entity_id] = entity

        _LOGGER.warn(f"Entities in Room '{room_name}': {entities_by_room}")
        return entities_by_room

    async def get_entities_and_devices_by_room(self, room_name):
        """Hole alle Entitäten und Geräte nach Raum."""
        # Entitäten abrufen
        entities = {}
        for entity in self.hass.states.async_all():
            area = entity.attributes.get("area_id")
            if area and area == room_name:
                entities[entity.entity_id] = entity

        # Geräte abrufen
        device_registry = async_get_device_registry(self.hass)
        devices = {
            device_id: device
            for device_id, device in device_registry.devices.items()
            if device.area_id == room_name
        }
        _LOGGER.warn(f"Devices in Room '{devices}")
        return {
            "entities": entities,
            "devices": devices,
        }

    async def get_filtered_entities(self, room_name):
        """Hole die gefilterten Entitäten für einen Raum."""
        # Hole registrierte Entitäten
        entity_registry = async_get_entity_registry(self.hass)
        registered_entities = {
            entity.entity_id: entity
            for entity in entity_registry.entities.values()
            if entity.area_id == room_name
        }

        # Hole Geräte und verknüpfte Entitäten
        device_registry = async_get_device_registry(self.hass)
        devices_in_room = {
            device.id: device
            for device in device_registry.devices.values()
            if device.area_id == room_name
        }

        # Verknüpfte Entitäten von Geräten
        device_entities = {
            entity.entity_id: entity
            for entity in entity_registry.entities.values()
            if entity.device_id in devices_in_room
        }

        # Kombiniere alle relevanten Entitäten
        combined_entities = {**registered_entities, **device_entities}

        # Rückgabe der `entity_id`s als Set
        return set(combined_entities.keys())

    async def get_filtered_entities_with_value(self, room_name, max_retries=5, retry_interval=1):
        """
        Hole die gefilterten Entitäten für einen Raum und deren Werte, gefiltert nach relevanten Typen.
        Gruppiere Entitäten basierend auf ihrem Präfix (device_name).
        """
        entity_registry = async_get_entity_registry(self.hass)
        device_registry = async_get_device_registry(self.hass)

        # Geräte im Raum filtern
        devices_in_room = {
            device.id: device
            for device in device_registry.devices.values()
            if device.area_id == room_name
        }
        
        # Relevante Präfixe und Schlüsselwörter
        relevant_prefixes = ("number.", "select.", "switch.", "light.", "time.","date.","text.","humidifier.", "fan.")
        relevant_keywords = ("_temperature", "_humidity", "_dewpoint", "_duty","_voltage","co2",)
        relevant_types = {
            "temperature": "Temperature entity found",
            "humidity": "Humidity entity found",
            "dewpoint": "Dewpoint entity found",
        }
        invalid_values = [None, "unknown", "unavailable", "Unbekannt"]

        grouped_entities_array = []

        async def process_entity(entity):
            """Verarbeite eine einzelne Entität mit Retry-Logik."""
            if entity.device_id not in devices_in_room:
                return None

            if not (entity.entity_id.startswith(relevant_prefixes) or
                    any(keyword in entity.entity_id for keyword in relevant_keywords)):
                return None

            # Extrahiere den Gerätenamen aus `entity_id`
            parts = entity.entity_id.split(".")
            device_name = parts[1].split("_")[0] if len(parts) > 1 else "Unknown"

            # Retry-Logik für den Wert
            state_value = None
            for attempt in range(max_retries):
                entity_state = self.hass.states.get(entity.entity_id)
                state_value = entity_state.state if entity_state else None
                if state_value not in invalid_values:
                    break
                _LOGGER.debug(f"Value for {entity.entity_id} is invalid ({state_value}). Retrying... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_interval)

            if state_value in invalid_values:
                _LOGGER.debug(f"Value for {entity.entity_id} is still invalid ({state_value}) after {max_retries} retries. Skipping...")
                return None

            # Erstelle die Gruppierung
            return {
                "device_name": device_name,
                "entity_id": entity.entity_id,
                "value": state_value,
            }

        # Verarbeite alle Entitäten parallel
        tasks = [process_entity(entity) for entity in entity_registry.entities.values()]
        results = await asyncio.gather(*tasks)

        # Gruppiere die Ergebnisse in das Array
        for result in filter(None, results):
            device_name = result["device_name"]

            # Gruppiere nach Gerätename
            group = next((g for g in grouped_entities_array if g["name"] == device_name), None)
            if not group:
                # Erstelle eine neue Gruppe, falls nicht vorhanden
                group = {"name": device_name, "entities": []}
                grouped_entities_array.append(group)

            # Füge die Entität zur Gruppe hinzu
            group["entities"].append({
                "entity_id": result["entity_id"],
                "value": result["value"],
            })

            # Überprüfe auf relevante Schlüsselwörter in der `entity_id`
            for key, message in relevant_types.items():
                if key in result["entity_id"]:
                    if "ogb_" in result["entity_id"]:
                        _LOGGER.debug(f"Skipping 'ogb_' entity: {result['entity_id']}")
                        continue
                    
                    # Füge die Entität zur WorkData hinzu
                    workdataStore = self.dataStore.getDeep(f"workData.{key}")
                    workdataStore.append({
                        "entity_id": result["entity_id"],
                        "value": result["value"],
                    })
                    _LOGGER.debug(f"{self.room_name} Updated WorkDataLoad {workdataStore} with {key}")
                    self.dataStore.setDeep(f"workData.{key}", workdataStore)

        # Debug-Ausgabe der gruppierten Ergebnisse
        _LOGGER.debug(f"Grouped Entities Array for Room '{room_name}': {grouped_entities_array}")
        return grouped_entities_array
     
    async def get_filtered_entities_with_valueForDevice(self, room_name, max_retries=5, retry_interval=1):
        """
        Hole die gefilterten Entitäten für einen Raum und deren Werte, gefiltert nach relevanten Typen.
        Gruppiere Entitäten basierend auf ihrem Präfix (device_name).
        """
        entity_registry = async_get_entity_registry(self.hass)
        device_registry = async_get_device_registry(self.hass)

        # Geräte im Raum filtern
        devices_in_room = {
            device.id: device
            for device in device_registry.devices.values()
            if device.area_id == room_name
        }
        
        # Relevante Präfixe und Schlüsselwörter
        relevant_prefixes = ("number.", "select.", "switch.", "light.", "time.","date.","text.", "humidifier.", "fan.")
        relevant_keywords = ("_temperature", "_humidity", "_dewpoint", "_duty","_voltage", "co2",)
        relevant_types = {
            "temperature": "Temperature entity found",
            "humidity": "Humidity entity found",
            "dewpoint": "Dewpoint entity found",
        }
        invalid_values = [None, "unknown", "unavailable", "Unbekannt"]

        grouped_entities_array = []

        async def process_entity(entity):
            """Verarbeite eine einzelne Entität mit Retry-Logik."""
            if entity.device_id not in devices_in_room:
                return None

            if not (entity.entity_id.startswith(relevant_prefixes) or
                    any(keyword in entity.entity_id for keyword in relevant_keywords)):
                return None

            # Extrahiere den Gerätenamen aus `entity_id`
            parts = entity.entity_id.split(".")
            device_name = parts[1].split("_")[0] if len(parts) > 1 else "Unknown"

            # Retry-Logik für den Wert
            state_value = None
            for attempt in range(max_retries):
                entity_state = self.hass.states.get(entity.entity_id)
                state_value = entity_state.state if entity_state else None
                if state_value not in invalid_values:
                    break
                _LOGGER.debug(f"Value for {entity.entity_id} is invalid ({state_value}). Retrying... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_interval)

            if state_value in invalid_values:
                _LOGGER.debug(f"Value for {entity.entity_id} is still invalid ({state_value}) after {max_retries} retries. Skipping...")
                return None

            # Erstelle die Gruppierung
            return {
                "device_name": device_name,
                "entity_id": entity.entity_id,
                "value": state_value,
            }

        # Verarbeite alle Entitäten parallel
        tasks = [process_entity(entity) for entity in entity_registry.entities.values()]
        results = await asyncio.gather(*tasks)

        # Gruppiere die Ergebnisse in das Array
        for result in filter(None, results):
            device_name = result["device_name"]

            # Gruppiere nach Gerätename
            group = next((g for g in grouped_entities_array if g["name"] == device_name), None)
            if not group:
                group = {"name": device_name, "entities": []}
                grouped_entities_array.append(group)

            group["entities"].append({
                "entity_id": result["entity_id"],
                "value": result["value"],
            })

        # Debug-Ausgabe der gruppierten Ergebnisse
        _LOGGER.debug(f"Grouped Entities Array for Room '{room_name}': {grouped_entities_array}")
        return grouped_entities_array

    # LIVE Event Monitoring 
    async def monitor_filtered_entities(self, room_name):
        """Überwache State-Changes nur für gefilterte Entitäten."""
        # Hole die gefilterten Entitäten
        filtered_entity_ids = await self.get_filtered_entities(room_name.lower())

        async def registryEventListener(event):
            """Callback für State-Changes."""
            entity_id = event.data.get("entity_id")
            if entity_id in filtered_entity_ids:
                old_state = event.data.get("old_state")
                new_state = event.data.get("new_state")

                def parse_state(state):
                    """Konvertiere den Zustand zu float oder lasse ihn als String."""
                    if state and state.state:
                        # Versuche, den Wert in einen Float umzuwandeln
                        try:
                            return float(state.state)
                        except ValueError:
                            # Wenn nicht möglich, behalte den ursprünglichen String
                            return state.state
                    return None

                old_state_value = parse_state(old_state)
                new_state_value = parse_state(new_state)

                # Erstelle das OGBEventPublication-Objekt
                eventData = OGBEventPublication(
                    Name=entity_id,
                    oldState=[old_state_value] if old_state_value is not None else [],
                    newState=[new_state_value] if new_state_value is not None else []
                )

                _LOGGER.warn(
                    f"State-Change für {entity_id} in {room_name}: "
                    f"Alt: {old_state_value}, Neu: {new_state_value}"
                )

                # Gib das Event-Publication-Objekt weiter
                await self.eventManager.emit("RoomUpdate", eventData)
                # Light Shedule Check
                await self.eventManager.emit("LightSheduleUpdate",None)
                
        # Registriere den Listener
        self.hass.bus.async_listen("state_changed", registryEventListener)
        _LOGGER.debug(f"State-Change Listener für Raum {room_name} registriert.")
        
import asyncio
import json
import logging

from homeassistant.core import callback
from homeassistant.helpers.area_registry import \
    async_get as async_get_area_registry
from homeassistant.helpers.device_registry import \
    async_get as async_get_device_registry
from homeassistant.helpers.entity_registry import \
    async_get as async_get_entity_registry
from homeassistant.helpers.label_registry import \
    async_get as async_get_label_registry

from .data.OGBDataClasses.OGBPublications import (OGBEventPublication, OGBVPDPublication)
from .data.OGBParams.OGBParams import (INVALID_VALUES, RELEVANT_KEYWORDS, RELEVANT_PREFIXES)
from .utils.sensor_identification import resolve_sensor_types
from .utils.lightTimeHelpers import update_light_state

_LOGGER = logging.getLogger(__name__)


class OGBRegistryEvenListener:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Registry Listener"
        self.hass = hass
        self.data_store = dataStore
        self.event_manager = eventManager
        self.room_name = room

    async def get_entities_by_room_async(self, room_name):
        """Get all entities by room."""
        entities_by_room = {}

        # Debug: Show all entities before the loop starts
        all_entities = self.hass.states.async_all()
        _LOGGER.debug(f"All entities: {all_entities}")

        # Get all current states from Home Assistant
        for entity in all_entities:
            # Check if the entity is assigned to a room (area_id)
            area = entity.attributes.get("area_id")
            _LOGGER.debug(f"Entity: {entity.entity_id}, Area: {area}")

            if area and area == room_name:
                entities_by_room[entity.entity_id] = entity

        _LOGGER.debug(f"Entities in Room '{room_name}': {entities_by_room}")
        return entities_by_room

    def _has_modbus_labels(self, entity, label_registry) -> bool:
        """Check if entity has Modbus-relevant labels."""
        if not hasattr(entity, 'labels') or not entity.labels:
            return False

        modbus_keywords = [
            "modbus", "modbus_device", "modbus_tcp", "modbus_rtu",
            "modbus_sensor", "modbus_temp", "modbus_humidity"
        ]

        entity_labels = []
        for label_id in entity.labels:
            label_entry = label_registry.labels.get(label_id)
            if label_entry:
                entity_labels.append(label_entry.name.lower())

        return any(any(keyword in label for keyword in modbus_keywords)
                  for label in entity_labels)

    def _matches_sensor_translations(self, entity, label_registry) -> bool:
        """Return True if a sensor entity matches translated sensor types."""
        entity_id = getattr(entity, "entity_id", "") or ""
        if not entity_id.startswith("sensor."):
            return False

        labels = []
        for label_id in (getattr(entity, "labels", None) or []):
            label_entry = label_registry.labels.get(label_id)
            if label_entry:
                labels.append({"id": label_id, "name": label_entry.name})

        return bool(resolve_sensor_types(entity_id, labels))

    async def get_entities_and_devices_by_room(self, room_name):
        """Get all entities and devices by room."""
        # Get entities
        entities = {}
        for entity in self.hass.states.async_all():
            area = entity.attributes.get("area_id")
            if area and area == room_name:
                entities[entity.entity_id] = entity

        # Get devices
        device_registry = async_get_device_registry(self.hass)
        devices = {
            device_id: device
            for device_id, device in device_registry.devices.items()
            if device.area_id == room_name
        }
        _LOGGER.debug(f"Devices in Room '{devices}")
        return {
            "entities": entities,
            "devices": devices,
        }

    async def get_filtered_entities(self, room_name):
        """Get the filtered entities for a room."""
        # Get registered entities
        entity_registry = async_get_entity_registry(self.hass)
        registered_entities = {
            entity.entity_id: entity
            for entity in entity_registry.entities.values()
            if entity.area_id == room_name
        }

        # Get devices and linked entities
        device_registry = async_get_device_registry(self.hass)
        devices_in_room = {
            device.id: device
            for device in device_registry.devices.values()
            if device.area_id == room_name
        }

        # Linked entities from devices
        device_entities = {
            entity.entity_id: entity
            for entity in entity_registry.entities.values()
            if entity.device_id in devices_in_room
        }

        # CRITICAL: Include OGB configuration entities for this room
        # These entities (select, text, number, etc.) control OGB settings
        # and must be monitored even if their device isn't in the room area
        room_lower = room_name.lower()
        ogb_entities = {
            entity.entity_id: entity
            for entity in entity_registry.entities.values()
            if f"ogb_" in entity.entity_id and f"_{room_lower}" in entity.entity_id
        }

        # Combine all relevant entities
        combined_entities = {**registered_entities, **device_entities, **ogb_entities}

        # Return entity_ids as a set
        return set(combined_entities.keys())

    async def get_filtered_entities_with_value(
        self, room_name, max_retries=5, retry_interval=1
    ):
        """
        Get the filtered entities for a room and their values, filtered by relevant types.
        Group entities based on their prefix (device_name).
        Includes platform information, labels (entity + device).
        """
        entity_registry = async_get_entity_registry(self.hass)
        device_registry = async_get_device_registry(self.hass)
        label_registry = async_get_label_registry(self.hass)

        # Log all areas and devices for debugging
        all_areas = set()
        all_devices = []
        for device in device_registry.devices.values():
            if device.area_id:
                all_areas.add(device.area_id)
            all_devices.append(f"{device.name} (area: {device.area_id})")


        # Filter devices in room - try exact match first
        devices_in_room = {
            device.id: device
            for device in device_registry.devices.values()
            if device.area_id == room_name
        }

        # If no exact match, try case-insensitive match
        if not devices_in_room:
            devices_in_room = {
                device.id: device
                for device in device_registry.devices.values()
                if device.area_id and device.area_id.lower() == room_name.lower()
            }

        grouped_entities_array = []
        room_lower = room_name.lower()

        async def process_entity(entity):
            """Process a single entity with retry logic."""
            
            # NEW: Skip disabled entities
            if entity.disabled:
                return None
            
            is_ogb_room_entity = "ogb_" in entity.entity_id and f"_{room_lower}" in entity.entity_id
            
            if not is_ogb_room_entity and entity.device_id not in devices_in_room:
                return None

            if not (
                entity.entity_id.startswith(RELEVANT_PREFIXES)
                or any(keyword in entity.entity_id for keyword in RELEVANT_KEYWORDS)
                or self._matches_sensor_translations(entity, label_registry)
            ):
                return None

            parts = entity.entity_id.split(".")
            device_name = parts[1].split("_")[0] if len(parts) > 1 else "Unknown"

            # Retry logic for the value
            state_value = None
            for attempt in range(max_retries):
                entity_state = self.hass.states.get(entity.entity_id)
                state_value = entity_state.state if entity_state else None
                if state_value not in INVALID_VALUES:
                    break
                #_LOGGER.debug(
                #    f"Value for {entity.entity_id} invalid ({state_value}), retry {attempt + 1}/{max_retries}"
                #)
                await asyncio.sleep(retry_interval)

            if state_value in INVALID_VALUES:
                # Keep sensor + actuator entities even if value is currently unavailable/unknown.
                # This is important during startup when some entities report None/unknown briefly,
                # otherwise device discovery can miss control entities (e.g. special lights).
                keep_prefixes = (
                    "sensor.",
                    "light.",
                    "switch.",
                    "fan.",
                    "climate.",
                    "humidifier.",
                )
                if entity.entity_id.startswith(keep_prefixes):
                    _LOGGER.warning(
                        f"Keeping {entity.entity_id} despite invalid initial value ({state_value})"
                    )
                    state_value = None
                else:
                    _LOGGER.error(
                        f"Skipping {entity.entity_id}, value invalid after retries ({state_value})"
                    )
                    return None

            platform = getattr(entity, "platform", "unknown")

            # Read labels (entity + device)
            labels = []

            # Entity labels
            if getattr(entity, "labels", None):
                for label_id in entity.labels:
                    label_entry = label_registry.labels.get(label_id)
                    if label_entry:
                        labels.append(
                            {
                                "id": label_id,
                                "name": label_entry.name,
                                "scope": "entity",
                                "icon": getattr(label_entry, "icon", None),
                                "color": getattr(label_entry, "color", None),
                            }
                        )

            # Device labels (for entity)
            device_info = devices_in_room.get(entity.device_id)
            device_labels = []  # Collect device labels separately
            if device_info and getattr(device_info, "labels", None):
                for label_id in device_info.labels:
                    label_entry = label_registry.labels.get(label_id)
                    if label_entry:
                        device_label = {
                            "id": label_id,
                            "name": label_entry.name,
                            "icon": getattr(label_entry, "icon", None),
                            "color": getattr(label_entry, "color", None),
                            "scope": "device",
                        }
                        labels.append(device_label)
                        device_labels.append(device_label)

            device_manufacturer = (
                getattr(device_info, "manufacturer", "Unknown")
                if device_info
                else "Unknown"
            )
            device_model = (
                getattr(device_info, "model", "Unknown") if device_info else "Unknown"
            )

            return {
                "device_name": device_name,
                "device_id": entity.device_id,
                "entity_id": entity.entity_id,
                "value": state_value,
                "platform": platform,
                "labels": labels,
                "device_labels": device_labels,  # Separate device labels
                "device_manufacturer": device_manufacturer,
                "device_model": device_model,
            }

        # Process in parallel
        tasks = [process_entity(entity) for entity in entity_registry.entities.values()]
        results = await asyncio.gather(*tasks)

        # Grouping
        for result in filter(None, results):
            device_name = result["device_name"]

            group = next(
                (g for g in grouped_entities_array if g["name"] == device_name), None
            )
            if not group:
                group = {
                    "name": device_name,
                    "entities": [],
                    "platform": result["platform"],
                    "device_info": {
                        "manufacturer": result["device_manufacturer"],
                        "model": result["device_model"],
                    },
                    "labels": result[
                        "device_labels"
                    ],  # Device-Labels auf Gruppen-Ebene
                }
                grouped_entities_array.append(group)

            group["entities"].append(
                {
                    "entity_id": result["entity_id"],
                    "value": result["value"],
                    "platform": result["platform"],
                    "labels": result["labels"],
                }
            )

        return grouped_entities_array

    async def get_filtered_entities_with_valueForDevice(
        self, room_name, max_retries=5, retry_interval=1
    ):
        """
        Get the filtered entities for a room and their values, filtered by relevant types.
        Group entities based on their prefix (device_name).
        Includes platform information and labels (entity + device).
        """
        entity_registry = async_get_entity_registry(self.hass)
        device_registry = async_get_device_registry(self.hass)
        label_registry = async_get_label_registry(self.hass)

        # Filter devices in room
        devices_in_room = {
            device.id: device
            for device in device_registry.devices.values()
            if device.area_id == room_name
        }

        grouped_entities_array = []

        async def process_entity(entity):
            """Process a single entity with retry logic."""
            
            # NEW: Skip disabled entities
            if entity.disabled:
                return None
            
            # EXISTING LOGIC: Physical devices must be in the room
            if entity.device_id and entity.device_id not in devices_in_room:
                return None

            # NEW: Allow Modbus entities without device_id if they have labels
            if not entity.device_id:
                has_modbus_labels = self._has_modbus_labels(entity, label_registry)
                if not has_modbus_labels:
                    return None  # No Modbus labels -> ignore

            if not (
                entity.entity_id.startswith(RELEVANT_PREFIXES)
                or any(keyword in entity.entity_id for keyword in RELEVANT_KEYWORDS)
                or self._matches_sensor_translations(entity, label_registry)
            ):
                return None

            # Extract device name from `entity_id`
            parts = entity.entity_id.split(".")
            device_name = parts[1].split("_")[0] if len(parts) > 1 else "Unknown"

            # Retry logic for the value
            state_value = None
            for attempt in range(max_retries):
                entity_state = self.hass.states.get(entity.entity_id)
                state_value = entity_state.state if entity_state else None
                if state_value not in INVALID_VALUES:
                    break
                #_LOGGER.warning(
                #    f"Value for {entity.entity_id} is invalid ({state_value}). Retrying... ({attempt + 1}/{max_retries})"
                #)
                await asyncio.sleep(retry_interval)

            if state_value in INVALID_VALUES:
                if entity.entity_id.startswith("sensor."):
                    _LOGGER.warning(
                        f"Keeping {entity.entity_id} despite invalid initial value ({state_value})"
                    )
                    state_value = None
                else:
                    #_LOGGER.error(
                    #    f"Value for {entity.entity_id} is still invalid ({state_value}) after {max_retries} retries. Skipping..."
                    #)
                    return None

            # Platform information
            platform = getattr(entity, "platform", "unknown")

            # Read labels (entity + device)
            labels = []

            # Entity labels
            if getattr(entity, "labels", None):
                for label_id in entity.labels:
                    label_entry = label_registry.labels.get(label_id)
                    if label_entry:
                        labels.append(
                            {
                                "id": label_id,
                                "name": label_entry.name,
                                "scope": "entity",
                                "icon": getattr(label_entry, "icon", None),
                                "color": getattr(label_entry, "color", None),
                            }
                        )

            # Device labels (for entity)
            device_info = devices_in_room.get(entity.device_id)
            device_labels = []  # Collect device labels separately
            if device_info and getattr(device_info, "labels", None):
                for label_id in device_info.labels:
                    label_entry = label_registry.labels.get(label_id)
                    if label_entry:
                        device_label = {
                            "id": label_id,
                            "name": label_entry.name,
                            "icon": getattr(label_entry, "icon", None),
                            "color": getattr(label_entry, "color", None),
                            "scope": "device",
                        }
                        labels.append(device_label)
                        device_labels.append(device_label)

            device_manufacturer = (
                getattr(device_info, "manufacturer", "Unknown")
                if device_info
                else "Unknown"
            )
            device_model = (
                getattr(device_info, "model", "Unknown") if device_info else "Unknown"
            )

            # Create the grouping
            return {
                "device_name": device_name,
                "device_id": entity.device_id,
                "entity_id": entity.entity_id,
                "value": state_value,
                "platform": platform,
                "labels": labels,
                "device_labels": device_labels,
                "device_manufacturer": device_manufacturer,
                "device_model": device_model,
            }

        # Process all entities in parallel
        tasks = [process_entity(entity) for entity in entity_registry.entities.values()]
        results = await asyncio.gather(*tasks)

        # Group results into the array
        for result in filter(None, results):
            device_name = result["device_name"]

            # Group by device name
            group = next(
                (g for g in grouped_entities_array if g["name"] == device_name), None
            )
            if not group:
                group = {
                    "name": device_name,
                    "entities": [],
                    "platform": result["platform"],
                    "device_info": {
                        "manufacturer": result["device_manufacturer"],
                        "model": result["device_model"],
                    },
                    "labels": result[
                        "device_labels"
                    ],  # Device labels on group level
                }
                grouped_entities_array.append(group)

            group["entities"].append(
                {
                    "entity_id": result["entity_id"],
                    "value": result["value"],
                    "platform": result["platform"],
                    "labels": result["labels"],
                }
            )

        return grouped_entities_array

    # LIVE Event Monitoring
    async def monitor_filtered_entities(self, room_name):
        """Monitor state changes only for filtered entities."""
        filtered_entity_ids = await self.get_filtered_entities(room_name.lower())

        async def registryEventListener(event):
            """Callback for state changes."""
            entity_id = event.data.get("entity_id")
            if entity_id in filtered_entity_ids:
                old_state = event.data.get("old_state")
                new_state = event.data.get("new_state")

                def parse_state(state):
                    """Convert the state to float or leave as string."""
                    if state and state.state:
                        try:
                            return float(state.state)
                        except ValueError:
                            return state.state
                    return None

                old_state_value = parse_state(old_state)
                new_state_value = parse_state(new_state)

                eventData = OGBEventPublication(
                    Name=entity_id,
                    oldState=[old_state_value] if old_state_value is not None else [],
                    newState=[new_state_value] if new_state_value is not None else [],
                )

                _LOGGER.debug(
                    f"State-Change for {entity_id} in {room_name}: "
                    f"Old: {old_state_value}, New: {new_state_value}"
                )

                await self.event_manager.emit("SensorUpdate", eventData)
                await self.event_manager.emit("RoomUpdate", eventData)


        self._listen_to_entity_registry_changes()

        self.hass.bus.async_listen("state_changed", registryEventListener)
        _LOGGER.debug(f"State change listener for room {room_name} registered.")

    def _listen_to_entity_registry_changes(self):
        """Listen for entity enabled/disabled changes."""
        
        @callback
        async def handle_entity_registry_updated(event):
            """Handle entity registry updates (enable/disable)."""
            entity_id = event.data.get("entity_id")
            changes = event.data.get("changes")
            
            if not changes or "disabled_by" not in changes:
                return
            
            from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
            from homeassistant.helpers import device_registry as dr
            from homeassistant.core import callback
            
            entity_registry = async_get_entity_registry(self.hass)
            entry = entity_registry.async_get(entity_id)
            
            if not entry:
                return
            
            if entry.disabled:
                _LOGGER.warning(
                    f"{self.room_name}: Entity {entity_id} was DISABLED by {entry.disabled_by}"
                )
                # Remove from capabilities
                await self._remove_disabled_entity_from_capabilities(entry)
            else:
                _LOGGER.debug(
                    f"{self.room_name}: Entity {entity_id} was ENABLED"
                )
                # Re-register in capabilities
                await self._register_enabled_entity_in_capabilities(entry)
        
        # Subscribe to entity registry events
        self.hass.bus.async_listen(
            "entity_registry_updated",
            handle_entity_registry_updated
        )
        _LOGGER.debug(f"Entity registry listener for room {self.room_name} registered.")

    async def _remove_disabled_entity_from_capabilities(self, entry):
        """Remove disabled entity from capabilities."""
        if not entry.device_id:
            return
        
        # Get current capabilities
        capabilities = self.data_store.get("capabilities") or {}
        
        # Find which capabilities this device belongs to
        # Extract device name from entity_id (e.g., "switch.devdoor" -> "devdoor")
        device_name = entry.entity_id.split(".", 1)[1] if "." in entry.entity_id else entry.entity_id
        
        for cap, cap_data in capabilities.items():
            if device_name in cap_data.get("devEntities", []):
                # Remove device from capability
                cap_data["devEntities"].remove(device_name)
                cap_data["count"] = len(cap_data["devEntities"])

                # Remove from deviceData
                if "deviceData" in cap_data and device_name in cap_data["deviceData"]:
                    del cap_data["deviceData"][device_name]

                # Update state if no devices left
                if cap_data["count"] == 0:
                    cap_data["state"] = False

                # Update capabilities
                self.data_store.setDeep(f"capabilities.{cap}", cap_data)
                
                _LOGGER.debug(
                    f"{self.room_name}: Removed disabled device {device_name} "
                    f"from capability {cap}. Remaining: {cap_data['count']}"
                )

    async def _register_enabled_entity_in_capabilities(self, entry):
        """Re-register enabled entity in capabilities."""
        if not entry.device_id:
            return
        
        from .data.OGBParams.OGBParams import CAP_MAPPING
        
        # Find which capabilities this device type belongs to
        for cap, device_types in CAP_MAPPING.items():
            # Check if entity matches device type
            entity_device = entry.device_id.split(".")[1].split("_")[0] if "." in entry.device_id and "_" in entry.device_id else None
            
            if not entity_device:
                continue
            
            if entity_device.lower() in (dt.lower() for dt in device_types):
                cap_path = f"capabilities.{cap}"
                current_cap = self.data_store.getDeep(cap_path)
                
                if not current_cap:
                    continue
                
                # Re-register device
                # Extract device name from entity_id (e.g., "switch.devdoor" -> "devdoor")
                device_name = entry.entity_id.split(".", 1)[1] if "." in entry.entity_id else entry.entity_id
                
                if device_name not in current_cap["devEntities"]:
                    current_cap["devEntities"].append(device_name)
                    current_cap["count"] = len(current_cap["devEntities"])
                    current_cap["state"] = True
                    
                    self.data_store.setDeep(cap_path, current_cap)
                    
                    _LOGGER.debug(
                        f"{self.room_name}: Re-enabled device {device_name} "
                        f"in capability {cap}. Total: {current_cap['count']}"
                    )

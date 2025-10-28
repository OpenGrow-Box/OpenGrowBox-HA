import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..OGBDataClasses.OGBMedium import GrowMedium, MediumType

_LOGGER = logging.getLogger(__name__)


class OGBMediumManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Grow Medium Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self._save_task: Optional[asyncio.Task] = None
        self._save_delay_seconds = 5  # Warte 5 Sekunden nach letztem Update
        
        self.media: List[GrowMedium] = []
        self.current_medium_type: Optional[MediumType] = None
        
        # Entity-ID zu Medium-Index Mapping
        self._entity_to_medium_index: Dict[str, int] = {}
        
        asyncio.create_task(self.init())

    async def init(self):
        """Initialize Medium Manager"""
        await self._load_mediums_from_store()
        self._setup_event_listeners()
        await self._start_daily_update_timer()
        _LOGGER.info(f"🌱 Medium Manager initialized for room: {self.room} with {len(self.media)} mediums")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self.eventManager.on("MediumChange", self._on_new_medium_change)
        self.eventManager.on("RegisterSensorToMedium", self._on_register_sensor)
        self.eventManager.on("UnregisterSensorFromMedium", self._on_unregister_sensor)
        self.eventManager.on("MediumSensorUpdate", self._on_medium_sensor_update)
        
        _LOGGER.info(f"Medium Manager: Event listeners registered")

    async def _on_register_sensor(self, data):
        """
        Registriert einen Sensor bei einem Medium.
        
        Expected data:
        {
            "entity_id": "sensor.medium_1_soil_temperature",
            "sensor_type": "temperature",
            "medium_label": "medium_1",
            "room": "room_name"
        }
        """
        try:
            logging.debug(f"{self.room} Sensor Registration on medium {data} ")
            # Nur für diesen Room
            if data.get("room") != self.room:
                return
            
            entity_id = data.get("entity_id")
            sensor_type = data.get("sensor_type")
            medium_label = data.get("medium_label")
            room = data.get("room")
            value = data.get("value")
            unit = data.get("unit")
            context = data.get("context")
            fromDevice = data.get("device_name")
            
            
            if not entity_id or not sensor_type or not medium_label:
                _LOGGER.error("RegisterSensorToMedium: entity_id, sensor_type und medium_label erforderlich")
                return
            
            # Extrahiere Medium-Nummer aus Label (z.B. "medium_1" -> 1)
            try:
                medium_number = int(medium_label.split("_")[-1])
                medium_index = medium_number - 1  # Array ist 0-basiert
            except (ValueError, IndexError):
                _LOGGER.error(f"Konnte Medium-Nummer nicht aus Label extrahieren: {medium_label}")
                return
            
            # Prüfe ob Medium existiert
            if 0 <= medium_index < len(self.media):
                medium = self.media[medium_index]
                
                sensor_data = {
                    "entity_id": entity_id,
                    "sensor_type": sensor_type,
                    "value": value,
                    "unit": unit,
                    "context": context,
                    "room": room,
                    "medium_label": medium_label
                }
                
                await medium.register_sensor(sensor_data)
                self._entity_to_medium_index[entity_id] = medium_index
                
                _LOGGER.debug(
                    f" Sensor {entity_id} ({sensor_type}) zu Medium {medium.name} "
                    f"(Index {medium_index}) registriert"
                )
                
                self._save_mediums_to_store()
            else:
                _LOGGER.warning(
                    f"Medium mit Index {medium_index} existiert nicht. "
                    f"Verfügbare Medien: {len(self.media)}"
                )
                
        except Exception as e:
            _LOGGER.error(f"Fehler bei Sensor-Registrierung: {e}", exc_info=True)

    async def _on_unregister_sensor(self, data):
        """Entfernt einen Sensor von einem Medium."""
        try:
            if data.get("room") != self.room:
                return
            
            entity_id = data.get("entity_id")
            
            if entity_id in self._entity_to_medium_index:
                medium_index = self._entity_to_medium_index[entity_id]
                medium = self.media[medium_index]
                
                if medium.unregister_sensor(entity_id):
                    del self._entity_to_medium_index[entity_id]
                    _LOGGER.info(f"Sensor {entity_id} von Medium {medium.name} entfernt")
                    self._save_mediums_to_store()
                    
        except Exception as e:
            _LOGGER.error(f"Fehler beim Entfernen von Sensor: {e}", exc_info=True)

    async def _on_medium_sensor_update(self, data):
        """
        Verarbeitet Sensor-Updates für Medien.
        
        Expected data:
        {
            "entity_id": "sensor.medium_1_soil_temperature",
            "sensor_type": "temperature",
            "value": 22.5,
            "timestamp": datetime(...),
            "medium_label": "medium_1",
            "room": "room_name"
        }
        """
        try:
            # Nur für diesen Room

            
            entity_id = data.get("entity_id")
            sensor_type = data.get("sensor_type")
            sensor_context = data.get("context")
            device_class = data.get("device_class")
            last_value = data.get("state")

            timestamp = data.get("timestamp")
            
            # Prüfe ob Sensor registriert ist
            if entity_id not in self._entity_to_medium_index:
                _LOGGER.error(f"Sensor {entity_id} ist keinem Medium zugeordnet")
                return
            
            medium_index = self._entity_to_medium_index[entity_id]
            
            # Prüfe ob Medium noch existiert
            if medium_index >= len(self.media):
                _LOGGER.error(f"Medium Index {medium_index} existiert nicht mehr")
                # Cleanup
                del self._entity_to_medium_index[entity_id]
                return
            
            medium = self.media[medium_index]
            await medium.update_sensor_reading_async(data)
            # Debounced save
            await self._schedule_save()
            
        except Exception as e:
            _LOGGER.error(f"Fehler beim Medium-Sensor-Update: {e}", exc_info=True)

    async def _schedule_save(self):
        """Speichert nach Verzögerung, resettet Timer bei neuen Updates"""
        # Cancel pending save
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        
        # Schedule new save
        self._save_task = asyncio.create_task(self._delayed_save())

    async def _delayed_save(self):
        """Wartet und speichert dann"""
        try:
            await asyncio.sleep(self._save_delay_seconds)
            self._save_mediums_to_store()
            _LOGGER.debug(f"Mediums nach Update-Batch gespeichert")
        except asyncio.CancelledError:
            pass  # Normal wenn neue Updates kommen

    async def _load_mediums_from_store(self):
        """Load existing mediums from dataStore on startup"""
        stored_mediums = self.dataStore.get("growMediums")
        
        if stored_mediums and len(stored_mediums) > 0:
            self.media = []
            
            for medium_dict in stored_mediums:
                try:
                    medium = GrowMedium.from_dict(medium_dict)
                    self.media.append(medium)
                    
                    # Rebuild entity_to_medium_index mapping
                    medium_index = len(self.media) - 1
                    for sensor_type, entity_ids in medium.registered_sensors.items():
                        for entity_id in entity_ids:
                            self._entity_to_medium_index[entity_id] = medium_index
                    
                except Exception as e:
                    _LOGGER.error(f"Failed to load medium from dict: {medium_dict}, Error: {e}")
            
            if self.media:
                self.current_medium_type = self.media[0].medium_type
                self._sync_medium_names()
            
            _LOGGER.info(
                f"Loaded {len(self.media)} mediums from dataStore for room {self.room}. "
                f"Registered sensors: {len(self._entity_to_medium_index)}"
            )
        else:
            _LOGGER.info(f"No existing mediums found - creating default medium for room {self.room}")
            await self._create_default_medium()

    async def _create_default_medium(self):
        """Create a default medium"""
        default_type = MediumType.SOIL
        self.current_medium_type = default_type
        await self._create_mediums(default_type, 1)
        _LOGGER.info(f"Created default medium: {default_type.value}_1 for room {self.room}")

    async def _on_new_medium_change(self, data):
        """Called when a new medium setup event is triggered."""
        if not data:
            return

        try:
            base, count = self._parse_medium_input(data)
        except ValueError as e:
            _LOGGER.error(f"Invalid medium input: {data} -> {e}")
            return

        if count < 1:
            _LOGGER.warning(f"Cannot have less than 1 medium, setting count to 1")
            count = 1

        await self._sync_mediums(base, count)

    def _parse_medium_input(self, input_str: str) -> tuple:
        """Parse input like 'COCOx3' or 'SOILx1'"""
        try:
            parts = input_str.upper().split("X")
            medium_name = parts[0].strip()
            count = int(parts[1]) if len(parts) > 1 else 1
            medium_type = MediumType[medium_name]
            return medium_type, count
        except (KeyError, ValueError, IndexError):
            raise ValueError("Input format must be like 'COCOx3', 'SOILx1', etc.")

    async def _sync_mediums(self, new_type: MediumType, desired_count: int):
        """Ensure mediums match desired type and count."""
        current_count = len(self.media)

        if self.current_medium_type != new_type:
            _LOGGER.info(f"Medium type changed: {self.current_medium_type} -> {new_type}")
            self.media.clear()
            self._entity_to_medium_index.clear()
            self.current_medium_type = new_type
            await self._create_mediums(new_type, desired_count)
        else:
            if desired_count > current_count:
                diff = desired_count - current_count
                _LOGGER.warning(f"{self.room} Adding {diff} new mediums of type {new_type.value}")
                await self._create_mediums(new_type, diff, start_index=current_count)
            elif desired_count < current_count:
                diff = current_count - desired_count
                _LOGGER.warning(f"{self.room} Removing {diff} newest mediums")
                
                # Cleanup sensor mappings für gelöschte Medien
                for i in range(desired_count, current_count):
                    medium = self.media[i]
                    for sensor_type, entity_ids in medium.registered_sensors.items():
                        for entity_id in entity_ids:
                            if entity_id in self._entity_to_medium_index:
                                del self._entity_to_medium_index[entity_id]
                
                self.media = self.media[:desired_count]
                self._sync_medium_names()
                self._save_mediums_to_store()

    def _sync_medium_names(self):
        """Ensure all medium names match their array index (1-based)."""
        for i, medium in enumerate(self.media):
            expected_name = f"{medium.medium_type.value}_{i + 1}"
            if medium.name != expected_name:
                _LOGGER.debug(f"Renaming medium from {medium.name} to {expected_name}")
                medium.name = expected_name

    async def _create_mediums(self, medium_type: MediumType, count: int, start_index: int = 0):
        """Create new mediums and store them."""
        for i in range(count):
            index = start_index + i + 1
            name = f"{medium_type.value}_{index}"
            medium = GrowMedium(self.eventManager,self.dataStore,self.room, medium_type=medium_type, name=name)
            self.media.append(medium)
            _LOGGER.info(f"Created medium {name}")
        
        self._sync_medium_names()
        self._save_mediums_to_store()

    def _save_mediums_to_store(self):
        """Save current mediums to dataStore"""
        mediums_as_dicts = [medium.to_dict() for medium in self.media]
        self.dataStore.set("growMediums", mediums_as_dicts)
             
    async def _start_daily_update_timer(self):
        """Schedule daily update for media status"""
        async def daily_update():
            while True:
                await asyncio.sleep(24 * 3600)
                _LOGGER.info(f"Daily medium check for {self.room}: {len(self.media)} active mediums")
                
                # Log status
                for medium in self.media:
                    status = medium.get_status()
                    _LOGGER.info(f"Medium {medium.name}: {status}")

        asyncio.create_task(daily_update())

    def get_mediums(self) -> List[GrowMedium]:
        """Return all active mediums"""
        return self.media

    def get_medium_by_index(self, index: int) -> Optional[GrowMedium]:
        """Gibt ein Medium nach Index zurück."""
        if 0 <= index < len(self.media):
            return self.media[index]
        return None
    
    def get_medium_by_name(self, name: str) -> Optional[GrowMedium]:
        """Gibt ein Medium nach Namen zurück."""
        for medium in self.media:
            if medium.name.lower() == name.lower():
                return medium
        return None
    
    def get_all_medium_readings(self) -> List[Dict[str, Any]]:
        """Gibt alle Medium-Readings zurück."""
        return [medium.get_all_readings() for medium in self.media]

    def get_status(self) -> Dict[str, Any]:
        """Return status summary for all mediums"""
        return {
            "room": self.room,
            "total_media": len(self.media),
            "registered_sensors": len(self._entity_to_medium_index),
            "media": [m.get_status() for m in self.media],
        }
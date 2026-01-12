"""
OpenGrowBox Medium Manager

Full implementation with:
- Medium creation and management (SOIL, COCO, AERO, etc.)
- Sensor registration and updates per medium
- Plant dates tracking per medium
- Event emission for frontend (MediumPlantsUpdate)
- Persistence to datastore
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...data.OGBDataClasses.OGBMedium import GrowMedium, MediumType
from ...data.OGBDataClasses.OGBPublications import OGBMediumPlantPublication

_LOGGER = logging.getLogger(__name__)


class OGBMediumManager:
    def __init__(self, hass, data_store, event_manager, room):
        self.name = "OGB Grow Medium Manager"
        self.hass = hass
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager
        self._save_task: Optional[asyncio.Task] = None
        self._save_delay_seconds = 5  # Increased to 5 seconds to reduce UI spam
        self._last_save_hash: Optional[int] = None  # Track data changes via hash
        self._save_count = 0  # Track save frequency for monitoring

        self.media: List[GrowMedium] = []
        self.current_medium_type: Optional[MediumType] = None

        # Backwards compatibility alias
        # Some code uses .mediums instead of .media
        
        # Entity-ID to Medium-Index Mapping
        self._entity_to_medium_index: Dict[str, int] = {}

        # Track background tasks for cleanup
        self._background_tasks: set = set()
        self._daily_update_task: Optional[asyncio.Task] = None
        
        # Flag to track initialization status
        self._initialized = False
        
        # Queue for sensor registrations that arrive before init completes
        self._pending_sensor_registrations: List[Dict[str, Any]] = []

        # CRITICAL: Setup event listeners IMMEDIATELY in __init__ 
        # so we don't miss any events that come before async init() completes
        self._setup_event_listeners()
        
        # NOTE: Do NOT auto-start init() here as a background task!
        # coordinator.py must explicitly await init() BEFORE managerInit()
        # to ensure mediums are loaded from datastore before MediumChange events arrive.
        # Otherwise the race condition causes plant_name/breeder_name to be lost.

    @property
    def mediums(self) -> List[GrowMedium]:
        """Backwards compatibility alias for self.media."""
        return self.media

    def _create_tracked_task(self, coro):
        """Create a background task and track it for cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def init(self):
        """Initialize Medium Manager - load data and emit initial state"""
        # Guard against double initialization
        if self._initialized:
            _LOGGER.debug(f"[{self.room}] ⚠️ MediumManager.init() called but already initialized! Skipping.")
            return
        
        _LOGGER.debug(f"[{self.room}] 🚀 MediumManager.init() STARTING")
        await self._load_mediums_from_store()
        # Note: Event listeners are already set up in __init__ to avoid race conditions
        
        # Mark as initialized BEFORE processing pending registrations
        self._initialized = True
        _LOGGER.debug(f"[{self.room}] ✅ MediumManager.init() COMPLETE - {len(self.media)} mediums loaded")
        
        # Process any sensor registrations that arrived before init completed
        if self._pending_sensor_registrations:
            _LOGGER.debug(f"[{self.room}] Processing {len(self._pending_sensor_registrations)} pending sensor registrations")
            for sensor_data in self._pending_sensor_registrations:
                await self._process_sensor_registration(sensor_data)
            self._pending_sensor_registrations.clear()
        
        await self._start_daily_update_timer()
        
        # Emit initial medium and plant data to UI
        await self._emit_initial_data()
        
        _LOGGER.debug(
            f"[{self.room}] Medium Manager FULLY initialized with {len(self.media)} mediums, {len(self._entity_to_medium_index)} registered sensors"
        )
    
    async def _emit_initial_data(self):
        """Emit initial medium and plant data to UI on startup."""
        try:
            _LOGGER.info(f"{self.room}: Emitting initial data for {len(self.media)} mediums")
            
            # Emit all plants data to MediumContext
            await self.emit_all_plants_update()
            
            # Also emit each medium's current values for monitoring
            for medium in self.media:
                medium_data = medium.get_all_medium_values()
                await self.event_manager.emit("LogForClient", medium_data, haEvent=True)
                _LOGGER.debug(f"Emitted initial data for medium: {medium.name}")
            
            _LOGGER.info(f"{self.room}: Initial data emission complete")
        except Exception as e:
            _LOGGER.error(f"Error emitting initial medium data: {e}", exc_info=True)

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self.event_manager.on("MediumChange", self._on_new_medium_change)
        self.event_manager.on("RegisterSensorToMedium", self._on_register_sensor)
        self.event_manager.on("UnregisterSensorFromMedium", self._on_unregister_sensor)
        self.event_manager.on("MediumSensorUpdate", self._on_medium_sensor_update)
        # Plant date update events
        self.event_manager.on("UpdateMediumPlantDates", self._on_update_plant_dates)
        self.event_manager.on("RequestMediumPlantsData", self._on_request_plants_data)
        # Global plantStage changes - sync all mediums
        self.event_manager.on("PlantStageChange", self._on_global_plant_stage_change)
        # Finish grow event - complete a grow cycle
        self.event_manager.on("FinishGrow", self._on_finish_grow)

        _LOGGER.debug(f"[{self.room}] Medium Manager: Event listeners registered for MediumChange, RegisterSensorToMedium, MediumSensorUpdate, UpdateMediumPlantDates, RequestMediumPlantsData, PlantStageChange, FinishGrow")

    async def _on_update_plant_dates(self, data: Dict[str, Any]):
        """
        Handle plant date update requests from UI/config.
        
        Expected data (UI may send different field names):
        {
            "room": "room_name",
            "medium_index": 0,  # or "medium_name": "coco_1"
            "grow_start": "2024-01-15",
            "bloom_switch": "2024-03-01",
            "breeder_bloom_days": 60,
            "plant_stage": "MidFlower",
            "plant_name": "Northern Lights #1",
            "breeder_name": "Northern Lights",  # OR "plant_strain" OR "strain"
            "plant_type": "photoperiodic"
        }
        """
        _LOGGER.debug(f"[{self.room}] 📥 UpdateMediumPlantDates EVENT RECEIVED: {data}")
        _LOGGER.debug(f"[{self.room}] 📥 ALL KEYS in data: {list(data.keys())}")
        
        if data.get("room") != self.room:
            _LOGGER.debug(f"[{self.room}] Ignoring event for room: {data.get('room')}")
            return
            
        # Get medium by index or name
        medium_index = data.get("medium_index")
        medium_name = data.get("medium_name")
        
        _LOGGER.debug(f"[{self.room}] Processing update for medium_index={medium_index}, medium_name={medium_name}")
        
        if medium_index is None and medium_name:
            # Find by name
            for i, m in enumerate(self.media):
                if m.name.lower() == medium_name.lower():
                    medium_index = i
                    break
                    
        if medium_index is None:
            _LOGGER.error(f"[{self.room}] UpdateMediumPlantDates: No medium_index or medium_name provided in data: {data}")
            return
        
        # FIELD MAPPING: UI may send different field names for strain/breeder
        # Check multiple possible field names and use first non-empty value
        # Get breeder_name from various possible field names
        # Use None if not provided so we don't overwrite existing value with empty string
        breeder_name = (
            data.get("breeder_name") or 
            data.get("plant_strain") or 
            data.get("strain") or 
            data.get("breeder") or
            data.get("strain_name") or
            None  # Use None instead of "" to not overwrite existing value
        )
        
        _LOGGER.debug(f"[{self.room}] FIELD MAPPING: breeder_name='{data.get('breeder_name')}', "
                       f"plant_strain='{data.get('plant_strain')}', strain='{data.get('strain')}' "
                       f"-> RESOLVED breeder_name='{breeder_name}'")
        
        _LOGGER.debug(f"[{self.room}] Calling update_medium_plant_dates with: "
                       f"index={medium_index}, name={data.get('plant_name')}, breeder={breeder_name}")
            
        await self.update_medium_plant_dates(
            medium_index=medium_index,
            grow_start=data.get("grow_start"),
            bloom_switch=data.get("bloom_switch"),
            breeder_bloom_days=data.get("breeder_bloom_days"),
            plant_stage=data.get("plant_stage"),
            plant_name=data.get("plant_name"),
            breeder_name=breeder_name,
            plant_type=data.get("plant_type"),
        )

    async def _on_request_plants_data(self, data: Dict[str, Any]):
        """Handle request for all plants data (for UI refresh)."""
        if data.get("room") != self.room:
            return
        await self.emit_all_plants_update()

    async def _on_finish_grow(self, data: Dict[str, Any]):
        """
        Handle finish grow event from UI.
        Archives the current grow data and resets the medium for a new grow.
        
        Expected data from frontend (GrowDayCounter.jsx):
        {
            "room": "room_name",
            "medium_index": 0,
            "medium_name": "coco_1",
            "plant_name": "Northern Lights #1",
            "breeder_name": "Sensi Seeds",
            "total_days": 90,
            "bloom_days": 60,
            "notes": "optional harvest notes"
        }
        """
        _LOGGER.debug(f"[{self.room}] 🏁 FinishGrow EVENT RECEIVED: {data}")
        
        if data.get("room") != self.room:
            _LOGGER.debug(f"[{self.room}] Ignoring FinishGrow event for room: {data.get('room')}")
            return
        
        medium_index = data.get("medium_index")
        if medium_index is None:
            _LOGGER.error(f"[{self.room}] FinishGrow: No medium_index provided")
            return
        
        # Call the finish method
        success = await self.finish_medium_grow(
            medium_index=medium_index,
            plant_name=data.get("plant_name"),
            breeder_name=data.get("breeder_name"),
            total_days=data.get("total_days"),
            bloom_days=data.get("bloom_days"),
            notes=data.get("notes"),
        )
        
        if success:
            _LOGGER.debug(f"[{self.room}] ✅ FinishGrow completed for medium index {medium_index}")
        else:
            _LOGGER.error(f"[{self.room}] ❌ FinishGrow failed for medium index {medium_index}")

    async def _on_register_sensor(self, data):
        """
        Registers a sensor with a medium.

        Expected data:
        {
            "entity_id": "sensor.medium_1_soil_temperature",
            "sensor_type": "temperature",
            "medium_label": "medium_1",
            "room": "room_name"
        }
        """
        try:
            _LOGGER.debug(f"[{self.room}] RegisterSensorToMedium EVENT RECEIVED: {data}")
            # Only for this room
            if data.get("room") != self.room:
                _LOGGER.debug(f"[{self.room}] Ignoring - event is for room: {data.get('room')}")
                return

            # If not yet initialized, queue the registration for later
            if not self._initialized:
                # Limit pending registrations to prevent memory leak (max 100)
                if len(self._pending_sensor_registrations) >= 100:
                    _LOGGER.debug(f"[{self.room}] Pending sensor registrations limit reached, dropping oldest")
                    self._pending_sensor_registrations.pop(0)
                _LOGGER.debug(f"[{self.room}] Queueing sensor registration (init not complete): {data.get('entity_id')}")
                self._pending_sensor_registrations.append(data)
                return
            
            await self._process_sensor_registration(data)

        except Exception as e:
            _LOGGER.error(f"Error in sensor registration: {e}", exc_info=True)

    async def _on_global_plant_stage_change(self, data):
        """Handle global plantStage changes - sync all mediums.
        
        When the room-level plantStage changes (e.g., user selects "MidFlower"),
        update all mediums to use this stage.
        """
        try:
            # Handle both string and dict format
            if isinstance(data, str):
                new_stage = data
            elif isinstance(data, dict):
                # Check if this is for our room
                event_room = data.get("room")
                if event_room and event_room != self.room:
                    return
                new_stage = data.get("stage") or data.get("plantStage") or data.get("value")
            else:
                _LOGGER.error(f"[{self.room}] Unknown PlantStageChange data format: {type(data)}")
                return
            
            if not new_stage:
                _LOGGER.debug(f"[{self.room}] PlantStageChange with no stage value: {data}")
                return
            
            # Update all mediums with new stage
            for medium in self.media:
                await medium.set_plant_stage(new_stage)
            
            # Save and emit updates
            self._save_mediums_to_store()
            await self.emit_all_plants_update()
            
            _LOGGER.info(f"[{self.room}] Updated {len(self.media)} mediums to plantStage={new_stage}")
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling PlantStageChange: {e}", exc_info=True)

    async def _process_sensor_registration(self, data: Dict[str, Any]):
        """Actually process a sensor registration (called after init or from queue)."""
        entity_id = data.get("entity_id")
        sensor_type = data.get("sensor_type")
        medium_label = data.get("medium_label")
        room = data.get("room")
        value = data.get("value")
        unit = data.get("unit")
        context = data.get("context")

        if not entity_id or not sensor_type or not medium_label:
            _LOGGER.error(
                "RegisterSensorToMedium: entity_id, sensor_type and medium_label required"
            )
            return

        # Extract medium number from label (e.g. "medium_1", "medium1", "Medium-1" -> 1)
        # Handles: medium_1, medium-1, medium1, Medium_1, MEDIUM_1, etc.
        try:
            import re
            # Normalize to lowercase and extract the number
            label_lower = medium_label.lower()
            match = re.search(r'medium[_\-]?(\d+)', label_lower)
            if match:
                medium_number = int(match.group(1))
            else:
                # Default to medium 1 if no number found (e.g., just "medium")
                medium_number = 1
            medium_index = medium_number - 1  # Array is 0-based
            _LOGGER.debug(f"[{self.room}] Extracted medium_index={medium_index} from label '{medium_label}'")
        except (ValueError, IndexError) as e:
            _LOGGER.error(
                f"Could not extract medium number from label: {medium_label} - {e}"
            )
            return

        # Check if medium exists
        if 0 <= medium_index < len(self.media):
            medium = self.media[medium_index]

            sensor_data = {
                "entity_id": entity_id,
                "sensor_type": sensor_type,
                "value": value,
                "unit": unit,
                "context": context,
                "room": room,
                "medium_label": medium_label,
            }

            await medium.register_sensor(sensor_data)
            self._entity_to_medium_index[entity_id] = medium_index

            _LOGGER.debug(
                f"[{self.room}] ✅ SENSOR REGISTERED: {entity_id} ({sensor_type}/{context}) -> Medium {medium.name} (Index {medium_index})"
            )

            self._save_mediums_to_store()
        else:
            _LOGGER.error(
                f"[{self.room}] ❌ SENSOR REGISTRATION FAILED: Medium index {medium_index} does not exist. "
                f"Available media: {len(self.media)}. Sensor: {entity_id}, Label: {medium_label}"
            )

    async def _on_unregister_sensor(self, data):
        """Removes a sensor from a medium."""
        try:
            if data.get("room") != self.room:
                return

            entity_id = data.get("entity_id")

            if entity_id in self._entity_to_medium_index:
                medium_index = self._entity_to_medium_index[entity_id]
                medium = self.media[medium_index]

                if medium.unregister_sensor(entity_id):
                    del self._entity_to_medium_index[entity_id]
                    _LOGGER.info(
                        f"Sensor {entity_id} removed from Medium {medium.name}"
                    )
                    self._save_mediums_to_store()

        except Exception as e:
            _LOGGER.error(f"Error removing sensor: {e}", exc_info=True)

    async def _on_medium_sensor_update(self, data):
        """
        Processes sensor updates for media with debounced saving.
        Updates are processed in RAM immediately, but saving is batched.
        
        Data comes from Sensor.py handleSensorUpdate as sensor_config dict:
        {
            "entity_id": "sensor.xxx",
            "sensor_type": "moisture",
            "context": "soil",
            "state": 45.2,
            "last_reading": 45.2,
            ...
        }
        """
        try:
            # Handle both dict and object data
            if hasattr(data, '__dict__'):
                data = vars(data)
            elif not isinstance(data, dict):
                _LOGGER.error(f"[{self.room}] Invalid data type in medium sensor update: {type(data)}")
                return
            
            _LOGGER.debug(f"[{self.room}] 📊 MediumSensorUpdate RECEIVED: entity={data.get('entity_id')}, type={data.get('sensor_type')}, value={data.get('state') or data.get('last_reading')}")

            # Validate data
            if not data:
                _LOGGER.debug(f"[{self.room}] Empty data in medium sensor update")
                return

            entity_id = data.get("entity_id")
            if not entity_id:
                _LOGGER.debug(f"[{self.room}] No entity_id in medium sensor update")
                return

            # Check if sensor is registered
            if entity_id not in self._entity_to_medium_index:
                _LOGGER.debug(f"[{self.room}] ⚠️ Sensor {entity_id} is NOT registered to any medium. Registered sensors: {list(self._entity_to_medium_index.keys())}")
                return

            medium_index = self._entity_to_medium_index[entity_id]

            # Check if medium still exists
            if medium_index >= len(self.media):
                _LOGGER.error(f"[{self.room}] Medium Index {medium_index} no longer exists")
                del self._entity_to_medium_index[entity_id]
                return

            medium = self.media[medium_index]

            # 1. Update medium in RAM - returns True only if value actually changed
            value_changed = await medium.update_sensor_reading_async(data)
            
            if not value_changed:
                # No actual change, skip save and emit
                return

            # 2. Debounced save - batches multiple updates together
            await self._schedule_save()
            
            # 3. Emit medium values update for UI - only on actual changes
            medium_values = medium.get_all_medium_values()
            _LOGGER.info(
                f"[{self.room}] 📊 Medium {medium.name} changed: {data.get('sensor_type')}="
                f"{data.get('state') or data.get('last_reading')} -> Emitting LogForClient"
            )
            await self.event_manager.emit("LogForClient", medium_values, haEvent=True)

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in medium sensor update: {e}", exc_info=True)

    async def _schedule_save(self):
        """Saves after delay, resets timer on new updates"""
        # Cancel pending save
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()

        # Schedule new save
        self._save_task = asyncio.create_task(self._delayed_save())

    async def _delayed_save(self):
        """Waits and then saves"""
        try:
            await asyncio.sleep(self._save_delay_seconds)
            self._save_mediums_to_store()
            _LOGGER.debug(f"Mediums saved after update batch")
        except asyncio.CancelledError:
            pass  # Normal if new updates come

    async def _load_mediums_from_store(self):
        """Load existing mediums from dataStore on startup"""
        stored_mediums = self.data_store.get("growMediums")
        
        _LOGGER.debug(f"[{self.room}] LOADING mediums from store: {len(stored_mediums) if stored_mediums else 0} found")

        if stored_mediums and len(stored_mediums) > 0:
            self.media = []

            for medium_dict in stored_mediums:
                try:
                    # Log what we're restoring
                    _LOGGER.debug(
                        f"[{self.room}] RESTORING medium: name={medium_dict.get('name')}, "
                        f"plant_name={medium_dict.get('plant_name')}, "
                        f"breeder_name={medium_dict.get('breeder_name') or medium_dict.get('plant_strain')}, "
                        f"breeder_bloom_days={medium_dict.get('breeder_bloom_days')}"
                    )
                    
                    # CRITICAL FIX: Pass eventManager, dataStore, and room to from_dict
                    # Without these, medium.event_manager and medium.data_store are None
                    medium = GrowMedium.from_dict(
                        medium_dict,
                        eventManager=self.event_manager,
                        dataStore=self.data_store,
                        room=self.room
                    )
                    self.media.append(medium)
                    
                    # Verify restoration worked
                    _LOGGER.debug(
                        f"[{self.room}] RESTORED medium object: name={medium.name}, "
                        f"plant_name={medium.plant_name}, breeder_name={medium.breeder_name}, "
                        f"breeder_bloom_days={medium.breeder_bloom_days}"
                    )

                    # Rebuild entity_to_medium_index mapping
                    medium_index = len(self.media) - 1
                    for sensor_type, entity_ids in medium.registered_sensors.items():
                        for entity_id in entity_ids:
                            self._entity_to_medium_index[entity_id] = medium_index
                            _LOGGER.debug(f"[{self.room}] RESTORED sensor mapping: {entity_id} -> medium index {medium_index}")

                except Exception as e:
                    _LOGGER.error(
                        f"Failed to load medium from dict: {medium_dict}, Error: {e}", exc_info=True
                    )

            if self.media:
                self.current_medium_type = self.media[0].medium_type
                # Only sync names on load if there's an actual mismatch (migration case)
                needs_sync = any(
                    m.name != f"{m.medium_type.value}_{i+1}" 
                    for i, m in enumerate(self.media)
                )
                if needs_sync:
                    _LOGGER.debug(f"[{self.room}] Syncing medium names after datastore load")
                    self._sync_medium_names()
                    self._save_mediums_to_store()

            _LOGGER.debug(
                f"[{self.room}] Loaded {len(self.media)} mediums from dataStore. "
                f"Registered sensors: {len(self._entity_to_medium_index)}"
            )
        else:
            _LOGGER.debug(
                f"[{self.room}] No existing mediums found - creating default medium"
            )
            await self._create_default_medium()

    async def _create_default_medium(self):
        """Create a default medium"""
        default_type = MediumType.SOIL
        self.current_medium_type = default_type
        await self._create_mediums(default_type, 1)
        _LOGGER.info(
            f"Created default medium: {default_type.value}_1 for room {self.room}"
        )

    async def _on_new_medium_change(self, data):
        """Called when a new medium setup event is triggered."""
        _LOGGER.debug(f"[{self.room}] MediumChange EVENT RECEIVED: {data}")
        if not data:
            _LOGGER.debug(f"{self.room}: Received empty medium change data")
            return

        # Handle both old string format and new dict format
        if isinstance(data, dict):
            # New format: {"room": "room_name", "medium_type": "SOILx2"}
            event_room = data.get("room")
            if event_room and event_room != self.room:
                _LOGGER.debug(f"[{self.room}] IGNORING MediumChange for room '{event_room}' (not my room)")
                return
            input_str = data.get("medium_type", "")
            _LOGGER.debug(f"[{self.room}] Processing MediumChange: {input_str}")
        elif isinstance(data, str):
            # Legacy format: just the medium type string - DANGEROUS without room check
            # We can't filter by room, so we have to process it (backwards compatibility)
            _LOGGER.debug(f"[{self.room}] ⚠️ Legacy MediumChange string format (no room filter): {data}")
            input_str = data
        else:
            _LOGGER.error(f"{self.room}: Invalid medium change data type: {type(data)}")
            return

        if not input_str or not input_str.strip():
            _LOGGER.debug(f"{self.room}: Empty medium_type in data: {data}")
            return

        try:
            base, count = self._parse_medium_input(input_str)
        except ValueError as e:
            _LOGGER.error(f"{self.room}: Invalid medium input '{input_str}' -> {e}")
            return

        if count < 1:
            _LOGGER.debug(f"{self.room}: Cannot have less than 1 medium, setting count to 1")
            count = 1

        _LOGGER.info(f"{self.room}: Processing medium change: {base.value} x {count}")
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
        
        _LOGGER.debug(
            f"[{self.room}] _sync_mediums called: current_type={self.current_medium_type}, "
            f"new_type={new_type}, current_count={current_count}, desired_count={desired_count}"
        )

        # Check if type actually changed (compare by value to handle enum comparison issues)
        type_changed = False
        
        # CRITICAL FIX: If we already have mediums loaded, check their actual type
        # This handles the case where current_medium_type is None but mediums exist from datastore
        if current_count > 0 and self.media[0].medium_type == new_type:
            # Mediums already exist with correct type - just update current_medium_type
            if self.current_medium_type is None:
                self.current_medium_type = new_type
                _LOGGER.debug(f"[{self.room}] Existing mediums match new_type, setting current_medium_type={new_type}")
            type_changed = False
        elif self.current_medium_type is None and current_count == 0:
            # No mediums exist, need to create
            type_changed = True
        elif self.current_medium_type != new_type:
            # Double-check by comparing values too (enum comparison can be tricky)
            if self.current_medium_type and self.current_medium_type.value != new_type.value:
                type_changed = True
            else:
                _LOGGER.debug(f"[{self.room}] Type appears same by value, skipping recreate")

        if type_changed:
            _LOGGER.debug(
                f"[{self.room}] Medium TYPE CHANGED: {self.current_medium_type} -> {new_type} - RECREATING MEDIUMS"
            )

            # Clean up all sensor mappings when type changes
            old_sensor_count = len(self._entity_to_medium_index)
            for medium in self.media:
                # Clear sensor registrations from each medium
                if hasattr(medium, "registered_sensors"):
                    medium.registered_sensors.clear()
                if hasattr(medium, "sensor_type_map"):
                    medium.sensor_type_map.clear()
                if hasattr(medium, "sensor_readings"):
                    medium.sensor_readings.clear()

            self.media.clear()
            self._entity_to_medium_index.clear()
            self.current_medium_type = new_type

            _LOGGER.debug(
                f"[{self.room}]: Medium type changed - cleared {old_sensor_count} sensor mappings"
            )

            await self._create_mediums(new_type, desired_count)
        else:
            if desired_count > current_count:
                diff = desired_count - current_count
                _LOGGER.debug(
                    f"{self.room} Adding {diff} new mediums of type {new_type.value}"
                )
                await self._create_mediums(new_type, diff, start_index=current_count)
            elif desired_count < current_count:
                diff = current_count - desired_count
                _LOGGER.debug(f"{self.room} Removing {diff} newest mediums")

                # Cleanup sensor mappings for deleted media
                for i in range(desired_count, current_count):
                    medium = self.media[i]
                    for sensor_type, entity_ids in medium.registered_sensors.items():
                        for entity_id in entity_ids:
                            if entity_id in self._entity_to_medium_index:
                                del self._entity_to_medium_index[entity_id]

                self.media = self.media[:desired_count]
                self._sync_medium_names()
                self._save_mediums_to_store()
        
        # Emit plants update after any medium changes
        await self.emit_all_plants_update()

    def _sync_medium_names(self):
        """
        Ensure internal medium names match their array index (1-based).
        ONLY touches the internal 'name' field - NEVER touches user-editable fields:
        - display_name (user's custom name)
        - plant_name (user's plant name)
        - breeder_name (user's breeder/strain)
        - dates (grow_start_date, bloom_switch_date)
        - breeder_bloom_days (user's harvest estimate)
        """
        for i, medium in enumerate(self.media):
            expected_name = f"{medium.medium_type.value}_{i + 1}"
            if medium.name != expected_name:
                _LOGGER.debug(f"Syncing internal name from {medium.name} to {expected_name}")
                medium.name = expected_name  # Only update internal ID, not display_name!

    async def _create_mediums(
        self, medium_type: MediumType, count: int, start_index: int = 0
    ):
        """Create new mediums and store them."""
        # Get global plantStage from dataStore for new mediums
        global_plant_stage = self.data_store.get("plantStage")
        
        for i in range(count):
            index = start_index + i + 1
            name = f"{medium_type.value}_{index}"
            medium = GrowMedium(
                self.event_manager,
                self.data_store,
                self.room,
                medium_type=medium_type,
                name=name,
                plant_stage=global_plant_stage,  # Use global plantStage
            )
            self.media.append(medium)
            _LOGGER.info(f"Created medium {name} with plantStage={global_plant_stage}")

        # Names are already correct from creation, no need to sync
        self._save_mediums_to_store()

    def _save_mediums_to_store(self):
        """
        Saves mediums to datastore with change detection to prevent spam.
        Only saves if data actually changed.
        """
        mediums_as_dicts = [medium.to_dict() for medium in self.media]
        
        # Log what we're about to save
        for md in mediums_as_dicts:
            _LOGGER.debug(
                f"[{self.room}] SAVING medium: name={md.get('name')}, "
                f"plant_name={md.get('plant_name')}, breeder_name={md.get('breeder_name')}, "
                f"breeder_bloom_days={md.get('breeder_bloom_days')}"
            )
        
        # Create hash of current data for change detection
        try:
            import json
            current_hash = hash(json.dumps(mediums_as_dicts, sort_keys=True, default=str))
            
            # Skip save if nothing changed
            if current_hash == self._last_save_hash:
                _LOGGER.debug(f"[{self.room}] Skipping save - no changes detected (hash match)")
                return
            
            self._last_save_hash = current_hash
        except Exception as e:
            # If hashing fails, just save anyway
            _LOGGER.debug(f"[{self.room}] Could not hash data for change detection: {e}")
        
        self._save_count += 1
        self.data_store.set("growMediums", mediums_as_dicts)
        _LOGGER.debug(
            f"[{self.room}] ✅ DATASTORE SAVED (save #{self._save_count}) - {len(mediums_as_dicts)} mediums"
        )
        
        # CRITICAL: Also persist to disk via SaveState event
        self._create_tracked_task(self.event_manager.emit("SaveState", {"source": "MediumManager"}))

    async def _start_daily_update_timer(self):
        """Schedule daily update for media status"""

        async def daily_update():
            while True:
                try:
                    await asyncio.sleep(24 * 3600)
                    _LOGGER.info(
                        f"Daily medium check for {self.room}: {len(self.media)} active mediums"
                    )

                    # Log status
                    for medium in self.media:
                        status = medium.get_status()
                        _LOGGER.info(f"Medium {medium.name}: {status}")
                except asyncio.CancelledError:
                    _LOGGER.debug(f"Daily update timer cancelled for {self.room}")
                    break
                except Exception as e:
                    _LOGGER.error(f"Error in daily update: {e}")

        self._daily_update_task = self._create_tracked_task(daily_update())

    async def async_shutdown(self):
        """Shutdown manager and cleanup resources."""
        try:
            _LOGGER.info(f"Shutting down Medium Manager for {self.room}")

            # Cancel save task
            if self._save_task and not self._save_task.done():
                self._save_task.cancel()

            # Cancel daily update task
            if self._daily_update_task and not self._daily_update_task.done():
                self._daily_update_task.cancel()

            # Cancel all background tasks
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()

            # Final save
            self._save_mediums_to_store()

            _LOGGER.info(f"Medium Manager shutdown complete for {self.room}")

        except Exception as e:
            _LOGGER.error(f"Error during shutdown: {e}")

    # ============================================================
    # PUBLIC API METHODS
    # ============================================================

    def get_mediums(self) -> List[GrowMedium]:
        """Return all active mediums"""
        return self.media

    def get_medium_by_index(self, index: int) -> Optional[GrowMedium]:
        """Returns a medium by index."""
        if 0 <= index < len(self.media):
            return self.media[index]
        return None

    def get_medium_by_name(self, name: str) -> Optional[GrowMedium]:
        """Returns a medium by name."""
        for medium in self.media:
            if medium.name.lower() == name.lower():
                return medium
        return None

    def get_all_medium_readings(self) -> List[Dict[str, Any]]:
        """Returns all medium readings."""
        return [medium.get_all_medium_values() for medium in self.media]

    def get_status(self) -> Dict[str, Any]:
        """Return status summary for all mediums"""
        return {
            "room": self.room,
            "total_media": len(self.media),
            "registered_sensors": len(self._entity_to_medium_index),
            "media": [m.get_status() for m in self.media],
        }

    def get_all_plants_data(self) -> List[Dict[str, Any]]:
        """
        Get plant data from all mediums as an array.
        This is what gets emitted to the UI for per-medium plant tracking.
        
        Returns:
            List of dictionaries, each containing full plant data including:
            - medium_index: Index in the mediums array (for frontend updates)
            - medium_name: Name of the medium (e.g., "SOIL_1", "coco_2")
            - plant_name, breeder_name, dates, targets, etc.
        """
        plants_data = []
        for index, medium in enumerate(self.media):
            plant_info = medium.get_plant_info()
            plant_info["medium_index"] = index
            plant_info["medium_name"] = medium.name
            plants_data.append(plant_info)
        return plants_data

    async def emit_all_plants_update(self) -> None:
        """
        Emit plant data for all mediums as an array.
        Call this when UI needs to refresh all plant data.
        
        This fires the MediumPlantsUpdate event which the frontend 
        subscribes to via MediumContext.
        """
        plants_data = self.get_all_plants_data()
        
        publication = OGBMediumPlantPublication(
            Name=self.room,
            plants=plants_data,
        )
        
        await self.event_manager.emit("MediumPlantsUpdate", publication, haEvent=True)
        # Note: Not emitting to LogForClient - this is only for MediumContext, not GrowLogs
        
        _LOGGER.info(f"{self.room}: Emitted MediumPlantsUpdate for {len(plants_data)} mediums")

    async def update_medium_plant_dates(
        self, 
        medium_index: int, 
        grow_start: Optional[str] = None,
        bloom_switch: Optional[str] = None,
        breeder_bloom_days: Optional[int] = None,
        plant_stage: Optional[str] = None,
        plant_name: Optional[str] = None,
        breeder_name: Optional[str] = None,
        plant_type: Optional[str] = None,
    ) -> bool:
        """
        Update plant dates for a specific medium.
        Returns True if update was successful.
        """
        _LOGGER.debug(f"[{self.room}] 🌱 update_medium_plant_dates called: "
                       f"index={medium_index}, name={plant_name}, breeder={breeder_name}, "
                       f"stage={plant_stage}, type={plant_type}")
        
        if medium_index < 0 or medium_index >= len(self.media):
            _LOGGER.error(f"[{self.room}] Invalid medium index: {medium_index}, available: {len(self.media)}")
            return False
            
        medium = self.media[medium_index]
        _LOGGER.debug(f"[{self.room}] Found medium: {medium.name}, current breeder={medium.breeder_name}")
        
        # Update plant name/breeder/type if provided
        if plant_name is not None:
            _LOGGER.debug(f"[{self.room}] Setting plant_name: {medium.plant_name} -> {plant_name}")
            medium.plant_name = plant_name
        if breeder_name is not None:
            _LOGGER.debug(f"[{self.room}] Setting breeder_name: {medium.breeder_name} -> {breeder_name}")
            medium.breeder_name = breeder_name
        if plant_type is not None:
            _LOGGER.debug(f"[{self.room}] Setting plant_type: {medium.plant_type} -> {plant_type}")
            medium.plant_type = plant_type
            
        # Update breeder bloom days if provided
        if breeder_bloom_days is not None:
            _LOGGER.debug(f"[{self.room}] Setting breeder_bloom_days: {medium.breeder_bloom_days} -> {breeder_bloom_days}")
            medium.breeder_bloom_days = breeder_bloom_days
        
        # Update grow start date
        if grow_start is not None:
            try:
                date = datetime.strptime(grow_start, "%Y-%m-%d")
                await medium.set_grow_start(date)
                _LOGGER.debug(f"[{self.room}] Set grow_start: {grow_start}")
            except ValueError as e:
                _LOGGER.error(f"Invalid grow_start date format: {grow_start} - {e}")
                
        # Update bloom switch date
        if bloom_switch is not None:
            try:
                date = datetime.strptime(bloom_switch, "%Y-%m-%d")
                await medium.set_bloom_switch(date)
                _LOGGER.debug(f"[{self.room}] Set bloom_switch: {bloom_switch}")
            except ValueError as e:
                _LOGGER.error(f"Invalid bloom_switch date format: {bloom_switch} - {e}")
                
        # Update plant stage
        if plant_stage is not None:
            await medium.set_plant_stage(plant_stage)
            _LOGGER.debug(f"[{self.room}] Set plant_stage: {plant_stage}")
        
        # Save changes
        self._save_mediums_to_store()
        _LOGGER.debug(f"[{self.room}] Medium saved. Current: name={medium.plant_name}, breeder={medium.breeder_name}")
        
        # Emit full plants update
        await self.emit_all_plants_update()
        
        _LOGGER.debug(f"[{self.room}] ✅ Plant dates updated for {medium.name}: name={medium.plant_name}, breeder={medium.breeder_name}")
        return True

    async def finish_medium_grow(
        self,
        medium_index: int,
        plant_name: Optional[str] = None,
        breeder_name: Optional[str] = None,
        total_days: Optional[int] = None,
        bloom_days: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """
        Complete the grow cycle for a specific medium.
        
        This method:
        1. Archives the current grow data (emits GrowCompleted event)
        2. Resets the medium for a new grow
        3. Emits updates to UI
        
        Returns True if successful, False otherwise.
        """
        _LOGGER.debug(f"[{self.room}] 🏁 finish_medium_grow called: "
                       f"index={medium_index}, plant={plant_name}, breeder={breeder_name}, "
                       f"total_days={total_days}, bloom_days={bloom_days}")
        
        if medium_index < 0 or medium_index >= len(self.media):
            _LOGGER.error(f"[{self.room}] Invalid medium index: {medium_index}, available: {len(self.media)}")
            return False
        
        medium = self.media[medium_index]
        
        # Gather current grow data for archiving
        harvest_data = {
            "room": self.room,
            "medium_index": medium_index,
            "medium_name": medium.name,
            "medium_type": medium.medium_type.value if medium.medium_type else None,
            # Plant info
            "plant_name": plant_name or medium.plant_name,
            "breeder_name": breeder_name or medium.breeder_name,
            "plant_type": medium.plant_type,
            "plant_stage": medium.plant_stage,
            # Dates
            "grow_start_date": medium.grow_start_date.isoformat() if medium.grow_start_date else None,
            "bloom_switch_date": medium.bloom_switch_date.isoformat() if medium.bloom_switch_date else None,
            "harvest_date": datetime.now().isoformat(),
            # Duration
            "total_days": total_days or medium.total_grow_days,
            "bloom_days": bloom_days or medium.bloom_days,
            "breeder_bloom_days": medium.breeder_bloom_days,
            # Final sensor readings
            "final_readings": medium.get_all_medium_values(),
            # Optional notes
            "notes": notes,
            # Timestamp
            "completed_at": datetime.now().isoformat(),
        }
        
        _LOGGER.debug(f"[{self.room}] 📦 Archiving grow data: {harvest_data}")
        
        # Emit GrowCompleted event for archiving/logging
        # This can be used by premium features to store harvest history
        try:
            await self.event_manager.emit("GrowCompleted", harvest_data, haEvent=True)
            _LOGGER.debug(f"[{self.room}] ✅ Emitted GrowCompleted event")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to emit GrowCompleted: {e}")
        
        # Reset the medium for a new grow
        _LOGGER.debug(f"[{self.room}] 🔄 Resetting medium {medium.name} for new grow")
        
        # Clear plant-specific data but keep medium type and name
        medium.plant_name = None
        medium.breeder_name = None
        medium.plant_type = None
        medium.grow_start_date = None
        medium.bloom_switch_date = None
        medium.breeder_bloom_days = None
        
        # Reset plant stage to initial state
        # Check if there's a global plantStage to use, otherwise default to Seedling
        global_plant_stage = self.data_store.get("plantStage")
        if global_plant_stage:
            await medium.set_plant_stage(global_plant_stage)
        else:
            await medium.set_plant_stage("Seedling")
        
        # Keep sensor registrations intact - just clear readings for a fresh start
        # This allows sensors to continue working for the next grow
        if hasattr(medium, 'sensor_readings'):
            medium.sensor_readings.clear()
        
        # Save changes
        self._save_mediums_to_store()
        
        # Emit plants update for UI refresh
        await self.emit_all_plants_update()
        
        # Also emit a specific event for UI notification
        notification_data = {
            "room": self.room,
            "medium_index": medium_index,
            "medium_name": medium.name,
            "message": f"Grow cycle completed for {harvest_data['plant_name'] or medium.name}!",
            "total_days": harvest_data["total_days"],
            "bloom_days": harvest_data["bloom_days"],
        }
        
        try:
            await self.event_manager.emit("GrowFinishNotification", notification_data, haEvent=True)
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to emit GrowFinishNotification: {e}")
        
        _LOGGER.debug(f"[{self.room}] ✅ Medium {medium.name} reset and ready for new grow")
        return True

    # ============================================================
    # LEGACY COMPATIBILITY METHODS
    # ============================================================

    async def register_sensor_to_medium(self, sensor_id: str, medium_id: str):
        """Legacy method for registering a sensor to a specific medium."""
        try:
            # Parse medium_id to get index
            try:
                medium_index = int(medium_id.split("_")[-1]) - 1
            except (ValueError, IndexError):
                _LOGGER.error(f"Could not parse medium_id: {medium_id}")
                return

            if 0 <= medium_index < len(self.media):
                if sensor_id not in self._entity_to_medium_index:
                    self._entity_to_medium_index[sensor_id] = medium_index
                    _LOGGER.debug(f"[{self.room}] Registered sensor {sensor_id} to medium {medium_id}")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error registering sensor to medium: {e}")

    async def update_medium_properties(self, medium_id: str, properties: Dict):
        """Legacy method for updating properties for a specific medium."""
        try:
            medium = self.get_medium_by_name(medium_id)
            if medium:
                for key, value in properties.items():
                    if hasattr(medium, key):
                        setattr(medium, key, value)
                self._save_mediums_to_store()
                _LOGGER.debug(f"[{self.room}] Updated properties for medium {medium_id}: {properties}")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error updating medium properties: {e}")

    def get_medium_sensors(self, medium_id: str) -> list:
        """Get all sensors registered to a specific medium."""
        medium = self.get_medium_by_name(medium_id)
        if medium:
            sensors = []
            for sensor_type, entity_ids in medium.registered_sensors.items():
                sensors.extend(entity_ids)
            return sensors
        return []

    def get_medium_properties(self, medium_id: str) -> Dict:
        """Get properties for a specific medium."""
        medium = self.get_medium_by_name(medium_id)
        if medium:
            return medium.get_status()
        return {}

    def get_all_mediums(self) -> Dict:
        """Get all registered mediums with their properties."""
        return {m.name: m.get_status() for m in self.media}

    async def initialize_mediums_from_config(self):
        """Initialize mediums from configuration data."""
        try:
            # Load medium configuration from dataStore
            medium_config = self.data_store.getDeep("Mediums")
            if medium_config:
                _LOGGER.info(f"[{self.room}] Found medium config, processing...")
                # Process legacy config format if present
                for medium_id, properties in medium_config.items():
                    await self.update_medium_properties(medium_id, properties)

            _LOGGER.info(f"[{self.room}] Initialized {len(self.media)} mediums from config")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error initializing mediums from config: {e}")

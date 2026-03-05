import asyncio
import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)

# Script storage constants
SCRIPT_MAX_SIZE_KB = 100
SCRIPT_BACKUP_SUFFIX = "_backup"
SCRIPT_DIR = "scripts"


def _is_corrupted_tuple_string(value: Any) -> bool:
    """Detect if a value is a corrupted tuple string.
    
    Corrupted tuples look like: "('(', \"'\", '(', \"'\", ',', ..."
    These are created when:
    1. A tuple (5.5, 6.5) is converted to string "(5.5, 6.5)"
    2. On load, tuple("(5.5, 6.5)") creates ('(', '5', '.', '5', ...)
    3. On next save, this becomes the massive string above
    
    Detection: If it's a string starting with "('" and contains many commas, it's corrupted.
    """
    if not isinstance(value, str):
        return False
    # Corrupted tuple strings start with "('" or "('("
    if value.startswith("('") and len(value) > 100:
        return True
    return False


def _clean_corrupted_data(data: Dict[str, Any], room: str) -> Dict[str, Any]:
    """Clean corrupted data in loaded state.
    
    Specifically handles:
    - Corrupted tuple strings in growMediums (ph_range, ec_range)
    - Overly large string values that indicate corruption
    
    Returns cleaned data dict.
    """
    if not isinstance(data, dict):
        return data
    
    # Clean growMediums
    if "growMediums" in data and isinstance(data["growMediums"], list):
        cleaned_mediums = []
        for medium in data["growMediums"]:
            if not isinstance(medium, dict):
                continue
            
            # Check for corrupted properties
            props = medium.get("properties", {})
            if isinstance(props, dict):
                # Fix corrupted ph_range
                if _is_corrupted_tuple_string(props.get("ph_range")):
                    _LOGGER.warning(f"[{room}] Fixing corrupted ph_range in medium '{medium.get('name')}'")
                    props["ph_range"] = [5.5, 7.0]  # Default value
                
                # Fix corrupted ec_range
                if _is_corrupted_tuple_string(props.get("ec_range")):
                    _LOGGER.warning(f"[{room}] Fixing corrupted ec_range in medium '{medium.get('name')}'")
                    props["ec_range"] = [1.0, 2.5]  # Default value
                
                # Convert any remaining tuple-like strings to proper lists
                for key in ["ph_range", "ec_range"]:
                    val = props.get(key)
                    if isinstance(val, str) and val.startswith("(") and val.endswith(")"):
                        try:
                            # Try to parse "(5.5, 6.5)" format
                            parsed = eval(val)  # Safe here since we validated format
                            if isinstance(parsed, tuple) and len(parsed) == 2:
                                props[key] = list(parsed)
                                _LOGGER.info(f"[{room}] Converted {key} from string to list: {props[key]}")
                        except:
                            _LOGGER.warning(f"[{room}] Could not parse {key}, using default")
                            props[key] = [5.5, 7.0] if key == "ph_range" else [1.0, 2.5]
                
                medium["properties"] = props
            
            cleaned_mediums.append(medium)
        
        data["growMediums"] = cleaned_mediums
        _LOGGER.info(f"[{room}] Cleaned {len(cleaned_mediums)} mediums in loaded state")
    
    return data


class OGBDSManager:
    def __init__(self, hass, dataStore, eventManager, room, regListener):
        self.name = "OGB DataStore Manager"
        self.hass = hass
        self.room = room
        self.regListener = regListener
        self.data_store = dataStore
        self.event_manager = eventManager
        self.is_initialized = False
        self._state_loaded = False

        self.storage_filename = f"ogb_{self.room.lower()}_state.json"
        self.storage_path = self._get_secure_path(self.storage_filename)

        # Events
        self.event_manager.on("SaveState", self.saveState)
        self.event_manager.on("LoadState", self.loadState)
        self.event_manager.on("RestoreState", self.loadState)
        self.event_manager.on("DeleteState", self.deleteState)

        # DON'T load state synchronously in __init__ - this blocks HA's event loop!
        # State will be loaded asynchronously via async_init() or loadState()
        self.is_initialized = True
        _LOGGER.info(f"[{self.room}] OGBDSManager initialized (state will be loaded async)")

    async def async_init(self):
        """Asynchronously initialize and load state from disk into datastore.
        
        This method should be called after __init__ to load state without blocking.
        Uses hass.async_add_executor_job to run file I/O in a thread pool.
        """
        if self._state_loaded:
            _LOGGER.debug(f"[{self.room}] State already loaded, skipping async_init")
            return
        
        await self._load_state_async()
        self._state_loaded = True
    
    async def _load_state_async(self):
        """Asynchronously load state from disk into datastore at startup.
        
        Uses hass.async_add_executor_job to avoid blocking the event loop.
        """
        if not os.path.exists(self.storage_path):
            _LOGGER.warning(f"[{self.room}] No saved state file at {self.storage_path} - starting fresh")
            return
        
        try:
            # Run file I/O in executor to avoid blocking event loop
            data = await self.hass.async_add_executor_job(self._sync_load_state)
            
            if data is None:
                return
            
            _LOGGER.warning(f"[{self.room}] 📥 LOADING state from {self.storage_path}")
            
            # CRITICAL: Clean corrupted data before loading into datastore
            # This fixes issues like corrupted tuple strings in growMediums
            data = _clean_corrupted_data(data, self.room)
            
            # Load growMediums first if present - this is critical for MediumManager
            if "growMediums" in data:
                mediums = data["growMediums"]
                _LOGGER.warning(f"[{self.room}] Found {len(mediums)} mediums in saved state")
                for m in mediums:
                    _LOGGER.warning(
                        f"[{self.room}]   - {m.get('name')}: plant_name={m.get('plant_name')}, "
                        f"breeder_name={m.get('breeder_name') or m.get('plant_strain')}, breeder_bloom_days={m.get('breeder_bloom_days')}"
                    )
            
            # Load plantsView (timelapse config) if present - critical for Camera
            if "plantsView" in data:
                plants_view = data["plantsView"]
                _LOGGER.warning(f"[{self.room}] Found plantsView in saved state: {plants_view}")
            
            # Load all data into datastore
            for key, value in data.items():
                self.data_store.set(key, value)
            
            _LOGGER.warning(f"[{self.room}] ✅ State loaded ASYNCHRONOUSLY into datastore ({len(data)} keys)")
            
        except json.JSONDecodeError as e:
            _LOGGER.error(f"[{self.room}] Failed to parse state file: {e}")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to load state: {e}", exc_info=True)
    
    def _sync_load_state(self):
        """Synchronous file read - called via executor job."""
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error reading state file: {e}")
            return None

    def _get_secure_path(self, filename: str) -> str:
        """Gibt einen sicheren Pfad unterhalb von /config/ogb_data zurück."""
        subdir = self.hass.config.path("ogb_data")
        os.makedirs(subdir, exist_ok=True)
        return os.path.join(subdir, filename)

    async def saveState(self, data):
        """Speichert den vollständigen aktuellen State."""
        _LOGGER.warning(f"[{self.room}] RECEIVED SaveState event: {data}")
        try:
            state = self.data_store.getFullState()
            
            # CRITICAL: Detect and fix corrupted data before saving
            # This prevents saving corrupted tuple strings that cause file growth
            state = self._sanitize_state_for_save(state)
            
            # Log key sizes for debugging unbounded growth
            if _LOGGER.isEnabledFor(logging.DEBUG):
                for key, value in state.items():
                    try:
                        key_size = len(json.dumps(value, default=str))
                        if key_size > 5000:  # Log keys larger than 5KB
                            _LOGGER.debug(f"[{self.room}] State key '{key}' size: {key_size} bytes")
                    except:
                        pass

            # Teste JSON-Serialisierung vor dem Speichern
            try:
                json_string = json.dumps(state, indent=2, default=str)
                json_size_kb = len(json_string) / 1024
                
                # CRITICAL: Refuse to save if file is too large (indicates corruption)
                if json_size_kb > 100:
                    _LOGGER.error(f"[{self.room}] ❌ State file too large ({json_size_kb:.1f}KB) - likely corrupted, NOT saving!")
                    _LOGGER.error(f"[{self.room}] Delete {self.storage_path} and restart to fix")
                    # Find the largest keys for debugging
                    for key, value in state.items():
                        try:
                            key_size = len(json.dumps(value, default=str)) / 1024
                            if key_size > 10:
                                _LOGGER.error(f"[{self.room}]   Large key: '{key}' = {key_size:.1f}KB")
                        except:
                            pass
                    return  # Don't save corrupted state!
                elif json_size_kb > 50:
                    _LOGGER.warning(f"[{self.room}] ⚠️ State file size: {json_size_kb:.1f}KB - consider cleanup")
                else:
                    _LOGGER.debug(f"[{self.room}] State file size: {json_size_kb:.1f}KB")
                    
            except Exception as json_error:
                _LOGGER.error(f"❌ JSON serialization failed: {json_error}")
                simplified_state = self._create_simplified_state(state)
                json_string = json.dumps(simplified_state, indent=2, default=str)
                _LOGGER.warning(f"⚠️ Saving simplified state instead")

            await asyncio.to_thread(self._sync_save, json_string)
            _LOGGER.warning(f"[{self.room}] ✅ DataStore saved to {self.storage_path}")

        except Exception as e:
            _LOGGER.error(f"❌ Failed to save DataStore: {e}")
            import traceback

            _LOGGER.error(f"❌ Full traceback: {traceback.format_exc()}")
    
    def _sanitize_state_for_save(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize state before saving to prevent corruption.
        
        This catches issues that would cause file growth, like:
        - Tuple strings that weren't properly converted
        - Overly large values in growMediums
        """
        if not isinstance(state, dict):
            return state
        
        # Sanitize growMediums
        if "growMediums" in state and isinstance(state["growMediums"], list):
            sanitized_mediums = []
            for medium in state["growMediums"]:
                if not isinstance(medium, dict):
                    continue
                
                # Check properties for corrupted data
                props = medium.get("properties", {})
                if isinstance(props, dict):
                    for key in ["ph_range", "ec_range"]:
                        val = props.get(key)
                        # Convert tuples to lists
                        if isinstance(val, tuple):
                            props[key] = list(val)
                        # Fix corrupted strings
                        elif _is_corrupted_tuple_string(val):
                            _LOGGER.warning(f"[{self.room}] Sanitizing corrupted {key} before save")
                            props[key] = [5.5, 7.0] if key == "ph_range" else [1.0, 2.5]
                        # Validate list format
                        elif isinstance(val, list) and len(val) != 2:
                            _LOGGER.warning(f"[{self.room}] Invalid {key} list length, using default")
                            props[key] = [5.5, 7.0] if key == "ph_range" else [1.0, 2.5]
                    
                    medium["properties"] = props
                
                sanitized_mediums.append(medium)
            
            state["growMediums"] = sanitized_mediums
        
        # Ensure plantsView is present for Camera timelapse config
        if "plantsView" not in state:
            _LOGGER.warning(f"[{self.room}] Adding default plantsView to state")
            state["plantsView"] = {
                "isTimeLapseActive": False,
                "TimeLapseIntervall": "",
                "StartDate": "",
                "EndDate": "",
                "OutPutFormat": "",
            }
        
        return state

    def _sync_save(self, json_string):
        with open(self.storage_path, "w", encoding="utf-8") as f:
            f.write(json_string)

    def _create_simplified_state(self, state):
        """Erstelle eine vereinfachte Version des States für die Serialisierung."""
        simplified = {}

        for key, value in state.items():
            try:
                json.dumps(value, default=str)
                simplified[key] = value
            except Exception:
                if isinstance(value, list) and len(value) > 0:
                    simplified[key] = [str(item) for item in value]
                else:
                    simplified[key] = str(value)

        return simplified

    async def loadState(self, data):
        """Lädt den Zustand aus der Datei und setzt ihn im DataStore."""
        if not os.path.exists(self.storage_path):
            _LOGGER.warning(f"⚠️ No saved state at {self.storage_path}")
            return
        try:
            loaded_data = await asyncio.to_thread(self._sync_load)
            
            # CRITICAL: Clean corrupted data before loading
            loaded_data = _clean_corrupted_data(loaded_data, self.room)
            
            _LOGGER.warning(f"✅ State loaded from {self.storage_path}")

            for key, value in loaded_data.items():
                self.data_store.set(key, value)

        except Exception as e:
            _LOGGER.error(f"❌ Failed to load DataStore: {e}")

    def _sync_load(self):
        with open(self.storage_path, "r") as f:
            return json.load(f)

    async def deleteState(self, data):
        """Löscht die gespeicherte Datei."""
        try:
            if os.path.exists(self.storage_path):
                await asyncio.to_thread(os.remove, self.storage_path)
                _LOGGER.warning(f"🗑️ Deleted saved state at {self.storage_path}")
            else:
                _LOGGER.warning(
                    f"⚠️ No state file found to delete at {self.storage_path}"
                )
        except Exception as e:
            _LOGGER.error(f"❌ Failed to delete state file: {e}")

    # =================================================================
    # SCRIPT STORAGE METHODS
    # Scripts are stored separately from state to avoid memory leaks
    # and ensure persistence across restarts
    # =================================================================

    def _get_script_dir(self) -> str:
        """Get or create the scripts directory."""
        script_dir = os.path.join(
            self.hass.config.path("ogb_data"),
            SCRIPT_DIR
        )
        os.makedirs(script_dir, exist_ok=True)
        return script_dir

    def _get_script_path(self, room: str, backup: bool = False) -> str:
        """Get the path for a script file.
        
        Args:
            room: Room name
            backup: If True, return backup file path
        """
        script_dir = self._get_script_dir()
        filename = f"{room.lower()}_script{SCRIPT_BACKUP_SUFFIX if backup else ''}.yaml"
        return os.path.join(script_dir, filename)

    async def load_script(self, room: str) -> Optional[Dict]:
        """Load script from file (NOT from DataStore).
        
        Args:
            room: Room name
            
        Returns:
            Script config dict or None if not found
        """
        script_path = self._get_script_path(room)
        
        if not os.path.exists(script_path):
            _LOGGER.debug(f"[{room}] No script file found at {script_path}")
            return None
        
        # Check file size
        try:
            file_size_kb = os.path.getsize(script_path) / 1024
            if file_size_kb > SCRIPT_MAX_SIZE_KB:
                _LOGGER.error(f"[{room}] Script file too large ({file_size_kb:.1f}KB), max {SCRIPT_MAX_SIZE_KB}KB")
                return None
        except Exception as e:
            _LOGGER.warning(f"[{room}] Could not check script file size: {e}")
        
        try:
            content = await self.hass.async_add_executor_job(
                self._sync_load_script, script_path
            )
            _LOGGER.info(f"[{room}] Script loaded from {script_path}")
            return content
        except Exception as e:
            _LOGGER.error(f"[{room}] Failed to load script: {e}")
            return None

    def _sync_load_script(self, path: str) -> Dict:
        """Synchronously load script from file."""
        try:
            import yaml
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            _LOGGER.error(f"Failed to parse script YAML: {e}")
            raise

    async def save_script(self, room: str, script_config: Dict) -> bool:
        """Save script to file with backup.
        
        Args:
            room: Room name
            script_config: Script configuration dict
            
        Returns:
            True if saved successfully
        """
        # Validate size
        script_size_kb = len(str(script_config)) / 1024
        if script_size_kb > SCRIPT_MAX_SIZE_KB:
            _LOGGER.error(f"[{room}] Script too large ({script_size_kb:.1f}KB), max {SCRIPT_MAX_SIZE_KB}KB")
            return False
        
        # Create backup before saving
        await self._backup_script(room)
        
        # Save script
        script_path = self._get_script_path(room)
        try:
            await self.hass.async_add_executor_job(
                self._sync_save_script, script_path, script_config
            )
            _LOGGER.info(f"[{room}] Script saved to {script_path}")
            return True
        except Exception as e:
            _LOGGER.error(f"[{room}] Failed to save script: {e}")
            return False

    def _sync_save_script(self, path: str, config: Dict):
        """Synchronously save script to file."""
        import yaml
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        # Set secure permissions
        os.chmod(path, 0o600)

    async def _backup_script(self, room: str):
        """Create single backup of current script."""
        script_path = self._get_script_path(room)
        backup_path = self._get_script_path(room, backup=True)
        
        if not os.path.exists(script_path):
            return
        
        try:
            await self.hass.async_add_executor_job(
                shutil.copy2, script_path, backup_path
            )
            _LOGGER.debug(f"[{room}] Script backup created")
        except Exception as e:
            _LOGGER.warning(f"[{room}] Failed to create script backup: {e}")

    async def restore_script_backup(self, room: str) -> bool:
        """Restore script from backup.
        
        Args:
            room: Room name
            
        Returns:
            True if restored successfully
        """
        script_path = self._get_script_path(room)
        backup_path = self._get_script_path(room, backup=True)
        
        if not os.path.exists(backup_path):
            _LOGGER.warning(f"[{room}] No backup found to restore")
            return False
        
        try:
            await self.hass.async_add_executor_job(
                shutil.copy2, backup_path, script_path
            )
            _LOGGER.info(f"[{room}] Script restored from backup")
            return True
        except Exception as e:
            _LOGGER.error(f"[{room}] Failed to restore script backup: {e}")
            return False

    def load_template(self, template_name: str) -> Optional[Dict]:
        """Load a built-in template.
        
        Args:
            template_name: Name of the template
            
        Returns:
            Template config or None if not found
        """
        # First check if template exists as file
        template_dir = os.path.join(self._get_script_dir(), "templates")
        template_path = os.path.join(template_dir, f"{template_name}.yaml")
        
        if os.path.exists(template_path):
            try:
                import yaml
                with open(template_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
            except Exception as e:
                _LOGGER.warning(f"Failed to load template file {template_name}: {e}")
        
        # Fallback to built-in templates
        return self._get_builtin_template(template_name)

    def _get_builtin_template(self, template_name: str) -> Optional[Dict]:
        """Get built-in template defined in code."""
        templates = {
            "basic_vpd_control": {
                "enabled": True,
                "type": "dsl",
                "script": """// Basic VPD Control Template
READ vpd_current FROM vpd.current
READ vpd_max FROM vpd.perfectMax
READ vpd_min FROM vpd.perfectMin

IF vpd_current > vpd_max THEN
    LOG "VPD too high"
    CALL exhaust.increase
    CALL dehumidifier.increase
ENDIF

IF vpd_current < vpd_min THEN
    LOG "VPD too low"
    CALL exhaust.reduce
    CALL humidifier.increase
ENDIF
"""
            },
            "advanced_environment": {
                "enabled": True,
                "type": "dsl", 
                "script": """// Advanced Environment Control Template
READ vpd FROM vpd.current
READ vpd_max FROM vpd.perfectMax
READ temp FROM tentData.temperature
READ temp_max FROM tentData.maxTemp
READ is_light_on FROM isPlantDay.islightON

// Critical VPD check
IF vpd > vpd_max + 0.3 THEN
    LOG "CRITICAL: VPD way too high!" LEVEL=error
    CALL exhaust.increase WITH priority=emergency
    CALL dehumidifier.increase WITH priority=emergency
ENDIF

// Temperature safety
IF temp > temp_max - 2 THEN
    LOG "Temperature high"
    CALL cooler.increase
    CALL exhaust.increase
ENDIF

// Day/Night cycle
IF is_light_on THEN
    CALL light.on
ELSE
    CALL light.off
ENDIF
"""
            }
        }
        
        return templates.get(template_name)

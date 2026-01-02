import asyncio
import json
import logging
import os

_LOGGER = logging.getLogger(__name__)


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
            
            # Load growMediums first if present - this is critical for MediumManager
            if "growMediums" in data:
                mediums = data["growMediums"]
                _LOGGER.warning(f"[{self.room}] Found {len(mediums)} mediums in saved state")
                for m in mediums:
                    _LOGGER.warning(
                        f"[{self.room}]   - {m.get('name')}: plant_name={m.get('plant_name')}, "
                        f"breeder_name={m.get('breeder_name') or m.get('plant_strain')}, breeder_bloom_days={m.get('breeder_bloom_days')}"
                    )
            
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
        try:
            state = self.data_store.getFullState()
            _LOGGER.debug(
                f"✅ DataStore TO BE saved with Data {type(state)} items: {len(str(state))}"
            )

            # Teste JSON-Serialisierung vor dem Speichern
            try:
                json_string = json.dumps(state, indent=2, default=str)
                _LOGGER.debug(f"JSON serialization test successful")
            except Exception as json_error:
                _LOGGER.error(f"❌ JSON serialization failed: {json_error}")
                simplified_state = self._create_simplified_state(state)
                json_string = json.dumps(simplified_state, indent=2, default=str)
                _LOGGER.warning(f"⚠️ Saving simplified state instead")

            await asyncio.to_thread(self._sync_save, json_string)
            _LOGGER.debug(f"✅ DataStore saved to {self.storage_path}")

        except Exception as e:
            _LOGGER.error(f"❌ Failed to save DataStore: {e}")
            import traceback

            _LOGGER.error(f"❌ Full traceback: {traceback.format_exc()}")

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
            data = await asyncio.to_thread(self._sync_load)
            _LOGGER.warning(f"✅ State loaded from {self.storage_path}: {data}")

            for key, value in data.items():
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

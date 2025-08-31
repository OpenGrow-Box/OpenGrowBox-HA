import logging
import asyncio
import json
import os

_LOGGER = logging.getLogger(__name__)

class OGBDSManager:
    def __init__(self, hass, dataStore, eventManager, room, regListener):
        self.name = "OGB DataStore Manager"
        self.hass = hass
        self.room = room
        self.regListener = regListener
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False

        self.storage_filename = f"ogb_{self.room.lower()}_state.json"
        self.storage_path = self._get_secure_path(self.storage_filename)
        
        # Events
        self.eventManager.on("SaveState", self.saveState)
        self.eventManager.on("LoadState", self.loadState)
        self.eventManager.on("RestoreState", self.loadState)
        self.eventManager.on("DeleteState", self.deleteState)

        self.init()

    def init(self):
        self.is_initialized = True

    def _get_secure_path(self, filename: str) -> str:
        """Gibt einen sicheren Pfad unterhalb von /config/ogb_data zurück."""
        subdir = self.hass.config.path("ogb_data")
        os.makedirs(subdir, exist_ok=True)
        return os.path.join(subdir, filename)

    async def saveState(self, data):
        """Speichert den vollständigen aktuellen State."""
        try:
            state = self.dataStore.getFullState()
            _LOGGER.debug(f"✅ DataStore TO BE saved with Data {type(state)} items: {len(str(state))}")
            
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
        with open(self.storage_path, "w", encoding='utf-8') as f:
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

    async def loadState(self,data):
        """Lädt den Zustand aus der Datei und setzt ihn im DataStore."""
        if not os.path.exists(self.storage_path):
            _LOGGER.warning(f"⚠️ No saved state at {self.storage_path}")
            return
        try:
            data = await asyncio.to_thread(self._sync_load)
            _LOGGER.warning(f"✅ State loaded from {self.storage_path}: {data}")

            for key, value in data.items():
                self.dataStore.set(key, value)

        except Exception as e:
            _LOGGER.error(f"❌ Failed to load DataStore: {e}")

    def _sync_load(self):
        with open(self.storage_path, "r") as f:
            return json.load(f)

    async def deleteState(self,data):
        """Löscht die gespeicherte Datei."""
        try:
            if os.path.exists(self.storage_path):
                await asyncio.to_thread(os.remove, self.storage_path)
                _LOGGER.warning(f"🗑️ Deleted saved state at {self.storage_path}")
            else:
                _LOGGER.warning(f"⚠️ No state file found to delete at {self.storage_path}")
        except Exception as e:
            _LOGGER.error(f"❌ Failed to delete state file: {e}")

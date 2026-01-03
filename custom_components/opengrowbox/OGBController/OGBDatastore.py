import dataclasses
import logging

_LOGGER = logging.getLogger(__name__)


class SimpleEventEmitter:
    def __init__(self):
        self.events = {}  # Speichert Events und ihre Listener

    def on(self, event_name, callback):
        """Abonniere ein Event."""
        if event_name not in self.events:
            self.events[event_name] = []
        self.events[event_name].append(callback)

    def off(self, event_name, callback):
        """Entferne einen Listener von einem Event."""
        if event_name in self.events:
            self.events[event_name] = [
                cb for cb in self.events[event_name] if cb != callback
            ]

    def emit(self, event_name, *args, **kwargs):
        """Trigger ein Event und rufe alle zugehörigen Listener auf."""
        if event_name in self.events:
            for callback in self.events[event_name]:
                callback(*args, **kwargs)

    async def emit_async(self, event_name, *args, **kwargs):
        """Asynchrones Event auslösen."""
        if event_name in self.events:
            for callback in self.events[event_name]:
                if callable(callback):
                    await callback(*args, **kwargs)


class DataStore(SimpleEventEmitter):
    # Keys to exclude from serialization to prevent unbounded growth
    # These contain runtime data that shouldn't be persisted
    SERIALIZATION_EXCLUDE_KEYS = {
        # Heavy runtime objects
        "hass",
        "event_manager",
        "eventManager",
        "data_store",
        "dataStore",
        # GrowMedium runtime data (not persisted)
        "sensor_history",
        "sensor_readings",
        "sensor_type_map",
        # Lists that grow unbounded
        "readings",
        "histories",
        "media",
        # Device lists (reconstructed at startup) - these are BIG!
        "devices",       # Top-level device list (~25KB)
        "ownDeviceList", # Own device list
        "Devices",       # workData.Devices (~8KB)
        # Callback functions
        "callback",
        "callbacks",
        # Private attributes
        "_background_tasks",
        "_shutdown_event",
        # Task objects (not serializable)
        "sunrise_task",
        "sunset_task",
        "pause_event",
        # workData - runtime sensor data, reconstructed on startup
        "workData",
    }
    
    # Keys within CropSteering that should NOT be persisted (runtime data)
    # Only Calibration data and user settings should be saved
    CROPSTEERING_RUNTIME_KEYS = {
        # Current sensor values - change constantly
        "vwc_current",
        "ec_current",
        "weight_current",
        # Runtime phase tracking
        "phaseStartTime",
        "lastIrrigationTime",
        "lastCheck",
        "shotCounter",
        "startNightMoisture",
        # P1 runtime tracking
        "p1_start_vwc",
        "p1_irrigation_count",
        "p1_last_vwc",
        "p1_last_irrigation_time",
        # P3 runtime tracking
        "p3_emergency_count",
    }

    def __init__(self, initial_state):
        super().__init__()
        # Falls initial_state None ist, benutze das leere OGBConf Objekt
        self.state = initial_state

    def __repr__(self):
        return f"Datastore State:'{self.state}'"

    def get(self, key):
        """Ruft den Wert für einen Schlüssel ab."""
        return getattr(self.state, key, None)

    def set(self, key, value):
        """Setzt einen neuen Wert und löst Events aus, falls der Wert geändert wurde."""
        if getattr(self.state, key, None) != value:
            setattr(self.state, key, value)
            self.emit(key, value)

    def getDeep(self, path, default=None):
        """Ruft verschachtelte Daten anhand eines Pfads ab (für Attribute oder Schlüssel in Dictionaries)."""
        keys = path.split(".")
        data = self.state
        for key in keys:
            if isinstance(data, dict):  # Falls `data` ein Dictionary ist
                data = data.get(key, None)
            elif hasattr(data, key):  # Falls `data` ein Objekt ist
                data = getattr(data, key)
            else:
                return default  # Schlüssel oder Attribut existiert nicht
        return data if data is not None else default

    def setDeep(self, path, value):
        """Setzt einen Wert in verschachtelten Daten und löst Events aus."""
        keys = path.split(".")
        data = self.state
        for key in keys[:-1]:
            if isinstance(data, dict):
                if key not in data:
                    data[key] = (
                        {}
                    )  # Initialisiere verschachteltes Dictionary, falls es nicht existiert
                data = data[key]
            elif hasattr(data, key):
                data = getattr(data, key)
            else:
                raise AttributeError(
                    f"Cannot access '{key}' on '{type(data).__name__}'"
                )

        last_key = keys[-1]
        if isinstance(data, dict):
            data[last_key] = value
            self.emit(path, value)
        elif hasattr(data, last_key):
            if getattr(data, last_key) != value:
                setattr(data, last_key, value)
                self.emit(path, value)
        else:
            raise AttributeError(f"Cannot set '{last_key}' on '{type(data).__name__}'")

    def _should_exclude_key(self, key: str) -> bool:
        """Check if a key should be excluded from serialization."""
        if key in self.SERIALIZATION_EXCLUDE_KEYS:
            return True
        if key.startswith("_"):
            return True
        return False
    
    def _filter_cropsteering_for_save(self, cs_data: dict) -> dict:
        """Filter CropSteering dict to only include persistable data.
        
        Only saves:
        - Calibration data (VWCMax, VWCMin, timestamps)
        - User settings (ShotIntervall, ShotDuration, etc.)
        - Mode and Active state
        
        Excludes runtime data that changes constantly.
        """
        if not isinstance(cs_data, dict):
            return cs_data
        
        filtered = {}
        for key, value in cs_data.items():
            # Skip runtime keys
            if key in self.CROPSTEERING_RUNTIME_KEYS:
                continue
            
            # Keep Calibration data
            if key == "Calibration":
                filtered[key] = value
                continue
            
            # Keep mode/active settings
            if key in ("Mode", "Active", "ActiveMode", "CropPhase", "MediumType"):
                filtered[key] = value
                continue
            
            # Keep phase-specific user settings (ShotIntervall, VWCTarget, etc.)
            # These are dicts like {"p0": {"value": X}, "p1": {"value": Y}, ...}
            if isinstance(value, dict) and any(p in value for p in ["p0", "p1", "p2", "p3"]):
                filtered[key] = value
                continue
        
        return filtered

    def _make_serializable(self, obj, visited=None):
        """Konvertiert Objekte in JSON-serialisierbare Formate mit Schutz vor zirkulären Referenzen.
        
        CRITICAL: Tuples are converted to lists to prevent corruption on reload.
        Python's tuple() on a string converts each CHARACTER to an element,
        causing exponential data growth on save/load cycles.
        """
        if visited is None:
            visited = set()

        # Schutz vor zirkulären Referenzen
        obj_id = id(obj)
        if obj_id in visited:
            return f"<circular reference to {type(obj).__name__}>"

        if obj is None:
            return None
        elif isinstance(obj, (str, int, float, bool)):
            return obj
        elif isinstance(obj, tuple):
            # CRITICAL: Convert tuples to lists to prevent corruption
            # Tuples like (5.5, 6.5) must be saved as [5.5, 6.5]
            # Otherwise str(tuple) creates "(5.5, 6.5)" and tuple(str) corrupts data
            visited.add(obj_id)
            try:
                result = [self._make_serializable(item, visited) for item in obj]
                visited.discard(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return list(obj)
        elif isinstance(obj, list):
            visited.add(obj_id)
            try:
                result = [self._make_serializable(item, visited) for item in obj]
                visited.remove(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return [str(item) for item in obj]
        elif isinstance(obj, dict):
            visited.add(obj_id)
            try:
                result = {
                    key: self._make_serializable(value, visited)
                    for key, value in obj.items()
                    if not self._should_exclude_key(key)
                }
                visited.remove(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return {key: str(value) for key, value in obj.items() if not self._should_exclude_key(key)}
        elif dataclasses.is_dataclass(obj):
            visited.add(obj_id)
            try:
                # Konvertiere Dataclass zu Dictionary, aber schließe excluded keys aus
                result = {}
                for field in dataclasses.fields(obj):
                    if not self._should_exclude_key(field.name):
                        value = getattr(obj, field.name)
                        result[field.name] = self._make_serializable(value, visited)
                visited.remove(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return str(obj)
        elif hasattr(obj, "to_dict"):
            # PRIORITY: Always use to_dict() if available - this ensures objects
            # like GrowMedium use their optimized serialization
            visited.add(obj_id)
            try:
                dict_result = obj.to_dict()
                result = self._make_serializable(dict_result, visited)
                visited.remove(obj_id)
                return result
            except Exception as e:
                visited.discard(obj_id)
                _LOGGER.warning(f"to_dict() failed for {type(obj).__name__}: {e}")
                return str(obj)
        elif hasattr(obj, "__dict__"):
            visited.add(obj_id)
            try:
                # Für andere Objekte mit __dict__, konvertiere zu Dictionary
                # Exclude all keys in SERIALIZATION_EXCLUDE_KEYS
                result = {}
                for key, value in obj.__dict__.items():
                    if not self._should_exclude_key(key):
                        result[key] = self._make_serializable(value, visited)
                visited.remove(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return str(obj)
        else:
            # Als letzter Ausweg, konvertiere zu String
            return str(obj)

    def getFullState(self):
        """Gibt den vollständigen State als JSON-serialisierbares dict zurück."""
        try:
            if dataclasses.is_dataclass(self.state):
                # Erstelle eine Kopie des State-Objekts ohne excluded fields
                state_dict = {}
                for field in dataclasses.fields(self.state):
                    # Use centralized exclusion check
                    if self._should_exclude_key(field.name):
                        continue
                        
                    try:
                        value = getattr(self.state, field.name)
                        
                        # Special handling for CropSteering - filter runtime data
                        if field.name == "CropSteering" and isinstance(value, dict):
                            value = self._filter_cropsteering_for_save(value)
                        
                        state_dict[field.name] = self._make_serializable(value)
                    except Exception as e:
                        _LOGGER.warning(
                            f"⚠️ Failed to serialize field '{field.name}': {e}"
                        )
                        state_dict[field.name] = str(
                            getattr(self.state, field.name, "N/A")
                        )
                return state_dict
            else:
                return self._make_serializable(self.state)
        except Exception as e:
            _LOGGER.error(f"❌ Failed to get full state: {e}")
            return {"error": "Failed to serialize state", "message": str(e)}

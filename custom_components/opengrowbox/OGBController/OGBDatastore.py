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
        # CRITICAL FIX: Runtime metadata that should never be persisted!
        # These are dataclass Field objects that get corrupted when saved
        "DeviceProfiles",
        "lightLedTypes",
        "Light",
        "weather"
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
        # Repair keys that may have been corrupted by old buggy versions
        self._repair_corrupted_state_keys()

    def _repair_corrupted_state_keys(self):
        """Repair state keys that were corrupted by previous code bugs."""
        cap_cal = getattr(self.state, "capCalibration", None)
        if not isinstance(cap_cal, dict):
            setattr(self.state, "capCalibration", {"active": None, "results": {}})

        profiles = getattr(self.state, "DeviceProfiles", None)
        if not isinstance(profiles, dict):
            setattr(self.state, "DeviceProfiles", {})

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

    def get_active_value(self, path, default=None):
        """Smart getter: Liefert GrowPlan-Werte wenn aktiv, sonst normale Werte.

        Verwendung in Managern statt getDeep() für Umgebungswerte.
        Prüft ob GrowPlan aktiv ist und mapped tentData keys zu GrowPlan keys.

        Args:
            path: Pfad zum Wert (z.B. "tentData.maxTemp")
            default: Default-Wert wenn nicht gefunden

        Returns:
            Wert aus GrowPlan (wenn aktiv) oder aus normalem Pfad
        """
        # Prüfe ob GrowPlan aktiv ist
        grow_plan_active = self.get("growManagerActive")
        if grow_plan_active:
            # Hole aktuelle Woche - zuerst aus currentWeekData, dann aus weeks
            week_data = self.getDeep("growPlan.currentWeekData")
            if not week_data:
                # Versuche aus weeks zu laden
                weeks = self.getDeep("growPlan.weeks", [])
                current_week = self.getDeep("growPlan.currentWeek", 1)
                for week in weeks:
                    if week.get("week") == current_week:
                        week_data = week
                        break
            
            if week_data:
                env = week_data.get("environment", {})
                light_cycle = env.get("lightCycle", {})
                light_intensity = env.get("lightIntensity", {})
                tent_controls = week_data.get("tentControls", {})
                
                # Mapping mit neuer Datenstruktur (final)
                if path == "tentData.maxTemp":
                    temp = env.get("temperature", {})
                    if isinstance(temp, dict):
                        day_temp = temp.get("day")
                        if isinstance(day_temp, dict):
                            return day_temp.get("max")
                        return day_temp
                    return temp
                elif path == "tentData.minTemp":
                    temp = env.get("temperature", {})
                    if isinstance(temp, dict):
                        night_temp = temp.get("night")
                        if isinstance(night_temp, dict):
                            return night_temp.get("min")
                        return night_temp
                    return temp
                elif path == "tentData.maxHumidity":
                    return env.get("humidity", {}).get("day")
                elif path == "tentData.minHumidity":
                    return env.get("humidity", {}).get("night")
                elif path == "tentData.targetVPD":
                    return env.get("vpd", {}).get("target")
                elif path == "tentData.targetCO2":
                    co2 = env.get("co2", {})
                    if isinstance(co2, dict):
                        return co2.get("optimal")
                    return co2
                elif path == "tentMode":
                    return week_data.get("tentMode")
                elif path == "isPlantDay.lightOnTime":
                    start_hour = light_cycle.get("startTime")
                    if start_hour is not None:
                        return f"{int(start_hour):02d}:00:00"
                elif path == "isPlantDay.lightOffTime":
                    start_hour = light_cycle.get("startTime", 0)
                    on_hours = light_cycle.get("on", 0)
                    if start_hour is not None and on_hours is not None:
                        end_hour = (start_hour + on_hours) % 24
                        return f"{int(end_hour):02d}:00:00"
                elif path == "isPlantDay.sunRiseTime":
                    sunrise_min = light_cycle.get("sunrise", 0)
                    if sunrise_min:
                        return f"00:{int(sunrise_min):02d}:00"
                elif path == "isPlantDay.sunSetTime":
                    sunset_min = light_cycle.get("sunset", 0)
                    if sunset_min:
                        return f"00:{int(sunset_min):02d}:00"
                elif path == "DeviceMinMax.Light.minVoltage":
                    if isinstance(light_intensity, dict):
                        return light_intensity.get("min")
                    elif isinstance(light_intensity, (int, float)):
                        return 0
                elif path == "DeviceMinMax.Light.maxVoltage":
                    if isinstance(light_intensity, dict):
                        return light_intensity.get("max")
                    elif isinstance(light_intensity, (int, float)):
                        return light_intensity
                # TentControls
                elif path == "controlOptions.nightVpdHold":
                    return tent_controls.get("nightVpdHold", {}).get("enabled")
                elif path == "controlOptions.deviceDampening":
                    return tent_controls.get("deviceDampening", {}).get("enabled")
                elif path == "controlOptions.vpdDeterminationMode":
                    return tent_controls.get("vpdDetermination", {}).get("mode")
                elif path == "controlOptions.dryingMode":
                    return tent_controls.get("drying", {}).get("mode")
                elif path == "controlOptions.dryingEnabled":
                    return tent_controls.get("drying", {}).get("enabled")

        # Fallback zu normalem Pfad
        return self.getDeep(path, default)

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

    def delete(self, path):
        """Löscht einen Wert aus verschachtelten Daten.
        
        Args:
            path: Punkt-getrennter Pfad zum zu löschenden Wert (z.B. "deadband.target_vpd")
        """
        keys = path.split(".")
        data = self.state
        
        # Navigiere zum übergeordneten Element
        for key in keys[:-1]:
            if isinstance(data, dict):
                if key not in data:
                    return  # Schlüssel existiert nicht, nichts zu löschen
                data = data[key]
            elif hasattr(data, key):
                data = getattr(data, key)
            else:
                return  # Attribut existiert nicht, nichts zu löschen
        
        last_key = keys[-1]
        if isinstance(data, dict):
            if last_key in data:
                del data[last_key]
                self.emit(f"{path}.deleted", None)
        elif hasattr(data, last_key):
            # Bei Objekten können wir nicht wirklich löschen, also setzen wir auf None
            setattr(data, last_key, None)
            self.emit(f"{path}.deleted", None)

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

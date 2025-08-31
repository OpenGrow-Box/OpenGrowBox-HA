import logging
import dataclasses

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
            self.events[event_name] = [cb for cb in self.events[event_name] if cb != callback]

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
    def __init__(self, initial_state):
        super().__init__()
        # Falls initial_state None ist, benutze das leere OGBConf Objekt
        self.state = initial_state
        
    def __repr__(self):
        return (f"Datastore State:'{self.state}'")

    def get(self, key):
        """Ruft den Wert für einen Schlüssel ab."""
        return getattr(self.state, key, None)

    def set(self, key, value):
        """Setzt einen neuen Wert und löst Events aus, falls der Wert geändert wurde."""
        if getattr(self.state, key, None) != value:
            setattr(self.state, key, value)
            self.emit(key, value)

    def getDeep(self, path):
        """Ruft verschachtelte Daten anhand eines Pfads ab (für Attribute oder Schlüssel in Dictionaries)."""
        keys = path.split(".")
        data = self.state
        for key in keys:
            if isinstance(data, dict):  # Falls `data` ein Dictionary ist
                data = data.get(key, None)
            elif hasattr(data, key):  # Falls `data` ein Objekt ist
                data = getattr(data, key)
            else:
                return None  # Schlüssel oder Attribut existiert nicht
        return data

    def setDeep(self, path, value):
        """Setzt einen Wert in verschachtelten Daten und löst Events aus."""
        keys = path.split(".")
        data = self.state
        for key in keys[:-1]:
            if isinstance(data, dict):
                if key not in data:
                    data[key] = {}  # Initialisiere verschachteltes Dictionary, falls es nicht existiert
                data = data[key]
            elif hasattr(data, key):
                data = getattr(data, key)
            else:
                raise AttributeError(f"Cannot access '{key}' on '{type(data).__name__}'")
        
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

    def _make_serializable(self, obj, visited=None):
        """Konvertiert Objekte in JSON-serialisierbare Formate mit Schutz vor zirkulären Referenzen."""
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
                result = {key: self._make_serializable(value, visited) for key, value in obj.items() if key != 'hass'}
                visited.remove(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return {key: str(value) for key, value in obj.items() if key != 'hass'}
        elif dataclasses.is_dataclass(obj):
            visited.add(obj_id)
            try:
                # Konvertiere Dataclass zu Dictionary, aber schließe hass aus
                result = {}
                for field in dataclasses.fields(obj):
                    if field.name != 'hass':  # Schließe hass aus
                        value = getattr(obj, field.name)
                        result[field.name] = self._make_serializable(value, visited)
                visited.remove(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return str(obj)
        elif hasattr(obj, 'to_dict'):
            visited.add(obj_id)
            try:
                # Falls das Objekt eine to_dict Methode hat
                dict_result = obj.to_dict()
                result = self._make_serializable(dict_result, visited)
                visited.remove(obj_id)
                return result
            except:
                visited.discard(obj_id)
                return str(obj)
        elif hasattr(obj, '__dict__'):
            visited.add(obj_id)
            try:
                # Für andere Objekte mit __dict__, konvertiere zu Dictionary
                result = {}
                for key, value in obj.__dict__.items():
                    if key != 'hass' and not key.startswith('_'):  # Schließe hass und private Attribute aus
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
                # Erstelle eine Kopie des State-Objekts ohne das hass-Attribut
                state_dict = {}
                for field in dataclasses.fields(self.state):
                    if field.name != 'hass':  # Schließe hass vom Serialisierungsprozess aus
                        try:
                            value = getattr(self.state, field.name)
                            state_dict[field.name] = self._make_serializable(value)
                        except Exception as e:
                            _LOGGER.warning(f"⚠️ Failed to serialize field '{field.name}': {e}")
                            state_dict[field.name] = str(getattr(self.state, field.name, 'N/A'))
                return state_dict
            else:
                return self._make_serializable(self.state)
        except Exception as e:
            _LOGGER.error(f"❌ Failed to get full state: {e}")
            return {"error": "Failed to serialize state", "message": str(e)}
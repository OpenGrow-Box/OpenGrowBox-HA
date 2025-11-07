from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from collections import deque
import logging


_LOGGER = logging.getLogger(__name__)


class MediumType(Enum):
    """Enum for different grow mediums"""
    ROCKWOOL = "rockwool"
    SOIL = "soil"
    COCO = "coco"
    AERO = "aero"
    WATER = "water"
    PERLITE = "perlite"
    CUSTOM = "custom"


class DeviceAction(Enum):
    """Actions that can be performed on devices"""
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SET_LEVEL = "set_level"


@dataclass
class MediumProperties:
    """Properties of a grow medium"""
    water_retention: float  # 0-100%
    air_porosity: float  # 0-100%
    ph_range: tuple[float, float]
    ec_range: tuple[float, float]
    watering_frequency: float  # hours
    drainage_speed: str
    nutrient_storage: float  # 0-100%


@dataclass
class ThresholdConfig:
    """Configuration for medium thresholds that trigger device actions"""
    ph_min: Optional[float] = None
    ph_max: Optional[float] = None
    ec_min: Optional[float] = None
    ec_max: Optional[float] = None
    moisture_min: Optional[float] = None  # 0-100%
    moisture_max: Optional[float] = None  # 0-100%
    temp_min: Optional[float] = None  # °C
    temp_max: Optional[float] = None  # °C


@dataclass
class SensorReading:
    """Einzelne Sensor-Messung mit Timestamp"""
    value: Any
    unit: str
    sensor_type: str
    device_name: str
    timestamp: datetime
    entity_id: str


class ReadingHistory:
    """Verwaltet begrenzte Historie pro Sensor mit intelligenter Aggregation"""
    
    def __init__(self, max_entries: int = 10):
        self.max_entries = max_entries
        self.readings: deque = deque(maxlen=max_entries)
    
    def add(self, reading: SensorReading) -> None:
        """Fügt neue Messung hinzu, älteste wird automatisch gelöscht"""
        self.readings.append(reading)
    
    def get_latest(self) -> Optional[SensorReading]:
        """Gibt neueste Messung zurück"""
        return self.readings[-1] if self.readings else None
    
    def get_average(self) -> Optional[float]:
        """Berechnet Durchschnitt der Messwerte"""
        if not self.readings:
            return None
        try:
            values = [float(r.value) for r in self.readings if isinstance(r.value, (int, float, str))]
            return sum(values) / len(values) if values else None
        except (ValueError, TypeError):
            return None
    
    def get_all(self) -> list:
        """Gibt alle Messwerte in chronologischer Reihenfolge zurück"""
        return list(self.readings)
    
    def to_dict(self) -> Optional[Dict[str, Any]]:
        """Serialisiert nur relevante Daten"""
        if not self.readings:
            return None
        latest = self.get_latest()
        return {
            "latest": {
                "value": latest.value,
                "timestamp": latest.timestamp.isoformat(),
                "device_name": latest.device_name,
                "entity_id": latest.entity_id
            },
            "average": round(self.get_average(), 3) if self.get_average() is not None else None,
            "count": len(self.readings),
            "oldest_timestamp": self.readings[0].timestamp.isoformat(),
            "newest_timestamp": latest.timestamp.isoformat()
        }


class DeviceBinding:
    """Represents a device bound to the medium with conditions"""
    
    def __init__(
        self,
        device_id: str,
        device_name: str,
        action_on_trigger: DeviceAction,
        trigger_condition: str,  # e.g., "ph_too_high", "moisture_too_low"
        cooldown_minutes: int = 30,
        callback: Optional[Callable] = None
    ):
        self.device_id = device_id
        self.device_name = device_name
        self.action_on_trigger = action_on_trigger
        self.trigger_condition = trigger_condition
        self.cooldown_minutes = cooldown_minutes
        self.callback = callback
        self.last_triggered: Optional[datetime] = None
        self.is_active = True
    
    def can_trigger(self) -> bool:
        """Check if device can be triggered (respects cooldown)"""
        if not self.is_active:
            return False
        if self.last_triggered is None:
            return True
        elapsed = (datetime.now() - self.last_triggered).total_seconds() / 60
        return elapsed >= self.cooldown_minutes
    
    def trigger(self, value: Any = None) -> bool:
        """Trigger the device action"""
        if not self.can_trigger():
            _LOGGER.debug(f"Device {self.device_name} in cooldown, skipping trigger")
            return False
        
        _LOGGER.info(f"Triggering {self.device_name} - Action: {self.action_on_trigger.value}")
        self.last_triggered = datetime.now()
        
        if self.callback:
            try:
                self.callback(self.device_id, self.action_on_trigger, value)
                return True
            except Exception as e:
                _LOGGER.error(f"Error triggering device {self.device_name}: {e}")
                return False
        return True


class GrowMedium:
    """
    Grow medium with integrated event manager for device control
    Acts as fallback control when normal automation is not sufficient
    """
    MEDIUM_DEFAULTS: Dict[MediumType, MediumProperties] = {
        MediumType.ROCKWOOL: MediumProperties(
            water_retention=75.0,
            air_porosity=15.0,
            ph_range=(5.5, 6.5),
            ec_range=(1.0, 2.0),
            watering_frequency=6.0,
            drainage_speed="high",
            nutrient_storage=10.0
        ),
        MediumType.SOIL: MediumProperties(
            water_retention=60.0,
            air_porosity=30.0,
            ph_range=(6.0, 7.0),
            ec_range=(0.5, 1.5),
            watering_frequency=48.0,
            drainage_speed="medium",
            nutrient_storage=70.0
        ),
        MediumType.COCO: MediumProperties(
            water_retention=65.0,
            air_porosity=25.0,
            ph_range=(5.5, 6.5),
            ec_range=(1.0, 1.5),
            watering_frequency=12.0,
            drainage_speed="high",
            nutrient_storage=35.0
        ),
        MediumType.AERO: MediumProperties(
            water_retention=0.0,
            air_porosity=100.0,
            ph_range=(5.2, 6.2),
            ec_range=(1.0, 4.0),
            watering_frequency=0.25,
            drainage_speed="very_high",
            nutrient_storage=0.0
        ),
        MediumType.WATER: MediumProperties(
            water_retention=100.0,
            air_porosity=0.0,
            ph_range=(5.5, 6.5),
            ec_range=(1.2, 2.2),
            watering_frequency=0.0,
            drainage_speed="none",
            nutrient_storage=0.0
        ),
    }

    SENSOR_HISTORY_LIMIT = 10

    def __init__(
        self,
        eventManager,
        dataStore,
        room,
        medium_type: MediumType,
        name: Optional[str] = None,
        properties: Optional[MediumProperties] = None,
        volume_liters: Optional[float] = None,
        thresholds: Optional[ThresholdConfig] = None,
        custom_attributes: Optional[Dict[str, Any]] = None
    ):
        self.room = room
        self.dataStore = dataStore
        self.medium_type = medium_type
        self.name = name or medium_type.value
        self.created_at = datetime.now()
        self.volume_liters = volume_liters
        self.custom_attributes = custom_attributes or {}
        self.eventManager = eventManager
        
        # Neue Struktur: History pro Sensor-Typ mit ReadingHistory
        self.sensor_history: Dict[str, ReadingHistory] = {
            "ph": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "ec": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "moisture": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "temperature": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "light": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "humidity": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "temp": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "illuminance": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
            "battery": ReadingHistory(self.SENSOR_HISTORY_LIMIT),
        }
        
        # Mapping: entity_id -> sensor_type
        self.registered_sensors: Dict[str, List[str]] = {}  # sensor_type -> [entity_id, ...]
        self.sensor_type_map: Dict[str, str] = {}  # entity_id -> sensor_type
        
        # Rohdaten nur noch im Speicher (nicht persistiert)
        self.sensor_readings: Dict[str, SensorReading] = {}
        
        # Schnelle Zugriffe auf aktuelle Werte
        self.current_ph: Optional[float] = None
        self.current_ec: Optional[float] = None
        self.current_moisture: Optional[float] = None
        self.current_temp: Optional[float] = None
        self.current_light: Optional[int] = None

        # Set properties
        if properties:
            self.properties = properties
        else:
            self.properties = self.MEDIUM_DEFAULTS.get(
                medium_type,
                MediumProperties(
                    water_retention=50.0,
                    air_porosity=50.0,
                    ph_range=(5.5, 7.0),
                    ec_range=(1.0, 2.5),
                    watering_frequency=24.0,
                    drainage_speed="medium",
                    nutrient_storage=50.0
                )
            )
        
        # Thresholds for triggering devices
        self.thresholds = thresholds or ThresholdConfig()
        
        # Device bindings
        self.devices: Dict[str, DeviceBinding] = {}
        
        # Fallback mode
        self.fallback_enabled = True
        self.fallback_triggered = False
        
        # Rate-Limiting für LogForClient Events
        self.last_log_event_time: Optional[datetime] = None

    def _safe_float_convert(self, value: Any) -> Optional[float]:
        """Safely convert a value to float, return None if conversion fails"""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except (ValueError, TypeError):
                _LOGGER.warning(f"Cannot convert '{value}' to float")
                return None
        return None

    async def register_sensor(self, sensor_data: dict):
        """
        Registriert einen Sensor und speichert seinen aktuellen Wert.
        """
        entity_id = sensor_data["entity_id"]
        sensor_type = sensor_data["sensor_type"]
        value = sensor_data["value"]
        unit = sensor_data.get("unit", "")
        device_name = sensor_data.get("device_name", "Unknown")
        timestamp = sensor_data.get("last_update") or datetime.now()

        numeric_value = self._safe_float_convert(value)
        
        # Sensor-Typ registrieren
        if sensor_type not in self.registered_sensors:
            self.registered_sensors[sensor_type] = []
        if entity_id not in self.registered_sensors[sensor_type]:
            self.registered_sensors[sensor_type].append(entity_id)

        self.sensor_type_map[entity_id] = sensor_type

        # Reading-Objekt erstellen
        reading = SensorReading(
            value=value,
            unit=unit,
            sensor_type=sensor_type,
            device_name=device_name,
            timestamp=timestamp,
            entity_id=entity_id
        )
        
        self.sensor_readings[entity_id] = reading
        
        # Zur History hinzufügen
        if sensor_type in self.sensor_history and numeric_value is not None:
            self.sensor_history[sensor_type].add(reading)
            self._update_aggregated_value(sensor_type)

        # dataStore Update
        if sensor_type == "moisture":
            self._update_datastore_list("workData.moisture", entity_id, value, sensor_type)
        elif sensor_type == "ec":
            self._update_datastore_list("workData.ec", entity_id, value, sensor_type)

        # Event
        mediumStats = self.get_all_medium_values()
        await self.eventManager.emit("LogForClient", mediumStats, haEvent=True)
        self.last_log_event_time = datetime.now()

    def _update_aggregated_value(self, sensor_type: str) -> None:
        """Updates aggregierte Werte basierend auf History"""
        latest = self.sensor_history[sensor_type].get_latest()
        if not latest:
            return
        
        numeric_value = self._safe_float_convert(latest.value)
        if numeric_value is None:
            return
        
        if sensor_type == "ph":
            self.current_ph = numeric_value
        elif sensor_type == "ec":
            self.current_ec = numeric_value
        elif sensor_type in ["moisture", "humidity"]:
            self.current_moisture = numeric_value
        elif sensor_type in ["temperature", "temp"]:
            self.current_temp = numeric_value
        elif sensor_type in ["light", "illuminance"]:
            self.current_light = numeric_value

    def _update_datastore_list(self, path: str, entity_id: str, value: Any, sensor_type: str):
        items = self.dataStore.getDeep(path) or []
        updated = False
        for item in items:
            if item.get("entity_id") == entity_id:
                item["value"] = value
                item["sensor_type"] = sensor_type
                updated = True
                break
        if not updated:
            items.append({"entity_id": entity_id, "value": value, "sensor_type": sensor_type})
        self.dataStore.setDeep(path, items)

    def _should_send_log_event(self) -> bool:
        if not self.last_log_event_time:
            return True
        return (datetime.now() - self.last_log_event_time).total_seconds() >= 60

    async def update_sensor_reading_async(self, data: dict):
        """
        Aktualisiert einen Sensor-Wert mit Rate-Limiting.
        Erwartet: entity_id, sensor_type, state, last_update, unit, device_name
        """
        entity_id = data.get("entity_id")
        sensor_type = data.get("sensor_type")
        value = data.get("state")
        timestamp = data.get("last_update") or data.get("timestamp") or datetime.now()
        unit = data.get("unit", "")
        device_name = data.get("device_name", "Unknown")

        if not entity_id or sensor_type not in {"ph", "ec", "moisture", "light", "temperature", "battery", "illuminance", "humidity", "temp"}:
            return

        # Prüfen: Ist dieser Sensor für dieses Medium registriert?
        if entity_id not in self.sensor_type_map:
            return

        numeric_value = self._safe_float_convert(value)
        if numeric_value is None:
            return

        # Alter aggregierter Wert
        old_agg = {
            "ph": self.current_ph,
            "ec": self.current_ec,
            "moisture": self.current_moisture,
            "light": self.current_light,
            "temperature": self.current_temp,
            "temp": self.current_temp,
            "humidity": self.current_moisture,
            "illuminance": self.current_light
        }.get(sensor_type)

        # Nur bei Änderung fortfahren
        if old_agg == numeric_value:
            return

        # Reading erstellen und hinzufügen
        reading = SensorReading(
            value=value,
            unit=unit,
            sensor_type=sensor_type,
            device_name=device_name,
            timestamp=timestamp,
            entity_id=entity_id
        )
        
        self.sensor_readings[entity_id] = reading
        
        # Zur History hinzufügen
        if sensor_type in self.sensor_history:
            self.sensor_history[sensor_type].add(reading)
            self._update_aggregated_value(sensor_type)

        # dataStore
        if sensor_type == "moisture":
            self._update_datastore_list("workData.moisture", entity_id, value, sensor_type)
        elif sensor_type == "ec":
            self._update_datastore_list("workData.ec", entity_id, value, sensor_type)

        # Rate-limited Event
        if self._should_send_log_event():
            _LOGGER.info(f"{self.room} - Medium {self.name}: {sensor_type} = {value}")
            await self.eventManager.emit("LogForClient", self.get_all_medium_values(), haEvent=True)
            self.last_log_event_time = datetime.now()

    def unregister_sensor(self, entity_id: str) -> bool:
        """Entfernt einen Sensor von diesem Medium."""
        if entity_id in self.sensor_type_map:
            sensor_type = self.sensor_type_map[entity_id]
            if sensor_type in self.registered_sensors and entity_id in self.registered_sensors[sensor_type]:
                self.registered_sensors[sensor_type].remove(entity_id)
            self.sensor_readings.pop(entity_id, None)
            self.sensor_type_map.pop(entity_id, None)
            _LOGGER.info(f"Medium {self.name}: Sensor {entity_id} entfernt")
            return True
        return False

    def get_sensor_value(self, sensor_type: str) -> Optional[Any]:
        """Gibt den aggregierten Wert eines Sensor-Typs zurück."""
        return {
            "ph": self.current_ph,
            "ec": self.current_ec,
            "moisture": self.current_moisture,
            "light": self.current_light,
            "temperature": self.current_temp
        }.get(sensor_type)

    def get_sensor_history(self, sensor_type: str) -> Optional[Dict[str, Any]]:
        """Gibt kompakte History eines Sensor-Typs zurück"""
        if sensor_type in self.sensor_history:
            return self.sensor_history[sensor_type].to_dict()
        return None

    def get_all_medium_values(self):
        ec_value = self.current_ec
        ec_unit = getattr(self, "medium_ec_unit", None)

        if ec_value is None:
            converted_ec = None
            ec_unit_detected = "unknown"
        else:
            try:
                ec_value = float(ec_value)
                converted_ec = ec_value
                ec_unit_detected = ec_unit or "auto"

                if ec_unit:
                    unit = ec_unit.lower().replace("µ", "u").replace("/cm", "").strip()
                    if unit in ["us", "uscm", "u"]:
                        converted_ec = ec_value / 1000
                        ec_unit_detected = "µS (converted)"
                    else:
                        ec_unit_detected = "mS (as given)"
                else:
                    if ec_value > 20:
                        converted_ec = ec_value / 1000
                        ec_unit_detected = "µS (auto-detected)"
                    else:
                        ec_unit_detected = "mS (auto-detected)"
            except (ValueError, TypeError):
                converted_ec = None
                ec_unit_detected = "invalid"

        return {
            "Name": f"{self.room} - Medium: {self.name.upper()} Info",
            "medium": True,
            "room": self.room,
            "medium_type": self.medium_type.value,
            "medium_ec": round(converted_ec, 3) if converted_ec is not None else None,
            "medium_ec_unit": "mS/cm" if converted_ec is not None else None,
            "medium_ec_source_unit": ec_unit_detected,
            "medium_ph": self.current_ph,
            "medium_moisture": self.current_moisture,
            "medium_light": self.current_light,
            "medium_temp": self.current_temp,
            "medium_sensors_total": sum(len(v) for v in self.registered_sensors.values()),
            "medium_sensors": self.registered_sensors,
            "sensor_history": {
                sensor_type: self.get_sensor_history(sensor_type)
                for sensor_type in self.sensor_history.keys()
                if self.get_sensor_history(sensor_type) is not None
            },
            "timestamp": datetime.now()
        }

    def is_ph_optimal(self, ph_value: Optional[float] = None) -> bool:
        value = ph_value if ph_value is not None else self.current_ph
        if value is None:
            return False
        min_ph, max_ph = self.properties.ph_range
        return min_ph <= value <= max_ph

    def is_ec_optimal(self, ec_value: Optional[float] = None) -> bool:
        value = ec_value if ec_value is not None else self.current_ec
        if value is None:
            return False
        min_ec, max_ec = self.properties.ec_range
        return min_ec <= value <= max_ec

    def get_status(self) -> Dict[str, Any]:
        current_readings = {}

        if self.current_moisture is not None:
            current_readings["moisture"] = self.current_moisture
        if self.current_light is not None:
            current_readings["light"] = self.current_light
        if self.current_ec is not None:
            current_readings["ec"] = self.current_ec
        if self.current_ph is not None:
            current_readings["ph"] = self.current_ph

        status = {
            "name": self.name,
            "type": self.medium_type.value,
            "sensor_count": sum(len(v) for v in self.registered_sensors.values()),
            "sensor_types": list(self.registered_sensors.keys()),
            "bound_devices": len(self.devices),
            "active_devices": sum(1 for d in self.devices.values() if d.is_active),
            "optimal_status": {
                "ec": self.is_ec_optimal(),
                "ph": self.is_ph_optimal()
            },
            "current_readings": current_readings,
            "fallback_enabled": self.fallback_enabled,
            "fallback_triggered": self.fallback_triggered
        }

        return {
            "name": self.name,
            "type": self.medium_type.value,
            "created_at": self.created_at.isoformat(),
            "volume_liters": self.volume_liters,
            "properties": {
                "water_retention": self.properties.water_retention,
                "air_porosity": self.properties.air_porosity,
                "ph_range": self.properties.ph_range,
                "ec_range": self.properties.ec_range,
                "watering_frequency": self.properties.watering_frequency,
                "drainage_speed": self.properties.drainage_speed,
                "nutrient_storage": self.properties.nutrient_storage,
            },
            "thresholds": {
                "ph_min": self.thresholds.ph_min,
                "ph_max": self.thresholds.ph_max,
                "ec_min": self.thresholds.ec_min,
                "ec_max": self.thresholds.ec_max,
                "moisture_min": self.thresholds.moisture_min,
                "moisture_max": self.thresholds.moisture_max,
                "temp_min": self.thresholds.temp_min,
                "temp_max": self.thresholds.temp_max,
            },
            "status": status,
            "registered_sensors": self.registered_sensors,
            "custom_attributes": self.custom_attributes,
            "sensor_history": {
                sensor_type: self.get_sensor_history(sensor_type)
                for sensor_type in self.sensor_history.keys()
                if self.get_sensor_history(sensor_type) is not None
            },
            "timestamp": datetime.now().isoformat()
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert medium to dictionary"""
        return self.get_status()

    @classmethod
    def from_dict(cls, data: Dict[str, Any], eventManager, dataStore, room) -> 'GrowMedium':
        medium_type = MediumType(data["type"])
        name = data.get("name", medium_type.value)

        medium = cls(
            eventManager=eventManager,
            dataStore=dataStore,
            room=room,
            medium_type=medium_type,
            name=name,
            volume_liters=data.get("volume_liters"),
            custom_attributes=data.get("custom_attributes", {})
        )

        # Properties
        props = data.get("properties", {})
        medium.properties = MediumProperties(
            water_retention=props.get("water_retention", 50.0),
            air_porosity=props.get("air_porosity", 50.0),
            ph_range=tuple(props.get("ph_range", (5.5, 7.0))),
            ec_range=tuple(props.get("ec_range", (1.0, 2.5))),
            watering_frequency=props.get("watering_frequency", 24.0),
            drainage_speed=props.get("drainage_speed", "medium"),
            nutrient_storage=props.get("nutrient_storage", 50.0)
        )

        # Thresholds
        thresh = data.get("thresholds", {})
        medium.thresholds = ThresholdConfig(**{k: v for k, v in thresh.items() if k in ThresholdConfig.__annotations__})

        # created_at
        if "created_at" in data:
            try:
                medium.created_at = datetime.fromisoformat(data["created_at"])
            except:
                pass

        # Sensoren
        medium.registered_sensors = data.get("registered_sensors", {})

        return medium

    # --- Device Management ---
    def bind_device(
        self,
        device_id: str,
        device_name: str,
        action_on_trigger: DeviceAction,
        trigger_condition: str,
        cooldown_minutes: int = 30,
        callback: Optional[Callable] = None
    ) -> None:
        device = DeviceBinding(
            device_id=device_id,
            device_name=device_name,
            action_on_trigger=action_on_trigger,
            trigger_condition=trigger_condition,
            cooldown_minutes=cooldown_minutes,
            callback=callback
        )
        self.devices[device_id] = device
        _LOGGER.info(f"Bound device {device_name} to medium {self.name}")

    def unbind_device(self, device_id: str) -> None:
        if device_id in self.devices:
            del self.devices[device_id]
            _LOGGER.info(f"Unbound device {device_id} from medium {self.name}")

    def enable_device(self, device_id: str) -> None:
        if device_id in self.devices:
            self.devices[device_id].is_active = True

    def disable_device(self, device_id: str) -> None:
        if device_id in self.devices:
            self.devices[device_id].is_active = False

    # --- Fallback Evaluation ---
    def update_sensor_readings(
        self,
        ph: Optional[float] = None,
        ec: Optional[float] = None,
        moisture: Optional[float] = None,
        temp: Optional[float] = None
    ) -> None:
        if ph is not None:
            self.current_ph = self._safe_float_convert(ph)
        if ec is not None:
            self.current_ec = self._safe_float_convert(ec)
        if moisture is not None:
            self.current_moisture = self._safe_float_convert(moisture)
        if temp is not None:
            self.current_temp = self._safe_float_convert(temp)
        if self.fallback_enabled:
            self._evaluate_conditions()

    def _evaluate_conditions(self) -> None:
        triggered_devices = []
        if self.current_ph is not None:
            if self.thresholds.ph_max and self.current_ph > self.thresholds.ph_max:
                triggered_devices.extend(self._trigger_condition("ph_too_high", self.current_ph))
            elif self.thresholds.ph_min and self.current_ph < self.thresholds.ph_min:
                triggered_devices.extend(self._trigger_condition("ph_too_low", self.current_ph))
        if self.current_ec is not None:
            if self.thresholds.ec_max and self.current_ec > self.thresholds.ec_max:
                triggered_devices.extend(self._trigger_condition("ec_too_high", self.current_ec))
            elif self.thresholds.ec_min and self.current_ec < self.thresholds.ec_min:
                triggered_devices.extend(self._trigger_condition("ec_too_low", self.current_ec))
        if self.current_moisture is not None:
            if self.thresholds.moisture_max and self.current_moisture > self.thresholds.moisture_max:
                triggered_devices.extend(self._trigger_condition("moisture_too_high", self.current_moisture))
            elif self.thresholds.moisture_min and self.current_moisture < self.thresholds.moisture_min:
                triggered_devices.extend(self._trigger_condition("moisture_too_low", self.current_moisture))
        if self.current_temp is not None:
            if self.thresholds.temp_max and self.current_temp > self.thresholds.temp_max:
                triggered_devices.extend(self._trigger_condition("temp_too_high", self.current_temp))
            elif self.thresholds.temp_min and self.current_temp < self.thresholds.temp_min:
                triggered_devices.extend(self._trigger_condition("temp_too_low", self.current_temp))
        if triggered_devices:
            self.fallback_triggered = True
            _LOGGER.warning(f"Fallback triggered for medium {self.name}: {triggered_devices}")
        else:
            self.fallback_triggered = False

    def _trigger_condition(self, condition: str, value: Any) -> List[str]:
        triggered = []
        for device_id, device in self.devices.items():
            if device.trigger_condition == condition:
                if device.trigger(value):
                    triggered.append(device.device_name)
        return triggered

    # --- Factory Methods ---
    @classmethod
    def create_rockwool(cls, eventManager, dataStore, room, volume_liters: float = 10.0, **kwargs) -> 'GrowMedium':
        return cls(eventManager, dataStore, room, MediumType.ROCKWOOL, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_soil(cls, eventManager, dataStore, room, volume_liters: float = 20.0, **kwargs) -> 'GrowMedium':
        return cls(eventManager, dataStore, room, MediumType.SOIL, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_coco(cls, eventManager, dataStore, room, volume_liters: float = 15.0, **kwargs) -> 'GrowMedium':
        return cls(eventManager, dataStore, room, MediumType.COCO, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_aero(cls, eventManager, dataStore, room, **kwargs) -> 'GrowMedium':
        return cls(eventManager, dataStore, room, MediumType.AERO, volume_liters=0.0, **kwargs)
    
    @classmethod
    def create_water(cls, eventManager, dataStore, room, volume_liters: float = 50.0, **kwargs) -> 'GrowMedium':
        return cls(eventManager, dataStore, room, MediumType.WATER, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_custom(
        cls,
        eventManager, dataStore, room,
        name: str,
        properties: MediumProperties,
        volume_liters: Optional[float] = None,
        **kwargs
    ) -> 'GrowMedium':
        return cls(
            eventManager, dataStore, room,
            MediumType.CUSTOM,
            name=name,
            properties=properties,
            volume_liters=volume_liters,
            **kwargs
        )
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
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

    def __init__(
        self,
        eventManager:None,
        dataStore:None,
        room:None,
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
        self.registered_sensors: Dict[str, List[str]] = {}
        self.sensor_readings: Dict[str, Any] = {}
        self.last_reading_time: Dict[str, datetime] = {}
    
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
        
        # Current sensor readings
        self.current_ph: Optional[float] = None
        self.current_ec: Optional[float] = None
        self.current_moisture: Optional[float] = None
        self.current_temp: Optional[float] = None
        self.current_light: Optional[int] = None
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
        
        Args:
            sensor_data: {
                "entity_id": "sensor.sensorsoilflower_ec",
                "sensor_type": "ec",
                "value": 2.3,
                "unit": "mS/cm",
                "context": "soil",
                "device_name": "SensorSoilFlower",
                "room": "Grow Room 1",
                "medium_label": "aero_1"
            }
        """
        entity_id = sensor_data["entity_id"]
        sensor_type = sensor_data["sensor_type"]
        value = sensor_data["value"]
        unit = sensor_data.get("unit", "")
        device_name = sensor_data.get("device_name", "Unknown")

        # Convert value to float safely
        numeric_value = self._safe_float_convert(value)
        
        # Sensor-Typ registrieren
        if sensor_type not in self.registered_sensors:
            self.registered_sensors[sensor_type] = []
                    
        if entity_id not in self.registered_sensors[sensor_type]:
            self.registered_sensors[sensor_type].append(entity_id)
            _LOGGER.warning(f"✓ Medium {self.name}: Sensor {entity_id} ({sensor_type}) registriert")
        
        # Wert auf entsprechende Property schreiben (as float)
        if sensor_type == "ph":
            self.current_ph = numeric_value
        elif sensor_type == "ec":
            self.current_ec = numeric_value
        elif sensor_type in ["moisture", "humidity"]:
            self.current_moisture = numeric_value
        elif sensor_type in ["temperature", "temp"]:
            self.current_temp = numeric_value
        elif sensor_type in ["light"]:
            self.current_light = numeric_value
            
        # Sensorwerte speichern (original value for display)
        self.sensor_readings[entity_id] = {
            "value": numeric_value,
            "unit": unit,
            "sensor_type": sensor_type,
            "device_name": device_name
        }
        self.last_reading_time[entity_id] = datetime.now()
        

        # --- Moisture speichern ---
        if sensor_type == "moisture":
            moistures = self.dataStore.getDeep("workData.moisture") or []
            updated = False

            for item in moistures:
                if item.get("entity_id") == entity_id:
                    item["value"] = value
                    item["sensor_type"] = sensor_type
                    updated = True
                    break

            if not updated:
                moistures.append({
                    "entity_id": entity_id,
                    "value": value,
                    "sensor_type": sensor_type,
                })

            self.dataStore.setDeep("workData.moisture", moistures)

        # --- EC speichern ---
        if sensor_type == "ec":
            ecs = self.dataStore.getDeep("workData.ec") or []
            updated = False

            for item in ecs:
                if item.get("entity_id") == entity_id:
                    item["value"] = value
                    item["sensor_type"] = sensor_type
                    updated = True
                    break

            if not updated:
                ecs.append({
                    "entity_id": entity_id,
                    "value": value,
                    "sensor_type": sensor_type,
                })

            self.dataStore.setDeep("workData.ec", ecs)

        
        mediumStats = self.get_all_medium_values()
        await self.eventManager.emit("LogForClient", mediumStats, haEvent=True)
        self.last_log_event_time = datetime.now()    
        # Schwellenwerte prüfen und ggf. Geräte triggern
        #if self.fallback_enabled:
        #    await self._check_thresholds(sensor_type, value)
    
    def unregister_sensor(self, entity_id: str) -> bool:
        """Entfernt einen Sensor von diesem Medium."""
        for sensor_type, entities in self.registered_sensors.items():
            if entity_id in entities:
                entities.remove(entity_id)
                _LOGGER.info(f"Medium {self.name}: Sensor {entity_id} entfernt")
                return True
        return False
    
    async def update_sensor_reading_async(self, data):
        """Aktualisiert einen Sensor-Wert mit Rate-Limiting für Events (max. 1x pro Minute)."""

        sensor_type = data.get("sensor_type")
        value = data.get("state")
        entity_id = data.get("entity_id")
        timestamp = data.get("timestamp") or datetime.now()

        old_value = self.sensor_readings.get(sensor_type)

        # Nur fortfahren wenn sich der Wert wirklich geändert hat
        if old_value == value:
            return

        # Wert in den internen Cache schreiben
        self.sensor_readings[sensor_type] = value
        self.last_reading_time[sensor_type] = datetime.now()

        # Wert auf interne Properties schreiben
        numeric_value = self._safe_float_convert(value)
        if sensor_type == "ph":
            self.current_ph = numeric_value
        elif sensor_type == "ec":
            self.current_ec = numeric_value
        elif sensor_type in ["moisture", "humidity"]:
            self.current_moisture = numeric_value
        elif sensor_type in ["temperature", "temp"]:
            self.current_temp = numeric_value
        elif sensor_type in ["light"]:
            self.current_light = numeric_value

        # --- Moisture speichern ---
        if sensor_type == "moisture":
            moistures = self.dataStore.getDeep("workData.moisture") or []
            updated = False

            for item in moistures:
                if item.get("entity_id") == entity_id:
                    item["value"] = value
                    item["sensor_type"] = sensor_type
                    updated = True
                    break

            if not updated:
                moistures.append({
                    "entity_id": entity_id,
                    "value": value,
                    "sensor_type": sensor_type,
                })

            self.dataStore.setDeep("workData.moisture", moistures)

        # --- EC speichern ---
        if sensor_type == "ec":
            ecs = self.dataStore.getDeep("workData.ec") or []
            updated = False

            for item in ecs:
                if item.get("entity_id") == entity_id:
                    item["value"] = value
                    item["sensor_type"] = sensor_type
                    updated = True
                    break

            if not updated:
                ecs.append({
                    "entity_id": entity_id,
                    "value": value,
                    "sensor_type": sensor_type,
                })

            self.dataStore.setDeep("workData.ec", ecs)

        # Rate-Limit für LogForClient Events
        should_send_event = True
        if self.last_log_event_time:
            time_diff = (datetime.now() - self.last_log_event_time).total_seconds()
            if time_diff < 60:
                should_send_event = False

        if should_send_event:
            _LOGGER.warning(
                f"{self.room} - 📊 Medium {self.name}: {sensor_type} = {value} "
                f"(vorher: {old_value})"
            )
            mediumStats = self.get_all_medium_values()
            await self.eventManager.emit("LogForClient", mediumStats, haEvent=True)
            self.last_log_event_time = datetime.now()

    def get_sensor_value(self, sensor_type: str) -> Optional[Any]:
        """Gibt den aktuellen Wert eines Sensor-Typs zurück."""
        return self.sensor_readings.get(sensor_type)
    
    def get_all_readings(self) -> Dict[str, Any]:
        """Gibt alle aktuellen Readings zurück."""
        return {
            "name": self.name,
            "medium_type": self.medium_type.value,
            "readings": self.sensor_readings.copy(),
            "last_updates": {
                k: v.isoformat() if v else None 
                for k, v in self.last_reading_time.items()
            },
            "registered_sensors": self.registered_sensors
        }
   
    # Factory methods
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GrowMedium':
        """Erstellt Medium aus Dictionary."""
        # Erstelle das Medium ohne die problematischen Felder
        medium = cls(
            medium_type=MediumType(data["type"]),
            name=data.get("name", "Unnamed Medium"),
            properties=MediumProperties(
                water_retention=50.0,
                air_porosity=50.0,
                ph_range=(5.5, 7.0),
                ec_range=(1.0, 2.5),
                watering_frequency=24.0,
                drainage_speed="medium",
                nutrient_storage=50.0
            )
        )
        
        # Setze Properties korrekt
        if "properties" in data:
            props_data = data["properties"]
            medium.properties = MediumProperties(
                water_retention=props_data.get("water_retention", 50.0),
                air_porosity=props_data.get("air_porosity", 50.0),
                ph_range=tuple(props_data.get("ph_range", (5.5, 7.0))),
                ec_range=tuple(props_data.get("ec_range", (1.0, 2.5))),
                watering_frequency=props_data.get("watering_frequency", 24.0),
                drainage_speed=props_data.get("drainage_speed", "medium"),
                nutrient_storage=props_data.get("nutrient_storage", 50.0)
            )
        
        # Setze Volume
        medium.volume_liters = data.get("volume_liters")
        
        # Setze Thresholds
        if "thresholds" in data:
            thresh_data = data["thresholds"]
            medium.thresholds = ThresholdConfig(
                ph_min=thresh_data.get("ph_min"),
                ph_max=thresh_data.get("ph_max"),
                ec_min=thresh_data.get("ec_min"),
                ec_max=thresh_data.get("ec_max"),
                moisture_min=thresh_data.get("moisture_min"),
                moisture_max=thresh_data.get("moisture_max"),
                temp_min=thresh_data.get("temp_min"),
                temp_max=thresh_data.get("temp_max")
            )
        
        # Setze created_at
        if "created_at" in data:
            try:
                medium.created_at = datetime.fromisoformat(data["created_at"])
            except (ValueError, TypeError):
                medium.created_at = datetime.now()
        
        # Normale Dictionary-Zuweisung
        medium.registered_sensors = data.get("registered_sensors", {})
        medium.sensor_readings = data.get("sensor_readings", {})
        medium.custom_attributes = data.get("custom_attributes", {})
        
        # Sichere Initialisierung von last_reading_time
        last_reading_data = data.get("last_reading_time", {})
        for k, v in last_reading_data.items():
            if v:
                try:
                    medium.last_reading_time[k] = datetime.fromisoformat(v)
                except (ValueError, TypeError):
                    _LOGGER.warning(f"Ungültiger Zeitstempel für {k}: {v}, überspringe")
                    continue
        
        # Setze aktuelle Readings (Fallback-Werte) - SAFE FLOAT CONVERSION
        status = data.get("status", {})
        current_readings = status.get("current_readings", {})
        medium.current_ph = medium._safe_float_convert(current_readings.get("ph"))
        medium.current_ec = medium._safe_float_convert(current_readings.get("ec"))
        medium.current_moisture = medium._safe_float_convert(current_readings.get("moisture"))
        medium.current_temp = medium._safe_float_convert(current_readings.get("temp"))
        
        return medium

    @classmethod
    def create_rockwool(cls, volume_liters: float = 10.0, **kwargs) -> 'GrowMedium':
        return cls(MediumType.ROCKWOOL, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_soil(cls, volume_liters: float = 20.0, **kwargs) -> 'GrowMedium':
        return cls(MediumType.SOIL, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_coco(cls, volume_liters: float = 15.0, **kwargs) -> 'GrowMedium':
        return cls(MediumType.COCO, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_aero(cls, **kwargs) -> 'GrowMedium':
        return cls(MediumType.AERO, volume_liters=0.0, **kwargs)
    
    @classmethod
    def create_water(cls, volume_liters: float = 50.0, **kwargs) -> 'GrowMedium':
        return cls(MediumType.WATER, volume_liters=volume_liters, **kwargs)
    
    @classmethod
    def create_custom(
        cls,
        name: str,
        properties: MediumProperties,
        volume_liters: Optional[float] = None,
        **kwargs
    ) -> 'GrowMedium':
        return cls(
            MediumType.CUSTOM,
            name=name,
            properties=properties,
            volume_liters=volume_liters,
            **kwargs
        )
    
    # Device management
    def bind_device(
        self,
        device_id: str,
        device_name: str,
        action_on_trigger: DeviceAction,
        trigger_condition: str,
        cooldown_minutes: int = 30,
        callback: Optional[Callable] = None
    ) -> None:
        """
        Bind a device to the medium for automatic control
        
        Args:
            device_id: Unique device identifier
            device_name: Human-readable name
            action_on_trigger: Action to perform when triggered
            trigger_condition: Condition that triggers action
                (e.g., "ph_too_high", "ph_too_low", "ec_too_high", 
                "moisture_too_low", "temp_too_high")
            cooldown_minutes: Minimum time between triggers
            callback: Function to call when device is triggered
        """
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
        """Remove device binding"""
        if device_id in self.devices:
            del self.devices[device_id]
            _LOGGER.info(f"Unbound device {device_id} from medium {self.name}")
    
    def enable_device(self, device_id: str) -> None:
        """Enable a specific device"""
        if device_id in self.devices:
            self.devices[device_id].is_active = True
    
    def disable_device(self, device_id: str) -> None:
        """Disable a specific device"""
        if device_id in self.devices:
            self.devices[device_id].is_active = False
    
    # Sensor updates and evaluation
    def update_sensor_readings(
        self,
        ph: Optional[float] = None,
        ec: Optional[float] = None,
        moisture: Optional[float] = None,
        temp: Optional[float] = None
    ) -> None:
        """
        Update current sensor readings and evaluate conditions
        This triggers device actions if thresholds are exceeded
        """
        if ph is not None:
            self.current_ph = self._safe_float_convert(ph)
        if ec is not None:
            self.current_ec = self._safe_float_convert(ec)
        if moisture is not None:
            self.current_moisture = self._safe_float_convert(moisture)
        if temp is not None:
            self.current_temp = self._safe_float_convert(temp)
        
        # Evaluate conditions and trigger devices if needed
        if self.fallback_enabled:
            self._evaluate_conditions()
    
    def _evaluate_conditions(self) -> None:
        """Evaluate all conditions and trigger appropriate devices"""
        triggered_devices = []
        
        # Check pH conditions
        if self.current_ph is not None:
            if self.thresholds.ph_max and self.current_ph > self.thresholds.ph_max:
                triggered_devices.extend(self._trigger_condition("ph_too_high", self.current_ph))
            elif self.thresholds.ph_min and self.current_ph < self.thresholds.ph_min:
                triggered_devices.extend(self._trigger_condition("ph_too_low", self.current_ph))
        
        # Check EC conditions
        if self.current_ec is not None:
            if self.thresholds.ec_max and self.current_ec > self.thresholds.ec_max:
                triggered_devices.extend(self._trigger_condition("ec_too_high", self.current_ec))
            elif self.thresholds.ec_min and self.current_ec < self.thresholds.ec_min:
                triggered_devices.extend(self._trigger_condition("ec_too_low", self.current_ec))
        
        # Check moisture conditions
        if self.current_moisture is not None:
            if self.thresholds.moisture_max and self.current_moisture > self.thresholds.moisture_max:
                triggered_devices.extend(self._trigger_condition("moisture_too_high", self.current_moisture))
            elif self.thresholds.moisture_min and self.current_moisture < self.thresholds.moisture_min:
                triggered_devices.extend(self._trigger_condition("moisture_too_low", self.current_moisture))
        
        # Check temperature conditions
        if self.current_temp is not None:
            if self.thresholds.temp_max and self.current_temp > self.thresholds.temp_max:
                triggered_devices.extend(self._trigger_condition("temp_too_high", self.current_temp))
            elif self.thresholds.temp_min and self.current_temp < self.thresholds.temp_min:
                triggered_devices.extend(self._trigger_condition("temp_too_low", self.current_temp))
        
        # Update fallback status
        if triggered_devices:
            self.fallback_triggered = True
            _LOGGER.warning(f"Fallback triggered for medium {self.name}: {triggered_devices}")
        else:
            self.fallback_triggered = False
    
    def _trigger_condition(self, condition: str, value: Any) -> List[str]:
        """Trigger all devices matching the condition"""
        triggered = []
        for device_id, device in self.devices.items():
            if device.trigger_condition == condition:
                if device.trigger(value):
                    triggered.append(device.device_name)
        return triggered
    
    # Status checks
    def get_all_medium_values(self):
        mediumValues = {
            "Name":f"{self.room} - Medium: {self.name.upper()} Info",
            "medium":True,
            "room":self.room,
            "medium_type": self.medium_type.value,
            "medium_ec": self.current_ec,
            "medium_ph": self.current_ph,
            "medium_moisture": self.current_moisture,
            "medium_light": self.current_light if hasattr(self, 'current_light') else None,
            "medium_temp": self.current_temp,
            "medium_sensors_total": len(self.registered_sensors),
            "medium_sensors": self.registered_sensors,
            "timestamp": datetime.now()
        }
        logging.debug(f"{self.room} Current Medium Values: {mediumValues}") 
        return mediumValues
    
    def is_ph_optimal(self, ph_value: Optional[float] = None) -> bool:
        """Check if pH is in optimal range"""
        value = ph_value if ph_value is not None else self.current_ph
        if value is None:
            return False
        # Ensure value is float
        numeric_value = self._safe_float_convert(value)
        if numeric_value is None:
            return False
        min_ph, max_ph = self.properties.ph_range
        return min_ph <= numeric_value <= max_ph
    
    def is_ec_optimal(self, ec_value: Optional[float] = None) -> bool:
        """Check if EC is in optimal range"""
        value = ec_value if ec_value is not None else self.current_ec
        if value is None:
            return False
        # Ensure value is float
        numeric_value = self._safe_float_convert(value)
        if numeric_value is None:
            return False
        min_ec, max_ec = self.properties.ec_range
        return min_ec <= numeric_value <= max_ec
    
    def get_watering_interval_hours(self) -> float:
        """Get recommended watering interval"""
        return self.properties.watering_frequency
    
    def get_status(self) -> Dict[str, Any]:
        """Get current medium status"""
        return {
            "name": self.name,
            "type": self.medium_type.value,
            "sensor_count": sum(len(sensors) for sensors in self.registered_sensors.values()),
            "sensor_types": list(self.registered_sensors.keys()),
            "current_readings": self.sensor_readings.copy(),
            "last_updates": {
                k: v.isoformat() if v else None 
                for k, v in self.last_reading_time.items()
            },
            "fallback_enabled": self.fallback_enabled,
            "fallback_triggered": self.fallback_triggered,
            "optimal_status": {
                "ph": self.is_ph_optimal(),
                "ec": self.is_ec_optimal(),
            },
            "bound_devices": len(self.devices),
            "active_devices": sum(1 for d in self.devices.values() if d.is_active)
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert medium to dictionary"""
        # Sichere Handhabung von last_reading_time
        safe_last_reading_time = {}
        for k, v in self.last_reading_time.items():
            if v:
                safe_last_reading_time[k] = v.isoformat()
        
        return {
            "type": self.medium_type.value,
            "name": self.name,
            "volume_liters": self.volume_liters,
            "registered_sensors": self.registered_sensors,
            "sensor_readings": self.sensor_readings,
            "last_reading_time": safe_last_reading_time,
            "created_at": self.created_at.isoformat(),
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
            "status": self.get_status(),
            "custom_attributes": self.custom_attributes
        }
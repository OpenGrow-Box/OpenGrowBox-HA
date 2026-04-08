import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

_LOGGER = logging.getLogger(__name__)


class OGBReservoirManager:
    """
    Manager for monitoring reservoir water levels and alerting on critical levels.
    
    Monitors:
    - Ultrasonic sensor for water level (percentage or distance)
    - Alerts when level drops below 25% (critical low)
    - Alerts when level exceeds 85% (critical high / potential overflow)
    
    Notifications are rate-limited to avoid spam.
    """
    
    def __init__(self, hass, data_store, event_manager, room: str, notificator=None):
        self.name = "OGB Reservoir Manager"
        self.hass = hass
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager
        self.notificator = notificator
        
        # Level thresholds (in percentage)
        self.low_threshold = 25.0  # Alert when below 25%
        self.high_threshold = 85.0  # Alert when above 85%
        
        # Current level tracking
        self.current_level: Optional[float] = None
        self.current_level_raw: Optional[float] = None  # Raw sensor value
        self.level_unit: str = "%"  # Default unit is percentage
        
        # State tracking
        self.last_alert_time: Optional[datetime] = None
        self.last_alert_type: Optional[str] = None  # "low" or "high"
        self.alert_cooldown = timedelta(minutes=30)  # Minimum time between alerts
        
        # Sensor entity tracking
        self.reservoir_sensor_entity: Optional[str] = None
        
        # Register event handlers
        self.event_manager.on("ReservoirLevelUpdate", self._handle_level_update)
        self.event_manager.on("SensorUpdate", self._check_sensor_update)
        
        _LOGGER.info(f"[{self.room}] OGB Reservoir Manager initialized")
    
    async def init(self):
        """Initialize and find reservoir sensor"""
        await self._find_reservoir_sensor()
    
    async def _find_reservoir_sensor(self):
        """Find reservoir ultrasonic sensor entity"""
        try:
            # Look for entities with reservoir or ultrasonic in name
            states = self.hass.states.async_all()
            for state in states:
                entity_id = state.entity_id
                if (
                    "reservoir" in entity_id.lower() 
                    or "ultrasonic" in entity_id.lower()
                ) and "sensor" in entity_id:
                    self.reservoir_sensor_entity = entity_id
                    _LOGGER.info(f"[{self.room}] Found reservoir sensor: {entity_id}")
                    
                    # Get initial value - create OGBEventPublication object
                    from ...data.OGBDataClasses.OGBPublications import OGBEventPublication
                    event_data = OGBEventPublication(
                        Name=entity_id,
                        oldState=[],
                        newState=[state.state] if state.state else []
                    )
                    await self._handle_level_update(event_data)
                    break
            
            if not self.reservoir_sensor_entity:
                _LOGGER.warning(f"[{self.room}] No reservoir sensor found")
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error finding reservoir sensor: {e}")
    
    async def _check_sensor_update(self, data):
        """Check if updated sensor is our reservoir sensor"""
        try:
            # Handle OGBEventPublication object (Name, newState, oldState)
            entity_id = getattr(data, 'Name', '')
            if (
                "reservoir" in entity_id.lower() 
                or "ultrasonic" in entity_id.lower()
            ):
                await self._handle_level_update(data)
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error checking sensor update: {e}")
    
    async def _handle_level_update(self, data):
        """Handle reservoir level update from sensor"""
        try:
            # Handle OGBEventPublication object
            entity_id = getattr(data, 'Name', 'unknown')
            new_state_list = getattr(data, 'newState', [])
            state = new_state_list[0] if new_state_list else None
            
            # Skip invalid values
            if state in [None, "unknown", "unavailable", "Unbekannt"]:
                _LOGGER.debug(f"[{self.room}] Skipping invalid reservoir level: {state}")
                return
            
            # Parse level value
            try:
                raw_value = float(state)
            except (ValueError, TypeError):
                _LOGGER.warning(f"[{self.room}] Cannot parse reservoir level: {state}")
                return
            
            self.current_level_raw = raw_value
            
            # Get attributes from Home Assistant state
            try:
                state_obj = self.hass.states.get(entity_id)
                if state_obj:
                    attributes = state_obj.attributes or {}
                else:
                    attributes = {}
            except Exception:
                attributes = {}
            
            # Determine unit and convert to percentage if needed
            unit = attributes.get("unit_of_measurement", "%")
            self.level_unit = unit
            
            if unit == "%":
                # Already percentage
                self.current_level = raw_value
            elif unit in ["cm", "m", "mm", "m"]:
                # Distance measurement - need to convert
                # Assume sensor is mounted at top, measuring distance to water surface
                # This requires calibration (max distance = empty, min distance = full)
                self.current_level = await self._convert_distance_to_percentage(
                    raw_value, unit, attributes
                )
            else:
                # Unknown unit, use raw value as-is
                self.current_level = raw_value
                _LOGGER.warning(f"[{self.room}] Unknown reservoir level unit: {unit}")
            
            # Store in dataStore
            self.data_store.setDeep("Hydro.ReservoirLevel", self.current_level)
            self.data_store.setDeep("Hydro.ReservoirLevelRaw", self.current_level_raw)
            self.data_store.setDeep("Hydro.ReservoirLastUpdate", datetime.now().isoformat())
            
            _LOGGER.debug(
                f"[{self.room}] Reservoir level: {self.current_level:.1f}% "
                f"(raw: {self.current_level_raw} {unit}) - Saved to dataStore"
            )
            
            # Check thresholds and alert if needed
            await self._check_thresholds()
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling level update: {e}")
    
    async def _convert_distance_to_percentage(
        self, distance: float, unit: str, attributes: Dict[str, Any]
    ) -> float:
        """Convert distance measurement to percentage full"""
        try:
            # Get calibration values from attributes or dataStore
            max_distance = attributes.get("max_distance") or self.data_store.getDeep(
                "Hydro.ReservoirMaxDistance", 100.0
            )
            min_distance = attributes.get("min_distance") or self.data_store.getDeep(
                "Hydro.ReservoirMinDistance", 10.0
            )
            
            # Convert to cm if needed
            if unit == "m":
                distance = distance * 100
                max_distance = max_distance * 100
                min_distance = min_distance * 100
            elif unit == "mm":
                distance = distance / 10
                max_distance = max_distance / 10
                min_distance = min_distance / 10
            
            # Calculate percentage
            # Distance decreases as water level rises
            if max_distance <= min_distance:
                return 50.0  # Fallback
            
            percentage = ((max_distance - distance) / (max_distance - min_distance)) * 100
            percentage = max(0.0, min(100.0, percentage))  # Clamp to 0-100
            
            return percentage
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error converting distance: {e}")
            return 50.0  # Fallback
    
    async def _check_thresholds(self):
        """Check if current level triggers alerts"""
        if self.current_level is None:
            return
        
        now = datetime.now()
        
        # Check if enough time passed since last alert
        if self.last_alert_time and (now - self.last_alert_time) < self.alert_cooldown:
            return
        
        # Check low threshold
        if self.current_level < self.low_threshold:
            if self.last_alert_type != "low":
                await self._send_low_level_alert()
                self.last_alert_time = now
                self.last_alert_type = "low"
        
        # Check high threshold
        elif self.current_level > self.high_threshold:
            if self.last_alert_type != "high":
                await self._send_high_level_alert()
                self.last_alert_time = now
                self.last_alert_type = "high"
        
        else:
            # Level is back in normal range, reset alert type
            if self.last_alert_type is not None:
                _LOGGER.info(
                    f"[{self.room}] Reservoir level back to normal: "
                    f"{self.current_level:.1f}%"
                )
                self._log_to_client(
                    f"Reservoir level normal: {self.current_level:.1f}%",
                    "INFO"
                )
                self.last_alert_type = None
    
    async def _send_low_level_alert(self):
        """Send critical low level alert"""
        message = (
            f"🚨 CRITICAL: Reservoir level is LOW ({self.current_level:.1f}%). "
            f"Fill up immediately to prevent pump damage. "
            f"Threshold: {self.low_threshold}%"
        )
        
        _LOGGER.critical(f"[{self.room}] {message}")
        
        # Send notification
        if self.notificator:
            await self.notificator.critical(
                message=message,
                title=f"OGB {self.room}: Reservoir Critical Low"
            )
        
        # Log to client
        self._log_to_client(
            f"Reservoir level CRITICAL LOW: {self.current_level:.1f}%",
            "ERROR",
            {"level": self.current_level, "threshold": self.low_threshold}
        )
    
    async def _send_high_level_alert(self):
        """Send critical high level alert"""
        message = (
            f"⚠️ WARNING: Reservoir level is HIGH ({self.current_level:.1f}%). "
            f"Risk of overflow. Check fill system. "
            f"Threshold: {self.high_threshold}%"
        )
        
        _LOGGER.warning(f"[{self.room}] {message}")
        
        # Send notification
        if self.notificator:
            await self.notificator.warning(
                message=message,
                title=f"OGB {self.room}: Reservoir High Level"
            )
        
        # Log to client
        self._log_to_client(
            f"Reservoir level HIGH: {self.current_level:.1f}%",
            "WARNING",
            {"level": self.current_level, "threshold": self.high_threshold}
        )
    
    def _log_to_client(self, message: str, log_type: str = "INFO", extra_data: dict = None):
        """Send clean log message to client via LogForClient"""
        try:
            log_data = {
                "Name": self.room,
                "Type": "RESERVOIR",
                "Message": message,
            }
            if extra_data:
                log_data.update(extra_data)
            
            # Use asyncio.create_task to not block
            import asyncio
            asyncio.create_task(
                self.event_manager.emit("LogForClient", log_data, haEvent=True, debug_type=log_type)
            )
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error logging to client: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current reservoir status"""
        return {
            "level_percentage": self.current_level,
            "level_raw": self.current_level_raw,
            "unit": self.level_unit,
            "low_threshold": self.low_threshold,
            "high_threshold": self.high_threshold,
            "last_alert_type": self.last_alert_type,
            "last_alert_time": self.last_alert_time.isoformat() if self.last_alert_time else None,
            "sensor_entity": self.reservoir_sensor_entity,
        }
    
    async def set_thresholds(self, low: float = None, high: float = None):
        """Update alert thresholds"""
        if low is not None:
            self.low_threshold = max(0.0, min(50.0, low))
            _LOGGER.info(f"[{self.room}] Low threshold updated to {self.low_threshold}%")
        
        if high is not None:
            self.high_threshold = max(50.0, min(100.0, high))
            _LOGGER.info(f"[{self.room}] High threshold updated to {self.high_threshold}%")
        
        # Re-check current level with new thresholds
        await self._check_thresholds()

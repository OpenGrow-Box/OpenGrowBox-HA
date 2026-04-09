import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

_LOGGER = logging.getLogger(__name__)


class OGBReservoirManager:
    """
    Manager for monitoring reservoir water levels and automatic refilling.
    
    Monitors:
    - Ultrasonic sensor for water level (percentage or distance)
    - Alerts when level drops below configured minimum (default: 25%)
    - Alerts when level exceeds configured maximum (default: 85%)
    - Automatic refill in 5% steps when below minimum
    
    User-configurable thresholds via OGB_Feed_Reservoir_Min/Max numbers.
    
    Auto-Fill Logic:
    - Trigger: Level drops to configured minimum (default: 25%)
    - Target: Fill to configured maximum (default: 85%)
    - Step size: 5% per cycle
    - Pump duration: Max 5 minutes per cycle
    - Safety: Stop early if 5% reached before 5 minutes
    - Monitoring: Live sensor updates during filling
    - Error handling: Block on 2 consecutive sensor errors
    
    Notifications are rate-limited to avoid spam.
    """
    
    def __init__(self, hass, data_store, event_manager, room: str, notificator=None):
        self.name = "OGB Reservoir Manager"
        self.hass = hass
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager
        self.notificator = notificator
        
        # Level thresholds (in percentage) - read from dataStore with defaults
        self._default_low = 25.0
        self._default_high = 85.0
        self._default_max_fill = 85.0
        self.fill_step_size = 5.0  # 5% per fill cycle
        
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
        
        # Auto-fill tracking
        self._is_filling = False
        self._fill_cycles_completed = 0
        self._fill_start_level = None
        self._fill_start_time = None
        self._last_fill_cycle_time = None
        self._consecutive_sensor_errors = 0
        self._max_sensor_errors = 2  # Block after 2 errors
        self._fill_blocked = False  # Permanent block on critical error
        self._fill_block_reason: Optional[str] = None
        self._last_feed_mode: Optional[str] = None
        self._expected_pump_state: Optional[str] = None
        
        # Pump tracking
        self.reservoir_pump_entity: Optional[str] = None
        
        # Register event handlers
        self.event_manager.on("ReservoirLevelUpdate", self._handle_level_update)
        self.event_manager.on("SensorUpdate", self._check_sensor_update)
        self.event_manager.on("ReservoirLevelChange", self._handle_level_config_change)
        self.event_manager.on("FeedModeChange", self._handle_feed_mode_change)
        
        _LOGGER.info(f"[{self.room}] OGB Reservoir Manager initialized")
    
    @property
    def low_threshold(self) -> float:
        """Get low threshold from dataStore or default"""
        default = getattr(self, '_default_low', 25.0)
        value = self.data_store.getDeep("Hydro.ReservoirMinLevel", default)
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @low_threshold.setter
    def low_threshold(self, value: float):
        """Set low threshold for compatibility with tests/direct overrides."""
        coerced = float(value)
        self._default_low = coerced
        if hasattr(self, "data_store") and self.data_store is not None:
            self.data_store.setDeep("Hydro.ReservoirMinLevel", coerced)
    
    @property
    def high_threshold(self) -> float:
        """Get high threshold from dataStore or default"""
        default = getattr(self, '_default_high', 85.0)
        value = self.data_store.getDeep("Hydro.ReservoirMaxLevel", default)
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @high_threshold.setter
    def high_threshold(self, value: float):
        """Set high threshold for compatibility with tests/direct overrides."""
        coerced = float(value)
        self._default_high = coerced
        self._default_max_fill = coerced
        if hasattr(self, "data_store") and self.data_store is not None:
            self.data_store.setDeep("Hydro.ReservoirMaxLevel", coerced)
    
    @property
    def max_fill_level(self) -> float:
        """Get max fill level from dataStore or default"""
        default = getattr(self, '_default_max_fill', 85.0)
        value = self.data_store.getDeep("Hydro.ReservoirMaxLevel", default)
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    async def init(self):
        """Initialize and find reservoir sensor and pump"""
        await self._sync_thresholds_from_ha_numbers()
        
        await self._find_reservoir_sensor()
        await self._find_reservoir_pump()

        if not self.reservoir_sensor_entity or not self.reservoir_pump_entity:
            asyncio.create_task(self._retry_discovery_after_delay())

    async def _retry_discovery_after_delay(self, delay_seconds: int = 15):
        """Retry discovery after device manager finished registering devices."""
        try:
            await asyncio.sleep(delay_seconds)
            await self._sync_thresholds_from_ha_numbers()
            await self._find_reservoir_sensor(log_missing=False)
            await self._find_reservoir_pump(log_missing=False)
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error during delayed reservoir discovery retry: {e}")

    async def _sync_thresholds_from_ha_numbers(self):
        """Load reservoir thresholds from HA number entities without persisting defaults."""
        try:
            room_slug = str(self.room).strip().lower().replace(" ", "_")
            min_entity_id = f"number.ogb_feed_reservoir_min_{room_slug}"
            max_entity_id = f"number.ogb_feed_reservoir_max_{room_slug}"

            min_state = self.hass.states.get(min_entity_id)
            if min_state and min_state.state not in [None, "unknown", "unavailable", ""]:
                self.data_store.setDeep("Hydro.ReservoirMinLevel", float(min_state.state))

            max_state = self.hass.states.get(max_entity_id)
            if max_state and max_state.state not in [None, "unknown", "unavailable", ""]:
                self.data_store.setDeep("Hydro.ReservoirMaxLevel", float(max_state.state))
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Could not sync reservoir thresholds from HA numbers: {e}")

    def _entity_looks_like_reservoir_level_sensor(self, entity_id: str) -> bool:
        """Return True only for reservoir level/distance sensors, not temperature sensors."""
        lowered = entity_id.lower()
        if "sensor." not in lowered:
            return False
        if not ("reservoir" in lowered or "ultrasonic" in lowered):
            return False
        if any(blocked in lowered for blocked in ["temperature", "temp", "humidity"]):
            return False
        return any(keyword in lowered for keyword in ["level", "distance", "ultrasonic"])
    
    async def _find_reservoir_sensor(self, log_missing: bool = True):
        """Find reservoir ultrasonic sensor entity"""
        try:
            await self._sync_thresholds_from_ha_numbers()
            # Look for entities with reservoir or ultrasonic in name
            states = self.hass.states.async_all()
            for state in states:
                entity_id = state.entity_id
                if self._entity_looks_like_reservoir_level_sensor(entity_id):
                    self.reservoir_sensor_entity = entity_id
                    _LOGGER.info(f"[{self.room}] Found reservoir sensor: {entity_id}")
                    
                    # Get initial value - create OGBEventPublication object
                    from ....data.OGBDataClasses.OGBPublications import OGBEventPublication
                    event_data = OGBEventPublication(
                        Name=entity_id,
                        oldState=[],
                        newState=[state.state] if state.state else []
                    )
                    await self._handle_level_update(event_data)
                    break
            
            if not self.reservoir_sensor_entity and log_missing:
                _LOGGER.warning(f"[{self.room}] No reservoir sensor found")
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error finding reservoir sensor: {e}")
    
    async def _find_reservoir_pump(self, log_missing: bool = True):
        """Find reservoir fill pump entity via capability-based discovery"""
        try:
            # Use capability-based discovery (like modes do)
            pump_capability = self.data_store.getDeep("capabilities.canReservoirFill")
            
            if pump_capability and pump_capability.get("state"):
                dev_entities = pump_capability.get("devEntities", [])
                if dev_entities:
                    # Get the first available pump entity
                    self.reservoir_pump_entity = dev_entities[0]
                    _LOGGER.info(
                        f"[{self.room}] Found reservoir pump via capability: {self.reservoir_pump_entity}"
                    )
                    return
            
            # Fallback: Check OGB devices directly
            devices = self.data_store.getDeep("devices", [])
            for device in devices:
                # Handle both dict and Device object
                if isinstance(device, dict):
                    device_type = device.get("deviceType", "").lower()
                    entities = device.get("entities", [])
                    device_labels = device.get("labels", [])
                else:
                    # Device object
                    device_type = getattr(device, "deviceType", "").lower()
                    entities = getattr(device, "entities", [])
                    device_labels = getattr(device, "labels", [])
                
                # Check if device type is ReservoirPump
                if device_type == "reservoirpump":
                    if entities:
                        if isinstance(entities[0], dict):
                            self.reservoir_pump_entity = entities[0].get("entity_id")
                        else:
                            self.reservoir_pump_entity = str(entities[0])
                        _LOGGER.info(
                            f"[{self.room}] Found reservoir pump by device type: {self.reservoir_pump_entity}"
                        )
                        return
                
                # Check labels
                for lbl in device_labels:
                    if isinstance(lbl, dict):
                        label_name = lbl.get("name", "").lower()
                    else:
                        label_name = str(lbl).lower()
                    
                    if label_name in ["reservoir_pump", "reservoirpump", "tank_fill", "fill_pump", "reservoir_fill", "water_fill"]:
                        if entities:
                            if isinstance(entities[0], dict):
                                self.reservoir_pump_entity = entities[0].get("entity_id")
                            else:
                                self.reservoir_pump_entity = str(entities[0])
                            _LOGGER.info(
                                f"[{self.room}] Found reservoir pump by label '{label_name}': {self.reservoir_pump_entity}"
                            )
                            return
            
            # Last resort: Fallback to HA entity search
            states = self.hass.states.async_all()
            for state in states:
                entity_id = state.entity_id
                
                # Look for switch/pump with reservoir keywords
                if "switch" in entity_id or "pump" in entity_id:
                    if any(keyword in entity_id.lower() for keyword in 
                           ['reservoir_pump', 'reservoirpump', 'tank_fill', 'fill_pump', 'reservoir_fill', 'water_fill']):
                        self.reservoir_pump_entity = entity_id
                        _LOGGER.info(
                            f"[{self.room}] Found reservoir pump by entity_id fallback: {entity_id}"
                        )
                        return
            
            if not self.reservoir_pump_entity and log_missing:
                _LOGGER.warning(f"[{self.room}] No reservoir pump found - auto-fill disabled")
                _LOGGER.info(
                    f"[{self.room}] To enable auto-fill, add a device with label 'reservoir_pump' "
                    f"or deviceType 'ReservoirPump'"
                )
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error finding reservoir pump: {e}")
    
    async def _check_sensor_update(self, data):
        """Check if updated sensor is our reservoir sensor"""
        try:
            entity_id, state = self._extract_entity_state(data)

            if entity_id and self.reservoir_pump_entity and entity_id == self.reservoir_pump_entity:
                await self._handle_pump_state_update(state)
                return

            if not self.reservoir_sensor_entity and self._entity_looks_like_reservoir_level_sensor(entity_id):
                self.reservoir_sensor_entity = entity_id
                _LOGGER.info(f"[{self.room}] Reservoir sensor discovered lazily: {entity_id}")

            if self.reservoir_sensor_entity and entity_id != self.reservoir_sensor_entity:
                return
            
            # STRICT FILTERING: Only accept actual sensor entities
            # Skip switches, pumps, and other non-sensor entities
            if not entity_id or 'sensor.' not in entity_id.lower():
                return
            
            # Check if it's a reservoir-related sensor
            if self._entity_looks_like_reservoir_level_sensor(entity_id):
                await self._handle_level_update(data)
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error checking sensor update: {e}")

    def _extract_entity_state(self, data):
        """Extract entity id and state from event payload."""
        if isinstance(data, dict):
            return data.get("entity_id", ""), data.get("state")

        entity_id = getattr(data, "Name", "")
        new_state_list = getattr(data, "newState", [])
        state = new_state_list[0] if new_state_list else None
        return entity_id, state

    async def _handle_pump_state_update(self, state):
        """Handle reservoir pump state updates and block unsafe behavior."""
        if state not in ["on", "off", "ON", "OFF"]:
            return

        normalized_state = str(state).lower()

        if self._expected_pump_state == normalized_state:
            _LOGGER.debug(
                f"[{self.room}] Reservoir pump reached expected state: {normalized_state}"
            )
            self._expected_pump_state = None
            return

        if normalized_state == "on" or self._is_filling:
            reason = (
                f"Unexpected pump state '{normalized_state}' detected on "
                f"{self.reservoir_pump_entity}"
            )
            await self._block_fill(reason)

    async def _handle_feed_mode_change(self, feed_mode):
        """Reset latched reservoir block only after manual mode toggle back to automatic."""
        mode_value = str(feed_mode)

        if self._fill_blocked:
            if mode_value in ["Disabled", "Config"]:
                self._last_feed_mode = mode_value
            elif mode_value == "Automatic" and self._last_feed_mode in ["Disabled", "Config"]:
                old_reason = self._fill_block_reason or "manual reset"
                self._fill_blocked = False
                self._fill_block_reason = None
                self._consecutive_sensor_errors = 0
                self._expected_pump_state = None
                self._last_feed_mode = mode_value
                await self._notify_user(
                    "Reservoir-Autofill reset",
                    f"Autofill block cleared after mode toggle. Previous block: {old_reason}",
                    level="warning",
                )
                _LOGGER.warning(
                    f"[{self.room}] Reservoir autofill block cleared after mode toggle: {old_reason}"
                )
                return

        self._last_feed_mode = mode_value
    
    async def _handle_level_update(self, data):
        """Handle reservoir level update from sensor"""
        try:
            # Handle both OGBEventPublication object and dictionary
            if isinstance(data, dict):
                # Dictionary format (from tests or direct calls)
                entity_id = data.get('entity_id', 'unknown')
                state = data.get('state')
                attributes = data.get('attributes', {})
            else:
                # OGBEventPublication object format
                entity_id = getattr(data, 'Name', 'unknown')
                new_state_list = getattr(data, 'newState', [])
                state = new_state_list[0] if new_state_list else None
                
                # Get attributes from Home Assistant state
                try:
                    state_obj = self.hass.states.get(entity_id)
                    if state_obj:
                        attributes = state_obj.attributes or {}
                    else:
                        attributes = {}
                except Exception:
                    attributes = {}
            
            # STRICT FILTERING: Skip non-sensor entities
            if 'sensor.' not in entity_id.lower():
                _LOGGER.debug(f"[{self.room}] Skipping non-sensor entity: {entity_id}")
                return

            if self.reservoir_sensor_entity and entity_id != self.reservoir_sensor_entity:
                _LOGGER.debug(
                    f"[{self.room}] Skipping non-reservoir-level sensor: {entity_id}"
                )
                return
            
            # Skip switch states (on/off) - these are pump states, not level readings
            if state in ['on', 'off', 'ON', 'OFF']:
                _LOGGER.debug(f"[{self.room}] Skipping switch state '{state}' from {entity_id}")
                return

            if self.reservoir_sensor_entity is None and not any(
                keyword in entity_id.lower()
                for keyword in ["reservoir", "ultrasonic", "level", "distance"]
            ):
                _LOGGER.debug(
                    f"[{self.room}] Skipping unrelated sensor: {entity_id}"
                )
                return
            
            # Skip invalid values
            if state in [None, "unknown", "unavailable", "Unbekannt", ""]:
                _LOGGER.debug(f"[{self.room}] Skipping invalid reservoir level: {state}")
                return
            
            # Parse level value
            try:
                raw_value = float(state)
            except (ValueError, TypeError):
                _LOGGER.warning(f"[{self.room}] Cannot parse reservoir level: {state} (entity: {entity_id})")
                return
            
            self.current_level_raw = raw_value
            
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
    
    async def _handle_level_config_change(self, data):
        """Handle reservoir level configuration changes"""
        try:
            if not self.reservoir_sensor_entity:
                await self._find_reservoir_sensor(log_missing=False)
            if not self.reservoir_pump_entity:
                await self._find_reservoir_pump(log_missing=False)

            if isinstance(data, dict):
                change_type = data.get('type')
                value = data.get('value')
                
                if change_type == 'min_level':
                    _LOGGER.info(f"[{self.room}] Reservoir min level config changed to {value}%")
                    # Re-check thresholds with new value
                    await self._check_thresholds()
                elif change_type == 'max_level':
                    _LOGGER.info(f"[{self.room}] Reservoir max level config changed to {value}%")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling level config change: {e}")
    
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
        """Check if current level triggers alerts or auto-fill"""
        if self.current_level is None:
            return
        
        now = datetime.now()
        
        # Check if enough time passed since last alert
        if self.last_alert_time and (now - self.last_alert_time) < self.alert_cooldown:
            return
        
        # Get current thresholds from dataStore
        low_thresh = self.low_threshold
        high_thresh = self.high_threshold
        
        # Check low threshold - trigger auto-fill
        if self.current_level < low_thresh:
            if self.last_alert_type != "low":
                await self._send_low_level_alert()
                self.last_alert_time = now
                self.last_alert_type = "low"
                
                # Start auto-fill if not already filling and not blocked
                if not self._is_filling and not self._fill_blocked:
                    asyncio.create_task(self._auto_fill_reservoir())
        
        # Check high threshold
        elif self.current_level > high_thresh:
            if self.last_alert_type != "high":
                await self._send_high_level_alert()
                self.last_alert_time = now
                self.last_alert_type = "high"
                
                # Stop filling if we reached target
                if self._is_filling:
                    await self._stop_fill("Target level reached")
        
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
        # Get current threshold value at notification time
        current_threshold = self.low_threshold
        
        message = (
            f"🚨 CRITICAL: Reservoir level is LOW ({self.current_level:.1f}%). "
            f"Fill up immediately to prevent pump damage. "
            f"Threshold: {current_threshold:.1f}%"
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
            f"Reservoir level CRITICAL LOW: {self.current_level:.1f}% (threshold: {current_threshold:.1f}%)",
            "ERROR",
            {"level": self.current_level, "threshold": current_threshold}
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

    async def _block_fill(self, reason: str):
        """Latch autofill block until user resets via mode toggle."""
        if self._fill_blocked and self._fill_block_reason == reason:
            return

        self._fill_blocked = True
        self._fill_block_reason = reason

        if self._is_filling:
            await self._stop_fill(reason)

        await self._notify_user(
            "Reservoir-Autofill blockiert",
            (
                f"Autofill gestoppt: {reason}. "
                "Zum Entsperren einmal von Automatic auf Config oder Disabled und zurueck auf Automatic wechseln."
            ),
            level="critical",
        )
        _LOGGER.critical(f"[{self.room}] Reservoir autofill blocked: {reason}")
    
    async def _auto_fill_reservoir(self):
        """
        Auto-fill reservoir in 5% steps when below configured minimum.
        
        Process:
        1. Start pump for max 5 minutes
        2. Monitor level increase during filling
        3. Stop early if 5% target reached before 5 minutes
        4. Wait 5 minutes for sensor to settle
        5. Repeat until configured maximum reached
        6. Block on 2 consecutive sensor errors
        """
        _LOGGER.info(f"[{self.room}] _auto_fill_reservoir() called - checking conditions...")
        
        if self._is_filling:
            _LOGGER.info(f"[{self.room}] Auto-fill already in progress, skipping")
            return
        
        if self._fill_blocked:
            _LOGGER.warning(f"[{self.room}] Auto-fill is blocked due to previous errors, skipping")
            if self._fill_block_reason:
                _LOGGER.warning(f"[{self.room}] Block reason: {self._fill_block_reason}")
            return
        
        # Try to find pump if not already found
        if not self.reservoir_pump_entity:
            _LOGGER.info(f"[{self.room}] No pump cached, attempting discovery...")
            await self._find_reservoir_pump()
        
        if not self.reservoir_pump_entity:
            _LOGGER.error(f"[{self.room}] Cannot auto-fill: No reservoir pump configured")
            _LOGGER.info(f"[{self.room}] Add a pump with label 'reservoir_pump' to enable auto-fill")
            return
        
        _LOGGER.info(f"[{self.room}] Starting auto-fill process with pump: {self.reservoir_pump_entity}")
        
        self._is_filling = True
        self._fill_cycles_completed = 0
        self._fill_start_level = self.current_level
        self._fill_start_time = datetime.now()
        self._consecutive_sensor_errors = 0
        
        target_level = self.max_fill_level
        
        _LOGGER.info(
            f"[{self.room}] Auto-fill initialized: {self.current_level:.1f}% → {target_level:.1f}%"
        )
        
        await self._notify_user(
            "🚨 Reservoir kritisch",
            (
                f"Fuellung gestartet: {self.current_level:.1f}% -> {target_level:.1f}%\n"
                f"Schrittgroesse: {self.fill_step_size:.1f}%\n"
                "Max. 5 Minuten pro Zyklus"
            ),
            level="warning",
        )
        
        try:
            while self._is_filling and self.current_level < target_level:
                # Check if blocked due to errors
                if self._fill_blocked:
                    _LOGGER.error(f"[{self.room}] Auto-fill blocked due to sensor errors")
                    await self._notify_user(
                        "❌ Füllung BLOCKIERT",
                        "Sensor-Fehler - Manuelle Ueberpruefung erforderlich",
                        level="critical",
                    )
                    break
                
                # Calculate target for this cycle
                cycle_target = min(
                    self.current_level + self.fill_step_size,
                    target_level
                )
                
                # Run one fill cycle
                success = await self._run_fill_cycle(cycle_target)
                
                if not success:
                    # Error occurred - check if we should block
                    self._consecutive_sensor_errors += 1
                    if self._consecutive_sensor_errors >= self._max_sensor_errors:
                        self._fill_blocked = True
                        await self._notify_user(
                            "❌ Füllung ABGEBROCHEN",
                            f"{self._max_sensor_errors} Sensor-Fehler - System blockiert",
                            level="critical",
                        )
                        break
                else:
                    self._consecutive_sensor_errors = 0
                    self._fill_cycles_completed += 1
                    next_cycle_target = min(
                        self.current_level + self.fill_step_size,
                        target_level,
                    )
                    progress_message = (
                        f"Aktuell: {self.current_level:.1f}% (+{self.current_level - self._fill_start_level:.1f}%)\n"
                        f"Ziel dieses Zyklus erreicht: {cycle_target:.1f}%"
                    )

                    if self.current_level < target_level:
                        progress_message += f"\nNaechstes Ziel: {next_cycle_target:.1f}%"
                    
                    # Notify user of progress
                    await self._notify_user(
                        f"⏳ Füllung läuft ({self._fill_cycles_completed})",
                        progress_message,
                        level="warning",
                    )
                
                # Wait 5 minutes before next cycle (if not done)
                if self._is_filling and self.current_level < target_level:
                    _LOGGER.debug(f"[{self.room}] Waiting 5 minutes before next fill cycle")
                    await asyncio.sleep(300)  # 5 minutes
                    
                    # Verify sensor is still working after wait
                    if self.current_level is None:
                        _LOGGER.error(f"[{self.room}] No sensor reading after wait period")
                        self._consecutive_sensor_errors += 1
                        if self._consecutive_sensor_errors >= self._max_sensor_errors:
                            self._fill_blocked = True
                            break
            
            # Fill complete or stopped
            if self.current_level >= target_level:
                duration = (datetime.now() - self._fill_start_time).total_seconds() / 60
                await self._notify_user(
                    "✅ Füllung ABGESCHLOSSEN",
                    f"Reservoir voll: {self.current_level:.1f}%\n"
                    f"Dauer: {duration:.0f} Minuten\n"
                    f"Zyklen: {self._fill_cycles_completed}",
                    level="warning",
                )
                _LOGGER.info(
                    f"[{self.room}] Auto-fill complete: {self._fill_start_level:.1f}% → "
                    f"{self.current_level:.1f}% in {self._fill_cycles_completed} cycles"
                )
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error during auto-fill: {e}")
            await self._notify_user(
                "❌ Füllung FEHLER",
                f"Unerwarteter Fehler: {str(e)}",
                level="critical",
            )
        finally:
            await self._stop_fill("Completed or error")
    
    async def _run_fill_cycle(self, target_level: float) -> bool:
        """
        Run one fill cycle: pump until 5% added or max 5 minutes.
        
        Returns:
            bool: True if successful, False on error
        """
        cycle_start_level = self.current_level
        cycle_start_time = datetime.now()
        max_pump_duration = 300  # 5 minutes in seconds
        
        _LOGGER.debug(
            f"[{self.room}] Fill cycle {self._fill_cycles_completed + 1}: "
            f"{cycle_start_level:.1f}% → {target_level:.1f}%"
        )
        
        try:
            # Start pump
            await self._activate_pump()
            
            # Monitor during pumping
            while True:
                elapsed = (datetime.now() - cycle_start_time).total_seconds()
                
                # Check timeout (5 minutes max)
                if elapsed >= max_pump_duration:
                    _LOGGER.info(f"[{self.room}] Fill cycle timeout after 5 minutes")
                    break
                
                # Check if target reached
                if self.current_level is not None:
                    added = self.current_level - cycle_start_level
                    
                    # Verify level is actually increasing
                    if added < -1:  # Allow 1% tolerance
                        # Level decreased - sensor error!
                        _LOGGER.error(
                            f"[{self.room}] Sensor error: Level decreased from "
                            f"{cycle_start_level:.1f}% to {self.current_level:.1f}% while pumping!"
                        )
                        await self._deactivate_pump()
                        return False
                    
                    # Check if we reached target
                    if self.current_level >= target_level:
                        _LOGGER.info(
                            f"[{self.room}] Fill cycle complete: "
                            f"+{added:.1f}% in {elapsed:.0f}s"
                        )
                        break
                else:
                    # No sensor reading
                    _LOGGER.warning(f"[{self.room}] No sensor reading during fill cycle")
                
                # Wait 10 seconds before next check
                await asyncio.sleep(10)
            
            # Stop pump
            await self._deactivate_pump()
            
            # Verify final reading
            if self.current_level is None:
                _LOGGER.error(f"[{self.room}] No sensor reading after fill cycle")
                return False
            
            actual_added = self.current_level - cycle_start_level
            if actual_added < 0:
                _LOGGER.error(
                    f"[{self.room}] Sensor error after cycle: Level decreased by {abs(actual_added):.1f}%"
                )
                return False
            
            _LOGGER.info(
                f"[{self.room}] Fill cycle {self._fill_cycles_completed + 1} complete: "
                f"{cycle_start_level:.1f}% → {self.current_level:.1f}% (+{actual_added:.1f}%)"
            )
            return True
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error in fill cycle: {e}")
            await self._deactivate_pump()
            return False
    
    async def _activate_pump(self):
        """Activate reservoir fill pump via event (like modes do)"""
        try:
            self._expected_pump_state = "on"
            # Use capability-based discovery to find pump
            pump_capability = self.data_store.getDeep("capabilities.canReservoirFill")
            
            if pump_capability and pump_capability.get("state"):
                dev_entities = pump_capability.get("devEntities", [])
                if dev_entities:
                    pump_entity = dev_entities[0]
                    
                    # Send event to pump device (like modes do)
                    await self.event_manager.emit(
                        "ReservoirFillAction",
                        {
                            "Name": self.room,
                            "Action": "on",
                            "Device": pump_entity,
                            "Cycle": True
                        }
                    )
                    _LOGGER.debug(f"[{self.room}] Reservoir pump activation event sent: {pump_entity}")
                    return
            
            # Fallback: Try stored entity
            if self.reservoir_pump_entity:
                await self.event_manager.emit(
                    "ReservoirFillAction",
                    {
                        "Name": self.room,
                        "Action": "on",
                        "Device": self.reservoir_pump_entity,
                        "Cycle": True
                    }
                )
                _LOGGER.debug(f"[{self.room}] Reservoir pump activation event sent (fallback): {self.reservoir_pump_entity}")
            else:
                _LOGGER.error(f"[{self.room}] No reservoir pump available to activate")
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to activate reservoir pump: {e}")
            raise
    
    async def _deactivate_pump(self):
        """Deactivate reservoir fill pump via event (like modes do)"""
        try:
            self._expected_pump_state = "off"
            # Use capability-based discovery to find pump
            pump_capability = self.data_store.getDeep("capabilities.canReservoirFill")
            
            if pump_capability and pump_capability.get("state"):
                dev_entities = pump_capability.get("devEntities", [])
                if dev_entities:
                    pump_entity = dev_entities[0]
                    
                    # Send event to pump device (like modes do)
                    await self.event_manager.emit(
                        "ReservoirFillAction",
                        {
                            "Name": self.room,
                            "Action": "off",
                            "Device": pump_entity,
                            "Cycle": True
                        }
                    )
                    _LOGGER.debug(f"[{self.room}] Reservoir pump deactivation event sent: {pump_entity}")
                    return
            
            # Fallback: Try stored entity
            if self.reservoir_pump_entity:
                await self.event_manager.emit(
                    "ReservoirFillAction",
                    {
                        "Name": self.room,
                        "Action": "off",
                        "Device": self.reservoir_pump_entity,
                        "Cycle": True
                    }
                )
                _LOGGER.debug(f"[{self.room}] Reservoir pump deactivation event sent (fallback): {self.reservoir_pump_entity}")
            else:
                _LOGGER.warning(f"[{self.room}] No reservoir pump available to deactivate")
                
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to deactivate reservoir pump: {e}")
    
    async def _stop_fill(self, reason: str):
        """Stop filling process"""
        if self._is_filling:
            _LOGGER.info(f"[{self.room}] Stopping auto-fill: {reason}")
            await self._deactivate_pump()
            self._is_filling = False
            self._last_fill_cycle_time = datetime.now()
            self._log_to_client(f"Autofill stopped: {reason}", "WARNING")
    
    async def _notify_user(self, title: str, message: str, level: str = "info"):
        """Send notification to user"""
        try:
            if self.notificator:
                notification_title = f"OGB {self.room}: {title}"
                if level == "critical":
                    await self.notificator.critical(message=message, title=notification_title)
                elif level == "warning":
                    await self.notificator.warning(message=message, title=notification_title)
                else:
                    await self.notificator.info(message=message, title=notification_title)
            
            # Also log to client
            self._log_to_client(
                f"{title}: {message}",
                "ERROR" if level == "critical" else "WARNING" if level == "warning" else "INFO"
            )
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Failed to notify user: {e}")
    
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
            "pump_entity": self.reservoir_pump_entity,
            "is_filling": self._is_filling,
            "fill_blocked": self._fill_blocked,
            "fill_cycles_completed": self._fill_cycles_completed,
        }
    
    async def set_thresholds(self, low: float = None, high: float = None):
        """Update alert thresholds"""
        if low is not None:
            self.data_store.setDeep("Hydro.ReservoirMinLevel", max(0.0, min(50.0, low)))
            _LOGGER.info(f"[{self.room}] Low threshold updated to {self.low_threshold}%")
        
        if high is not None:
            self.data_store.setDeep("Hydro.ReservoirMaxLevel", max(50.0, min(100.0, high)))
            _LOGGER.info(f"[{self.room}] High threshold updated to {self.high_threshold}%")
        
        # Re-check current level with new thresholds
        await self._check_thresholds()

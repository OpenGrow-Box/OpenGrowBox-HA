"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                      🛡️ OGB FALLBACK MANAGER 🛡️                             ║
║              Sensor & Device Health Monitoring and Alerts                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

This module monitors all sensors and devices for staleness (no updates) and
notifies users when issues are detected.

Features:
- Monitors sensor last_update timestamps
- Detects sensors/devices that haven't reported for 30+ minutes
- Sends critical alerts via notification manager
- Sends recovery notifications when sensors come back online
- Prevents notification spam with rate limiting
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Set

from ..utils.ambient import is_ambient_room

_LOGGER = logging.getLogger(__name__)


@dataclass
class MonitoredEntityState:
    """State tracking for a monitored entity (sensor or device)."""

    entity_id: str
    entity_type: str  # "sensor" or "device"
    sensor_type: Optional[str] = None  # temperature, humidity, etc.
    device_name: str = ""
    context: str = "unknown"  # air/water/soil/light or device label
    device_type: Optional[str] = None  # Exhaust, Light, etc. (for devices)
    device_ref: Any = None  # Reference to device object for turn_on/off
    last_update: datetime = field(default_factory=datetime.now)
    last_value: Any = None
    is_stale: bool = False
    stale_since: Optional[datetime] = None
    notification_sent: bool = False


@dataclass
class DeviceReliabilityState:
    """State tracking for device reliability monitoring."""

    device_name: str
    last_power_before_action: Optional[float] = None
    action_type: Optional[str] = None  # "on" or "off"
    retry_count: int = 0
    is_reliable: bool = True
    last_check_time: Optional[datetime] = None
    notification_sent: bool = False


class OGBFallBackManager:
    """
    Fallback Manager - monitors sensor and device health.

    Detects when sensors/devices stop reporting and alerts users.
    """

    # Configuration constants
    STALE_THRESHOLD_MINUTES = 30  # Global threshold
    CHECK_INTERVAL_SECONDS = 60  # Check every minute
    NOTIFICATION_COOLDOWN_MINUTES = 60  # Don't spam same sensor

    # Device Reliability constants
    RELIABILITY_CHECK_DELAY_SECONDS = 5  # Wait after turn_on/off before checking
    RELIABILITY_RETRY_INTERVAL_SECONDS = 15  # Wait between retries
    RELIABILITY_MAX_RETRIES = 3  # Max retry attempts
    RELIABILITY_POWER_OFF_THRESHOLD = 0.3  # Power must drop below 30% of previous
    RELIABILITY_POWER_ON_THRESHOLD_WATTS = 5  # Minimum power when "on"

    def __init__(self, hass, dataStore, eventManager, room, regListener, notificator):
        """
        Initialize the Fallback Manager.

        Args:
            hass: Home Assistant instance
            dataStore: OGB DataStore
            eventManager: OGB Event Manager
            room: Room name
            regListener: Registry Listener
            notificator: OGBNotificator instance
        """
        self.name = "OGB FallBack Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager
        self.regListener = regListener
        self.notificator = notificator

        # Skip for ambient room - no devices/sensors to monitor
        if is_ambient_room(self.room):
            _LOGGER.debug(f"{self.room}: FallBack Manager disabled - ambient room")
            return

        # State tracking
        self._monitored_entities: Dict[str, MonitoredEntityState] = {}
        self._stale_entities: Set[str] = set()
        self._last_notification: Dict[str, datetime] = {}

        # Device reliability tracking
        self._device_reliability: Dict[str, DeviceReliabilityState] = {}

        # Running-but-off device tracking (rate limiting)
        self._running_off_notifications: Dict[str, datetime] = {}
        self._running_off_notification_cooldown = timedelta(minutes=60)

        # Task management
        self._check_task: Optional[asyncio.Task] = None
        self._is_running = False
        self.is_initialized = False

        _LOGGER.debug(f"✅ {self.room} FallBack Manager initialized")

        # Setup event listeners
        self._setup_event_listeners()

    def _setup_event_listeners(self):
        """Setup event listeners for sensor/device updates."""
        # Sensor events
        self.event_manager.on("SensorUpdate", self._on_sensor_update)
        self.event_manager.on("SensorInitialized", self._on_sensor_initialized)

        # Device events
        self.event_manager.on("DeviceInitialized", self._on_device_initialized)
        self.event_manager.on("DeviceStateChange", self._on_device_state_change)
        self.event_manager.on("DeviceRemoved", self._on_entity_removed)

        _LOGGER.debug(f"{self.room} FallBack Manager event listeners registered")

    async def start_monitoring(self):
        """Start the periodic health check monitoring."""
        if self._is_running:
            _LOGGER.warning(f"{self.room} FallBack Manager already running")
            return

        self._is_running = True
        self.is_initialized = True

        if self._check_task is None or self._check_task.done():
            self._check_task = asyncio.create_task(self._monitoring_loop())
            _LOGGER.debug(f"🔍 {self.room} FallBack Manager monitoring started")

    async def stop_monitoring(self):
        """Stop the monitoring loop."""
        self._is_running = False

        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        _LOGGER.debug(f"🛑 {self.room} FallBack Manager monitoring stopped")

    async def _monitoring_loop(self):
        """Main monitoring loop - checks all entities periodically."""
        _LOGGER.debug(f"{self.room} FallBack Manager monitoring loop started")

        while self._is_running:
            try:
                await self._check_all_entities()
                await self._check_running_but_off_devices()
                await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                _LOGGER.debug(f"{self.room} Monitoring loop cancelled")
                break
            except Exception as e:
                _LOGGER.error(
                    f"❌ {self.room} Error in monitoring loop: {e}", exc_info=True
                )
                await asyncio.sleep(10)  # Brief pause on error

    async def _check_all_entities(self):
        """Check all monitored entities for staleness."""
        if not self._monitored_entities:
            return

        now = datetime.now()
        threshold = timedelta(minutes=self.STALE_THRESHOLD_MINUTES)

        stale_count = 0
        recovered_count = 0

        for entity_id, state in list(self._monitored_entities.items()):
            age = now - state.last_update
            was_stale = state.is_stale

            # Check if entity became stale
            if age > threshold:
                if not was_stale:
                    # Entity just became stale
                    state.is_stale = True
                    state.stale_since = now
                    self._stale_entities.add(entity_id)
                    await self._notify_entity_stale(state, age)
                    stale_count += 1
            else:
                # Check if entity recovered
                if was_stale:
                    state.is_stale = False
                    state.stale_since = None
                    state.notification_sent = False
                    self._stale_entities.discard(entity_id)
                    await self._notify_entity_recovered(state)
                    recovered_count += 1

        if stale_count > 0 or recovered_count > 0:
            _LOGGER.debug(
                f"{self.room} Health check: {stale_count} new stale, "
                f"{recovered_count} recovered, {len(self._stale_entities)} total stale"
            )

    async def _check_running_but_off_devices(self):
        """
        Check for devices that are registered as 'off' but still consuming power.
        This indicates a hardware defect or failed turn_off command.
        """
        now = datetime.now()

        _LOGGER.debug(f"{self.room}: FB _check_running_but_off_devices scanning {len(self._monitored_entities)} entities")

        for entity_id, state in list(self._monitored_entities.items()):
            if state.entity_type != "device":
                continue

            _LOGGER.debug(f"{self.room}: FB checking device '{state.device_name}' type={state.device_type} last_value={state.last_value} has_ref={state.device_ref is not None}")

            # Only check devices that are supposed to be off
            if str(state.last_value).lower() not in ["off", "false", "0", "none"]:
                _LOGGER.debug(f"{self.room}: FB skip '{state.device_name}' – last_value '{state.last_value}' is not off")
                continue

            # Check if device has a reference and power sensor
            if not state.device_ref:
                _LOGGER.debug(f"{self.room}: FB skip '{state.device_name}' – no device_ref")
                continue

            device_name = state.device_name or state.context
            if not device_name:
                _LOGGER.debug(f"{self.room}: FB skip entity {entity_id} – no device_name")
                continue

            power_sensor = self._find_power_sensor(device_name)
            _LOGGER.debug(f"{self.room}: FB power sensor lookup for '{device_name}': {power_sensor}")
            if not power_sensor:
                continue

            try:
                current_power = await self._get_power_value(power_sensor)
                _LOGGER.debug(f"{self.room}: FB '{device_name}' power via {power_sensor}: {current_power}W (threshold={self.RELIABILITY_POWER_ON_THRESHOLD_WATTS}W)")
                if current_power is None:
                    continue

                # If power is above threshold while supposed to be off → DEFECT!
                if current_power >= self.RELIABILITY_POWER_ON_THRESHOLD_WATTS:
                    _LOGGER.warning(
                        f"{self.room}: ⚠️ Device '{device_name}' is OFF but consuming "
                        f"{current_power}W - forcing turn_off!"
                    )

                    # Attempt to turn off
                    try:
                        await state.device_ref.turn_off()
                        _LOGGER.info(
                            f"{self.room}: Forced turn_off for '{device_name}' "
                            f"(was consuming {current_power}W while OFF)"
                        )
                    except Exception as e:
                        _LOGGER.error(
                            f"{self.room}: Failed to force turn_off for '{device_name}': {e}"
                        )

                    # Send notification (with rate limiting)
                    await self._notify_suspected_defect(device_name, current_power)
                else:
                    _LOGGER.debug(f"{self.room}: FB '{device_name}' power {current_power}W below threshold, OK")

            except Exception as e:
                _LOGGER.debug(
                    f"{self.room}: Error checking running-but-off state for "
                    f"'{device_name}': {e}"
                )

    async def _notify_suspected_defect(self, device_name: str, power: float):
        """
        Notify user about a suspected defective device.
        Rate-limited to avoid spam.
        """
        try:
            # Check cooldown
            last_notif = self._running_off_notifications.get(device_name)
            if last_notif:
                if datetime.now() - last_notif < self._running_off_notification_cooldown:
                    _LOGGER.debug(
                        f"{self.room}: Skipping defect notification for '{device_name}' "
                        f"(cooldown active)"
                    )
                    return

            message = (
                f"Suspected defective device identified:\n\n"
                f"Device: {device_name}\n"
                f"Room: {self.room}\n"
                f"Power consumption: {power}W\n\n"
                f"The device is registered as OFF but is still consuming power. "
                f"This indicates a hardware defect or a failed turn-off command.\n\n"
                f"A repeated turn-off command has just been sent. "
                f"Please check the device manually."
            )

            await self.notificator.warning(
                message=message,
                title=f"OGB {self.room}: Defective device detected - {device_name}",
            )

            self._running_off_notifications[device_name] = datetime.now()

            _LOGGER.warning(
                f"{self.room}: 🚨 Sent suspected defect notification for '{device_name}' "
                f"(consuming {power}W while OFF)"
            )

        except Exception as e:
            _LOGGER.error(
                f"{self.room}: Failed to send defect notification for '{device_name}': {e}"
            )

    # =================================================================
    # Event Handlers
    # =================================================================

    async def _on_sensor_update(self, event_data):
        """Handle sensor update event."""
        try:
            # Extract entity_id from OGBEventPublication
            entity_id = (
                event_data.Name
                if hasattr(event_data, "Name")
                else event_data.get("entity_id")
            )

            if not entity_id:
                return

            # Update tracking
            if entity_id in self._monitored_entities:
                state = self._monitored_entities[entity_id]
                state.last_update = datetime.now()

                # Update last value
                if hasattr(event_data, "newState"):
                    state.last_value = (
                        event_data.newState[0] if event_data.newState else None
                    )
                elif "value" in event_data:
                    state.last_value = event_data.get("value")

                _LOGGER.debug(f"{self.room} Updated tracking for sensor {entity_id}")

        except Exception as e:
            _LOGGER.error(f"Error handling sensor update: {e}", exc_info=True)

    async def _on_sensor_initialized(self, event_data):
        """Handle sensor initialization event."""
        try:
            entity_id = event_data.get("entity_id")
            sensor_type = event_data.get("sensor_type")
            device_name = event_data.get("device_name")
            context = event_data.get("context", "unknown")
            room = event_data.get("room")

            # Only monitor sensors from our room
            if room and room.lower() != self.room.lower():
                return

            if not entity_id:
                return

            # Register sensor for monitoring
            self._monitored_entities[entity_id] = MonitoredEntityState(
                entity_id=entity_id,
                entity_type="sensor",
                sensor_type=sensor_type,
                device_name=device_name,
                context=context,
                last_update=datetime.now(),
            )

            _LOGGER.debug(
                f"📊 {self.room} Registered sensor for monitoring: "
                f"{sensor_type} ({device_name}) - {entity_id}"
            )

        except Exception as e:
            _LOGGER.error(f"Error handling sensor initialization: {e}", exc_info=True)

    async def _on_device_initialized(self, event_data):
        """Handle device initialization event."""
        try:
            entity_id = event_data.get("entity_id")
            device_name = event_data.get("device_name")
            device_type = event_data.get("device_type")
            context = event_data.get("context", device_type)  # Use device_label if available, fallback to device_type
            room = event_data.get("room")

            # Only monitor devices from our room
            if room and room.lower() != self.room.lower():
                return

            if not entity_id:
                return

            # Register device for monitoring
            device_ref = event_data.get("device_ref")
            self._monitored_entities[entity_id] = MonitoredEntityState(
                entity_id=entity_id,
                entity_type="device",
                device_name=device_name,
                context=context,
                device_type=device_type,
                device_ref=device_ref,
                last_update=datetime.now(),
                last_value="off",
            )

            _LOGGER.debug(
                f"🔌 {self.room} Registered device for monitoring: "
                f"{device_name} (Type: {device_type}, Label: {context}) - {entity_id} ref={device_ref is not None}"
            )

        except Exception as e:
            _LOGGER.error(f"Error handling device initialization: {e}", exc_info=True)

    async def _on_device_state_change(self, event_data):
        """Handle device state change event."""
        try:
            entity_id = event_data.get("entity_id")

            if not entity_id:
                return

            # Update tracking
            if entity_id in self._monitored_entities:
                state = self._monitored_entities[entity_id]
                state.last_update = datetime.now()
                state.last_value = event_data.get("new_state")

                _LOGGER.debug(f"{self.room} Updated tracking for device {entity_id} → last_value={state.last_value}")
            else:
                _LOGGER.debug(f"{self.room} FB DeviceStateChange entity_id '{entity_id}' not in monitored (keys: {list(self._monitored_entities.keys())[:5]})")

        except Exception as e:
            _LOGGER.error(f"Error handling device state change: {e}", exc_info=True)

    async def _on_entity_removed(self, event_data):
        """Handle entity removal event."""
        try:
            entity_id = event_data.get("entity_id")

            if entity_id and entity_id in self._monitored_entities:
                del self._monitored_entities[entity_id]
                self._stale_entities.discard(entity_id)

                _LOGGER.debug(f"{self.room} Removed entity from monitoring: {entity_id}")

        except Exception as e:
            _LOGGER.error(f"Error handling entity removal: {e}", exc_info=True)

    # =================================================================
    # Device Reliability Methods
    # =================================================================

    async def validate_device_state(self, device_name: str, device_ref, expected_state: str):
        """
        Validate if device actually reached expected state after turn_on/off.
        If not, perform retrigger with notification.
        """
        try:
            # Get or create reliability state
            if device_name not in self._device_reliability:
                self._device_reliability[device_name] = DeviceReliabilityState(device_name=device_name)

            state = self._device_reliability[device_name]

            # Wait before checking
            await asyncio.sleep(self.RELIABILITY_CHECK_DELAY_SECONDS)

            # Find power sensor for device
            power_sensor = self._find_power_sensor(device_name)
            if not power_sensor:
                _LOGGER.debug(f"{self.room}: No power sensor for {device_name}, skipping validation")
                return True

            # Read current power
            current_power = await self._get_power_value(power_sensor)
            if current_power is None:
                _LOGGER.debug(f"{self.room}: Could not read power for {device_name}, skipping validation")
                return True

            # Validate based on expected state
            is_valid = False
            if expected_state == "off":
                # Power should have dropped significantly
                threshold = (state.last_power_before_action or 0) * self.RELIABILITY_POWER_OFF_THRESHOLD
                is_valid = current_power <= threshold or current_power < 2  # < 2W considered off
                if not is_valid:
                    _LOGGER.warning(
                        f"{self.room}: ⚠️ {device_name} OFF validation FAILED - "
                        f"Power: {current_power}W (threshold: {threshold:.1f}W)"
                    )
            elif expected_state == "on":
                # Power should have increased
                prev_power = state.last_power_before_action or 0
                is_valid = current_power > prev_power * 1.5 or current_power > self.RELIABILITY_POWER_ON_THRESHOLD_WATTS
                if not is_valid:
                    _LOGGER.warning(
                        f"{self.room}: ⚠️ {device_name} ON validation FAILED - "
                        f"Power: {current_power}W (expected > {prev_power * 1.5:.1f}W or > {self.RELIABILITY_POWER_ON_THRESHOLD_WATTS}W)"
                    )

            if is_valid:
                _LOGGER.debug(f"{self.room}: ✅ {device_name} {expected_state.upper()} validation passed ({current_power}W)")
                state.retry_count = 0
                state.is_reliable = True
                return True

            # Validation failed - attempt retrigger
            if state.retry_count < self.RELIABILITY_MAX_RETRIES:
                state.retry_count += 1
                _LOGGER.warning(
                    f"{self.room}: 🔄 Retriggering {device_name} ({state.retry_count}/{self.RELIABILITY_MAX_RETRIES})"
                )

                await self._execute_retrigger(device_ref, expected_state)

                # Wait and re-check
                await asyncio.sleep(self.RELIABILITY_RETRY_INTERVAL_SECONDS)
                return await self.validate_device_state(device_name, device_ref, expected_state)
            else:
                # Max retries reached - notify user
                _LOGGER.error(
                    f"{self.room}: ❌ {device_name} failed validation after {self.RELIABILITY_MAX_RETRIES} retries"
                )
                await self._notify_device_unreliable(device_name, expected_state, state.retry_count)
                state.is_reliable = False
                return False

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error validating {device_name}: {e}", exc_info=True)
            return True  # Assume OK on error to avoid blocking

    async def _execute_retrigger(self, device_ref, expected_state: str):
        """Execute retrigger sequence for unreliable device."""
        try:
            if expected_state == "off":
                # Turn on briefly then off again
                _LOGGER.debug(f"{self.room}: Retrigger OFF - turning ON then OFF")
                await device_ref.turn_on()
                await asyncio.sleep(3)
                await device_ref.turn_off()
            elif expected_state == "on":
                # Turn off briefly then on again
                _LOGGER.debug(f"{self.room}: Retrigger ON - turning OFF then ON")
                await device_ref.turn_off()
                await asyncio.sleep(3)
                await device_ref.turn_on()
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error during retrigger: {e}", exc_info=True)

    async def _notify_device_unreliable(self, device_name: str, expected_state: str, retry_count: int):
        """Notify user about unreliable device."""
        try:
            message = (
                f"Device '{device_name}' could not reliably turn {expected_state.upper()}.\n\n"
                f"Retries attempted: {retry_count}\n\n"
                f"The device likely has a hardware defect or API issue. "
                f"We temporarily bypassed it, but the device should be replaced."
            )

            await self.notificator.warning(
                message=message,
                title=f"OGB {self.room}: Device Reliability Issue",
            )

            _LOGGER.warning(f"{self.room}: 🚨 Sent reliability alert for {device_name}")

        except Exception as e:
            _LOGGER.error(f"{self.room}: Failed to send reliability notification: {e}")

    def _find_power_sensor(self, device_name: str) -> Optional[str]:
        """Find associated power sensor for device."""
        # Try common patterns
        patterns = [
            f"sensor.{device_name.lower()}_power",
            f"sensor.{device_name.lower()}_energy",
            f"sensor.{device_name.lower()}_watt",
        ]

        _LOGGER.debug(f"{self.room}: FB _find_power_sensor for '{device_name}' trying patterns: {patterns}")

        for pattern in patterns:
            state = self.hass.states.get(pattern) if self.hass else None
            _LOGGER.debug(f"{self.room}: FB   pattern '{pattern}' → {state.state if state else 'not found'}")
            if state:
                return pattern

        # Search in energy context of device
        # (This would need device reference to check its sensors)
        _LOGGER.debug(f"{self.room}: FB no power sensor found for '{device_name}'")
        return None

    async def _get_power_value(self, entity_id: str) -> Optional[float]:
        """Get current power value from sensor."""
        try:
            if not self.hass:
                return None
            state = self.hass.states.get(entity_id)
            if state and state.state not in [None, "unknown", "unavailable", "", "None"]:
                return float(state.state)
        except (ValueError, TypeError):
            pass
        return None

    # =================================================================
    # Notification Methods
    # =================================================================

    async def _notify_entity_stale(self, state: MonitoredEntityState, age: timedelta):
        """Send notification for stale entity."""
        # Check cooldown to prevent spam
        last_notif = self._last_notification.get(state.entity_id)
        if last_notif:
            cooldown = timedelta(minutes=self.NOTIFICATION_COOLDOWN_MINUTES)
            if datetime.now() - last_notif < cooldown:
                _LOGGER.debug(
                    f"{self.room} Skipping notification for {state.entity_id} "
                    f"(cooldown)"
                )
                return

        age_minutes = int(age.total_seconds() / 60)

        # Build notification message
        if state.entity_type == "sensor":
            entity_label = f"Sensor '{state.sensor_type}'"
            details = f"Context: {state.context}"
        else:
            entity_label = f"Device '{state.device_name}'"
            # Show both device_type and context (label) if available
            if state.device_type and state.context and state.context != state.device_type:
                details = f"Type: {state.device_type}\nLabel: {state.context}"
            elif state.device_type:
                details = f"Type: {state.device_type}"
            else:
                details = f"Type: {state.context}"

        message = (
            f"⚠️ {entity_label} has not reported data for {age_minutes} minutes.\n\n"
            f"Device: {state.device_name}\n"
            f"Entity: {state.entity_id}\n"
            f"{details}\n"
        )

        if state.last_value is not None:
            message += f"Last value: {state.last_value}\n"

        message += (
            f"\n"
            f"⚠️ This may indicate a sensor failure, connectivity issue, "
            f"or device power problem."
        )

        try:
            # Send notification via NotifyManager
            await self.notificator.critical(
                message=message,
                title=f"OGB {self.room}: {state.entity_type.title()} Not Responding",
            )

            self._last_notification[state.entity_id] = datetime.now()
            state.notification_sent = True

            _LOGGER.warning(
                f"🚨 {self.room} Sent stale alert for {state.entity_id} "
                f"(age: {age_minutes} min)"
            )

            # Also emit event for potential frontend integration
            await self.event_manager.emit(
                "EntityStale",
                {
                    "room": self.room,
                    "entity_id": state.entity_id,
                    "entity_type": state.entity_type,
                    "sensor_type": state.sensor_type,
                    "device_name": state.device_name,
                    "context": state.context,
                    "device_type": state.device_type,
                    "age_minutes": age_minutes,
                    "last_value": state.last_value,
                    "timestamp": datetime.now().isoformat(),
                },
                haEvent=True,
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Failed to send stale notification: {e}")

    async def _notify_entity_recovered(self, state: MonitoredEntityState):
        """Send notification when entity recovers."""
        # Build recovery message
        if state.entity_type == "sensor":
            entity_label = f"Sensor '{state.sensor_type}'"
            details = f"Context: {state.context}"
        else:
            entity_label = f"Device '{state.device_name}'"
            # Show both device_type and context (label) if available
            if state.device_type and state.context and state.context != state.device_type:
                details = f"Type: {state.device_type}\nLabel: {state.context}"
            elif state.device_type:
                details = f"Type: {state.device_type}"
            else:
                details = f"Type: {state.context}"

        message = (
            f"✅ {entity_label} is now reporting data again.\n\n"
            f"Device: {state.device_name}\n"
            f"Entity: {state.entity_id}\n"
            f"{details}\n"
        )

        if state.last_value is not None:
            message += f"Current value: {state.last_value}\n"

        try:
            # Send recovery notification
            await self.notificator.info(
                message=message,
                title=f"OGB {self.room}: {state.entity_type.title()} Recovered",
            )

            _LOGGER.debug(
                f"✅ {self.room} Sent recovery notification for {state.entity_id}"
            )

            # Emit recovery event
            await self.event_manager.emit(
                "EntityRecovered",
                {
                    "room": self.room,
                    "entity_id": state.entity_id,
                    "entity_type": state.entity_type,
                    "sensor_type": state.sensor_type,
                    "device_name": state.device_name,
                    "context": state.context,
                    "device_type": state.device_type,
                    "current_value": state.last_value,
                    "timestamp": datetime.now().isoformat(),
                },
                haEvent=True,
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Failed to send recovery notification: {e}")

    # =================================================================
    # Status & Diagnostics
    # =================================================================

    def get_status(self) -> dict:
        """Get current monitoring status."""
        return {
            "room": self.room,
            "is_running": self._is_running,
            "is_initialized": self.is_initialized,
            "monitored_count": len(self._monitored_entities),
            "stale_count": len(self._stale_entities),
            "stale_entities": list(self._stale_entities),
            "threshold_minutes": self.STALE_THRESHOLD_MINUTES,
            "check_interval_seconds": self.CHECK_INTERVAL_SECONDS,
        }

    def get_monitored_entities(self) -> list:
        """Get list of all monitored entities with status."""
        entities = []

        for entity_id, state in self._monitored_entities.items():
            age = (datetime.now() - state.last_update).total_seconds() / 60

            entities.append(
                {
                    "entity_id": entity_id,
                    "entity_type": state.entity_type,
                    "sensor_type": state.sensor_type,
                    "device_name": state.device_name,
                    "context": state.context,
                    "device_type": state.device_type,
                    "is_stale": state.is_stale,
                    "age_minutes": round(age, 1),
                    "last_value": state.last_value,
                    "last_update": state.last_update.isoformat(),
                }
            )

        return sorted(entities, key=lambda x: x["age_minutes"], reverse=True)

    def get_stale_entities(self) -> list:
        """Get list of currently stale entities."""
        stale = []

        for entity_id in self._stale_entities:
            if entity_id in self._monitored_entities:
                state = self._monitored_entities[entity_id]
                age = (datetime.now() - state.last_update).total_seconds() / 60

                stale.append(
                    {
                        "entity_id": entity_id,
                        "entity_type": state.entity_type,
                        "sensor_type": state.sensor_type,
                        "device_name": state.device_name,
                        "context": state.context,
                        "device_type": state.device_type,
                        "age_minutes": round(age, 1),
                        "stale_since": (
                            state.stale_since.isoformat() if state.stale_since else None
                        ),
                    }
                )

        return stale

    async def shutdown(self):
        """Cleanup and shutdown."""
        await self.stop_monitoring()
        self._monitored_entities.clear()
        self._stale_entities.clear()
        self._last_notification.clear()
        _LOGGER.debug(f"🧹 {self.room} FallBack Manager shutdown complete")

    def __repr__(self):
        """String representation for debugging."""
        return (
            f"<OGBFallBackManager room={self.room} "
            f"monitored={len(self._monitored_entities)} "
            f"stale={len(self._stale_entities)} "
            f"running={self._is_running}>"
        )

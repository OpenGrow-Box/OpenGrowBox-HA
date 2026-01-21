"""
OpenGrowBox Ultra Instinct Manager

Advanced grow control mode with DIRECT device control.
Provides intelligent, self-learning control that adapts to plant needs over time.

Unlike other modes that emit VPD-based events through ActionManager,
Ultra Instinct directly controls devices with its own intelligent logic.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class UltraInstinctManager:
    """
    Manager for Ultra Instinct mode - DIRECT device control with adaptive optimization.

    Key Difference from Other Modes:
    - Other modes emit VPD events through ActionManager
    - Ultra Instinct directly controls devices with intelligent logic
    - Own control loop with custom algorithms (not VPD-based)
    - Adaptive learning from historical data
    """

    def __init__(self, data_store, event_manager, room, hass, device_manager=None):
        """
        Initialize the Ultra Instinct manager.

        Args:
            data_store: Reference to the data store
            event_manager: Reference to the event manager
            room: Room identifier
            hass: Home Assistant instance
            device_manager: Reference to device manager for direct device access
        """
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.hass = hass
        self.device_manager = device_manager

        # Control parameters
        self.control_active = False
        self.update_interval = 30  # seconds - faster response than VPD modes
        self.adaptive_learning = True

        # Control state
        self.last_control_time = None
        self.control_task: Optional[asyncio.Task] = None
        self.learning_data: Dict[str, Any] = {}

        # Device references (populated on start)
        self.devices: Dict[str, Any] = {}

        # Control targets (calculated by Ultra Instinct logic)
        self.targets = {
            "light_intensity": None,
            "exhaust_speed": None,
            "intake_speed": None,
            "ventilation_speed": None,
            "humidifier": None,
            "dehumidifier": None,
            "heater": None,
            "co2_injector": None,
        }

        # Register for Ultra Instinct control events
        self.event_manager.on("ultra_instinct_control", self._handle_direct_control)

        _LOGGER.info(f"Ultra Instinct Manager initialized for {room}")

    async def start_control(self):
        """
        Start the Ultra Instinct control loop with direct device access.
        """
        if self.control_active:
            _LOGGER.debug(f"Ultra Instinct control already active for {self.room}")
            return

        self.control_active = True

        # Populate device references for direct control
        await self._populate_devices()

        self.control_task = asyncio.create_task(self._control_loop())
        _LOGGER.info(f"Ultra Instinct control started for {self.room}")

        # Log mode activation
        await self.event_manager.emit(
            "LogForClient", {"Name": self.room, "Mode": "Ultra Instinct"}
        )

    async def stop_control(self):
        """
        Stop the Ultra Instinct control loop.
        """
        self.control_active = False

        if self.control_task:
            self.control_task.cancel()
            try:
                await self.control_task
            except asyncio.CancelledError:
                pass

        _LOGGER.info(f"Ultra Instinct control stopped for {self.room}")

    async def _populate_devices(self):
        """
        Populate device references for direct control.
        """
        if not self.device_manager:
            _LOGGER.warning(f"{self.room}: No device_manager available for direct control")
            return

        try:
            # Get devices from device manager
            devices = self.device_manager.get_all_devices() if hasattr(self.device_manager, 'get_all_devices') else {}
            self.devices = devices
            _LOGGER.info(f"{self.room}: Populated {len(self.devices)} devices for Ultra Instinct control")
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error populating devices: {e}")

    async def _control_loop(self):
        """
        Main control loop for Ultra Instinct management.
        Runs adaptive control cycles that learn and optimize over time.
        """
        _LOGGER.info(f"Ultra Instinct control loop started for {self.room}")

        while self.control_active:
            try:
                await self._execute_control_cycle()
                await asyncio.sleep(self.update_interval)

            except Exception as e:
                _LOGGER.error(f"Error in Ultra Instinct control loop for {self.room}: {e}")
                await asyncio.sleep(30)

    async def _execute_control_cycle(self):
        """
        Execute a complete Ultra Instinct control cycle with DIRECT device control.
        """
        _LOGGER.debug(f"Ultra Instinct: Direct control cycle for {self.room}")

        # Get current sensor data
        sensor_data = self._get_sensor_data()
        if not sensor_data:
            _LOGGER.warning(f"{self.room}: No sensor data available")
            return

        # Calculate optimal targets using Ultra Instinct logic
        await self._calculate_targets(sensor_data)

        # Execute DIRECT control actions on devices
        await self._execute_direct_control(sensor_data)

        # Learn from current state for future optimization
        if self.adaptive_learning:
            await self._learn_from_cycle(sensor_data)

        # Update control timestamp
        self.last_control_time = datetime.now()

    def _get_sensor_data(self) -> Dict[str, Any]:
        """
        Get current sensor data for control decisions.
        """
        return {
            "temperature": self.data_store.getDeep("tentData.temperature"),
            "humidity": self.data_store.getDeep("tentData.humidity"),
            "vpd": self.data_store.getDeep("vpd.current"),
            "co2": self.data_store.getDeep("tentData.co2"),
            "light_intensity": self.data_store.getDeep("tentData.light_intensity"),
            "ambient_temp": self.data_store.getDeep("tentData.AmbientTemp"),
            "ambient_hum": self.data_store.getDeep("tentData.AmbientHum"),
        }

    async def _calculate_targets(self, sensor_data: Dict[str, Any]):
        """
        Calculate optimal control targets using Ultra Instinct logic.
        
        ULTRA INSTINCT LOGIC (to be implemented):
        - Multi-sensor fusion
        - Predictive control based on trends
        - Energy-efficient optimization
        - Plant stage-aware decisions
        
        For now: Base targets with simple optimization
        """
        _LOGGER.debug(f"Ultra Instinct: Calculating targets for {self.room}")

        # Get plant stage for adaptive targets
        plant_stage = self.data_store.get("plantStage") or "LateVeg"

        # Base targets per plant stage (can be overridden by learning)
        stage_targets = {
            "Germination": {"light": 30, "exhaust": 20, "humidity": 75},
            "Clones": {"light": 40, "exhaust": 25, "humidity": 72},
            "EarlyVeg": {"light": 55, "exhaust": 35, "humidity": 68},
            "MidVeg": {"light": 70, "exhaust": 45, "humidity": 65},
            "LateVeg": {"light": 85, "exhaust": 55, "humidity": 62},
            "EarlyFlower": {"light": 90, "exhaust": 60, "humidity": 58},
            "MidFlower": {"light": 95, "exhaust": 70, "humidity": 55},
            "LateFlower": {"light": 95, "exhaust": 75, "humidity": 52},
        }

        base = stage_targets.get(plant_stage, stage_targets["LateVeg"])

        # Adjust based on current conditions (simple optimization)
        current_vpd = sensor_data.get("vpd", 1.0)
        target_vpd = self.data_store.getDeep("vpd.perfection") or 1.0
        perfection_max_vpd = self.data_store.getDeep("vpd.perfectMax") or 1.2

        if current_vpd < target_vpd - 0.2:
            # VPD too low - reduce humidity contribution
            base["humidity"] = max(40, base["humidity"] - 5)
            _LOGGER.debug(f"{self.room}: VPD low, adjusted humidity target to {base['humidity']}%")
        elif current_vpd > perfection_max_vpd + 0.2:
            # VPD too high - increase humidity contribution
            base["humidity"] = min(85, base["humidity"] + 5)
            _LOGGER.debug(f"{self.room}: VPD high, adjusted humidity target to {base['humidity']}%")

        # Set targets
        self.targets["light_intensity"] = base["light"]
        self.targets["exhaust_speed"] = base["exhaust"]
        self.targets["humidity_target"] = base["humidity"]

        _LOGGER.debug(f"Ultra Instinct targets for {self.room}: {self.targets}")

    async def _execute_direct_control(self, sensor_data: Dict[str, Any]):
        """
        Execute DIRECT control actions on devices.
        This is the key difference: We don't emit VPD events.
        We directly control each device with calculated targets.
        """
        _LOGGER.debug(f"Ultra Instinct: Executing direct control for {self.room}")

        # Control Light
        if "light" in self.devices and self.targets.get("light_intensity"):
            light = self.devices["light"]
            target_intensity = self.targets["light_intensity"]
            current_intensity = getattr(light, 'voltage', 0)
            if abs(current_intensity - target_intensity) > 5:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Setting light to {target_intensity}%")
                try:
                    if hasattr(light, 'turn_on'):
                        await light.turn_on(brightness_pct=target_intensity)
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling light: {e}")

        # Control Exhaust
        if "exhaust" in self.devices and self.targets.get("exhaust_speed"):
            exhaust = self.devices["exhaust"]
            target_speed = self.targets["exhaust_speed"]
            current_speed = getattr(exhaust, 'dutyCycle', 0)
            if abs(current_speed - target_speed) > 5:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Setting exhaust to {target_speed}%")
                try:
                    if hasattr(exhaust, 'set_duty_cycle'):
                        await exhaust.set_duty_cycle(target_speed)
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling exhaust: {e}")

        # Control Intake
        if "intake" in self.devices and self.targets.get("intake_speed"):
            intake = self.devices["intake"]
            target_speed = self.targets["intake_speed"]
            current_speed = getattr(intake, 'dutyCycle', 0)
            if abs(current_speed - target_speed) > 5:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Setting intake to {target_speed}%")
                try:
                    if hasattr(intake, 'set_duty_cycle'):
                        await intake.set_duty_cycle(target_speed)
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling intake: {e}")

        # Control Humidity (Humidifier/Dehumidifier)
        current_humidity = sensor_data.get("humidity", 50)
        humidity_target = self.targets.get("humidity_target", 60)

        if current_humidity < humidity_target - 3:
            # Need more humidity - turn on humidifier
            if "humidifier" in self.devices:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Turning on humidifier")
                try:
                    await self.devices["humidifier"].turn_on()
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling humidifier: {e}")
        elif current_humidity > humidity_target + 3:
            # Too humid - turn on dehumidifier
            if "dehumidifier" in self.devices:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Turning on dehumidifier")
                try:
                    await self.devices["dehumidifier"].turn_on()
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling dehumidifier: {e}")

    async def _handle_direct_control(self, data: Dict[str, Any]):
        """
        Handle direct Ultra Instinct control commands.
        
        This allows other components to send direct control commands
        through the Ultra Instinct manager.
        
        Args:
            data: Control data containing device, action, and parameters
                  Example: {"device": "light", "action": "set_intensity", "value": 75}
        """
        device = data.get("device")
        action = data.get("action")
        value = data.get("value")

        if not device or not action:
            _LOGGER.warning(f"{self.room}: Invalid ultra_instinct_control data: {data}")
            return

        if device not in self.devices:
            _LOGGER.warning(f"{self.room}: Unknown device for direct control: {device}")
            return

        try:
            dev = self.devices[device]
            if action == "set_intensity" or action == "set_brightness":
                if hasattr(dev, 'turn_on'):
                    await dev.turn_on(brightness_pct=value)
            elif action == "set_duty_cycle":
                if hasattr(dev, 'set_duty_cycle'):
                    await dev.set_duty_cycle(value)
            elif action == "turn_on":
                await dev.turn_on()
            elif action == "turn_off":
                await dev.turn_off()
            _LOGGER.info(f"{self.room}: Ultra Instinct direct control - {device}: {action} = {value}")
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error in direct control {device}: {e}")

    async def _learn_from_cycle(self, sensor_data: Dict[str, Any]):
        """
        Learn from the current control cycle for future optimization.
        """
        # Store sensor data for analysis
        entry = {
            "timestamp": datetime.now().isoformat(),
            "sensors": sensor_data,
            "targets": self.targets.copy(),
        }

        if "control_history" not in self.learning_data:
            self.learning_data["control_history"] = []
        self.learning_data["control_history"].append(entry)

        # Keep only last 100 entries
        if len(self.learning_data["control_history"]) > 100:
            self.learning_data["control_history"].pop(0)

    def get_control_status(self) -> Dict[str, Any]:
        """
        Get current Ultra Instinct control status.
        """
        return {
            "room": self.room,
            "control_active": self.control_active,
            "update_interval": self.update_interval,
            "adaptive_learning": self.adaptive_learning,
            "last_control_time": self.last_control_time.isoformat() if self.last_control_time else None,
            "targets": self.targets,
            "devices_controlled": len(self.devices),
            "learning_data_entries": len(self.learning_data.get("control_history", [])),
        }

    async def emergency_stop(self):
        """
        Emergency stop of all Ultra Instinct control.
        """
        # Turn off all devices
        for name, dev in self.devices.items():
            try:
                if hasattr(dev, 'turn_off'):
                    await dev.turn_off()
            except Exception:
                pass

        await self.stop_control()
        _LOGGER.warning(f"Emergency stop initiated for Ultra Instinct control in {self.room}")

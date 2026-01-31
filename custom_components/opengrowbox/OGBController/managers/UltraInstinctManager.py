"""
OpenGrowBox Ultra Instinct Manager

Advanced grow control mode with DIRECT device control and PREDICTIVE intelligence.
Provides intelligent, self-learning control that adapts to plant needs over time.
Uses Ambient and Outside data for predictive control - anticipating changes before they happen.

Unlike other modes that emit VPD-based events through ActionManager,
Ultra Instinct directly controls devices with its own intelligent logic.
Key innovation: Predictive control using Outside→Ambient→Tent gradients.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class UltraInstinctManager:
    """
    Manager for Ultra Instinct mode - DIRECT device control with PREDICTIVE optimization.

    Key Differences from Other Modes:
    - Other modes emit VPD events through ActionManager
    - Ultra Instinct directly controls devices with intelligent logic
    - PREDICTIVE control using Outside→Ambient→Tent gradient analysis
    - Anticipates changes before they happen (preventive, not reactive)
    - Own control loop with custom algorithms (not VPD-based)
    - Adaptive learning from historical data
    
    Predictive Control Strategy:
    1. Outside conditions → predict Ambient changes (5-15 min ahead)
    2. Ambient changes → predict Tent impact (2-5 min ahead)
    3. Pre-adjust devices before deviations occur
    4. VPD Perfection-style precision with energy-efficient ambient optimization
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
        
        # Predictive control parameters
        self.prediction_window_minutes = 5  # How far ahead to predict
        self.gradient_history_size = 20  # Number of data points for trend analysis
        self.outside_ambient_inertia = 0.7  # Factor for Outside→Ambient prediction (0-1)
        self.ambient_tent_inertia = 0.5  # Factor for Ambient→Tent prediction (0-1)

        # Control state
        self.last_control_time = None
        self.control_task: Optional[asyncio.Task] = None
        self.learning_data: Dict[str, Any] = {}
        
        # Historical data for trend analysis
        self.sensor_history: List[Dict[str, Any]] = []

        # Device references (populated on start)
        self.devices: Dict[str, Any] = {}

        # Control targets (calculated by Ultra Instinct logic)
        self.targets: Dict[str, Optional[float]] = {
            "light_intensity": None,
            "exhaust_speed": None,
            "intake_speed": None,
            "ventilation_speed": None,
            "humidifier": None,
            "dehumidifier": None,
            "heater": None,
            "co2_injector": None,
            "temperature_target": None,
            "humidity_target": None,
        }
        
        # Predictive adjustments
        self.predictive_adjustments = {
            "temp_adjustment": 0.0,
            "hum_adjustment": 0.0,
            "exhaust_pre_adjust": 0.0,
            "light_pre_adjust": 0.0,
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
            
        # Update sensor history for trend analysis
        self._update_sensor_history(sensor_data)

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
        Includes all temperature/humidity layers for predictive analysis.
        """
        return {
            # Inside tent (current state)
            "temperature": self.data_store.getDeep("tentData.temperature"),
            "humidity": self.data_store.getDeep("tentData.humidity"),
            "vpd": self.data_store.getDeep("vpd.current"),
            "co2": self.data_store.getDeep("tentData.co2"),
            "light_intensity": self.data_store.getDeep("tentData.light_intensity"),
            "tent_temp": self.data_store.getDeep("tentData.Temperature"),
            "tent_hum": self.data_store.getDeep("tentData.Humidity"),
            # Ambient (room) conditions
            "ambient_temp": self.data_store.getDeep("tentData.AmbientTemp"),
            "ambient_hum": self.data_store.getDeep("tentData.AmbientHum"),
            # Outside conditions (for prediction)
            "outside_temp": self.data_store.getDeep("tentData.OutsiteTemp"),
            "outside_hum": self.data_store.getDeep("tentData.OutsiteHum"),
        }
        
    def _update_sensor_history(self, sensor_data: Dict[str, Any]):
        """
        Store sensor data for trend analysis.
        Keeps a rolling window of historical data points.
        """
        entry = {
            "timestamp": datetime.now(),
            **sensor_data
        }
        
        self.sensor_history.append(entry)
        
        # Keep only last N entries
        if len(self.sensor_history) > self.gradient_history_size:
            self.sensor_history.pop(0)
            
    def _calculate_gradients(self) -> Dict[str, float]:
        """
        Calculate temperature and humidity gradients between all layers.
        
        Returns gradient dict with:
        - outside_to_ambient_temp: How fast Outside temp affects Ambient
        - outside_to_ambient_hum: How fast Outside humidity affects Ambient
        - ambient_to_tent_temp: How fast Ambient temp affects Tent
        - ambient_to_tent_hum: How fast Ambient humidity affects Tent
        """
        if len(self.sensor_history) < 2:
            return {
                "outside_to_ambient_temp": 0.0,
                "outside_to_ambient_hum": 0.0,
                "ambient_to_tent_temp": 0.0,
                "ambient_to_tent_hum": 0.0,
                "temp_trend": 0.0,
                "hum_trend": 0.0,
            }
        
        # Get latest and oldest data points
        latest = self.sensor_history[-1]
        oldest = self.sensor_history[0]
        
        # Calculate time delta in hours
        time_delta_hours = (latest["timestamp"] - oldest["timestamp"]).total_seconds() / 3600
        if time_delta_hours == 0:
            time_delta_hours = 0.5  # Assume 30 min default
            
        # Calculate Outside→Ambient gradients (environmental transfer rate)
        if latest.get("outside_temp") is not None and latest.get("ambient_temp") is not None:
            outside_ambient_temp_diff = latest["outside_temp"] - latest["ambient_temp"]
            # Gradient: how many °C difference drives change
            oat_gradient = outside_ambient_temp_diff / time_delta_hours if time_delta_hours > 0 else 0
        else:
            oat_gradient = 0.0
            
        if latest.get("outside_hum") is not None and latest.get("ambient_hum") is not None:
            outside_ambient_hum_diff = latest["outside_hum"] - latest["ambient_hum"]
            oah_gradient = outside_ambient_hum_diff / time_delta_hours if time_delta_hours > 0 else 0
        else:
            oah_gradient = 0.0
        
        # Calculate Ambient→Tent gradients (tent isolation/transfer rate)
        if latest.get("ambient_temp") is not None and latest.get("tent_temp") is not None:
            ambient_tent_temp_diff = latest["ambient_temp"] - latest["tent_temp"]
            att_gradient = ambient_tent_temp_diff / time_delta_hours if time_delta_hours > 0 else 0
        else:
            att_gradient = 0.0
            
        if latest.get("ambient_hum") is not None and latest.get("tent_hum") is not None:
            ambient_tent_hum_diff = latest["ambient_hum"] - latest["tent_hum"]
            ath_gradient = ambient_tent_hum_diff / time_delta_hours if time_delta_hours > 0 else 0
        else:
            ath_gradient = 0.0
        
        # Calculate internal Tent trends (rate of change)
        if len(self.sensor_history) >= 3:
            # Use last 3 points for trend
            temp_changes = []
            hum_changes = []
            for i in range(len(self.sensor_history) - 1, max(0, len(self.sensor_history) - 4), -1):
                if i > 0:
                    curr = self.sensor_history[i]
                    prev = self.sensor_history[i-1]
                    dt = (curr["timestamp"] - prev["timestamp"]).total_seconds() / 3600
                    if dt > 0:
                        if curr.get("tent_temp") and prev.get("tent_temp"):
                            temp_changes.append((curr["tent_temp"] - prev["tent_temp"]) / dt)
                        if curr.get("tent_hum") and prev.get("tent_hum"):
                            hum_changes.append((curr["tent_hum"] - prev["tent_hum"]) / dt)
            
            temp_trend = sum(temp_changes) / len(temp_changes) if temp_changes else 0
            hum_trend = sum(hum_changes) / len(hum_changes) if hum_changes else 0
        else:
            temp_trend = 0.0
            hum_trend = 0.0
        
        return {
            "outside_to_ambient_temp": oat_gradient,
            "outside_to_ambient_hum": oah_gradient,
            "ambient_to_tent_temp": att_gradient,
            "ambient_to_tent_hum": ath_gradient,
            "temp_trend": temp_trend,
            "hum_trend": hum_trend,
        }
        
    def _calculate_predictive_factors(self, sensor_data: Dict[str, Any], gradients: Dict[str, float]) -> Dict[str, float]:
        """
        Calculate predictive adjustment factors based on Outside→Ambient→Tent gradients.
        
        Returns factors that predict what will happen in prediction_window_minutes.
        Positive = will increase, Negative = will decrease.
        """
        window_hours = self.prediction_window_minutes / 60
        
        # Get current values
        outside_temp = sensor_data.get("outside_temp")
        outside_hum = sensor_data.get("outside_hum")
        ambient_temp = sensor_data.get("ambient_temp")
        ambient_hum = sensor_data.get("ambient_hum")
        tent_temp = sensor_data.get("tent_temp") or sensor_data.get("temperature")
        tent_hum = sensor_data.get("tent_hum") or sensor_data.get("humidity")
        
        # Initialize factors
        temp_factor = 0.0
        hum_factor = 0.0
        exhaust_factor = 0.0
        
        if outside_temp is not None and ambient_temp is not None and tent_temp is not None:
            # Step 1: Predict where Ambient will be in 5-15 min based on Outside trend
            # Higher gradient = faster Outside influence on Ambient
            outside_influence = gradients["outside_to_ambient_temp"] * self.outside_ambient_inertia
            predicted_ambient_temp = ambient_temp + (outside_influence * window_hours)
            
            # Step 2: Predict where Tent will be based on predicted Ambient
            # How much Ambient change transfers to Tent
            ambient_influence = gradients["ambient_to_tent_temp"] * self.ambient_tent_inertia
            temp_change_from_ambient = (predicted_ambient_temp - ambient_temp) * 0.3  # 30% transfer
            
            # Step 3: Add current Tent trend
            temp_change_from_trend = gradients["temp_trend"] * window_hours
            
            # Combined prediction
            predicted_tent_temp = tent_temp + temp_change_from_ambient + temp_change_from_trend
            temp_factor = predicted_tent_temp - tent_temp
            
            # Exhaust pre-adjustment based on predicted heat load
            if temp_factor > 0.5:  # Will get warmer
                exhaust_factor = min(15, temp_factor * 5)  # Pre-increase exhaust
            elif temp_factor < -0.5:  # Will get cooler
                exhaust_factor = max(-10, temp_factor * 3)  # Can reduce exhaust slightly
                
        if outside_hum is not None and ambient_hum is not None and tent_hum is not None:
            # Same logic for humidity
            outside_influence_hum = gradients["outside_to_ambient_hum"] * self.outside_ambient_inertia
            predicted_ambient_hum = ambient_hum + (outside_influence_hum * window_hours)
            
            ambient_influence_hum = gradients["ambient_to_tent_hum"] * self.ambient_tent_inertia
            hum_change_from_ambient = (predicted_ambient_hum - ambient_hum) * 0.4  # 40% transfer
            
            hum_change_from_trend = gradients["hum_trend"] * window_hours
            
            predicted_tent_hum = tent_hum + hum_change_from_ambient + hum_change_from_trend
            hum_factor = predicted_tent_hum - tent_hum
        
        _LOGGER.debug(
            f"{self.room}: Predictive factors - Temp: {temp_factor:+.2f}°C, "
            f"Hum: {hum_factor:+.2f}%, Exhaust: {exhaust_factor:+.1f}% "
            f"(in {self.prediction_window_minutes}min)"
        )
        
        return {
            "temp_predicted_change": temp_factor,
            "hum_predicted_change": hum_factor,
            "exhaust_pre_adjust": exhaust_factor,
        }

    async def _calculate_targets(self, sensor_data: Dict[str, Any]):
        """
        Calculate optimal control targets using PREDICTIVE Ultra Instinct logic.
        
        Combines:
        1. VPD Perfection-style range-based targeting
        2. Predictive control using Outside→Ambient→Tent gradients
        3. Plant stage optimization
        4. Energy-efficient ambient-aware adjustments
        """
        _LOGGER.debug(f"Ultra Instinct: Calculating PREDICTIVE targets for {self.room}")
        
        # Get gradients and predictive factors
        gradients = self._calculate_gradients()
        predictive = self._calculate_predictive_factors(sensor_data, gradients)

        # Get plant stage for adaptive targets
        plant_stage = self.data_store.get("plantStage") or "LateVeg"
        
        # Get VPD Perfection targets from datastore
        target_vpd = self.data_store.getDeep("vpd.perfection") or 1.0
        min_vpd = self.data_store.getDeep("vpd.perfectMin") or 0.8
        max_vpd = self.data_store.getDeep("vpd.perfectMax") or 1.2
        current_vpd = sensor_data.get("vpd", 1.0)

        # Get plant stage data for temperature/humidity ranges
        stage_data = self._get_plant_stage_data(plant_stage)
        
        if stage_data:
            # Use plant stage ranges (like VPD perfection)
            temp_min = stage_data.get("minTemp", 20)
            temp_max = stage_data.get("maxTemp", 30)
            hum_optimal = stage_data.get("optimalHumidity", 60)
        else:
            # Default ranges
            temp_min, temp_max = 20, 30
            hum_optimal = 60
            
        # Calculate perfection midpoint (like VPD perfection)
        target_temp = (temp_min + temp_max) / 2
        
        # Apply PREDICTIVE adjustments
        predicted_temp_change = predictive["temp_predicted_change"]
        predicted_hum_change = predictive["hum_predicted_change"]
        
        # Pre-adjust target to counteract predicted changes
        # If tent will get 1°C warmer, target slightly lower to compensate
        predictive_temp_adjustment = predicted_temp_change * 0.7
        predictive_hum_adjustment = predicted_hum_change * 0.6
        
        adjusted_target_temp = target_temp - predictive_temp_adjustment
        adjusted_target_hum = hum_optimal - predictive_hum_adjustment
        
        # Keep within safety bounds
        adjusted_target_temp = max(temp_min, min(temp_max, adjusted_target_temp))
        adjusted_target_hum = max(40, min(85, adjusted_target_hum))
        
        # Calculate exhaust speed with predictive pre-adjustment
        base_exhaust = self._calculate_base_exhaust_speed(plant_stage, sensor_data, current_vpd, target_vpd, max_vpd)
        predictive_exhaust_adjust = predictive["exhaust_pre_adjust"]
        target_exhaust = base_exhaust + predictive_exhaust_adjust
        target_exhaust = max(10, min(100, target_exhaust))
        
        # Calculate intake (typically 10-20% lower than exhaust for negative pressure)
        target_intake = target_exhaust * 0.85
        
        # Light intensity based on plant stage (with predictive dimming if too hot)
        base_light = self._calculate_base_light_intensity(plant_stage)
        if predicted_temp_change > 1.0:  # Will get significantly warmer
            # Preemptive light reduction to prevent overheating
            light_reduction = min(20, predicted_temp_change * 8)
            target_light = base_light - light_reduction
            _LOGGER.debug(f"{self.room}: Preemptive light reduction: -{light_reduction:.1f}% "
                         f"due to predicted temp rise")
        else:
            target_light = base_light
            
        target_light = max(0, min(100, target_light))

        # Set targets
        self.targets["temperature_target"] = round(adjusted_target_temp, 1)
        self.targets["humidity_target"] = round(adjusted_target_hum, 1)
        self.targets["light_intensity"] = round(target_light, 1)
        self.targets["exhaust_speed"] = round(target_exhaust, 1)
        self.targets["intake_speed"] = round(target_intake, 1)
        
        # Store predictive data for logging/debugging
        self.predictive_adjustments = {
            "temp_predicted_change": round(predicted_temp_change, 2),
            "hum_predicted_change": round(predicted_hum_change, 2),
            "temp_adjustment": round(predictive_temp_adjustment, 2),
            "hum_adjustment": round(predictive_hum_adjustment, 2),
            "exhaust_pre_adjust": round(predictive_exhaust_adjust, 1),
        }
        
        _LOGGER.info(
            f"{self.room}: Predictive targets - Temp: {self.targets['temperature_target']}°C "
            f"(predicted change: {predicted_temp_change:+.2f}°C), "
            f"Hum: {self.targets['humidity_target']}% "
            f"(predicted change: {predicted_hum_change:+.2f}%), "
            f"Light: {self.targets['light_intensity']}%, "
            f"Exhaust: {self.targets['exhaust_speed']}%"
        )
        
    def _get_plant_stage_data(self, plant_stage: str) -> Optional[Dict]:
        """Get plant stage data from datastore."""
        if not plant_stage:
            return None
        plant_stages = self.data_store.get("plantStages")
        if plant_stages:
            # Try exact match first
            if plant_stage in plant_stages:
                return plant_stages[plant_stage]
            # Try normalized match
            normalized = plant_stage.replace(" ", "").replace("-", "")
            for key, data in plant_stages.items():
                if key.replace(" ", "").replace("-", "") == normalized:
                    return data
        return None
        
    def _calculate_base_exhaust_speed(
        self, 
        plant_stage: str, 
        sensor_data: Dict[str, Any],
        current_vpd: float,
        target_vpd: float,
        max_vpd: float
    ) -> float:
        """Calculate base exhaust speed with VPD-aware adjustment."""
        # Base speeds by plant stage
        stage_exhaust = {
            "Germination": 15,
            "Clones": 20,
            "EarlyVeg": 30,
            "MidVeg": 45,
            "LateVeg": 55,
            "EarlyFlower": 60,
            "MidFlower": 70,
            "LateFlower": 75,
        }
        
        base = stage_exhaust.get(plant_stage, 55)
        
        # Adjust based on VPD (like VPD Perfection)
        if current_vpd > max_vpd + 0.2:
            # VPD too high - increase exhaust to remove moisture
            base += 15
        elif current_vpd < target_vpd - 0.3:
            # VPD too low - reduce exhaust to retain moisture
            base -= 10
        elif current_vpd > target_vpd + 0.1:
            # Slightly high - fine tune
            base += 5
            
        # Consider ambient temperature
        ambient_temp = sensor_data.get("ambient_temp")
        if ambient_temp is not None:
            if ambient_temp > 28:  # Warm ambient
                base += 5  # More exhaust for cooling
            elif ambient_temp < 15:  # Cold ambient
                base -= 5  # Less exhaust to retain heat
                
        return max(10, min(100, base))
        
    def _calculate_base_light_intensity(self, plant_stage: str) -> float:
        """Calculate base light intensity by plant stage."""
        stage_light = {
            "Germination": 25,
            "Clones": 35,
            "EarlyVeg": 50,
            "MidVeg": 65,
            "LateVeg": 80,
            "EarlyFlower": 85,
            "MidFlower": 95,
            "LateFlower": 95,
        }
        return stage_light.get(plant_stage, 80)

    async def _execute_direct_control(self, sensor_data: Dict[str, Any]):
        """
        Execute DIRECT control actions on devices with PREDICTIVE adjustments.
        """
        _LOGGER.debug(f"Ultra Instinct: Executing direct control for {self.room}")
        
        current_temp = sensor_data.get("tent_temp") or sensor_data.get("temperature", 22)
        current_hum = sensor_data.get("tent_hum") or sensor_data.get("humidity", 60)
        target_temp = self.targets.get("temperature_target", 24)
        target_hum = self.targets.get("humidity_target", 60)

        # Control Light
        if "light" in self.devices and self.targets.get("light_intensity") is not None:
            light = self.devices["light"]
            target_intensity = self.targets["light_intensity"] or 0
            current_intensity = getattr(light, 'voltage', 0)
            if abs(current_intensity - target_intensity) > 5:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Setting light to {target_intensity}%")
                try:
                    if hasattr(light, 'turn_on'):
                        await light.turn_on(brightness_pct=target_intensity)
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling light: {e}")

        # Control Exhaust
        if "exhaust" in self.devices and self.targets.get("exhaust_speed") is not None:
            exhaust = self.devices["exhaust"]
            target_speed = self.targets["exhaust_speed"] or 0
            current_speed = getattr(exhaust, 'dutyCycle', 0)
            if abs(current_speed - target_speed) > 5:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Setting exhaust to {target_speed}%")
                try:
                    if hasattr(exhaust, 'set_duty_cycle'):
                        await exhaust.set_duty_cycle(target_speed)
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling exhaust: {e}")

        # Control Intake
        if "intake" in self.devices and self.targets.get("intake_speed") is not None:
            intake = self.devices["intake"]
            target_speed = self.targets["intake_speed"] or 0
            current_speed = getattr(intake, 'dutyCycle', 0)
            if abs(current_speed - target_speed) > 5:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Setting intake to {target_speed}%")
                try:
                    if hasattr(intake, 'set_duty_cycle'):
                        await intake.set_duty_cycle(target_speed)
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling intake: {e}")

        # Control Humidity (Humidifier/Dehumidifier) with predictive consideration
        hum_diff = current_hum - target_hum
        
        # Add hysteresis to prevent oscillation
        if hum_diff < -5:  # Need more humidity
            if "humidifier" in self.devices:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Turning on humidifier (current: {current_hum}%, target: {target_hum}%)")
                try:
                    await self.devices["humidifier"].turn_on()
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling humidifier: {e}")
            if "dehumidifier" in self.devices:
                try:
                    await self.devices["dehumidifier"].turn_off()
                except Exception:
                    pass
                    
        elif hum_diff > 5:  # Too humid
            if "dehumidifier" in self.devices:
                _LOGGER.info(f"{self.room}: Ultra Instinct - Turning on dehumidifier (current: {current_hum}%, target: {target_hum}%)")
                try:
                    await self.devices["dehumidifier"].turn_on()
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling dehumidifier: {e}")
            if "humidifier" in self.devices:
                try:
                    await self.devices["humidifier"].turn_off()
                except Exception:
                    pass
        
        # Control Heater if available
        if "heater" in self.devices:
            temp_diff = target_temp - current_temp
            if temp_diff > 3:  # Need heating
                _LOGGER.info(f"{self.room}: Ultra Instinct - Turning on heater (current: {current_temp}°C, target: {target_temp}°C)")
                try:
                    await self.devices["heater"].turn_on()
                except Exception as e:
                    _LOGGER.error(f"{self.room}: Error controlling heater: {e}")
            elif temp_diff < -1:  # Warm enough
                try:
                    await self.devices["heater"].turn_off()
                except Exception:
                    pass

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
        Stores gradient data for adaptive prediction improvement.
        """
        gradients = self._calculate_gradients()
        
        entry = {
            "timestamp": datetime.now().isoformat(),
            "sensors": sensor_data,
            "targets": self.targets.copy(),
            "predictive_adjustments": self.predictive_adjustments.copy(),
            "gradients": gradients,
        }

        if "control_history" not in self.learning_data:
            self.learning_data["control_history"] = []
        self.learning_data["control_history"].append(entry)

        # Keep only last 100 entries
        if len(self.learning_data["control_history"]) > 100:
            self.learning_data["control_history"].pop(0)
            
        # Learn gradient patterns (simplified learning)
        if len(self.sensor_history) >= 10:
            # Calculate average gradients over recent history
            recent_gradients = []
            for i in range(len(self.learning_data["control_history"]) - 10, len(self.learning_data["control_history"])):
                if i >= 0 and "gradients" in self.learning_data["control_history"][i]:
                    recent_gradients.append(self.learning_data["control_history"][i]["gradients"])
            
            if recent_gradients:
                # Update inertia factors based on observed patterns
                avg_temp_gradient = sum(g.get("ambient_to_tent_temp", 0) for g in recent_gradients) / len(recent_gradients)
                if avg_temp_gradient > 2:
                    # Fast transfer - reduce prediction window
                    self.ambient_tent_inertia = min(0.8, self.ambient_tent_inertia + 0.05)
                elif avg_temp_gradient < 0.5:
                    # Slow transfer - increase prediction window
                    self.ambient_tent_inertia = max(0.3, self.ambient_tent_inertia - 0.05)

    def get_control_status(self) -> Dict[str, Any]:
        """
        Get current Ultra Instinct control status including predictive data.
        """
        gradients = self._calculate_gradients()
        
        return {
            "room": self.room,
            "control_active": self.control_active,
            "update_interval": self.update_interval,
            "adaptive_learning": self.adaptive_learning,
            "last_control_time": self.last_control_time.isoformat() if self.last_control_time else None,
            "targets": self.targets,
            "predictive_adjustments": self.predictive_adjustments,
            "gradients": gradients,
            "sensor_history_count": len(self.sensor_history),
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

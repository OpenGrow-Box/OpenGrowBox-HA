"""
OpenGrowBox Drying Actions

Handles all drying mode operations including ElClassico, 5DayDry, and DewBased algorithms.
"""

import math
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from ..utils.calcs import calc_Dry5Days_vpd, calc_dew_vpd

_LOGGER = logging.getLogger(__name__)


class DryingActions:
    """Handles drying mode operations and algorithms."""

    def __init__(self, data_store, event_manager, room: str):
        """Initialize drying actions."""
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.name = f"DryingActions-{room}"
        
        # Register cleanup listener for when drying mode is turned off
        self.event_manager.on("drying_cleanup", self._handle_cleanup_event)

    async def handle_drying(self) -> Optional[Dict[str, Any]]:
        """
        Main drying mode dispatcher.
        Routes to specific drying algorithms based on current mode.
        """
        currentDryMode = self.data_store.getDeep("drying.currentDryMode")
        
        _LOGGER.warning(f"{self.name}: handle_drying called, currentDryMode={currentDryMode}")

        # Check if start time exists, if not set it
        # Guard against empty or invalid drying modes
        if not currentDryMode or currentDryMode == "NO-Dry":
            _LOGGER.debug(f"{self.name}: No drying mode active (currentDryMode={currentDryMode}), skipping")
            return None
            
        mode_start_time = self.data_store.getDeep("drying.mode_start_time")
        if mode_start_time is None:
            self.start_drying_mode(currentDryMode)

        if currentDryMode == "ElClassico":
            phaseConfig = self.data_store.getDeep(f"drying.modes.{currentDryMode}")
            await self.handle_ElClassico(phaseConfig)
        elif currentDryMode == "DewBased":
            phaseConfig = self.data_store.getDeep(f"drying.modes.{currentDryMode}")
            await self.handle_DewBased(phaseConfig)
        elif currentDryMode == "5DayDry":
            phaseConfig = self.data_store.getDeep(f"drying.modes.{currentDryMode}")
            await self.handle_5DayDry(phaseConfig)
        elif currentDryMode == "OwnDry":
            await self.handle_OwnDry()
        elif currentDryMode == "NO-Dry":
            return None
        else:
            _LOGGER.debug(f"{self.name} Unknown DryMode Received: {currentDryMode}")
            return None

    async def _handle_cleanup_event(self, data):
        """Handle drying_cleanup event from configuration manager."""
        event_room = data.get("room") if isinstance(data, dict) else None
        # Only cleanup if this is for our room
        if event_room is None or event_room == self.room:
            await self.cleanup_drying_devices()
    
    async def cleanup_drying_devices(self) -> None:
        """
        Turn off all drying-related devices when switching to NO-Dry.
        """
        _LOGGER.debug(f"{self.name}: Cleaning up drying devices")
        
        # Direct event emission for cleanup (no cooldowns needed for cleanup)
        await self.event_manager.emit("Reduce Heater", None)
        await self.event_manager.emit("Reduce Cooler", None)
        await self.event_manager.emit("Reduce Humidifier", None)
        await self.event_manager.emit("Reduce Dehumidifier", None)
        await self.event_manager.emit("Reduce Exhaust", None)
        await self.event_manager.emit("Reduce Ventilation", None)
        
        _LOGGER.debug(f"{self.name}: All drying devices turned off")

    def start_drying_mode(self, mode_name: str) -> None:
        """
        Initialize a drying mode and store the start timestamp.
        """
        self.data_store.setDeep("drying.mode_start_time", datetime.now().isoformat())
        self.data_store.setDeep("drying.currentDryMode", mode_name)
        self.data_store.setDeep("drying.isRunning", True)
        _LOGGER.warning(
            f"{self.name}: Started drying mode '{mode_name}' at {datetime.now()}"
        )

    async def handle_ElClassico(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Classic drying algorithm with temperature and humidity control.
        Direct event emission - no cooldowns or VPD logic interference.
        """
        _LOGGER.warning(f"{self.name} Run Drying 'El Classico'")
        tentData = self.data_store.get("tentData")
        
        _LOGGER.warning(f"{self.name}: tentData={tentData}")

        tempTolerance = 1
        humTolerance = 2
        finalActionMap = {}

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        _LOGGER.warning(f"{self.name}: current_phase={current_phase}")

        # Guard against missing sensor data
        raw_temp = tentData.get("temperature")
        raw_hum = tentData.get("humidity")
        if raw_temp is None or raw_hum is None:
            _LOGGER.warning(f"{self.name}: Missing temperature or humidity data, skipping control")
            return
        
        # CRITICAL FIX: Ensure numeric types for comparisons
        try:
            current_temp = float(raw_temp)
            current_hum = float(raw_hum)
        except (ValueError, TypeError) as e:
            _LOGGER.error(f"{self.name}: Invalid sensor data - temp={raw_temp} (type={type(raw_temp).__name__}), hum={raw_hum} (type={type(raw_hum).__name__}): {e}")
            return
        
        target_temp = current_phase.get("targetTemp")
        target_hum = current_phase.get("targetHumidity")
        
        if target_temp is None or target_hum is None:
            _LOGGER.error(f"{self.name}: Phase missing targetTemp or targetHumidity: {current_phase}")
            return
        
        try:
            target_temp = float(target_temp)
            target_hum = float(target_hum)
        except (ValueError, TypeError) as e:
            _LOGGER.error(f"{self.name}: Invalid phase targets - targetTemp={target_temp}, targetHumidity={target_hum}: {e}")
            return

        _LOGGER.warning(f"{self.name}: ElClassico CHECK - Current: {current_temp}°C / {current_hum}% | Phase targets: {target_temp}°C / {target_hum}%")

        # Check temperature independently
        temp_ok = abs(current_temp - target_temp) <= tempTolerance

        if not temp_ok:
            if current_temp < target_temp:
                _LOGGER.warning(f"{self.name}: ElClassico TEMP LOW - {current_temp}°C < {target_temp}°C → Heater ON, Cooler OFF")
                finalActionMap["Increase Heater"] = True
                finalActionMap["Reduce Exhaust"] = True
                finalActionMap["Reduce Cooler"] = True
                finalActionMap["Increase Ventilation"] = True
            else:
                _LOGGER.warning(f"{self.name}: ElClassico TEMP HIGH - {current_temp}°C > {target_temp}°C → Cooler ON, Heater OFF")
                finalActionMap["Increase Cooler"] = True
                finalActionMap["Increase Exhaust"] = True
                finalActionMap["Reduce Heater"] = True
                finalActionMap["Reduce Ventilation"] = True

        # Check humidity independently
        hum_ok = abs(current_hum - target_hum) <= humTolerance

        if not hum_ok:
            if current_hum < target_hum:
                _LOGGER.warning(f"{self.name}: ElClassico HUM LOW - {current_hum}% < {target_hum}% → Humidify ON")
                finalActionMap["Increase Humidifier"] = True
                finalActionMap["Reduce Dehumidifier"] = True
                finalActionMap["Reduce Exhaust"] = True
                finalActionMap["Increase Ventilation"] = True
            else:
                _LOGGER.warning(f"{self.name}: ElClassico HUM HIGH - {current_hum}% > {target_hum}% → Dehumidify ON")
                finalActionMap["Increase Dehumidifier"] = True
                finalActionMap["Reduce Humidifier"] = True
                finalActionMap["Increase Exhaust"] = True
                finalActionMap["Increase Ventilation"] = True

        # CRITICAL FIX: Prevent conflicting actions (cannot increase and reduce same device)
        conflict_pairs = [
            ("Increase Heater", "Reduce Heater"),
            ("Increase Cooler", "Reduce Cooler"),
            ("Increase Humidifier", "Reduce Humidifier"),
            ("Increase Dehumidifier", "Reduce Dehumidifier"),
            ("Increase Exhaust", "Reduce Exhaust"),
            ("Increase Ventilation", "Reduce Ventilation"),
        ]
        
        for increase_key, reduce_key in conflict_pairs:
            if increase_key in finalActionMap and reduce_key in finalActionMap:
                # Remove both - let the next cycle decide
                del finalActionMap[increase_key]
                del finalActionMap[reduce_key]
                _LOGGER.warning(f"{self.name}: Removed conflicting actions: {increase_key} + {reduce_key}")

        # Emit all actions directly
        if finalActionMap:
            _LOGGER.warning(f"{self.name}: ElClassico executing {len(finalActionMap)} actions: {list(finalActionMap.keys())}")
            for action_name in finalActionMap:
                await self.event_manager.emit(action_name, None)
        else:
            _LOGGER.warning(f"{self.name}: ElClassico - No actions needed, conditions within tolerance")

    async def handle_5DayDry(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Structured 5-day drying program with VPD-based control.
        Direct event emission - no cooldowns or VPD logic interference.
        """
        _LOGGER.warning(f"{self.name}: Run Drying '5DayDry'")
        tentData = self.data_store.get("tentData")
        capabilities = self.data_store.get("capabilities")
        vpdPub = self.data_store.get("vpd")

        currentVPD = vpdPub.get("current") if vpdPub else None

        if currentVPD is None:
            _LOGGER.warning(f"{self.name}: No current VPD data available")
            return

        current_temp = tentData.get("temperature")
        current_humidity = tentData.get("humidity")
        
        if current_temp is None or current_humidity is None:
            _LOGGER.warning(f"{self.name}: Missing sensor data for 5DayDry VPD calculation")
            return

        try:
            current_temp = float(current_temp)
            current_humidity = float(current_humidity)
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.name}: Invalid sensor data types for 5DayDry")
            return

        phaseVPD = calc_Dry5Days_vpd(current_temp, current_humidity)

        if phaseVPD is None:
            _LOGGER.warning(f"{self.name}: Could not calculate phase VPD")
            return

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        target_temp = current_phase.get("targetTemp")
        target_humidity = current_phase.get("targetHumidity")

        if target_temp is None or target_humidity is None:
            _LOGGER.error(f"{self.name}: Phase missing targetTemp or targetHumidity")
            return

        current_temp = tentData.get("temperature")
        current_humidity = tentData.get("humidity")
        
        if current_temp is None or current_humidity is None:
            _LOGGER.warning(f"{self.name}: Missing sensor data")
            return
            
        try:
            current_temp = float(current_temp)
            current_humidity = float(current_humidity)
            target_temp = float(target_temp)
            target_humidity = float(target_humidity)
            currentVPD = float(currentVPD)
            phaseVPD = float(phaseVPD)
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.name}: Invalid numeric data for 5DayDry")
            return

        tempTolerance = 1
        humTolerance = 2
        vpdTolerance = 0.1
        finalActionMap = {}

        _LOGGER.warning(f"{self.name}: 5DayDry CHECK - Temp: {current_temp}°C vs {target_temp}°C | Hum: {current_humidity}% vs {target_humidity}% | VPD: {currentVPD:.2f} vs {phaseVPD:.2f}")

        temp_ok = abs(current_temp - target_temp) <= tempTolerance
        hum_ok = abs(current_humidity - target_humidity) <= humTolerance
        vpd_ok = abs(currentVPD - phaseVPD) <= vpdTolerance

        if not temp_ok:
            if current_temp < target_temp:
                _LOGGER.warning(f"{self.name}: 5DayDry TEMP LOW")
                finalActionMap["Increase Heater"] = True
                finalActionMap["Reduce Cooler"] = True
            else:
                _LOGGER.warning(f"{self.name}: 5DayDry TEMP HIGH")
                finalActionMap["Increase Cooler"] = True
                finalActionMap["Reduce Heater"] = True

        if not hum_ok:
            if current_humidity < target_humidity:
                _LOGGER.warning(f"{self.name}: 5DayDry HUM LOW")
                finalActionMap["Increase Humidifier"] = True
                finalActionMap["Reduce Dehumidifier"] = True
            else:
                _LOGGER.warning(f"{self.name}: 5DayDry HUM HIGH")
                finalActionMap["Increase Dehumidifier"] = True
                finalActionMap["Reduce Humidifier"] = True

        if not vpd_ok:
            if currentVPD < phaseVPD:
                _LOGGER.warning(f"{self.name}: 5DayDry VPD LOW")
                finalActionMap["Increase Heater"] = True
                finalActionMap["Reduce Exhaust"] = True
            else:
                _LOGGER.warning(f"{self.name}: 5DayDry VPD HIGH")
                finalActionMap["Increase Cooler"] = True
                finalActionMap["Increase Exhaust"] = True

        # CRITICAL FIX: Prevent conflicting actions
        conflict_pairs = [
            ("Increase Heater", "Reduce Heater"),
            ("Increase Cooler", "Reduce Cooler"),
            ("Increase Humidifier", "Reduce Humidifier"),
            ("Increase Dehumidifier", "Reduce Dehumidifier"),
            ("Increase Exhaust", "Reduce Exhaust"),
            ("Increase Ventilation", "Reduce Ventilation"),
        ]
        
        for increase_key, reduce_key in conflict_pairs:
            if increase_key in finalActionMap and reduce_key in finalActionMap:
                del finalActionMap[increase_key]
                del finalActionMap[reduce_key]
                _LOGGER.warning(f"{self.name}: Removed conflicting actions: {increase_key} + {reduce_key}")

        if finalActionMap:
            _LOGGER.warning(f"{self.name}: 5DayDry executing {len(finalActionMap)} actions: {list(finalActionMap.keys())}")
            for action_name in finalActionMap:
                await self.event_manager.emit(action_name, None)
        else:
            _LOGGER.warning(f"{self.name}: 5DayDry - All conditions within tolerance")

    async def handle_DewBased(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Dew point-based drying with precise moisture control.
        Direct event emission - no cooldowns or VPD logic interference.
        """
        _LOGGER.warning(f"{self.name}: Run Drying 'DewBased'")
        tentData = self.data_store.get("tentData")

        # Get current sensor values with type safety
        raw_temp = tentData.get("temperature")
        raw_hum = tentData.get("humidity")
        raw_dew = tentData.get("dewpoint")

        if raw_temp is None or raw_hum is None or raw_dew is None:
            _LOGGER.warning(f"{self.name}: Missing sensor data for DewBased drying")
            return

        try:
            currenTemperature = float(raw_temp)
            currenHumidity = float(raw_hum)
            currentDewPoint = float(raw_dew)
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.name}: Invalid sensor data types for DewBased")
            return

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        target_temp = current_phase.get("targetTemp")
        target_humidity = current_phase.get("targetHumidity")

        if target_temp is None or target_humidity is None:
            _LOGGER.error(f"{self.name}: Phase missing targetTemp or targetHumidity")
            return

        try:
            target_temp = float(target_temp)
            target_humidity = float(target_humidity)
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.name}: Invalid phase targets for DewBased")
            return

        tempTolerance = 1
        humTolerance = 2
        dewPointTolerance = 1.5
        finalActionMap = {}

        _LOGGER.warning(f"{self.name}: DewBased CHECK - Temp: {currenTemperature}°C vs {target_temp}°C | Hum: {currenHumidity}% vs {target_humidity}% | Dew: {currentDewPoint:.1f}°C")

        temp_ok = abs(currenTemperature - target_temp) <= tempTolerance
        hum_ok = abs(currenHumidity - target_humidity) <= humTolerance

        # Dew point calculation
        dew_vpd_result = calc_dew_vpd(currenTemperature, currentDewPoint)
        dewpoint_vpd = dew_vpd_result.get("dewpoint_vpd")
        
        if dewpoint_vpd is None:
            _LOGGER.warning(f"{self.name}: Could not calculate dew point VPD")
            return
        
        # Calculate dew point difference (target dew point vs actual)
        target_dew_point = current_phase.get("targetDewPoint")
        if target_dew_point is not None:
            try:
                target_dew_point = float(target_dew_point)
                dew_diff = currentDewPoint - target_dew_point
            except (ValueError, TypeError):
                dew_diff = 0
        else:
            # Fallback: use VPD-based dew point logic
            dew_diff = dewpoint_vpd
        vp_low = currenHumidity < target_humidity - humTolerance
        vp_high = currenHumidity > target_humidity + humTolerance

        if not temp_ok:
            if currenTemperature < target_temp:
                _LOGGER.warning(f"{self.name}: DewBased TEMP LOW - {currenTemperature}°C < {target_temp}°C → Increase Heater")
                finalActionMap["Increase Heater"] = True
                finalActionMap["Reduce Cooler"] = True
            else:
                _LOGGER.warning(f"{self.name}: DewBased TEMP HIGH - {currenTemperature}°C > {target_temp}°C → Increase Cooler")
                finalActionMap["Increase Cooler"] = True
                finalActionMap["Reduce Heater"] = True

        if abs(dew_diff) > dewPointTolerance or vp_low or vp_high:
            if dew_diff < -dewPointTolerance or vp_low:
                _LOGGER.warning(f"{self.name}: DewBased DEW LOW - Too dry → Humidify")
                finalActionMap["Increase Humidifier"] = True
                finalActionMap["Reduce Dehumidifier"] = True
                finalActionMap["Reduce Exhaust"] = True
                finalActionMap["Increase Ventilation"] = True
            elif dew_diff > dewPointTolerance or vp_high:
                _LOGGER.warning(f"{self.name}: DewBased DEW HIGH - Too humid → Dehumidify")
                finalActionMap["Increase Dehumidifier"] = True
                finalActionMap["Reduce Humidifier"] = True
                finalActionMap["Increase Exhaust"] = True
                finalActionMap["Increase Ventilation"] = True

        # CRITICAL FIX: Prevent conflicting actions
        conflict_pairs = [
            ("Increase Heater", "Reduce Heater"),
            ("Increase Cooler", "Reduce Cooler"),
            ("Increase Humidifier", "Reduce Humidifier"),
            ("Increase Dehumidifier", "Reduce Dehumidifier"),
            ("Increase Exhaust", "Reduce Exhaust"),
            ("Increase Ventilation", "Reduce Ventilation"),
        ]
        
        for increase_key, reduce_key in conflict_pairs:
            if increase_key in finalActionMap and reduce_key in finalActionMap:
                del finalActionMap[increase_key]
                del finalActionMap[reduce_key]
                _LOGGER.warning(f"{self.name}: Removed conflicting actions: {increase_key} + {reduce_key}")

        if finalActionMap:
            _LOGGER.warning(f"{self.name}: DewBased executing {len(finalActionMap)} actions: {list(finalActionMap.keys())}")
            for action_name in finalActionMap:
                await self.event_manager.emit(action_name, None)
        else:
            _LOGGER.warning(f"{self.name}: DewBased - All conditions within tolerance")

    async def handle_OwnDry(self) -> None:
        """
        OwnDry mode - continuous min/max control.
        Uses min/max values from controlOptionData.minmax.
        Direct event emission - no cooldowns or VPD logic interference.
        """
        _LOGGER.warning(f"{self.name}: Run Drying 'OwnDry'")
        tentData = self.data_store.get("tentData")
        
        # Get min/max values from controlOptionData.minmax
        minmax_data = self.data_store.getDeep("controlOptionData.minmax") or {}
        
        min_temp = minmax_data.get("minTemp")
        max_temp = minmax_data.get("maxTemp")
        min_hum = minmax_data.get("minHum")
        max_hum = minmax_data.get("maxHum")
        
        if None in (min_temp, max_temp, min_hum, max_hum):
            _LOGGER.error(f"{self.name}: OwnDry missing min/max values: {minmax_data}")
            return
        
        try:
            min_temp = float(min_temp)
            max_temp = float(max_temp)
            min_hum = float(min_hum)
            max_hum = float(max_hum)
            current_temp = float(tentData.get("temperature", 0))
            current_hum = float(tentData.get("humidity", 0))
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.name}: OwnDry invalid numeric values")
            return
        
        target_temp = (min_temp + max_temp) / 2
        target_hum = (min_hum + max_hum) / 2
        tempTolerance = 1
        humTolerance = 2
        finalActionMap = {}

        _LOGGER.warning(f"{self.name}: OwnDry CHECK - Current: {current_temp}°C / {current_hum}% | Midpoints: {target_temp}°C / {target_hum}% | Limits: Temp {min_temp}-{max_temp}°C | Hum {min_hum}-{max_hum}%")

        # Temperature control
        if abs(current_temp - target_temp) > tempTolerance:
            if current_temp < target_temp:
                _LOGGER.warning(f"{self.name}: OwnDry TEMP LOW → Heater ON")
                finalActionMap["Increase Heater"] = True
                finalActionMap["Reduce Cooler"] = True
            else:
                _LOGGER.warning(f"{self.name}: OwnDry TEMP HIGH → Cooler ON")
                finalActionMap["Increase Cooler"] = True
                finalActionMap["Reduce Heater"] = True

        # Humidity control
        if abs(current_hum - target_hum) > humTolerance:
            if current_hum < target_hum:
                _LOGGER.warning(f"{self.name}: OwnDry HUM LOW → Humidify ON")
                finalActionMap["Increase Humidifier"] = True
                finalActionMap["Reduce Dehumidifier"] = True
            else:
                _LOGGER.warning(f"{self.name}: OwnDry HUM HIGH → Dehumidify ON")
                finalActionMap["Increase Dehumidifier"] = True
                finalActionMap["Reduce Humidifier"] = True

        # CRITICAL FIX: Prevent conflicting actions
        conflict_pairs = [
            ("Increase Heater", "Reduce Heater"),
            ("Increase Cooler", "Reduce Cooler"),
            ("Increase Humidifier", "Reduce Humidifier"),
            ("Increase Dehumidifier", "Reduce Dehumidifier"),
            ("Increase Exhaust", "Reduce Exhaust"),
            ("Increase Ventilation", "Reduce Ventilation"),
        ]
        
        for increase_key, reduce_key in conflict_pairs:
            if increase_key in finalActionMap and reduce_key in finalActionMap:
                del finalActionMap[increase_key]
                del finalActionMap[reduce_key]
                _LOGGER.warning(f"{self.name}: Removed conflicting actions: {increase_key} + {reduce_key}")

        if finalActionMap:
            _LOGGER.warning(f"{self.name}: OwnDry executing {len(finalActionMap)} actions: {list(finalActionMap.keys())}")
            for action_name in finalActionMap:
                await self.event_manager.emit(action_name, None)
        else:
            _LOGGER.warning(f"{self.name}: OwnDry - Conditions within tolerance")

    def get_current_phase(self, phaseConfig: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Determine the current drying phase based on elapsed time.
        
        Supports the DataStore structure with phases as:
        - "start", "halfTime", "endTime" keys with durationHours
        """
        if not phaseConfig:
            _LOGGER.debug(f"{self.name}: get_current_phase - phaseConfig is None or empty")
            return None

        mode_start_time = self.data_store.getDeep("drying.mode_start_time")
        if mode_start_time is None:
            _LOGGER.debug(f"{self.name}: get_current_phase - mode_start_time is None")
            return None

        _LOGGER.debug(f"{self.name}: get_current_phase - mode_start_time={mode_start_time}")

        mode_start_time_dt = datetime.fromisoformat(mode_start_time) if isinstance(mode_start_time, str) else mode_start_time
        elapsed_seconds = (datetime.now() - mode_start_time_dt).total_seconds()
        _LOGGER.debug(f"{self.name}: get_current_phase - elapsed_seconds={elapsed_seconds:.0f}")

        # Check for phases under "phase" key (new structure)
        phases_dict = phaseConfig.get("phase", {})
        _LOGGER.debug(f"{self.name}: get_current_phase - phases_dict={phases_dict}")
        if not phases_dict:
            # Fallback to old "phases" array structure
            phases_list = phaseConfig.get("phases", [])
            for phase in phases_list:
                start_time = phase.get("startTime", 0)
                end_time = phase.get("endTime", float('inf'))
                if start_time <= elapsed_seconds < end_time:
                    return phase
            return None

        # New structure: phases are "start", "halfTime", "endTime" with durationHours
        phase_order = ["start", "halfTime", "endTime"]
        accumulated_time = 0

        for phase_name in phase_order:
            if phase_name not in phases_dict:
                continue

            phase = phases_dict[phase_name]
            duration_hours = phase.get("durationHours", 0)
            duration_seconds = duration_hours * 3600

            if accumulated_time <= elapsed_seconds < accumulated_time + duration_seconds:
                # Add timing info to the phase
                phase["phase_name"] = phase_name
                return phase

            accumulated_time += duration_seconds

        return None

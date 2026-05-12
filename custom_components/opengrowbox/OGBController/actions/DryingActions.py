"""
OpenGrowBox Drying Actions

Handles all drying mode operations including ElClassico, 5DayDry, and DewBased algorithms.
"""

import math
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from ..utils.calcs import calc_Dry5Days_vpd, calc_dew_vpd
from ..data.OGBDataClasses.OGBPublications import OGBActionPublication

_LOGGER = logging.getLogger(__name__)


class DryingActions:
    """Handles drying mode operations and algorithms."""

    def __init__(self, data_store, event_manager, action_manager, room: str):
        """Initialize drying actions."""
        self.data_store = data_store
        self.event_manager = event_manager
        self.action_manager = action_manager
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
        Uses ActionManager for proper cleanup.
        """
        _LOGGER.info(f"{self.name}: Cleaning up drying devices")
        
        action_map = [
            OGBActionPublication(
                Name=self.room, capability="canHeat", action="Reduce",
                message="Drying cleanup: Stop heater", priority="medium"
            ),
            OGBActionPublication(
                Name=self.room, capability="canCool", action="Reduce",
                message="Drying cleanup: Stop cooler", priority="medium"
            ),
            OGBActionPublication(
                Name=self.room, capability="canHumidify", action="Reduce",
                message="Drying cleanup: Stop humidifier", priority="medium"
            ),
            OGBActionPublication(
                Name=self.room, capability="canDehumidify", action="Reduce",
                message="Drying cleanup: Stop dehumidifier", priority="medium"
            ),
            OGBActionPublication(
                Name=self.room, capability="canExhaust", action="Reduce",
                message="Drying cleanup: Reduce exhaust", priority="low"
            ),
            OGBActionPublication(
                Name=self.room, capability="canVentilate", action="Reduce",
                message="Drying cleanup: Reduce ventilation", priority="low"
            ),
        ]
        
        if self.action_manager:
            await self.action_manager.checkLimitsAndPublicateNoVPD(action_map)
        else:
            # Fallback to direct events if no action_manager available
            for action in ["Reduce Heater", "Reduce Cooler", "Reduce Humidifier", 
                          "Reduce Dehumidifier", "Reduce Exhaust", "Reduce Ventilation"]:
                await self.event_manager.emit(action, None)
    
    def start_drying_mode(self, mode_name: str) -> None:
        """
        Initialize a drying mode and store the start timestamp.
        """
        self.data_store.setDeep("drying.mode_start_time", datetime.now())
        self.data_store.setDeep("drying.currentDryMode", mode_name)
        self.data_store.setDeep("drying.isRunning", True)
        _LOGGER.warning(
            f"{self.name}: Started drying mode '{mode_name}' at {datetime.now()}"
        )

    async def handle_ElClassico(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Classic drying algorithm with temperature and humidity control.
        Uses ActionManager for proper action processing (conflict resolution, cooldown, etc.)
        """
        _LOGGER.warning(f"{self.name} Run Drying 'El Classico'")
        tentData = self.data_store.get("tentData")
        
        _LOGGER.warning(f"{self.name}: tentData={tentData}")

        tempTolerance = 1
        humTolerance = 2
        action_map: List[OGBActionPublication] = []

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        _LOGGER.warning(f"{self.name}: current_phase={current_phase}")

        # Check temperature independently
        temp_ok = (
            abs(tentData["temperature"] - current_phase["targetTemp"]) <= tempTolerance
        )

        if not temp_ok:
            if tentData["temperature"] < current_phase["targetTemp"]:
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canHeat", action="Increase",
                    message="ElClassico: Temp too low", priority="high"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canExhaust", action="Reduce",
                    message="ElClassico: Retain heat", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canCool", action="Reduce",
                    message="ElClassico: Stop cooling", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canVentilate", action="Increase",
                    message="ElClassico: Circulate warm air", priority="low"
                ))
            else:
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canCool", action="Increase",
                    message="ElClassico: Temp too high", priority="high"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canExhaust", action="Increase",
                    message="ElClassico: Remove hot air", priority="high"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canHeat", action="Reduce",
                    message="ElClassico: Stop heating", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canVentilate", action="Reduce",
                    message="ElClassico: Reduce circulation", priority="low"
                ))

        # Check humidity independently (not nested under temperature check)
        if (
            abs(tentData["humidity"] - current_phase["targetHumidity"])
            > humTolerance
        ):
            if tentData["humidity"] < current_phase["targetHumidity"]:
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canHumidify", action="Increase",
                    message="ElClassico: Humidity too low", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canVentilate", action="Increase",
                    message="ElClassico: Distribute humidity", priority="low"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canExhaust", action="Reduce",
                    message="ElClassico: Retain moisture", priority="low"
                ))
            else:
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canDehumidify", action="Increase",
                    message="ElClassico: Humidity too high", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canExhaust", action="Increase",
                    message="ElClassico: Remove moist air", priority="high"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canVentilate", action="Increase",
                    message="ElClassico: Air exchange", priority="medium"
                ))

        _LOGGER.warning(f"{self.name}: ElClassico action_map={len(action_map)} actions")

        # Use ActionManager for proper processing (conflicts, cooldown, Environment Guard)
        if action_map and self.action_manager:
            await self.action_manager.checkLimitsAndPublicateNoVPD(action_map)
        elif not action_map:
            _LOGGER.debug(f"{self.name}: ElClassico - No actions needed, conditions within tolerance")

    async def handle_5DayDry(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Structured 5-day drying program with VPD-based control.
        Uses ActionManager for proper action processing.
        """
        _LOGGER.debug(f"{self.name} Run Drying '5 Day Dry'")

        tentData = self.data_store.get("tentData")
        vpdTolerance = float(self.data_store.getDeep("vpd.tolerance") or 3) / 100.0
        capabilities = self.data_store.getDeep("capabilities")

        _LOGGER.debug(f"{self.name}: 5DayDry phaseConfig keys = {list(phaseConfig.keys()) if phaseConfig else None}")

        current_phase = self.get_current_phase(phaseConfig)

        _LOGGER.debug(f"{self.name}: 5DayDry current_phase = {current_phase}")

        if current_phase is None:
            mode_start_time = self.data_store.getDeep("drying.mode_start_time")
            phase_data = self.data_store.getDeep(f"drying.modes.5DayDry")
            _LOGGER.error(f"{self.name}: Could not determine current phase - mode_start_time={mode_start_time}, phaseConfig={phaseConfig}, 5DayDry_config={phase_data}")
            return

        current_temp = tentData["temperature"] if "temperature" in tentData else None
        current_humidity = tentData["humidity"] if "humidity" in tentData else None

        if current_temp is None or current_humidity is None:
            _LOGGER.warning(f"{self.room}: Missing tentData values for VPD calculation")
            return

        if isinstance(tentData["temperature"], (list, tuple)):
            temp_value = sum(tentData["temperature"]) / len(tentData["temperature"])
        else:
            temp_value = tentData["temperature"]

        Dry5DaysVPD = calc_Dry5Days_vpd(temp_value, current_humidity)
        self.data_store.setDeep("drying.5DayDryVPD", Dry5DaysVPD)

        target_vpd = current_phase.get("targetVPD")
        if target_vpd is None:
            _LOGGER.error(f"{self.room}: Current phase has no targetVPD key")
            return

        delta = Dry5DaysVPD - target_vpd

        # Get temperature and humidity targets from phase config
        target_temp = current_phase.get("targetTemp")
        target_humidity = current_phase.get("targetHumidity")
        max_temp = current_phase.get("maxTemp")
        
        tempTolerance = 1.0
        humTolerance = 2.0
        
        action_map: List[OGBActionPublication] = []
        
        # Check VPD control (use semantic events which route through ActionManager)
        if abs(delta) > vpdTolerance:
            if delta < 0:
                _LOGGER.debug(
                    f"{self.room}: Dry5Days VPD {Dry5DaysVPD:.2f} < Target {target_vpd:.2f} → Increase VPD"
                )
                await self.event_manager.emit("increase_vpd", capabilities)
            else:
                _LOGGER.debug(
                    f"{self.room}: Dry5Days VPD {Dry5DaysVPD:.2f} > Target {target_vpd:.2f} → Reduce VPD"
                )
                await self.event_manager.emit("reduce_vpd", capabilities)
        
        # Check temperature control independently
        if target_temp is not None and current_temp is not None:
            if abs(current_temp - target_temp) > tempTolerance:
                if current_temp < target_temp:
                    _LOGGER.debug(f"{self.room}: 5DayDry Temp {current_temp}°C < Target {target_temp}°C → Increase Heat")
                    action_map.append(OGBActionPublication(
                        Name=self.room, capability="canHeat", action="Increase",
                        message="5DayDry: Temp too low", priority="high"
                    ))
                elif max_temp is not None and current_temp > max_temp:
                    _LOGGER.debug(f"{self.room}: 5DayDry Temp {current_temp}°C > Max {max_temp}°C → Increase Cooler")
                    action_map.append(OGBActionPublication(
                        Name=self.room, capability="canCool", action="Increase",
                        message="5DayDry: Temp too high", priority="high"
                    ))
        
        # Check humidity control independently
        if target_humidity is not None and current_humidity is not None:
            if abs(current_humidity - target_humidity) > humTolerance:
                if current_humidity < target_humidity:
                    _LOGGER.debug(f"{self.room}: 5DayDry Humidity {current_humidity}% < Target {target_humidity}% → Increase Humidifier")
                    action_map.append(OGBActionPublication(
                        Name=self.room, capability="canHumidify", action="Increase",
                        message="5DayDry: Humidity too low", priority="medium"
                    ))
                else:
                    _LOGGER.debug(f"{self.room}: 5DayDry Humidity {current_humidity}% > Target {target_humidity}% → Increase Dehumidifier")
                    action_map.append(OGBActionPublication(
                        Name=self.room, capability="canDehumidify", action="Increase",
                        message="5DayDry: Humidity too high", priority="medium"
                    ))
        
        # Use ActionManager for proper processing
        if action_map and self.action_manager:
            await self.action_manager.checkLimitsAndPublicateNoVPD(action_map)
        elif not action_map and abs(delta) <= vpdTolerance:
            _LOGGER.debug(
                f"{self.room}: Dry5Days VPD {Dry5DaysVPD:.2f} within tolerance (±{vpdTolerance*100:.1f}%) → No action"
            )

    async def handle_DewBased(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Dew point based drying algorithm.
        Uses ActionManager for proper action processing.
        """
        _LOGGER.debug(f"{self.name}: Run Drying 'Dew Based'")

        tentData = self.data_store.get("tentData")
        currentDewPoint = tentData.get("dewpoint")
        currenTemperature = tentData.get("temperature")

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        action_map: List[OGBActionPublication] = []

        # Check temperature control independently
        target_temp = current_phase.get("targetTemp")
        tempTolerance = 1.0
        
        if target_temp is not None and currenTemperature is not None:
            if abs(currenTemperature - target_temp) > tempTolerance:
                if currenTemperature < target_temp:
                    _LOGGER.debug(f"{self.room}: DewBased Temp {currenTemperature}°C < Target {target_temp}°C → Increase Heater")
                    action_map.append(OGBActionPublication(
                        Name=self.room, capability="canHeat", action="Increase",
                        message="DewBased: Temp too low", priority="high"
                    ))
                else:
                    _LOGGER.debug(f"{self.room}: DewBased Temp {currenTemperature}°C > Target {target_temp}°C → Increase Cooler")
                    action_map.append(OGBActionPublication(
                        Name=self.room, capability="canCool", action="Increase",
                        message="DewBased: Temp too high", priority="high"
                    ))

        dewPointTolerance = 0.5
        dew_vps = calc_dew_vpd(currenTemperature, currentDewPoint)

        vaporPressureActual = dew_vps.get("vapor_pressure_actual")
        vaporPressureSaturation = dew_vps.get("vapor_pressure_saturation")

        self.data_store.setDeep("drying.vaporPressureActual", vaporPressureActual)
        self.data_store.setDeep(
            "drying.vaporPressureSaturation", vaporPressureSaturation
        )

        if (
            currentDewPoint is None
            or not isinstance(currentDewPoint, (int, float))
            or math.isnan(currentDewPoint)
        ):
            _LOGGER.warning(
                f"{self.name}: Current Dew Point is unavailable or invalid."
            )
            return

        targetDewPoint = current_phase.get("targetDewPoint")
        if targetDewPoint is None:
            _LOGGER.error(f"{self.name}: Current phase has no targetDewPoint key")
            return

        dew_diff = currentDewPoint - targetDewPoint
        vp_low = (
            vaporPressureActual < 0.9 * vaporPressureSaturation
            if vaporPressureActual and vaporPressureSaturation
            else False
        )
        vp_high = (
            vaporPressureActual > 1.1 * vaporPressureSaturation
            if vaporPressureActual and vaporPressureSaturation
            else False
        )

        if abs(dew_diff) > dewPointTolerance or vp_low or vp_high:
            if dew_diff < -dewPointTolerance or vp_low:
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canHumidify", action="Increase",
                    message="DewBased: Too dry", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canDehumidify", action="Reduce",
                    message="DewBased: Too dry", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canExhaust", action="Reduce",
                    message="DewBased: Too dry - retain moisture", priority="low"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canVentilate", action="Increase",
                    message="DewBased: Too dry - circulate air", priority="low"
                ))
                _LOGGER.debug(
                    f"{self.room}: Too dry. Humidify ↑, Dehumidifier ↓, Exhaust ↓, Ventilation ↑"
                )
            elif dew_diff > dewPointTolerance or vp_high:
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canDehumidify", action="Increase",
                    message="DewBased: Too humid", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canHumidify", action="Reduce",
                    message="DewBased: Too humid", priority="medium"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canExhaust", action="Increase",
                    message="DewBased: Too humid - remove moist air", priority="high"
                ))
                action_map.append(OGBActionPublication(
                    Name=self.room, capability="canVentilate", action="Increase",
                    message="DewBased: Too humid - air exchange", priority="medium"
                ))
                _LOGGER.debug(
                    f"{self.room}: Too humid. Dehumidify ↑, Humidifier ↓, Exhaust ↑, Ventilation ↑"
                )
        else:
            # Within tolerance - reduce all devices (cleanup)
            action_map.append(OGBActionPublication(
                Name=self.room, capability="canHumidify", action="Reduce",
                message="DewBased: Within tolerance", priority="low"
            ))
            action_map.append(OGBActionPublication(
                Name=self.room, capability="canDehumidify", action="Reduce",
                message="DewBased: Within tolerance", priority="low"
            ))
            action_map.append(OGBActionPublication(
                Name=self.room, capability="canExhaust", action="Reduce",
                message="DewBased: Within tolerance", priority="low"
            ))
            action_map.append(OGBActionPublication(
                Name=self.room, capability="canVentilate", action="Reduce",
                message="DewBased: Within tolerance", priority="low"
            ))
            _LOGGER.debug(
                f"{self.room}: Dew Point {currentDewPoint:.2f} within ±{dewPointTolerance} → All systems idle"
            )
        
        # Use ActionManager for proper processing
        if action_map and self.action_manager:
            await self.action_manager.checkLimitsAndPublicateNoVPD(action_map)

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
        last_phase = None
        last_phase_name = None

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
            last_phase = phase
            last_phase_name = phase_name

        # If elapsed time exceeds all phases, stay in the final phase indefinitely
        if last_phase is not None:
            last_phase["phase_name"] = last_phase_name
            _LOGGER.info(
                f"{self.name}: Drying mode duration exceeded, staying in final phase '{last_phase_name}'"
            )
            return last_phase

        return None
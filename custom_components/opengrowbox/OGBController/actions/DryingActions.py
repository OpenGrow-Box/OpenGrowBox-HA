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

    async def handle_drying(self) -> Optional[Dict[str, Any]]:
        """
        Main drying mode dispatcher.
        Routes to specific drying algorithms based on current mode.
        """
        currentDryMode = self.data_store.getDeep("drying.currentDryMode")

        # Check if start time exists, if not set it
        mode_start_time = self.data_store.getDeep("drying.mode_start_time")
        if mode_start_time is None and currentDryMode != "NO-Dry":
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
            _LOGGER.debug(f"{self.name} Unknown DryMode Received")
            return None

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
        """
        _LOGGER.warning(f"{self.name} Run Drying 'El Classico'")
        tentData = self.data_store.get("tentData")

        tempTolerance = 1
        humTolerance = 2
        finalActionMap = {}

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        temp_ok = (
            abs(tentData["temperature"] - current_phase["targetTemp"]) <= tempTolerance
        )

        if not temp_ok:
            if tentData["temperature"] < current_phase["targetTemp"]:
                finalActionMap["Increase Heater"] = True
                finalActionMap["Reduce Exhaust"] = True
                finalActionMap["Reduce Cooler"] = True
                finalActionMap["Increase Ventilation"] = True
            else:
                finalActionMap["Increase Cooler"] = True
                finalActionMap["Increase Exhaust"] = True
                finalActionMap["Reduce Heater"] = True
                finalActionMap["Reduce Ventilation"] = True
        else:
            if (
                abs(tentData["humidity"] - current_phase["targetHumidity"])
                > humTolerance
            ):
                if tentData["humidity"] < current_phase["targetHumidity"]:
                    finalActionMap["Increase Humidifier"] = True
                    finalActionMap["Increase Ventilation"] = True
                    finalActionMap["Reduce Exhaust"] = True
                else:
                    finalActionMap["Increase Dehumidifier"] = True
                    finalActionMap["Increase Ventilation"] = True
                    finalActionMap["Increase Exhaust"] = True

        # Emit all actions in the map
        for action in finalActionMap.keys():
            await self.event_manager.emit(action, None)

        # Send summary to client
        await self.event_manager.emit("LogForClient", finalActionMap, haEvent=True)

    async def handle_5DayDry(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Structured 5-day drying program with VPD-based control.
        """
        _LOGGER.debug(f"{self.name} Run Drying '5 Day Dry'")

        tentData = self.data_store.get("tentData")
        vpdTolerance = self.data_store.get("vpd.tolerance") or 3  # %
        capabilities = self.data_store.getDeep("capabilities")

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
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
        else:
            _LOGGER.debug(
                f"{self.room}: Dry5Days VPD {Dry5DaysVPD:.2f} within tolerance (±{vpdTolerance}) → No action"
            )

    async def handle_DewBased(self, phaseConfig: Dict[str, Any]) -> None:
        """
        Dew point based drying algorithm.
        """
        _LOGGER.debug(f"{self.name}: Run Drying 'Dew Based'")

        tentData = self.data_store.get("tentData")
        currentDewPoint = tentData.get("dewpoint")
        currenTemperature = tentData.get("temperature")

        dewPointTolerance = 0.5
        dew_vps = calc_dew_vpd(currenTemperature, currentDewPoint)

        vaporPressureActual = dew_vps.get("vapor_pressure_actual")
        vaporPressureSaturation = dew_vps.get("vapor_pressure_saturation")

        self.data_store.setDeep("drying.vaporPressureActual", vaporPressureActual)
        self.data_store.setDeep(
            "drying.vaporPressureSaturation", vaporPressureSaturation
        )

        current_phase = self.get_current_phase(phaseConfig)

        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

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
                await self.event_manager.emit("Increase Humidifier", None)
                await self.event_manager.emit("Reduce Dehumidifier", None)
                await self.event_manager.emit("Reduce Exhaust", None)
                await self.event_manager.emit("Increase Ventilation", None)
                _LOGGER.debug(
                    f"{self.room}: Too dry. Humidify ↑, Dehumidifier ↓, Exhaust ↓, Ventilation ↑"
                )
            elif dew_diff > dewPointTolerance or vp_high:
                await self.event_manager.emit("Increase Dehumidifier", None)
                await self.event_manager.emit("Reduce Humidifier", None)
                await self.event_manager.emit("Increase Exhaust", None)
                await self.event_manager.emit("Increase Ventilation", None)
                _LOGGER.debug(
                    f"{self.room}: Too humid. Dehumidify ↑, Humidifier ↓, Exhaust ↑, Ventilation ↑"
                )
        else:
            await self.event_manager.emit("Reduce Humidifier", None)
            await self.event_manager.emit("Reduce Dehumidifier", None)
            await self.event_manager.emit("Reduce Exhaust", None)
            await self.event_manager.emit("Reduce Ventilation", None)
            _LOGGER.debug(
                f"{self.room}: Dew Point {currentDewPoint:.2f} within ±{dewPointTolerance} → All systems idle"
            )

    def get_current_phase(self, phaseConfig: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Determine the current drying phase based on elapsed time.
        """
        if not phaseConfig:
            return None

        mode_start_time = self.data_store.getDeep("drying.mode_start_time")
        if mode_start_time is None:
            return None

        elapsed_seconds = (datetime.now() - mode_start_time).total_seconds()

        for phase in phaseConfig.get("phases", []):
            start_time = phase.get("startTime", 0)
            end_time = phase.get("endTime", float('inf'))

            if start_time <= elapsed_seconds < end_time:
                return phase

        return None
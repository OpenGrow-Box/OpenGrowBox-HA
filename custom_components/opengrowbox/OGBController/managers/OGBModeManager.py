import asyncio
import logging
from datetime import datetime

from ..actions.DryingActions import DryingActions
from ..data.OGBDataClasses.OGBPublications import (OGBCropSteeringPublication,
                                             OGBDripperAction, OGBECAction,
                                             OGBHydroAction,
                                             OGBHydroPublication,
                                             OGBModePublication,
                                             OGBModeRunPublication,
                                             OGBRetrieveAction,
                                             OGBRetrivePublication)
from ..premium.analytics.OGBAIDataBridge import OGBAIDataBridge
from .hydro.crop_steering.OGBCSManager import OGBCSManager
from .ClosedEnvironmentManager import ClosedEnvironmentManager
from .OGBScriptMode import OGBScriptMode

_LOGGER = logging.getLogger(__name__)


class OGBModeManager:
    def __init__(self, hass, dataStore, event_manager, room):
        self.name = "OGB Mode Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = event_manager
        self.isInitialized = False

        self.CropSteeringManager = OGBCSManager(hass, dataStore, self.event_manager, room)

        # Closed Environment Manager for ambient-enhanced control
        self.closedEnvironmentManager = ClosedEnvironmentManager(dataStore, self.event_manager, room, hass)

        # Drying Actions for drying mode handling
        self.dryingActions = DryingActions(dataStore, self.event_manager, room)

        # Script Mode Manager for custom user scripts
        self.scriptModeManager: OGBScriptMode | None = None

        # AI Data Bridge for cropsteering learning integration
        # Will be started when premium control is active
        self.aiDataBridge = OGBAIDataBridge(hass, self.event_manager, dataStore, room)
        self._ai_bridge_started = False

        self.currentMode = None
        self._hydro_task: asyncio.Task | None = None
        self._retrive_task: asyncio.Task | None = None
        self._crop_steering_task: asyncio.Task | None = None
        self._plant_watering_task: asyncio.Task | None = None
        
        # Deadband hold tracking for smart deadband (5 minutes hold time)
        self._deadband_hold_start: float | None = None
        self._deadband_hold_duration: float = 300  # 5 minutes (300 seconds)
        self._deadband_active_devices: set = set()  # Track which devices are in deadband hold
        self._is_in_deadband: bool = False
        
        # NEW: Advanced deadband tracking
        self._deadband_stability_start: float | None = None  # When VPD became stable
        self._deadband_stability_duration: float = 120  # 2 minutes for full reduction
        self._vpd_history: list = []  # Last 3 VPD values for trend analysis
        self._deadband_check_interval: float = 30  # Check every 30 seconds during deadband
        self._last_deadband_check: float = 0
        self._current_deadband_stage: int = 0  # 0=none, 1=soft, 2=medium, 3=full
        
        # Hysteres for deadband stability (211% above deadband to exit - prevents oscillation)
        self._deadband_hysteresis_factor: float = 3.11  # Exit at 311% of deadband
        self._deadband_exit_threshold: float = 0.0  # Calculated dynamically
        self._deadband_last_exit_time: float | None = None  # Timestamp of last exit
        self._deadband_min_hold_after_exit: float = 120  # Max 2 minutes hold after exit (in seconds)
        
        # Device categories for deadband handling
        self._deadband_devices = {
            # Climate-Geräte
            "canHumidify", "canDehumidify", "canHeat", "canCool", "canClimate",
            # Ventilations-Geräte die Außenluft bringen (beeinflussen VPD!)
            "canExhaust", "canIntake", "canWindow"
        }
        # Ventilation (Umluft) wird vom Deadband ignoriert (regelt Mikroklima)
        self._ventilation_devices = {"canVentilate"}

        ## Events
        self.event_manager.on("selectActionMode", self.selectActionMode)

        # Prem
        self.event_manager.on("PremiumCheck", self.handle_premium_modes)

    def _calculate_dynamic_deadband(self, mode_name: str) -> float:
        """
        Calculate dynamic deadband based on plant stage and mode.
        
        VPD Perfection: Uses plant stage specific deadbands
        VPD Target: Uses tolerance-based deadband
        Closed Environment: Uses fixed deadband
        
        Returns:
            Dynamic deadband value in kPa
        """
        # Base deadband from settings
        base_deadband = 0.05
        
        if mode_name == "VPD Perfection":
            # Get current plant stage
            plant_stage = self.data_store.get("plantStage") or "MidVeg"
            
            # Plant stage specific deadbands
            stage_deadbands = {
                "Germination": 0.03,  # Very sensitive phase
                "Clones": 0.03,       # Very sensitive phase
                "EarlyVeg": 0.05,     # Normal
                "MidVeg": 0.05,       # Normal
                "LateVeg": 0.04,      # More precise (transition to flower)
                "EarlyFlower": 0.04,  # More precise
                "MidFlower": 0.05,    # Normal
                "LateFlower": 0.05,   # Normal
            }
            
            return stage_deadbands.get(plant_stage, base_deadband)
            
        elif mode_name == "VPD Target":
            # Use tolerance from settings
            tolerance = self.data_store.getDeep("controlOptionData.deadband.vpdTargetDeadband")
            if tolerance:
                return float(tolerance)
            return base_deadband
            
        elif mode_name == "Closed Environment":
            # Use fixed deadband for closed environment
            return base_deadband
            
        return base_deadband
    
    def _update_deadband_thresholds(self, deadband: float):
        """
        Update deadband exit threshold with hysteresis.
        
        Args:
            deadband: Current deadband value in kPa
        """
        self._deadband_exit_threshold = deadband * self._deadband_hysteresis_factor
        self.data_store.setDeep("controlOptionData.deadband.exit_threshold", self._deadband_exit_threshold)
        self.data_store.setDeep("controlOptionData.deadband.hysteresis_factor", self._deadband_hysteresis_factor)
        _LOGGER.debug(
            f"{self.room}: Deadband hysteresis updated - enter: ±{deadband:.3f}, "
            f"exit: >{self._deadband_exit_threshold:.3f} (factor: {self._deadband_hysteresis_factor})"
        )
    
    def _calculate_trend(self, current_vpd: float) -> str:
        """
        Calculate VPD trend based on last 3 values.
        
        Returns:
            'towards_target', 'away_from_target', or 'stable'
        """
        import time
        
        now = time.time()
        
        # Add current value to history
        self._vpd_history.append({"vpd": current_vpd, "time": now})
        
        # Keep only last 3 values
        if len(self._vpd_history) > 3:
            self._vpd_history = self._vpd_history[-3:]
        
        # Need at least 2 values for trend
        if len(self._vpd_history) < 2:
            return "stable"
        
        # Calculate trend
        vpd_values = [entry["vpd"] for entry in self._vpd_history]
        target_vpd = self.data_store.getDeep("vpd.target") or self.data_store.getDeep("vpd.perfection") or 1.2
        
        # Check if getting closer to target
        current_deviation = abs(vpd_values[-1] - target_vpd)
        previous_deviation = abs(vpd_values[0] - target_vpd)
        
        if current_deviation < previous_deviation * 0.9:  # At least 10% improvement
            return "towards_target"
        elif current_deviation > previous_deviation * 1.1:  # At least 10% worse
            return "away_from_target"
        else:
            return "stable"
    
    def _determine_deadband_stage(self, deviation: float, deadband: float, trend: str) -> int:
        """
        Determine deadband reduction stage based on deviation and trend.
        
        Stage 1 (Soft):   Slight reduction (50% for climate, 75% for air-exchange)
        Stage 2 (Medium): Moderate reduction (25% for climate, 50% for air-exchange)
        Stage 3 (Full):   Maximum reduction (10% for climate, 25% for air-exchange)
        
        Args:
            deviation: Current VPD deviation from target
            deadband: Current deadband value
            trend: VPD trend ('towards_target', 'away_from_target', 'stable')
            
        Returns:
            Stage (1, 2, or 3)
        """
        import time
        
        # Calculate relative deviation (0.0 to 1.0 where 1.0 is at deadband limit)
        relative_deviation = deviation / deadband if deadband > 0 else 0
        
        # Trend-based adjustments
        if trend == "towards_target":
            # If trending towards target, be more aggressive with reduction
            # Enter stage 1 earlier (at 60% of deadband instead of 80%)
            stage_1_threshold = 0.60
            stage_2_threshold = 0.80
        elif trend == "away_from_target":
            # If trending away, be conservative
            # Only enter deadband at higher thresholds
            stage_1_threshold = 0.90
            stage_2_threshold = 0.95
        else:
            # Stable trend - normal thresholds
            stage_1_threshold = 0.80
            stage_2_threshold = 0.90
        
        # Check stability duration for stage 3
        now = time.time()
        stability_duration = 0
        if self._deadband_stability_start:
            stability_duration = now - self._deadband_stability_start
        
        # Determine stage
        if relative_deviation < stage_1_threshold:
            # Very close to target - soft reduction
            if self._current_deadband_stage != 1:
                _LOGGER.debug(f"{self.room}: Entering deadband stage 1 (soft) - deviation: {deviation:.3f}, trend: {trend}")
            self._current_deadband_stage = 1
            return 1
        elif relative_deviation < stage_2_threshold:
            # Medium distance - medium reduction
            if self._current_deadband_stage != 2:
                _LOGGER.debug(f"{self.room}: Entering deadband stage 2 (medium) - deviation: {deviation:.3f}, trend: {trend}")
            self._current_deadband_stage = 2
            return 2
        elif stability_duration >= self._deadband_stability_duration:
            # Stable for long time - full reduction
            if self._current_deadband_stage != 3:
                _LOGGER.info(f"{self.room}: Entering deadband stage 3 (full) - stable for {stability_duration:.0f}s")
            self._current_deadband_stage = 3
            return 3
        else:
            # Not stable long enough - stay at stage 2
            return 2
    
    async def _handle_smart_deadband(self, current_vpd: float, target_vpd: float, deadband: float, mode_name: str) -> bool:
        """
        Smart Deadband Handler - Advanced version with dynamic stages and predictive logic.

        Features:
        - Dynamic deadband based on plant stage (VPD Perfection only)
        - 3-stage gradual reduction (soft, medium, full)
        - Trend analysis for predictive behavior
        - Night mode only when nightVPDHold is enabled
        - Max 10 minute deadband with 30-second checks

        Args:
            current_vpd: Aktueller VPD Wert
            target_vpd: Ziel VPD Wert
            deadband: Deadband Toleranz (may be overridden by dynamic calculation)
            mode_name: Name des Modus (für Logging)

        Returns:
            bool: True if deadband is active, False if deadband is blocked (e.g., night mode without nightVPDHold)
        """
        import time
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        now = time.time()
        deviation = abs(current_vpd - target_vpd)
        
        # First: Set exit threshold (even if we exit, it helps for logging)
        self._update_deadband_thresholds(deadband)
        
        # KRITISCH: Prüfe ob VPD das Deadband mit Hysteres verlassen hat
        # Exit erst wenn deviation > exit_threshold (115% des deadbands)
        if deviation > self._deadband_exit_threshold:
            if self._is_in_deadband:
                time_since_exit = now - self._deadband_last_exit_time if self._deadband_last_exit_time else 0
                _LOGGER.info(
                    f"{self.room}: VPD {current_vpd} EXITED deadband with hysteresis "
                    f"(deviation: {deviation:.3f} > exit_threshold: {self._deadband_exit_threshold:.3f}, "
                    f"deadband: {deadband:.3f}, last_exit: {time_since_exit:.0f}s ago) - "
                    f"exiting deadband"
                )
                self._deadband_last_exit_time = now
                self._reset_deadband_state()
            return False  # Deadband is NOT active
        
        # Prüfen ob wir bereits im Deadband sind
        if not self._is_in_deadband:
            # Prüfe ob wir zu kurz nach Exit sind (Mindest-Hold-Zeit nach Exit)
            if self._deadband_last_exit_time:
                time_since_exit = now - self._deadband_last_exit_time
                if time_since_exit < self._deadband_min_hold_after_exit:
                    _LOGGER.debug(
                        f"{self.room}: Blocking deadband re-entry - too soon after exit "
                        f"({time_since_exit:.0f}s < {self._deadband_min_hold_after_exit}s hold)"
                    )
                    return False  # Deadband is NOT active (blocked by re-entry cooldown)
            
            # Erster Eintritt in Deadband
            self._is_in_deadband = True
            self._deadband_hold_start = now
            self._deadband_active_devices.clear()
            
            # WICHTIG: Speichere Deadband State im DataStore für andere Komponenten
            self.data_store.setDeep("controlOptionData.deadband.active", True)
            self.data_store.setDeep("controlOptionData.deadband.target_vpd", target_vpd)
            self.data_store.setDeep("controlOptionData.deadband.deadband_value", deadband)
            self.data_store.setDeep("controlOptionData.deadband.entered_at", now)
            self.data_store.setDeep("controlOptionData.deadband.mode", mode_name)
            
            _LOGGER.info(
                f"{self.room}: VPD {current_vpd} entered deadband ±{deadband} of target {target_vpd} - "
                f"starting smart deadband (hold: {self._deadband_hold_duration}s)"
            )
            
            # Emit SmartDeadbandEntered Events für alle Deadband-Geräte
            deadband_device_types = {
                "Heater", "Cooler", "Humidifier", "Dehumidifier", "Climate",
                "Exhaust", "Intake", "Window"
            }
            for device_type in deadband_device_types:
                await self.event_manager.emit("SmartDeadbandEntered", {"deviceType": device_type})
        
        # Berechne verbleibende Hold-Zeit
        hold_elapsed = now - (self._deadband_hold_start or now)
        hold_remaining = max(0, self._deadband_hold_duration - hold_elapsed)
        
        # Aktualisiere DataStore mit verbleibender Zeit
        self.data_store.setDeep("controlOptionData.deadband.hold_remaining", hold_remaining)
        
        # Calculate dynamic deadband based on plant stage (VPD Perfection only)
        dynamic_deadband = self._calculate_dynamic_deadband(mode_name)
        if dynamic_deadband != deadband:
            _LOGGER.debug(f"{self.room}: Using dynamic deadband {dynamic_deadband} instead of {deadband} for {mode_name}")
            deadband = dynamic_deadband
        
        # Update exit threshold with hysteresis
        self._update_deadband_thresholds(deadband)
        
        # Check if night mode and nightVPDHold is disabled
        is_night = not self.data_store.getDeep("isPlantDay.islightON", True)
        night_vpd_hold = self.data_store.getDeep("controlOptions.nightVPDHold", True)

        if is_night and not night_vpd_hold:
            # Night mode without VPD hold - no deadband, use power-saving mode instead
            if self._is_in_deadband:
                _LOGGER.info(f"{self.room}: Night mode without VPD hold - exiting deadband for power-saving")
                self._reset_deadband_state()
            return False  # Deadband is NOT active (night mode without nightVPDHold)
        
        # Calculate trend for predictive behavior
        trend = self._calculate_trend(current_vpd)
        
        # Check max deadband time (10 minutes)
        max_deadband_time = 600  # 10 minutes
        if self._deadband_hold_start and (now - self._deadband_hold_start) > max_deadband_time:
            _LOGGER.info(f"{self.room}: Max deadband time ({max_deadband_time}s) reached - exiting")
            self._reset_deadband_state()
            return False  # Deadband is NOT active (max time reached)

        # Periodic check every 30 seconds during deadband
        if self._last_deadband_check and (now - self._last_deadband_check) < self._deadband_check_interval:
            # Skip this cycle, just update hold time
            self.data_store.setDeep("controlOptionData.deadband.hold_remaining", hold_remaining)
            return True  # Deadband IS active (periodic check skip)
        
        self._last_deadband_check = now
        
        # Determine deadband stage based on deviation and trend
        stage = self._determine_deadband_stage(deviation, deadband, trend)
        
        # Update stability tracking
        if stage >= 2 and not self._deadband_stability_start:
            self._deadband_stability_start = now
        elif stage < 2:
            self._deadband_stability_start = None
        
        # Get capabilities
        caps = self.data_store.get("capabilities") or {}
        
        # Build deadband actions based on stage
        deadband_actions = []
        devices_dimmed = []
        devices_reduced = []
        
        # Stage-based reduction levels
        reduction_levels = {
            1: {"climate": 50, "air_exchange": 75},  # Soft: Climate 50%, Air-Exchange 75%
            2: {"climate": 25, "air_exchange": 50},  # Medium: Climate 25%, Air-Exchange 50%
            3: {"climate": 10, "air_exchange": 25},  # Full: Climate 10%, Air-Exchange 25%
        }
        
        level = reduction_levels.get(stage, {"climate": 50, "air_exchange": 75})
        
        # Climate devices (affect VPD directly)
        climate_devices = ["canHumidify", "canDehumidify", "canHeat", "canCool", "canClimate"]
        for cap in climate_devices:
            if caps.get(cap, {}).get("state", False):
                is_dimmable = caps.get(cap, {}).get("isDimmable", False)
                
                if is_dimmable:
                    action = OGBActionPublication(
                        capability=cap,
                        action="Reduce",
                        Name=self.room,
                        message=f"Deadband Stage {stage}: {cap} reduced to {level['climate']}%",
                        priority="low"
                    )
                    devices_dimmed.append(f"{cap}:{level['climate']}%")
                else:
                    action = OGBActionPublication(
                        capability=cap,
                        action="Reduce",
                        Name=self.room,
                        message=f"Deadband Stage {stage}: {cap} turned off",
                        priority="low"
                    )
                    devices_reduced.append(cap)
                
                deadband_actions.append(action)
                self._deadband_active_devices.add(cap)
        
        # Air exchange devices (affect VPD through air exchange)
        air_exchange_devices = ["canExhaust", "canIntake", "canWindow"]
        for cap in air_exchange_devices:
            if caps.get(cap, {}).get("state", False):
                is_dimmable = caps.get(cap, {}).get("isDimmable", False)
                
                if is_dimmable:
                    action = OGBActionPublication(
                        capability=cap,
                        action="Reduce",
                        Name=self.room,
                        message=f"Deadband Stage {stage}: {cap} reduced to {level['air_exchange']}%",
                        priority="low"
                    )
                    devices_dimmed.append(f"{cap}:{level['air_exchange']}%")
                else:
                    action = OGBActionPublication(
                        capability=cap,
                        action="Reduce",
                        Name=self.room,
                        message=f"Deadband Stage {stage}: {cap} turned off",
                        priority="low"
                    )
                    devices_reduced.append(cap)
                
                deadband_actions.append(action)
                self._deadband_active_devices.add(cap)
        
        # Ventilation (internal circulation) - always runs at 100%
        ventilation_running = []
        for cap in self._ventilation_devices:
            if caps.get(cap, {}).get("state", False):
                ventilation_running.append(cap)
        
        # Light - unchanged
        light_status = "unchanged"
        if caps.get("canLight", {}).get("state", False):
            light_status = "running (unchanged)"
        
        # Emit LogForClient with detailed status
        await self.event_manager.emit("LogForClient", {
            "Name": self.room,
            "message": f"Smart Deadband Stage {stage} active - hold: {hold_remaining:.0f}s, trend: {trend}",
            "VPDStatus": "InDeadband",
            "currentVPD": current_vpd,
            "targetVPD": target_vpd,
            "deadband": deadband,
            "exitThreshold": self._deadband_exit_threshold,
            "deviation": deviation,
            "holdTimeRemaining": hold_remaining,
            "holdDuration": self._deadband_hold_duration,
            "stage": stage,
            "trend": trend,
            "mode": mode_name,
            "devicesDimmed": devices_dimmed,
            "devicesReduced": devices_reduced,
            "ventilationRunning": ventilation_running,
            "lightStatus": light_status,
            "deadbandActive": True,
            "hysteresisFactor": self._deadband_hysteresis_factor
        }, haEvent=True, debug_type="INFO")

        # NOTE: Deadband actions are NOT executed here anymore
        # The SmartDeadbandEntered event (lines 322-328) already handles device reduction via setToMinimum()
        # The "Reduce" events were redundant and were blocked by device handlers anyway
        # Devices are now restored to their previous state when exiting deadband (Device.py:restoreFromMinimum())

        # Check if hold time elapsed
        if hold_remaining <= 0:
            _LOGGER.info(
                f"{self.room}: Deadband hold time ({self._deadband_hold_duration}s) elapsed - checking extension"
            )
            # Extend hold time only if VPD is stable AND within hysteresis zone
            if trend == "stable" or trend == "towards_target":
                if deviation <= self._deadband_exit_threshold:
                    # Both conditions met: stable trend AND within hysteresis zone
                    self._deadband_hold_start = now
                    _LOGGER.info(
                        f"{self.room}: Extending deadband - VPD stable ({trend}) and within hysteresis zone (deviation: {deviation:.3f} <= {self._deadband_exit_threshold:.3f})"
                    )
                else:
                    # Trend is good but outside hysteresis zone - exit
                    _LOGGER.info(
                        f"{self.room}: Not extending - trend {trend} but outside hysteresis zone (deviation: {deviation:.3f} > {self._deadband_exit_threshold:.3f})"
                    )
                    self._reset_deadband_state()
                    return False
            else:
                # Trend is bad - exit
                _LOGGER.info(f"{self.room}: Not extending - trend {trend}")
                self._reset_deadband_state()
                return False

        # Deadband is active
        return True
    
    def _reset_deadband_state(self):
        """Reset deadband state when leaving deadband."""
        if self._is_in_deadband:
            _LOGGER.info(f"{self.room}: Leaving deadband - resetting state and restoring devices")
            self._is_in_deadband = False
            self._deadband_hold_start = None
            self._deadband_active_devices.clear()
            
            # NEW: Reset advanced tracking
            self._deadband_stability_start = None
            self._vpd_history.clear()
            self._last_deadband_check = 0
            self._current_deadband_stage = 0
            
            # WICHTIG: Lösche Deadband State aus DataStore
            self.data_store.setDeep("controlOptionData.deadband.active", False)
            self.data_store.delete("controlOptionData.deadband.target_vpd")
            self.data_store.delete("controlOptionData.deadband.deadband_value")
            self.data_store.delete("controlOptionData.deadband.entered_at")
            self.data_store.delete("controlOptionData.deadband.hold_remaining")
            self.data_store.delete("controlOptionData.deadband.mode")
            
            # Emit SmartDeadbandExited Events für alle Deadband-Geräte
            deadband_device_types = {
                "Heater", "Cooler", "Humidifier", "Dehumidifier", "Climate",
                "Exhaust", "Intake", "Window"
            }
            for device_type in deadband_device_types:
                asyncio.create_task(self.event_manager.emit("SmartDeadbandExited", {"deviceType": device_type}))
            
            _LOGGER.debug(f"{self.room}: Deadband state reset complete - devices will resume normal operation")

    async def selectActionMode(self, Publication):
        """
        Handhabt Änderungen des Modus basierend auf `tentMode`.
        """
        controlOption = self.data_store.get("mainControl")

        if controlOption not in ["HomeAssistant", "Premium"]:
            return False

        # tentMode = self.data_store.get("tentMode")
        tentMode = None
        if isinstance(Publication, OGBModePublication):
            return
        elif isinstance(Publication, OGBModeRunPublication):
            tentMode = Publication.currentMode
            # _LOGGER.debug(f"{self.name}: Run Mode {tentMode} for {self.room}")
        else:
            _LOGGER.debug(
                f"Unbekannter Datentyp: {type(Publication)} - Daten: {Publication}"
            )
            return

        if tentMode == "VPD Perfection":
            await self.handle_vpd_perfection()
        elif tentMode == "VPD Target":
            await self.handle_targeted_vpd()
        elif tentMode == "Drying":
            await self.handle_drying()
        elif tentMode == "MPC Control":
            await self.handle_premium_mode_cycle(tentMode)
        elif tentMode == "PID Control":
            await self.handle_premium_mode_cycle(tentMode)
        elif tentMode == "AI Control":
            await self.handle_premium_mode_cycle(tentMode)
        elif tentMode == "Closed Environment":
            await self.handle_closed_environment()
        elif tentMode == "Script Mode":
            await self.handle_script_mode()
        elif tentMode == "Disabled":
            await self.handle_disabled_mode()

        else:
            _LOGGER.debug(f"{self.name}: Unbekannter Modus {tentMode}")

    async def handle_disabled_mode(self):
        """
        Handhabt den Modus 'Disabled'.
        Stops all active control actions and ensures devices are in safe state.
        """
        _LOGGER.info(f"🔴 {self.room}: Tent mode set to Disabled - stopping all control actions")
        
        # Emit disabled event for other managers to clean up
        await self.event_manager.emit("TentModeDisabled", {"room": self.room})
        
        # Log the disabled state
        await self.event_manager.emit(
            "LogForClient", {"Name": self.room, "Mode": "Disabled"}
        )
        
        # Emit MinMaxControlDisabled for all device types to ensure safe state
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Light"})
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Ventilation"})
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Exhaust"})
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Intake"})
        
        _LOGGER.info(f"🔴 {self.room}: All control actions disabled")
        
        return None

    async def handle_closed_environment(self):
        """
        Handhabt den Modus 'Closed Environment' für sealed grow chambers (stateless).
        Executes one control cycle with ambient-enhanced logic.
        """
        # Ambient room should never trigger Closed Environment actions - only used as reference
        if self.room.lower() == "ambient":
            _LOGGER.debug(
                f"{self.room}: Ambient room - skipping Closed Environment mode, "
                f"only used as reference for other rooms"
            )
            return

        _LOGGER.debug(f"ModeManager: {self.room} executing Closed Environment cycle")

        # NOTE: Smart Deadband für Closed Environment DEAKTIVIERT
        # Closed Environment kontrolliert NUR Temperature und Humidity via min/max Limits
        # Kein VPD-basierter Deadband - das verursachte nur Probleme

        # Execute single control cycle (stateless like VPD Perfection)
        await self.closedEnvironmentManager.execute_cycle()

        # Log mode activation
        await self.event_manager.emit(
            "LogForClient", {"Name": self.room, "Mode": "Closed Environment"}
        )

    ## VPD Modes
    async def handle_vpd_perfection(self):
        """
        Handhabt den Modus 'VPD Perfection' und steuert die Geräte basierend auf dem aktuellen VPD-Wert.
        """
        # Ambient room should never trigger VPD actions - only used as reference for Closed Environment
        if self.room.lower() == "ambient":
            _LOGGER.debug(
                f"{self.room}: Ambient room - skipping VPD Perfection mode, "
                f"only used as reference for Closed Environment"
            )
            return

        # Aktuelle VPD-Werte abrufen
        currentVPD = self.data_store.getDeep("vpd.current")
        perfectionVPD = self.data_store.getDeep("vpd.perfection")
        perfectionMinVPD = self.data_store.getDeep("vpd.perfectMin")
        perfectionMaxVPD = self.data_store.getDeep("vpd.perfectMax")

        # Validierung: Alle Werte müssen gesetzt sein
        if currentVPD is None or perfectionMinVPD is None or perfectionMaxVPD is None or perfectionVPD is None:
            _LOGGER.debug(
                f"{self.room}: VPD values not initialized (current={currentVPD}, min={perfectionMinVPD}, max={perfectionMaxVPD}, perfect={perfectionVPD}). Skipping VPD control."
            )
            return

        capabilities = self.data_store.get("capabilities")

        # SMART DEADBAND CHECK für VPD Perfection
        deadband = self.data_store.getDeep("controlOptionData.deadband.vpdDeadband") or 0.05
        deviation = abs(float(currentVPD) - float(perfectionVPD))

        if deviation <= deadband:
            # Im Deadband - Smart Deadband Handler aufrufen
            deadband_active = await self._handle_smart_deadband(float(currentVPD), float(perfectionVPD), deadband, "VPD Perfection")

            if deadband_active:
                # Deadband IS active - devices are already reduced to minimum
                # No FineTune needed - deadband handles this functionality
                return  # Keine normalen VPD Actions ausführen
            # If deadband is NOT active (e.g., night mode without nightVPDHold), continue to normal cycle
        else:
            # Außerhalb Deadband - Reset Deadband State
            self._reset_deadband_state()

        if currentVPD < perfectionMinVPD:
            _LOGGER.debug(
                f"{self.room}: Current VPD ({currentVPD}) is below minimum ({perfectionMinVPD}). Increasing VPD."
            )
            await self.event_manager.emit("increase_vpd", capabilities)
        elif currentVPD > perfectionMaxVPD:
            _LOGGER.debug(
                f"{self.room}: Current VPD ({currentVPD}) is above maximum ({perfectionMaxVPD}). Reducing VPD."
            )
            await self.event_manager.emit("reduce_vpd", capabilities)
        # FineTune entfernt - Deadband übernimmt diese Funktion
        # Wenn VPD zwischen min/max aber nicht perfekt ist, wird keine Aktion ausgeführt
        # Das System lässt sich natürlich beruhigen, wie VPD Target Mode

        if self.data_store.getDeep("controlOptions.co2Control"):
            await self.event_manager.emit("maintain_co2", capabilities)

    async def handle_targeted_vpd(self):
        """
        Handhabt den Modus 'Targeted VPD' mit Toleranz.
        """
        # Ambient room should never trigger VPD actions - only used as reference for Closed Environment
        if self.room.lower() == "ambient":
            _LOGGER.debug(
                f"{self.room}: Ambient room - skipping VPD Target mode, "
                f"only used as reference for Closed Environment"
            )
            return

        _LOGGER.info(f"ModeManager: {self.room} Modus 'Targeted VPD' aktiviert.")
        _LOGGER.debug(
            f"{self.room} VPD Target state: "
            f"current={self.data_store.getDeep('vpd.current')}, "
            f"targeted={self.data_store.getDeep('vpd.targeted')}, "
            f"min={self.data_store.getDeep('vpd.targetedMin')}, "
            f"max={self.data_store.getDeep('vpd.targetedMax')}"
        )

        try:
            # Aktuelle VPD-Werte abrufen
            currentVPD_raw = self.data_store.getDeep("vpd.current")
            targetedVPD_raw = self.data_store.getDeep("vpd.targeted")
            tolerance_raw = self.data_store.getDeep("vpd.tolerance")
            min_vpd_raw = self.data_store.getDeep("vpd.targetedMin")
            max_vpd_raw = self.data_store.getDeep("vpd.targetedMax")

            # Validierung: current/targeted müssen gesetzt sein
            if None in (currentVPD_raw, targetedVPD_raw):
                _LOGGER.warning(
                    f"{self.room}: VPD values not initialized (current={currentVPD_raw}, targeted={targetedVPD_raw}, min={min_vpd_raw}, max={max_vpd_raw}, tolerance={tolerance_raw}). Skipping VPD control."
                )
                return

            currentVPD = float(currentVPD_raw)
            targetedVPD = float(targetedVPD_raw)

            if min_vpd_raw is None or max_vpd_raw is None:
                if tolerance_raw is None:
                    _LOGGER.warning(
                        f"{self.room}: Missing targeted min/max and tolerance is not set. Skipping VPD control."
                    )
                    return

                tolerance_percent = float(tolerance_raw)
                tolerance_value = targetedVPD * (tolerance_percent / 100)
                min_vpd = round(targetedVPD - tolerance_value, 2)
                max_vpd = round(targetedVPD + tolerance_value, 2)

                self.data_store.setDeep("vpd.targetedMin", min_vpd)
                self.data_store.setDeep("vpd.targetedMax", max_vpd)
            else:
                min_vpd = float(min_vpd_raw)
                max_vpd = float(max_vpd_raw)

            # Verfügbare Capabilities abrufen
            capabilities = self.data_store.get("capabilities")

            # Validate capabilities exist
            if not capabilities:
                _LOGGER.warning(
                    f"{self.room}: No capabilities available. Skipping VPD control."
                )
                return

            # SMART DEADBAND CHECK - Wenn VPD im Deadband ist
            deadband = self.data_store.getDeep("controlOptionData.deadband.vpdTargetDeadband") or 0.05
            deviation = abs(currentVPD - targetedVPD)

            if deviation <= deadband:
                # Im Deadband - Smart Deadband Handler aufrufen
                deadband_active = await self._handle_smart_deadband(currentVPD, targetedVPD, deadband, "VPD Target")

                if deadband_active:
                    return  # Keine normalen VPD Actions ausführen
                # If deadband is NOT active (e.g., night mode without nightVPDHold), continue to normal cycle
            else:
                # Außerhalb Deadband - Reset Deadband State
                self._reset_deadband_state()

            # VPD steuern basierend auf der Toleranz (nur außerhalb Deadband)
            if currentVPD < min_vpd:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is below minimum ({min_vpd}). Increasing VPD."
                )
                await self.event_manager.emit("vpdt_increase_vpd", capabilities)
            elif currentVPD > max_vpd:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is above maximum ({max_vpd}). Reducing VPD."
                )
                await self.event_manager.emit("vpdt_reduce_vpd", capabilities)

        except ValueError as e:
            _LOGGER.error(
                f"ModeManager: Fehler beim Konvertieren der VPD-Werte oder Toleranz in Zahlen. {e}"
            )
        except Exception as e:
            _LOGGER.error(
                f"ModeManager: Unerwarteter Fehler in 'handle_targeted_vpd': {e}"
            )

    ## Premium Handle
    async def handle_premium_mode_cycle(self, tent_mode: str):
        """
        Handle premium control mode cycle by triggering DataRelease to API.

        The API is responsible for controller execution (PID/MPC/AI) and returns
        actions asynchronously via websocket. This method only validates access
        and triggers the data send path.
        """
        mainControl = self.data_store.get("mainControl")
        if mainControl != "Premium":
            _LOGGER.debug(
                f"{self.room}: Premium mode '{tent_mode}' selected but mainControl is '{mainControl}' - skipping API controller cycle"
            )
            return

        tent_mode_to_controller = {
            "PID Control": "PID",
            "MPC Control": "MPC",
            "AI Control": "AI",
        }

        controllerType = tent_mode_to_controller.get(tent_mode)
        if not controllerType:
            _LOGGER.warning(f"{self.room}: Unknown premium tent mode '{tent_mode}'")
            return

        # Check feature flags before sending data to API
        subscription_data = self.data_store.get("subscriptionData") or {}
        features = subscription_data.get("features", {})
        controller_feature_map = {
            "PID": "pidControllers",
            "MPC": "mpcControllers",
            "AI": "aiControllers",
        }
        feature_key = controller_feature_map.get(controllerType)
        feature_enabled = features.get(feature_key, False)

        if not feature_enabled:
            _LOGGER.warning(
                f"{self.room}: {controllerType} mode selected but feature '{feature_key}' is not enabled"
            )
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Warning": f"{controllerType} controller not available in your subscription",
                    "Feature": feature_key,
                    "Enabled": False,
                },
                haEvent=True,
                debug_type="WARNING",
            )
            return

        _LOGGER.info(
            f"{self.room}: Premium controller cycle for {controllerType} - emitting DataRelease to API"
        )
        await self.event_manager.emit("DataRelease", True)

        if controllerType == "AI" and not self._ai_bridge_started:
            await self.start_ai_data_bridge()

    async def handle_premium_modes(self, data):
        """
        Handle premium controller modes (PID, MPC, AI).
        
        Checks feature flags from subscription_data before executing.
        Feature access is determined by:
        1. Kill switch (global disable)
        2. Tenant override (admin dashboard)
        3. Subscription plan features (from API)
        """
        if not isinstance(data, dict):
            return

        controllerTypeRaw = data.get("controllerType")
        if isinstance(controllerTypeRaw, str):
            controllerType = controllerTypeRaw.strip().upper()
        else:
            controllerType = None

        if not controllerType:
            return
            
        # Get subscription data to check feature access
        # subscription_data is stored in datastore after login
        subscription_data = self.data_store.get("subscriptionData") or {}
        features = subscription_data.get("features", {})
        
        # Map controller types to their feature keys (API uses camelCase)
        controller_feature_map = {
            "PID": "pidControllers",
            "MPC": "mpcControllers",
            "AI": "aiControllers",
        }
        
        feature_key = controller_feature_map.get(controllerType)
        if not feature_key:
            _LOGGER.warning(f"{self.room}: Unknown controller type: {controllerType}")
            return
        
        # Check if feature is enabled in subscription
        feature_enabled = features.get(feature_key, False)
        
        if not feature_enabled:
            _LOGGER.warning(
                f"{self.room}: {controllerType} controller requested but feature '{feature_key}' "
                f"is not enabled in subscription (plan features: {list(features.keys())})"
            )
            # Emit event for UI notification
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Warning": f"{controllerType} controller not available in your subscription",
                "Feature": feature_key,
                "Enabled": False
            }, haEvent=True, debug_type="WARNING")
            return
        
        _LOGGER.info(f"{self.room}: Executing {controllerType} controller (feature '{feature_key}' enabled)")
        
        actionData = data.get("actionData")
        if not actionData:
            _LOGGER.debug(f"{self.room}: PremiumCheck received without actionData for {controllerType}")
            return

        if controllerType == "PID":
            await self.event_manager.emit("PIDActions", data)
        elif controllerType == "MPC":
            await self.event_manager.emit("MPCActions", data)
        elif controllerType == "AI":
            await self.event_manager.emit("AIActions", data)

            # Start AI Data Bridge for cropsteering learning when AI control is active
            if not self._ai_bridge_started:
                await self.start_ai_data_bridge()

        return

    async def start_ai_data_bridge(self):
        """Start the AI Data Bridge for cropsteering learning integration"""
        try:
            if not self._ai_bridge_started:
                await self.aiDataBridge.start()
                self._ai_bridge_started = True
                _LOGGER.info(
                    f"{self.room} - AI Data Bridge started for cropsteering learning"
                )
        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to start AI Data Bridge: {e}")

    async def stop_ai_data_bridge(self):
        """Stop the AI Data Bridge"""
        try:
            if self._ai_bridge_started:
                await self.aiDataBridge.stop()
                self._ai_bridge_started = False
                _LOGGER.info(f"{self.room} - AI Data Bridge stopped")
        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to stop AI Data Bridge: {e}")

    ## Drying Mode - Delegated to DryingActions
    async def handle_drying(self):
        """
        Handles 'Drying' mode by delegating to DryingActions.
        Supports ElClassico, 5DayDry, and DewBased algorithms.
        """
        await self.dryingActions.handle_drying()

    ## Script Mode - Custom user-defined automation
    async def handle_script_mode(self):
        """
        Handles 'Script Mode' - fully customizable user scripts.
        Stateless execution like VPD Perfection - called cyclically by ModeManager.
        """
        _LOGGER.debug(f"ModeManager: {self.room} executing Script Mode cycle")

        # Initialize script mode manager if needed
        if self.scriptModeManager is None:
            if hasattr(self, '_ogb_ref') and self._ogb_ref:
                self.scriptModeManager = OGBScriptMode(self._ogb_ref)
            else:
                _LOGGER.warning(f"{self.room}: Script Mode requires OGB reference. Set _ogb_ref first.")
                return

        # Execute script (stateless - like VPD Perfection)
        # Script is loaded from DataStore on each execution
        await self.scriptModeManager.execute()

    def set_ogb_reference(self, ogb):
        """
        Set OGB reference for Script Mode.
        Called by coordinator after initialization.
        """
        self._ogb_ref = ogb
        if self.scriptModeManager is None:
            self.scriptModeManager = OGBScriptMode(ogb)
            _LOGGER.info(f"{self.room}: Script Mode manager initialized")

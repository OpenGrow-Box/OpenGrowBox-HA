import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ....data.OGBDataClasses.OGBPublications import OGBHydroAction, OGBWaterAction, OGBWaterPublication

from .OGBAdvancedSensor import OGBAdvancedSensor
from .OGBCSCalibrationManager import OGBCSCalibrationManager
from .OGBCSConfigurationManager import CSMode, OGBCSConfigurationManager
from .OGBCSIrrigationManager import OGBCSIrrigationManager
from .OGBCSPhaseManager import OGBCSPhaseManager

_LOGGER = logging.getLogger(__name__)


class OGBCSManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Crop Steering Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager
        self.isInitialized = False

        # Advanced sensor processing for TDR/VWC/EC calculations
        self.advanced_sensor = OGBAdvancedSensor()
        self.medium_type = "rockwool"  # Default, will be synced from medium manager

        # Initialize specialized managers
        self.phase_manager = OGBCSPhaseManager(
            data_store=dataStore,
            room=room,
            event_manager=eventManager
        )
        
        self.irrigation_manager = OGBCSIrrigationManager(
            room=room,
            data_store=dataStore,
            event_manager=eventManager,
            hass=hass
        )
        
        # Calibration Manager - handles VWC max/min calibration
        self.calibration_manager = OGBCSCalibrationManager(
            room=room,
            data_store=dataStore,
            event_manager=eventManager,
            advanced_sensor=self.advanced_sensor,
            hass=hass
        )
        
        # Configuration Manager - handles presets and medium adjustments
        self.config_manager = OGBCSConfigurationManager(
            data_store=dataStore,
            room=room
        )

        self.blockCheckIntervall = 300  # 5 minutes
        self.max_irrigation_attempts = 5
        self.stability_tolerance = 0.1

        # Single task for any CS operation
        self._main_task = None
        self._calibration_task = None
        
        # Irrigation protection - prevents mode change from cancelling active irrigation
        self._irrigation_lock = asyncio.Lock()
        self._irrigation_in_progress = False
        
        # Debounce protection - prevents duplicate handle_mode_change calls with SAME mode
        self._last_mode_change_time = None
        self._last_mode_change_mode = None  # Track which mode was last activated
        self._mode_change_debounce_seconds = 2.0  # Ignore duplicate calls within 2 seconds

        # Event subscriptions
        # NOTE: CropSteeringChanges is handled by OGBCastManager which validates 
        # Hydro.Mode and then calls our handle_mode_change() - no direct subscription needed!
        # self.event_manager.on("CropSteeringChanges", self.handle_mode_change)  # REMOVED - was causing double calls!
        self.event_manager.on(
            "VWCCalibrationCommand", self.handle_vwc_calibration_command
        )
        self.event_manager.on("MediumChange", self._on_medium_change)

    # ==================== MEDIUM SYNC ====================

    async def _sync_medium_type(self):
        """Sync medium type from medium manager or dataStore.
        
        Priority:
        1. GrowMedium objects from growMediums list (authoritative source)
        2. CropSteering.MediumType in dataStore (user override)
        3. Default to rockwool
        """
        try:
            medium_found = False
            
            # Priority 1: Get from actual GrowMedium objects
            grow_mediums = self.data_store.get("growMediums") or []
            if grow_mediums and len(grow_mediums) > 0:
                first_medium = grow_mediums[0]
                if hasattr(first_medium, "medium_type"):
                    # GrowMedium object - medium_type is MediumType enum
                    self.medium_type = first_medium.medium_type.value
                    medium_found = True
                    _LOGGER.info(f"{self.room} - Got medium type from GrowMedium: {self.medium_type}")
                elif isinstance(first_medium, dict) and "type" in first_medium:
                    # Dict format (from persistence)
                    self.medium_type = first_medium["type"].lower()
                    medium_found = True
                    _LOGGER.info(f"{self.room} - Got medium type from dict: {self.medium_type}")

            # Priority 2: Fallback to dataStore CropSteering settings (only if no medium found)
            if not medium_found:
                stored_medium = self.data_store.getDeep("CropSteering.MediumType")
                if stored_medium:
                    self.medium_type = stored_medium.lower()
                    medium_found = True
                    _LOGGER.info(f"{self.room} - Got medium type from CropSteering.MediumType: {self.medium_type}")

            # Priority 3: Default fallback
            if not medium_found:
                self.medium_type = "rockwool"
                _LOGGER.warning(f"{self.room} - No medium found, defaulting to: {self.medium_type}")

        except Exception as e:
            _LOGGER.warning(f"{self.room} - Could not sync medium type: {e}")
            self.medium_type = "rockwool"

        _LOGGER.info(
            f"{self.room} - CropSteering using medium type: {self.medium_type}"
        )

    async def _on_medium_change(self, data):
        """Handle medium type changes from medium manager"""
        # Defensive: Handle both dict and string formats
        if isinstance(data, str):
            # Legacy format: just the medium type string
            new_medium = data.lower()
        elif isinstance(data, dict):
            # Proper format: dict with room and medium_type
            if data.get("room") != self.room:
                return
            new_medium = data.get("medium_type", "").lower()
        else:
            _LOGGER.warning(f"{self.room} - MediumChange event with invalid data type: {type(data)}")
            return

        if new_medium and new_medium != self.medium_type:
            old_medium = self.medium_type
            self.medium_type = new_medium
            self.data_store.setDeep("CropSteering.MediumType", new_medium)

            _LOGGER.info(
                f"{self.room} - CropSteering medium changed: {old_medium} → {new_medium}"
            )

            # Notify about recalibration
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Medium changed to {new_medium}. Sensor calibrations updated.",
                },
                haEvent=True,
            )

    # ==================== CALIBRATION RESET ====================

    async def reset_calibration(self, phase: str = None):
        """Reset calibration values to force re-calibration.
        
        Args:
            phase: Optional phase to reset (p0, p1, p2, p3). If None, resets all.
        """
        phases_to_reset = [phase] if phase else ["p0", "p1", "p2", "p3"]
        
        for p in phases_to_reset:
            # Clear calibration values
            self.data_store.setDeep(f"CropSteering.Calibration.{p}.VWCMax", None)
            self.data_store.setDeep(f"CropSteering.Calibration.{p}.VWCMin", None)
            self.data_store.setDeep(f"CropSteering.Calibration.{p}.timestamp", None)
            _LOGGER.warning(f"{self.room} - Reset calibration for phase {p}")
        
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"Calibration reset for phases: {', '.join(phases_to_reset)}",
            },
            haEvent=True,
        )
    
    def debug_dump_cropsteering_config(self):
        """Dump all CropSteering config from DataStore for debugging."""
        cs_data = self.data_store.getDeep("CropSteering") or {}
        
        _LOGGER.warning(f"===== {self.room} CropSteering DataStore Dump =====")
        _LOGGER.warning(f"ActiveMode: {cs_data.get('ActiveMode')}")
        _LOGGER.warning(f"Active: {cs_data.get('Active')}")
        _LOGGER.warning(f"CropPhase: {cs_data.get('CropPhase')}")
        _LOGGER.warning(f"MediumType: {cs_data.get('MediumType')}")
        
        # Calibration values
        calibration = cs_data.get('Calibration', {})
        _LOGGER.warning(f"Calibration: {calibration}")
        
        # Substrate (user settings)
        substrate = cs_data.get('Substrate', {})
        _LOGGER.warning(f"Substrate (user settings): {substrate}")
        
        # Current sensor values
        _LOGGER.warning(f"vwc_current: {cs_data.get('vwc_current')}")
        _LOGGER.warning(f"ec_current: {cs_data.get('ec_current')}")
        _LOGGER.warning(f"===== End Dump =====")
        
        return cs_data

    # ==================== PRESET ACCESS (delegated to config_manager) ====================

    def _get_adjusted_preset(self, phase: str, plant_phase: str, generative_week: int) -> Dict[str, Any]:
        """
        Get preset with all adjustments applied.
        Delegates to OGBCSConfigurationManager.
        
        Args:
            phase: Phase identifier (p0, p1, p2, p3)
            plant_phase: Current plant phase ('veg' or 'gen')
            generative_week: Week number in generative phase
            
        Returns:
            Adjusted preset configuration
        """
        return self.config_manager.get_adjusted_preset(
            phase=phase,
            plant_phase=plant_phase,
            generative_week=generative_week,
            medium_type=self.medium_type
        )

    async def _update_number_entity(self, parameter: str, phase: str, value: float):
        """
        Update a HA number entity with calibrated value.
        
        Args:
            parameter: Parameter name (e.g., 'VWCMax', 'VWCMin')
            phase: Phase identifier (e.g., 'p1', 'p2')
            value: The calibrated value to set
        """
        if not self.hass:
            _LOGGER.debug(f"{self.room} - Cannot update number entity: hass not available")
            return
        
        try:
            # Entity naming: OGB_CropSteering_P1_VWC_Max_{room} -> number.ogb_cropsteering_p1_vwc_max_{room}
            # Map parameter names to entity format
            param_map = {
                "VWCMax": "vwc_max",
                "VWCMin": "vwc_min",
                "VWCTarget": "vwc_target",
            }
            param_name = param_map.get(parameter, parameter.lower())
            entity_id = f"number.ogb_cropsteering_{phase}_{param_name}_{self.room.lower()}"
            
            await self.hass.services.async_call(
                domain="number",
                service="set_value",
                service_data={"entity_id": entity_id, "value": float(value)},
                blocking=True,
            )
            
            _LOGGER.info(
                f"{self.room} - Updated number entity {entity_id} to {value:.1f}"
            )
            
        except Exception as e:
            _LOGGER.warning(f"{self.room} - Failed to update number entity for {parameter}.{phase}: {e}")

    # ==================== ENTRY POINT ====================
    async def handle_mode_change(self, data):
        """SINGLE entry point for all mode changes.
        
        Called from:
        1. CropSteeringChanges event via CastManager (when user changes CS sub-selector)
        2. CastManager.HydroModeChange() (when user selects Crop-Steering hydro mode)
        
        CRITICAL: This is the ONLY place where CS tasks are started/stopped!
        """
        requested_mode = self.data_store.getDeep("CropSteering.ActiveMode") or "Automatic"
        _LOGGER.warning(f"{self.room} - CropSteering handle_mode_change: requested={requested_mode}, data={data}")

        # ===== STEP 1: Parse mode FIRST to know what to do =====
        mode = self._parse_mode(requested_mode)
        _LOGGER.warning(f"{self.room} - CropSteering parsed mode: {mode}")

        # ===== STEP 2: Handle STOP modes (Disabled/Config) - ALWAYS stop, no checks =====
        if mode == CSMode.DISABLED:
            _LOGGER.warning(f"{self.room} - CropSteering DISABLED - stopping all operations")
            await self._force_stop_all()
            self.data_store.setDeep("CropSteering.Active", False)
            # CRITICAL: Reset debounce so next mode change works immediately
            self._last_mode_change_mode = None
            self._last_mode_change_time = None
            # CRITICAL: Reset P1 state tracking so next Automatic start irrigates immediately
            self._reset_p1_state_tracking()
            return
            
        if mode == CSMode.CONFIG:
            _LOGGER.warning(f"{self.room} - CropSteering CONFIG mode - stopping operations")
            await self._force_stop_all()
            # CRITICAL: Reset debounce so next mode change works immediately
            self._last_mode_change_mode = None
            self._last_mode_change_time = None
            # CRITICAL: Reset P1 state tracking so next Automatic start irrigates immediately
            self._reset_p1_state_tracking()
            return

        # ===== STEP 3: For RUN modes, validate environment =====
        hydro_mode = self.data_store.getDeep("Hydro.Mode")
        if hydro_mode != "Crop-Steering":
            _LOGGER.warning(f"{self.room} - CropSteering BLOCKED: Hydro.Mode='{hydro_mode}' != 'Crop-Steering'")
            await self._force_stop_all()
            return

        # ===== STEP 4: Check if task already running =====
        # If a task is running, DON'T restart unless mode actually changed
        if self._main_task and not self._main_task.done():
            # Task is running - check if we should restart
            if self._last_mode_change_mode == requested_mode:
                _LOGGER.warning(f"{self.room} - CS task already running for '{requested_mode}', ignoring duplicate call")
                return
            else:
                _LOGGER.warning(f"{self.room} - Mode changed from '{self._last_mode_change_mode}' to '{requested_mode}', restarting...")
                await self._cancel_main_task()
        
        # ===== STEP 5: Debounce - prevent rapid restarts =====
        now = datetime.now()
        if self._last_mode_change_time and self._last_mode_change_mode == requested_mode:
            elapsed = (now - self._last_mode_change_time).total_seconds()
            if elapsed < self._mode_change_debounce_seconds:
                _LOGGER.warning(f"{self.room} - DEBOUNCED: Same mode '{requested_mode}' within {elapsed:.1f}s")
                return
        
        self._last_mode_change_time = now
        self._last_mode_change_mode = requested_mode

        # ===== STEP 6: Validate prerequisites =====
        multimediumCtrl = self.data_store.getDeep("controlOptions.multiMediumControl")
        if multimediumCtrl is False:
            _LOGGER.error(f"{self.room} - CropSteering requires multiMediumControl=True")
            return

        # Sync medium type
        if not self.isInitialized:
            await self._sync_medium_type()
            self.isInitialized = True

        # DEBUG: Dump CropSteering config to see what's actually stored
        self.debug_dump_cropsteering_config()

        # Get sensor data
        sensor_data = await self._get_sensor_averages()
        if not sensor_data:
            _LOGGER.warning(f"{self.room} - No sensor data! Cannot start CropSteering")
            await self._log_missing_sensors()
            return

        # Update current values
        self.data_store.setDeep("CropSteering.vwc_current", sensor_data["vwc"])
        self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])
        self.data_store.setDeep("CropSteering.Active", True)

        # CRITICAL FIX: Filter capabilities for Crop-Steering mode (only drippers)
        await self._filter_capabilities_for_crop_steering()

        # Send correct device status to UI (only drippers for Crop-Steering)
        await self._send_device_status_update()

        # Get configuration
        config = await self._get_configuration(mode)
        if not config:
            _LOGGER.error(f"{self.room} - Failed to get configuration for mode {mode}")
            return

        # Log start
        await self._log_mode_start(mode, config, sensor_data)

        # ===== STEP 7: Start the appropriate task =====
        if mode == CSMode.AUTOMATIC:
            _LOGGER.warning(f"{self.room} - STARTING AUTOMATIC cycle")
            self._main_task = asyncio.create_task(self._automatic_cycle())
        elif mode.value.startswith("Manual"):
            # For Manual mode, get phase from CropPhase selector (set by Phases entity)
            stored_phase = self.data_store.getDeep("CropSteering.CropPhase")
            
            # FIX: Handle case where stored_phase is None or invalid
            if stored_phase:
                stored_phase_lower = stored_phase.lower()
                if stored_phase_lower in ["p0", "p1", "p2", "p3"]:
                    phase = stored_phase_lower
                    _LOGGER.warning(f"{self.room} - Using phase from CropPhase selector: {phase}")
                else:
                    # Try to extract phase from stored_phase value (e.g., "P1" -> "p1")
                    phase = self._extract_phase_from_value(stored_phase)
                    _LOGGER.warning(f"{self.room} - Extracted phase from stored value: {phase}")
            else:
                # Fallback: extract from mode.value (e.g., "MANUAL_P1" -> "p1")
                phase = self._extract_phase_from_mode(mode)
                _LOGGER.warning(f"{self.room} - Extracted phase from mode enum: {phase}")
            
            _LOGGER.warning(f"{self.room} - STARTING MANUAL cycle for phase {phase}")
            self._main_task = asyncio.create_task(self._manual_cycle(phase))
        else:
            _LOGGER.error(f"{self.room} - Unknown mode: {mode}")

    def _extract_phase_from_mode(self, mode: CSMode) -> str:
        """Extract phase identifier from Manual mode enum.
        
        Handles:
        - Enum value: "Manual-p1" -> "p1"
        - Enum name: "MANUAL_P1" -> "p1"
        """
        # Try enum value first (e.g., "Manual-p1")
        mode_value = mode.value
        if "-" in mode_value:
            return mode_value.split("-")[1].lower()
        
        # Try enum name (e.g., "MANUAL_P1")
        mode_name = mode.name
        if "_" in mode_name:
            phase = mode_name.split("_")[-1].lower()
            if phase in ["p0", "p1", "p2", "p3"]:
                return phase
        
        # Default to p0
        _LOGGER.warning(f"{self.room} - Could not extract phase from mode {mode}, defaulting to p0")
        return "p0"

    def _extract_phase_from_value(self, value: str) -> str:
        """Extract phase from stored value (e.g., "P1" -> "p1").
        
        Handles:
        - Uppercase: "P1" -> "p1"
        - Lowercase: "p1" -> "p1"
        - Mixed case: "P0" -> "p0"
        """
        if not value:
            return "p0"
        
        value_lower = value.lower()
        if value_lower in ["p0", "p1", "p2", "p3"]:
            return value_lower
        
        # Try to extract last 2 characters (e.g., "P1" -> "p1")
        if len(value_lower) >= 2:
            possible_phase = value_lower[-2:]
            if possible_phase in ["p0", "p1", "p2", "p3"]:
                return possible_phase
        
        # Try numeric extraction (e.g., "1" -> "p1")
        if value_lower.isdigit():
            return f"p{value_lower}"
        
        # Default
        _LOGGER.warning(f"{self.room} - Could not extract phase from value {value}, defaulting to p0")
        return "p0"

    async def _cancel_main_task(self):
        """Cancel main task without turning off drippers."""
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
            self._main_task = None

    async def _force_stop_all(self):
        """Force stop all operations - used for Disabled/Config modes."""
        _LOGGER.warning(f"{self.room} - 🛑 FORCE STOP: Cancelling all CS operations...")
        
        # Cancel main task
        if self._main_task:
            task_done = self._main_task.done()
            _LOGGER.warning(f"{self.room} - 🛑 Main task exists, done={task_done}")
            if not task_done:
                _LOGGER.warning(f"{self.room} - 🛑 Cancelling main task...")
                self._main_task.cancel()
                try:
                    await self._main_task
                except asyncio.CancelledError:
                    _LOGGER.warning(f"{self.room} - 🛑 Main task cancelled successfully")
                except Exception as e:
                    _LOGGER.error(f"{self.room} - 🛑 Error cancelling main task: {e}")
        else:
            _LOGGER.warning(f"{self.room} - 🛑 No main task to cancel")
        
        # Cancel calibration task
        if self._calibration_task:
            task_done = self._calibration_task.done()
            _LOGGER.warning(f"{self.room} - 🛑 Calibration task exists, done={task_done}")
            if not task_done:
                _LOGGER.warning(f"{self.room} - 🛑 Cancelling calibration task...")
                self._calibration_task.cancel()
                try:
                    await self._calibration_task
                except asyncio.CancelledError:
                    _LOGGER.warning(f"{self.room} - 🛑 Calibration task cancelled successfully")
                except Exception as e:
                    _LOGGER.error(f"{self.room} - 🛑 Error cancelling calibration task: {e}")
        
        self._main_task = None
        self._calibration_task = None
        self._irrigation_in_progress = False
        
        # Turn off drippers
        await self._turn_off_all_drippers()
        _LOGGER.warning(f"{self.room} - 🛑 FORCE STOP COMPLETE: All CS operations stopped")

    async def handle_stop(self, event=None):
        """Stop handler for external stop events"""
        await self.stop_all_operations()

    def _reset_p1_state_tracking(self):
        """Reset P1 state tracking variables.
        
        Called when entering Config/Disabled mode so that when
        Automatic mode starts again, it will irrigate immediately
        instead of waiting for the remaining interval time.
        """
        _LOGGER.warning(f"{self.room} - 🔄 Resetting P1 state tracking (irrigation will start fresh)")
        self.data_store.setDeep("CropSteering.p1_start_vwc", None)
        self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
        self.data_store.setDeep("CropSteering.p1_last_vwc", None)
        self.data_store.setDeep("CropSteering.p1_last_irrigation_time", None)

    # ==================== MODE PARSING ====================

    def _parse_mode(self, cropMode: str) -> CSMode:
        """Parse mode string to enum.
        
        For Manual mode, if no phase is specified in the mode string,
        we check CropSteering.CropPhase (set by the Phases selector entity).
        This allows users to select "Manual" and then separately choose p0/p1/p2/p3.
        """
        if not cropMode:
            return CSMode.DISABLED
        if "Automatic" in cropMode:
            return CSMode.AUTOMATIC
        elif "Disabled" in cropMode:
            return CSMode.DISABLED
        elif "Config" in cropMode:
            return CSMode.CONFIG
        elif "Manual" in cropMode:
            # First check if phase is in the mode string (e.g., "Manual-p1")
            for phase in ["p0", "p1", "p2", "p3"]:
                if phase in cropMode.lower():
                    return CSMode[f"MANUAL_{phase.upper()}"]
            
            # No phase in mode string - check CropPhase selector
            stored_phase = self.data_store.getDeep("CropSteering.CropPhase")
            if stored_phase and stored_phase.lower() in ["p0", "p1", "p2", "p3"]:
                _LOGGER.info(f"{self.room} - Manual mode using CropPhase: {stored_phase}")
                return CSMode[f"MANUAL_{stored_phase.upper()}"]
            
            # Default to P0 if nothing else specified
            _LOGGER.warning(f"{self.room} - Manual mode defaulting to P0 (no phase specified)")
            return CSMode.MANUAL_P0
        return CSMode.DISABLED

    # ==================== SENSOR DATA ====================

    async def _get_sensor_averages(self) -> Optional[Dict[str, Any]]:
        """
        Get averaged sensor data with advanced processing.

        Uses TDR-style calibration with:
        - Medium-specific VWC polynomial calibration
        - Temperature-normalized EC
        - Pore water EC calculation (Hilhorst/mass-balance hybrid)
        - Validation and anomaly detection
        """
        vwc_values = []
        bulk_ec_values = []
        temp_values = []

        # Sync medium type if not initialized
        if not self.isInitialized:
            await self._sync_medium_type()
            self.isInitialized = True

        # Moisture/VWC sensors
        moistures = self.data_store.getDeep("workData.moisture") or []
        for item in moistures:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                raw_val = float(raw)
                # Apply medium-specific VWC calibration
                if self.advanced_sensor:
                    calibrated_vwc = self.advanced_sensor.calculate_vwc(
                        raw_val, self.medium_type
                    )
                else:
                    calibrated_vwc = raw_val  # Fallback to raw value
                vwc_values.append(calibrated_vwc)
            except (ValueError, TypeError) as e:
                _LOGGER.debug(f"{self.room} - VWC conversion error: {e}")
                continue

        # EC sensors - with automatic µS/cm to mS/cm conversion
        ecs = self.data_store.getDeep("workData.ec") or []
        for item in ecs:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                ec_val = float(raw)
                # Auto-detect unit: values > 20 are likely in µS/cm, convert to mS/cm
                # Typical EC range: 0.5 - 4.0 mS/cm (500 - 4000 µS/cm)
                if ec_val > 20:
                    ec_val = ec_val / 1000  # Convert µS/cm to mS/cm
                    _LOGGER.debug(f"{self.room} - EC auto-converted from µS to mS: {raw} -> {ec_val}")
                bulk_ec_values.append(ec_val)
            except (ValueError, TypeError):
                continue

        # Temperature sensors (for EC normalization)
        temps = self.data_store.getDeep("workData.temperature") or []
        for item in temps:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                temp_values.append(float(raw))
            except (ValueError, TypeError):
                continue

        if not vwc_values and not bulk_ec_values:
            return None

        # Calculate averages
        avg_vwc = sum(vwc_values) / len(vwc_values) if vwc_values else 0
        avg_bulk_ec = sum(bulk_ec_values) / len(bulk_ec_values) if bulk_ec_values else 0
        avg_temp = (
            sum(temp_values) / len(temp_values) if temp_values else 25.0
        )  # Default 25C

        # Calculate pore water EC using hybrid model
        pore_ec = 0
        validation = None
        if self.advanced_sensor and avg_bulk_ec > 0 and avg_vwc > 0:
            pore_ec = self.advanced_sensor.calculate_pore_ec(
                avg_bulk_ec, avg_vwc, avg_temp, self.medium_type
            )

            # Validate readings
            validation = self.advanced_sensor.validate_readings(
                avg_vwc, avg_bulk_ec, pore_ec, avg_temp, self.medium_type
            )
        else:
            # Create a mock validation object when sensor is disabled
            from types import SimpleNamespace
            validation = SimpleNamespace(issues=[], warnings=[], recommendations=[])

        if validation.issues:
            _LOGGER.warning(
                f"{self.room} - Sensor validation issues: {validation.issues}"
            )
            # Apply corrections if available
            if "vwc" in validation.corrected_values:
                avg_vwc = validation.corrected_values["vwc"]
            if "pore_ec" in validation.corrected_values:
                pore_ec = validation.corrected_values["pore_ec"]

        result = {
            "vwc": avg_vwc,
            "ec": avg_bulk_ec,  # Keep 'ec' key for backward compatibility
            "bulk_ec": avg_bulk_ec,
            "pore_ec": pore_ec,
            "temperature": avg_temp,
            "medium_type": self.medium_type,
            "validation_valid": validation.is_valid,
            "sensor_count": {
                "vwc": len(vwc_values),
                "ec": len(bulk_ec_values),
                "temp": len(temp_values),
            },
        }

        _LOGGER.debug(
            f"{self.room} - Sensor data: VWC={avg_vwc:.1f}%, EC={avg_bulk_ec:.2f}/{pore_ec:.2f} (bulk/pore), T={avg_temp:.1f}C [{self.medium_type}]"
        )

        return result

    # ==================== CONFIGURATION ====================

    async def _get_configuration(self, mode: CSMode):
        """Get configuration for mode"""
        plant_phase, gen_week = self._get_plant_info_from_medium()
        config = {
            "mode": mode,
            "drippers": self._get_drippers(),
            "plant_phase": plant_phase,
            "generative_week": gen_week,
        }

        if not config["drippers"]:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "INVALID PUMPS",
                    "message": "No valid dripper devices found",
                },
                haEvent=True,
            )
            return None

        # Manual Mode uses user settings
        if mode.value.startswith("Manual"):
            phase = mode.value.split("-")[1]
            config["phase_settings"] = self._get_manual_phase_settings(phase)

        return config

    def _get_drippers(self):
        """Get valid dripper devices from canPump capability.

        Filter returns only devices that contain 'dripper' keyword in name.
        This excludes cloner pumps and other non-irrigation pumps.
        """
        dripperDevices = self.data_store.getDeep("capabilities.canPump")
        if not dripperDevices:
            _LOGGER.warning(f"{self.room} - _get_drippers: No canPump capability found!")
            return []

        devices = dripperDevices.get("devEntities", [])
        if not devices:
            _LOGGER.warning(f"{self.room} - _get_drippers: No pump devices found!")
            return []

        valid_keywords = ["dripper"]

        dripper_devices = [
            dev for dev in devices
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]

        if not dripper_devices:
            _LOGGER.warning(f"{self.room} - _get_drippers: No dripper devices found in: {devices}")

        return dripper_devices

    async def _filter_capabilities_for_crop_steering(self):
        """Filter capabilities.canPump to show only dripper devices for Crop-Steering mode.

        This ensures the UI shows correct device count by modifying the global capabilities registry.
        """
        try:
            # Get all pump devices
            pump_capabilities = self.data_store.getDeep("capabilities.canPump")
            if not pump_capabilities:
                _LOGGER.warning(f"{self.room} - No canPump capabilities found for filtering")
                return

            all_devices = pump_capabilities.get("devEntities", [])
            if not all_devices:
                _LOGGER.warning(f"{self.room} - No pump devices in capabilities")
                return

            # Filter to only dripper devices
            dripper_devices = [
                dev for dev in all_devices
                if "dripper" in dev.lower()
            ]

            # Update capabilities with filtered list for Crop-Steering mode
            filtered_capabilities = pump_capabilities.copy()
            filtered_capabilities["devEntities"] = dripper_devices
            filtered_capabilities["count"] = len(dripper_devices)
            filtered_capabilities["state"] = len(dripper_devices) > 0

            # Temporarily override capabilities for UI display
            self.data_store.setDeep("capabilities.canPump", filtered_capabilities)

            _LOGGER.info(f"{self.room} - Filtered capabilities.canPump for Crop-Steering: {all_devices} → {dripper_devices}")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error filtering capabilities for Crop-Steering: {e}")

    async def _restore_full_capabilities(self):
        """Restore full capabilities.canPump when Crop-Steering stops.

        This ensures other modes see all available pump devices.
        """
        try:
            # For now, we can't easily restore the original list without storing it
            # The DeviceManager should re-register devices when modes change
            # This is a placeholder for future enhancement
            _LOGGER.debug(f"{self.room} - Crop-Steering stopped, capabilities may need refresh")

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error restoring capabilities: {e}")

    async def _send_device_status_update(self):
        """Send correct device status for Crop-Steering mode.

        CRITICAL FIX: Send only dripper devices to UI to show "1/1 Active" instead of "2/2 Active".
        This ensures UI displays correct device count for current mode.
        """
        dripper_devices = self._get_drippers()

        # Send device status update to UI - ONLY dripper devices for Crop-Steering
        # This ensures UI shows "1/1 Active" not "2/2 Active" by filtering out non-dripper pumps
        if dripper_devices:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Mode": "Crop-Steering",
                    "Message": f"Crop-Steering active with {len(dripper_devices)} dripper(s)",
                    "Devices": dripper_devices,
                    "DeviceCount": len(dripper_devices)
                },
                haEvent=True
            )
            _LOGGER.debug(f"{self.room} - Crop-Steering device status updated: {dripper_devices} (filtered drippers only)")
        else:
            await self.event_manager.emit(
                "LogForClient",
                {"Name": self.room, "Type": "ERROR", "Message": "No dripper devices found for Crop-Steering"},
                haEvent=True
            )
            _LOGGER.error(f"{self.room} - No dripper devices available for Crop-Steering mode")

    def _get_automatic_timing_settings(self, phase: str) -> Dict[str, Any]:
        """
        Get USER timing settings for Automatic Mode.
        
        Reads Duration/Interval/ShotSum from user settings.
        These are the ONLY user-settable parameters in Automatic mode.
        All other parameters (VWC/EC/etc) come from presets.
        
        Args:
            phase: Phase identifier (p0, p1, p2, p3)
            
        Returns:
            Dictionary with timing settings as proper numeric types
        """
        def get_timing_value(path: str, default: float, as_int: bool = False):
            """Get timing value with proper type conversion."""
            val = self.data_store.getDeep(path)
            if val is not None:
                try:
                    numeric_val = float(val)
                    return int(numeric_val) if as_int else numeric_val
                except (ValueError, TypeError):
                    pass
            return default
        
        settings = {
            "ShotDuration": get_timing_value(
                f"CropSteering.Substrate.{phase}.Shot_Duration_Sec",
                30.0,  # Default 30 seconds
                as_int=True
            ),
            "ShotIntervall": get_timing_value(
                f"CropSteering.Substrate.{phase}.Shot_Intervall",
                60.0,  # Default 60 minutes
                as_int=False
            ),
            "ShotSum": get_timing_value(
                f"CropSteering.Substrate.{phase}.Shot_Sum",
                5,  # Default 5 shots
                as_int=True
            )
        }
        
        _LOGGER.info(
            f"{self.room} - Automatic timing settings for {phase}: "
            f"Duration={settings['ShotDuration']}s, "
            f"Interval={settings['ShotIntervall']}min, "
            f"Count={settings['ShotSum']}"
        )
        
        return settings

    def _get_manual_phase_settings(self, phase):
        """
        Get USER settings for Manual Mode.
        
        Reads from CropSteering.Substrate.{phase}.{parameter} paths
        as set by core OGBConfigurationManager.
        Falls back to legacy paths if new paths not available.
        
        CRITICAL: All values are returned as proper numeric types (int/float),
        not strings, because DataStore may store values as strings like '35.0'.
        """
        def get_numeric_value(new_path, legacy_path, default, as_int=False):
            """Try new path first, then legacy, then default. Returns numeric value.
            
            IMPORTANT: Zero (0) IS a valid value and should be returned.
            Only None or parse errors should trigger fallback to next source.
            """
            # Try new path (from OGBConfigurationManager)
            val = self.data_store.getDeep(f"CropSteering.Substrate.{phase}.{new_path}")
            _LOGGER.debug(f"{self.room} - get_numeric_value: {new_path} from Substrate.{phase} = {val} (type={type(val).__name__})")
            if val is not None:
                try:
                    numeric_val = float(val)
                    # Zero IS valid - only skip on parse error
                    return int(numeric_val) if as_int else numeric_val
                except (ValueError, TypeError):
                    pass
            
            # Try legacy path (from OGBData.py defaults)
            legacy_val = self.data_store.getDeep(f"CropSteering.{legacy_path}.{phase}")
            if legacy_val is not None:
                try:
                    if isinstance(legacy_val, dict):
                        v = legacy_val.get("value", default)
                        numeric_val = float(v) if v is not None else float(default)
                    else:
                        numeric_val = float(legacy_val)
                    return int(numeric_val) if as_int else numeric_val
                except (ValueError, TypeError):
                    pass
            
            return int(default) if as_int else float(default)
        
        return {
            "ShotIntervall": {"value": get_numeric_value("Shot_Intervall", "ShotIntervall", 30, as_int=False)},  # minutes
            "ShotDuration": {"value": get_numeric_value("Shot_Duration_Sec", "ShotDuration", 30, as_int=True)},  # seconds
            "ShotSum": {"value": get_numeric_value("Shot_Sum", "ShotSum", 5, as_int=True)},  # count
            "MoistureDryBack": {"value": get_numeric_value("Moisture_Dryback", "MoistureDryBack", 10, as_int=False)},  # percent
            "ECDryBack": {"value": get_numeric_value("EC_Dryback", "ECDryBack", 0.2, as_int=False)},
            "ECTarget": {"value": get_numeric_value("Shot_EC", "ECTarget", 2.0, as_int=False)},
            "MaxEC": {"value": get_numeric_value("Max_EC", "MaxEC", 2.5, as_int=False)},
            "MinEC": {"value": get_numeric_value("Min_EC", "MinEC", 1.5, as_int=False)},
            "VWCTarget": {"value": get_numeric_value("VWC_Target", "VWCTarget", 65, as_int=False)},
            "VWCMax": {"value": get_numeric_value("VWC_Max", "VWCMax", 70, as_int=False)},
            "VWCMin": {"value": get_numeric_value("VWC_Min", "VWCMin", 55, as_int=False)},
        }

    # ==================== AUTOMATIC MODE ====================

    async def _determine_initial_phase(self):
        """
        Intelligente Bestimmung der Start-Phase basierend auf:
        - Aktueller VWC
        - Licht-Status
        - Kalibrierte/Preset Werte
        
        Priority:
        - Light OFF -> P3 (Night Dryback) unless emergency dry
        - Light ON + dry -> P1 (Saturation)
        - Light ON + full -> P2 (Maintenance)
        - Light ON + normal -> P0 (Monitoring)
        """
        vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
        is_light_on_raw = self.data_store.getDeep("isPlantDay.islightON")
        
        # CRITICAL: Ensure proper boolean conversion - handle None, strings, etc.
        if is_light_on_raw is None:
            is_light_on = False
        elif isinstance(is_light_on_raw, str):
            is_light_on = is_light_on_raw.lower() in ("true", "1", "on", "yes")
        else:
            is_light_on = bool(is_light_on_raw)

        # Get plant info from GrowMedium (authoritative source)
        plant_phase, gen_week = self._get_plant_info_from_medium()

        # Get adjusted presets
        p0_preset = self._get_adjusted_preset("p0", plant_phase, gen_week)
        p2_preset = self._get_adjusted_preset("p2", plant_phase, gen_week)

        _LOGGER.warning(
            f"{self.room} - Determining initial phase: "
            f"VWC={vwc:.1f}%, is_light_on_raw={is_light_on_raw} (type={type(is_light_on_raw).__name__}), "
            f"is_light_on={is_light_on}, VWCMin={p0_preset.get('VWCMin')}, VWCMax={p2_preset.get('VWCMax')}"
        )

        # Decision logic - LIGHT STATUS IS PRIMARY FACTOR
        if not is_light_on:
            # === NIGHT TIME - ALWAYS P3, no irrigation at night ===
            # Night irrigation disrupts the dryback cycle which is essential for generative steering
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            _LOGGER.info(f"{self.room} - Night time (light OFF), starting P3 Dryback (VWC={vwc:.1f}%)")
            return "p3"
        
        # === DAY TIME ===
        if vwc == 0:
            _LOGGER.warning(f"{self.room} - No VWC data, starting P0 Monitoring")
            return "p0"
        
        vwc_max = p2_preset.get("VWCMax", 68)
        vwc_min = p0_preset.get("VWCMin", 55)
        
        # DEBUG: Log ALL values used for phase determination
        _LOGGER.warning(
            f"🔍 {self.room} PHASE DECISION: VWC={vwc:.1f}%, VWCMin={vwc_min}, VWCMax={vwc_max}, "
            f"is_light_on={is_light_on}, p0_preset={p0_preset}, p2_preset={p2_preset}"
        )
        
        # P0 is the DEFAULT starting phase for daytime
        # Only start in P1 if critically dry, only start in P2 if already at/above max
        if vwc < vwc_min:
            # Block is dry -> P1 Saturation needed
            _LOGGER.info(f"{self.room} - Day, VWC low ({vwc:.1f}% < {vwc_min:.1f}%), starting P1 Saturation")
            return "p1"
        elif vwc >= vwc_max:
            # Block is already at max -> P2 Maintenance (just hold it)
            _LOGGER.info(f"{self.room} - Day, VWC at max ({vwc:.1f}% >= {vwc_max:.1f}%), starting P2 Maintenance")
            return "p2"
        else:
            # VWC is between min and max -> P0 Monitoring (wait for dryback signal)
            _LOGGER.info(f"{self.room} - Day, VWC normal ({vwc:.1f}% between {vwc_min:.1f}%-{vwc_max:.1f}%), starting P0 Monitoring")
            return "p0"

    def _get_plant_info_from_medium(self) -> tuple:
        """
        Get plant phase and week from GrowMedium objects.
        Falls back to isPlantDay data if no medium found.
        
        Returns:
            tuple: (plant_phase, generative_week)
        """
        try:
            grow_mediums = self.data_store.get("growMediums") or []
            
            for medium in grow_mediums:
                try:
                    if hasattr(medium, 'get_current_phase') and hasattr(medium, 'get_bloom_week'):
                        # GrowMedium object
                        phase = medium.get_current_phase()  # "veg" or "flower"
                        if phase == "flower":
                            week = medium.get_bloom_week()
                            return ("flower", week)
                        else:
                            week = medium.get_veg_week()
                            return ("veg", week)
                    elif isinstance(medium, dict):
                        # Dictionary representation
                        bloom_switch = medium.get("bloom_switch_date")
                        grow_start = medium.get("grow_start_date")
                        
                        if bloom_switch:
                            # In flower - calculate bloom week
                            from datetime import datetime
                            if isinstance(bloom_switch, str):
                                bloom_switch = datetime.fromisoformat(bloom_switch.replace('Z', '+00:00'))
                            days = (datetime.now() - bloom_switch).days
                            week = (days // 7) + 1 if days > 0 else 1
                            return ("flower", week)
                        elif grow_start:
                            # In veg - calculate veg week  
                            from datetime import datetime
                            if isinstance(grow_start, str):
                                grow_start = datetime.fromisoformat(grow_start.replace('Z', '+00:00'))
                            days = (datetime.now() - grow_start).days
                            week = (days // 7) + 1 if days > 0 else 1
                            return ("veg", week)
                except Exception as e:
                    _LOGGER.warning(f"{self.room} - Error parsing medium plant info: {e}")
                    continue
        except Exception as e:
            _LOGGER.warning(f"{self.room} - Error getting plant info from mediums: {e}")
        
        # Fallback to isPlantDay data
        plant_phase = self.data_store.getDeep("isPlantDay.plantPhase") or "veg"
        generative_week = self.data_store.getDeep("isPlantDay.generativeWeek") or 0
        return (plant_phase, generative_week)

    async def _automatic_cycle(self):
        """Automatic sensor-based cycle mit festen Presets"""
        try:
            # IMPORTANT: Sync medium type FIRST before any preset calculations
            if not self.isInitialized:
                await self._sync_medium_type()
                self.isInitialized = True
            
            # Get plant info from GrowMedium (authoritative source)
            plant_phase, generative_week = self._get_plant_info_from_medium()
            
            _LOGGER.info(f"{self.room} - Plant info from medium: phase={plant_phase}, week={generative_week}")

            # IMPORTANT: ALWAYS determine start phase based on CURRENT conditions
            # Ignore any persisted phase - we need to evaluate the actual situation
            _LOGGER.warning(f"{self.room} - Automatic cycle starting, determining initial phase...")
            initial_phase = await self._determine_initial_phase()
            
            # Clear any old persisted phase and set the freshly determined one
            self.data_store.setDeep("CropSteering.CropPhase", initial_phase)

            _LOGGER.warning(
                f"{self.room} - Automatic CS cycle started in phase {initial_phase}"
            )

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Started in {initial_phase} - {plant_phase} week {generative_week}",
                },
                haEvent=True,
            )

            while True:
                try:
                    # === CRITICAL: Read sensor data NEWLY! ===
                    sensor_data = await self._get_sensor_averages()
                    if sensor_data:
                        self.data_store.setDeep(
                            "CropSteering.vwc_current", sensor_data["vwc"]
                        )
                        self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])

                    current_phase = self.data_store.getDeep("CropSteering.CropPhase") or "p0"

                    # Get adjusted presets based on growth phase
                    preset = self._get_adjusted_preset(
                        current_phase, plant_phase, generative_week
                    )

                    vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                    ec = float(self.data_store.getDeep("CropSteering.ec_current") or 0)
                    is_light_on_raw = self.data_store.getDeep("isPlantDay.islightON")
                    # Ensure proper boolean conversion
                    if is_light_on_raw is None:
                        is_light_on = False
                    elif isinstance(is_light_on_raw, str):
                        is_light_on = is_light_on_raw.lower() in ("true", "1", "on", "yes")
                    else:
                        is_light_on = bool(is_light_on_raw)

                    if vwc == 0:
                        _LOGGER.debug(f"{self.room} - Automatic: No VWC data yet, waiting...")
                        await asyncio.sleep(self.blockCheckIntervall)
                        continue

                    # Emit sensor update for AI learning (non-critical, wrap in try)
                    try:
                        if sensor_data:
                            env_data = self.data_store.getDeep("workData") or {}
                            await self.event_manager.emit(
                                "CSSensorUpdate",
                                {
                                    "room": self.room,
                                    "vwc": sensor_data.get("vwc"),
                                    "vwc_raw": sensor_data.get("vwc"),
                                    "ec": sensor_data.get("ec"),
                                    "ec_raw": sensor_data.get("bulk_ec"),
                                    "pore_ec": sensor_data.get("pore_ec"),
                                    "temperature": sensor_data.get("temperature"),
                                    "soil_temp": sensor_data.get("temperature"),
                                    "vwc_min": preset.get("VWCMin"),
                                    "vwc_max": preset.get("VWCMax"),
                                    "ec_target": preset.get("ECTarget"),
                                    "air_temp": self._get_env_avg(env_data, "temperature"),
                                    "humidity": self._get_env_avg(env_data, "humidity"),
                                    "vpd": self._get_env_avg(env_data, "vpd"),
                                    "light_intensity": self._get_env_avg(env_data, "lightPPFD"),
                                    "light_status": "on" if is_light_on else "off",
                                },
                            )
                    except Exception as emit_err:
                        _LOGGER.debug(f"{self.room} - CSSensorUpdate emit error (non-critical): {emit_err}")

                    # Phase logic with presets
                    if current_phase == "p0":
                        await self._handle_phase_p0_auto(vwc, ec, preset)
                    elif current_phase == "p1":
                        await self._handle_phase_p1_auto(vwc, ec, preset)
                    elif current_phase == "p2":
                        await self._handle_phase_p2_auto(vwc, ec, is_light_on, preset)
                    elif current_phase == "p3":
                        await self._handle_phase_p3_auto(vwc, ec, is_light_on, preset)

                except Exception as loop_error:
                    # Don't kill the whole cycle for one iteration's error
                    _LOGGER.error(f"{self.room} - Automatic cycle iteration error: {loop_error}", exc_info=True)

                await asyncio.sleep(self.blockCheckIntervall)

        except asyncio.CancelledError:
            _LOGGER.warning(f"{self.room} - Automatic cycle CANCELLED")
            await self._turn_off_all_drippers()
            raise
        except Exception as e:
            _LOGGER.error(f"{self.room} - Automatic cycle FATAL error: {e}", exc_info=True)
            await self._emergency_stop()

    async def _handle_phase_p0_auto(self, vwc, ec, preset):
        """P0: Monitoring phase - Wait for Dryback Signal
        
        IMPORTANT: If lights go OFF during P0, transition to P3.
        """
        # Check light status first - ensure proper boolean conversion
        is_light_on_raw = self.data_store.getDeep("isPlantDay.islightON")
        if is_light_on_raw is None:
            is_light_on = False
        elif isinstance(is_light_on_raw, str):
            is_light_on = is_light_on_raw.lower() in ("true", "1", "on", "yes")
        else:
            is_light_on = bool(is_light_on_raw)
        if not is_light_on:
            _LOGGER.info(
                f"{self.room} - P0: Lights are OFF, transitioning to P3 Night Dryback"
            )
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            self.data_store.setDeep("CropSteering.CropPhase", "p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change("p0", "p3", f"Lights OFF - switching to night dryback (VWC: {vwc:.1f}%)")
            return
        
        # P0 is simple: Wait until VWC falls below minimum
        if vwc < preset["VWCMin"]:
            _LOGGER.info(
                f"{self.room} - P0: VWC {vwc:.1f}% < Min {preset['VWCMin']:.1f}% → Switching to P1"
            )
            self.data_store.setDeep("CropSteering.CropPhase", "p1")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change(
                "p0",
                "p1",
                f"Dryback detected - VWC: {vwc:.1f}% < Min: {preset['VWCMin']:.1f}%",
            )
        else:
            # Debug: Show current VWC in P0
            _LOGGER.debug(
                f"{self.room} - P0 monitoring: VWC {vwc:.1f}% (waiting for < {preset['VWCMin']:.1f}%)"
            )

    async def _handle_phase_p1_auto(self, vwc, ec, preset):
        """
        P1: Saturation phase - Saturate block quickly
        WITH OWN INTERVAL TRACKING (not blockCheckIntervall!)
        
        IMPORTANT: P1 should only run during lights ON.
        If lights go OFF during P1, transition to P3.
        """
        # Check light status first - P1 only runs during day
        is_light_on_raw = self.data_store.getDeep("isPlantDay.islightON")
        if is_light_on_raw is None:
            is_light_on = False
        elif isinstance(is_light_on_raw, str):
            is_light_on = is_light_on_raw.lower() in ("true", "1", "on", "yes")
        else:
            is_light_on = bool(is_light_on_raw)
        if not is_light_on:
            _LOGGER.warning(
                f"{self.room} - P1: Lights are OFF, transitioning to P3 Night Dryback"
            )
            # Clear P1 state
            self.data_store.setDeep("CropSteering.p1_start_vwc", None)
            self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
            self.data_store.setDeep("CropSteering.p1_last_vwc", None)
            self.data_store.setDeep("CropSteering.p1_last_irrigation_time", None)
            # Set night moisture for dryback calculation
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            # Transition to P3
            self.data_store.setDeep("CropSteering.CropPhase", "p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change("p1", "p3", f"Lights OFF - switching to night dryback (VWC: {vwc:.1f}%)")
            return
        
        # Check if calibrated max value already exists
        # IMPORTANT: Only use calibrated value if it's REASONABLE (> 40% and > preset VWCMin)
        calibrated_max = self.data_store.getDeep(f"CropSteering.Calibration.p1.VWCMax")
        preset_vwc_max = preset.get("VWCMax", 70)
        preset_vwc_min = preset.get("VWCMin", 55)
        
        # Use calibrated value only if it makes sense
        # A calibrated value of 19% when preset is 65% is clearly wrong - auto-reset it
        use_calibrated = (
            calibrated_max is not None 
            and calibrated_max > 40 
            and calibrated_max > preset_vwc_min
        )
        
        # AUTO-RESET: If calibrated value is clearly wrong (< 40% or < preset min), clear it
        if calibrated_max is not None and not use_calibrated:
            _LOGGER.warning(
                f"{self.room} - P1: BAD CALIBRATION VALUE DETECTED! "
                f"calibrated={calibrated_max}% is invalid (< 40 or < preset_min={preset_vwc_min}). "
                f"RESETTING to use preset value {preset_vwc_max}%"
            )
            self.data_store.setDeep("CropSteering.Calibration.p1.VWCMax", None)
            calibrated_max = None
        
        target_max = float(calibrated_max) if use_calibrated else preset_vwc_max
        
        _LOGGER.warning(
            f"{self.room} - P1 CHECK: vwc={vwc:.1f}%, calibrated={calibrated_max}, "
            f"preset_max={preset_vwc_max}, use_calibrated={use_calibrated}, target={target_max}"
        )

        # Get USER timing settings for Automatic mode
        timing_settings = self._get_automatic_timing_settings("p1")
        shot_duration = timing_settings["ShotDuration"]
        wait_between = timing_settings["ShotIntervall"] * 60  # Convert minutes to seconds
        max_cycles = timing_settings["ShotSum"]
        
        # === P1 State Tracking ===
        p1_start_vwc = self.data_store.getDeep("CropSteering.p1_start_vwc")
        p1_irrigation_count = (
            self.data_store.getDeep("CropSteering.p1_irrigation_count") or 0
        )
        p1_last_vwc = self.data_store.getDeep("CropSteering.p1_last_vwc") or vwc
        last_irrigation_time = self.data_store.getDeep(
            "CropSteering.p1_last_irrigation_time"
        )
        
        now = datetime.now()
        
        # Initialize on first entry into P1
        if p1_start_vwc is None:
            self.data_store.setDeep("CropSteering.p1_start_vwc", vwc)
            self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
            self.data_store.setDeep("CropSteering.p1_last_vwc", vwc)
            self.data_store.setDeep(
                "CropSteering.p1_last_irrigation_time",
                now - timedelta(seconds=wait_between),
            )
            p1_start_vwc = vwc
            p1_last_vwc = vwc
            last_irrigation_time = now - timedelta(seconds=wait_between)

        # === 1. Target reached? ===
        if vwc >= target_max:
            _LOGGER.info(
                f"{self.room} - P1: Target reached {vwc:.1f}% >= {target_max:.1f}%"
            )
            await self._complete_p1_saturation(vwc, target_max, success=True)
            return

        # === 2. Stagnation detected? ===
        # CRITICAL: Only accept stagnation as "block full" if VWC is at least 40%!
        # A stagnation at 19% means there's a problem (sensor issue, no water, etc.), not that the block is full.
        vwc_increase_since_last = vwc - p1_last_vwc
        min_vwc_for_stagnation = max(40.0, preset_vwc_min)  # At least 40% or preset minimum
        
        if p1_irrigation_count >= 3 and vwc_increase_since_last < 0.5:
            if vwc >= min_vwc_for_stagnation:
                # Legitimate stagnation - block is actually full
                _LOGGER.info(
                    f"{self.room} - P1: Stagnation at {vwc:.1f}% (no increase since last shot)"
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"Block full at {vwc:.1f}% (no more increase)",
                    },
                    haEvent=True,
                )
                self.data_store.setDeep("CropSteering.Calibration.p1.VWCMax", vwc)
                self.data_store.setDeep("CropSteering.Calibration.p1.timestamp", datetime.now().isoformat())
                # Update the Number entity so user sees the new calibrated value
                await self._update_number_entity("VWCMax", "p1", vwc)
                await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})
                await self._complete_p1_saturation(vwc, vwc, success=True, updated_max=True)
                return
            else:
                # Stagnation at low VWC - something is wrong, NOT block full!
                _LOGGER.warning(
                    f"{self.room} - P1: IGNORING stagnation at {vwc:.1f}% - too low! "
                    f"(need >= {min_vwc_for_stagnation:.1f}% to consider block full). "
                    f"Check: pump working? water supply? sensor calibration?"
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"WARNING: VWC stuck at {vwc:.1f}% after {p1_irrigation_count} shots - check pump/water supply!",
                    },
                    haEvent=True,
                )
                # Continue trying to irrigate - don't save bad calibration!

        # === 3. Max Attempts? ===
        if p1_irrigation_count >= max_cycles:
            _LOGGER.info(f"{self.room} - P1: Max attempts reached ({max_cycles})")
            
            # Only save calibration if VWC reached a reasonable level
            if vwc >= min_vwc_for_stagnation:
                self.data_store.setDeep("CropSteering.Calibration.p1.VWCMax", vwc)
                self.data_store.setDeep("CropSteering.Calibration.p1.timestamp", datetime.now().isoformat())
                # Update the Number entity so user sees the new calibrated value
                await self._update_number_entity("VWCMax", "p1", vwc)
                await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})
                await self._complete_p1_saturation(vwc, vwc, success=True, updated_max=True)
            else:
                # VWC too low after max attempts - problem detected!
                _LOGGER.error(
                    f"{self.room} - P1: Max attempts ({max_cycles}) reached but VWC only {vwc:.1f}%! "
                    f"NOT saving as calibration. Check pump/water supply!"
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"ERROR: {max_cycles} irrigations but VWC only {vwc:.1f}% - check system!",
                    },
                    haEvent=True,
                )
                # Move to P2 anyway but don't save bad calibration
                await self._complete_p1_saturation(vwc, target_max, success=False, updated_max=False)
            return
        
        # === 4. Check interval ===
        time_since_last = (
            (now - last_irrigation_time).total_seconds()
            if last_irrigation_time
            else float("inf")
        )
        time_until_next = max(0, wait_between - time_since_last)
        
        if time_since_last >= wait_between:
            # Time for next shot!
            await self._irrigate(duration=shot_duration)

            # Update state
            p1_irrigation_count += 1
            self.data_store.setDeep(
                "CropSteering.p1_irrigation_count", p1_irrigation_count
            )
            self.data_store.setDeep("CropSteering.p1_last_vwc", vwc)
            self.data_store.setDeep("CropSteering.p1_last_irrigation_time", now)

            # Calculate next shot time
            next_shot_min = wait_between / 60
            
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"P1 Shot {p1_irrigation_count}/{max_cycles} → VWC: {vwc:.1f}% (target: {target_max:.1f}%) | Duration: {shot_duration}s | Next in: {next_shot_min:.0f}min",
                },
                haEvent=True,
            )
            _LOGGER.info(
                f"{self.room} - P1: Shot {p1_irrigation_count}/{max_cycles}, VWC={vwc:.1f}%, duration={shot_duration}s, next in {next_shot_min:.0f}min"
            )
        else:
            # Not time yet - log waiting status
            _LOGGER.debug(
                f"{self.room} - P1: Waiting for next shot, {time_until_next:.0f}s remaining (interval: {wait_between}s)"
            )

    async def _complete_p1_saturation(
        self, vwc, target_max, success=True, updated_max=False
    ):
        """
        Complete P1 saturation phase and transition to P2.
        Called when target VWC is reached, stagnation detected, or max attempts reached.
        """
        # Clear P1 state tracking
        self.data_store.setDeep("CropSteering.p1_start_vwc", None)
        self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
        self.data_store.setDeep("CropSteering.p1_last_vwc", None)
        self.data_store.setDeep("CropSteering.p1_last_irrigation_time", None)

        # Transition to P2
        self.data_store.setDeep("CropSteering.CropPhase", "p2")
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        # Log the transition
        message = f"Saturation complete - VWC: {vwc:.1f}%"
        if updated_max:
            message += f" (new calibrated max)"

        await self._log_phase_change("p1", "p2", message)

        await self.event_manager.emit(
            "LogForClient",
            {"Name": self.room, "Type": "CSLOG", "Message": f"P1 → P2: {message}"},
            haEvent=True,
        )

        _LOGGER.info(
            f"{self.room} - P1 complete: VWC={vwc:.1f}%, target={target_max:.1f}%, success={success}"
        )

    async def _handle_phase_p2_auto(self, vwc, ec, is_light_on, preset):
        """
        P2: Maintenance phase - Maintain level during light phase
        WITH STAGE-CHECKER for light change
        """
        # Get USER timing settings for P2
        timing_settings = self._get_automatic_timing_settings("p2")
        shot_duration = timing_settings["ShotDuration"]
        
        if is_light_on:
            # Normal day maintenance
            
            # Use calibrated max if available
            calibrated_max = self.data_store.getDeep(
                f"CropSteering.Calibration.p1.VWCMax"
            )
            effective_max = (
                float(calibrated_max) if calibrated_max else preset["VWCMax"]
            )
            
            hold_threshold = effective_max * preset.get("hold_percentage", 0.95)
            
            if vwc < hold_threshold:
                await self._irrigate(duration=shot_duration)
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P2 Maintenance: VWC {vwc:.1f}% < Hold {hold_threshold:.1f}% → Irrigation",
                    },
                    haEvent=True,
                )
                _LOGGER.info(
                    f"{self.room} - P2: Irrigated (VWC {vwc:.1f}% < {hold_threshold:.1f}%)"
                )
            else:
                # Debug: Show status in P2
                _LOGGER.debug(
                    f"{self.room} - P2 maintenance: VWC {vwc:.1f}% (hold at {hold_threshold:.1f}%, OK)"
                )
        else:
            # STAGE-CHECKER: Light is off -> Switch to P3
            _LOGGER.info(f"{self.room} - P2: Light OFF → Switching to P3")
            self.data_store.setDeep("CropSteering.CropPhase", "p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            await self._log_phase_change(
                "p2", "p3", f"Night begins - Starting VWC: {vwc:.1f}%"
            )

    async def _handle_phase_p3_auto(self, vwc, ec, is_light_on, preset):
        """
        P3: Night dry-back phase - Controlled nightly dryback
        WITH STAGE-CHECKER for light change and calibrated values
        """
        if not is_light_on:
            # Normal night phase
            start_night = self.data_store.getDeep("CropSteering.startNightMoisture")

            # If startNightMoisture is missing (e.g. after restart), set it now
            if start_night is None or start_night == 0:
                self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
                start_night = vwc
                _LOGGER.info(
                    f"{self.room} - P3: Initialized startNightMoisture to {vwc:.1f}%"
                )

            target_dryback = preset["target_dryback_percent"]
            current_dryback = (
                ((start_night - vwc) / start_night) * 100 if start_night else 0
            )

            _LOGGER.debug(
                f"{self.room} - P3: Dryback {current_dryback:.1f}% (target {target_dryback:.1f}%, start {start_night:.1f}%, current {vwc:.1f}%)"
            )

            # EC adjustment based on dryback
            if current_dryback < preset.get("min_dryback_percent", 8.0):
                # Too little dryback -> increase EC (more stress)
                await self._adjust_ec_for_dryback(
                    preset["ECTarget"],
                    increase=True,
                    step=preset.get("ec_increase_step", 0.1),
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P3 Low dryback {current_dryback:.1f}% < {preset.get('min_dryback_percent', 8.0):.1f}% → Increasing EC",
                    },
                    haEvent=True,
                )
                _LOGGER.info(f"{self.room} - P3: Low dryback, EC increased")

            elif current_dryback > preset.get("max_dryback_percent", 12.0):
                # Too much dryback -> decrease EC (less stress)
                await self._adjust_ec_for_dryback(
                    preset["ECTarget"],
                    increase=False,
                    step=preset.get("ec_decrease_step", 0.1),
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P3 High dryback {current_dryback:.1f}% > {preset.get('max_dryback_percent', 12.0):.1f}% → Decreasing EC",
                    },
                    haEvent=True,
                )
                _LOGGER.info(f"{self.room} - P3: High dryback, EC decreased")
            else:
                _LOGGER.debug(
                    f"{self.room} - P3: Dryback optimal at {current_dryback:.1f}%"
                )

            # Emergency irrigation if too dry
            calibrated_max = self.data_store.getDeep(
                f"CropSteering.Calibration.p1.VWCMax"
            )
            effective_max = (
                float(calibrated_max) if calibrated_max else preset["VWCMax"]
            )

            emergency_level = effective_max * preset.get("emergency_threshold", 0.90)
            if vwc < emergency_level:
                p3_emergency_count = (
                    self.data_store.getDeep("CropSteering.p3_emergency_count") or 0
                )
                max_emergency = preset.get("max_emergency_shots", 2)
                emergency_shot_duration = preset.get("irrigation_duration", 30)
                
                if p3_emergency_count < max_emergency:
                    await self._irrigate(
                        duration=emergency_shot_duration,
                        is_emergency=True,
                    )
                    self.data_store.setDeep(
                        "CropSteering.p3_emergency_count", p3_emergency_count + 1
                    )
                    await self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "Type": "CSLOG",
                            "Message": f"P3 Emergency irrigation {p3_emergency_count + 1}/{max_emergency}: VWC {vwc:.1f}% < {emergency_level:.1f}%",
                        },
                        haEvent=True,
                    )
                    _LOGGER.warning(
                        f"{self.room} - P3: Emergency irrigation {p3_emergency_count + 1}/{max_emergency} (VWC {vwc:.1f}% < {emergency_level:.1f}%)"
                    )
                else:
                    _LOGGER.warning(
                        f"{self.room} - P3: Max emergency irrigations reached ({max_emergency}), skipping"
                    )
        else:
            # STAGE-CHECKER: Light is on -> Back to P0
            start_night = self.data_store.getDeep("CropSteering.startNightMoisture")
            current_dryback = (
                ((start_night - vwc) / start_night) * 100 if start_night else 0
            )
            night_start_time = self.data_store.getDeep("CropSteering.phaseStartTime")

            _LOGGER.info(
                f"{self.room} - P3: Light ON → Switching to P0 (Dryback was {current_dryback:.1f}%)"
            )
            self.data_store.setDeep("CropSteering.CropPhase", "p0")
            self.data_store.setDeep(
                "CropSteering.startNightMoisture", None
            )  # Reset for next night

            # Emit dryback complete event for AI learning
            night_duration = None
            if night_start_time:
                night_duration = (datetime.now() - night_start_time).total_seconds()

            await self.event_manager.emit(
                "CSDrybackComplete",
                {
                    "room": self.room,
                    "start_time": (
                        night_start_time.timestamp() * 1000
                        if night_start_time
                        else None
                    ),
                    "end_time": datetime.now().timestamp() * 1000,
                    "duration": night_duration,
                    "vwc_start": start_night,
                    "vwc_end": vwc,
                    "vwc_min": preset.get("VWCMin"),
                    "vwc_max": preset.get("VWCMax"),
                    "dryback_percent": current_dryback,
                    "target_dryback": preset.get("target_dryback_percent"),
                    "irrigation_count": self.data_store.getDeep(
                        "CropSteering.p3_emergency_count"
                    )
                    or 0,
                },
            )

            # Reset P3 tracking
            self.data_store.setDeep("CropSteering.p3_emergency_count", 0)

            await self._log_phase_change(
                "p3",
                "p0",
                f"Day starts - Final VWC: {vwc:.1f}%, Dryback: {current_dryback:.1f}%",
            )

    # ==================== MANUAL MODE ====================
    async def _manual_cycle(self, phase):
        """Manual time-based cycle (uses USER settings)"""
        _LOGGER.warning(f"{self.room} - CS - Manual {phase}: Started")
        try:
            settings = self._get_manual_phase_settings(phase)

            # Values are already converted to proper numeric types by _get_manual_phase_settings
            shot_duration = settings["ShotDuration"]["value"]  # int (seconds)
            shot_interval = settings["ShotIntervall"]["value"]  # float (minutes)
            shot_count = settings["ShotSum"]["value"]  # int (count)
            
            _LOGGER.warning(f"{self.room} - Manual {phase} settings: duration={shot_duration}s, interval={shot_interval}min, count={shot_count}")

            # Apply sensible defaults if values are invalid
            if shot_duration <= 0:
                shot_duration = 30
                _LOGGER.warning(f"{self.room} - Manual {phase}: Invalid duration, using default 30s")
            if shot_interval <= 0:
                shot_interval = 30  # 30 minutes default
                _LOGGER.warning(f"{self.room} - Manual {phase}: Invalid interval, using default 30min")
            if shot_count <= 0:
                shot_count = 5
                _LOGGER.warning(f"{self.room} - Manual {phase}: Invalid count, using default 5")

            self.data_store.setDeep("CropSteering.shotCounter", 0)
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

            _LOGGER.warning(
                f"{self.room} - Manual {phase}: {shot_count} shots every {shot_interval}min"
            )

            while True:
                try:
                    # === CRITICAL: Read sensor data NEWLY! ===
                    sensor_data = await self._get_sensor_averages()
                    if sensor_data:
                        self.data_store.setDeep(
                            "CropSteering.vwc_current", sensor_data["vwc"]
                        )
                        self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])

                    vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                    ec = float(self.data_store.getDeep("CropSteering.ec_current") or 0)
                    
                    # Safe shot_counter read - handle None case
                    raw_counter = self.data_store.getDeep("CropSteering.shotCounter")
                    shot_counter = int(float(raw_counter)) if raw_counter is not None else 0

                    # EC management - LOG ONLY (no actual adjustment, needs nutrient system integration)
                    # Values are already proper numeric types from _get_manual_phase_settings
                    ec_target = settings["ECTarget"]["value"]
                    min_ec = settings["MinEC"]["value"]
                    max_ec = settings["MaxEC"]["value"]
                    
                    if ec_target > 0 and ec:
                        if ec < min_ec:
                            _LOGGER.info(f"{self.room} - Manual: EC {ec:.2f} < Min {min_ec:.2f} (would increase)")
                        elif ec > max_ec:
                            _LOGGER.info(f"{self.room} - Manual: EC {ec:.2f} > Max {max_ec:.2f} (would decrease)")

                    # Emergency irrigation - VWCMin is already a float
                    vwc_min = settings["VWCMin"]["value"]
                    if vwc and vwc_min > 0 and vwc < vwc_min * 0.9:
                        await self._irrigate(duration=shot_duration)
                        await self.event_manager.emit(
                            "LogForClient",
                            {
                                "Name": self.room,
                                "Type": "Emergency irrigation",
                                "Message": f"CropSteering {phase}: Emergency irrigation",
                            },
                            haEvent=True,
                        )

                    # Scheduled irrigation
                    last_irrigation = self.data_store.getDeep(
                        "CropSteering.lastIrrigationTime"
                    )
                    now = datetime.now()

                    should_irrigate = (
                        last_irrigation is None
                        or (now - last_irrigation).total_seconds() / 60 >= shot_interval
                    )

                    if should_irrigate and shot_counter < shot_count:
                        await self._irrigate(duration=shot_duration)
                        shot_counter += 1
                        self.data_store.setDeep("CropSteering.shotCounter", shot_counter)
                        self.data_store.setDeep("CropSteering.lastIrrigationTime", now)

                        await self.event_manager.emit(
                            "LogForClient",
                            {
                                "Name": self.room,
                                "Type": "CSLOG",
                                "Message": f"CropSteering {phase}: Shot {shot_counter}/{shot_count}",
                            },
                            haEvent=True,
                        )

                    # Reset counter after full cycle
                    if shot_counter >= shot_count:
                        phase_start = self.data_store.getDeep("CropSteering.phaseStartTime")
                        if phase_start:
                            elapsed = (now - phase_start).total_seconds() / 60

                            if elapsed >= shot_interval:
                                self.data_store.setDeep("CropSteering.shotCounter", 0)
                                self.data_store.setDeep("CropSteering.phaseStartTime", now)
                                await self.event_manager.emit(
                                    "LogForClient",
                                    {
                                        "Name": self.room,
                                        "Type": "CSLOG",
                                        "Message": f"CropSteering {phase}: New cycle started",
                                    },
                                    haEvent=True,
                                )
                        else:
                            # phaseStartTime was None, reset it
                            self.data_store.setDeep("CropSteering.phaseStartTime", now)

                except Exception as loop_error:
                    # Don't kill the whole cycle for one iteration's error
                    _LOGGER.error(f"{self.room} - Manual cycle iteration error: {loop_error}", exc_info=True)

                await asyncio.sleep(10)

        except asyncio.CancelledError:
            _LOGGER.warning(f"{self.room} - Manual cycle CANCELLED")
            await self._turn_off_all_drippers()
            raise
        except Exception as e:
            _LOGGER.error(f"{self.room} - Manual cycle FATAL error: {e}", exc_info=True)
            await self._emergency_stop()

    # ==================== IRRIGATION ====================

    async def _irrigate(self, duration=None, is_emergency=False):
        """Execute irrigation with protection against cancellation.
        
        Uses _irrigation_in_progress flag to prevent handle_mode_change 
        from stopping irrigation mid-cycle.
        
        Args:
            duration: Irrigation duration in seconds. If None, reads from preset.
            is_emergency: Whether this is an emergency irrigation
        """
        drippers = self._get_drippers()

        if not drippers:
            _LOGGER.warning(f"⚠️ {self.room} - No drippers found, skipping irrigation")
            return
        # If no duration passed, get from USER timing settings
        if duration is None or duration <= 0:
            current_phase = self.data_store.getDeep("CropSteering.CropPhase") or "p1"
            timing_settings = self._get_automatic_timing_settings(current_phase)
            duration = timing_settings["ShotDuration"]
            _LOGGER.warning(f"{self.room} - _irrigate: No duration passed, using USER timing value: {duration}s")
        
        _LOGGER.warning(f"{self.room} - _irrigate called with duration={duration}s")
        
        # Log current settings for debugging
        current_phase = self.data_store.getDeep("CropSteering.CropPhase") or "p1"
        plant_phase, gen_week = self._get_plant_info_from_medium()
        preset = self._get_adjusted_preset(current_phase, plant_phase, gen_week)
        
        _LOGGER.warning(
             f"{self.room} - Using user timing from settings: "
             f"Duration={duration}s (User), "
             f"VWCMin={preset.get('VWCMin')}, VWCMax={preset.get('VWCMax')} (Preset)"
         )

        # Get sensor data BEFORE irrigation for event logging
        pre_sensor_data = await self._get_sensor_averages()
        pre_vwc = pre_sensor_data.get("vwc", 0) if pre_sensor_data else 0
        pre_ec = pre_sensor_data.get("ec", 0) if pre_sensor_data else 0
        pre_pore_ec = pre_sensor_data.get("pore_ec", 0) if pre_sensor_data else 0
        pre_temp = pre_sensor_data.get("temperature", 25) if pre_sensor_data else 25

        try:
            # Turn ON drippers - same pattern as CastManager
            _LOGGER.warning(f"🚿 {self.room} - Starting irrigation for {duration}s with {len(drippers)} drippers")
            for dev_id in drippers:
                pumpAction = OGBHydroAction(
                    Name=self.room, Action="on", Device=dev_id, Cycle="false"
                )
                await self.event_manager.emit("PumpAction", pumpAction)
                _LOGGER.warning(f"🚿 {self.room} - Sent ON to {dev_id}")
            
            # Wait for irrigation duration
            await asyncio.sleep(duration)

            # Turn OFF drippers - same pattern as CastManager
            _LOGGER.warning(f"🛑 {self.room} - Irrigation STOPPING after {duration}s - turning off {len(drippers)} drippers")
            for dev_id in drippers:
                pumpAction = OGBHydroAction(
                    Name=self.room, Action="off", Device=dev_id, Cycle="false"
                )
                await self.event_manager.emit("PumpAction", pumpAction)
                _LOGGER.warning(f"🛑 {self.room} - Sent OFF to {dev_id}")

            # Emit AI irrigation event
            await self.event_manager.emit(
                "CSIrrigation",
                {
                    "room": self.room,
                    "shot_number": self.data_store.getDeep("CropSteering.shotCounter")
                    or 1,
                    "duration": duration,
                    "pre_vwc": pre_vwc,
                    "pre_ec": pre_ec,
                    "pre_pore_ec": pre_pore_ec,
                    "pre_temperature": pre_temp,
                    "interval": preset.get("irrigation_interval")
                    or preset.get("wait_between"),
                    "target_vwc": preset.get("VWCTarget"),
                    "max_shots": preset.get("max_cycles") or preset.get("ShotSum"),
                    "is_emergency": is_emergency,
                },
            )

        except asyncio.CancelledError:
            # Task was cancelled - still need to turn off drippers safely
            _LOGGER.warning(f"⚠️ {self.room} - Irrigation CANCELLED mid-cycle! Turning off drippers...")
            await self._turn_off_all_drippers()
            raise  # Re-raise to propagate cancellation
        except Exception as e:
            _LOGGER.error(f"Irrigation error: {e}")
            await self._emergency_stop()
        finally:
            # ALWAYS release the irrigation lock
            self._irrigation_in_progress = False
            _LOGGER.info(f"🔓 {self.room} - Irrigation lock RELEASED")

    # ==================== EC ADJUSTMENT ====================

    async def _adjust_ec_for_dryback(self, target_ec, increase=True, step=0.1):
        """
        Adjust EC based on dryback performance
        Only used in Automatic Mode P3
        """
        direction = "increase" if increase else "decrease"
        new_ec = target_ec + step if increase else target_ec - step

        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"EC {direction}: {target_ec:.1f} -> {new_ec:.1f} (Dryback control)",
            },
            haEvent=True,
        )

        # Here the actual EC adjustment would take place via fertilizer dosing
        # TODO: Integration with Nutrient-System

    async def _adjust_ec_to_target(self, target_ec, increase=True):
        """EC adjustment for Manual Mode"""
        direction = "increase" if increase else "decrease"
        await self.event_manager.emit(
            "LogForClient",
            f"CropSteering: Adjusting EC {direction} towards {target_ec}",
            haEvent=True,
        )

    # ==================== STOP & CLEANUP ====================

    async def stop_all_operations(self):
        """Stop all running operations - delegates to _force_stop_all."""
        await self._force_stop_all()

    async def _emergency_stop(self):
        """Emergency stop all operations"""
        await self._turn_off_all_drippers()
        await self.event_manager.emit(
            "LogForClient", f"{self.room}: Emergency stop activated", haEvent=True
        )

    async def _turn_off_all_drippers(self):
        """Turn off all drippers - same pattern as CastManager"""
        drippers = self._get_drippers()

        for dev_id in drippers:
            try:
                pumpAction = OGBHydroAction(
                    Name=self.room, Action="off", Device=dev_id, Cycle="false"
                )
                await self.event_manager.emit("PumpAction", pumpAction)
            except Exception as e:
                _LOGGER.error(f"Error turning off {dev_id}: {e}")

    # ==================== HELPERS ====================

    def _get_env_avg(self, work_data: Dict, key: str) -> Optional[float]:
        """Get average value from workData for a sensor type"""
        try:
            values = work_data.get(key) or []
            if not values:
                return None
            numeric_values = []
            for item in values:
                val = item.get("value") if isinstance(item, dict) else item
                if val is not None:
                    try:
                        numeric_values.append(float(val))
                    except (ValueError, TypeError):
                        continue
            return sum(numeric_values) / len(numeric_values) if numeric_values else None
        except Exception:
            return None

    # ==================== LOGGING ====================

    async def _log_mode_start(self, mode, config, sensor_data):
        """Log mode start with preset values"""
        # Get current phase preset to show configured values
        current_phase = self.data_store.getDeep("CropSteering.CropPhase") or "p1"
        plant_phase = config.get("plant_phase", "unknown")
        gen_week = config.get("generative_week", 0)
        preset = self._get_adjusted_preset(current_phase, plant_phase, gen_week)
        
        # Build detailed message
        duration = preset.get("irrigation_duration", "?")
        interval = preset.get("wait_between", preset.get("irrigation_interval", "?"))
        max_shots = preset.get("max_cycles", preset.get("ShotSum", "?"))
        vwc_target = preset.get("VWCTarget", "?")
        
        # Convert interval to minutes for display
        interval_min = int(interval / 60) if isinstance(interval, (int, float)) else interval
        
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"CropSteering {mode.value} started",
                "Phase": current_phase,
                "Duration": f"{duration}s",
                "Interval": f"{interval_min}min",
                "MaxShots": max_shots,
                "VWCTarget": vwc_target,
                "VWC": sensor_data.get("vwc"),
                "EC": sensor_data.get("ec"),
                "PlantPhase": plant_phase,
                "Week": gen_week,
            },
            haEvent=True,
        )
        
        _LOGGER.warning(
            f"{self.room} - CS Started: phase={current_phase}, duration={duration}s, "
            f"interval={interval_min}min, max_shots={max_shots}, vwc_target={vwc_target}"
        )

    async def _log_phase_change(self, from_phase, to_phase, reason):
        """Log phase change with preset details"""
        # Get new phase preset for logging
        plant_phase, gen_week = self._get_plant_info_from_medium()
        new_preset = self._get_adjusted_preset(to_phase, plant_phase, gen_week)
        
        duration = new_preset.get("irrigation_duration", "?")
        interval = new_preset.get("wait_between", new_preset.get("irrigation_interval", 0))
        interval_min = int(interval / 60) if isinstance(interval, (int, float)) and interval > 0 else "?"
        max_shots = new_preset.get("max_cycles", new_preset.get("ShotSum", "?"))
        vwc_target = new_preset.get("VWCTarget", "?")
        
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"Phase {from_phase} -> {to_phase}: {reason}",
                "NewPhase": to_phase,
                "Duration": f"{duration}s",
                "Interval": f"{interval_min}min",
                "MaxShots": max_shots,
                "VWCTarget": vwc_target,
            },
            haEvent=True,
        )
        
        _LOGGER.warning(
            f"{self.room} - Phase change: {from_phase} -> {to_phase} ({reason}) "
            f"| duration={duration}s, interval={interval_min}min, max_shots={max_shots}"
        )

        # Emit AI event for learning
        sensor_data = await self._get_sensor_averages()
        is_light_on_raw = self.data_store.getDeep("isPlantDay.islightON")
        if is_light_on_raw is None:
            is_light_on = False
        elif isinstance(is_light_on_raw, str):
            is_light_on = is_light_on_raw.lower() in ("true", "1", "on", "yes")
        else:
            is_light_on = bool(is_light_on_raw)
        plant_phase, gen_week = self._get_plant_info_from_medium()
        preset = self._get_adjusted_preset(to_phase, plant_phase, gen_week)

        await self.event_manager.emit(
            "CSPhaseChange",
            {
                "room": self.room,
                "from_phase": from_phase,
                "to_phase": to_phase,
                "trigger": reason,
                "vwc": sensor_data.get("vwc") if sensor_data else None,
                "ec": sensor_data.get("ec") if sensor_data else None,
                "pore_ec": sensor_data.get("pore_ec") if sensor_data else None,
                "temperature": sensor_data.get("temperature") if sensor_data else None,
                "vwc_min": preset.get("VWCMin"),
                "vwc_max": preset.get("VWCMax"),
                "ec_target": preset.get("ECTarget"),
                "light_status": "on" if is_light_on else "off",
            },
        )

    async def _log_missing_sensors(self):
        """Log missing sensor data"""
        _LOGGER.debug(
            f"{self.room} Message: CropSteering: Waiting for sensor data (VWC/EC missing)"
        )
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": "Waiting for sensor data (VWC/EC missing)",
            },
            haEvent=True,
        )

    # ==================== VWC CALIBRATION (ONLY FOR AUTOMATIC MODE) ====================

    async def handle_vwc_calibration_command(self, command_data):
        """
        Handle VWC calibration commands - delegates to CalibrationManager
        Calibration only runs in Automatic Mode

        Expected:
        {
            "action": "start_max" | "start_min" | "stop",
            "phase": "p0" | "p1" | "p2" | "p3"
        }
        """

        # Prüfe ob Automatic Mode aktiv
        current_mode = self.data_store.getDeep("CropSteering.ActiveMode") or ""
        if "Automatic" not in current_mode:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "VWC Calibration only available in Automatic Mode",
                },
                haEvent=True,
            )
            return

        # Delegate to CalibrationManager
        if self.calibration_manager:
            await self.calibration_manager.handle_vwc_calibration_command(command_data)
        else:
            _LOGGER.error(f"{self.room} - CalibrationManager not initialized")

    # NOTE: VWC calibration methods (start_vwc_max_calibration, start_vwc_min_calibration, 
    # stop_vwc_calibration, etc.) are now handled by OGBCSCalibrationManager
    # See handle_vwc_calibration_command() which delegates to self.calibration_manager

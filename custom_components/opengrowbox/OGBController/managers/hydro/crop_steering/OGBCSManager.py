import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ....data.OGBDataClasses.OGBPublications import OGBHydroAction, OGBWaterAction, OGBWaterPublication

from .OGBAdvancedSensor import OGBAdvancedSensor
from .OGBCSCalibrationManager import OGBCSCalibrationManager
from .OGBCSConfigurationManager import CSMode, OGBCSConfigurationManager
from ....utils.ambient import is_ambient_room, is_not_ambient_room

_LOGGER = logging.getLogger(__name__)


class OGBCSManager:
    # Frontend can request a full snapshot + recent history via HA events.
    REQUEST_EVENT = "RequestCropSteeringState"
    RESPONSE_EVENT = "CropSteeringState"
    MAX_RECENT_LOG_EVENTS = 100

    def __init__(self, hass, dataStore, eventManager, room, medium_manager=None):
        self.name = "OGB Crop Steering Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager
        self.medium_manager = medium_manager
        self.isInitialized = False
        # Buffer of recent LogForClient events so the dashboard can show history
        # immediately when it opens, without waiting for new events.
        self._recent_log_events = []

        # AMBIENT ROOM CHECK: Ambient rooms don't use Crop Steering
        if is_ambient_room(self.room):
            _LOGGER.debug(f"{self.room}: Crop Steering disabled - ambient room")
            return

        # Advanced sensor processing for TDR/VWC/EC calculations
        self.advanced_sensor = OGBAdvancedSensor()
        self.medium_type = "rockwool"  # Default, will be synced from medium manager

        # Calibration Manager - handles VWC max/min calibration
        self.calibration_manager = OGBCSCalibrationManager(
            room=room,
            data_store=dataStore,
            event_manager=eventManager,
            advanced_sensor=self.advanced_sensor,
            hass=hass,
            irrigate_callback=self._irrigate
        )
        
        # Configuration Manager - handles presets and medium adjustments
        self.config_manager = OGBCSConfigurationManager(
            data_store=dataStore,
            room=room
        )

        self.blockCheckIntervall = 60  # Check sensors every 60s in automatic mode
        self.max_irrigation_attempts = 5
        self.stability_tolerance = 1.5

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

        # Periodic state heartbeat tracking
        self._last_state_heartbeat_time = None

        # Manual phase change signalling - allows immediate restart of manual cycle
        self._manual_phase_changed_event = asyncio.Event()

        # Failsafe / learning state initialization
        self._init_failsafe_state()

        # Event subscriptions
        # NOTE: CropSteeringChanges is handled by OGBCastManager which validates 
        # Hydro.Mode and then calls our handle_mode_change() - no direct subscription needed!
        # self.event_manager.on("CropSteeringChanges", self.handle_mode_change)  # REMOVED - was causing double calls!
        self.event_manager.on(
            "VWCCalibrationCommand", self.handle_vwc_calibration_command
        )
        self.event_manager.on("MediumChange", self._on_medium_change)
        self.event_manager.on("CSManualPhaseChanged", self._on_manual_phase_changed)

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
                    _LOGGER.debug(f"{self.room} - Got medium type from GrowMedium: {self.medium_type}")
                elif isinstance(first_medium, dict) and "type" in first_medium:
                    # Dict format (from persistence)
                    self.medium_type = first_medium["type"].lower()
                    medium_found = True
                    _LOGGER.debug(f"{self.room} - Got medium type from dict: {self.medium_type}")

            # Priority 2: Fallback to dataStore CropSteering settings (only if no medium found)
            if not medium_found:
                stored_medium = self.data_store.getDeep("CropSteering.MediumType")
                if stored_medium:
                    self.medium_type = stored_medium.lower()
                    medium_found = True
                    _LOGGER.debug(f"{self.room} - Got medium type from CropSteering.MediumType: {self.medium_type}")

            # Priority 3: Default fallback
            if not medium_found:
                self.medium_type = "rockwool"
                _LOGGER.warning(f"{self.room} - No medium found, defaulting to: {self.medium_type}")

        except Exception as e:
            _LOGGER.warning(f"{self.room} - Could not sync medium type: {e}")
            self.medium_type = "rockwool"

        _LOGGER.debug(
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

    async def _on_manual_phase_changed(self, data=None):
        """Handle manual phase selector changes by interrupting the current manual cycle."""
        # Only relevant while manual mode runner is active
        if self._main_task and not self._main_task.done():
            new_phase = None
            if isinstance(data, dict):
                new_phase = data.get("phase")
            elif isinstance(data, str):
                new_phase = data
            if new_phase:
                new_phase = self._extract_phase_from_value(new_phase)
            _LOGGER.debug(
                f"{self.room} - Manual phase change requested: {new_phase}. "
                f"Signalling manual cycle to restart."
            )
            self._manual_phase_changed_event.set()

    # ==================== FAILSAFE & LEARNING ====================

    # Notificator instance (injected from OGBMainController)
    notificator = None

    # Failsafe / learning state
    _ABSOLUTE_VWC_MIN = 5.0
    _ABSOLUTE_VWC_MAX = 90.0
    _SENSOR_STUCK_THRESHOLD = 15
    _MAX_VWC_JUMP = 30.0
    _MAX_TOTAL_PUMP_SECONDS_PER_CYCLE = 300  # 5 minutes hard cap per saturation cycle
    _PUMP_SECONDS_INEFFECTIVE_THRESHOLD = 3  # after N shots, VWC must have risen

    def _init_failsafe_state(self):
        """Initialize in-memory failsafe/learning tracking."""
        self._vwc_history = []  # (timestamp, vwc) tuples for jump/stuck detection
        self._stuck_counter = 0
        self._irrigation_total_seconds = 0.0
        self._irrigation_shot_count = 0
        self._last_irrigation_vwc = None
        self._notification_cooldowns = {}
        self._failsafe_stop = None  # {"reason": str, "source": str} once a failsafe latched

    def _load_learned_values(self) -> Dict[str, Any]:
        """Load learned VWC bounds from DataStore (persisted across restarts)."""
        return {
            "max_saturation_vwc": self.data_store.getDeep("CropSteering.Learned.max_saturation_vwc"),
            "min_dryback_vwc": self.data_store.getDeep("CropSteering.Learned.min_dryback_vwc"),
            "field_capacity_vwc": self.data_store.getDeep("CropSteering.Learned.field_capacity_vwc"),
            "saturation_samples": self.data_store.getDeep("CropSteering.Learned.saturation_samples") or 0,
            "dryback_samples": self.data_store.getDeep("CropSteering.Learned.dryback_samples") or 0,
        }

    def _save_learned_value(self, key: str, value: Any):
        """Persist a single learned value."""
        self.data_store.setDeep(f"CropSteering.Learned.{key}", value)

    def _get_plant_info_for_preset(self) -> tuple:
        """Return (plant_phase, generative_week) for automatic preset calculation."""
        room_stage = self.data_store.get("plantStage")
        flower_stages = {"EarlyFlower", "MidFlower", "LateFlower", "Flush"}
        plant_phase = "flower" if room_stage in flower_stages else "veg"
        generative_week = 0
        try:
            generative_week = int(self.data_store.get("generativeWeek") or 0)
        except (ValueError, TypeError):
            generative_week = 0
        return plant_phase, generative_week

    def _get_automatic_preset(self, phase: str) -> Dict[str, Any]:
        """Get automatic preset for automatic mode (no user overrides)."""
        plant_phase, generative_week = self._get_plant_info_for_preset()
        return self.config_manager.get_automatic_preset(
            phase=phase,
            medium_type=self.medium_type,
            plant_phase=plant_phase,
            generative_week=generative_week,
        )

    def _update_learned_max_saturation(self, vwc: float):
        """Update learned maximum saturation VWC from P1 irrigation peaks."""
        learned = self._load_learned_values()
        current_max = learned["max_saturation_vwc"]
        if current_max is None or vwc > current_max:
            current_max = round(vwc, 1)
            self._save_learned_value("max_saturation_vwc", current_max)
            self._save_learned_value(
                "saturation_samples", (learned["saturation_samples"] or 0) + 1
            )
            _LOGGER.debug(
                f"{self.room} - Learned max_saturation_vwc updated to {current_max}%"
            )

    def _update_learned_field_capacity(self, vwc: float):
        """Update learned field capacity from stable post-irrigation VWC."""
        learned = self._load_learned_values()
        current_fc = learned["field_capacity_vwc"]
        # Only update if within plausible range and not above saturation
        max_sat = learned["max_saturation_vwc"] or self._ABSOLUTE_VWC_MAX
        if vwc < self._ABSOLUTE_VWC_MIN or vwc > max_sat:
            return
        if current_fc is None:
            self._save_learned_value("field_capacity_vwc", round(vwc, 1))
        else:
            # Exponential moving average for stability
            new_fc = round(current_fc * 0.7 + vwc * 0.3, 1)
            self._save_learned_value("field_capacity_vwc", new_fc)
        _LOGGER.debug(
            f"{self.room} - Learned field_capacity_vwc updated to {self._load_learned_values()['field_capacity_vwc']}%"
        )

    def _update_learned_min_dryback(self, vwc: float):
        """Update learned minimum dryback VWC from P3 night minima."""
        learned = self._load_learned_values()
        current_min = learned["min_dryback_vwc"]
        if current_min is None or vwc < current_min:
            current_min = round(vwc, 1)
            self._save_learned_value("min_dryback_vwc", current_min)
            self._save_learned_value(
                "dryback_samples", (learned["dryback_samples"] or 0) + 1
            )
            _LOGGER.debug(
                f"{self.room} - Learned min_dryback_vwc updated to {current_min}%"
            )

    def _get_safe_vwc_bounds(self, preset: Dict[str, Any]) -> Dict[str, float]:
        """
        Compute safe VWC bounds clamped by learned values and absolute limits.
        """
        learned = self._load_learned_values()
        max_sat = learned["max_saturation_vwc"]
        min_dry = learned["min_dryback_vwc"]

        hard_max = self._ABSOLUTE_VWC_MAX
        hard_min = self._ABSOLUTE_VWC_MIN

        # Clamp max by learned saturation (with small headroom) if available
        if max_sat is not None:
            hard_max = min(hard_max, max_sat + 5.0)

        # Clamp min by learned dryback (with small safety margin) if available
        if min_dry is not None:
            hard_min = max(hard_min, min_dry * 0.8)

        vwc_max = min(preset.get("VWCMax", self._ABSOLUTE_VWC_MAX), hard_max)
        vwc_target = min(preset.get("VWCTarget", vwc_max), vwc_max)
        vwc_min = max(preset.get("VWCMin", hard_min), hard_min)

        return {
            "VWCMax": vwc_max,
            "VWCTarget": vwc_target,
            "VWCMin": vwc_min,
        }

    def _record_sensor_reading(self, vwc: float):
        """Record VWC reading for jump/stuck detection."""
        from datetime import datetime
        self._vwc_history.append((datetime.now(), vwc))
        # Keep last 20 readings
        if len(self._vwc_history) > 20:
            self._vwc_history.pop(0)

    def _validate_sensor_reading(self, vwc: float) -> tuple:
        """
        Validate sensor reading for plausibility.
        Returns (valid: bool, reason: str | None).
        """
        if vwc is None:
            return False, "Sensor returned None"
        try:
            vwc_f = float(vwc)
        except (ValueError, TypeError):
            return False, f"Sensor returned non-numeric value: {vwc!r}"

        if vwc_f < 0 or vwc_f > 100:
            return False, f"VWC out of physical range: {vwc_f:.1f}%"

        if len(self._vwc_history) >= 2:
            last_vwc = self._vwc_history[-1][1]
            jump = abs(vwc_f - last_vwc)
            if jump > self._MAX_VWC_JUMP:
                return False, f"VWC jump too large: {last_vwc:.1f}% → {vwc_f:.1f}%"

        # Stuck detection: same value for N readings
        if len(self._vwc_history) >= self._SENSOR_STUCK_THRESHOLD:
            recent = [v for (_, v) in self._vwc_history[-self._SENSOR_STUCK_THRESHOLD:]]
            if all(round(v, 1) == round(recent[0], 1) for v in recent):
                self._stuck_counter += 1
                if self._stuck_counter >= 2:
                    return False, f"Sensor stuck at {vwc_f:.1f}% for {self._SENSOR_STUCK_THRESHOLD} readings"
            else:
                self._stuck_counter = 0

        return True, None

    def _reset_irrigation_tracking(self):
        """Reset per-cycle irrigation tracking."""
        self._irrigation_total_seconds = 0.0
        self._irrigation_shot_count = 0
        self._last_irrigation_vwc = None

    def _record_irrigation(self, duration: float, vwc_before: float):
        """Record irrigation shot for failsafe tracking."""
        self._irrigation_total_seconds += duration
        self._irrigation_shot_count += 1
        self._last_irrigation_vwc = vwc_before

    def _is_irrigation_ineffective(self, vwc_after: float) -> bool:
        """Check if recent irrigation shots did not increase VWC."""
        if self._last_irrigation_vwc is None or self._irrigation_shot_count == 0:
            return False
        # After N shots, VWC should have risen at least a bit
        if self._irrigation_shot_count >= self._PUMP_SECONDS_INEFFECTIVE_THRESHOLD:
            return (vwc_after - self._last_irrigation_vwc) < 0.5
        return False

    async def _send_critical_notification(self, title: str, message: str):
        """Send critical notification via notificator with cooldown."""
        from datetime import datetime
        now = datetime.now()
        cooldown = self._notification_cooldowns.get(title)
        if cooldown and (now - cooldown).total_seconds() < 3600:
            _LOGGER.debug(f"{self.room} - Critical notification cooldown active: {title}")
            return
        self._notification_cooldowns[title] = now

        if self.notificator and hasattr(self.notificator, "critical"):
            try:
                await self.notificator.critical(message=message, title=title)
            except Exception as e:
                _LOGGER.error(f"{self.room} - Failed to send critical notification: {e}")
        else:
            _LOGGER.warning(f"{self.room} - No notificator available for critical alert: {title}")

    def _evaluate_failsafe_condition(self, vwc: float) -> Optional[str]:
        """
        Pure failsafe evaluation - returns a reason key (str) if a guard is
        triggered, or None if everything is safe. No side effects.
        """
        valid, _ = self._validate_sensor_reading(vwc)
        if not valid:
            return "sensor_invalid"
        if vwc >= self._ABSOLUTE_VWC_MAX:
            return "flood_guard"
        if vwc <= self._ABSOLUTE_VWC_MIN:
            return "dryout_guard"
        if self._is_irrigation_ineffective(vwc):
            return "irrigation_ineffective"
        if self._irrigation_total_seconds >= self._MAX_TOTAL_PUMP_SECONDS_PER_CYCLE:
            return "max_runtime"
        return None

    def _clear_failsafe(self, reason: str):
        """Clear a latched failsafe stop, logging the re-arm."""
        if self._failsafe_stop is not None:
            _LOGGER.warning(f"{self.room} - Failsafe cleared ({reason}), irrigation re-armed")
        self._failsafe_stop = None

    async def _run_failsafe_checks(self, vwc: float, source: str = "automatic") -> tuple:
        """
        Run all failsafe checks. Returns (safe: bool, reason: str | None).
        If unsafe, stops irrigation and sends critical notification.
        Once a failsafe has latched (_failsafe_stop), subsequent cycles are
        silent: no repeated "off" commands, notifications or warnings until
        the triggering condition clears or the latch is explicitly reset.
        """
        reason = self._evaluate_failsafe_condition(vwc)

        # Condition cleared -> re-arm and resume
        if reason is None:
            if self._failsafe_stop is not None:
                self._clear_failsafe("condition cleared")
            return True, None

        # Already latched for the same reason -> stay stopped, stay quiet
        if self._failsafe_stop is not None and self._failsafe_stop.get("reason") == reason:
            return False, reason

        # First trigger (or reason changed) -> latch, stop, notify once
        self._failsafe_stop = {"reason": reason, "source": source}

        if reason == "sensor_invalid":
            valid, invalid_reason = self._validate_sensor_reading(vwc)
            await self._stop_all_irrigation(f"Sensor validation failed: {invalid_reason}")
            await self._send_critical_notification(
                f"OGB {self.room}: CropSteering Sensor Alert",
                f"{invalid_reason}\n\nMode: {source}\nAll irrigation stopped. Please check the VWC sensor.",
            )
        elif reason == "flood_guard":
            await self._stop_all_irrigation(f"VWC {vwc:.1f}% >= absolute max {self._ABSOLUTE_VWC_MAX}%")
            await self._send_critical_notification(
                f"OGB {self.room}: CropSteering FLOOD GUARD",
                f"VWC {vwc:.1f}% is at or above the absolute maximum of {self._ABSOLUTE_VWC_MAX}%. "
                f"All irrigation has been stopped to prevent flooding. Check sensor and pump.",
            )
        elif reason == "dryout_guard":
            await self._stop_all_irrigation(f"VWC {vwc:.1f}% <= absolute min {self._ABSOLUTE_VWC_MIN}%")
            await self._send_critical_notification(
                f"OGB {self.room}: CropSteering DRYOUT GUARD",
                f"VWC {vwc:.1f}% is at or below the absolute minimum of {self._ABSOLUTE_VWC_MIN}%. "
                f"All irrigation has been stopped. Sensor may be faulty or medium is extremely dry.",
            )
        elif reason == "irrigation_ineffective":
            await self._stop_all_irrigation(
                f"Irrigation ineffective: {self._irrigation_shot_count} shots, "
                f"total {self._irrigation_total_seconds:.0f}s, VWC did not rise sufficiently"
            )
            await self._send_critical_notification(
                f"OGB {self.room}: CropSteering Irrigation Ineffective",
                f"After {self._irrigation_shot_count} irrigation shots ({self._irrigation_total_seconds:.0f}s total), "
                f"VWC did not rise sufficiently. Pump may be empty, blocked, or the sensor is not responding. "
                f"All irrigation stopped.",
            )
        elif reason == "max_runtime":
            await self._stop_all_irrigation(
                f"Total irrigation runtime {self._irrigation_total_seconds:.0f}s exceeded max {self._MAX_TOTAL_PUMP_SECONDS_PER_CYCLE}s"
            )
            await self._send_critical_notification(
                f"OGB {self.room}: CropSteering Max Runtime Reached",
                f"Total irrigation runtime for this cycle reached {self._irrigation_total_seconds:.0f}s. "
                f"Stopping to prevent over-watering. Check irrigation setup.",
            )

        return False, reason

    async def _stop_all_irrigation(self, reason: str):
        """Turn off all drippers and reset irrigation tracking."""
        _LOGGER.warning(f"{self.room} - STOPPING ALL IRRIGATION: {reason}")
        await self._turn_off_all_drippers()
        self._reset_irrigation_tracking()

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
        
        _LOGGER.debug(f"===== {self.room} CropSteering DataStore Dump =====")
        _LOGGER.debug(f"ActiveMode: {cs_data.get('ActiveMode')}")
        _LOGGER.debug(f"Active: {cs_data.get('Active')}")
        _LOGGER.debug(f"CropPhase: {cs_data.get('CropPhase')}")
        _LOGGER.debug(f"MediumType: {cs_data.get('MediumType')}")
        
        # Calibration values
        calibration = cs_data.get('Calibration', {})
        _LOGGER.debug(f"Calibration: {calibration}")
        
        # Substrate (user settings)
        substrate = cs_data.get('Substrate', {})
        _LOGGER.debug(f"Substrate (user settings): {substrate}")
        
        # Current sensor values
        _LOGGER.debug(f"vwc_current: {cs_data.get('vwc_current')}")
        _LOGGER.debug(f"ec_current: {cs_data.get('ec_current')}")
        _LOGGER.debug(f"===== End Dump =====")
        
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
            
            _LOGGER.debug(
                f"{self.room} - Updated number entity {entity_id} to {value:.1f}"
            )
            
        except Exception as e:
            _LOGGER.warning(f"{self.room} - Failed to update number entity for {parameter}.{phase}: {e}")

    async def _set_crop_phase_and_update_selector(self, phase: str):
        """
        Set CropSteering.CropPhase and update the HA phase selector entity
        so the UI reflects internal phase transitions.
        """
        phase_lower = phase.lower() if phase else "p0"
        self.data_store.setDeep("CropSteering.CropPhase", phase_lower)
        self._clear_failsafe("phase change")

        if not self.hass:
            _LOGGER.debug(f"{self.room} - Cannot update phase selector: hass not available")
            return

        try:
            entity_id = f"select.ogb_cropsteering_phases_{self.room.lower()}"
            await self.hass.services.async_call(
                domain="select",
                service="select_option",
                service_data={"entity_id": entity_id, "option": phase_lower.upper()},
                blocking=False,
            )
            _LOGGER.debug(
                f"{self.room} - Updated phase selector {entity_id} to {phase_lower.upper()}"
            )
        except Exception as e:
            _LOGGER.warning(f"{self.room} - Failed to update phase selector to {phase_lower}: {e}")

    async def _sync_adjusted_presets_to_entities(self, plant_phase: str, gen_week: int):
        """
        Write the final adjusted preset values back to HA number entities,
        so the UI shows what thresholds are actually active.
        Only fires HA service calls when values change (tracked by old values).
        """
        if not self.hass:
            return

        params = [
            ("VWCTarget", "vwc_target"),
            ("VWCMin", "vwc_min"),
            ("VWCMax", "vwc_max"),
            ("ECTarget", "ec_target"),
        ]
        for phase in ("p0", "p1", "p2", "p3"):
            preset = self._get_adjusted_preset(phase, plant_phase, gen_week)
            for key, ent_suffix in params:
                val = preset.get(key)
                if val is not None:
                    entity_id = f"number.ogb_cropsteering_{phase}_{ent_suffix}_{self.room.lower()}"
                    try:
                        await self.hass.services.async_call(
                            domain="number",
                            service="set_value",
                            service_data={"entity_id": entity_id, "value": float(val)},
                            blocking=True,
                        )
                    except Exception:
                        pass

    # ==================== ENTRY POINT ====================
    async def handle_mode_change(self, data):
        """SINGLE entry point for all mode changes.
        
        Called from:
        1. CropSteeringChanges event via CastManager (when user changes CS sub-selector)
        2. CastManager.HydroModeChange() (when user selects Crop-Steering hydro mode)
        
        CRITICAL: This is the ONLY place where CS tasks are started/stopped!
        """
        requested_mode = self.data_store.getDeep("CropSteering.ActiveMode") or "Automatic"
        _LOGGER.debug(f"{self.room} - CropSteering handle_mode_change: requested={requested_mode}, data={data}")

        # ===== STEP 1: Parse mode FIRST to know what to do =====
        mode = self._parse_mode(requested_mode)
        _LOGGER.debug(f"{self.room} - CropSteering parsed mode: {mode}")

        # ===== STEP 2: Handle STOP modes (Disabled/Config) - ALWAYS stop, no checks =====
        if mode == CSMode.DISABLED:
            _LOGGER.debug(f"{self.room} - CropSteering DISABLED - stopping all operations")
            await self._force_stop_all()
            self.data_store.setDeep("CropSteering.Active", False)
            # CRITICAL: Reset debounce so next mode change works immediately
            self._last_mode_change_mode = None
            self._last_mode_change_time = None
            # CRITICAL: Reset P1/P2/P3 state tracking so next Automatic start operates immediately
            self._reset_p1_state_tracking()
            self._reset_p2_state_tracking()
            self._reset_p3_state_tracking()
            return

        if mode == CSMode.CONFIG:
            _LOGGER.debug(f"{self.room} - CropSteering CONFIG mode - stopping operations")
            await self._force_stop_all()
            # CRITICAL: Reset debounce so next mode change works immediately
            self._last_mode_change_mode = None
            self._last_mode_change_time = None
            # CRITICAL: Reset P1/P2/P3 state tracking so next Automatic start operates immediately
            self._reset_p1_state_tracking()
            self._reset_p2_state_tracking()
            self._reset_p3_state_tracking()
            return

        # ===== STEP 3: For RUN modes, validate environment =====
        hydro_mode = self.data_store.getDeep("Hydro.Mode")
        if hydro_mode != "Crop-Steering":
            _LOGGER.debug(f"{self.room} - CropSteering BLOCKED: Hydro.Mode='{hydro_mode}' != 'Crop-Steering'")
            await self._force_stop_all()
            return

        # ===== STEP 4: Check if task already running =====
        # If a task is running, DON'T restart unless mode actually changed
        if self._main_task and not self._main_task.done():
            # Task is running - check if we should restart
            if self._last_mode_change_mode == requested_mode:
                _LOGGER.debug(f"{self.room} - CS task already running for '{requested_mode}', ignoring duplicate call")
                return
            else:
                _LOGGER.debug(f"{self.room} - Mode changed from '{self._last_mode_change_mode}' to '{requested_mode}', restarting...")
                await self._cancel_main_task()
                # Reset phase state when switching between Automatic/Manual so stale
                # counters from the previous mode don't corrupt the new mode.
                _LOGGER.debug(f"{self.room} - Resetting P1/P2/P3 state tracking due to mode change")
                self._reset_p1_state_tracking()
                self._reset_p2_state_tracking()
                self._reset_p3_state_tracking()
                self.data_store.setDeep("CropSteering.shotCounter", 0)
                self.data_store.setDeep("CropSteering.lastIrrigationTime", None)
        
        # ===== STEP 5: Debounce - prevent rapid restarts =====
        now = datetime.now()
        if self._last_mode_change_time and self._last_mode_change_mode == requested_mode:
            elapsed = (now - self._last_mode_change_time).total_seconds()
            if elapsed < self._mode_change_debounce_seconds:
                _LOGGER.debug(f"{self.room} - DEBOUNCED: Same mode '{requested_mode}' within {elapsed:.1f}s")
                return
        
        self._last_mode_change_time = now
        self._last_mode_change_mode = requested_mode

        # ===== STEP 6: Validate prerequisites =====
        #multimediumCtrl = self.data_store.getDeep("controlOptions.multiMediumControl")
        #if multimediumCtrl is False:
        #    _LOGGER.error(f"{self.room} - CropSteering requires multiMediumControl=True")
        #    return

        # Sync medium type
        if not self.isInitialized:
            await self._sync_medium_type()
            self.isInitialized = True

        # DEBUG: Dump CropSteering config to see what's actually stored
        self.debug_dump_cropsteering_config()

        # Get sensor data (best-effort; don't abort if sensors aren't ready yet,
        # otherwise the cycle never starts when mode is set before sensor data arrives)
        sensor_data = await self._get_sensor_averages()
        if sensor_data:
            self.data_store.setDeep("CropSteering.vwc_current", sensor_data["vwc"])
            self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])
        else:
            _LOGGER.warning(
                f"{self.room} - No sensor data available yet, starting CropSteering anyway. "
                f"The cycle will wait for valid VWC/EC readings."
            )
            self.data_store.setDeep("CropSteering.vwc_current", 0)
            self.data_store.setDeep("CropSteering.ec_current", 0)
            await self._log_missing_sensors()

        # Mark crop steering as active
        self.data_store.setDeep("CropSteering.Active", True)
        # New run started -> re-arm any latched failsafe
        self._clear_failsafe("run mode started")

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
            _LOGGER.debug(f"{self.room} - STARTING AUTOMATIC cycle")
            self._main_task = asyncio.create_task(self._automatic_cycle())
        elif mode.value.startswith("Manual"):
            # For Manual mode, get phase from CropPhase selector (set by Phases entity)
            stored_phase = self.data_store.getDeep("CropSteering.CropPhase")
            
            # FIX: Handle case where stored_phase is None or invalid
            if stored_phase:
                stored_phase_lower = stored_phase.lower()
                if stored_phase_lower in ["p0", "p1", "p2", "p3"]:
                    phase = stored_phase_lower
                    _LOGGER.debug(f"{self.room} - Using phase from CropPhase selector: {phase}")
                else:
                    # Try to extract phase from stored_phase value (e.g., "P1" -> "p1")
                    phase = self._extract_phase_from_value(stored_phase)
                    _LOGGER.debug(f"{self.room} - Extracted phase from stored value: {phase}")
            else:
                # Fallback: extract from mode.value (e.g., "MANUAL_P1" -> "p1")
                phase = self._extract_phase_from_mode(mode)
                _LOGGER.debug(f"{self.room} - Extracted phase from mode enum: {phase}")
            
            _LOGGER.debug(f"{self.room} - STARTING MANUAL cycle for phase {phase}")
            self._main_task = asyncio.create_task(self._run_manual_mode())
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
        _LOGGER.debug(f"{self.room} - FORCE STOP: Cancelling all CS operations...")
        
        # Cancel main task
        if self._main_task:
            task_done = self._main_task.done()
            _LOGGER.debug(f"{self.room} - Main task exists, done={task_done}")
            if not task_done:
                _LOGGER.debug(f"{self.room} - Cancelling main task...")
                self._main_task.cancel()
                try:
                    await self._main_task
                except asyncio.CancelledError:
                    _LOGGER.debug(f"{self.room} - Main task cancelled successfully")
                except Exception as e:
                    _LOGGER.error(f"{self.room} - Error cancelling main task: {e}")
        else:
            _LOGGER.debug(f"{self.room} - No main task to cancel")
        
        # Cancel calibration task
        if self._calibration_task:
            task_done = self._calibration_task.done()
            _LOGGER.debug(f"{self.room} - Calibration task exists, done={task_done}")
            if not task_done:
                _LOGGER.debug(f"{self.room} - Cancelling calibration task...")
                self._calibration_task.cancel()
                try:
                    await self._calibration_task
                except asyncio.CancelledError:
                    _LOGGER.debug(f"{self.room} - Calibration task cancelled successfully")
                except Exception as e:
                    _LOGGER.error(f"{self.room} - Error cancelling calibration task: {e}")
        
        self._main_task = None
        self._calibration_task = None
        self._irrigation_in_progress = False
        
        # Turn off drippers
        await self._turn_off_all_drippers()
        _LOGGER.debug(f"{self.room} - FORCE STOP COMPLETE: All CS operations stopped")

    async def handle_stop(self, event=None):
        """Stop handler for external stop events"""
        await self.stop_all_operations()

    def _reset_p1_state_tracking(self):
        """Reset P1 state tracking variables.
        
        Called when entering Config/Disabled mode so that when
        Automatic mode starts again, it will irrigate immediately
        instead of waiting for the remaining interval time.
        """
        _LOGGER.debug(f"{self.room} - Resetting P1 state tracking (irrigation will start fresh)")
        self.data_store.setDeep("CropSteering.p1_start_vwc", None)
        self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
        self.data_store.setDeep("CropSteering.p1_last_vwc", None)
        self.data_store.setDeep("CropSteering.p1_last_irrigation_time", None)

    def _reset_p3_state_tracking(self):
        """Reset P3 state tracking variables.

        Called when entering Config/Disabled mode or when P3 phase is left,
        so that when P3 starts again, emergency irrigation can start fresh.
        """
        _LOGGER.debug(f"{self.room} - Resetting P3 state tracking")
        self.data_store.setDeep("CropSteering.p3_emergency_count", 0)
        self.data_store.setDeep("CropSteering.p3_last_emergency_time", None)
        self.data_store.setDeep("CropSteering.p3_last_irrigation_time", None)

    def _reset_p2_state_tracking(self):
        """Reset P2 state tracking variables.

        Called when entering Config/Disabled mode or when P2 phase is left,
        so that when P2 starts again, it will check immediately
        instead of waiting for the remaining interval time.
        """
        _LOGGER.debug(f"{self.room} - Resetting P2 state tracking (checks will start fresh)")
        self.data_store.setDeep("CropSteering.p2_last_check_time", None)
        self.data_store.setDeep("CropSteering.p2_shot_count", 0)
        self.data_store.setDeep("CropSteering.p2_last_irrigation_time", None)

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
                _LOGGER.debug(f"{self.room} - Manual mode using CropPhase: {stored_phase}")
                return CSMode[f"MANUAL_{stored_phase.upper()}"]
            
            # Default to P0 if nothing else specified
            _LOGGER.warning(f"{self.room} - Manual mode defaulting to P0 (no phase specified)")
            return CSMode.MANUAL_P0
        return CSMode.DISABLED

    # ==================== SENSOR DATA ====================

    async def _get_sensor_averages(self) -> Optional[Dict[str, Any]]:
        """
        Get averaged sensor data from the GrowMedium objects.

        The GrowMedium is the single source of truth for sensor readings.
        current_moisture is already the calibrated VWC percentage and is used
        directly (no second calibration). We read current_moisture,
        current_ec and current_temp from every medium, ignore None/0 values,
        and compute the room-wide average plus pore-water EC and validation.
        """
        vwc_values = []
        bulk_ec_values = []
        temp_values = []

        # Sync medium type if not initialized
        if not self.isInitialized:
            await self._sync_medium_type()
            self.isInitialized = True

        # Read from live GrowMedium objects if available
        mediums = []
        if self.medium_manager is not None:
            try:
                mediums = self.medium_manager.get_mediums() or []
            except Exception as e:
                _LOGGER.warning(f"{self.room} - Could not read mediums from medium_manager: {e}")

        if mediums:
            for medium in mediums:
                raw_moisture = getattr(medium, "current_moisture", None)
                _LOGGER.warning(f"{self.room} - VWC values from medium: {raw_moisture}")
                if raw_moisture:
                    try:
                        raw_val = float(raw_moisture)
                        if raw_val != 0:
                            vwc_values.append(raw_val)
                    except (ValueError, TypeError) as e:
                        _LOGGER.debug(f"{self.room} - VWC conversion error from medium: {e}")

                raw_ec = getattr(medium, "current_ec", None)
                _LOGGER.warning(f"{self.room} - EC conversion Values: {raw_ec}")
                if raw_ec:
                    try:
                        ec_val = float(raw_ec)
                        if ec_val != 0:
                            if ec_val > 20:
                                ec_val = ec_val / 1000
                                _LOGGER.debug(f"{self.room} - EC auto-converted from µS to mS: {raw_ec} -> {ec_val}")
                            bulk_ec_values.append(ec_val)
                    except (ValueError, TypeError):
                        pass

                raw_temp = getattr(medium, "current_temp", None)
                if raw_temp:
                    try:
                        temp_val = float(raw_temp)
                        if temp_val != 0:
                            temp_values.append(temp_val)
                    except (ValueError, TypeError):
                        pass

        # Fallback to legacy workData only if no live medium data is available
        if not vwc_values and not bulk_ec_values:
            _LOGGER.error(f"{self.room} - WORKDATA MOISTURE : {self.data_store.getDeep('workData.moisture')}")
            for item in self.data_store.getDeep("workData.moisture") or []:
                raw = item.get("value")
                if raw is None:
                    continue
                try:
                    raw_val = float(raw)
                    if raw_val != 0:
                        vwc_values.append(raw_val)
                except (ValueError, TypeError) as e:
                    _LOGGER.error(f"{self.room} - VWC conversion error from workData: {e}")

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
            validation = SimpleNamespace(
                issues=[],
                warnings=[],
                recommendations=[],
                is_valid=False,
                corrected_values={}
            )

        result = {
            "vwc": round(avg_vwc, 1),
            "ec": round(avg_bulk_ec, 3),
            "pore_ec": round(pore_ec, 3),
            "temperature": round(avg_temp, 1),
            "validation": validation,
            "source": "medium" if mediums else "workData",
        }

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
        """Get dripper devices from pump capabilities.

        CRITICAL: Only devices explicitly labeled as 'dripper' are selected when
        any such label exists. If no 'dripper' labels are present, we fall back
        to matching 'dripper' in the device name so legacy setups keep working.
        This prevents crop-steering from accidentally turning on non-dripper
        pumps (e.g. reservoir, circulation, or air pumps).
        """
        # AMBIENT ROOM CHECK: Ambient rooms don't have drippers
        if is_ambient_room(self.room):
            return []

        dripperDevices = self.data_store.getDeep("capabilities.canPump")
        if not dripperDevices:
            _LOGGER.warning(f"{self.room} - _get_drippers: No canPump capability found!")
            return []

        devices = dripperDevices.get("devEntities", [])
        if not devices:
            _LOGGER.warning(f"{self.room} - _get_drippers: No pump devices found!")
            return []

        device_data = dripperDevices.get("deviceData", {})

        # Check whether any device carries an explicit 'dripper' label.
        # When at least one device has the label we make labels authoritative
        # and ignore name-only matches, so unrelated pumps are never started.
        any_labeled_dripper = any(
            any(lbl.lower() == "dripper" for lbl in device_data.get(dev, {}).get("labels", []))
            for dev in devices
        )

        if any_labeled_dripper:
            _LOGGER.debug(
                f"{self.room} - _get_drippers: 'dripper' label found; label-based selection is authoritative"
            )
        else:
            _LOGGER.warning(
                f"{self.room} - _get_drippers: No 'dripper' label found; falling back to name matching. "
                f"Label your actual dripper pumps with the 'dripper' label to avoid starting wrong pumps."
            )

        dripper_devices = []
        for dev in devices:
            labels = device_data.get(dev, {}).get("labels", [])
            has_label = any(lbl.lower() == "dripper" for lbl in labels)
            has_name = "dripper" in dev.lower()

            if has_label:
                dripper_devices.append(dev)
                _LOGGER.debug(f"{self.room} - _get_drippers: selected by label: {dev}")
            elif not any_labeled_dripper and has_name:
                dripper_devices.append(dev)
                _LOGGER.warning(
                    f"{self.room} - _get_drippers: selected by name fallback (no 'dripper' labels): {dev}"
                )
            else:
                _LOGGER.debug(
                    f"{self.room} - _get_drippers: skipped non-dripper pump: {dev} (labels={labels})"
                )

        if not dripper_devices:
            _LOGGER.warning(
                f"{self.room} - _get_drippers: No dripper devices found in: {devices}"
            )

        return dripper_devices

    async def _filter_capabilities_for_crop_steering(self):
        """Filter capabilities.canPump to show only dripper devices for Crop-Steering mode.

        This ensures the UI shows the same device count that irrigation will actually use.
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

            # Use the same selection logic as the irrigation code path
            dripper_devices = self._get_drippers()

            # Update capabilities with filtered list for Crop-Steering mode
            filtered_capabilities = pump_capabilities.copy()
            filtered_capabilities["devEntities"] = dripper_devices
            filtered_capabilities["count"] = len(dripper_devices)
            filtered_capabilities["state"] = len(dripper_devices) > 0

            # Temporarily override capabilities for UI display
            self.data_store.setDeep("capabilities.canPump", filtered_capabilities)

            _LOGGER.debug(f"{self.room} - Filtered capabilities.canPump for Crop-Steering: {all_devices} → {dripper_devices}")

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
        These values are read directly for the phase handlers; the full preset
        (including any user overrides for VWC/EC thresholds) is passed separately.
        
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
        
        _LOGGER.debug(
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

    def _get_active_vwc_target(self, phase: str) -> Optional[float]:
        """Return the effective VWC target for a phase based on the active mode.

        Manual mode reads the user's datastore settings (_get_manual_phase_settings),
        automatic mode uses the bulletproof preset with safe bounds.
        Returns None if the target cannot be determined.
        """
        try:
            active_mode = self.data_store.getDeep("CropSteering.ActiveMode") or "Automatic"
            if isinstance(active_mode, str) and active_mode.startswith("Manual"):
                settings = self._get_manual_phase_settings(phase)
                target = settings.get("VWCTarget", {}).get("value")
                if target is not None:
                    return float(target)
            preset = self._get_automatic_preset(phase)
            return float(self._get_safe_vwc_bounds(preset)["VWCTarget"])
        except Exception:
            return None

    def _get_calibration_snapshot(self) -> Dict[str, Any]:
        """Structured calibration data for the frontend calibration cards."""
        learned = self._load_learned_values()
        return {
            "P1": {"VWCMax": self.data_store.getDeep("CropSteering.Calibration.p1.VWCMax")},
            "P2": {"VWCMax": self.data_store.getDeep("CropSteering.Calibration.p2.VWCMax")},
            "P3": {"VWCMin": self.data_store.getDeep("CropSteering.Calibration.p3.VWCMin")},
            "Learned": learned,
        }

    def _build_cs_log(self, message: str, phase: Optional[str] = None, calibration: bool = False, **extra) -> Dict[str, Any]:
        """Build a LogForClient payload with the active VWC target attached.

        Attaches the phase-correct VWC target so the frontend can always
        display the real threshold for the current phase. Set `calibration=True`
        to attach the full structured calibration snapshot as well.
        """
        payload = {
            "Name": self.room,
            "Type": "CSLOG",
            "Message": message,
        }
        if phase is None:
            phase = self.data_store.getDeep("CropSteering.CropPhase") or "p0"
        target = self._get_active_vwc_target(phase)
        if target is not None:
            payload["VWCTarget"] = round(float(target), 1)
        if calibration:
            payload["Calibration"] = self._get_calibration_snapshot()
        payload.update(extra)
        return payload

    async def _emit_state_heartbeat(self, force: bool = False):
        """Emit a lightweight state heartbeat so the frontend always knows mode/phase/target/calibration.

        Rate-limited to 60 seconds by default; pass force=True to emit immediately
        (used on mode start and phase changes). This is non-critical and must never
        crash the cycle.
        """
        now = datetime.now()
        last = self._last_state_heartbeat_time
        interval_seconds = 60
        if not force and last and (now - last).total_seconds() < interval_seconds:
            return
        self._last_state_heartbeat_time = now

        phase = self.data_store.getDeep("CropSteering.CropPhase") or "p0"
        active_mode = self.data_store.getDeep("CropSteering.ActiveMode") or "Automatic"
        target = self._get_active_vwc_target(phase)
        vwc_current = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
        ec_current = float(self.data_store.getDeep("CropSteering.ec_current") or 0)

        try:
            payload = {
                "Name": self.room,
                "Type": "CSSTATE",
                "Message": f"Crop Steering state: {active_mode} {phase}",
                "mode": active_mode,
                "cropMode": active_mode,
                "phase": phase,
                "isNightMode": not self._is_lights_on(),
                "VWCTarget": round(float(target), 1) if target is not None else None,
                "VWC": round(vwc_current, 1) if vwc_current > 0 else None,
                "EC": round(ec_current, 2) if ec_current > 0 else None,
                "Calibration": self._get_calibration_snapshot(),
            }
            await self.event_manager.emit(
                "LogForClient", payload, haEvent=True, debug_type="DEBUG"
            )
        except Exception as e:
            _LOGGER.debug(
                f"{self.room} - State heartbeat emit error (non-critical): {e}"
            )

    def register_event_handlers(self):
        """Register internal and HA event handlers for dashboard state snapshots."""
        # Avoid duplicate registration if called multiple times.
        if getattr(self, "_cs_event_handlers_registered", False):
            return
        self._cs_event_handlers_registered = True
        self.event_manager.on("LogForClient", self._on_log_for_client_event)
        self.hass.bus.async_listen(self.REQUEST_EVENT, self._handle_request_state)
        _LOGGER.debug(f"{self.room} - Crop steering event handlers registered")

    def _on_log_for_client_event(self, payload):
        """Capture recent LogForClient events so they can be replayed on dashboard load."""
        if not isinstance(payload, dict):
            return
        if str(payload.get("Name", "")).lower() != str(self.room).lower():
            return
        # Skip the snapshot events themselves to avoid circular growth.
        if payload.get("Type") == "CSSTATE" and "state:" in (payload.get("Message") or ""):
            return
        entry = {
            "time_fired": datetime.now().isoformat(),
            "payload": payload,
        }
        self._recent_log_events.append(entry)
        if len(self._recent_log_events) > self.MAX_RECENT_LOG_EVENTS:
            self._recent_log_events.pop(0)

    async def _handle_request_state(self, event):
        """Handle a frontend request for the current crop steering state + history."""
        try:
            event_data = getattr(event, "data", {}) or {}
            request_id = event_data.get("requestId") or event_data.get("request_id")
            requested_room = str(event_data.get("room") or "").lower()

            if requested_room and requested_room != str(self.room).lower():
                return

            _LOGGER.debug(
                f"{self.room} - Crop steering state request received (request_id={request_id})"
            )

            phase = self.data_store.getDeep("CropSteering.CropPhase") or "p0"
            active_mode = self.data_store.getDeep("CropSteering.ActiveMode") or "Automatic"
            target = self._get_active_vwc_target(phase)
            vwc_current = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
            ec_current = float(self.data_store.getDeep("CropSteering.ec_current") or 0)

            state = {
                "mode": active_mode,
                "cropMode": active_mode,
                "phase": phase,
                "isNightMode": not self._is_lights_on(),
                "VWCTarget": round(float(target), 1) if target is not None else None,
                "VWC": round(vwc_current, 1) if vwc_current > 0 else None,
                "EC": round(ec_current, 2) if ec_current > 0 else None,
                "Calibration": self._get_calibration_snapshot(),
            }

            self.hass.bus.async_fire(
                self.RESPONSE_EVENT,
                {
                    "requestId": request_id,
                    "room": self.room,
                    "state": state,
                    "events": self._recent_log_events,
                },
            )
        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to handle crop steering state request: {e}")

    async def _calibrate_p1_vwc_max(self, vwc: float, cap: Optional[float] = None):
        """Store observed saturation VWC as calibrated VWCMax for P1."""
        if vwc is None or vwc <= 0:
            return
        calibrated_value = min(vwc, cap) if cap is not None and cap > 0 else vwc
        calibrated_value = round(calibrated_value, 1)
        self.data_store.setDeep("CropSteering.Calibration.p1.VWCMax", calibrated_value)
        self.data_store.setDeep(
            "CropSteering.Calibration.p1.timestamp", datetime.now().isoformat()
        )
        await self._update_number_entity("VWCMax", "p1", calibrated_value)
        await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})
        _LOGGER.debug(f"{self.room} - P1: Calibrated VWCMax to {calibrated_value:.1f}%")
        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(
                f"P1: Calibrated VWCMax to {calibrated_value:.1f}%",
                phase="p1",
                calibration=True,
            ),
            haEvent=True,
        )

    async def _calibrate_p3_vwc_min(self, vwc: float):
        """Track night dryback minima and store a consistent VWCMin for P3."""
        if vwc is None or vwc <= 0:
            return
        p3_vwc_mins = self.data_store.getDeep("CropSteering.p3_night_vwc_mins") or []
        p3_vwc_mins.append(round(vwc, 1))
        if len(p3_vwc_mins) > 5:
            p3_vwc_mins.pop(0)
        self.data_store.setDeep("CropSteering.p3_night_vwc_mins", p3_vwc_mins)

        if len(p3_vwc_mins) >= 2 and max(p3_vwc_mins) - min(p3_vwc_mins) <= 2.0:
            avg_vwc = sum(p3_vwc_mins) / len(p3_vwc_mins)
            _LOGGER.debug(
                f"{self.room} - P3: Consistent night minimum {avg_vwc:.1f}% - auto-calibrating VWCMin"
            )
            self.data_store.setDeep(
                "CropSteering.Calibration.p3.VWCMin", round(avg_vwc, 1)
            )
            self.data_store.setDeep(
                "CropSteering.Calibration.p3.timestamp", datetime.now().isoformat()
            )
            await self._update_number_entity("VWCMin", "p3", avg_vwc)
            await self.event_manager.emit(
                "SaveState", {"source": "CropSteeringCalibration"}
            )
            await self.event_manager.emit(
                "LogForClient",
                self._build_cs_log(
                    f"P3: Auto-calibrated VWCMin to {avg_vwc:.1f}% (based on {len(p3_vwc_mins)} consistent night minima)",
                    phase="p3",
                    calibration=True,
                ),
                haEvent=True,
            )

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
        is_light_on = self._is_lights_on()

        # Get plant info from GrowMedium (authoritative source)
        plant_phase, gen_week = self._get_plant_info_from_medium()

        # Get bulletproof presets for initial phase determination (NO user overrides)
        p0_preset = self._get_automatic_preset("p0")
        p2_preset = self._get_automatic_preset("p2")

        _LOGGER.debug(
            f"{self.room} - Determining initial phase: "
            f"VWC={vwc:.1f}%, is_light_on_raw={is_light_on_raw} (type={type(is_light_on_raw).__name__}), "
            f"is_light_on={is_light_on}, VWCMin={p0_preset.get('VWCMin')}, VWCMax={p2_preset.get('VWCMax')}"
        )

        # Decision logic - LIGHT STATUS IS PRIMARY FACTOR
        if not is_light_on:
            # === NIGHT TIME - ALWAYS P3, no irrigation at night ===
            # Night irrigation disrupts the dryback cycle which is essential for generative steering
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            _LOGGER.debug(f"{self.room} - Night time (light OFF), starting P3 Dryback (VWC={vwc:.1f}%)")
            return "p3"
        
        # === DAY TIME ===
        if vwc == 0:
            _LOGGER.warning(f"{self.room} - No VWC data, starting P0 Monitoring")
            return "p0"
        
        vwc_max = p2_preset.get("VWCMax", 68)
        vwc_min = p0_preset.get("VWCMin", 55)
        
        # DEBUG: Log ALL values used for phase determination
        _LOGGER.debug(
            f"{self.room} PHASE DECISION: VWC={vwc:.1f}%, VWCMin={vwc_min}, VWCMax={vwc_max}, "
            f"is_light_on={is_light_on}, p0_preset={p0_preset}, p2_preset={p2_preset}"
        )
        
        # P0 is only the pre-irrigation phase right after light on (outside the configured
        # irrigation window). Once we are inside the irrigation window and VWC is normal,
        # the plant should be in P2 maintenance. This prevents Automatic from resetting to
        # P0 whenever VWC is in the normal range.
        if vwc < vwc_min:
            # Block is dry -> P1 Saturation needed
            _LOGGER.debug(f"{self.room} - Day, VWC low ({vwc:.1f}% < {vwc_min:.1f}%), starting P1 Saturation")
            return "p1"
        elif vwc >= vwc_max:
            # Block is already at max -> P2 Maintenance (just hold it)
            _LOGGER.debug(f"{self.room} - Day, VWC at max ({vwc:.1f}% >= {vwc_max:.1f}%), starting P2 Maintenance")
            return "p2"
        else:
            in_irrigation_window = self._is_in_irrigation_window()
            if is_light_on and not in_irrigation_window:
                # Still in the pre-irrigation buffer after light on -> P0 Monitoring
                _LOGGER.debug(
                    f"{self.room} - Day, VWC normal ({vwc:.1f}% between "
                    f"{vwc_min:.1f}%-{vwc_max:.1f}%), still in pre-irrigation buffer -> P0 Monitoring"
                )
                return "p0"
            else:
                # Irrigation window active (or no schedule) -> P2 Maintenance
                _LOGGER.debug(
                    f"{self.room} - Day, VWC normal ({vwc:.1f}% between "
                    f"{vwc_min:.1f}%-{vwc_max:.1f}%), irrigation window active -> P2 Maintenance"
                )
                return "p2"

    def _get_light_transition_times(self):
        """Calculate light on/off times and irrigation buffer windows.
        
        Returns:
            Dict with light times and irrigation window boundaries
        """
        try:
            # Get light schedule from dataStore
            light_on_time_str = self.data_store.getDeep("isPlantDay.lightOnTime")
            light_off_time_str = self.data_store.getDeep("isPlantDay.lightOffTime")
            
            if not light_on_time_str or not light_off_time_str:
                _LOGGER.warning(f"{self.room} - Light schedule not set, allowing irrigation anytime")
                return None
            
            # Parse times
            try:
                light_on = datetime.strptime(light_on_time_str, "%H:%M:%S").time()
                light_off = datetime.strptime(light_off_time_str, "%H:%M:%S").time()
            except ValueError:
                # Try without seconds
                light_on = datetime.strptime(light_on_time_str, "%H:%M").time()
                light_off = datetime.strptime(light_off_time_str, "%H:%M").time()
            
            # Get buffer hours (default 2 hours)
            raw = self.data_store.getDeep("CropSteering.LightBufferHours")
            buffer_hours = int(raw) if raw is not None else 2
            
            now = datetime.now().time()
            
            # Calculate irrigation window
            # Convert to datetime for arithmetic, then back to time
            today = datetime.now().date()
            light_on_dt = datetime.combine(today, light_on)
            light_off_dt = datetime.combine(today, light_off)
            
            # Handle overnight schedules (e.g., 20:00 to 08:00)
            if light_off_dt <= light_on_dt:
                light_off_dt += timedelta(days=1)
            
            # Calculate buffer boundaries
            irrigation_start = light_on_dt + timedelta(hours=buffer_hours)
            irrigation_stop = light_off_dt - timedelta(hours=buffer_hours)
            
            return {
                'light_on': light_on,
                'light_off': light_off,
                'irrigation_start_time': irrigation_start.time(),
                'irrigation_stop_time': irrigation_stop.time(),
                'current_time': now,
                'buffer_hours': buffer_hours,
                'is_overnight': light_off_dt > datetime.combine(today + timedelta(days=1), datetime.min.time())
            }
        except Exception as e:
            _LOGGER.error(f"{self.room} - Error calculating light transition times: {e}")
            return None

    def _is_in_irrigation_window(self):
        """Check if current time is within the allowed irrigation window.
        
        Irrigation is only allowed:
        - 2h AFTER light on
        - 2h BEFORE light off
        
        Returns:
            bool: True if irrigation is allowed
        """
        times = self._get_light_transition_times()
        if times is None:
            return True  # Allow if no schedule set
        
        now = times['current_time']
        start = times['irrigation_start_time']
        stop = times['irrigation_stop_time']
        
        # Handle overnight schedules
        if times['is_overnight']:
            # Light is on overnight (e.g., 20:00 to 08:00)
            # Irrigation window: 22:00 to 06:00
            if start > stop:
                # Window spans midnight
                in_window = now >= start or now <= stop
            else:
                in_window = start <= now <= stop
        else:
            # Normal schedule (e.g., 08:00 to 20:00)
            # Irrigation window: 10:00 to 18:00
            in_window = start <= now <= stop
        
        if not in_window:
            _LOGGER.debug(
                f"{self.room} - Outside irrigation window (buffer: {times['buffer_hours']}h). "
                f"Allowed: {start.strftime('%H:%M')} - {stop.strftime('%H:%M')}, "
                f"Current: {now.strftime('%H:%M')}"
            )
        
        return in_window

    def _is_near_light_off(self, buffer_minutes=120):
        """Check if lights will turn off soon.
        
        Args:
            buffer_minutes: Minutes before light off to check (default 120 = 2h)
            
        Returns:
            bool: True if lights turn off within buffer period
        """
        try:
            light_off_time_str = self.data_store.getDeep("isPlantDay.lightOffTime")
            if not light_off_time_str:
                return False
            
            # Parse light off time
            try:
                light_off = datetime.strptime(light_off_time_str, "%H:%M:%S").time()
            except ValueError:
                light_off = datetime.strptime(light_off_time_str, "%H:%M").time()
            
            now = datetime.now()
            today = now.date()
            
            # Create datetime for light off today
            light_off_dt = datetime.combine(today, light_off)
            
            # Check if light off is in the future today
            if light_off_dt <= now:
                # Light off already passed, check tomorrow
                light_off_dt += timedelta(days=1)
            
            # Calculate time until light off
            time_until_off = (light_off_dt - now).total_seconds() / 60  # minutes
            
            return time_until_off <= buffer_minutes
            
        except Exception as e:
            _LOGGER.error(f"{self.room} - Error checking light off time: {e}")
            return False

    def _get_plant_info_from_medium(self) -> tuple:
        """
        Get plant phase and week from room-level plantStage (authoritative).
        Falls back to GrowMedium objects, then isPlantDay data.

        Returns:
            tuple: (plant_phase, generative_week)
        """
        FLOWER_STAGES = {"EarlyFlower", "MidFlower", "LateFlower", "Flush"}
        try:
            grow_mediums = self.data_store.get("growMediums") or []
            room_stage = self.data_store.get("plantStage")

            for medium in grow_mediums:
                try:
                    week = None
                    if hasattr(medium, 'get_current_phase') and hasattr(medium, 'get_bloom_week'):
                        if room_stage:
                            phase = "flower" if room_stage in FLOWER_STAGES else "veg"
                        else:
                            phase = medium.get_current_phase()
                        week = medium.get_bloom_week() if phase == "flower" else medium.get_veg_week()
                        return (phase, week)
                    elif isinstance(medium, dict):
                        bloom_switch = medium.get("bloom_switch_date")
                        grow_start = medium.get("grow_start_date")

                        if room_stage:
                            phase = "flower" if room_stage in FLOWER_STAGES else "veg"
                        else:
                            phase = "flower" if bloom_switch else ("veg" if grow_start else "unknown")

                        if phase == "flower":
                            if bloom_switch:
                                from datetime import datetime
                                if isinstance(bloom_switch, str):
                                    bloom_switch = datetime.fromisoformat(bloom_switch.replace('Z', '+00:00'))
                                days = (datetime.now() - bloom_switch).days
                                week = (days // 7) + 1 if days > 0 else 1
                            return ("flower", week or 1)
                        else:
                            if grow_start:
                                from datetime import datetime
                                if isinstance(grow_start, str):
                                    grow_start = datetime.fromisoformat(grow_start.replace('Z', '+00:00'))
                                days = (datetime.now() - grow_start).days
                                week = (days // 7) + 1 if days > 0 else 1
                            return ("veg", week or 1)
                except Exception as e:
                    _LOGGER.warning(f"{self.room} - Error parsing medium plant info: {e}")
                    continue
        except Exception as e:
            _LOGGER.warning(f"{self.room} - Error getting plant info from mediums: {e}")

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
            
            _LOGGER.debug(f"{self.room} - Plant info from medium: phase={plant_phase}, week={generative_week}")

            # Respect an existing phase selector when starting Automatic (e.g. when switching
            # from Manual P2 to Automatic). Only fall back to automatic phase determination if
            # no valid phase is stored. This prevents the cycle from throwing the user back to
            # P0 when VWC happens to be in the normal range.
            stored_phase = str(self.data_store.getDeep("CropSteering.CropPhase") or "").lower()
            if stored_phase in ["p1", "p2", "p3"]:
                initial_phase = stored_phase
                _LOGGER.debug(
                    f"{self.room} - Automatic cycle resuming from stored phase: {initial_phase}"
                )
            else:
                _LOGGER.debug(
                    f"{self.room} - Automatic cycle starting, determining initial phase..."
                )
                initial_phase = await self._determine_initial_phase()

            if initial_phase != stored_phase:
                await self._set_crop_phase_and_update_selector(initial_phase)

            _LOGGER.debug(
                f"{self.room} - Automatic CS cycle started in phase {initial_phase}"
            )

            await self.event_manager.emit(
                "LogForClient",
                self._build_cs_log(
                    f"Started in {initial_phase} - {plant_phase} week {generative_week}",
                    phase=initial_phase,
                    calibration=True,
                ),
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

                    # Re-read plant info every cycle so stage changes take effect immediately
                    plant_phase, generative_week = self._get_plant_info_from_medium()

                    # Get bulletproof presets for automatic mode (NO user overrides)
                    preset = self._get_automatic_preset(current_phase)

                    vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                    ec = float(self.data_store.getDeep("CropSteering.ec_current") or 0)

                    # Record sensor reading for jump/stuck detection
                    self._record_sensor_reading(vwc)

                    # Run bulletproof failsafe checks
                    failsafe_latched = self._failsafe_stop is not None
                    safe, reason = await self._run_failsafe_checks(vwc, source="automatic")
                    if not safe:
                        if failsafe_latched:
                            _LOGGER.debug(
                                f"{self.room} - Automatic failsafe still active: {reason}. "
                                f"Skipping phase logic this cycle."
                            )
                        else:
                            _LOGGER.warning(
                                f"{self.room} - Automatic failsafe triggered: {reason}. "
                                f"Skipping phase logic this cycle."
                            )
                        await asyncio.sleep(self.blockCheckIntervall)
                        continue

                    is_light_on = self._is_lights_on()

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

                    # Periodic state heartbeat so the frontend always knows mode/phase/target/calibration
                    await self._emit_state_heartbeat()

                    # Check calibration status periodically (once per day)
                    await self._check_calibration_status()

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

    async def _check_calibration_status(self):
        """
        Check calibration status and emit reminders if needed.
        - First-start: warn if any calibrations are missing
        - Periodic: remind to re-calibrate if calibration is > 4 weeks old
        Runs at most once per day to avoid spamming.
        """
        last_check = self.data_store.getDeep("CropSteering._last_calibration_check_time")
        now_ts = datetime.now().timestamp()
        one_day_sec = 86400

        if last_check and (now_ts - last_check) < one_day_sec:
            return
        self.data_store.setDeep("CropSteering._last_calibration_check_time", now_ts)

        four_weeks_sec = 2419200  # 28 days
        missing = []
        stale = []

        # Each phase calibrates its key threshold during normal operation:
        # p1/p2 calibrate VWCMax (saturation), p3 calibrates VWCMin (night dryback).
        # Only the relevant value is checked, so a completed auto-calibration
        # does not leave a permanent "calibration needed" warning.
        phase_key = {
            "p1": "VWCMax",
            "p2": "VWCMax",
            "p3": "VWCMin",
        }

        for phase, key in phase_key.items():
            cal_value = self.data_store.getDeep(f"CropSteering.Calibration.{phase}.{key}")
            cal_ts = self.data_store.getDeep(f"CropSteering.Calibration.{phase}.timestamp")

            if cal_value is None:
                missing.append(phase)
            elif cal_ts:
                try:
                    age = now_ts - datetime.fromisoformat(cal_ts).timestamp()
                    if age > four_weeks_sec:
                        stale.append(f"{phase} ({int(age/86400)}d old)")
                except (ValueError, TypeError):
                    pass

        if missing:
            msg = f"Calibration needed: {', '.join(m.upper() for m in missing)} — run VWC calibration cycle"
            _LOGGER.warning(f"{self.room} - {msg}")
            await self.event_manager.emit(
                "LogForClient",
                {"Name": self.room, "Type": "CSWARNING", "Message": msg},
                haEvent=True,
            )

        if stale:
            msg = f"Re-calibration recommended: {', '.join(stale)} — older than 4 weeks"
            _LOGGER.warning(f"{self.room} - {msg}")
            await self.event_manager.emit(
                "LogForClient",
                {"Name": self.room, "Type": "CSWARNING", "Message": msg},
                haEvent=True,
            )

    async def _handle_phase_p0_auto(self, vwc, ec, preset):
        """P0: Monitoring phase - Wait for Dryback Signal

        IMPORTANT:
        - If lights go OFF during P0, transition to P3.
        - If VWC drops below the phase minimum, transition to P1 immediately
          so the block does not dry out (no irrigation-window delay).
        """
        # Check light status first - ensure proper boolean conversion
        is_light_on = self._is_lights_on()
        if not is_light_on:
            _LOGGER.debug(
                f"{self.room} - P0: Lights are OFF, transitioning to P3 Night Dryback"
            )
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            # Transition to P3
            await self._set_crop_phase_and_update_selector("p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change("p0", "p3", f"Lights OFF - switching to night dryback (VWC: {vwc:.1f}%)")
            return

        # P0 is simple: Wait until VWC falls below minimum, then start P1.
        if vwc < preset["VWCMin"]:
            _LOGGER.debug(
                f"{self.room} - P0: VWC {vwc:.1f}% < Min {preset['VWCMin']:.1f}% → Switching to P1"
            )
            await self._set_crop_phase_and_update_selector("p1")
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
        is_light_on = self._is_lights_on()
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
            await self._set_crop_phase_and_update_selector("p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change("p1", "p3", f"Lights OFF - switching to night dryback (VWC: {vwc:.1f}%)")
            return
        
        # Check if lights will turn off soon (2h buffer)
        if self._is_near_light_off(buffer_minutes=120):
            _LOGGER.debug(
                f"{self.room} - P1: Lights will turn off soon, stopping irrigation early"
            )
            # Transition to P3 early to start dryback
            self.data_store.setDeep("CropSteering.p1_start_vwc", None)
            self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
            self.data_store.setDeep("CropSteering.p1_last_vwc", None)
            self.data_store.setDeep("CropSteering.p1_last_irrigation_time", None)
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            await self._set_crop_phase_and_update_selector("p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change("p1", "p3", f"Lights turning off soon - early transition to night dryback (VWC: {vwc:.1f}%)")
            return
        
        # Get safe bounds (clamped by learned values and absolute limits)
        safe_bounds = self._get_safe_vwc_bounds(preset)
        target_vwc = safe_bounds["VWCTarget"]
        vwc_max_cap = safe_bounds["VWCMax"]
        vwc_min = safe_bounds["VWCMin"]
        # Saturation target: never exceed learned max saturation or hard cap
        learned_max_sat = self._load_learned_values()["max_saturation_vwc"]
        if learned_max_sat is not None:
            target_vwc = min(target_vwc, learned_max_sat)
        target_max = min(target_vwc, vwc_max_cap)

        # Get timing from bulletproof preset (no user settings in automatic)
        shot_duration = int(preset.get("irrigation_duration", 45))
        wait_between = int(preset.get("wait_between", 180))
        max_cycles = int(preset.get("max_cycles", 10))

        # Raw preset thresholds + calibrated value (for logging and stagnation checks)
        preset_vwc_min = float(preset.get("VWCMin", 55.0))
        preset_vwc_max = float(preset.get("VWCMax", 70.0))
        calibrated_max = self.data_store.getDeep("CropSteering.Calibration.p1.VWCMax")

        _LOGGER.debug(
            f"{self.room} - P1 BOUNDS: calibrated={calibrated_max}, "
            f"preset_max={preset_vwc_max}, target_vwc={target_vwc:.1f}%, "
            f"vwc_max_cap={vwc_max_cap:.1f}%, effective_target={target_max:.1f}%"
        )
        
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
            _LOGGER.debug(
                f"{self.room} - P1: Target reached {vwc:.1f}% >= {target_max:.1f}%"
            )
            await self._complete_p1_saturation(vwc, target_max, success=True)
            return

        # === 2. Stagnation detected? ===
        # CRITICAL: Only accept stagnation as "block full" if VWC is at least 25%!
        # A stagnation at 15% means there's a problem (sensor issue, no water, etc.), not that the block is full.
        vwc_increase_since_last = vwc - p1_last_vwc
        min_vwc_for_stagnation = max(25.0, preset_vwc_min)  # At least 25% or preset minimum
        
        if p1_irrigation_count >= 3 and vwc_increase_since_last < 1.5:
            if vwc >= min_vwc_for_stagnation:
                # Legitimate stagnation - block is actually full
                _LOGGER.debug(
                    f"{self.room} - P1: Stagnation at {vwc:.1f}% (no increase since last shot)"
                )
                await self.event_manager.emit(
                    "LogForClient",
                    self._build_cs_log(
                        f"Block full at {vwc:.1f}% (no more increase)",
                        phase="p1",
                        calibration=True,
                    ),
                    haEvent=True,
                )
                await self._calibrate_p1_vwc_max(vwc, cap=vwc_max_cap)
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
                    self._build_cs_log(
                        f"WARNING: VWC stuck at {vwc:.1f}% after {p1_irrigation_count} shots - check pump/water supply!",
                        phase="p1",
                    ),
                    haEvent=True,
                )
                # Continue trying to irrigate - don't save bad calibration!

        # === 3. Max Attempts? ===
        if p1_irrigation_count >= max_cycles:
            _LOGGER.debug(f"{self.room} - P1: Max attempts reached ({max_cycles})")
            
            # Only save calibration if VWC reached a reasonable level
            if vwc >= min_vwc_for_stagnation:
                await self._calibrate_p1_vwc_max(vwc, cap=vwc_max_cap)
                await self._complete_p1_saturation(vwc, vwc, success=True, updated_max=True)
            else:
                # VWC too low after max attempts - problem detected!
                _LOGGER.error(
                    f"{self.room} - P1: Max attempts ({max_cycles}) reached but VWC only {vwc:.1f}%! "
                    f"NOT saving as calibration. Check pump/water supply!"
                )
                await self.event_manager.emit(
                    "LogForClient",
                    self._build_cs_log(
                        f"ERROR: {max_cycles} irrigations but VWC only {vwc:.1f}% - check system!",
                        phase="p1",
                    ),
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
            # Safety: do not irrigate if VWC is already at or above target max
            if vwc >= target_max:
                _LOGGER.debug(
                    f"{self.room} - P1: VWC {vwc:.1f}% already at/above target max "
                    f"{target_max:.1f}%, skipping irrigation and completing P1"
                )
                await self._complete_p1_saturation(vwc, target_max, success=True)
                return

            # Safety: never irrigate above hard VWCMax cap
            if vwc_max_cap > 0 and vwc >= vwc_max_cap:
                _LOGGER.warning(
                    f"{self.room} - P1: VWC {vwc:.1f}% already at/above hard cap "
                    f"{vwc_max_cap:.1f}%, skipping irrigation and completing P1"
                )
                await self._complete_p1_saturation(vwc, target_max, success=True)
                return

            # Time for next shot! Pass target and cap for early-stop safety
            await self._irrigate(duration=shot_duration, target_vwc=target_vwc, max_vwc=vwc_max_cap)

            # CRITICAL: Check if target reached after irrigation
            current_vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
            if current_vwc >= target_max:
                _LOGGER.debug(
                    f"{self.room} - P1: Target reached after irrigation! "
                    f"VWC {current_vwc:.1f}% >= {target_max:.1f}% → Switching to P2"
                )
                await self._complete_p1_saturation(current_vwc, target_max, success=True)
                return

            # Hard cap reached (should have been caught by early-stop, but double-check)
            if vwc_max_cap > 0 and current_vwc >= vwc_max_cap:
                _LOGGER.warning(
                    f"{self.room} - P1: Hard cap {vwc_max_cap:.1f}% reached after irrigation, "
                    f"switching to P2"
                )
                await self._complete_p1_saturation(current_vwc, target_max, success=True)
                return

            # Update state only if target not yet reached
            p1_irrigation_count += 1
            self.data_store.setDeep(
                "CropSteering.p1_irrigation_count", p1_irrigation_count
            )
            self.data_store.setDeep("CropSteering.p1_last_vwc", current_vwc)
            self.data_store.setDeep("CropSteering.p1_last_irrigation_time", now)

            # Calculate next shot time
            next_shot_min = wait_between / 60
            
            await self.event_manager.emit(
                "LogForClient",
                self._build_cs_log(
                    f"P1 Shot {p1_irrigation_count}/{max_cycles} | VWC: {vwc:.1f}% → {current_vwc:.1f}% (target: {target_max:.1f}%) | Duration: {shot_duration}s | Next in: {next_shot_min:.0f}min",
                    phase="p1",
                ),
                haEvent=True,
            )
            _LOGGER.debug(
                f"{self.room} - P1: Shot {p1_irrigation_count}/{max_cycles}, VWC {vwc:.1f}% → {current_vwc:.1f}%, duration={shot_duration}s, next in {next_shot_min:.0f}min"
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
        # Learn max saturation from successful P1 completion
        if vwc > 0:
            self._update_learned_max_saturation(vwc)
            self._update_learned_field_capacity(vwc)

        # Clear P1 state tracking
        self.data_store.setDeep("CropSteering.p1_start_vwc", None)
        self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
        self.data_store.setDeep("CropSteering.p1_last_vwc", None)
        self.data_store.setDeep("CropSteering.p1_last_irrigation_time", None)

        # Reset per-cycle irrigation tracking for P2
        self._reset_irrigation_tracking()

        # Transition to P2
        await self._set_crop_phase_and_update_selector("p2")
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        # Log the transition
        message = f"Saturation complete - VWC: {vwc:.1f}%"
        if updated_max:
            message += f" (new calibrated max)"

        await self._log_phase_change("p1", "p2", message)

        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(f"P1 → P2: {message}", phase="p2"),
            haEvent=True,
        )

        _LOGGER.debug(
            f"{self.room} - P1 complete: VWC={vwc:.1f}%, target={target_max:.1f}%, success={success}"
        )


    async def _handle_phase_p2_auto(self, vwc, ec, is_light_on, preset):
        """
        P2: Maintenance phase - Maintain level during light phase
        Uses bulletproof preset values (no user settings).
        """
        # Get timing from bulletproof preset
        shot_duration = int(preset.get("irrigation_duration", 20))
        check_interval_seconds = int(preset.get("irrigation_interval", 1800))
        max_shots = int(preset.get("max_cycles", 10))

        # Get safe bounds (clamped by learned values and absolute limits)
        safe_bounds = self._get_safe_vwc_bounds(preset)
        vwc_max_cap = safe_bounds["VWCMax"]
        vwc_min = safe_bounds["VWCMin"]
        target_vwc = safe_bounds["VWCTarget"]

        # Use learned field capacity as target if available
        learned_fc = self._load_learned_values()["field_capacity_vwc"]
        if learned_fc is not None:
            target_vwc = min(learned_fc, vwc_max_cap)
            _LOGGER.debug(
                f"{self.room} - P2: Using learned field capacity {learned_fc:.1f}% as target"
            )

        # Hold threshold: 95% of target, but never below min
        hold_threshold = max(vwc_min, target_vwc * preset.get("hold_percentage", 0.95))

        # === P2 State Tracking ===
        p2_last_check_time = self.data_store.getDeep("CropSteering.p2_last_check_time")
        p2_shot_count = self.data_store.getDeep("CropSteering.p2_shot_count") or 0
        now = datetime.now()

        # Initialize on first entry into P2
        if p2_last_check_time is None:
            self.data_store.setDeep(
                "CropSteering.p2_last_check_time",
                now - timedelta(seconds=check_interval_seconds)  # Allow immediate first check
            )
            p2_last_check_time = now - timedelta(seconds=check_interval_seconds)

        # Check if it's time for P2 maintenance check
        time_since_last_check = (now - p2_last_check_time).total_seconds()

        # === STAGE-CHECKER: Light OFF -> Switch to P3 immediately (don't wait for interval)
        if not is_light_on:
            _LOGGER.debug(f"{self.room} - P2: Light OFF → Switching to P3")
            self._reset_p2_state_tracking()
            await self._set_crop_phase_and_update_selector("p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            await self._log_phase_change(
                "p2", "p3", f"Night begins - Starting VWC: {vwc:.1f}%"
            )
            return

        # === P2 EMERGENCY: If VWC drops below the calibrated minimum, bypass the regular
        # check interval and irrigate immediately. This prevents the block from drying out
        # while waiting for the next scheduled maintenance check.
        emergency_level = max(self._ABSOLUTE_VWC_MIN, vwc_min)
        if vwc < emergency_level:
            last_p2_irrigation = self.data_store.getDeep("CropSteering.p2_last_irrigation_time")
            emergency_interval = int(preset.get("emergency_interval", 300))
            max_emergency_shots = int(preset.get("max_emergency_shots", 5))
            if last_p2_irrigation and (now - last_p2_irrigation).total_seconds() < emergency_interval:
                _LOGGER.debug(
                    f"{self.room} - P2 Emergency: VWC {vwc:.1f}% < {emergency_level:.1f}% "
                    f"but emergency interval not elapsed yet"
                )
            elif p2_shot_count >= max_emergency_shots:
                _LOGGER.warning(
                    f"{self.room} - P2 Emergency: VWC {vwc:.1f}% < {emergency_level:.1f}% "
                    f"but max emergency shots ({max_emergency_shots}) reached"
                )
                await self.event_manager.emit(
                    "LogForClient",
                    self._build_cs_log(
                        f"P2 Emergency: Max emergency shots reached ({max_emergency_shots}), VWC {vwc:.1f}%",
                        phase="p2",
                    ),
                    haEvent=True,
                )
            else:
                await self._irrigate(
                    duration=shot_duration, target_vwc=target_vwc, max_vwc=vwc_max_cap
                )
                p2_shot_count += 1
                self.data_store.setDeep("CropSteering.p2_shot_count", p2_shot_count)
                self.data_store.setDeep("CropSteering.p2_last_irrigation_time", now)
                # Update last check time too so the regular maintenance slot resets
                self.data_store.setDeep("CropSteering.p2_last_check_time", now)
                await self.event_manager.emit(
                    "LogForClient",
                    self._build_cs_log(
                        f"P2 Emergency: VWC {vwc:.1f}% < {emergency_level:.1f}% → Irrigation",
                        phase="p2",
                    ),
                    haEvent=True,
                )
                _LOGGER.warning(
                    f"{self.room} - P2 Emergency irrigation (VWC {vwc:.1f}% < {emergency_level:.1f}%)"
                )
            return

        # Adaptive check interval: if VWC is already below the hold threshold, check
        # more often so normal maintenance shots fire before VWC hits the emergency level.
        effective_check_interval = check_interval_seconds
        if vwc < hold_threshold:
            effective_check_interval = min(check_interval_seconds, int(preset.get("emergency_interval", 300)))

        if time_since_last_check >= effective_check_interval:
            # Time for P2 check - update timestamp
            self.data_store.setDeep("CropSteering.p2_last_check_time", now)

            # Normal day maintenance
            if vwc < hold_threshold:
                # Check if we've reached max shots limit
                if p2_shot_count >= max_shots:
                    _LOGGER.debug(f"{self.room} - P2: Max shots reached ({max_shots}) - skipping irrigation")
                    await self.event_manager.emit(
                        "LogForClient",
                        self._build_cs_log(
                            f"P2 Maintenance: Max shots reached ({max_shots})",
                            phase="p2",
                        ),
                        haEvent=True,
                    )
                else:
                    await self._irrigate(
                        duration=shot_duration, target_vwc=target_vwc, max_vwc=vwc_max_cap
                    )
                    p2_shot_count += 1
                    self.data_store.setDeep("CropSteering.p2_shot_count", p2_shot_count)
                    self.data_store.setDeep("CropSteering.p2_last_irrigation_time", now)
                    # Learn field capacity from post-irrigation VWC (if stable-ish)
                    post_vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or vwc)
                    if post_vwc > vwc_min:
                        self._update_learned_field_capacity(post_vwc)
                await self.event_manager.emit(
                    "LogForClient",
                    self._build_cs_log(
                        f"P2 Maintenance: VWC {vwc:.1f}% < Hold {hold_threshold:.1f}% → Irrigation",
                        phase="p2",
                    ),
                    haEvent=True,
                )
                _LOGGER.debug(
                    f"{self.room} - P2: Irrigated (VWC {vwc:.1f}% < {hold_threshold:.1f}%)"
                )
            else:
                # Debug: Show status in P2
                _LOGGER.debug(
                    f"{self.room} - P2 maintenance: VWC {vwc:.1f}% (hold at {hold_threshold:.1f}%, OK)"
                )
                # Track post-irrigation peaks for calibration
                p2_last_irrigation_time = self.data_store.getDeep("CropSteering.p2_last_irrigation_time")
                if p2_last_irrigation_time:
                    time_since_p2_irrigation = (now - p2_last_irrigation_time).total_seconds()
                    if time_since_p2_irrigation <= check_interval_seconds * 2:
                        await self._track_p2_vwc_peak(vwc)
        else:
            # Not time for check yet - log waiting status
            time_until_next_check = check_interval_seconds - time_since_last_check
            _LOGGER.debug(
                f"{self.room} - P2: Waiting for next check, {time_until_next_check:.0f}s remaining "
                f"(interval: {check_interval_seconds / 60:.0f}min)"
            )

    async def _track_p2_vwc_peak(self, vwc: float):
        """
        Track post-irrigation VWC peaks in P2 for automatic VWCMax calibration.
        When a consistent peak is observed across multiple irrigation cycles,
        save it as the calibrated VWCMax for P2.
        """
        tolerance = 2.0
        min_peaks = 3
        min_cycles = 3

        peaks = self.data_store.getDeep("CropSteering.p2_irrigation_peaks") or []
        irrigation_count = self.data_store.getDeep("CropSteering.p2_shot_count") or 0

        peaks.append(round(vwc, 1))
        if len(peaks) > 5:
            peaks.pop(0)
        self.data_store.setDeep("CropSteering.p2_irrigation_peaks", peaks)

        if len(peaks) >= min_peaks and irrigation_count >= min_cycles:
            if max(peaks) - min(peaks) <= tolerance:
                avg_vwc = sum(peaks) / len(peaks)
                _LOGGER.debug(
                    f"{self.room} - P2: Consistent post-irrigation peak {avg_vwc:.1f}% - auto-calibrating VWCMax"
                )
                self.data_store.setDeep(
                    "CropSteering.Calibration.p2.VWCMax", round(avg_vwc, 1)
                )
                self.data_store.setDeep(
                    "CropSteering.Calibration.p2.timestamp",
                    datetime.now().isoformat()
                )
                await self._update_number_entity("VWCMax", "p2", avg_vwc)
                await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})
                await self.event_manager.emit(
                    "LogForClient",
                    self._build_cs_log(
                        f"P2: Auto-calibrated VWCMax to {avg_vwc:.1f}% (based on {len(peaks)} consistent irrigation peaks)",
                        phase="p2",
                        calibration=True,
                    ),
                    haEvent=True,
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
                _LOGGER.debug(
                    f"{self.room} - P3: Initialized startNightMoisture to {vwc:.1f}%"
                )

            target_dryback = preset.get("target_dryback_percent", 10.0)
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
                    self._build_cs_log(
                        f"P3 Low dryback {current_dryback:.1f}% < {preset.get('min_dryback_percent', 8.0):.1f}% → Increasing EC",
                        phase="p3",
                    ),
                    haEvent=True,
                )
                _LOGGER.debug(f"{self.room} - P3: Low dryback, EC increased")

            elif current_dryback > preset.get("max_dryback_percent", 12.0):
                # Too much dryback -> decrease EC (less stress)
                await self._adjust_ec_for_dryback(
                    preset["ECTarget"],
                    increase=False,
                    step=preset.get("ec_decrease_step", 0.1),
                )
                await self.event_manager.emit(
                    "LogForClient",
                    self._build_cs_log(
                        f"P3 High dryback {current_dryback:.1f}% > {preset.get('max_dryback_percent', 12.0):.1f}% → Decreasing EC",
                        phase="p3",
                    ),
                    haEvent=True,
                )
                _LOGGER.debug(f"{self.room} - P3: High dryback, EC decreased")
            else:
                _LOGGER.debug(
                    f"{self.room} - P3: Dryback optimal at {current_dryback:.1f}%"
                )

            # Learn minimum dryback from current night VWC
            self._update_learned_min_dryback(vwc)

            # Emergency irrigation if too dry (CONSERVATIVE SETTINGS)
            safe_bounds = self._get_safe_vwc_bounds(preset)
            vwc_min = safe_bounds["VWCMin"]
            learned_min_dry = self._load_learned_values()["min_dryback_vwc"]

            # Emergency level: highest of hard min, learned min + safety margin, preset min
            emergency_candidates = [self._ABSOLUTE_VWC_MIN]
            if vwc_min is not None:
                emergency_candidates.append(vwc_min)
            if learned_min_dry is not None:
                emergency_candidates.append(learned_min_dry + 3.0)
            emergency_level = max(emergency_candidates)

            if vwc < emergency_level:
                # Use bulletproof preset timing values (no user settings)
                emergency_shot_duration = int(preset.get("irrigation_duration", 15))
                # Conservative: cap at 15s per emergency shot
                emergency_shot_duration = min(emergency_shot_duration, 15)
                emergency_interval_seconds = int(preset.get("emergency_interval", 300))
                # Allow repeated emergency shots (configurable) to keep VWC above min
                max_emergency = int(preset.get("max_emergency_shots", 5))

                p3_emergency_count = (
                    self.data_store.getDeep("CropSteering.p3_emergency_count") or 0
                )
                p3_last_emergency_time = self.data_store.getDeep("CropSteering.p3_last_emergency_time")
                now = datetime.now()

                # Initialize emergency state on first emergency
                if p3_last_emergency_time is None:
                    self.data_store.setDeep(
                        "CropSteering.p3_last_emergency_time",
                        now - timedelta(seconds=emergency_interval_seconds)
                    )
                    p3_last_emergency_time = now - timedelta(seconds=emergency_interval_seconds)

                # Check if enough time has passed since last emergency
                time_since_last_emergency = (now - p3_last_emergency_time).total_seconds()

                if p3_emergency_count < max_emergency and time_since_last_emergency >= emergency_interval_seconds:
                    await self._irrigate(
                        duration=emergency_shot_duration,
                        is_emergency=True,
                    )
                    self.data_store.setDeep(
                        "CropSteering.p3_emergency_count", p3_emergency_count + 1
                    )
                    self.data_store.setDeep("CropSteering.p3_last_emergency_time", now)
                    await self.event_manager.emit(
                        "LogForClient",
                        self._build_cs_log(
                            f"P3 CONSERVATIVE Emergency irrigation {p3_emergency_count + 1}/{max_emergency}: VWC {vwc:.1f}% < {emergency_level:.1f}% (duration: {emergency_shot_duration}s)",
                            phase="p3",
                        ),
                        haEvent=True,
                    )
                    _LOGGER.warning(
                        f"{self.room} - P3: CONSERVATIVE Emergency irrigation {p3_emergency_count + 1}/{max_emergency} "
                        f"(VWC {vwc:.1f}% < {emergency_level:.1f}%, duration: {emergency_shot_duration}s)"
                    )
                else:
                    _LOGGER.warning(
                        f"{self.room} - P3: Max emergency irrigations reached ({max_emergency}) or too soon, skipping"
                    )
        else:
            # STAGE-CHECKER: Light is on -> Back to P0
            start_night = self.data_store.getDeep("CropSteering.startNightMoisture")
            current_dryback = (
                ((start_night - vwc) / start_night) * 100 if start_night else 0
            )
            night_start_time = self.data_store.getDeep("CropSteering.phaseStartTime")

            _LOGGER.debug(
                f"{self.room} - P3: Light ON → Switching to P0 (Dryback was {current_dryback:.1f}%)"
            )
            # Reset P3 state tracking before leaving phase
            self._reset_p3_state_tracking()
            await self._set_crop_phase_and_update_selector("p0")
            self.data_store.setDeep(
                "CropSteering.startNightMoisture", None
            )  # Reset for next night

            # Auto-calibration: track night minimum VWC for VWCMin
            await self._calibrate_p3_vwc_min(vwc)

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

            await self._log_phase_change(
                "p3",
                "p0",
                f"Day starts - Final VWC: {vwc:.1f}%, Dryback: {current_dryback:.1f}%",
            )

    # ==================== MANUAL MODE ====================

    async def _run_manual_mode(self):
        """
        Wrapper around per-phase manual cycles that restarts the cycle
        when CropPhase changes (e.g. user selects a new phase in HA).
        """
        _LOGGER.debug(f"{self.room} - Manual mode runner started")
        self._manual_phase_changed_event.clear()
        try:
            while True:
                phase = self.data_store.getDeep("CropSteering.CropPhase") or "p0"
                phase = self._extract_phase_from_value(phase)
                _LOGGER.debug(f"{self.room} - Manual runner starting cycle for phase {phase}")
                cycle_task = asyncio.create_task(self._manual_cycle(phase))
                wait_task = asyncio.create_task(self._manual_phase_changed_event.wait())
                done, pending = await asyncio.wait(
                    [cycle_task, wait_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if wait_task in done:
                    self._manual_phase_changed_event.clear()
                    _LOGGER.debug(f"{self.room} - Manual runner restarting due to phase change")
                    await asyncio.sleep(0.1)
                else:
                    # cycle_task finished normally (e.g. phase changed internally)
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            _LOGGER.warning(f"{self.room} - Manual mode runner CANCELLED")
            await self._turn_off_all_drippers()
            raise

    async def _manual_phase_light_transition(self, phase, vwc, settings):
        """
        Check manual-phase light/dryback transitions for P0/P2/P3.

        Returns True if a phase transition was performed (the calling cycle
        should exit so the manual runner restarts with the new phase).
        """
        if phase == "p2":
            # Wie Automatic P2: bei Lights-Off zur Nacht-Dryback P3 wechseln.
            if not self._is_lights_on():
                _LOGGER.debug(f"{self.room} - Manual P2: Lights off, switching to P3")
                await self._complete_manual_p2(vwc)
                return True

        elif phase == "p3":
            # Dryback-Phase: keine Bewässerung. Endet mit Lights-ON.
            if self._is_lights_on():
                _LOGGER.debug(f"{self.room} - Manual P3: Lights on, switching to P0")
                await self._complete_manual_p3(vwc)
                return True

        elif phase == "p0":
            # Dryback-Phase: keine Bewässerung.
            # Lights OFF -> P3 (night dryback), like automatic P0.
            if not self._is_lights_on():
                _LOGGER.debug(f"{self.room} - Manual P0: Lights off, switching to P3")
                await self._complete_manual_p0_to_p3(vwc)
                return True

            # Lights ON: wie Automatic P0 erst im Irrigation-Window und bei
            # VWC unter dem eigenen VWCMin von P0 -> P1.
            p0_vwc_min = settings.get("VWCMin", {}).get("value", 0)
            if (
                p0_vwc_min > 0
                and vwc < p0_vwc_min
                and self._is_in_irrigation_window()
            ):
                _LOGGER.debug(
                    f"{self.room} - Manual P0: Lights on and VWC {vwc:.1f}% < "
                    f"{p0_vwc_min:.1f}% (in irrigation window), switching to P1"
                )
                await self._complete_manual_p0(vwc)
                return True

        return False

    async def _manual_cycle(self, phase):
            """Manual time-based cycle (uses USER settings)"""
            _LOGGER.debug(f"{self.room} - CS - Manual {phase}: Started")
            try:
                settings = self._get_manual_phase_settings(phase)

                shot_duration = settings["ShotDuration"]["value"]
                shot_interval = settings["ShotIntervall"]["value"]
                shot_count = settings["ShotSum"]["value"]

                _LOGGER.warning(f"{self.room} - Manual {phase} settings: duration={shot_duration}s, interval={shot_interval}min, count={shot_count}")

                if shot_duration <= 0:
                    shot_duration = 30
                    _LOGGER.warning(f"{self.room} - Manual {phase}: Invalid duration, using default 30s")
                if shot_interval <= 0:
                    shot_interval = 30
                    _LOGGER.warning(f"{self.room} - Manual {phase}: Invalid interval, using default 30min")
                if shot_count <= 0:
                    shot_count = 5
                    _LOGGER.warning(f"{self.room} - Manual {phase}: Invalid count, using default 5")

                self.data_store.setDeep("CropSteering.shotCounter", 0)
                self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

                _LOGGER.warning(f"{self.room} - Manual {phase}: {shot_count} shots every {shot_interval}min")

                while True:
                    try:
                        # === CRITICAL: Read sensor data NEWLY! ===
                        sensor_data = await self._get_sensor_averages()
                        if sensor_data:
                            self.data_store.setDeep("CropSteering.vwc_current", sensor_data["vwc"])
                            self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])

                        vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                        ec = float(self.data_store.getDeep("CropSteering.ec_current") or 0)

                        # Periodic state heartbeat so the frontend always knows mode/phase/target/calibration
                        await self._emit_state_heartbeat()

                        self._record_sensor_reading(vwc)
                        failsafe_latched = self._failsafe_stop is not None
                        safe, reason = await self._run_failsafe_checks(vwc, source="manual")
                        if not safe:
                            if failsafe_latched:
                                _LOGGER.debug(
                                    f"{self.room} - Manual {phase} failsafe still active: {reason}. "
                                    f"Skipping irrigation this cycle."
                                )
                            else:
                                _LOGGER.warning(
                                    f"{self.room} - Manual {phase} failsafe triggered: {reason}. "
                                    f"Skipping irrigation this cycle."
                                )
                            await asyncio.sleep(10)
                            continue

                        current_phase = self.data_store.getDeep("CropSteering.CropPhase") or phase
                        if current_phase != phase:
                            _LOGGER.warning(
                                f"{self.room} - Manual {phase}: phase changed to {current_phase}, exiting cycle"
                            )
                            return

                        raw_counter = self.data_store.getDeep("CropSteering.shotCounter")
                        shot_counter = int(float(raw_counter)) if raw_counter is not None else 0

                        # EC management - LOG ONLY
                        ec_target = settings["ECTarget"]["value"]
                        min_ec = settings["MinEC"]["value"]
                        max_ec = settings["MaxEC"]["value"]

                        if ec_target > 0 and ec:
                            if ec < min_ec:
                                _LOGGER.warning(f"{self.room} - Manual: EC {ec:.2f} < Min {min_ec:.2f} (would increase)")
                            elif ec > max_ec:
                                _LOGGER.warning(f"{self.room} - Manual: EC {ec:.2f} > Max {max_ec:.2f} (would decrease)")

                        vwc_min = settings["VWCMin"]["value"]
                        vwc_max = settings["VWCMax"]["value"]
                        vwc_target = settings["VWCTarget"]["value"]

                        # ==================================================
                        # === Phase-spezifische Auto-Transition-Logik ===
                        # ==================================================
                        if phase == "p1":
                            # Wie Automatic P1: bei Lights-Off oder kurz vor
                            # Lights-Off zur Nacht-Dryback P3 wechseln (keine
                            # nächtliche Bewässerung mehr).
                            if not self._is_lights_on():
                                _LOGGER.warning(
                                    f"{self.room} - Manual P1: Lights off, switching to P3 night dryback"
                                )
                                await self._complete_manual_p1_to_p3(vwc)
                                return

                            if self._is_near_light_off(buffer_minutes=120):
                                _LOGGER.warning(
                                    f"{self.room} - Manual P1: Lights off soon, switching to P3 early dryback"
                                )
                                await self._complete_manual_p1_to_p3(vwc)
                                return

                            if vwc_target > 0 and vwc >= vwc_target:
                                _LOGGER.warning(
                                    f"{self.room} - Manual P1: Target VWC reached "
                                    f"{vwc:.1f}% >= {vwc_target:.1f}%, switching to P2"
                                )
                                await self._complete_manual_p1(vwc, vwc_target)
                                return

                            if vwc_max > 0 and vwc >= vwc_max:
                                _LOGGER.warning(
                                    f"{self.room} - Manual P1: VWC at/above max cap "
                                    f"{vwc:.1f}% >= {vwc_max:.1f}%, switching to P2"
                                )
                                await self._complete_manual_p1(vwc, vwc_target)
                                return

                            if shot_counter >= shot_count:
                                _LOGGER.warning(
                                    f"{self.room} - Manual P1: Max shots reached "
                                    f"({shot_counter}/{shot_count}), switching to P2"
                                )
                                await self._complete_manual_p1(vwc, vwc_target)
                                return

                        elif phase in ("p0", "p2", "p3"):
                            # Licht-/Dryback-Übergänge: P0→P3, P0→P1, P2→P3, P3→P0
                            if await self._manual_phase_light_transition(
                                phase, vwc, settings
                            ):
                                return

                        # P0: reine Dryback-Phase, keine Bewässerung.
                        if phase == "p0":
                            await asyncio.sleep(10)
                            continue

                        # P3: Nacht-Dryback mit konservativer Not-Bewässerung
                        # (wie Automatic, ohne Kalibrierung/EC-Anpassung).
                        if phase == "p3":
                            if vwc > 0:
                                await self._manual_p3_emergency(vwc, settings)
                            await asyncio.sleep(10)
                            continue

                        # === Emergency irrigation (nur p1/p2) ===
                        if vwc and vwc_min > 0 and vwc < vwc_min * 0.9:
                            if shot_counter >= shot_count:
                                _LOGGER.debug(
                                    f"{self.room} - Manual {phase}: Emergency irrigation needed "
                                    f"but max shots reached ({shot_counter}/{shot_count})"
                                )
                                await self.event_manager.emit(
                                    "LogForClient",
                                    self._build_cs_log(
                                        f"Manual {phase}: Emergency irrigation blocked - max shots reached",
                                        phase=phase,
                                    ),
                                    haEvent=True,
                                )
                            elif vwc_max > 0 and vwc >= vwc_max:
                                _LOGGER.debug(
                                    f"{self.room} - Manual {phase}: VWC {vwc:.1f}% already at/above max "
                                    f"{vwc_max:.1f}%, skipping emergency irrigation"
                                )
                            else:
                                await self._irrigate(duration=shot_duration, target_vwc=vwc_target, max_vwc=vwc_max)
                                shot_counter += 1
                                self.data_store.setDeep("CropSteering.shotCounter", shot_counter)
                                self.data_store.setDeep("CropSteering.lastIrrigationTime", datetime.now())

                                post_vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)

                                await self.event_manager.emit(
                                    "LogForClient",
                                    self._build_cs_log(
                                        f"CropSteering {phase}: Emergency irrigation ({shot_counter}/{shot_count}) | VWC: {vwc:.1f}% → {post_vwc:.1f}%",
                                        phase=phase,
                                        Type="Emergency irrigation",
                                    ),
                                    haEvent=True,
                                )

                                if phase == "p1" and vwc_target > 0:
                                    current_vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                                    if current_vwc >= vwc_target:
                                        await self._complete_manual_p1(current_vwc, vwc_target)
                                        return
                                    if vwc_max > 0 and current_vwc >= vwc_max:
                                        await self._complete_manual_p1(current_vwc, vwc_target)
                                        return

                        # === Scheduled irrigation (nur p1/p2) ===
                        last_irrigation = self.data_store.getDeep("CropSteering.lastIrrigationTime")
                        now = datetime.now()

                        should_irrigate = (
                            last_irrigation is None
                            or (now - last_irrigation).total_seconds() / 60 >= shot_interval
                        )

                        if should_irrigate and shot_counter < shot_count:
                            if vwc_max > 0 and vwc >= vwc_max:
                                _LOGGER.debug(
                                    f"{self.room} - Manual {phase}: VWC {vwc:.1f}% already at/above max "
                                    f"{vwc_max:.1f}%, skipping scheduled irrigation"
                                )
                            else:
                                await self._irrigate(duration=shot_duration, target_vwc=vwc_target, max_vwc=vwc_max)
                                shot_counter += 1
                                self.data_store.setDeep("CropSteering.shotCounter", shot_counter)
                                self.data_store.setDeep("CropSteering.lastIrrigationTime", now)

                                post_vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)

                                await self.event_manager.emit(
                                    "LogForClient",
                                    self._build_cs_log(
                                        f"CropSteering {phase}: Shot {shot_counter}/{shot_count} | VWC: {vwc:.1f}% → {post_vwc:.1f}%",
                                        phase=phase,
                                    ),
                                    haEvent=True,
                                )

                                if phase == "p1" and vwc_target > 0:
                                    current_vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                                    if current_vwc >= vwc_target:
                                        await self._complete_manual_p1(current_vwc, vwc_target)
                                        return
                                    if vwc_max > 0 and current_vwc >= vwc_max:
                                        await self._complete_manual_p1(current_vwc, vwc_target)
                                        return

                        # Reset counter after full cycle (nur P2 - P1/P0/P3 haben eigene Transition-Logik).
                        if phase == "p2" and shot_counter >= shot_count:
                            phase_start = self.data_store.getDeep("CropSteering.phaseStartTime")
                            if phase_start:
                                elapsed = (now - phase_start).total_seconds() / 60
                                if elapsed >= shot_interval:
                                    self.data_store.setDeep("CropSteering.shotCounter", 0)
                                    self.data_store.setDeep("CropSteering.phaseStartTime", now)
                                    await self.event_manager.emit(
                                        "LogForClient",
                                        self._build_cs_log(
                                            f"CropSteering {phase}: New cycle started",
                                            phase=phase,
                                        ),
                                        haEvent=True,
                                    )
                            else:
                                self.data_store.setDeep("CropSteering.phaseStartTime", now)

                    except Exception as loop_error:
                        _LOGGER.error(f"{self.room} - Manual cycle iteration error: {loop_error}", exc_info=True)

                    await asyncio.sleep(10)

            except asyncio.CancelledError:
                _LOGGER.warning(f"{self.room} - Manual cycle CANCELLED")
                await self._turn_off_all_drippers()
                raise
            except Exception as e:
                _LOGGER.error(f"{self.room} - Manual cycle FATAL error: {e}", exc_info=True)
                await self._emergency_stop()

    async def _complete_manual_p0(self, vwc):
        """Complete P0 dryback phase and transition to P1 (lights on + VWC below P1 threshold)."""
        self.data_store.setDeep("CropSteering.shotCounter", 0)
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        await self._set_crop_phase_and_update_selector("p1")

        message = f"P0 → P1: VWC unter Schwelle - VWC: {vwc:.1f}%"
        await self._log_phase_change("p0", "p1", message)
        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(message, phase="p1"),
            haEvent=True,
        )
        _LOGGER.debug(f"{self.room} - Manual P0 complete: VWC={vwc:.1f}%")

    async def _complete_manual_p0_to_p3(self, vwc):
        """Complete P0 monitoring phase and transition to P3 when lights go off (night dryback)."""
        self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
        self.data_store.setDeep("CropSteering.shotCounter", 0)
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        await self._set_crop_phase_and_update_selector("p3")

        message = f"P0 → P3: Lights off - VWC: {vwc:.1f}%"
        await self._log_phase_change("p0", "p3", message)
        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(f"P1 → P2: {message}", phase="p2"),
            haEvent=True,
        )
        _LOGGER.debug(f"{self.room} - Manual P0 complete (night): VWC={vwc:.1f}%")

    async def _complete_manual_p1(self, vwc, target_max):
        """
        Complete manual P1 saturation phase and transition to P2.
        Resets both P1 state tracking and manual shot counter. Also calibrates
        VWCMax from the observed saturation value, like automatic mode does.
        """
        # Read user's VWCMax cap before leaving p1 and use it as safety cap.
        p1_settings = self._get_manual_phase_settings("p1")
        user_vwc_max = p1_settings.get("VWCMax", {}).get("value") if p1_settings else None

        self._reset_p1_state_tracking()
        self.data_store.setDeep("CropSteering.shotCounter", 0)
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        # Calibrate VWCMax from observed saturation VWC
        await self._calibrate_p1_vwc_max(vwc, cap=user_vwc_max)

        # Transition to P2
        await self._set_crop_phase_and_update_selector("p2")

        message = f"Manual saturation complete - VWC: {vwc:.1f}%"
        await self._log_phase_change("p1", "p2", message)

        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(f"P1 → P2: {message}", phase="p2"),
            haEvent=True,
        )

        _LOGGER.debug(
            f"{self.room} - Manual P1 complete: VWC={vwc:.1f}%, target={target_max:.1f}%"
        )

    async def _complete_manual_p1_to_p3(self, vwc):
        """Complete manual P1 saturation phase and transition to P3 (night dryback).

        Mirrors automatic P1: lights off or near lights-off ends saturation
        early so the night dryback can start. Also calibrates VWCMax.
        """
        # Read user's VWCMax cap before leaving p1 and use it as safety cap.
        p1_settings = self._get_manual_phase_settings("p1")
        user_vwc_max = p1_settings.get("VWCMax", {}).get("value") if p1_settings else None

        self._reset_p1_state_tracking()
        self.data_store.setDeep("CropSteering.shotCounter", 0)
        self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        # Calibrate VWCMax from observed saturation VWC
        await self._calibrate_p1_vwc_max(vwc, cap=user_vwc_max)

        await self._set_crop_phase_and_update_selector("p3")

        message = f"P1 → P3: Lights off - VWC: {vwc:.1f}%"
        await self._log_phase_change("p1", "p3", message)
        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(message, phase="p3"),
            haEvent=True,
        )
        _LOGGER.debug(f"{self.room} - Manual P1 complete (night): VWC={vwc:.1f}%")

    async def _complete_manual_p2(self, vwc):
        """Complete P2 maintenance phase and transition to P3 (pre-lights-off dryback)."""
        self.data_store.setDeep("CropSteering.shotCounter", 0)
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        await self._set_crop_phase_and_update_selector("p3")

        message = f"P2 → P3: Lights-off in Kürze - VWC: {vwc:.1f}%"
        await self._log_phase_change("p2", "p3", message)
        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(message, phase="p3"),
            haEvent=True,
        )
        _LOGGER.debug(f"{self.room} - Manual P2 complete: VWC={vwc:.1f}%")

    async def _complete_manual_p3(self, vwc):
        """Complete P3 dryback phase and transition to P0 (triggered by lights-on).

        Calibrates VWCMin from consistent night minima, like automatic P3.
        """
        # Calibrate VWCMin from night dryback minimum
        await self._calibrate_p3_vwc_min(vwc)

        # Reset P3 state so the next night starts with fresh emergency counters.
        self._reset_p3_state_tracking()
        self.data_store.setDeep("CropSteering.shotCounter", 0)
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        await self._set_crop_phase_and_update_selector("p0")

        message = f"P3 → P0: Lights on - VWC: {vwc:.1f}%"
        await self._log_phase_change("p3", "p0", message)
        await self.event_manager.emit(
            "LogForClient",
            self._build_cs_log(message, phase="p0"),
            haEvent=True,
        )
        _LOGGER.debug(f"{self.room} - Manual P3 complete: VWC={vwc:.1f}%")

    async def _manual_p3_emergency(self, vwc, settings):
        """
        Konservative Not-Bewässerung in Manual P3 bei Nacht.

        Nachgebaut nach der Automatic P3 Emergency-Logik, aber OHNE
        EC-Anpassung. Kalibrierung wird beim Phasen-Abschluss erledigt.
        """
        vwc_min = settings.get("VWCMin", {}).get("value", 0)
        if vwc_min <= 0:
            return

        # Emergency-Schwelle: höchster Wert aus absolutem Minimum, User-VWCMin
        # und gelerntem Dryback-Minimum + Sicherheitsmarge.
        emergency_candidates = [self._ABSOLUTE_VWC_MIN]
        emergency_candidates.append(vwc_min)
        learned_min_dry = self._load_learned_values()["min_dryback_vwc"]
        if learned_min_dry is not None:
            emergency_candidates.append(learned_min_dry + 3.0)
        emergency_level = max(emergency_candidates)

        if vwc >= emergency_level:
            return

        # Konservative Timing-Werte: wiederholbare Not-Bewässerung, um das Min zu halten
        emergency_shot_duration = min(
            settings.get("ShotDuration", {}).get("value", 15) or 15, 15
        )
        emergency_interval_seconds = 300  # 5 min between emergency shots
        max_emergency = 5

        p3_emergency_count = (
            self.data_store.getDeep("CropSteering.p3_emergency_count") or 0
        )
        p3_last_emergency_time = self.data_store.getDeep(
            "CropSteering.p3_last_emergency_time"
        )
        now = datetime.now()

        if p3_last_emergency_time is None:
            p3_last_emergency_time = now - timedelta(
                seconds=emergency_interval_seconds
            )

        time_since_last = (now - p3_last_emergency_time).total_seconds()

        if (
            p3_emergency_count < max_emergency
            and time_since_last >= emergency_interval_seconds
        ):
            await self._irrigate(
                duration=emergency_shot_duration,
                is_emergency=True,
            )
            self.data_store.setDeep(
                "CropSteering.p3_emergency_count", p3_emergency_count + 1
            )
            self.data_store.setDeep("CropSteering.p3_last_emergency_time", now)
            await self.event_manager.emit(
                "LogForClient",
                self._build_cs_log(
                    f"Manual P3 CONSERVATIVE Emergency irrigation {p3_emergency_count + 1}/{max_emergency}: VWC {vwc:.1f}% < {emergency_level:.1f}% (duration: {emergency_shot_duration}s)",
                    phase="p3",
                ),
                haEvent=True,
            )
            _LOGGER.warning(
                f"{self.room} - Manual P3: CONSERVATIVE Emergency irrigation {p3_emergency_count + 1}/{max_emergency} "
                f"(VWC {vwc:.1f}% < {emergency_level:.1f}%, duration: {emergency_shot_duration}s)"
            )
        else:
            _LOGGER.warning(
                f"{self.room} - Manual P3: Max emergency irrigations reached ({max_emergency}) or too soon, skipping"
            )

    # ==================== IRRIGATION ====================

    async def _irrigate(self, duration=None, is_emergency=False, target_vwc=None, max_vwc=None):
        """Execute irrigation with protection against cancellation.
        
        Uses _irrigation_in_progress flag to prevent handle_mode_change 
        from stopping irrigation mid-cycle.

        Args:
            duration: Irrigation duration in seconds. If None, uses a safe default.
            is_emergency: Whether this is an emergency irrigation
            target_vwc: Optional VWC target. Irrigation stops early when reached.
            max_vwc: Optional VWC safety cap. Irrigation stops immediately when reached.
        """
        drippers = self._get_drippers()

        if not drippers:
            _LOGGER.warning(f"⚠️ {self.room} - No drippers found, skipping irrigation")
            return
        # If no duration passed, use a safe default (never read user settings here)
        if duration is None or duration <= 0:
            duration = 30
            _LOGGER.warning(f"{self.room} - _irrigate: No duration passed, using safe default: {duration}s")
        
        _LOGGER.debug(f"{self.room} - _irrigate called with duration={duration}s")
        
        # Get sensor data BEFORE irrigation for event logging and failsafe tracking
        pre_sensor_data = await self._get_sensor_averages()
        pre_vwc = pre_sensor_data.get("vwc", 0) if pre_sensor_data else 0
        pre_ec = pre_sensor_data.get("ec", 0) if pre_sensor_data else 0
        pre_pore_ec = pre_sensor_data.get("pore_ec", 0) if pre_sensor_data else 0
        pre_temp = pre_sensor_data.get("temperature", 25) if pre_sensor_data else 25

        try:
            self._irrigation_in_progress = True
            # Turn ON drippers - same pattern as CastManager
            _LOGGER.debug(f"{self.room} - Starting irrigation for {duration}s with {len(drippers)} drippers")
            for dev_id in drippers:
                pumpAction = OGBHydroAction(
                    Name=self.room, Action="on", Device=dev_id, Cycle="false"
                )
                await self.event_manager.emit("PumpAction", pumpAction)
                _LOGGER.debug(f"{self.room} - Sent ON to {dev_id}")
            
            # Wait for irrigation duration, polling VWC periodically for early stop
            elapsed = 0
            poll_interval = 1  # seconds
            stopped_early = False
            stop_reason = None
            while elapsed < duration:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                # Only poll for early-stop if a bound was supplied
                if max_vwc is not None or target_vwc is not None:
                    sensor_data = await self._get_sensor_averages()
                    current_vwc = sensor_data.get("vwc") if sensor_data else None
                    if current_vwc is not None:
                        if max_vwc is not None and current_vwc >= max_vwc:
                            stopped_early = True
                            stop_reason = f"safety cap {max_vwc:.1f}% reached"
                            _LOGGER.debug(
                                f"{self.room} - Irrigation stopping early after {elapsed}s: "
                                f"VWC {current_vwc:.1f}% >= max {max_vwc:.1f}%"
                            )
                            break
                        if target_vwc is not None and current_vwc >= target_vwc:
                            stopped_early = True
                            stop_reason = f"target {target_vwc:.1f}% reached"
                            _LOGGER.debug(
                                f"{self.room} - Irrigation stopping early after {elapsed}s: "
                                f"VWC {current_vwc:.1f}% >= target {target_vwc:.1f}%"
                            )
                            break

            # Record actual irrigation runtime for failsafe tracking
            self._record_irrigation(elapsed, pre_vwc)

            # Turn OFF drippers - same pattern as CastManager
            _LOGGER.debug(
                f"{self.room} - Irrigation STOPPING after {elapsed}s "
                f"(requested {duration}s){' - ' + stop_reason if stop_reason else ''} "
                f"- turning off {len(drippers)} drippers"
            )
            for dev_id in drippers:
                pumpAction = OGBHydroAction(
                    Name=self.room, Action="off", Device=dev_id, Cycle="false"
                )
                await self.event_manager.emit("PumpAction", pumpAction)
                _LOGGER.debug(f"{self.room} - Sent OFF to {dev_id}")

            # Read post-irrigation sensor values for history/logging
            post_sensor_data = await self._get_sensor_averages()
            post_vwc = post_sensor_data.get("vwc", 0) if post_sensor_data else 0
            self.data_store.setDeep("CropSteering.vwc_current", post_vwc)
            post_ec = post_sensor_data.get("ec", 0) if post_sensor_data else 0
            self.data_store.setDeep("CropSteering.ec_current", post_ec)
            post_pore_ec = post_sensor_data.get("pore_ec", 0) if post_sensor_data else 0
            self.data_store.setDeep("CropSteering.pore_ec_current", post_pore_ec)
            post_temp = post_sensor_data.get("temperature", 25) if post_sensor_data else 25
            self.data_store.setDeep("CropSteering.temperature_current", post_temp)
            _LOGGER.warning(
                f"{self.room} - Irrigation completed: {elapsed}s (requested {duration}s), "
                f"VWC: {pre_vwc:.1f}% → {post_vwc:.1f}%, EC: {pre_ec:.2f} → {post_ec:.2f}, Pore EC: {pre_pore_ec:.2f} → {post_pore_ec:.2f}, Temperature: {pre_temp:.1f}°C → {post_temp:.1f}°C"
            )

            try:
                vwc_delta = round(float(post_vwc) - float(pre_vwc), 1) if post_vwc is not None and pre_vwc is not None else None
            except (ValueError, TypeError):
                vwc_delta = None

            # Emit AI irrigation event
            await self.event_manager.emit(
                "CSIrrigation",
                {
                    "room": self.room,
                    "shot_number": self.data_store.getDeep("CropSteering.shotCounter")
                    or 1,
                    "duration": elapsed,
                    "requested_duration": duration,
                    "stopped_early": stopped_early,
                    "stop_reason": stop_reason,
                    "pre_vwc": pre_vwc,
                    "pre_ec": pre_ec,
                    "pre_pore_ec": pre_pore_ec,
                    "pre_temperature": pre_temp,
                    "post_vwc": post_vwc,
                    "post_ec": post_ec,
                    "post_pore_ec": post_pore_ec,
                    "post_temperature": post_temp,
                    "vwc_delta": vwc_delta,
                    "target_vwc": target_vwc,
                    "max_vwc": max_vwc,
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
            _LOGGER.debug(f"🔓 {self.room} - Irrigation lock RELEASED")

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
        await self._send_critical_notification(
            f"OGB {self.room}: CropSteering Emergency Stop",
            "An irrigation error occurred. All drippers have been turned off. Please check the pump and sensor setup.",
        )
        await self.event_manager.emit(
            "LogForClient", f"{self.room}: Emergency stop activated", haEvent=True, debug_type="ERROR"
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

    def _light_schedule_state(self):
        """Compute the desired light state from the configured schedule.

        Returns True/False, or None if the schedule is not configured."""
        light_on_time_str = self.data_store.getDeep("isPlantDay.lightOnTime")
        light_off_time_str = self.data_store.getDeep("isPlantDay.lightOffTime")
        if not light_on_time_str or not light_off_time_str:
            return None

        try:
            light_on_time = datetime.strptime(str(light_on_time_str), "%H:%M:%S").time()
            light_off_time = datetime.strptime(str(light_off_time_str), "%H:%M:%S").time()
        except (ValueError, TypeError):
            try:
                light_on_time = datetime.strptime(str(light_on_time_str), "%H:%M").time()
                light_off_time = datetime.strptime(str(light_off_time_str), "%H:%M").time()
            except (ValueError, TypeError):
                return None

        current_time = datetime.now().time()
        if light_on_time < light_off_time:
            # Normal cycle (e.g., 08:00 to 20:00)
            return light_on_time <= current_time < light_off_time
        # Over midnight (e.g., 20:00 to 08:00)
        return current_time >= light_on_time or current_time < light_off_time

    def _is_lights_on(self) -> bool:
        """Return True if lights are currently on.

        Uses the persisted islightON flag and falls back to the configured
        light schedule when the flag is missing or stale (e.g. shortly after
        a restart before the periodic light check refreshed it)."""
        is_light_on_raw = self.data_store.getDeep("isPlantDay.islightON")
        if is_light_on_raw is None:
            is_light_on = False
        elif isinstance(is_light_on_raw, str):
            is_light_on = is_light_on_raw.lower() in ("true", "1", "on", "yes")
        else:
            is_light_on = bool(is_light_on_raw)

        if is_light_on:
            return True
        # Stale/missing flag: trust the schedule instead of assuming night.
        return self._light_schedule_state() is True

    def _minutes_until_lights_off(self):
        """Minutes remaining until lights turn off, or None if unknown."""
        lights_off_time_str = self.data_store.getDeep("isPlantDay.lightOffTime")
        if not lights_off_time_str:
            return None

        try:
            lights_off_time = datetime.strptime(
                str(lights_off_time_str), "%H:%M:%S"
            ).time()
        except (ValueError, TypeError):
            try:
                lights_off_time = datetime.strptime(
                    str(lights_off_time_str), "%H:%M"
                ).time()
            except (ValueError, TypeError):
                return None

        now = datetime.now()
        target = now.replace(
            hour=lights_off_time.hour,
            minute=lights_off_time.minute,
            second=lights_off_time.second,
            microsecond=0,
        )
        if target < now:
            target += timedelta(days=1)

        return (target - now).total_seconds() / 60


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
        active_target = self._get_active_vwc_target(current_phase)
        vwc_target = active_target if active_target is not None else preset.get("VWCTarget", "?")
        
        # Convert interval to minutes for display
        interval_min = int(interval / 60) if isinstance(interval, (int, float)) else interval
        
        vwc_value = sensor_data.get("vwc") if sensor_data else None
        ec_value = sensor_data.get("ec") if sensor_data else None
        
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
                "VWC": vwc_value,
                "EC": ec_value,
                "PlantPhase": plant_phase,
                "Week": gen_week,
            },
            haEvent=True,
        )
        
        _LOGGER.debug(
            f"{self.room} - CS Started: phase={current_phase}, duration={duration}s, "
            f"interval={interval_min}min, max_shots={max_shots}, vwc_target={vwc_target}"
        )

        # Force a state heartbeat on mode start so a freshly loaded dashboard gets the current state immediately.
        await self._emit_state_heartbeat(force=True)

    async def _log_phase_change(self, from_phase, to_phase, reason):
        """Log phase change with preset details"""
        # Get new phase preset for logging
        plant_phase, gen_week = self._get_plant_info_from_medium()
        new_preset = self._get_adjusted_preset(to_phase, plant_phase, gen_week)
        
        duration = new_preset.get("irrigation_duration", "?")
        interval = new_preset.get("wait_between", new_preset.get("irrigation_interval", 0))
        interval_min = int(interval / 60) if isinstance(interval, (int, float)) and interval > 0 else "?"
        max_shots = new_preset.get("max_cycles", new_preset.get("ShotSum", "?"))
        active_target = self._get_active_vwc_target(to_phase)
        vwc_target = active_target if active_target is not None else new_preset.get("VWCTarget", "?")
        
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
        
        _LOGGER.debug(
            f"{self.room} - Phase change: {from_phase} -> {to_phase} ({reason}) "
            f"| duration={duration}s, interval={interval_min}min, max_shots={max_shots}"
        )

        # Emit AI event for learning
        sensor_data = await self._get_sensor_averages()
        is_light_on = self._is_lights_on()
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

        # Force a state heartbeat on phase change so the dashboard updates immediately.
        await self._emit_state_heartbeat(force=True)

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

    # ==================== VWC CALIBRATION (USER-INITIATED) ====================

    async def handle_vwc_calibration_command(self, command_data):
        """
        Handle VWC calibration commands - delegates to CalibrationManager.
        Available in both Automatic and Manual mode (user starts it via
        ConsoleManager in manual mode).

        Expected:
        {
            "action": "start_max" | "start_min" | "stop",
            "phase": "p0" | "p1" | "p2" | "p3"
        }
        """

        # Delegate to CalibrationManager
        if self.calibration_manager:
            await self.calibration_manager.handle_vwc_calibration_command(command_data)
        else:
            _LOGGER.error(f"{self.room} - CalibrationManager not initialized")

    # NOTE: VWC calibration methods (start_vwc_max_calibration, start_vwc_min_calibration, 
    # stop_vwc_calibration, etc.) are now handled by OGBCSCalibrationManager
    # See handle_vwc_calibration_command() which delegates to self.calibration_manager

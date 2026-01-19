"""
OpenGrowBox Far Red Light Device

LABEL: lightfarred

Far Red lights are used for:
- Initiating the Emerson effect at start of light cycle
- Accelerating phytochrome conversion (Pfr -> Pr) at end of light cycle
- Reducing stretch and promoting flowering

Mode options:
- Schedule: ON at start/end of light cycle for configurable duration (default behavior)
- Always On: ON whenever main lights are ON
- Always Off: Disabled, never turns on automatically
- Manual: Only responds to manual commands, no automatic control

Timing behavior (Schedule mode only):
- ON for configurable duration at START of light cycle (default: 15 min)
- ON for configurable duration at END of light cycle (default: 15 min)
- OFF during the main light period
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from .Light import Light
from ..data.OGBDataClasses.OGBPublications import OGBLightAction

_LOGGER = logging.getLogger(__name__)


# Valid modes for Far Red light control
class FarRedMode:
    SCHEDULE = "Schedule"      # Start/End timing windows
    ALWAYS_ON = "Always On"    # ON when main lights are ON
    ALWAYS_OFF = "Always Off"  # Never on automatically
    MANUAL = "Manual"          # Only manual control


class LightFarRed(Light):
    """Far Red light device with configurable operation modes."""

    def __init__(
        self,
        deviceName,
        deviceData,
        eventManager,
        dataStore,
        deviceType,
        inRoom,
        hass=None,
        deviceLabel="EMPTY",
        allLabels=[],
    ):
        super().__init__(
            deviceName,
            deviceData,
            eventManager,
            dataStore,
            deviceType,
            inRoom,
            hass,
            deviceLabel,
            allLabels,
        )

        # Mode setting
        self.mode = FarRedMode.SCHEDULE  # Default to schedule-based operation
        
        # Far Red specific settings (for Schedule mode)
        self.start_duration_minutes = 15  # Duration at start of light cycle
        self.end_duration_minutes = 15    # Duration at end of light cycle
        self.intensity = 100              # Brightness percentage

        # Smart scheduling features (new)
        self.smart_start_enabled = True   # Use 15 min before main lights
        self.smart_end_enabled = True     # Use 15 min after main lights
        
        # State tracking
        self.is_fr_active = False
        self.current_phase: Optional[str] = None  # 'start', 'end', 'always_on', or None
        self.current_intensity = 0.0  # Current intensity for ramping (0-100%)
        self._last_processed_intensity = None  # Last intensity sent to device
        
        # Light schedule reference
        self.lightOnTime = None
        self.lightOffTime = None
        self.islightON = None
        
        # Task tracking
        self._schedule_task = None
        self._turn_off_task = None
        
        # Lock to prevent duplicate scheduler tasks
        self._scheduler_lock = asyncio.Lock()
        
        # Initialize parent class first (important for Device inheritance)
        self.init()

        # Initialize FarRed specific settings
        self._load_settings()

        # Register event handlers FIRST (before scheduler starts)
        # This ensures we don't miss any events emitted during startup
        self.event_manager.on("LightTimeChanges", self._on_light_time_change)
        self.event_manager.on("toggleLight", self._on_main_light_toggle)
        self.event_manager.on("FarRedSettingsUpdate", self._on_settings_update)

        # Validate entity availability
        self._validate_entity_availability()

        # Scheduler is started in _load_settings() when enabled=True
        # Don't start here to avoid duplicate scheduler starts

    def __repr__(self):
        return (
            f"LightFarRed('{self.deviceName}' in {self.inRoom}) "
            f"Mode:{self.mode} "
            f"StartDuration:{self.start_duration_minutes}min EndDuration:{self.end_duration_minutes}min "
            f"Active:{self.is_fr_active} Phase:{self.current_phase} Running:{self.isRunning}"
        )

    def _validate_entity_availability(self):
        """
        Validate that the light entity is available in Home Assistant.
        If switches list is empty, log warning and attempt to find the entity.
        """
        if not self.switches:
            _LOGGER.warning(
                f"{self.deviceName}: No switches/entities found! "
                f"The Far Red light entity may be unavailable or not correctly labeled. "
                f"Please ensure the entity exists in Home Assistant and has the correct label "
                f"(light_fr, light_farred, farred, far_red)."
            )
            # Try to find entity in HA directly if hass is available
            if self.hass:
                # Common entity patterns for far red lights
                possible_entity_ids = [
                    f"light.{self.deviceName}",
                    f"light.{self.deviceName.lower()}",
                    f"light.{self.deviceName.replace(' ', '_').lower()}",
                    f"switch.{self.deviceName}",
                    f"switch.{self.deviceName.lower()}",
                ]
                
                for entity_id in possible_entity_ids:
                    state = self.hass.states.get(entity_id)
                    if state and state.state not in ("unavailable", "unknown", None):
                        _LOGGER.info(
                            f"{self.deviceName}: Found entity '{entity_id}' in HA. "
                            f"Adding to switches list."
                        )
                        self.switches.append({
                            "entity_id": entity_id,
                            "value": state.state,
                            "platform": "recovered"
                        })
                        self.isRunning = state.state == "on"
                        return
                
                _LOGGER.error(
                    f"{self.deviceName}: Could not find any valid entity in Home Assistant. "
                    f"Tried: {possible_entity_ids}. "
                    f"Please check that your Far Red light device exists and is correctly configured."
                )
        else:
            # Validate existing switches are available
            for switch in self.switches:
                entity_id = switch.get("entity_id")
                if self.hass and entity_id:
                    state = self.hass.states.get(entity_id)
                    if state and state.state in ("unavailable", "unknown"):
                        _LOGGER.warning(
                            f"{self.deviceName}: Entity '{entity_id}' is currently {state.state}. "
                            f"The device may not respond to commands until it becomes available."
                        )

    def _load_settings(self):
        """Load Far Red settings from datastore."""
        try:
            # Get main light times
            light_on_str = self.data_store.getDeep("isPlantDay.lightOnTime")
            light_off_str = self.data_store.getDeep("isPlantDay.lightOffTime")
            
            if light_on_str:
                self.lightOnTime = datetime.strptime(light_on_str, "%H:%M:%S").time()
            if light_off_str:
                self.lightOffTime = datetime.strptime(light_off_str, "%H:%M:%S").time()
                
            self.islightON = self.data_store.getDeep("isPlantDay.islightON")
            
            # Get Far Red specific settings (with defaults)
            fr_settings = self.data_store.getDeep("specialLights.farRed") or {}
            
            # Also read directly from the path that OGBConfigurationManager uses
            direct_start = self.data_store.getDeep("specialLights.farRed.startDurationMinutes")
            direct_end = self.data_store.getDeep("specialLights.farRed.endDurationMinutes")
            
            _LOGGER.debug(f"{self.deviceName}: Raw fr_settings from datastore: {fr_settings}")
            _LOGGER.debug(f"{self.deviceName}: Direct startDurationMinutes: {direct_start}")
            _LOGGER.debug(f"{self.deviceName}: Direct endDurationMinutes: {direct_end}")
            
            # Use direct values if available, otherwise fall back to fr_settings dict
            self.start_duration_minutes = direct_start if direct_start is not None else fr_settings.get("startDurationMinutes", 15)
            self.end_duration_minutes = direct_end if direct_end is not None else fr_settings.get("endDurationMinutes", 15)
            
            # CRITICAL: Check enabled setting FIRST - this controls whether light operates
            self.enabled = fr_settings.get("enabled", True)  # Default to enabled for backward compatibility
            
            # Get mode setting - this determines behavior
            self.mode = fr_settings.get("mode", FarRedMode.SCHEDULE)
            # start_duration_minutes and end_duration_minutes already set above (lines 206-207)
            
            # Intensity from data store or default
            self.intensity = fr_settings.get("intensity", 100)

            # New smart scheduling features (enabled by default)
            self.smart_start_enabled = fr_settings.get("smartStartEnabled", True)
            self.smart_end_enabled = fr_settings.get("smartEndEnabled", True)
            
            _LOGGER.info(
                f"{self.deviceName}: FarRed settings loaded - "
                f"Enabled: {self.enabled}, Mode: {self.mode}, "
                f"Start: {self.start_duration_minutes}min, End: {self.end_duration_minutes}min, "
                f"Intensity: {self.intensity}%, "
                f"LightOn: {self.lightOnTime}, LightOff: {self.lightOffTime}"
            )
            
            # Only start scheduler if enabled AND mode is Schedule
            if self.enabled and self.mode == FarRedMode.SCHEDULE:
                _LOGGER.info(f"{self.deviceName}: FarRed enabled={self.enabled}, mode={self.mode} - Starting scheduler with immediate check")
                asyncio.create_task(self._start_scheduler_with_immediate_check())
            else:
                # If disabled, ensure FarRed is off and scheduler is not running
                if self.is_fr_active:
                    _LOGGER.info(f"{self.deviceName}: FarRed disabled, deactivating...")
                    asyncio.create_task(self._deactivate_far_red("Disabled"))
                else:
                    _LOGGER.info(f"{self.deviceName}: FarRed disabled, scheduler not started")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error loading settings: {e}")

    async def WorkMode(self, workmode):
        """Override WorkMode - FarRed uses dedicated scheduling, not WorkMode system."""
        _LOGGER.debug(f"{self.deviceName}: Ignoring WorkMode {workmode}, using dedicated FarRed scheduling")
        # Do NOT call super().WorkMode() - we handle our own scheduling

    async def _start_scheduler(self):
        """Start the periodic scheduler for Far Red timing. Uses _restart_scheduler for safety."""
        await self._restart_scheduler()

    async def _start_scheduler_with_immediate_check(self):
        """Start the scheduler.
        
        Note: Removed immediate check to prevent race conditions with the periodic scheduler.
        The first check will happen after 10 seconds when _schedule_loop runs.
        """
        _LOGGER.info(f"{self.deviceName}: Starting scheduler")
        await self._restart_scheduler()

    async def _restart_scheduler(self):
        """Safely restart the scheduler with lock protection."""
        async with self._scheduler_lock:
            if self._schedule_task and not self._schedule_task.done():
                _LOGGER.info(f"{self.deviceName}: Stopping existing scheduler before restart")
                self._schedule_task.cancel()
                try:
                    await asyncio.wait_for(self._schedule_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                self._schedule_task = None
            
            if self.enabled and self.mode == FarRedMode.SCHEDULE:
                self._schedule_task = asyncio.create_task(self._schedule_loop())
                _LOGGER.info(f"{self.deviceName}: Far Red scheduler restarted")
            elif not self.enabled or self.mode == FarRedMode.ALWAYS_OFF:
                if self.is_fr_active:
                    await self._deactivate_far_red()
                _LOGGER.info(f"{self.deviceName}: FarRed not restarting - enabled={self.enabled}, mode={self.mode}")

    async def _on_sunrise_window_status(self, data):
        """FarRed has its own scheduling - ignore sunrise window events from main light."""
        pass

    async def _on_sunset_window_status(self, data):
        """FarRed has its own scheduling - ignore sunset window events from main light."""
        pass

    async def _schedule_loop(self):
        """Main scheduling loop - checks every 10 seconds for activation conditions."""
        while True:
            try:
                await self._check_activation_conditions()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Schedule loop error: {e}")
                import traceback
                _LOGGER.error(traceback.format_exc())
            
            await asyncio.sleep(10)  # Check every 10 seconds for smoother ramping

    async def _check_activation_conditions(self):
        """Check if Far Red should be ON or OFF based on current mode."""
        
        # Mode: Always Off - never activate automatically
        if self.mode == FarRedMode.ALWAYS_OFF:
            if self.is_fr_active:
                await self._deactivate_far_red()
            return
        
        # Mode: Manual - don't do anything automatic
        if self.mode == FarRedMode.MANUAL:
            return
        
        # Mode: Always On - ON whenever main lights are ON
        if self.mode == FarRedMode.ALWAYS_ON:
            if self.islightON:
                if not self.is_fr_active:
                    await self._activate_far_red('always_on')
            else:
                if self.is_fr_active:
                    await self._deactivate_far_red()
            return
        
        # Mode: Schedule - original start/end window logic
        if self.mode == FarRedMode.SCHEDULE:
            await self._check_schedule_windows()

    async def _check_schedule_windows(self):
        """Check if we're in a Far Red activation window (Schedule mode).
        
        FarRed behavior:
        - Starts 7.5 min BEFORE main lights (ramp 0→100%)
        - Runs 7.5 min WITH main lights (100%)
        - Starts 7.5 min BEFORE lights off (ramp 100→0%)
        - Runs 7.5 min AFTER lights off (0%)
        
        Total windows: 15 min each, centered on light on/off times
        """
        # CRITICAL: Check if enabled first
        if not getattr(self, 'enabled', True):
            _LOGGER.debug(f"{self.deviceName}: Schedule check skipped (disabled)")
            # Ensure we're off if disabled
            if self.is_fr_active:
                await self._deactivate_far_red("Disabled")
            return

        if not self.lightOnTime or not self.lightOffTime:
            return
            
        now = datetime.now()
        current_time = now.time()
        
        # Calculate window times
        light_on_dt = datetime.combine(now.date(), self.lightOnTime)
        light_off_dt = datetime.combine(now.date(), self.lightOffTime)
        
        # Handle overnight schedules
        if self.lightOffTime < self.lightOnTime:
            if current_time < self.lightOffTime:
                light_on_dt -= timedelta(days=1)
            else:
                light_off_dt += timedelta(days=1)
        
        # FarRed windows are centered on light_on/off times
        # Start window: 7.5 min before + 7.5 min after = 15 min total, ends at light_on_time
        # End window: 7.5 min before + 7.5 min after = 15 min total, ends at light_off_time
        
        # Start window: centered on lightOnTime (ramp 50%→100%)
        half_start_duration = timedelta(minutes=self.start_duration_minutes / 2)  # 7.5 min
        start_window_begin = light_on_dt - half_start_duration
        start_window_end = light_on_dt  # Ends exactly at light_on_time
        
        # End window: centered on lightOffTime (ramp 100%→0%)
        half_end_duration = timedelta(minutes=self.end_duration_minutes / 2)  # 7.5 min
        end_window_begin = light_off_dt - half_end_duration
        end_window_end = light_off_dt  # Ends exactly at light_off_time
        
        in_start_window = start_window_begin <= now <= start_window_end
        in_end_window = end_window_begin <= now <= end_window_end
        
        
        # FarRed Phase Schedule:
        # Phase 1: 0-7.5 min VOR light_on: 20% → 50%
        # Phase 2: 7.5-15 min NACH light_on: 50% → 100%
        # PAUSE: Dazwischen (AUS)
        # Phase 3: 0-7.5 min VOR light_off: 100% → 50%
        # Phase 4: 7.5-15 min NACH light_off: 50% → 20%
        
        current_intensity = 20
        is_ramping = False
        ramp_direction = None
        
        # Calculate window boundaries
        start_window_begin = light_on_dt - half_start_duration
        phase_2_end = light_on_dt + half_start_duration
        end_window_begin = light_off_dt - half_end_duration
        end_window_end = light_off_dt + half_end_duration
        
        if now < start_window_begin:
            # Before Phase 1: off (standby at 20%)
            current_intensity = 20
            is_ramping = False
            ramp_direction = None
        elif now < light_on_dt:
            # Phase 1: 20% → 50% (erste Hälfte von start_duration)
            elapsed = (now - start_window_begin).total_seconds()
            progress = min(elapsed / half_start_duration.total_seconds(), 1.0)
            current_intensity = 20 + (progress * 30)  # 20% → 50%
            is_ramping = True
            ramp_direction = 'up'
        elif now < phase_2_end:
            # Phase 2: 50% → 100% (zweite Hälfte von start_duration)
            elapsed = (now - light_on_dt).total_seconds()
            progress = min(elapsed / half_start_duration.total_seconds(), 1.0)
            current_intensity = 50 + (progress * 50)
            # Ensure 100% is reached on the last scheduler tick before Phase 2 ends
            # If next tick would be after Phase 2 ends, force 100%
            if (now + timedelta(seconds=10)) >= phase_2_end:
                current_intensity = 100
            is_ramping = True
            ramp_direction = 'up'
        elif now < end_window_begin:
            # PAUSE: Dazwischen - FarRed ist AUS
            current_intensity = 20
            is_ramping = False
            ramp_direction = None
        elif now < light_off_dt:
            # Phase 3: 100% → 50% (erste Hälfte von end_duration)
            elapsed = (now - end_window_begin).total_seconds()
            progress = min(elapsed / half_end_duration.total_seconds(), 1.0)
            current_intensity = 100 - (progress * 50)  # 100% → 50%
            is_ramping = True
            ramp_direction = 'down'
        elif now < end_window_end:
            # Phase 4: 50% → 20% (zweite Hälfte von end_duration)
            elapsed = (now - light_off_dt).total_seconds()
            progress = min(elapsed / half_end_duration.total_seconds(), 1.0)
            current_intensity = 50 - (progress * 30)  # 50% → 20%
            is_ramping = True
            ramp_direction = 'down'
        else:
            # After Phase 4: off
            current_intensity = 20
            is_ramping = False
            ramp_direction = None
        
        _LOGGER.info(
            f"{self.deviceName}: Time check - Now: {now.strftime('%H:%M:%S')}, "
            f"LightOn: {self.lightOnTime}, LightOff: {self.lightOffTime}, "
            f"InStart: {now < light_on_dt}, InMid: {light_on_dt <= now < end_window_begin}, InEnd: {end_window_begin <= now < end_window_end}, "
            f"Intensity: {current_intensity:.1f}%, Ramping: {is_ramping} ({ramp_direction})"
        )
        
        # Track if we were previously active (before this calculation)
        was_previously_active = self.is_fr_active
        last_processed = getattr(self, '_last_processed_intensity', 0)
        
        # Phase detection - order matters for if-elif chain!
        phase_2_end = light_on_dt + half_start_duration
        
        in_phase_1 = start_window_begin <= now < light_on_dt
        in_phase_2 = light_on_dt <= now < phase_2_end
        in_pause = phase_2_end <= now < end_window_begin
        in_phase_3 = end_window_begin <= now < light_off_dt
        in_phase_4 = light_off_dt <= now < end_window_end
        
        # Calculate what intensity the device SHOULD be at
        target_intensity = current_intensity
        
        # Calculate progress for Phase 2 (needed for activation logic)
        phase_2_progress = 0.0
        if now >= light_on_dt:
            elapsed = (now - light_on_dt).total_seconds()
            phase_2_progress = min(elapsed / half_start_duration.total_seconds(), 1.0)
        
        # Check if we're at the end of Phase 2 or in PAUSE
        # Use time-based check to ensure we catch the transition
        time_until_phase_2_end = (phase_2_end - now).total_seconds() if phase_2_end > now else 0
        
        # Activation logic - use small threshold for smooth ramping
        if in_phase_1:
            # Phase 1: 20% → 50% - activate if first time or significant change
            if not was_previously_active or abs(last_processed - target_intensity) > 0.2:
                self._last_processed_intensity = target_intensity
                await self._activate_far_red_with_intensity(target_intensity, ramp_direction)
        elif in_phase_2:
            # Phase 2: 50% → 100% - ramping up
            # Always activate with target intensity (including final 100%)
            if not was_previously_active or abs(last_processed - target_intensity) > 0.2:
                self._last_processed_intensity = target_intensity
                await self._activate_far_red_with_intensity(target_intensity, ramp_direction)
            
            # Check if Phase 2 is ending (within last 20 seconds)
            # This ensures we turn off after reaching 100%
            if time_until_phase_2_end <= 20 and target_intensity >= 99.5:
                _LOGGER.info(f"{self.deviceName}: Phase 2 ending at {target_intensity:.1f}%, will deactivate after current tick")
                # Schedule deactivation after this tick completes
                asyncio.create_task(self._delayed_deactivate(5, "Phase 2 complete"))

        elif in_pause:
            # PAUSE: FarRed ist AUS - ensure it's off
            # If we just came from Phase 2 at 100%, deactivate now
            if self.is_fr_active:
                await self._deactivate_far_red("Pause before Phase 3")
        elif in_phase_3:
            # Phase 3: 100% → 50% - activate to reach target intensity
            # Don't check was_previously_active - activate based on target intensity
            if abs(last_processed - target_intensity) > 0.2:
                self._last_processed_intensity = target_intensity
                await self._activate_far_red_with_intensity(target_intensity, ramp_direction)
        elif in_phase_4:
            # Phase 4: Update intensity for ramping
            if was_previously_active and abs(last_processed - target_intensity) > 0.2:
                self._last_processed_intensity = target_intensity
                await self._activate_far_red_with_intensity(target_intensity, ramp_direction)
        else:
            # Outside all windows: ensure FarRed is off
            if was_previously_active:
                await self._deactivate_far_red("Outside schedule windows")
        
    async def _activate_far_red_with_intensity(self, intensity: float, direction: Optional[str] = None):
        """Activate Far Red light with specific intensity and ramping direction.
        
        Args:
            intensity: Target intensity (0-100%)
            direction: 'up' for ramping up, 'down' for ramping down
        """
        self.current_intensity = intensity
        self.current_phase = direction  # 'up' or 'down'
        self.is_fr_active = True
        
        message = f"FarRed activated - {intensity:.1f}% ({direction} ramping)"
        _LOGGER.info(f"{self.deviceName}: {message}")
        
        # Create action log
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type="LightFarRed",
            Action="ON",
            Message=message,
            Voltage=int(intensity),
            Dimmable=False,
            SunRise=False,
            SunSet=False,
        )
        await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
        
        # Turn on the light with intensity - round properly and cap at 100%
        display_intensity = min(100, round(intensity))
        await self.turn_on(brightness_pct=display_intensity)

    async def _activate_far_red(self, phase: str):
        """Activate Far Red light for the specified phase."""
        if self.is_fr_active and self.current_phase == phase:
            return  # Already active in this phase
            
        self.current_phase = phase
        self.is_fr_active = True
        
        # Create descriptive message based on phase
        if phase == 'always_on':
            message = f"FarRed activated (Always On mode)"
        else:
            message = f"FarRed {phase.upper()} phase activated"
        
        _LOGGER.info(f"{self.deviceName}: {message}")
        
        # Create action log
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type="LightFarRed",
            Action="ON",
            Message=message,
            Voltage=self.intensity,
            Dimmable=False,
            SunRise=False,
            SunSet=False,
        )
        await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
        
        # Turn on the light with intensity
        await self.turn_on(brightness_pct=int(self.intensity))

    async def _deactivate_far_red(self, reason: Optional[str] = None):
        """Deactivate Far Red light - uses direct HA service call to bypass any issues."""
        if not self.is_fr_active:
            _LOGGER.debug(f"{self.deviceName}: Already inactive, skipping deactivate")
            return
            
        previous_phase = self.current_phase
        self.is_fr_active = False
        self.current_phase = None
        self.current_intensity = 0.0
        self._last_processed_intensity = None  # Reset to prevent reactivation
        
        # Use provided reason or generate from phase
        if reason:
            message = f"FarRed deactivated: {reason}"
        elif previous_phase == 'always_on':
            message = "FarRed deactivated (main lights off)"
        elif previous_phase:
            message = f"FarRed {previous_phase.upper()} phase ended"
        else:
            message = "FarRed deactivated"
        
        _LOGGER.info(f"{self.deviceName}: {message}")
        
        # Create action log
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type="LightFarRed",
            Action="OFF",
            Message=message,
            Voltage=0,
            Dimmable=False,
            SunRise=False,
            SunSet=False,
        )
        await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
        
        # Turn off the light using direct HA service call - bypasses any internal logic
        entity_id = None
        if self.switches:
            for switch in self.switches:
                eid = switch.get("entity_id", "")
                if "light." in eid:
                    entity_id = eid
                    break
        
        if entity_id:
            try:
                # Direct HA call to turn off - bypasses all internal logic
                await self.hass.services.async_call(
                    domain="light",
                    service="turn_off",
                    service_data={"entity_id": entity_id},
                )
                _LOGGER.info(f"{self.deviceName}: FarRed turned off via direct HA call")
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Direct HA turn_off failed: {e}")
                # Fallback: try turning on with 0%
                try:
                    await self.hass.services.async_call(
                        domain="light",
                        service="turn_on",
                        service_data={"entity_id": entity_id, "brightness_pct": 0},
                    )
                    _LOGGER.info(f"{self.deviceName}: FarRed turned off via fallback (brightness_pct=0)")
                except Exception as e2:
                    _LOGGER.error(f"{self.deviceName}: Fallback also failed: {e2}")
        else:
            _LOGGER.warning(f"{self.deviceName}: No light entity found in switches, trying turn_off()")
            try:
                await self.turn_off()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: turn_off() also failed: {e}")
        
        # Ensure voltage is reset
        if hasattr(self, 'voltage'):
            self.voltage = 0

    async def _delayed_deactivate(self, delay_seconds: float, reason: str):
        """Schedule FarRed deactivation after a delay - ensures 100% is applied before turning off."""
        await asyncio.sleep(delay_seconds)
        await self._deactivate_far_red(reason)

    async def _on_light_time_change(self, data):
        """Handle main light schedule changes - reload settings and restart scheduler."""
        _LOGGER.info(f"{self.deviceName}: Light schedule changed, reloading settings")
        
        # Use _restart_scheduler for safe scheduler restart with lock protection
        await self._restart_scheduler()

    async def _on_main_light_toggle(self, lightState):
        """Handle main light toggle events with intelligent filtering."""
        # CRITICAL: Check if enabled FIRST
        if not getattr(self, 'enabled', True):
            _LOGGER.debug(f"{self.deviceName}: Ignoring toggleLight (disabled)")
            return

        # Handle both old format (boolean) and new format (dict with target_devices)
        target_state = lightState
        is_targeted = True

        if isinstance(lightState, dict):
            # New format: {"state": True/False, "target_devices": ["device1", "device2"]}
            target_state = lightState.get("state", False)
            target_devices = lightState.get("target_devices", [])
            # Check if this device is in the target list
            is_targeted = not target_devices or self.deviceName in target_devices

        self.islightON = target_state

        # If this device is not targeted, ignore the event completely
        if not is_targeted:
            _LOGGER.debug(f"{self.deviceName}: Not targeted by toggleLight event, ignoring")
            return

        # CRITICAL: Only respond to ToggleLight if mode is ALWAYS_ON
        # If mode is SCHEDULE, our scheduler handles timing - ignore ToggleLight
        # If mode is ALWAYS_OFF or MANUAL, never respond to ToggleLight
        if self.mode == FarRedMode.SCHEDULE:
            _LOGGER.debug(f"{self.deviceName}: Ignoring toggleLight in Schedule mode (using dedicated scheduling)")
            return

        if self.mode == FarRedMode.ALWAYS_OFF:
            _LOGGER.debug(f"{self.deviceName}: Ignoring toggleLight in Always Off mode")
            return

        if self.mode == FarRedMode.MANUAL:
            _LOGGER.debug(f"{self.deviceName}: Ignoring toggleLight in Manual mode")
            return

        # Mode is ALWAYS_ON - respond to main light toggle
        if self.mode == FarRedMode.ALWAYS_ON:
            if not target_state:
                # Main lights going off - deactivate FarRed
                if self.is_fr_active:
                    await self._deactivate_far_red("Main lights off (Always On mode)")
            else:
                # Main lights coming on - activate FarRed
                if not self.is_fr_active:
                    await self._activate_far_red('always_on')

        _LOGGER.debug(f"{self.deviceName}: Main light toggled to {target_state} (mode={self.mode})")

    async def _on_settings_update(self, data):
        """Handle Far Red settings updates from UI."""
        if data.get("device") == self.deviceName or data.get("device") is None:
            settings_changed = False
            
            # Update mode
            if "mode" in data:
                old_mode = self.mode
                self.mode = data["mode"]
                if old_mode != self.mode:
                    settings_changed = True
                    _LOGGER.info(f"{self.deviceName}: Mode changed from '{old_mode}' to '{self.mode}'")
                    
                    # Handle mode transitions
                    if self.mode == FarRedMode.ALWAYS_OFF:
                        # Switching to Always Off - deactivate immediately
                        if self.is_fr_active:
                            await self._deactivate_far_red()
                    elif self.mode == FarRedMode.ALWAYS_ON and self.islightON:
                        # Switching to Always On while lights are on - activate
                        if not self.is_fr_active:
                            await self._activate_far_red('always_on')
                    elif self.mode == FarRedMode.SCHEDULE:
                        # Switching to Schedule - check windows immediately
                        await self._check_schedule_windows()
            
            # Update enabled state
            if "enabled" in data:
                enabled = data["enabled"]
                if not enabled:
                    # Disabled - deactivate if active
                    if self.is_fr_active:
                        await self._deactivate_far_red()
            
            # Update timing settings
            if "startDurationMinutes" in data:
                self.start_duration_minutes = data["startDurationMinutes"]
                settings_changed = True
            if "endDurationMinutes" in data:
                self.end_duration_minutes = data["endDurationMinutes"]
                settings_changed = True
            if "intensity" in data:
                self.intensity = data["intensity"]
                settings_changed = True

            # Update smart scheduling features
            if "smartStartEnabled" in data:
                self.smart_start_enabled = data["smartStartEnabled"]
                settings_changed = True
            if "smartEndEnabled" in data:
                self.smart_end_enabled = data["smartEndEnabled"]
                settings_changed = True
                
            if settings_changed:
                _LOGGER.info(
                    f"{self.deviceName}: Settings updated - "
                    f"Mode: {self.mode}, "
                    f"Start: {self.start_duration_minutes}min, End: {self.end_duration_minutes}min, "
                    f"Intensity: {self.intensity}%"
                )

    def get_status(self) -> dict:
        """Get current Far Red light status."""
        return {
            "device_name": self.deviceName,
            "device_type": "LightFarRed",
            "mode": self.mode,
            "is_active": self.is_fr_active,
            "current_phase": self.current_phase,
            "is_running": self.isRunning,
            "intensity": self.intensity,
            "start_duration_minutes": self.start_duration_minutes,
            "end_duration_minutes": self.end_duration_minutes,
            "light_on_time": str(self.lightOnTime) if self.lightOnTime else None,
            "light_off_time": str(self.lightOffTime) if self.lightOffTime else None,
        }

    async def cleanup(self):
        """Cleanup tasks on shutdown."""
        if self._schedule_task and not self._schedule_task.done():
            self._schedule_task.cancel()
            try:
                await self._schedule_task
            except asyncio.CancelledError:
                pass
                
        if self._turn_off_task and not self._turn_off_task.done():
            self._turn_off_task.cancel()
            try:
                await self._turn_off_task
            except (asyncio.CancelledError, TypeError):
                pass

"""
OpenGrowBox UV Light Device

LABEL: lightuv

UV lights (UVA/UVB) are used for:
- Stress response triggering (trichome/resin production)
- Pathogen control
- Compact growth

Mode options:
- Schedule: ON during middle portion of light cycle (default behavior)
- Always On: ON whenever main lights are ON
- Always Off: Disabled, never turns on automatically
- Manual: Only responds to manual commands, no automatic control

Timing behavior (Schedule mode only):
- OFF at start of light cycle (plants need to "wake up")
- ON during middle portion of light cycle
- OFF before end of light cycle (recovery time)

Default: Active for middle 4-6 hours of a 12-hour light cycle
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from .Light import Light
from ..data.OGBDataClasses.OGBPublications import OGBLightAction

_LOGGER = logging.getLogger(__name__)


# Valid modes for UV light control
class UVMode:
    SCHEDULE = "Schedule"      # Mid-day timing window
    ALWAYS_ON = "Always On"    # ON when main lights are ON
    ALWAYS_OFF = "Always Off"  # Never on automatically
    MANUAL = "Manual"          # Only manual control


class LightUV(Light):
    """UV light device with configurable operation modes."""

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
        self.mode = UVMode.SCHEDULE  # Default to schedule-based operation
        
        # UV specific settings (for Schedule mode)
        self.delay_after_start_minutes = 120  # Wait 2 hours after lights on
        self.stop_before_end_minutes = 120    # Stop 2 hours before lights off
        self.max_duration_hours = 6           # Maximum UV exposure per day
        self.intensity_percent = 100          # UV intensity (if dimmable)

        # New midday scheduling features
        self.midday_start_time = "12:00"      # Start midday period (preset options)
        self.midday_end_time = "14:00"        # End midday period (preset options)
        
        # State tracking
        self.is_uv_active = False
        self.current_phase: Optional[str] = None  # 'schedule', 'always_on', or None
        self.daily_exposure_minutes = 0
        self.last_exposure_date = None
        
        # Light schedule reference
        self.lightOnTime = None
        self.lightOffTime = None
        self.islightON = None
        
        # Task tracking
        self._schedule_task = None
        
        # Initialize parent class first (important for Device inheritance)
        self.init()

        # Initialize UV specific settings
        self._load_settings()

        # Register event handlers FIRST (before scheduler starts)
        # This ensures we don't miss any events emitted during startup
        self.event_manager.on("LightTimeChanges", self._on_light_time_change)
        self.event_manager.on("toggleLight", self._on_main_light_toggle)
        self.event_manager.on("UVSettingsUpdate", self._on_settings_update)

        # Validate entity availability
        self._validate_entity_availability()

        # Scheduler is started in _load_settings() when enabled=True
        # Don't start here to avoid duplicate scheduler starts

    def _validate_entity_availability(self):
        """
        Validate that the light entity is available in Home Assistant.
        If switches list is empty, log warning and attempt to find the entity.
        """
        if not self.switches:
            _LOGGER.warning(
                f"{self.deviceName}: No switches/entities found! "
                f"The UV light entity may be unavailable or not correctly labeled. "
                f"Please ensure the entity exists in Home Assistant and has the correct label "
                f"(light_uv, uv, ultraviolet)."
            )
            if self.hass:
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
                    f"Please check that your UV light device exists and is correctly configured."
                )
        else:
            for switch in self.switches:
                entity_id = switch.get("entity_id")
                if self.hass and entity_id:
                    state = self.hass.states.get(entity_id)
                    if state and state.state in ("unavailable", "unknown"):
                        _LOGGER.warning(
                            f"{self.deviceName}: Entity '{entity_id}' is currently {state.state}. "
                            f"The device may not respond to commands until it becomes available."
                        )

    def __repr__(self):
        return (
            f"LightUV('{self.deviceName}' in {self.inRoom}) "
            f"Mode:{self.mode} "
            f"DelayStart:{self.delay_after_start_minutes}min StopBefore:{self.stop_before_end_minutes}min "
            f"MaxDuration:{self.max_duration_hours}h Active:{self.is_uv_active} Running:{self.isRunning}"
        )

    def _load_settings(self):
        """Load UV settings from datastore."""
        try:
            # Get main light times
            light_on_str = self.data_store.getDeep("isPlantDay.lightOnTime")
            light_off_str = self.data_store.getDeep("isPlantDay.lightOffTime")
            
            if light_on_str:
                self.lightOnTime = datetime.strptime(light_on_str, "%H:%M:%S").time()
            if light_off_str:
                self.lightOffTime = datetime.strptime(light_off_str, "%H:%M:%S").time()
                
            self.islightON = self.data_store.getDeep("isPlantDay.islightON")
            
            # Get UV specific settings (with defaults)
            uv_settings = self.data_store.getDeep("specialLights.uv") or {}
            
            # CRITICAL: Check enabled setting FIRST - this controls whether light operates
            self.enabled = uv_settings.get("enabled", True)  # Default to enabled for backward compatibility
            
            # Get mode setting - this determines behavior
            self.mode = uv_settings.get("mode", UVMode.SCHEDULE)
            self.delay_after_start_minutes = uv_settings.get("delayAfterStartMinutes", 120)
            self.stop_before_end_minutes = uv_settings.get("stopBeforeEndMinutes", 120)
            self.max_duration_hours = uv_settings.get("maxDurationHours", 6)
            self.intensity_percent = uv_settings.get("intensity", 100)

            # New midday scheduling features (with defaults)
            self.midday_start_time = uv_settings.get("middayStartTime", "12:00")
            self.midday_end_time = uv_settings.get("middayEndTime", "14:00")
            
            _LOGGER.info(
                f"{self.deviceName}: UV settings loaded - "
                f"Enabled: {self.enabled}, Mode: {self.mode}, "
                f"Delay: {self.delay_after_start_minutes}min, StopBefore: {self.stop_before_end_minutes}min, "
                f"MaxDuration: {self.max_duration_hours}h, Intensity: {self.intensity_percent}%, "
                f"Midday: {self.midday_start_time}-{self.midday_end_time}, "
                f"LightOn: {self.lightOnTime}, LightOff: {self.lightOffTime}"
            )
            
            # CRITICAL: Stop any existing scheduler before deciding to start a new one
            if self._schedule_task and not self._schedule_task.done():
                _LOGGER.info(f"{self.deviceName}: Stopping existing scheduler before reload")
                self._schedule_task.cancel()
                self._schedule_task = None
            
            # Only start scheduler if enabled AND mode is Schedule - use immediate check
            if self.enabled and self.mode == UVMode.SCHEDULE:
                _LOGGER.info(f"{self.deviceName}: UV enabled={self.enabled}, mode={self.mode} - Starting scheduler with immediate check")
                asyncio.create_task(self._start_scheduler_with_immediate_check())
            else:
                _LOGGER.info(
                    f"{self.deviceName}: UV NOT starting scheduler - "
                    f"enabled={self.enabled} (type: {type(self.enabled).__name__}), "
                    f"mode={self.mode}"
                )
                # Ensure light is off if not enabled or not in schedule mode
                if not self.enabled or self.mode == UVMode.ALWAYS_OFF:
                    _LOGGER.info(f"{self.deviceName}: UV disabled or Always Off - ensuring light is off")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error loading settings: {e}")

    async def WorkMode(self, workmode):
        """Override WorkMode - UV uses dedicated scheduling, not WorkMode system."""
        _LOGGER.debug(f"{self.deviceName}: Ignoring WorkMode {workmode}, using dedicated UV scheduling")
        # Do NOT call super().WorkMode() - we handle our own scheduling

    def _start_scheduler(self):
        """Start the periodic scheduler for UV timing."""
        if self._schedule_task and not self._schedule_task.done():
            return
            
        self._schedule_task = asyncio.create_task(self._schedule_loop())
        _LOGGER.info(f"{self.deviceName}: UV scheduler started")

    async def _start_scheduler_with_immediate_check(self):
        """Start the scheduler and run an immediate check without waiting.
        
        This ensures UV can activate immediately if we're already in a window,
        without waiting for the first sleep cycle to complete.
        """
        _LOGGER.info(f"{self.deviceName}: Starting scheduler with immediate check")
        
        # Run immediate check first (no wait)
        try:
            _LOGGER.info(f"{self.deviceName}: Running immediate activation check")
            await self._check_activation_conditions()
            self._check_daily_reset()
            _LOGGER.info(f"{self.deviceName}: Immediate check completed")
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Immediate check error: {e}")
            import traceback
            _LOGGER.error(traceback.format_exc())
        
        # Then start the periodic scheduler
        self._start_scheduler()

    async def _on_sunrise_window_status(self, data):
        """UV has its own scheduling - ignore sunrise window events from main light."""
        pass

    async def _on_sunset_window_status(self, data):
        """UV has its own scheduling - ignore sunset window events from main light."""
        pass

    async def _schedule_loop(self):
        """Main scheduling loop - checks every minute for activation conditions."""
        _LOGGER.info(f"{self.deviceName}: UV scheduler loop started")
        while True:
            try:
                _LOGGER.debug(f"{self.deviceName}: UV scheduler tick - running check")
                await self._check_activation_conditions()
                self._check_daily_reset()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Schedule loop error: {e}")
                import traceback
                _LOGGER.error(traceback.format_exc())
            
            await asyncio.sleep(60)  # Check every minute

    def _check_daily_reset(self):
        """Reset daily exposure counter at midnight."""
        today = datetime.now().date()
        if self.last_exposure_date != today:
            self.daily_exposure_minutes = 0
            self.last_exposure_date = today
            _LOGGER.debug(f"{self.deviceName}: Daily UV exposure counter reset")

    async def _check_activation_conditions(self):
        """Check if UV should be ON or OFF based on current mode."""
        
        # Mode: Always Off - never activate automatically
        if self.mode == UVMode.ALWAYS_OFF:
            if self.is_uv_active:
                await self._deactivate_uv("Always Off mode")
            return
        
        # Mode: Manual - don't do anything automatic
        if self.mode == UVMode.MANUAL:
            return
        
        # Mode: Always On - ON whenever main lights are ON (but NOT during sunrise/sunset)
        if self.mode == UVMode.ALWAYS_ON:
            # Check if main light is in sunrise or sunset phase - UV must be OFF during transitions
            sun_phase_active = getattr(self, 'sunPhaseActive', False)
            if self.islightON and not sun_phase_active:
                if not self.is_uv_active:
                    await self._activate_uv('always_on')
            else:
                if self.is_uv_active:
                    reason = "Main lights off" if not self.islightON else "Sun phase active"
                    await self._deactivate_uv(reason)
            return
        
        # Mode: Schedule - original mid-day window logic
        if self.mode == UVMode.SCHEDULE:
            await self._check_schedule_window()

    async def _check_schedule_window(self):
        """Check if we're in a UV activation window (Schedule mode).
        
        UV Behavior:
        - Uses relative schedule: starts after delay, ends before stop
        - Default: 2 hours (120 min) after light on, 2 hours (120 min) before light off
        - Can be customized via delayAfterStartMinutes and stopBeforeEndMinutes
        
        Default behavior:
        - If light is 07:00-22:00:
          - UV starts at: 07:00 + 120min = 09:00
          - UV ends at: 22:00 - 120min = 20:00
          - UV window: 09:00-20:00 (11 hours total)
        """
        # CRITICAL: Check if enabled first
        if not getattr(self, 'enabled', True):
            _LOGGER.debug(f"{self.deviceName}: Schedule check skipped (disabled)")
            # Ensure we're off if disabled
            if self.is_uv_active:
                await self._deactivate_uv("Disabled")
            return

        if not self.lightOnTime or not self.lightOffTime:
            return
            
        # Check if main light is in sunrise or sunset phase - UV must be OFF during transitions
        # Use both methods for maximum reliability
        sun_phase_active = getattr(self, 'sunPhaseActive', False)
        
        # Additional check: Look for main light states in data store
        main_light_sun_phase = self.data_store.getDeep("isPlantDay.sunPhaseActive") or False
        main_light_state = self.data_store.getDeep("isPlantDay.islightON") or False
        
        # UV must be off if ANY of these conditions are met
        if not main_light_state or sun_phase_active or main_light_sun_phase:
            if self.is_uv_active:
                reason = "Main lights off"
                if not main_light_state:
                    reason = "Main lights off"
                elif sun_phase_active:
                    reason = "Sun phase active"
                elif main_light_sun_phase:
                    reason = "Sun phase active"
                await self._deactivate_uv(reason)
            return
            
        now = datetime.now()
        current_time = now.time()
        
        # Calculate light period
        light_on_dt = datetime.combine(now.date(), self.lightOnTime)
        light_off_dt = datetime.combine(now.date(), self.lightOffTime)
        
        # Handle overnight schedules
        if self.lightOffTime < self.lightOnTime:
            if current_time < self.lightOffTime:
                light_on_dt -= timedelta(days=1)
            else:
                light_off_dt += timedelta(days=1)
        
        # Calculate UV window based on delay/stop relative to light times
        # Default: 120 min (2 hours) after light on, 120 min before light off
        delay_minutes = getattr(self, 'delay_after_start_minutes', 120)
        stop_minutes = getattr(self, 'stop_before_end_minutes', 120)
        
        uv_start = light_on_dt + timedelta(minutes=delay_minutes)
        uv_end = light_off_dt - timedelta(minutes=stop_minutes)
        
        # Check max duration limit
        max_duration_hours = getattr(self, 'max_duration_hours', 6)
        max_duration_dt = timedelta(hours=max_duration_hours)
        light_duration = (light_off_dt - light_on_dt).total_seconds() / 60  # in minutes
        
        if (uv_end - uv_start).total_seconds() > max_duration_dt.total_seconds():
            # Calculate center of the allowed UV window within light period
            # UV window should be: delay_minutes after start to stop_minutes before end
            available_start = light_on_dt + timedelta(minutes=delay_minutes)
            available_end = light_off_dt - timedelta(minutes=stop_minutes)
            available_duration = (available_end - available_start).total_seconds() / 2  # Split remaining time
            
            center = available_start + timedelta(seconds=available_duration)
            uv_start = center - (max_duration_dt / 2)
            uv_end = center + (max_duration_dt / 2)
        
        in_uv_window = uv_start <= now <= uv_end
        
        # Check if we've hit daily exposure limit
        max_daily_minutes = max_duration_hours * 60
        daily_exposure = getattr(self, 'daily_exposure_minutes', 0)
        exposure_limit_reached = daily_exposure >= max_daily_minutes
        
        _LOGGER.info(
            f"{self.deviceName}: UV check - Now: {now.strftime('%H:%M')}, "
            f"Light: {self.lightOnTime}-{self.lightOffTime}, "
            f"Delay: {delay_minutes}min, StopBefore: {stop_minutes}min, "
            f"Window: {uv_start.strftime('%H:%M')}-{uv_end.strftime('%H:%M')}, "
            f"InWindow: {in_uv_window}, DailyExposure: {daily_exposure}min/{max_daily_minutes}min"
        )
        
        # Determine if we should be ON or OFF
        if in_uv_window and not exposure_limit_reached:
            if not self.is_uv_active:
                await self._activate_uv('schedule')
            else:
                # Track exposure time
                self.daily_exposure_minutes = daily_exposure + 1
        else:
            if self.is_uv_active:
                reason = "Exposure limit reached" if exposure_limit_reached else "Outside UV window"
                await self._deactivate_uv(reason)

    async def _activate_uv(self, phase: str):
        """Activate UV light with ramp-up."""
        if self.is_uv_active and self.current_phase == phase:
            return
        
        self.is_uv_active = True
        self.current_phase = phase
        
        # Create descriptive message based on phase
        if phase == 'always_on':
            message = f"UV light activated (Always On mode, intensity: {self.intensity_percent}%)"
        else:
            message = f"UV light activated (intensity: {self.intensity_percent}%)"
        
        _LOGGER.info(f"{self.deviceName}: {message}")
        
        # Ramp up to target intensity over transition time (default 60 seconds)
        if self.isDimmable:
            transition_seconds = getattr(self, 'transition_seconds', 60)
            await self._ramp_to_intensity(self.intensity_percent, transition_seconds)
        else:
            # Non-dimmable: just turn on
            await self.turn_on()
    
    async def _ramp_to_intensity(self, target_percent: int, duration_seconds: int):
        """Ramp UV light to target intensity over specified duration."""
        # UV starts at 20% (initVoltage) regardless of current voltage
        start_voltage = getattr(self, 'initVoltage', 20)
        target_voltage = target_percent
        
        if start_voltage == target_voltage:
            _LOGGER.debug(f"{self.deviceName}: Already at target intensity {target_percent}%, skipping ramp")
            await self.turn_on()
            return
        
        steps = 10
        step_duration = duration_seconds / steps
        voltage_step = (target_voltage - start_voltage) / steps
        
        _LOGGER.info(
            f"{self.deviceName}: Ramping UV from {start_voltage}% to {target_voltage}% "
            f"over {duration_seconds}s ({steps} steps, {step_duration:.1f}s per step)"
        )
        
        for i in range(1, steps + 1):
            next_voltage = round(start_voltage + (voltage_step * i), 1)
            _LOGGER.debug(f"{self.deviceName}: UV ramp step {i}/{steps}: {next_voltage}%")
            await self.turn_on(brightness_pct=next_voltage)
            await asyncio.sleep(step_duration)
        
        _LOGGER.info(f"{self.deviceName}: UV ramp complete at {target_percent}%")
    
    async def _deactivate_uv(self, reason: str = ""):
        """Deactivate UV light."""
        if not self.is_uv_active:
            return
            
        previous_phase = self.current_phase
        self.is_uv_active = False
        self.current_phase = None
        
        _LOGGER.info(f"{self.deviceName}: Deactivating UV light ({reason})")
        
        # Create action log
        message = f"UV light deactivated: {reason}" if reason else "UV light deactivated"
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type="LightUV",
            Action="OFF",
            Message=message,
            Voltage=0,
            Dimmable=False,
            SunRise=False,
            SunSet=False,
        )
        await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
        
        # Turn off the light
        await self.turn_off()

    async def _on_light_time_change(self, data):
        """Handle main light schedule changes - reload settings and restart scheduler."""
        _LOGGER.info(f"{self.deviceName}: Light schedule changed, reloading settings")
        
        # CRITICAL: Stop existing scheduler before reloading
        if self._schedule_task and not self._schedule_task.done():
            _LOGGER.info(f"{self.deviceName}: Stopping scheduler for time change reload")
            self._schedule_task.cancel()
            self._schedule_task = None
        
        # Reload settings - this will restart scheduler if needed
        self._load_settings()

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

        # Store the main light state for UV scheduling
        self.islightON = target_state

        # If this device is not targeted, ignore the event completely
        if not is_targeted:
            _LOGGER.debug(f"{self.deviceName}: Not targeted by toggleLight event, ignoring")
            return

        # CRITICAL: Only respond to ToggleLight if mode is ALWAYS_ON
        # If mode is SCHEDULE, our scheduler handles timing - ignore ToggleLight
        # If mode is ALWAYS_OFF or MANUAL, never respond to ToggleLight
        if self.mode == UVMode.SCHEDULE:
            _LOGGER.debug(f"{self.deviceName}: Ignoring toggleLight in Schedule mode (using dedicated scheduling)")
            return

        if self.mode == UVMode.ALWAYS_OFF:
            _LOGGER.debug(f"{self.deviceName}: Ignoring toggleLight in Always Off mode")
            return

        if self.mode == UVMode.MANUAL:
            _LOGGER.debug(f"{self.deviceName}: Ignoring toggleLight in Manual mode")
            return

        # Mode is ALWAYS_ON - respond to main light toggle
        if self.mode == UVMode.ALWAYS_ON:
            if not target_state:
                # Main lights going off - deactivate UV
                if self.is_uv_active:
                    await self._deactivate_uv("Main lights off (Always On mode)")
            else:
                # Main lights coming on - activate UV
                if not self.is_uv_active:
                    await self._activate_uv('always_on')

        _LOGGER.debug(f"{self.deviceName}: Main light toggled to {target_state} (mode={self.mode})")

    async def _on_settings_update(self, data):
        """Handle UV settings updates from UI."""
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
                    if self.mode == UVMode.ALWAYS_OFF:
                        # Switching to Always Off - deactivate immediately
                        if self.is_uv_active:
                            await self._deactivate_uv("Mode changed to Always Off")
                    elif self.mode == UVMode.ALWAYS_ON and self.islightON:
                        # Switching to Always On while lights are on - activate
                        if not self.is_uv_active:
                            await self._activate_uv('always_on')
                    elif self.mode == UVMode.SCHEDULE:
                        # Switching to Schedule - check window immediately
                        await self._check_schedule_window()
            
            # Update enabled state
            if "enabled" in data:
                enabled = data["enabled"]
                if not enabled:
                    # Disabled - deactivate if active
                    if self.is_uv_active:
                        await self._deactivate_uv("Disabled")
            
            # Update timing settings
            if "delayAfterStartMinutes" in data:
                self.delay_after_start_minutes = data["delayAfterStartMinutes"]
                settings_changed = True
            if "stopBeforeEndMinutes" in data:
                self.stop_before_end_minutes = data["stopBeforeEndMinutes"]
                settings_changed = True
            if "maxDurationHours" in data:
                self.max_duration_hours = data["maxDurationHours"]
                settings_changed = True
            if "intensity" in data:
                self.intensity_percent = data["intensity"]
                settings_changed = True

            # Update midday scheduling settings
            if "middayStartTime" in data:
                self.midday_start_time = data["middayStartTime"]
                settings_changed = True
            if "middayEndTime" in data:
                self.midday_end_time = data["middayEndTime"]
                settings_changed = True
                
            if settings_changed:
                _LOGGER.info(
                    f"{self.deviceName}: Settings updated - "
                    f"Mode: {self.mode}, "
                    f"Delay: {self.delay_after_start_minutes}min, StopBefore: {self.stop_before_end_minutes}min, "
                    f"MaxDuration: {self.max_duration_hours}h, Intensity: {self.intensity_percent}%"
                )

    def get_status(self) -> dict:
        """Get current UV light status."""
        return {
            "device_name": self.deviceName,
            "device_type": "LightUV",
            "mode": self.mode,
            "is_active": self.is_uv_active,
            "current_phase": self.current_phase,
            "is_running": self.isRunning,
            "daily_exposure_minutes": self.daily_exposure_minutes,
            "max_duration_hours": self.max_duration_hours,
            "delay_after_start_minutes": self.delay_after_start_minutes,
            "stop_before_end_minutes": self.stop_before_end_minutes,
            "intensity_percent": self.intensity_percent,
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

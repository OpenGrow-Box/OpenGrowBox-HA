"""
OpenGrowBox UV Light Device

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

from .Device import Device
from ..data.OGBDataClasses.OGBPublications import OGBLightAction

_LOGGER = logging.getLogger(__name__)


# Valid modes for UV light control
class UVMode:
    SCHEDULE = "Schedule"      # Mid-day timing window
    ALWAYS_ON = "Always On"    # ON when main lights are ON
    ALWAYS_OFF = "Always Off"  # Never on automatically
    MANUAL = "Manual"          # Only manual control


class LightUV(Device):
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
        
        # Initialize
        self._load_settings()
        self._start_scheduler()
        
        # Register event handlers
        self.event_manager.on("LightTimeChanges", self._on_light_time_change)
        self.event_manager.on("toggleLight", self._on_main_light_toggle)
        self.event_manager.on("UVSettingsUpdate", self._on_settings_update)

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
            self.mode = uv_settings.get("mode", UVMode.SCHEDULE)
            self.delay_after_start_minutes = uv_settings.get("delayAfterStartMinutes", 120)
            self.stop_before_end_minutes = uv_settings.get("stopBeforeEndMinutes", 120)
            self.max_duration_hours = uv_settings.get("maxDurationHours", 6)
            self.intensity_percent = uv_settings.get("intensity", 100)
            
            _LOGGER.info(
                f"{self.deviceName}: UV settings loaded - "
                f"Mode: {self.mode}, "
                f"Delay: {self.delay_after_start_minutes}min, StopBefore: {self.stop_before_end_minutes}min, "
                f"MaxDuration: {self.max_duration_hours}h, Intensity: {self.intensity_percent}%"
            )
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error loading settings: {e}")

    def _start_scheduler(self):
        """Start the periodic scheduler for UV timing."""
        if self._schedule_task and not self._schedule_task.done():
            return
            
        self._schedule_task = asyncio.create_task(self._schedule_loop())
        _LOGGER.info(f"{self.deviceName}: UV scheduler started")

    async def _schedule_loop(self):
        """Main scheduling loop - checks every minute for activation conditions."""
        while True:
            try:
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
        
        # Mode: Always On - ON whenever main lights are ON
        if self.mode == UVMode.ALWAYS_ON:
            if self.islightON:
                if not self.is_uv_active:
                    await self._activate_uv('always_on')
            else:
                if self.is_uv_active:
                    await self._deactivate_uv("Main lights off")
            return
        
        # Mode: Schedule - original mid-day window logic
        if self.mode == UVMode.SCHEDULE:
            await self._check_schedule_window()

    async def _check_schedule_window(self):
        """Check if we're in a UV activation window (Schedule mode)."""
        if not self.lightOnTime or not self.lightOffTime:
            return
            
        if not self.islightON:
            if self.is_uv_active:
                await self._deactivate_uv("Main lights off")
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
        
        # Calculate UV window
        uv_start = light_on_dt + timedelta(minutes=self.delay_after_start_minutes)
        uv_end = light_off_dt - timedelta(minutes=self.stop_before_end_minutes)
        
        # Check max duration limit
        max_duration_dt = timedelta(hours=self.max_duration_hours)
        if (uv_end - uv_start) > max_duration_dt:
            # Center the UV window
            total_light_duration = light_off_dt - light_on_dt
            center = light_on_dt + (total_light_duration / 2)
            uv_start = center - (max_duration_dt / 2)
            uv_end = center + (max_duration_dt / 2)
        
        in_uv_window = uv_start <= now <= uv_end
        
        # Check if we've hit daily exposure limit
        max_daily_minutes = self.max_duration_hours * 60
        exposure_limit_reached = self.daily_exposure_minutes >= max_daily_minutes
        
        _LOGGER.debug(
            f"{self.deviceName}: UV check - Now: {now.strftime('%H:%M')}, "
            f"Window: {uv_start.strftime('%H:%M')}-{uv_end.strftime('%H:%M')}, "
            f"InWindow: {in_uv_window}, DailyExposure: {self.daily_exposure_minutes}min, "
            f"LimitReached: {exposure_limit_reached}"
        )
        
        # Determine if we should be ON or OFF
        if in_uv_window and not exposure_limit_reached:
            if not self.is_uv_active:
                await self._activate_uv('schedule')
            else:
                # Track exposure time
                self.daily_exposure_minutes += 1
        else:
            if self.is_uv_active:
                reason = "Exposure limit reached" if exposure_limit_reached else "Outside UV window"
                await self._deactivate_uv(reason)

    async def _activate_uv(self, phase: str):
        """Activate UV light."""
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
        
        # Create action log
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type="LightUV",
            Action="ON",
            Message=message,
            Voltage=self.intensity_percent,
            Dimmable=self.isDimmable,
            SunRise=False,
            SunSet=False,
        )
        await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
        
        # Turn on the light
        if self.isDimmable:
            await self.turn_on(brightness_pct=self.intensity_percent)
        else:
            await self.turn_on()

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
        """Handle main light schedule changes."""
        _LOGGER.info(f"{self.deviceName}: Light schedule changed, reloading settings")
        self._load_settings()

    async def _on_main_light_toggle(self, lightState):
        """Handle main light toggle events."""
        self.islightON = lightState
        
        if not lightState:
            # Main lights going off - deactivate if active
            if self.is_uv_active:
                await self._deactivate_uv("Main lights off")
        elif self.mode == UVMode.ALWAYS_ON:
            # Main lights coming on and we're in Always On mode - activate
            if not self.is_uv_active:
                await self._activate_uv('always_on')
        
        _LOGGER.debug(f"{self.deviceName}: Main light toggled to {lightState}")

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

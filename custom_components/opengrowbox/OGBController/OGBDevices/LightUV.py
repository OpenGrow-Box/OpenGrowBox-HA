"""
OpenGrowBox UV Light Device

UV lights (UVA/UVB) are used for:
- Stress response triggering (trichome/resin production)
- Pathogen control
- Compact growth

Timing behavior:
- OFF at start of light cycle (plants need to "wake up")
- ON during middle portion of light cycle
- OFF before end of light cycle (recovery time)

Default: Active for middle 4-6 hours of an 12-hour light cycle
"""

import asyncio
import logging
from datetime import datetime, timedelta

from .Device import Device
from ..data.OGBDataClasses.OGBPublications import OGBLightAction

_LOGGER = logging.getLogger(__name__)


class LightUV(Device):
    """UV light device with mid-day timing."""

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

        # UV specific settings
        self.delay_after_start_minutes = 120  # Wait 2 hours after lights on
        self.stop_before_end_minutes = 120    # Stop 2 hours before lights off
        self.max_duration_hours = 6           # Maximum UV exposure per day
        self.intensity_percent = 100          # UV intensity (if dimmable)
        
        # State tracking
        self.is_uv_active = False
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
            self.delay_after_start_minutes = uv_settings.get("delayAfterStart", 120)
            self.stop_before_end_minutes = uv_settings.get("stopBeforeEnd", 120)
            self.max_duration_hours = uv_settings.get("maxDuration", 6)
            self.intensity_percent = uv_settings.get("intensity", 100)
            
            _LOGGER.info(
                f"{self.deviceName}: UV settings loaded - "
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
        """Main scheduling loop - checks every minute for activation windows."""
        while True:
            try:
                await self._check_activation_window()
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

    async def _check_activation_window(self):
        """Check if we're in a UV activation window."""
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
                await self._activate_uv()
            else:
                # Track exposure time
                self.daily_exposure_minutes += 1
        else:
            if self.is_uv_active:
                reason = "Exposure limit reached" if exposure_limit_reached else "Outside UV window"
                await self._deactivate_uv(reason)

    async def _activate_uv(self):
        """Activate UV light."""
        if self.is_uv_active:
            return
            
        self.is_uv_active = True
        
        _LOGGER.info(f"{self.deviceName}: Activating UV light")
        
        # Create action log
        message = f"UV light activated (intensity: {self.intensity_percent}%)"
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
            
        self.is_uv_active = False
        
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
        
        if not lightState and self.is_uv_active:
            await self._deactivate_uv("Main lights off")
        
        _LOGGER.debug(f"{self.deviceName}: Main light toggled to {lightState}")

    async def _on_settings_update(self, data):
        """Handle UV settings updates."""
        if data.get("device") == self.deviceName or data.get("device") is None:
            if "delayAfterStart" in data:
                self.delay_after_start_minutes = data["delayAfterStart"]
            if "stopBeforeEnd" in data:
                self.stop_before_end_minutes = data["stopBeforeEnd"]
            if "maxDuration" in data:
                self.max_duration_hours = data["maxDuration"]
            if "intensity" in data:
                self.intensity_percent = data["intensity"]
                
            _LOGGER.info(
                f"{self.deviceName}: Settings updated - "
                f"Delay: {self.delay_after_start_minutes}min, StopBefore: {self.stop_before_end_minutes}min, "
                f"MaxDuration: {self.max_duration_hours}h"
            )

    def get_status(self) -> dict:
        """Get current UV light status."""
        return {
            "device_name": self.deviceName,
            "device_type": "LightUV",
            "is_active": self.is_uv_active,
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

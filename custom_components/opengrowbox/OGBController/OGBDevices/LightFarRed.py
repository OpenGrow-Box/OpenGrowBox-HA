"""
OpenGrowBox Far Red Light Device

Far Red lights are used for:
- Initiating the Emerson effect at start of light cycle
- Accelerating phytochrome conversion (Pfr -> Pr) at end of light cycle
- Reducing stretch and promoting flowering

Timing behavior:
- ON for configurable duration at START of light cycle (default: 15 min)
- ON for configurable duration at END of light cycle (default: 15 min)
- OFF during the main light period
"""

import asyncio
import logging
from datetime import datetime, timedelta

from .Device import Device
from ..data.OGBDataClasses.OGBPublications import OGBLightAction

_LOGGER = logging.getLogger(__name__)


class LightFarRed(Device):
    """Far Red light device with start/end of day timing."""

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

        # Far Red specific settings
        self.start_duration_minutes = 15  # Duration at start of light cycle
        self.end_duration_minutes = 15    # Duration at end of light cycle
        
        # State tracking
        self.is_fr_active = False
        self.current_phase = None  # 'start', 'end', or None
        
        # Light schedule reference
        self.lightOnTime = None
        self.lightOffTime = None
        self.islightON = None
        
        # Task tracking
        self._schedule_task = None
        self._turn_off_task = None
        
        # Initialize
        self._load_settings()
        self._start_scheduler()
        
        # Register event handlers
        self.event_manager.on("LightTimeChanges", self._on_light_time_change)
        self.event_manager.on("toggleLight", self._on_main_light_toggle)
        self.event_manager.on("FarRedSettingsUpdate", self._on_settings_update)

    def __repr__(self):
        return (
            f"LightFarRed('{self.deviceName}' in {self.inRoom}) "
            f"StartDuration:{self.start_duration_minutes}min EndDuration:{self.end_duration_minutes}min "
            f"Active:{self.is_fr_active} Phase:{self.current_phase} Running:{self.isRunning}"
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
            self.start_duration_minutes = fr_settings.get("startDuration", 15)
            self.end_duration_minutes = fr_settings.get("endDuration", 15)
            
            _LOGGER.info(
                f"{self.deviceName}: FarRed settings loaded - "
                f"Start: {self.start_duration_minutes}min, End: {self.end_duration_minutes}min, "
                f"LightOn: {self.lightOnTime}, LightOff: {self.lightOffTime}"
            )
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error loading settings: {e}")

    def _start_scheduler(self):
        """Start the periodic scheduler for Far Red timing."""
        if self._schedule_task and not self._schedule_task.done():
            return
            
        self._schedule_task = asyncio.create_task(self._schedule_loop())
        _LOGGER.info(f"{self.deviceName}: Far Red scheduler started")

    async def _schedule_loop(self):
        """Main scheduling loop - checks every minute for activation windows."""
        while True:
            try:
                await self._check_activation_windows()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Schedule loop error: {e}")
                import traceback
                _LOGGER.error(traceback.format_exc())
            
            await asyncio.sleep(30)  # Check every 30 seconds for responsiveness

    async def _check_activation_windows(self):
        """Check if we're in a Far Red activation window."""
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
        
        # Start window: lightOnTime to lightOnTime + start_duration
        start_window_begin = light_on_dt
        start_window_end = light_on_dt + timedelta(minutes=self.start_duration_minutes)
        
        # End window: lightOffTime - end_duration to lightOffTime
        end_window_begin = light_off_dt - timedelta(minutes=self.end_duration_minutes)
        end_window_end = light_off_dt
        
        in_start_window = start_window_begin <= now <= start_window_end
        in_end_window = end_window_begin <= now <= end_window_end
        
        _LOGGER.debug(
            f"{self.deviceName}: Time check - Now: {now.strftime('%H:%M:%S')}, "
            f"StartWindow: {start_window_begin.strftime('%H:%M')}-{start_window_end.strftime('%H:%M')}, "
            f"EndWindow: {end_window_begin.strftime('%H:%M')}-{end_window_end.strftime('%H:%M')}, "
            f"InStart: {in_start_window}, InEnd: {in_end_window}"
        )
        
        # Determine if we should be ON or OFF
        if in_start_window and self.islightON:
            if not self.is_fr_active or self.current_phase != 'start':
                await self._activate_far_red('start')
        elif in_end_window and self.islightON:
            if not self.is_fr_active or self.current_phase != 'end':
                await self._activate_far_red('end')
        else:
            if self.is_fr_active:
                await self._deactivate_far_red()

    async def _activate_far_red(self, phase: str):
        """Activate Far Red light for the specified phase."""
        if self.is_fr_active and self.current_phase == phase:
            return  # Already active in this phase
            
        self.current_phase = phase
        self.is_fr_active = True
        
        _LOGGER.info(f"{self.deviceName}: Activating Far Red for '{phase}' phase")
        
        # Create action log
        message = f"FarRed {phase.upper()} phase activated"
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type="LightFarRed",
            Action="ON",
            Message=message,
            Voltage=100,  # Far Red is typically on/off, not dimmable
            Dimmable=False,
            SunRise=False,
            SunSet=False,
        )
        await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
        
        # Turn on the light
        await self.turn_on()

    async def _deactivate_far_red(self):
        """Deactivate Far Red light."""
        if not self.is_fr_active:
            return
            
        previous_phase = self.current_phase
        self.is_fr_active = False
        self.current_phase = None
        
        _LOGGER.info(f"{self.deviceName}: Deactivating Far Red (was in '{previous_phase}' phase)")
        
        # Create action log
        message = f"FarRed {previous_phase.upper() if previous_phase else ''} phase ended"
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
            # Main lights going off - if we're active, deactivate
            if self.is_fr_active:
                await self._deactivate_far_red()
        
        _LOGGER.debug(f"{self.deviceName}: Main light toggled to {lightState}")

    async def _on_settings_update(self, data):
        """Handle Far Red settings updates."""
        if data.get("device") == self.deviceName or data.get("device") is None:
            if "startDuration" in data:
                self.start_duration_minutes = data["startDuration"]
            if "endDuration" in data:
                self.end_duration_minutes = data["endDuration"]
                
            _LOGGER.info(
                f"{self.deviceName}: Settings updated - "
                f"Start: {self.start_duration_minutes}min, End: {self.end_duration_minutes}min"
            )

    def get_status(self) -> dict:
        """Get current Far Red light status."""
        return {
            "device_name": self.deviceName,
            "device_type": "LightFarRed",
            "is_active": self.is_fr_active,
            "current_phase": self.current_phase,
            "is_running": self.isRunning,
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
            except asyncio.CancelledError:
                pass

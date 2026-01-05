"""
OpenGrowBox Far Red Light Device

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

from .Device import Device
from ..data.OGBDataClasses.OGBPublications import OGBLightAction

_LOGGER = logging.getLogger(__name__)


# Valid modes for Far Red light control
class FarRedMode:
    SCHEDULE = "Schedule"      # Start/End timing windows
    ALWAYS_ON = "Always On"    # ON when main lights are ON
    ALWAYS_OFF = "Always Off"  # Never on automatically
    MANUAL = "Manual"          # Only manual control


class LightFarRed(Device):
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
        
        # State tracking
        self.is_fr_active = False
        self.current_phase: Optional[str] = None  # 'start', 'end', 'always_on', or None
        
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
        
        # Validate entity availability
        self._validate_entity_availability()
        
        # Register event handlers
        self.event_manager.on("LightTimeChanges", self._on_light_time_change)
        self.event_manager.on("toggleLight", self._on_main_light_toggle)
        self.event_manager.on("FarRedSettingsUpdate", self._on_settings_update)

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
            self.mode = fr_settings.get("mode", FarRedMode.SCHEDULE)
            self.start_duration_minutes = fr_settings.get("startDurationMinutes", 15)
            self.end_duration_minutes = fr_settings.get("endDurationMinutes", 15)
            self.intensity = fr_settings.get("intensity", 100)
            
            _LOGGER.info(
                f"{self.deviceName}: FarRed settings loaded - "
                f"Mode: {self.mode}, "
                f"Start: {self.start_duration_minutes}min, End: {self.end_duration_minutes}min, "
                f"Intensity: {self.intensity}%, "
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
        """Main scheduling loop - checks every 30 seconds for activation conditions."""
        while True:
            try:
                await self._check_activation_conditions()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Schedule loop error: {e}")
                import traceback
                _LOGGER.error(traceback.format_exc())
            
            await asyncio.sleep(30)  # Check every 30 seconds for responsiveness

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
        """Check if we're in a Far Red activation window (Schedule mode)."""
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
        
        # Turn on the light
        await self.turn_on()

    async def _deactivate_far_red(self):
        """Deactivate Far Red light."""
        if not self.is_fr_active:
            return
            
        previous_phase = self.current_phase
        self.is_fr_active = False
        self.current_phase = None
        
        # Create descriptive message based on previous phase
        if previous_phase == 'always_on':
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
        elif self.mode == FarRedMode.ALWAYS_ON:
            # Main lights coming on and we're in Always On mode - activate
            if not self.is_fr_active:
                await self._activate_far_red('always_on')
        
        _LOGGER.debug(f"{self.deviceName}: Main light toggled to {lightState}")

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
            except asyncio.CancelledError:
                pass

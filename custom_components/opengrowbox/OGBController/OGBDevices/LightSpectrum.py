"""
OpenGrowBox Spectrum Light Device (Blue/Red channels)

Spectrum lights allow control of individual color channels:
- Blue light: Promotes vegetative growth, compact plants
- Red light: Promotes flowering, stretching

Timing behavior based on plant stage and time of day:
- Morning: Higher blue ratio (wake up, photosynthesis)
- Midday: Balanced spectrum
- Evening: Higher red ratio (prepare for night, flowering signal)

This class handles both LightBlue and LightRed device types.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from .Device import Device
from ..data.OGBDataClasses.OGBPublications import OGBLightAction

_LOGGER = logging.getLogger(__name__)


class LightSpectrum(Device):
    """Spectrum light device (Blue or Red channel) with time-based intensity profiles."""

    # Spectrum type constants
    SPECTRUM_BLUE = "blue"
    SPECTRUM_RED = "red"

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

        # Determine spectrum type from device type
        self.spectrum_type = self._determine_spectrum_type(deviceType, deviceLabel)
        
        # Intensity profiles (percent) - morning/midday/evening
        # These define the intensity at different parts of the light cycle
        if self.spectrum_type == self.SPECTRUM_BLUE:
            # Blue: High in morning, medium midday, low evening
            self.morning_intensity = 80
            self.midday_intensity = 60
            self.evening_intensity = 30
        else:  # Red
            # Red: Low in morning, medium midday, high evening
            self.morning_intensity = 30
            self.midday_intensity = 60
            self.evening_intensity = 80
        
        # Current state
        self.current_intensity = 0
        self.is_spectrum_active = False
        self.current_phase = None  # 'morning', 'midday', 'evening'
        
        # Light schedule reference
        self.lightOnTime = None
        self.lightOffTime = None
        self.islightON = None
        
        # Task tracking
        self._schedule_task = None
        
        # Phase transition settings (percentage of light period)
        self.morning_phase_percent = 25   # First 25% of light period
        self.midday_phase_percent = 50    # Middle 50% of light period
        self.evening_phase_percent = 25   # Last 25% of light period
        
        # Transition smoothing
        self.smooth_transitions = True
        self.transition_steps = 10
        
        # Initialize
        self._load_settings()
        self._start_scheduler()
        
        # Register event handlers
        self.event_manager.on("LightTimeChanges", self._on_light_time_change)
        self.event_manager.on("toggleLight", self._on_main_light_toggle)
        self.event_manager.on("PlantStageChange", self._on_plant_stage_change)
        self.event_manager.on("SpectrumSettingsUpdate", self._on_settings_update)

    def _determine_spectrum_type(self, device_type: str, device_label: str) -> str:
        """Determine if this is a blue or red spectrum light."""
        type_lower = device_type.lower()
        label_lower = device_label.lower()
        
        if "blue" in type_lower or "blue" in label_lower:
            return self.SPECTRUM_BLUE
        elif "red" in type_lower or "red" in label_lower:
            return self.SPECTRUM_RED
        else:
            # Default to blue if unclear
            _LOGGER.warning(
                f"{self.deviceName}: Could not determine spectrum type from "
                f"type='{device_type}', label='{device_label}'. Defaulting to blue."
            )
            return self.SPECTRUM_BLUE

    def __repr__(self):
        return (
            f"LightSpectrum[{self.spectrum_type.upper()}]('{self.deviceName}' in {self.inRoom}) "
            f"Morning:{self.morning_intensity}% Midday:{self.midday_intensity}% Evening:{self.evening_intensity}% "
            f"CurrentPhase:{self.current_phase} CurrentIntensity:{self.current_intensity}% "
            f"Active:{self.is_spectrum_active} Running:{self.isRunning}"
        )

    def _load_settings(self):
        """Load spectrum settings from datastore."""
        try:
            # Get main light times
            light_on_str = self.data_store.getDeep("isPlantDay.lightOnTime")
            light_off_str = self.data_store.getDeep("isPlantDay.lightOffTime")
            
            if light_on_str:
                self.lightOnTime = datetime.strptime(light_on_str, "%H:%M:%S").time()
            if light_off_str:
                self.lightOffTime = datetime.strptime(light_off_str, "%H:%M:%S").time()
                
            self.islightON = self.data_store.getDeep("isPlantDay.islightON")
            
            # Get spectrum-specific settings
            spectrum_settings = self.data_store.getDeep(f"specialLights.spectrum.{self.spectrum_type}") or {}
            
            self.morning_intensity = spectrum_settings.get("morningIntensity", self.morning_intensity)
            self.midday_intensity = spectrum_settings.get("middayIntensity", self.midday_intensity)
            self.evening_intensity = spectrum_settings.get("eveningIntensity", self.evening_intensity)
            self.smooth_transitions = spectrum_settings.get("smoothTransitions", True)
            
            # Adjust based on plant stage
            self._adjust_for_plant_stage()
            
            _LOGGER.info(
                f"{self.deviceName}: {self.spectrum_type.upper()} spectrum settings loaded - "
                f"Morning: {self.morning_intensity}%, Midday: {self.midday_intensity}%, "
                f"Evening: {self.evening_intensity}%"
            )
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error loading settings: {e}")

    def _adjust_for_plant_stage(self):
        """Adjust intensity profiles based on current plant stage."""
        plant_stage = self.data_store.get("plantStage") or ""
        plant_stage_lower = plant_stage.lower()
        
        # Veg stages favor blue, flower stages favor red
        if "veg" in plant_stage_lower:
            if self.spectrum_type == self.SPECTRUM_BLUE:
                # Boost blue during veg
                self.morning_intensity = min(100, self.morning_intensity + 10)
                self.midday_intensity = min(100, self.midday_intensity + 10)
            else:
                # Reduce red during veg
                self.evening_intensity = max(20, self.evening_intensity - 10)
                
        elif "flower" in plant_stage_lower:
            if self.spectrum_type == self.SPECTRUM_RED:
                # Boost red during flower
                self.midday_intensity = min(100, self.midday_intensity + 10)
                self.evening_intensity = min(100, self.evening_intensity + 10)
            else:
                # Reduce blue during flower
                self.morning_intensity = max(20, self.morning_intensity - 10)

    def _start_scheduler(self):
        """Start the periodic scheduler for spectrum timing."""
        if self._schedule_task and not self._schedule_task.done():
            return
            
        self._schedule_task = asyncio.create_task(self._schedule_loop())
        _LOGGER.info(f"{self.deviceName}: Spectrum scheduler started")

    async def _schedule_loop(self):
        """Main scheduling loop - checks and adjusts intensity."""
        while True:
            try:
                await self._update_intensity()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Schedule loop error: {e}")
                import traceback
                _LOGGER.error(traceback.format_exc())
            
            # Check more frequently for smoother transitions
            await asyncio.sleep(60 if self.smooth_transitions else 300)

    async def _update_intensity(self):
        """Update spectrum intensity based on current time within light cycle."""
        if not self.lightOnTime or not self.lightOffTime:
            return
            
        if not self.islightON:
            if self.is_spectrum_active:
                await self._deactivate_spectrum("Main lights off")
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
        
        # Check if we're in the light period
        if not (light_on_dt <= now <= light_off_dt):
            if self.is_spectrum_active:
                await self._deactivate_spectrum("Outside light period")
            return
        
        # Calculate position in light cycle (0.0 to 1.0)
        total_duration = (light_off_dt - light_on_dt).total_seconds()
        elapsed = (now - light_on_dt).total_seconds()
        cycle_position = elapsed / total_duration
        
        # Determine phase and target intensity
        target_intensity, phase = self._calculate_target_intensity(cycle_position)
        
        # Update if changed
        if not self.is_spectrum_active:
            await self._activate_spectrum(target_intensity, phase)
        elif self.current_intensity != target_intensity or self.current_phase != phase:
            await self._adjust_intensity(target_intensity, phase)

    def _calculate_target_intensity(self, cycle_position: float) -> tuple[int, str]:
        """
        Calculate target intensity based on position in light cycle.
        
        Args:
            cycle_position: Position in light cycle (0.0 = start, 1.0 = end)
            
        Returns:
            Tuple of (intensity_percent, phase_name)
        """
        morning_end = self.morning_phase_percent / 100
        evening_start = 1.0 - (self.evening_phase_percent / 100)
        
        if cycle_position < morning_end:
            # Morning phase - interpolate from 0 to morning intensity
            if self.smooth_transitions:
                progress = cycle_position / morning_end
                intensity = int(self.morning_intensity * progress)
            else:
                intensity = self.morning_intensity
            return intensity, "morning"
            
        elif cycle_position > evening_start:
            # Evening phase - interpolate from midday to evening intensity
            if self.smooth_transitions:
                progress = (cycle_position - evening_start) / (1.0 - evening_start)
                intensity = int(self.midday_intensity + (self.evening_intensity - self.midday_intensity) * progress)
            else:
                intensity = self.evening_intensity
            return intensity, "evening"
            
        else:
            # Midday phase
            if self.smooth_transitions:
                # Smooth transition from morning to midday
                midday_start = morning_end
                midday_end = evening_start
                midday_middle = (midday_start + midday_end) / 2
                
                if cycle_position < midday_middle:
                    # Transition from morning to midday
                    progress = (cycle_position - midday_start) / (midday_middle - midday_start)
                    intensity = int(self.morning_intensity + (self.midday_intensity - self.morning_intensity) * progress)
                else:
                    # Hold at midday intensity
                    intensity = self.midday_intensity
            else:
                intensity = self.midday_intensity
            return intensity, "midday"

    async def _activate_spectrum(self, intensity: int, phase: str):
        """Activate spectrum light at given intensity."""
        self.is_spectrum_active = True
        self.current_intensity = intensity
        self.current_phase = phase
        
        _LOGGER.info(
            f"{self.deviceName}: Activating {self.spectrum_type.upper()} spectrum - "
            f"Phase: {phase}, Intensity: {intensity}%"
        )
        
        # Create action log
        message = f"{self.spectrum_type.upper()} spectrum activated ({phase} phase, {intensity}%)"
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type=f"Light{self.spectrum_type.capitalize()}",
            Action="ON",
            Message=message,
            Voltage=intensity,
            Dimmable=True,
            SunRise=False,
            SunSet=False,
        )
        await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
        
        # Turn on the light
        if self.isDimmable:
            await self.turn_on(brightness_pct=intensity)
        else:
            await self.turn_on()

    async def _adjust_intensity(self, intensity: int, phase: str):
        """Adjust spectrum intensity."""
        old_intensity = self.current_intensity
        self.current_intensity = intensity
        self.current_phase = phase
        
        _LOGGER.debug(
            f"{self.deviceName}: Adjusting {self.spectrum_type.upper()} - "
            f"{old_intensity}% -> {intensity}% ({phase} phase)"
        )
        
        # Apply new intensity
        if self.isDimmable:
            await self.turn_on(brightness_pct=intensity)

    async def _deactivate_spectrum(self, reason: str = ""):
        """Deactivate spectrum light."""
        if not self.is_spectrum_active:
            return
            
        self.is_spectrum_active = False
        self.current_intensity = 0
        self.current_phase = None
        
        _LOGGER.info(f"{self.deviceName}: Deactivating {self.spectrum_type.upper()} spectrum ({reason})")
        
        # Create action log
        message = f"{self.spectrum_type.upper()} spectrum deactivated: {reason}" if reason else f"{self.spectrum_type.upper()} spectrum deactivated"
        lightAction = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type=f"Light{self.spectrum_type.capitalize()}",
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
        
        if not lightState and self.is_spectrum_active:
            await self._deactivate_spectrum("Main lights off")
        
        _LOGGER.debug(f"{self.deviceName}: Main light toggled to {lightState}")

    async def _on_plant_stage_change(self, data):
        """Handle plant stage changes - adjust spectrum profile."""
        _LOGGER.info(f"{self.deviceName}: Plant stage changed, adjusting spectrum profile")
        self._load_settings()

    async def _on_settings_update(self, data):
        """Handle spectrum settings updates."""
        if data.get("spectrum") != self.spectrum_type and data.get("device") != self.deviceName:
            return
            
        if "morningIntensity" in data:
            self.morning_intensity = data["morningIntensity"]
        if "middayIntensity" in data:
            self.midday_intensity = data["middayIntensity"]
        if "eveningIntensity" in data:
            self.evening_intensity = data["eveningIntensity"]
        if "smoothTransitions" in data:
            self.smooth_transitions = data["smoothTransitions"]
                
        _LOGGER.info(
            f"{self.deviceName}: Settings updated - "
            f"Morning: {self.morning_intensity}%, Midday: {self.midday_intensity}%, "
            f"Evening: {self.evening_intensity}%"
        )

    def get_status(self) -> dict:
        """Get current spectrum light status."""
        return {
            "device_name": self.deviceName,
            "device_type": f"Light{self.spectrum_type.capitalize()}",
            "spectrum_type": self.spectrum_type,
            "is_active": self.is_spectrum_active,
            "is_running": self.isRunning,
            "current_intensity": self.current_intensity,
            "current_phase": self.current_phase,
            "morning_intensity": self.morning_intensity,
            "midday_intensity": self.midday_intensity,
            "evening_intensity": self.evening_intensity,
            "smooth_transitions": self.smooth_transitions,
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


# Convenience aliases for specific spectrum types
class LightBlue(LightSpectrum):
    """Blue spectrum light - alias for LightSpectrum with blue defaults."""
    pass


class LightRed(LightSpectrum):
    """Red spectrum light - alias for LightSpectrum with red defaults."""
    pass

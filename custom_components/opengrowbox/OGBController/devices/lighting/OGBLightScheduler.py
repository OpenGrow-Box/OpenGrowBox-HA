import asyncio
import logging
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class OGBLightScheduler:
    """Handles light scheduling, timing, and plant stage adjustments for OpenGrowBox lights."""

    def __init__(self, device_name, data_store, event_manager):
        """Initialize the light scheduler.

        Args:
            device_name: Name of the light device
            data_store: Reference to the data store
            event_manager: Reference to the event manager
        """
        self.device_name = device_name
        self.data_store = data_store
        self.event_manager = event_manager

        # Light Times
        self.light_on_time = ""
        self.light_off_time = ""
        self.sun_rise_duration = ""  # Duration of SunRise in minutes
        self.sun_set_duration = ""  # Duration of SunSet in minutes
        self.is_scheduled = False

        # Plant Phase
        self.current_plant_stage = ""

        self.plant_stage_min_max = {
            "Germination": {"min": 20, "max": 30},
            "Clones": {"min": 20, "max": 30},
            "EarlyVeg": {"min": 30, "max": 40},
            "MidVeg": {"min": 40, "max": 50},
            "LateVeg": {"min": 50, "max": 65},
            "EarlyFlower": {"min": 70, "max": 100},
            "MidFlower": {"min": 70, "max": 100},
            "LateFlower": {"min": 70, "max": 100},
        }

        self.last_day_reset = datetime.now().date()

    def initialize_scheduler(self):
        """Initialize the light scheduler."""
        self.set_light_times()

        # Register event listeners
        self.event_manager.on("SunRiseTimeUpdates", self.update_sun_rise_time)
        self.event_manager.on("SunSetTimeUpdates", self.update_sun_set_time)
        self.event_manager.on("PlantStageChange", self.set_plant_stage_light)
        self.event_manager.on("LightTimeChanges", self.change_light_times)

    def set_light_times(self):
        """Set light timing parameters from data store."""

        def parse_to_time(tstr):
            # Handle empty or None values (like original)
            if not tstr or tstr == "":
                return None
            try:
                return datetime.strptime(tstr, "%H:%M:%S").time()
            except Exception as e:
                _LOGGER.error(f"{self.device_name}: Error Parsing Time '{tstr}': {e}")
                return None

        light_on = self.data_store.getDeep("isPlantDay.lightOnTime") or ""
        light_off = self.data_store.getDeep("isPlantDay.lightOffTime") or ""
        
        self.light_on_time = parse_to_time(light_on)
        self.light_off_time = parse_to_time(light_off)

        # Sun rise/set duration - read from data store, default to 300 seconds (5 min)
        # Format is "HH:MM:SS" (e.g., "00:05:00" = 5 min, "00:15:00" = 15 min)
        def parse_to_duration(dstr):
            if not dstr or dstr == "" or dstr == "00:00:00":
                return 300  # Default 5 minutes
            try:
                # Parse as time and convert to seconds
                dt = datetime.strptime(dstr, "%H:%M:%S")
                return dt.hour * 3600 + dt.minute * 60 + dt.second
            except Exception as e:
                _LOGGER.error(f"{self.device_name}: Error Parsing Duration '{dstr}': {e}")
                return 300  # Default 5 minutes

        sun_rise_duration = self.data_store.getDeep("isPlantDay.sun_rise_duration")
        sun_set_duration = self.data_store.getDeep("isPlantDay.sun_set_duration")

        _LOGGER.debug(f"{self.device_name}: sun_rise_duration from data_store = {sun_rise_duration}")
        _LOGGER.debug(f"{self.device_name}: sun_set_duration from data_store = {sun_set_duration}")

        # Support both formats: time string "HH:MM:SS" or numeric seconds or None
        if sun_rise_duration is None:
            self.sun_rise_duration = 300  # Default 5 min
            _LOGGER.debug(f"{self.device_name}: Using default sun_rise_duration = 300")
        elif isinstance(sun_rise_duration, (int, float)):
            self.sun_rise_duration = int(sun_rise_duration)
        else:
            self.sun_rise_duration = parse_to_duration(str(sun_rise_duration))

        if sun_set_duration is None:
            self.sun_set_duration = 300  # Default 5 min
        elif isinstance(sun_set_duration, (int, float)):
            self.sun_set_duration = int(sun_set_duration)
        else:
            self.sun_set_duration = parse_to_duration(str(sun_set_duration))

        self.is_scheduled = self.data_store.getDeep("isPlantDay.islightON")

        _LOGGER.debug(
            f"{self.device_name}: LightTime-Setup "
            f"LightOn:{self.is_scheduled} Start:{self.light_on_time} Stop:{self.light_off_time} "
            f"SunRise:{self.sun_rise_duration} SunSet:{self.sun_set_duration}"
        )

    async def change_light_times(self, data):
        """Update light timing parameters."""

        def parse_to_time(tstr):
            try:
                return datetime.strptime(tstr, "%H:%M:%S").time()
            except Exception as e:
                _LOGGER.error(f"{self.device_name}: Error Parsing Time '{tstr}': {e}")
                return None

        self.light_on_time = parse_to_time(
            self.data_store.getDeep("isPlantDay.lightOnTime")
        )
        self.light_off_time = parse_to_time(
            self.data_store.getDeep("isPlantDay.lightOffTime")
        )

        # Also refresh sun durations from DataStore
        self._refresh_sun_durations()
        _LOGGER.debug(
            f"{self.device_name}: Light times updated - "
            f"LightOn:{self.light_on_time} LightOff:{self.light_off_time} "
            f"SunRise:{self.sun_rise_duration} SunSet:{self.sun_set_duration}"
        )

    async def set_plant_stage_light(self, plant_stage_data):
        """Adjust light settings based on plant stage.

        Args:
            plant_stage_data: Plant stage change data

        Returns:
            Tuple of (min_voltage, max_voltage) or None if not applicable
        """
        plant_stage = self.data_store.get("plantStage")
        self.current_plant_stage = plant_stage

        if plant_stage in self.plant_stage_min_max:
            percent_range = self.plant_stage_min_max[plant_stage]
            min_voltage = percent_range["min"]
            max_voltage = percent_range["max"]

            _LOGGER.info(
                f"{self.device_name}: Set voltage range for phase '{plant_stage}' "
                f"to {min_voltage}V–{max_voltage}V."
            )
            return min_voltage, max_voltage

        _LOGGER.error(
            f"{self.device_name}: Unknown plant phase '{plant_stage}'. Standard values will be used."
        )
        return None

    def parse_time_sec(self, time_str: str) -> int:
        """Parse a time string like '00:30:00' into seconds."""
        try:
            t = datetime.strptime(time_str, "%H:%M:%S").time()
            return t.hour * 3600 + t.minute * 60 + t.second
        except Exception as e:
            _LOGGER.error(f"{self.device_name}: Invalid time format '{time_str}': {e}")
            return 0

    def update_sun_rise_time(self, time_str):
        """Update sun rise duration from DataStore when times change."""
        self._refresh_sun_durations()
        _LOGGER.debug(f"{self.device_name}: SunRise duration refreshed to {self.sun_rise_duration}s")

    def update_sun_set_time(self, time_str):
        """Update sun set duration from DataStore when times change."""
        self._refresh_sun_durations()
        _LOGGER.debug(f"{self.device_name}: SunSet duration refreshed to {self.sun_set_duration}s")

    def _refresh_sun_durations(self):
        """Refresh sun rise/set durations from DataStore."""
        sun_rise_duration = self.data_store.getDeep("isPlantDay.sun_rise_duration")
        sun_set_duration = self.data_store.getDeep("isPlantDay.sun_set_duration")

        _LOGGER.debug(f"{self.device_name}: _refresh_sun_durations - sun_rise={sun_rise_duration}, sun_set={sun_set_duration}")

        def parse_to_duration(dstr):
            if not dstr or dstr == "" or dstr == "00:00:00":
                return 300  # Default 5 minutes
            try:
                dt = datetime.strptime(dstr, "%H:%M:%S")
                return dt.hour * 3600 + dt.minute * 60 + dt.second
            except Exception as e:
                _LOGGER.error(f"{self.device_name}: Error Parsing Duration '{dstr}': {e}")
                return 300

        # Support both formats: time string "HH:MM:SS" or numeric seconds or None
        if sun_rise_duration is None:
            self.sun_rise_duration = 300
        elif isinstance(sun_rise_duration, (int, float)):
            self.sun_rise_duration = int(sun_rise_duration)
        else:
            self.sun_rise_duration = parse_to_duration(str(sun_rise_duration))

        if sun_set_duration is None:
            self.sun_set_duration = 300
        elif isinstance(sun_set_duration, (int, float)):
            self.sun_set_duration = int(sun_set_duration)
        else:
            self.sun_set_duration = parse_to_duration(str(sun_set_duration))

    def _in_window(self, current, target, duration_minutes, is_sunset=False):
        """Check if current time is within the window for SunRise/SunSet.

        Supports time windows that cross midnight.

        Args:
            current: Current time as time object
            target: Target time (SunRise/SunSet) as time object
            duration_minutes: Window duration in minutes
            is_sunset: Boolean, True for SunSet, False for SunRise

        Returns:
            Boolean: True if current time is in window, False otherwise
        """
        if not target:
            _LOGGER.debug(f"{self.device_name}: _in_window: No target time available")
            return False

        # Convert to minutes since midnight for easier comparison
        current_minutes = current.hour * 60 + current.minute
        target_minutes = target.hour * 60 + target.minute

        # Different logic for SunRise and SunSet
        if is_sunset:
            # For SunSet: Window BEFORE the target time (subtract offset)
            start_minutes = target_minutes - duration_minutes
            end_minutes = target_minutes

            # Handle if SunSet window crosses midnight into previous day
            if start_minutes < 0:
                # Window crosses midnight into previous day
                start_minutes_wrapped = start_minutes + (
                    24 * 60
                )  # Convert to positive minutes of previous day
                return (start_minutes_wrapped <= current_minutes < 24 * 60) or (
                    0 <= current_minutes <= end_minutes
                )
            else:
                # Normal case (no midnight crossing)
                return start_minutes <= current_minutes <= end_minutes
        else:
            # For SunRise: Window BEFORE the target time (subtract offset)
            start_minutes = target_minutes - duration_minutes
            end_minutes = target_minutes

            # Check normal case (no midnight crossing)
            if start_minutes >= 0:  # Starts after midnight
                return start_minutes <= current_minutes <= end_minutes
            else:
                # Time window crosses midnight into previous day
                start_minutes_wrapped = start_minutes + (24 * 60)
                return (start_minutes_wrapped <= current_minutes < 24 * 60) or (
                    0 <= current_minutes <= end_minutes
                )

    async def periodic_sun_phase_check(self):
        """Periodically check for sun phase activation."""
        while True:
            try:
                # Daily reset check
                self._check_should_reset_phases()

                plant_stage = self.data_store.get("plantStage")
                self.current_plant_stage = plant_stage

                if plant_stage in self.plant_stage_min_max:
                    percent_range = self.plant_stage_min_max[plant_stage]
                    # Update voltage ranges if needed

                now = datetime.now().time()

                # Enhanced logging for better diagnostics
                _LOGGER.debug(
                    f"{self.device_name}: Checking sun phases - Current time: {now}"
                )
                _LOGGER.debug(f"{self.device_name}: LightOn: {self.is_scheduled}")

                # Emit events for sun phase activation/deactivation
                # This will be handled by the main light controller

                await self._emit_sun_phase_status(now)

            except Exception as e:
                _LOGGER.error(f"{self.device_name} sun-phase error: {e}")
                import traceback

                _LOGGER.error(traceback.format_exc())
            await asyncio.sleep(60)

    async def _emit_sun_phase_status(self, current_time):
        """Emit sun phase activation status events."""
        # Check for SunRise
        if self.sun_rise_duration and isinstance(self.sun_rise_duration, (int, float)):
            sun_rise_duration_minutes = self.sun_rise_duration / 60
            in_sunrise_window = self._in_window(
                current_time,
                self.light_on_time,
                sun_rise_duration_minutes,
                is_sunset=False,
            )
            await self.event_manager.emit(
                "SunRiseWindowStatus",
                {
                    "device": self.device_name,
                    "in_window": in_sunrise_window,
                    "current_time": str(current_time),
                    "target_time": str(self.light_on_time),
                },
            )

        # Check for SunSet
        if self.sun_set_duration and isinstance(self.sun_set_duration, (int, float)):
            sun_set_duration_minutes = self.sun_set_duration / 60
            in_sunset_window = self._in_window(
                current_time,
                self.light_off_time,
                sun_set_duration_minutes,
                is_sunset=True,
            )
            await self.event_manager.emit(
                "SunSetWindowStatus",
                {
                    "device": self.device_name,
                    "in_window": in_sunset_window,
                    "current_time": str(current_time),
                    "target_time": str(self.light_off_time),
                },
            )

    def _check_should_reset_phases(self):
        """Check if phases should be reset (once per day) and ensure both phases are reset."""
        today = datetime.now().date()
        if today > self.last_day_reset:
            self.last_day_reset = today
            _LOGGER.info(f"{self.device_name}: Daily reset of sun phases performed")
            return True
        return False

    def get_scheduling_info(self):
        """Get current scheduling information.

        Returns:
            Dict with current scheduling state
        """
        return {
            "light_on_time": str(self.light_on_time),
            "light_off_time": str(self.light_off_time),
            "sun_rise_duration": self.sun_rise_duration,
            "sun_set_duration": self.sun_set_duration,
            "is_scheduled": self.is_scheduled,
            "current_plant_stage": self.current_plant_stage,
            "plant_stage_ranges": self.plant_stage_min_max,
        }

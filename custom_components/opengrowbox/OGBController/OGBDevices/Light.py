import asyncio
import logging
from datetime import datetime, time

from ..data.OGBDataClasses.OGBPublications import OGBLightAction
from .Device import Device

_LOGGER = logging.getLogger(__name__)


class Light(Device):
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
        self.voltage = 0
        self.initVoltage = 20
        self.minVoltage = None
        self.maxVoltage = None
        self.steps = 2

        self.isInitialized = False
        self.voltageFromNumber = False

        # Initialize attributes used in __repr__
        self.islightON = None
        self.ogbLightControl = None

        # Light Times
        self.lightOnTime = ""
        self.lightOffTime = ""
        self.sunRiseDuration = ""  # Dauer des SunRises in Minuten
        self.sunSetDuration = ""  # Dauer des SunSets in Minuten
        self.isScheduled = False

        # Sunrise/Sunset
        self.sunPhaseActive = False
        self.sunrise_task = None  # Task reference for sunrise
        self.sunset_task = None  # Task reference for sunset

        # Pause/Resume Control
        self.sun_phase_paused = False
        self.pause_event = asyncio.Event()
        self.pause_event.set()  # Initially not paused

        # Plant Phase
        self.currentPlantStage = ""

        self.PlantStageMinMax = {
            "Germination": {"min": 20, "max": 30},
            "Clones": {"min": 20, "max": 30},
            "EarlyVeg": {"min": 30, "max": 40},
            "MidVeg": {"min": 40, "max": 50},
            "LateVeg": {"min": 50, "max": 65},
            "EarlyFlower": {"min": 65, "max": 80},
            "MidFlower": {"min": 65, "max": 90},
            "LateFlower": {"min": 65, "max": 100},
        }

        self.sunrise_phase_active = False
        self.sunset_phase_active = False
        self.last_day_reset = datetime.now().date()

        if self.isAcInfinDev:
            self.steps = 10
            self.minVoltage = 0
            self.maxVoltage = 100

        self.init()

        # Listen for sun phase events from OGBLightScheduler (single source of truth)
        self.event_manager.on("SunRiseWindowStatus", self._on_sunrise_window_status)
        self.event_manager.on("SunSetWindowStatus", self._on_sunset_window_status)

        ## Events Register
        self.event_manager.on("SunRiseTimeUpdates", self.updateSunRiseTime)
        self.event_manager.on("SunSetTimeUpdates", self.updateSunSetTime)
        self.event_manager.on("PlantStageChange", self.setPlanStageLight)
        self.event_manager.on("LightTimeChanges", self.changeLightTimes)

        self.event_manager.on("toggleLight", self.toggleLight)
        self.event_manager.on("Increase Light", self.increaseAction)
        self.event_manager.on("Reduce Light", self.reduceAction)

        self.event_manager.on("pauseSunPhase", self.pause_sun_phases)
        self.event_manager.on("resumeSunPhase", self.resume_sun_phases)
        self.event_manager.on("stopSunPhase", self.stop_sun_phases)
        self.event_manager.on("DLIUpdate", self.updateLight)

    async def _on_sunrise_window_status(self, data):
        """Handle sunrise window status events from OGBLightScheduler."""
        if not self.isDimmable:
            return
        
        in_window = data.get("in_window", False)
        
        # Debounce: Ignore duplicate events within 500ms
        now = datetime.now()
        if hasattr(self, '_last_sunrise_event_time'):
            time_since_last = (now - self._last_sunrise_event_time).total_seconds() * 1000
            if time_since_last < 500 and in_window:
                _LOGGER.debug(
                    f"{self.deviceName}: Ignoring duplicate SunRise event ({time_since_last:.1f}ms)"
                )
                return
        
        self._last_sunrise_event_time = now
        
        _LOGGER.info(
            f"{self.deviceName}: Received SunRiseWindowStatus - in_window: {in_window}, islightON: {self.islightON}"
        )
        
        if in_window:
            # Set flag BEFORE creating task - prevents duplicate listeners on same event
            if self.sunrise_phase_active:
                _LOGGER.debug(f"{self.deviceName}: Sunrise already active, skipping")
                return
            
            _LOGGER.info(f"{self.deviceName}: Starting sunrise phase (islightON={self.islightON})")
            self.sunrise_phase_active = True  # Set FIRST - blocks duplicate events
            self.start_sunrise_task()
        else:
            if self.sunrise_phase_active and (self.sunrise_task is None or self.sunrise_task.done()):
                _LOGGER.debug(f"{self.deviceName}: Sunrise window exited, resetting phase")
                self.sunrise_phase_active = False

    async def _on_sunset_window_status(self, data):
        """Handle sunset window status events from OGBLightScheduler."""
        if not self.isDimmable:
            return
        
        in_window = data.get("in_window", False)
        
        # Debounce: Ignore duplicate events within 500ms
        now = datetime.now()
        if hasattr(self, '_last_sunset_event_time'):
            time_since_last = (now - self._last_sunset_event_time).total_seconds() * 1000
            if time_since_last < 500 and in_window:
                _LOGGER.debug(
                    f"{self.deviceName}: Ignoring duplicate SunSet event ({time_since_last:.1f}ms)"
                )
                return
        
        self._last_sunset_event_time = now
        
        _LOGGER.debug(
            f"{self.deviceName}: Received SunSetWindowStatus - in_window: {in_window}, islightON: {self.islightON}"
        )
        
        if in_window:
            # Set flag BEFORE creating task - prevents duplicate listeners on same event
            if self.sunset_phase_active:
                _LOGGER.debug(f"{self.deviceName}: Sunset already active, skipping")
                return
            
            _LOGGER.info(f"{self.deviceName}: Starting sunset phase (islightON={self.islightON})")
            self.sunset_phase_active = True  # Set FIRST - blocks duplicate events
            self.start_sunset_task()
        else:
            if self.sunset_phase_active and (self.sunset_task is None or self.sunset_task.done()):
                _LOGGER.debug(f"{self.deviceName}: Sunset window exited, resetting phase")
                self.sunset_phase_active = False


    def __repr__(self):
        return (
            f"DeviceName:'{self.deviceName}' Typ:'{self.deviceType}'RunningState:'{self.isRunning}'"
            f"Dimmable:'{self.isDimmable}' Voltage:'{self.voltage}' Switches:'{self.switches}' Sensors:'{self.sensors}'"
            f"Options:'{self.options}' OGBS:'{self.ogbsettings}' islightON: '{self.islightON}'"
            f"StartTime:'{self.lightOnTime}' StopTime:{self.lightOffTime} sunSetDuration:'{self.sunSetDuration}' sunRiseDuration:'{self.sunRiseDuration}'"
            f"SunPhasePaused:'{self.sun_phase_paused}'"
        )

    async def pause_sun_phases(self, data=None):
        """Pausiert alle laufenden Sonnenphasen"""
        if not self.sun_phase_paused:
            self.sun_phase_paused = True
            self.pause_event.clear()  # Blockiert alle wartenden Tasks
            _LOGGER.info(f"{self.deviceName}: Sonnenphasen pausiert")
        else:
            _LOGGER.debug(f"{self.deviceName}: Sonnenphasen bereits pausiert")

    async def resume_sun_phases(self, data=None):
        """Setzt alle pausierten Sonnenphasen fort"""
        if self.sun_phase_paused:
            self.sun_phase_paused = False
            self.pause_event.set()  # Gibt alle wartenden Tasks frei
            _LOGGER.info(f"{self.deviceName}: Sonnenphasen fortgesetzt")
        else:
            _LOGGER.debug(f"{self.deviceName}: Sonnenphasen sind nicht pausiert")

    async def stop_sun_phases(self, data=None):
        """Stoppt alle laufenden Sonnenphasen komplett"""
        _LOGGER.info(f"{self.deviceName}: Stoppe alle Sonnenphasen")

        # Zuerst pausieren falls aktiv
        if self.sun_phase_paused:
            await self.resume_sun_phases()

        # Tasks abbrechen
        if self.sunrise_task and not self.sunrise_task.done():
            self.sunrise_task.cancel()
            _LOGGER.info(f"{self.deviceName}: SunRise Task abgebrochen")

        if self.sunset_task and not self.sunset_task.done():
            self.sunset_task.cancel()
            _LOGGER.info(f"{self.deviceName}: SunSet Task abgebrochen")

        # Status zurücksetzen
        self.sunPhaseActive = False
        self.sunrise_phase_active = False
        self.sunset_phase_active = False

        _LOGGER.info(f"{self.deviceName}: Alle Sonnenphasen gestoppt")

    async def _wait_if_paused(self):
        """Wartet, wenn die Sonnenphasen pausiert sind"""
        if self.sun_phase_paused:
            _LOGGER.info(
                f"{self.deviceName}: Sonnenphase pausiert, warte auf Fortsetzung..."
            )
            await self.pause_event.wait()
            _LOGGER.info(f"{self.deviceName}: Sonnenphase fortgesetzt")

    def init(self):
        if not self.isInitialized:
            self.setLightTimes()

            if self.isDimmable:
                # 1. Read sensor value FIRST (like original 1.4.1.7)
                self.checkForControlValue()
                
                # 2. Set plant stage min/max values
                self.checkPlantStageLightValue()
                self.checkMinMax(False)
                
                # 3. Initialize voltage ONLY if voltage is 0/None
                if self.voltage == 0 or self.voltage is None:
                    self.initialize_voltage()

                _LOGGER.debug(f"{self.deviceName}: Voltage init complete -> {self.voltage}% (Min: {self.minVoltage}, Max: {self.maxVoltage}).")
            self.isInitialized = True

    def _has_user_defined_minmax(self) -> bool:
        """
        Check if user has defined custom min/max values for this device.
        Returns True if user settings exist AND are active, meaning we should NOT use plant stage defaults.
        """
        minMaxSets = self.data_store.getDeep(f"DeviceMinMax.{self.deviceType}")
        if not minMaxSets:
            return False
        # User has settings - check if they're active
        return minMaxSets.get("active", False) is True

    def _apply_plant_stage_minmax(self, plantStage: str) -> bool:
        """
        Apply plant stage min/max values if no user-defined values exist.
        Returns True if values were applied, False otherwise.
        """
        if self._has_user_defined_minmax():
            _LOGGER.debug(
                f"{self.deviceName}: User-defined min/max active, skipping plant stage defaults"
            )
            return False

        if plantStage in self.PlantStageMinMax:
            percentRange = self.PlantStageMinMax[plantStage]
            self.minVoltage = percentRange["min"]
            self.maxVoltage = percentRange["max"]
            _LOGGER.debug(
                f"{self.deviceName}: Applied plant stage '{plantStage}' defaults: min={self.minVoltage}%, max={self.maxVoltage}%"
            )
            return True
        return False

    def checkPlantStageLightValue(self):
        if not self.isDimmable:
            return None

        # Check if user has custom min/max settings - if so, respect them
        if self._has_user_defined_minmax():
            _LOGGER.debug(
                f"{self.deviceName}: User-defined min/max is active, not applying plant stage defaults"
            )
            return

        plantStage = self.data_store.get("plantStage")
        self.currentPlantStage = plantStage

        self._apply_plant_stage_minmax(plantStage)

    def initialize_voltage(self):
        """Initialisiert den Voltage auf MinVoltage."""
        if self.islightON:
            if self.voltage == None or self.voltage == 0:
                self.voltage = self.initVoltage
        else:
            self.voltage = 0
        _LOGGER.debug(f"{self.deviceName}: initialize_voltage -> islightON={self.islightON}, voltage={self.voltage}%")

    def setLightTimes(self):

        def parse_to_time(tstr):
            try:
                if not tstr or tstr.strip() == "":
                    _LOGGER.debug(f"{self.deviceName}: Empty time string, using None")
                    return None
                return datetime.strptime(tstr, "%H:%M:%S").time()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Error Parsing Time '{tstr}': {e}")
                return None

        self.lightOnTime = parse_to_time(
            self.data_store.getDeep("isPlantDay.lightOnTime")
        )
        self.lightOffTime = parse_to_time(
            self.data_store.getDeep("isPlantDay.lightOffTime")
        )

        sun_rise = self.data_store.getDeep("isPlantDay.sunRiseTime")
        sun_set = self.data_store.getDeep("isPlantDay.sunSetTime")

        # Modified to return 0 instead of empty string for invalid durations
        def parse_if_valid(time_str):
            if time_str and time_str != "00:00:00":
                return self.parse_time_sec(time_str)
            return 0  # Return 0 instead of empty string

        self.sunRiseDuration = parse_if_valid(sun_rise)
        self.sunSetDuration = parse_if_valid(sun_set)

        # Sync durations to DataStore for OGBLightScheduler
        self.data_store.setDeep("isPlantDay.sun_rise_duration", self.sunRiseDuration)
        self.data_store.setDeep("isPlantDay.sun_set_duration", self.sunSetDuration)

        self.islightON = self.data_store.getDeep("isPlantDay.islightON")

        _LOGGER.debug(
            f"{self.deviceName}: LightTime-Setup "
            f"LightOn:{self.islightON} Start:{self.lightOnTime} Stop:{self.lightOffTime} "
            f"SunRise:{self.sunRiseDuration} SunSet:{self.sunSetDuration}"
        )

    async def changeLightTimes(self, data):

        def parse_to_time(tstr):
            try:
                return datetime.strptime(tstr, "%H:%M:%S").time()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Error Parsing Time '{tstr}': {e}")
                return None

        self.lightOnTime = parse_to_time(
            self.data_store.getDeep("isPlantDay.lightOnTime")
        )
        self.lightOffTime = parse_to_time(
            self.data_store.getDeep("isPlantDay.lightOffTime")
        )

    ## Helpers
    def calculate_actual_voltage(self, percent):
        return percent * (10 / 100)

    def clamp_voltage(self, v):
        if v is None:
            return 0
        min_v = self.minVoltage if self.minVoltage is not None else 0
        max_v = self.maxVoltage if self.maxVoltage is not None else 100
        return max(min_v, min(max_v, v))

    async def setPlanStageLight(self, plantStageData):
        if not self.isDimmable:
            return None

        plantStage = self.data_store.get("plantStage")
        self.currentPlantStage = plantStage

        # Only apply plant stage defaults if user hasn't set custom min/max
        if self._apply_plant_stage_minmax(plantStage):
            if self.islightON:
                if self.sunPhaseActive:
                    return
                await self.turn_on(brightness_pct=self.minVoltage)

            _LOGGER.info(
                f"{self.deviceName}: Setze Spannung für Phase '{plantStage}' auf {self.initVoltage}V–{self.maxVoltage}V-CURRENT:{self.voltage}V."
            )
        elif plantStage not in self.PlantStageMinMax and not self._has_user_defined_minmax():
            _LOGGER.error(
                f"{self.deviceName}: Unbekannte Pflanzenphase '{plantStage}'. Standardwerte werden verwendet."
            )

    # Actions Helpers
    def change_voltage(self, increase=True):
        if not self.isDimmable or self.minVoltage is None or self.voltage is None:
            _LOGGER.debug(f"{self.deviceName}: Cannot change voltage")
            return None

        target = self.voltage + (self.steps if increase else -self.steps)

        self.voltage = self.clamp_voltage(target)

        actual = self.calculate_actual_voltage(self.voltage)

        _LOGGER.info(
            f"{self.deviceName}: Voltage changed to {self.voltage}% ({actual:.2f}V)"
        )
        return self.voltage

    # SunPhases Helpers
    def parse_time_sec(self, time_str: str) -> int:
        """Parst einen Zeitstring wie '00:30:00' in Sekunden."""
        try:
            t = datetime.strptime(time_str, "%H:%M:%S").time()
            return t.hour * 3600 + t.minute * 60 + t.second
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Ungültiges Zeitformat '{time_str}': {e}")
            return 0

    def updateSunRiseTime(self, time_str):
        if not self.isDimmable:
            return None
        self.sunRiseDuration = self.parse_time_sec(time_str)

    def updateSunSetTime(self, time_str):
        if not self.isDimmable:
            return None
        self.sunSetDuration = self.parse_time_sec(time_str)

    def _in_window(self, current, target, duration_minutes, is_sunset=False):
        """
        Überprüft, ob die aktuelle Zeit innerhalb des Fensters für SunRise/SunSet liegt.
        Unterstützt auch Zeitfenster, die über Mitternacht gehen.

        Args:
            current: Die aktuelle Zeit als time-Objekt
            target: Die Zielzeit (SunRise/SunSet) als time-Objekt
            duration_minutes: Die Dauer des Fensters in Minuten
            is_sunset: Boolean, True für SunSet, False für SunRise

        Returns:
            Boolean: True, wenn die aktuelle Zeit im Fenster liegt, sonst False
        """
        if not target:
            _LOGGER.debug(f"{self.deviceName}: _in_window: Keine Zielzeit vorhanden")
            return False

        # Umwandlung in Minuten seit Mitternacht für einfacheren Vergleich
        current_minutes = current.hour * 60 + current.minute
        target_minutes = target.hour * 60 + target.minute

        # Unterschiedliche Logik für SunRise und SunSet
        if is_sunset:
            # Für SunSet: Fenster VOR der Zielzeit (Offset subtrahieren)
            start_minutes = target_minutes - duration_minutes
            end_minutes = target_minutes

            # Handle wenn das SunSetsfenster über Mitternacht in den Vortag geht
            if start_minutes < 0:
                # Fenster überschreitet Mitternacht in den Vortag
                start_minutes_wrapped = start_minutes + (
                    24 * 60
                )  # Konvertiere zu positiven Minuten des Vortags
                return (start_minutes_wrapped <= current_minutes < 24 * 60) or (
                    0 <= current_minutes <= end_minutes
                )
            else:
                # Normaler Fall (keine Überschreitung von Mitternacht)
                return start_minutes <= current_minutes <= end_minutes
        else:
            # Für SunRise: Fenster VOR der Zielzeit (Offset subtrahieren)
            start_minutes = target_minutes - duration_minutes
            end_minutes = target_minutes

            # Check normalen Fall (keine Überschreitung von Mitternacht)
            if start_minutes >= 0:  # Starts after midnight
                return start_minutes <= current_minutes <= end_minutes
            else:
                # Time window crosses midnight into previous day
                start_minutes_wrapped = start_minutes + (24 * 60)
                return (start_minutes_wrapped <= current_minutes < 24 * 60) or (
                    0 <= current_minutes <= end_minutes
                )

    async def periodic_sun_phase_check(self):
        """Legacy method - sun phase now controlled by OGBLightScheduler events.
        
        Kept for backward compatibility, but sun phase control is now event-driven.
        Only performs daily phase reset check.
        """
        while True:
            try:
                self._check_should_reset_phases()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Error in legacy sun phase check: {e}")
            await asyncio.sleep(60)

    def _check_should_reset_phases(self):
        """Überprüft, ob die Phasen zurückgesetzt werden sollten (einmal pro Tag) und garantiert, dass beide Phasen zurückgesetzt werden."""
        today = datetime.now().date()
        if today > self.last_day_reset:
            # Ensure both flags are reset
            self.sunrise_phase_active = False
            self.sunset_phase_active = False
            self.last_day_reset = today
            _LOGGER.info(
                f"{self.deviceName}: Täglicher Reset der Sonnenphasen durchgeführt"
            )
            return True
        return False

    def start_sunrise_task(self):
        """Creates a new sunrise task if one isn't already running.
        
        Uses simple flag to prevent race conditions with duplicate tasks.
        Priority for voltage values:
        1. User MinMax (if DeviceMinMax.active = True)
        2. Plant Stage (if User MinMax is not active)
        """
        if self.sunrise_task is not None and not self.sunrise_task.done():
            _LOGGER.debug(
                f"{self.deviceName}: Sunrise task already running, not starting a new one"
            )
            return
        
        self.sunrise_task = asyncio.create_task(self._run_sunrise())
        _LOGGER.debug(f"{self.deviceName}: Created new sunrise task")

    def start_sunset_task(self):
        """Creates a new sunset task if one isn't already running.
        
        Uses simple flag to prevent race conditions with duplicate sunset tasks.
        Priority for voltage values:
        1. User MinMax (if DeviceMinMax.active = True)
        2. Plant Stage (if User MinMax is not active)
        """
        if self.sunset_task is not None and not self.sunset_task.done():
            _LOGGER.debug(
                f"{self.deviceName}: Sunset task already running, not starting a new one"
            )
            return
        
        self.sunset_task = asyncio.create_task(self._run_sunset())
        _LOGGER.info(f"{self.deviceName}: Created new sunset task")

    async def _run_sunrise(self):
        """Führt die SunRisessequenz als separate Task aus.
        
        Priority for voltage values:
        1. User MinMax (if DeviceMinMax.active = True)
        2. Plant Stage (if User MinMax is not active)
        """
        # Prevent concurrent sunrise tasks
        if getattr(self, '_sunrise_running', False):
            _LOGGER.debug(f"{self.deviceName}: Sunrise already running, skipping")
            return
        self._sunrise_running = True
        
        # Store original MinMax values and temporarily disable limits for smooth sunrise
        original_min = self.minVoltage
        original_max = self.maxVoltage
        self.minVoltage = 0    # Allow 0% during sunrise
        self.maxVoltage = 100  # Allow 100% during sunrise
        
        try:
            if not self.isDimmable or not self.islightON:
                _LOGGER.warning(
                    f"{self.deviceName}: SunRise kann nicht ausgeführt werden - isDimmable: {self.isDimmable}, islightON: {self.islightON}"
                )
                return

            if self.maxVoltage is None:
                _LOGGER.warning(
                    f"{self.deviceName}: maxVoltage nicht gesetzt. SunRise abgebrochen."
                )
                return

            if self.sun_phase_paused:
                _LOGGER.warning(
                    f"{self.deviceName}: SunRise Paused."
                )
                return

            # Calculate sunrise voltages with correct priority
            # Priority: User MinMax → Plant Stage → initVoltage
            # sunrise_target = the voltage we want to reach (capped by Plant Stage or User Max)
            # sunrise_start = where we actually start (current voltage, not sunrise_min!)
            is_minmax_active = getattr(self, 'is_minmax_active', False)
            plantStage = self.data_store.get("plantStage")
            
            if is_minmax_active:
                # Use User MinMax values - target is user max, capped at plant stage if needed
                user_max = self.maxVoltage if self.maxVoltage is not None else 100
                if plantStage and plantStage in self.PlantStageMinMax:
                    plant_max = self.PlantStageMinMax[plantStage]["max"]
                    sunrise_target = max(user_max, plant_max)
                    voltage_source = f"User MinMax (max of User={user_max} and PlantStage={plant_max})"
                else:
                    sunrise_target = user_max
                    voltage_source = "User MinMax"
            elif plantStage and plantStage in self.PlantStageMinMax:
                # Use Plant Stage values
                plant_range = self.PlantStageMinMax[plantStage]
                sunrise_target = plant_range["max"]
                voltage_source = f"Plant Stage ({plantStage})"
            else:
                # Fallback to initVoltage and maxVoltage
                sunrise_target = self.maxVoltage if self.maxVoltage is not None else 100
                voltage_source = "initVoltage/maxVoltage"

            # Start from initVoltage (20%), NOT current voltage!
            # This ensures sunrise always starts from the minimum regardless of previous state
            start_voltage = self.initVoltage if hasattr(self, 'initVoltage') else 20
            target_voltage = sunrise_target
            step_duration = float(self.sunRiseDuration or 0) / 10
            
            # Log with correct start voltage (initVoltage = 20%, not self.voltage)
            self.sunPhaseActive = True
            _LOGGER.warning(
                f"{self.inRoom} - {self.deviceName}: Start SunRise von {start_voltage}% bis {target_voltage}% ({voltage_source})"
            )
            _LOGGER.warning(
                f"{self.deviceName}: Sunrise values - start: {start_voltage}, target: {target_voltage}, source: {voltage_source}"
            )

            # Optimierung: Wenn bereits am Ziel oder darüber, kein Dimming nötig
            if start_voltage >= target_voltage:
                _LOGGER.info(
                    f"{self.deviceName}: Sunrise übersprungen - bereits bei {start_voltage}%, Ziel {target_voltage}% erreicht"
                )
                return

            for i in range(1, 11):
                # Check if we should continue with sunrise
                if not self.islightON:
                    _LOGGER.warning(
                        f"{self.deviceName}: SunRise abgebrochen - Licht ausgeschaltet"
                    )
                    break

                # Warten falls pausiert
                if self.sun_phase_paused:
                    await self._wait_if_paused()
                else:
                    await asyncio.sleep(step_duration)
                    # Use progress-based calculation for smooth ramp
                    progress = i / 10
                    next_voltage = start_voltage + ((target_voltage - start_voltage) * progress)
                    self.voltage = round(next_voltage, 1)

                    message = f"{self.deviceName}: SunRise Step {i}: {self.voltage}%"
                    lightAction = OGBLightAction(
                        Name=self.inRoom,
                        Device=self.deviceName,
                        Type=self.deviceType,
                        Action="ON",
                        Message=message,
                        Voltage=self.voltage,
                        Dimmable=True,
                        SunRise=self.sunrise_phase_active,
                        SunSet=self.sunset_phase_active,
                    )
                    await self.event_manager.emit(
                        "LogForClient", lightAction, haEvent=True
                    )
                    _LOGGER.warning(
                        f"{self.deviceName}: SunRise Step {i}: {self.voltage}%"
                    )
                    # Debug: Check voltage before turn_on
                    _LOGGER.debug(f"{self.deviceName}: Sunrise turn_on with voltage={self.voltage}, type={type(self.voltage)}")
                    await self.turn_on(brightness_pct=self.voltage)

            _LOGGER.debug(f"{self.deviceName}: SunRise finished")

        except asyncio.CancelledError:
            _LOGGER.warning(f"{self.deviceName}: SunRise has Stopped")
            raise  # Re-raise to properly handle cancellation
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error on  SunRise: {e}")
        finally:
            # Restore original MinMax values
            self.minVoltage = original_min
            self.maxVoltage = original_max
            # Reset running flag
            self._sunrise_running = False
            # Always reset sunPhaseActive, but sunrise_phase_active stays until window is exited
            self.sunPhaseActive = False
            _LOGGER.warning(
                f"{self.deviceName}: SunRise Task finished, sunPhaseActive=False"
            )

    async def _run_sunset(self):
        """Führt die Sonnenuntergangssequenz als separate Task aus."""
        # Prevent concurrent sunset tasks
        if getattr(self, '_sunset_running', False):
            _LOGGER.debug(f"{self.deviceName}: Sunset already running, skipping")
            return
        self._sunset_running = True
        
        # Store original MinMax values and temporarily disable limits for smooth sunset
        original_min = self.minVoltage
        original_max = self.maxVoltage
        self.minVoltage = 0    # Allow 0% during sunset
        self.maxVoltage = 100  # Allow 100% during sunset
        
        try:
            if not self.isDimmable or not self.islightON:
                _LOGGER.debug(f"{self.deviceName}: Sonnenuntergang kann nicht ausgeführt werden - isDimmable: {self.isDimmable}, islightON: {self.islightON}")
                return
            if self.sun_phase_paused:
                return
            self.sunPhaseActive = True

            start_voltage = self.voltage if self.voltage is not None else self.maxVoltage
            target_voltage = self.initVoltage
            
            # Calculate how much time has elapsed in the sunset window
            # Sunset window ends at lightOffTime
            light_off_time_str = self.data_store.getDeep("isPlantDay.lightOffTime")
            total_duration = float(self.sunSetDuration or 900)  # Default 15 min
            
            from datetime import datetime
            current_time = datetime.now()
            elapsed_seconds = 0
            start_step = 1
            
            if light_off_time_str:
                try:
                    off_time = datetime.strptime(light_off_time_str, "%H:%M:%S").time()
                    off_dt = datetime.combine(current_time.date(), off_time)
                    # Handle case where off_time is earlier than current time (nighttime)
                    if off_dt < current_time:
                        off_dt = off_dt.replace(day=off_dt.day + 1)
                    elapsed = (current_time - off_dt).total_seconds() + total_duration
                    elapsed_seconds = max(0, min(elapsed, total_duration))
                    # Calculate which step we should start at
                    start_step = max(1, min(10, int((elapsed_seconds / total_duration) * 10) + 1))
                    _LOGGER.debug(
                        f"{self.deviceName}: Sunset late start - elapsed={elapsed_seconds:.0f}s, "
                        f"total={total_duration:.0f}s, starting at step {start_step}"
                    )
                except Exception as e:
                    _LOGGER.debug(f"{self.deviceName}: Could not parse lightOffTime: {e}")
            
            step_duration = total_duration / 10
            
            _LOGGER.debug(f"{self.deviceName}: Start SunSet {start_voltage}% bis {target_voltage}%, step_duration={step_duration:.1f}s")

            for i in range(1, 11):
                # Skip steps that have already passed
                if i < start_step:
                    continue
                
                # Check if we should continue with sunset
                if not self.islightON:
                    _LOGGER.error(f"{self.deviceName}: SunSet Stopped - Light is OFF")
                    break
                
                # Warten falls pausiert
                if self.sun_phase_paused:
                    await self._wait_if_paused()
                else:
                    await asyncio.sleep(step_duration)
                    # Use progress-based calculation for smooth ramp
                    progress = i / 10
                    next_voltage = start_voltage - ((start_voltage - target_voltage) * progress)
                    self.voltage = round(next_voltage, 1)
                    message = f"{self.deviceName}: SunSet Step {i}: {self.voltage}%"
                    lightAction = OGBLightAction(Name=self.inRoom,Device=self.deviceName,Type=self.deviceType,Action="ON",Message=message,Voltage=self.voltage,Dimmable=True,SunRise=self.sunrise_phase_active,SunSet=self.sunset_phase_active)
                    await self.eventManager.emit("LogForClient",lightAction,haEvent=True)
                    _LOGGER.debug(f"{self.deviceName}: SunSet Step {i}: {self.voltage}%")
                    await self.turn_on(brightness_pct=self.voltage)

            _LOGGER.debug(f"{self.deviceName}: SunSet Finish")
            self.voltage = 0
            await self.turn_off(brightness_pct=self.voltage)
            
        except asyncio.CancelledError:
            _LOGGER.debug(f"{self.deviceName}: SunSet has Stopped")
            raise  # Re-raise to properly handle cancellation
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error on SunSet: {e}")
        finally:
            # Restore original MinMax values
            self.minVoltage = original_min
            self.maxVoltage = original_max
            # Reset running flag
            self._sunset_running = False
            # Immer sunPhaseActive zurücksetzen, aber sunset_phase_active bleibt bis das Fenster verlassen wird
            self.sunPhaseActive = False
            _LOGGER.debug(f"{self.deviceName}: SunSet Task ended, sunPhaseActive=False")
    
    ## Actions
    async def toggleLight(self, lightState):
        """Toggle the light based on schedule."""
        # CRITICAL: Special light types should NOT respond to this method
        # They have their own dedicated scheduling logic in their respective classes
        special_light_types = {"LightFarRed", "LightUV", "LightBlue", "LightRed", "LightSpectrum"}
        if self.deviceType in special_light_types:
            _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring base toggleLight - using dedicated scheduling")
            return

        self.islightON = lightState
        self.ogbLightControl = self.dataStore.getDeep("controlOptions.lightbyOGBControl")

        if not self.ogbLightControl:
            _LOGGER.info(f"{self.deviceName}: OGB control disabled")
            return False

        if lightState:
            if not self.isRunning:
                if not self.isDimmable:
                    message = "Turn On"
                    lightAction = OGBLightAction(
                        Name=self.inRoom,
                        Device=self.deviceName,
                        Type=self.deviceType,
                        Action="ON",
                        Message=message,
                        Voltage=self.voltage,
                        Dimmable=False,
                        SunRise=self.sunrise_phase_active,
                        SunSet=self.sunset_phase_active,
                    )
                    await self.event_manager.emit(
                        "LogForClient", lightAction, haEvent=True
                    )
                    await self.turn_on()
                    # Activate pending workmode if light is now on
                    if hasattr(self, 'pendingWorkMode') and self.pendingWorkMode is not None:
                        self.inWorkMode = self.pendingWorkMode
                        self.pendingWorkMode = None
                        _LOGGER.info(f"{self.deviceName}: Activated pending WorkMode {self.inWorkMode}")
                else:
                    # Ensure voltage is set based on min/max settings or plant stage defaults
                    # Only use minVoltage if it's explicitly set (> 0), otherwise use initVoltage
                    if self.voltage is None or self.voltage == 0:
                        if self.minVoltage is not None and self.minVoltage > 0:
                            self.voltage = self.initVoltage
                        else:
                            self.voltage = self.initVoltage
                    message = "Turn On"
                    # Activate pending workmode
                    if hasattr(self, 'pendingWorkMode') and self.pendingWorkMode is not None:
                        self.inWorkMode = self.pendingWorkMode
                        self.pendingWorkMode = None
                        _LOGGER.info(f"{self.deviceName}: Activated pending WorkMode {self.inWorkMode}")
                    lightAction = OGBLightAction(
                        Name=self.inRoom,
                        Device=self.deviceName,
                        Type=self.deviceType,
                        Action="ON",
                        Message=message,
                        Voltage=self.voltage,
                        Dimmable=True,
                        SunRise=self.sunrise_phase_active,
                        SunSet=self.sunset_phase_active,
                    )
                    await self.event_manager.emit(
                        "LogForClient", lightAction, haEvent=True
                    )
                    await self.turn_on(brightness_pct=self.voltage)  # Explicitly pass brightness_pct
                self.log_action("Turn ON via toggle")
        else:
            if self.isRunning:
                self.voltage = 0
                message = "Turn Off"
                lightAction = OGBLightAction(
                    Name=self.inRoom,
                    Device=self.deviceName,
                    Type=self.deviceType,
                    Action="OFF",
                    Message=message,
                    Voltage=self.voltage,
                    Dimmable=False,
                    SunRise=self.sunrise_phase_active,
                    SunSet=self.sunset_phase_active,
                )
                await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
                await self.turn_off()
                self.log_action("Turn OFF via toggle")

    async def increaseAction(self, data):
        """Erhöht die Spannung."""
        new_voltage = None

        if not self.isDimmable == False:
            self.log_action("Not Allowed: Device Not Dimmable")
            return
        if self.islightON == False:
            self.log_action("Not Allowed: LightSchedule is 'OFF'")
            return
        if self.ogbLightControl == False:
            self.log_action("Not Allowed: OGBLightControl is 'OFF'")
            return
        if self.sunPhaseActive:
            self.log_action("Changing State Not Allowed In SunPhase")
            return

        if self.data_store.getDeep("controlOptions.vpdLightControl"):
            new_voltage = self.change_voltage(increase=True)

        if new_voltage is not None:
            self.log_action("IncreaseAction")
            message = "IncreaseAction"
            lightAction = OGBLightAction(
                Name=self.inRoom,
                Device=self.deviceName,
                Type=self.deviceType,
                Action="ON",
                Message=message,
                Voltage=new_voltage,
                Dimmable=True,
                SunRise=self.sunrise_phase_active,
                SunSet=self.sunset_phase_active,
            )
            await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
            await self.turn_on(brightness_pct=new_voltage)

    async def reduceAction(self, data):
        """Reduziert die Spannung."""
        new_voltage = None
        if not self.isDimmable == False:
            self.log_action("Not Allowed: Device Not Dimmable")
            return
        if self.islightON == False:
            self.log_action("Not Allowed: LightSchedule is 'OFF'")
            return
        if self.ogbLightControl == False:
            self.log_action("Not Allowed: OGBLightControl is 'OFF'")
            return
        if self.sunPhaseActive:
            self.log_action("Changing State Not Allowed In SunPhase")
            return

        if self.data_store.getDeep("controlOptions.vpdLightControl"):
            _LOGGER.debug(
                f"LightDebug-RED: CV:{self.voltage} MaxV:{self.maxVoltage} MinV:{self.minVoltage}  "
            )
            new_voltage = self.change_voltage(increase=False)

        if new_voltage is not None:
            self.log_action("ReduceAction")
            message = "ReduceAction"
            lightAction = OGBLightAction(
                Name=self.inRoom,
                Device=self.deviceName,
                Type=self.deviceType,
                Action="ON",
                Message=message,
                Voltage=new_voltage,
                Dimmable=True,
                SunRise=self.sunrise_phase_active,
                SunSet=self.sunset_phase_active,
            )
            await self.event_manager.emit("LogForClient", lightAction, haEvent=True)
            await self.turn_on(brightness_pct=new_voltage)

    async def updateLight(self, data=None):
        """Aktualisiert die Lichtdauer basierend auf der DLI"""
        _LOGGER.debug(f"💡 {self.deviceName}: UpdateLight called")
        # Passe die voltage des lichtes entsprechend des DLI an
        if data is None:
            _LOGGER.warning(
                f"💡 {self.deviceName}: No Data provided from Event for DLI Light Control: {data}. Trying to get DLI from Data Store."
            )
            current_dli = self.data_store.getDeep("Light.dli")
        else:
            _LOGGER.debug(
                f"💡 {self.deviceName}: Data provided from Event for DLI Light Control: {data}"
            )
            current_dli = data.DLI
        _LOGGER.debug(f"💡 {self.deviceName}: Current DLI: {current_dli}")

        light_control_type = self.data_store.getDeep("controlOptions.lightControlType")
        if light_control_type is None or light_control_type.upper() != "DLI":
            _LOGGER.info(
                f"💡 {self.deviceName}: Light Control by OGB is set to {light_control_type}"
            )
            return
        await self.updated_light_voltage_by_dli(current_dli)

    async def updated_light_voltage_by_dli(self, dli):
        """Berechnet die Voltage basierend auf dem DLI"""
        _LOGGER.warning(f"DLI update called, sunrise_active: {self.sunrise_phase_active}, current dli: {dli}")
        if self.sunrise_phase_active:
            _LOGGER.warning("Skipping DLI update during sunrise")
            return
        calibration_step_size = 1.0  # Constant
        dli_tollerance = 0.05

        if self.isDimmable == False:
            _LOGGER.debug(
                f"💡 {self.deviceName}: Device is not dimmable. DLI Light Control not possible."
            )
            return
        # get current DLI
        selected_lightplan = self.data_store.get("plantType").lower()
        if not selected_lightplan:
            _LOGGER.debug(
                f"💡 {self.deviceName}: No light plan selected. DLI Light Control not possible."
            )
            return
        plant_stage = self.data_store.get("plantStage").lower()
        if not plant_stage:
            _LOGGER.debug(
                f"💡 {self.deviceName}: No plant stage selected. DLI Light Control not possible."
            )
            return
        if self.islightON == False:
            self.log_action("Not Allowed: LightSchedule is 'OFF'")
            return
        if self.ogbLightControl == False:
            self.log_action("Not Allowed: OGBLightControl is 'OFF'")
            return

        # Hat plant_stage Veg im String
        if plant_stage.lower().find("veg") != -1:
            plant_stage = "veg"
            week = (
                datetime.now().date()
                - datetime.strptime(
                    self.data_store.getDeep("plantDates.growstartdate"), "%Y-%m-%d"
                ).date()
            ).days // 7
        elif plant_stage.lower().find("flower") != -1:
            plant_stage = "flower"
            week = (
                datetime.now().date()
                - datetime.strptime(
                    self.data_store.getDeep("plantDates.bloomswitchdate"), "%Y-%m-%d"
                ).date()
            ).days // 7
        else:
            _LOGGER.debug(
                f"💡 {self.deviceName}: Invalid plant stage selected. DLI Light Control not possible. Set plant stage to '*Veg' or '*Flower'."
            )
            return

        light_plan = self.data_store.getDeep(
            "Light.plans." + selected_lightplan + "." + plant_stage + ".curve"
        )
        if not light_plan:
            _LOGGER.debug(
                f"💡 {self.deviceName}: No light curve found for selected plant stage {plant_stage} and light plan {selected_lightplan}."
            )
            return

        # search dictionarray for curve object where week property is week
        dli_target_week = None
        for curve in light_plan:
            if curve["week"] == week:
                dli_target_week = curve["DLITarget"]
                break

        if not dli_target_week:
            _LOGGER.debug(
                f"💡 {self.deviceName}: No DLI target found for week {week}. Using last week's target."
            )
            dli_target_week = light_plan[-1]["DLITarget"]
        _LOGGER.info(
            f"💡 {self.deviceName}: DLI target for week {week}: {dli_target_week} from phase {plant_stage} for light plan {selected_lightplan}"
        )

        # get current DLI
        _LOGGER.info(
            f"💡 {self.deviceName}: Current DLI: {dli}, Target DLI: {dli_target_week}"
        )

        # Get light min max
        device_minmax = self.data_store.getDeep("DeviceMinMax.Light")
        if not device_minmax:
            _LOGGER.warning(f"💡 {self.deviceName}: DeviceMinMax.Light not found in DataStore. Using defaults.")
            light_min_max_active = False
            light_min = 20.0
            light_max = 100.0
        else:
            light_min_max_active = device_minmax.get("active", "True")
            if not light_min_max_active:
                _LOGGER.warning(f"💡 {self.deviceName}: DeviceMinMax.Light not active. Using default values 20-100%.")
                light_min = 20.0
                light_max = 100.0
            else:
                light_min = float(device_minmax.get("minVoltage", 20.0))
                light_max = float(device_minmax.get("maxVoltage", 100.0))
        if not light_min_max_active:
            _LOGGER.warning(
                f"💡 {self.deviceName}: No active light min max found. Using default values 20-100%."
            )
            light_min = 20.0
            light_max = 100.0
        else:
            light_min = float(
                self.data_store.getDeep("DeviceMinMax.Light").get("minVoltage")
            )
            light_max = float(
                self.data_store.getDeep("DeviceMinMax.Light").get("maxVoltage")
            )

            # Validate voltage ranges - must be 0-100% for brightness control
            # Note: These are brightness percentages, not PPFD µmol/m²/s values
            if light_max > 100:
                _LOGGER.warning(
                    f"💡 {self.deviceName}: maxVoltage {light_max} is too high for brightness control! "
                    f"Maximum brightness is 100%. Using 100% instead. "
                    f"If you meant PPFD µmol/m²/s, configure that in the light plan targets."
                )
                light_max = 100.0
            elif light_max > 200:
                # Additional check: if someone set extremely high values (like 1200)
                # they might have confused brightness % with PPFD µmol/m²/s
                _LOGGER.warning(
                    f"💡 {self.deviceName}: maxVoltage {light_max} seems very high. "
                    f"Are you confusing brightness % with PPFD µmol/m²/s? "
                    f"PPFD targets are configured in light plans, not here. "
                    f"Using 100% for safety."
                )
                light_max = 100.0

            if light_min < 0:
                _LOGGER.warning(
                    f"💡 {self.deviceName}: minVoltage {light_min} is negative! "
                    f"Using 0% instead."
                )
                light_min = 0.0
            if light_min >= light_max:
                _LOGGER.warning(
                    f"💡 {self.deviceName}: minVoltage {light_min} >= maxVoltage {light_max}! "
                    f"Using safe defaults: 20-100%."
                )
                light_min = 20.0
                light_max = 100.0

            _LOGGER.info(
                f"💡 {self.deviceName}: Using validated min {light_min} and max {light_max} from data store."
            )

        _LOGGER.debug(
            f"{self.deviceName}: Light min: {light_min}, Light max: {light_max}"
        )
        if not light_min or not light_max:
            _LOGGER.debug(
                f"{self.deviceName}: No valid light min max found. No DLI control possible."
            )
            return

        # force min and max
        if self.voltage < light_min:
            self.voltage = light_min
        elif self.voltage > light_max:
            self.voltage = light_max

        # Change Voltage if DLI is too high or too low. Use tollerance of 3%
        if dli_target_week is None or self.voltage is None:
            _LOGGER.warning(f"{self.deviceName}: Cannot adjust voltage for DLI - dli_target_week={dli_target_week}, voltage={self.voltage}")
            return

        if dli < dli_target_week * (1 - dli_tollerance):
            new_voltage = min(light_max, self.voltage + calibration_step_size)
            _LOGGER.info(
                f"💡 {self.deviceName}: DLI {dli} is lower than {dli_target_week * (1 - dli_tollerance)}. Voltage will be increased by {calibration_step_size} from {self.voltage}% to {new_voltage}%"
            )
        elif dli > dli_target_week * (1 + dli_tollerance):
            new_voltage = max(light_min, self.voltage - calibration_step_size)
            _LOGGER.info(
                f"💡 {self.deviceName}: DLI {dli} is lower than {dli_target_week * (1 - dli_tollerance)}. Voltage will be decreased by {calibration_step_size} from {self.voltage}% to {new_voltage}%"
            )
        else:
            _LOGGER.info(
                f"💡 {self.deviceName}: DLI {dli} is within tolerance of {dli_tollerance}. No voltage change needed."
            )
            return

        _LOGGER.debug(f"💡 {self.deviceName}: Voltage set to {new_voltage}%")
        self.voltage = new_voltage

        message = f"Update Light Voltage of {self.deviceName} to {new_voltage}%"
        self.log_action(message)
        light_action = OGBLightAction(
            Name=self.inRoom,
            Device=self.deviceName,
            Type=self.deviceType,
            Action="ON",
            Message=message,
            Voltage=new_voltage,
            Dimmable=True,
            SunRise=self.sunrise_phase_active,
            SunSet=self.sunset_phase_active,
        )
        await self.event_manager.emit("LogForClient", light_action, haEvent=True)
        await self.turn_on(brightness_pct=new_voltage)

    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion mit tatsächlicher Spannung."""
        if self.voltage is not None:
            actual_voltage = self.calculate_actual_voltage(self.voltage)
            log_message = f"{self.deviceName} Voltage: {self.voltage}% (Actual: {actual_voltage:.2f} V)"
        else:
            log_message = f"{self.deviceName} Voltage: Not Set"
        _LOGGER.debug(f"{self.deviceName} - {action_name}: {log_message}")

        
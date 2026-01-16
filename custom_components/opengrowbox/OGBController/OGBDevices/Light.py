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
        self.vpdLightControl = None

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
            "MidFlower": {"min": 80, "max": 100},
            "LateFlower": {"min": 80, "max": 100},
        }

        self.sunrise_phase_active = False
        self.sunset_phase_active = False
        self.last_day_reset = datetime.now().date()

        if self.isAcInfinDev:
            self.steps = 10
            self.minVoltage = 0
            self.maxVoltage = 100

        self.init()

        # SunPhaseListener
        asyncio.create_task(self.periodic_sun_phase_check())

        ## Events Register
        self.event_manager.on("SunRiseTimeUpdates", self.updateSunRiseTime)
        self.event_manager.on("SunSetTimeUpdates", self.updateSunSetTime)
        self.event_manager.on("PlantStageChange", self.setPlanStageLight)
        self.event_manager.on("LightTimeChanges", self.changeLightTimes)

        self.event_manager.on("VPDLightControl", self.vpdLightControlChange)

        self.event_manager.on("toggleLight", self.toggleLight)
        self.event_manager.on("Increase Light", self.increaseAction)
        self.event_manager.on("Reduce Light", self.reduceAction)

        self.event_manager.on("pauseSunPhase", self.pause_sun_phases)
        self.event_manager.on("resumeSunPhase", self.resume_sun_phases)
        self.event_manager.on("stopSunPhase", self.stop_sun_phases)
        self.event_manager.on("DLIUpdate", self.updateLight)


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
                # Load last saved voltage first
                saved_voltage = self.data_store.getDeep(f"devices.{self.deviceName}.lastVoltage")
                if saved_voltage is not None:
                    try:
                        self.voltage = float(saved_voltage)
                        _LOGGER.debug(
                            f"{self.deviceName}: Loaded last voltage from DataStore -> {self.voltage}%."
                        )
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"{self.deviceName}: Invalid saved voltage '{saved_voltage}', ignoring")
                        self.voltage = None  # Reset to None so initialize_voltage runs

                self.checkForControlValue()
                self.checkPlantStageLightValue()
                self.checkMinMax(False)
                if self.voltage == None or self.voltage == 0:
                    if self.minVoltage is not None:
                        self.voltage = self.minVoltage
                        _LOGGER.debug(f"{self.deviceName}: Voltage init to minVoltage {self.voltage}%.")
                    else:
                        self.initialize_voltage()
                else:
                    _LOGGER.debug(
                        f"{self.deviceName}: Voltage init done with saved value -> {self.voltage}%."
                    )
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
        """Initialisiert den Voltage auf gespeicherten Wert oder initVoltage."""
        saved_voltage = self.data_store.getDeep(f"devices.{self.deviceName}.lastVoltage")
        if saved_voltage is not None:
            try:
                self.voltage = float(saved_voltage)
                _LOGGER.debug(f"{self.deviceName}: Restored voltage {self.voltage}% from saved value")
                return
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: Invalid saved voltage '{saved_voltage}', using default")

        # Fallback: Immer initVoltage verwenden, nicht 0
        self.voltage = self.initVoltage  # 20%
        _LOGGER.debug(f"{self.deviceName}: Using initVoltage {self.voltage}%")

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

    async def vpdLightControlChange(self, data):
        self.vpdLightControl = (
            data
            if data is not None
            else self.data_store.getDeep("controlOptions.vpdLightControl")
        )

    ## Helpers
    def calculate_actual_voltage(self, percent):
        return percent * (10 / 100)

    def clamp_voltage(self, v):
        return max(self.minVoltage, min(self.maxVoltage, v))

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
        if not self.isDimmable or self.minVoltage is None:
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
            # Für SunRise: Fenster NACH der Zielzeit (Offset addieren) - ursprüngliche Logik
            start_minutes = target_minutes
            end_minutes = target_minutes + duration_minutes

            # Prüfe normalen Fall (keine Überschreitung von Mitternacht)
            if end_minutes < 24 * 60:  # Endet vor Mitternacht
                return start_minutes <= current_minutes <= end_minutes
            else:
                # Zeitfenster überschreitet Mitternacht in den nächsten Tag
                end_minutes_wrapped = end_minutes % (
                    24 * 60
                )  # Modulo für Minuten nach Mitternacht
                return (start_minutes <= current_minutes < 24 * 60) or (
                    0 <= current_minutes <= end_minutes_wrapped
                )

    # SunPhases
    async def periodic_sun_phase_check(self):
        if not self.isDimmable:
            return

        if self.sun_phase_paused:
            return

        while True:
            try:
                # Täglichen Reset überprüfen
                self._check_should_reset_phases()

                plantStage = self.data_store.get("plantStage")
                self.currentPlantStage = plantStage

                # Only apply plant stage defaults if user hasn't set custom min/max
                self._apply_plant_stage_minmax(plantStage)
                # Ensure user min/max overrides plant if active
                self.checkMinMax(False)

                now = datetime.now().time()

                # Verbesserte Logging für bessere Diagnose
                _LOGGER.warning(
                    f"{self.deviceName}: Prüfe Sonnenphasen - Aktuelle Zeit: {now}"
                )
                _LOGGER.warning(
                    f"{self.deviceName}: LightOn: {self.islightON}, SunPhaseActive: {self.sunPhaseActive}"
                )
                _LOGGER.warning(
                    f"{self.deviceName}: LightOnTime: {self.lightOnTime}, LightOffTime: {self.lightOffTime}"
                )
                _LOGGER.warning(
                    f"{self.deviceName}: SunRiseDuration: {self.sunRiseDuration} Sek ({self.sunRiseDuration/60} Min)"
                )
                _LOGGER.warning(
                    f"{self.deviceName}: SunSetDuration: {self.sunSetDuration} Sek ({self.sunSetDuration/60} Min)"
                )
                _LOGGER.warning(
                    f"{self.deviceName}: Sunrise_phase_active: {self.sunrise_phase_active}, Sunset_phase_active: {self.sunset_phase_active}"
                )
                _LOGGER.warning(
                    f"{self.deviceName}: SunPhasePaused: {self.sun_phase_paused}"
                )

                # Prüfung für SunRise
                if self.sunRiseDuration and not self.sun_phase_paused:
                    sunRiseDuration_minutes = self.sunRiseDuration / 60
                    in_sunrise_window = self._in_window(
                        now, self.lightOnTime, sunRiseDuration_minutes, is_sunset=False
                    )
                    _LOGGER.warning(
                        f"{self.deviceName}: Im SunRisesfenster: {in_sunrise_window}"
                    )

                    if in_sunrise_window and self.islightON and self.voltage is not None and self.maxVoltage is not None and self.voltage < self.maxVoltage:
                        if not self.sunrise_phase_active:
                            _LOGGER.warning(f"{self.deviceName}: Start SunRisesphase")
                            self.sunrise_phase_active = True
                            self.start_sunrise_task()
                    elif not in_sunrise_window:
                        # Nur zurücksetzen wenn wir nicht mehr im Fenster sind UND keine Task läuft
                        if self.sunrise_phase_active and (
                            self.sunrise_task is None or self.sunrise_task.done()
                        ):
                            _LOGGER.warning(
                                f"{self.deviceName}: SunRisesfenster verlassen und Task beendet - reset Phase"
                            )
                            self.sunrise_phase_active = False

                # Prüfung für SunSet
                if self.sunSetDuration and not self.sun_phase_paused:
                    sunSetDuration_minutes = self.sunSetDuration / 60
                    in_sunset_window = self._in_window(
                        now, self.lightOffTime, sunSetDuration_minutes, is_sunset=True
                    )
                    _LOGGER.debug(
                        f"{self.deviceName}: Im SunSetsfenster: {in_sunset_window}"
                    )

                    if in_sunset_window and self.islightON and self.voltage is not None and self.minVoltage is not None and self.voltage > self.minVoltage:
                        if not self.sunset_phase_active:
                            _LOGGER.debug(
                                f"{self.deviceName}: Start Sonnenuntergangsphase"
                            )
                            self.sunset_phase_active = True
                            self.start_sunset_task()
                    elif not in_sunset_window:
                        # Nur zurücksetzen wenn wir nicht mehr im Fenster sind UND keine Task läuft
                        if self.sunset_phase_active and (
                            self.sunset_task is None or self.sunset_task.done()
                        ):
                            _LOGGER.debug(
                                f"{self.deviceName}: Sonnenuntergangsfenster verlassen und Task beendet - reset Phase"
                            )
                            self.sunset_phase_active = False

            except Exception as e:
                _LOGGER.error(f"{self.deviceName} sun-phase error: {e}")
                import traceback

                _LOGGER.error(traceback.format_exc())
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
        """Creates a new sunrise task if one isn't already running."""
        if self.sunrise_task is None or self.sunrise_task.done():
            self.sunrise_task = asyncio.create_task(self._run_sunrise())
            _LOGGER.debug(f"{self.deviceName}: Created new sunrise task")
        else:
            _LOGGER.debug(
                f"{self.deviceName}: Sunrise task already running, not starting a new one"
            )

    def start_sunset_task(self):
        """Creates a new sunset task if one isn't already running."""
        if self.sunset_task is None or self.sunset_task.done():
            self.sunset_task = asyncio.create_task(self._run_sunset())
            _LOGGER.info(f"{self.deviceName}: Created new sunset task")
        else:
            _LOGGER.debug(
                f"{self.deviceName}: Sunset task already running, not starting a new one"
            )

    async def _run_sunrise(self):
        """Führt die SunRisessequenz als separate Task aus."""

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

            self.sunPhaseActive = True
            _LOGGER.warning(
                f"{self.inRoom} - {self.deviceName}: Start SunRise von {self.initVoltage}% bis {self.maxVoltage}%"
            )
            _LOGGER.warning(
                f"{self.deviceName}: Sunrise values - minVoltage: {self.minVoltage}, maxVoltage: {self.maxVoltage}, current voltage: {self.voltage}"
            )

            start_voltage = (
                self.voltage if self.voltage is not None else self.maxVoltage
            )
            target_voltage = self.maxVoltage
            step_duration = self.sunRiseDuration / 10
            voltage_step = (target_voltage - start_voltage) / 10

            # Optimierung: Wenn bereits am Ziel oder darüber, kein Dimming nötig
            if start_voltage is not None and target_voltage is not None and start_voltage >= target_voltage:
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
                    next_voltage = min(
                        start_voltage + (voltage_step * i), target_voltage
                    )
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
            _LOGGER.warning(f"{self.deviceName}: SunRise has stopped")
            raise  # Re-raise to properly handle cancellation
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error on  SunRise: {e}")
        finally:
            # Immer sunPhaseActive zurücksetzen, aber sunrise_phase_active bleibt bis das Fenster verlassen wird
            self.sunPhaseActive = False
            _LOGGER.warning(
                f"{self.deviceName}: SunRise Task finished, sunPhaseActive=False"
            )

    async def _run_sunset(self):
        """Führt die Sonnenuntergangssequenz als separate Task aus."""
        try:
            if not self.isDimmable or not self.islightON:
                _LOGGER.warning(
                    f"{self.deviceName}: Sonnenuntergang kann nicht ausgeführt werden - isDimmable: {self.isDimmable}, islightON: {self.islightON}"
                )
                return
            if self.sun_phase_paused:
                return
            self.sunPhaseActive = True

            start_voltage = (
                self.voltage if self.voltage is not None else self.maxVoltage
            )
            target_voltage = self.initVoltage
            step_duration = self.sunSetDuration / 10
            voltage_step = (start_voltage - target_voltage) / 10

            # Optimierung: Wenn bereits am Ziel oder darunter, kein Dimming nötig
            if start_voltage is not None and target_voltage is not None and start_voltage <= target_voltage:
                _LOGGER.info(
                    f"{self.deviceName}: Sunset übersprungen - bereits bei {start_voltage}%, Ziel {target_voltage}% erreicht"
                )
                self.voltage = 0
                await self.turn_off(brightness_pct=self.voltage)
                return

            _LOGGER.warning(
                f"{self.deviceName}: Start SunSet {start_voltage}% bis {target_voltage}%"
            )

            for i in range(1, 11):
                # Check if we should continue with sunset
                if not self.islightON:
                    _LOGGER.debug(f"{self.deviceName}: SunSet Stopped - Light is OFF")
                    break

                # Warten falls pausiert
                if self.sun_phase_paused:
                    await self._wait_if_paused()
                else:
                    await asyncio.sleep(step_duration)
                    next_voltage = max(
                        start_voltage - (voltage_step * i), target_voltage
                    )
                    self.voltage = round(next_voltage, 1)
                    message = f"{self.deviceName}: SunSet Step {i}: {self.voltage}%"
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
                    _LOGGER.debug(
                        f"{self.deviceName}: SunSet Step {i}: {self.voltage}%"
                    )
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
                    if self.voltage is None or self.voltage == 0:
                        if self.minVoltage is not None:
                            self.voltage = self.minVoltage
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

        if self.vpdLightControl:
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

        if self.vpdLightControl:
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
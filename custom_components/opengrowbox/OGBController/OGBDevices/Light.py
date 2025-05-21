from .Device import Device
import logging
from datetime import datetime, time
import asyncio

_LOGGER = logging.getLogger(__name__)

class Light(Device):
    def __init__(self, deviceName, deviceData, eventManager, dataStore, deviceType, inRoom, hass=None):
        super().__init__(deviceName, deviceData, eventManager, dataStore, deviceType, inRoom, hass)
        self.voltage = None
        self.initVoltage = 20
        self.minVoltage = None
        self.maxVoltage = None
        self.steps = 2
        
        
        self.isInitialized = False
        self.voltageFromNumber = False

        self.islightON = None
        self.ogbLightControl = None
        self.vpdLightControl = None
        
        # Light Times     
        self.lightOnTime = ""
        self.lightOffTime = ""
        self.sunRiseDuration = ""  # Dauer des Sonnenaufgangs in Minuten
        self.sunSetDuration = ""  # Dauer des Sonnenuntergangs in Minuten
        self.isScheduled = False

        # Sunrise/Sunset
        self.sunPhaseActive = None
        self.sunrise_task = None  # Task reference for sunrise
        self.sunset_task = None   # Task reference for sunset

        self.sunPhases = {
            "sunRise": {
                "isSunRise": False,
                "isRunning": False,
                "time": "",
                "minSunRise": 20,
                "maxSunRise": self.maxVoltage,
            },
            "sunSet": {
                "isSunSet": False,
                "isRunning": False,
                "time": "",
                "minSunSet": 20,
                "maxSunSet": self.maxVoltage,
                "startDuty": None,
            },
        }
        
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

        self.init()
        
        # SunPhaseListener
        asyncio.create_task(self.periodic_sun_phase_check())
        
        ## Events Register
        self.eventManager.on("SunRiseTimeUpdates", self.updateSunRiseTime)   
        self.eventManager.on("SunSetTimeUpdates", self.updateSunSetTime)
        self.eventManager.on("PlantStageChange", self.setPlanStageLight)
        self.eventManager.on("VPDLightControl", self.vpdLightControlChange)
        
        self.eventManager.on("toggleLight", self.toggleLight)
        self.eventManager.on("Increase Light", self.increaseAction)
        self.eventManager.on("Reduce Light", self.reduceAction)

    def __repr__(self):
        return (f"DeviceName:'{self.deviceName}' Typ:'{self.deviceType}'RunningState:'{self.isRunning}'"
                f"Dimmable:'{self.isDimmable}' Switches:'{self.switches}' Sensors:'{self.sensors}'"
                f"Options:'{self.options}' OGBS:'{self.ogbsettings}' islightON: '{self.islightON}'"
                f"StartTime:'{self.lightOnTime}' StopTime:{self.lightOffTime} sunSetDuration:'{self.sunSetDuration}' sunRiseDuration:'{self.sunRiseDuration}'"
                ) 
        
    def init(self):
        if not self.isInitialized:
            Init=True
            self.setLightTimes()
            
            if self.isDimmable:
                self.checkForControlValue()
                self.setPlanStageLight(Init) 
                                
                if self.voltage == None or self.voltage == 0:
                    self.initialize_voltage()
                    
            self.isInitialized = True
          
    def initialize_voltage(self):
        """Initialisiert den Voltage auf MinVoltage."""
        self.voltage = self.initVoltage  
        _LOGGER.warning(f"{self.deviceName}: Voltage initialisiert auf {self.voltage}%.")

    def setLightTimes(self):
        _LOGGER.warning(f"{self.deviceName}: DataStoreTest  {self.dataStore}")

        def parse_to_time(tstr):
            try:
                return datetime.strptime(tstr, "%H:%M:%S").time()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Fehler beim Parsen von Zeit '{tstr}': {e}")
                return None

        self.lightOnTime = parse_to_time(self.dataStore.getDeep("isPlantDay.lightOnTime"))
        self.lightOffTime = parse_to_time(self.dataStore.getDeep("isPlantDay.lightOffTime"))

        sun_rise = self.dataStore.getDeep("isPlantDay.sunRiseTime")
        sun_set = self.dataStore.getDeep("isPlantDay.sunSetTime")

        def parse_if_valid(time_str):
            if time_str and time_str != "00:00:00":
                return self.parse_time_sec(time_str)
            return ""

        self.sunRiseDuration = parse_if_valid(sun_rise)
        self.sunSetDuration = parse_if_valid(sun_set)

        self.islightON = self.dataStore.getDeep("isPlantDay.islightON")

        _LOGGER.warning(
            f"{self.deviceName}: LightTime-Setup "
            f"LightOn:{self.islightON} Start:{self.lightOnTime} Stop:{self.lightOffTime} "
            f"SunRise:{self.sunRiseDuration} SunSet:{self.sunSetDuration}"
        )

    async def vpdLightControlChange(self, data):
       self.vpdLightControl = data if data is not None else self.dataStore.getDeep("controlOptions.vpdLightControl")
        
    ## Helpers
    def calculate_actual_voltage(self, percent):
        return percent * (10 / 100)

    def clamp_voltage(self, v):
        return max(self.minVoltage, min(self.maxVoltage, v))

    async def setPlanStageLight(self, plantStageData):
        if not self.isDimmable:
            return None

        plantStage = self.dataStore.get("plantStage")
        self.currentPlantStage = plantStage
        
        if plantStage in self.PlantStageMinMax:
            percentRange = self.PlantStageMinMax[plantStage]

            # Rechne Prozentangaben in Spannungswerte um
            self.minVoltage = percentRange["min"]
            self.maxVoltage = percentRange["max"]
            
            if self.islightON:
                if self.sunPhaseActive:
                    return
                await self.turn_on(brightness_pct=self.minVoltage)
            
            _LOGGER.error(f"{self.deviceName}: Setze Spannung für Phase '{plantStage}' auf {self.initVoltage}V–{self.maxVoltage}V-CURRENT:{self.voltage}V.")
        else:
            _LOGGER.error(f"{self.deviceName}: Unbekannte Pflanzenphase '{plantStage}'. Standardwerte werden verwendet.")

    #Actions Helpers
    def change_voltage(self, increase=True):
        if not self.isDimmable or self.minVoltage is None:
            _LOGGER.warning(f"{self.deviceName}: Cannot change voltage")
            return None
        target = self.voltage + (self.steps if increase else -self.steps)
        self.voltage = self.clamp_voltage(target)
        actual = self.calculate_actual_voltage(self.voltage)
        _LOGGER.info(f"{self.deviceName}: Voltage changed to {self.voltage}% ({actual:.2f}V)")
        return self.voltage

    #SunPhases Helpers
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

    def _in_window(self, current, target, duration_minutes):
        """
        Überprüft, ob die aktuelle Zeit innerhalb des Fensters für Sonnenaufgang/Sonnenuntergang liegt.
        
        Args:
            current: Die aktuelle Zeit als time-Objekt
            target: Die Zielzeit (Sonnenaufgang/Sonnenuntergang) als time-Objekt
            duration_minutes: Die Dauer des Fensters in Minuten
            
        Returns:
            Boolean: True, wenn die aktuelle Zeit im Fenster liegt, sonst False
        """
        if not target:
            return False
        
        # Umwandlung in Minuten seit Mitternacht für einfacheren Vergleich
        current_minutes = current.hour * 60 + current.minute
        target_minutes = target.hour * 60 + target.minute
        
        # Prüfe, ob current im Zeitfenster vom Start bis Ende der Dauer liegt
        return target_minutes <= current_minutes <= (target_minutes + duration_minutes)

    def _check_should_reset_phases(self):
        """Überprüft, ob die Phasen zurückgesetzt werden sollten (einmal pro Tag)."""
        today = datetime.now().date()
        if today > self.last_day_reset:
            self.sunrise_phase_active = False
            self.sunset_phase_active = False
            self.last_day_reset = today
            _LOGGER.info(f"{self.deviceName}: Täglicher Reset der Sonnenphasen")
            return True
        return False

    # SunPhases
    async def periodic_sun_phase_check(self):
        if not self.isDimmable:
            return
        while True:
            try:
                # Täglichen Reset überprüfen
                self._check_should_reset_phases()
                
                plantStage = self.dataStore.get("plantStage")
                self.currentPlantStage = plantStage
                
                if plantStage in self.PlantStageMinMax:
                    percentRange = self.PlantStageMinMax[plantStage]

                    # Rechne Prozentangaben in Spannungswerte um
                    self.minVoltage = percentRange["min"]
                    self.maxVoltage = percentRange["max"]
                    
                now = datetime.now().time()
                _LOGGER.warning(f"{self.deviceName}: Prüfe Sonnenphasen - Aktuelle Zeit: {now}, sunRiseDuration: {self.sunRiseDuration/60} Min, sunSetDuration: {self.sunSetDuration/60} Min")
                
                # Detaillierte warning-Informationen
                if self.sunRiseDuration:
                    in_sunrise_window = self._in_window(now, self.lightOnTime, self.sunRiseDuration)
                    _LOGGER.warning(f"{self.deviceName}: Im Sonnenaufgangsfenster: {in_sunrise_window}.")
                    
                    if in_sunrise_window and not self.sunrise_phase_active and self.islightON:
                        _LOGGER.warning(f"{self.deviceName}: Starte Sonnenaufgangsphase")
                        self.sunrise_phase_active = True
                        # Start sunrise as a separate task
                        self.start_sunrise_task()
                
                if self.sunSetDuration:
                    in_sunset_window = self._in_window(now, self.lightOffTime, self.sunSetDuration)
                    _LOGGER.warning(f"{self.deviceName}: Im Sonnenuntergangsfenster: {in_sunset_window}.")
                    
                    if in_sunset_window and not self.sunset_phase_active and self.islightON:
                        _LOGGER.warning(f"{self.deviceName}: Starte Sonnenuntergangsphase")
                        self.sunset_phase_active = True
                        # Start sunset as a separate task
                        self.start_sunset_task()
            except Exception as e:
                _LOGGER.error(f"{self.deviceName} sun-phase error: {e}")
            await asyncio.sleep(10)
            
    def start_sunrise_task(self):
        """Creates a new sunrise task if one isn't already running."""
        if self.sunrise_task is None or self.sunrise_task.done():
            self.sunrise_task = asyncio.create_task(self._run_sunrise())
            _LOGGER.info(f"{self.deviceName}: Created new sunrise task")
        else:
            _LOGGER.warning(f"{self.deviceName}: Sunrise task already running, not starting a new one")
            
    def start_sunset_task(self):
        """Creates a new sunset task if one isn't already running."""
        if self.sunset_task is None or self.sunset_task.done():
            self.sunset_task = asyncio.create_task(self._run_sunset())
            _LOGGER.info(f"{self.deviceName}: Created new sunset task")
        else:
            _LOGGER.warning(f"{self.deviceName}: Sunset task already running, not starting a new one")

    async def _run_sunrise(self):
        """Führt die Sonnenaufgangssequenz als separate Task aus."""
        try:
            if not self.isDimmable or not self.islightON:
                _LOGGER.warning(f"{self.deviceName}: Sonnenaufgang kann nicht ausgeführt werden - isDimmable: {self.isDimmable}, islightON: {self.islightON}")
                return

            if self.maxVoltage is None:
                _LOGGER.warning(f"{self.deviceName}: maxVoltage nicht gesetzt. Sonnenaufgang abgebrochen.")
                return

            self.sunPhaseActive = True
            _LOGGER.warning(f"{self.deviceName}: Starte Sonnenaufgang von {self.initVoltage}% bis {self.maxVoltage}%")

            start_voltage = self.initVoltage
            target_voltage = self.maxVoltage
            step_duration = self.sunRiseDuration / 10
            voltage_step = (target_voltage - start_voltage) / 10

            for i in range(1, 11):
                # Check if we should continue with sunrise
                if not self.islightON:
                    _LOGGER.warning(f"{self.deviceName}: Sonnenaufgang abgebrochen - Licht ausgeschaltet")
                    break
                
                await asyncio.sleep(step_duration)
                next_voltage = min(start_voltage + (voltage_step * i), target_voltage)
                self.voltage = next_voltage
                _LOGGER.warning(f"{self.deviceName}: Sonnenaufgang Schritt {i}: {self.voltage}%")
                await self.turn_on(brightness_pct=self.voltage)

            _LOGGER.warning(f"{self.deviceName}: Sonnenaufgang abgeschlossen")
            self.sunPhaseActive = False
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Fehler bei Sonnenaufgang: {e}")
        finally:
            self.sunPhaseActive = False

    async def _run_sunset(self):
        """Führt die Sonnenuntergangssequenz als separate Task aus."""
        try:
            if not self.isDimmable or not self.islightON:
                _LOGGER.warning(f"{self.deviceName}: Sonnenuntergang kann nicht ausgeführt werden - isDimmable: {self.isDimmable}, islightON: {self.islightON}")
                return

            self.sunPhaseActive = True

            start_voltage = self.voltage if self.voltage is not None else self.maxVoltage
            target_voltage = self.initVoltage
            step_duration = self.sunSetDuration / 10
            voltage_step = (start_voltage - target_voltage) / 10

            _LOGGER.warning(f"{self.deviceName}: Starte Sonnenuntergang von {start_voltage}% bis {target_voltage}%")

            for i in range(1, 11):
                # Check if we should continue with sunset
                if not self.islightON:
                    _LOGGER.warning(f"{self.deviceName}: Sonnenuntergang abgebrochen - Licht ausgeschaltet")
                    break
                    
                await asyncio.sleep(step_duration)
                next_voltage = max(start_voltage - (voltage_step * i), target_voltage)
                self.voltage = next_voltage
                _LOGGER.warning(f"{self.deviceName}: Sonnenuntergang Schritt {i}: {self.voltage}%")
                await self.turn_on(brightness_pct=self.voltage)

            _LOGGER.warning(f"{self.deviceName}: Sonnenuntergang abgeschlossen")
            self.sunPhaseActive = False
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Fehler bei Sonnenuntergang: {e}")
        finally:
            self.sunPhaseActive = False
     
    ## Actions
    async def toggleLight(self, lightState):
        self.islightON = lightState
        self.ogbLightControl = self.dataStore.getDeep("controlOptions.lightbyOGBControl")
        if not self.ogbLightControl:
            _LOGGER.info(f"{self.deviceName}: OGB control disabled")
            return
        if lightState:
            if not self.isRunning:
                if self.voltage is 0 or self.voltage is None:
                    await self.turn_on(brightness_pct=self.initVoltage if self.isDimmable else None)
                    self.log_action("Turn ON via toggle with InitValue")  
                else:
                    await self.turn_on(brightness_pct=self.voltage if self.isDimmable else None)
                    self.log_action("Turn ON via toggle")
        else:
            if self.isRunning:
                await self.turn_off(brightness_pct=0)
                self.voltage = 0
                self.log_action("Turn OFF via toggle")
    
    async def increaseAction(self, data):
        """Erhöht die Spannung."""
        new_voltage = None
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
            _LOGGER.error(f"LightDebug-INC: CV:{self.voltage} MaxV:{self.maxVoltage} MinV:{self.minVoltage}  ")
            new_voltage = self.change_voltage(increase=True)
            
        if new_voltage is not None:
            self.log_action("IncreaseAction")
            await self.turn_on(brightness_pct=new_voltage)

    async def reduceAction(self, data):
        """Reduziert die Spannung."""
        new_voltage = None
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
            _LOGGER.error(f"LightDebug-RED: CV:{self.voltage} MaxV:{self.maxVoltage} MinV:{self.minVoltage}  ")
            new_voltage = self.change_voltage(increase=False)
        
        if new_voltage is not None:
            self.log_action("ReduceAction")
            await self.turn_on(brightness_pct=new_voltage)

    def log_action(self, action_name):
        """Protokolliert die ausgeführte Aktion mit tatsächlicher Spannung."""
        if self.voltage is not None:
            actual_voltage = self.calculate_actual_voltage(self.voltage)
            log_message = f"{self.deviceName} Voltage: {self.voltage}% (Actual: {actual_voltage:.2f} V)"
        else:
            log_message = f"{self.deviceName} Voltage: Not Set"
        _LOGGER.warn(f"{self.deviceName} - {action_name}: {log_message}")
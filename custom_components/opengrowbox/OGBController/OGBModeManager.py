import logging
import asyncio
import math
from .OGBDataClasses.OGBPublications import OGBModePublication
from .OGBDataClasses.OGBPublications import OGBModeRunPublication,OGBHydroPublication,OGBECAction,OGBHydroAction,OGBRetrieveAction,OGBRetrivePublication,OGBCropSteeringPublication,OGBDripperAction
from datetime import datetime
from .utils.calcs import calc_dew_vpd,calc_Dry5Days_vpd

from .OGBSpecialManagers.CropSteering.OGBCSManager import OGBCSManager

_LOGGER = logging.getLogger(__name__)


class OGBModeManager:
    def __init__(self, hass, dataStore, eventManager,room):
        self.name = "OGB Mode Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore 
        self.eventManager = eventManager
        self.isInitialized = False

        self.CropSteeringManager = OGBCSManager(hass,dataStore,eventManager,room)

        self.currentMode = None
        self._hydro_task: asyncio.Task | None = None    
        self._retrive_task: asyncio.Task | None = None           
        self._crop_steering_task: asyncio.Task | None = None
        self._plant_watering_task: asyncio.Task | None = None
        
        ## Events
        self.eventManager.on("selectActionMode", self.selectActionMode)

        # Prem
        self.eventManager.on("PremiumCheck", self.handle_premium_modes)       


    async def selectActionMode(self, Publication):
        """
        Handhabt Änderungen des Modus basierend auf `tentMode`.
        """
        controlOption = self.dataStore.get("mainControl")        
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        #tentMode = self.dataStore.get("tentMode")
        if isinstance(Publication, OGBModePublication):
            return
        elif isinstance(Publication, OGBModeRunPublication):
            tentMode = Publication.currentMode
            #_LOGGER.debug(f"{self.name}: Run Mode {tentMode} for {self.room}")
        else:
            _LOGGER.debug(f"Unbekannter Datentyp: {type(Publication)} - Daten: {Publication}")      
       
        if tentMode == "VPD Perfection":
            await self.handle_vpd_perfection()
        elif tentMode == "VPD Target":
            await self.handle_targeted_vpd()
        elif tentMode == "Drying":
            await self.handle_drying()
        elif tentMode == "MPC Control":
            await self.handle_premium_modes(False)
        elif tentMode == "PID Control":
            await self.handle_premium_modes(False)
        elif tentMode == "AI Control":
            await self.handle_premium_modes(False)
        elif tentMode == "Disabled":
            await self.handle_disabled_mode()
    
        else:
            _LOGGER.debug(f"{self.name}: Unbekannter Modus {tentMode}")

    async def handle_disabled_mode(self):
        """
        Handhabt den Modus 'Disabled'.
        """
        await self.eventManager.emit("LogForClient",{"Name":self.room,"Mode":"Disabled"})
        return None

    ## VPD Modes
    async def handle_vpd_perfection(self):
        """
        Handhabt den Modus 'VPD Perfection' und steuert die Geräte basierend auf dem aktuellen VPD-Wert.
        """
        # Aktuelle VPD-Werte abrufen
        currentVPD = self.dataStore.getDeep("vpd.current")
        perfectionVPD = self.dataStore.getDeep("vpd.perfection")
        perfectionMinVPD = self.dataStore.getDeep("vpd.perfectMin")
        perfectionMaxVPD = self.dataStore.getDeep("vpd.perfectMax")

        # Verfügbare Capabilities abrufen
        capabilities = self.dataStore.getDeep("capabilities")

        if currentVPD < perfectionMinVPD:
            _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is below minimum ({perfectionMinVPD}). Increasing VPD.")
            await self.eventManager.emit("increase_vpd",capabilities)
        elif currentVPD > perfectionMaxVPD:
            _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is above maximum ({perfectionMaxVPD}). Reducing VPD.")
            await self.eventManager.emit("reduce_vpd",capabilities)
        elif currentVPD != perfectionVPD:
            _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is within range but not at perfection ({perfectionVPD}). Fine-tuning.")
            await self.eventManager.emit("FineTune_vpd",capabilities)
        else:
            _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is at perfection ({perfectionVPD}). No action required.")

    async def handle_targeted_vpd(self):
        """
        Handhabt den Modus 'Targeted VPD' mit Toleranz.
        """
        _LOGGER.info(f"ModeManager: {self.room} Modus 'Targeted VPD' aktiviert.")
        
        try:
            # Aktuelle VPD-Werte abrufen
            currentVPD = float(self.dataStore.getDeep("vpd.current"))
            targetedVPD = float(self.dataStore.getDeep("vpd.targeted"))
            tolerance_percent = float(self.dataStore.getDeep("vpd.tolerance"))  # Prozentuale Toleranz (1-25%)

            # Mindest- und Höchstwert basierend auf der Toleranz berechnen
            tolerance_value = targetedVPD * (tolerance_percent / 100)
            min_vpd = targetedVPD - tolerance_value
            max_vpd = targetedVPD + tolerance_value

            # Verfügbare Capabilities abrufen
            capabilities = self.dataStore.getDeep("capabilities")

            # VPD steuern basierend auf der Toleranz
            if currentVPD < min_vpd:
                _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is below minimum ({min_vpd}). Increasing VPD.")
                await self.eventManager.emit("increase_vpd", capabilities)
            elif currentVPD > max_vpd:
                _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is above maximum ({max_vpd}). Reducing VPD.")
                await self.eventManager.emit("reduce_vpd", capabilities)
            elif currentVPD != targetedVPD:
                _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is within range but not at Targeted ({targetedVPD}). Fine-tuning.")
                await self.eventManager.emit("FineTune_vpd", capabilities)
            else:
                _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is within tolerance range ({min_vpd} - {max_vpd}). No action required.")
                return
            
        except ValueError as e:
            _LOGGER.error(f"ModeManager: Fehler beim Konvertieren der VPD-Werte oder Toleranz in Zahlen. {e}")
        except Exception as e:
            _LOGGER.error(f"ModeManager: Unerwarteter Fehler in 'handle_targeted_vpd': {e}")

    ## Premium Handle
    async def handle_premium_modes(self,data):
        
        if data == False:
            return
        controllerType = data.get("controllerType")
        if controllerType == "PID":
            await self.eventManager.emit("PIDActions",data)
        if controllerType == "MPC":
            await self.eventManager.emit("MPCActions",data)
        if controllerType == "AI":
            await self.eventManager.emit("AIActions",data)
        return

    ## Drying Modes
    async def handle_ElClassico(self, phaseConfig):
        _LOGGER.warning(f"{self.name} Run Drying 'El Classico'")          
        tentData = self.dataStore.get("tentData")     

        tempTolerance = 1
        humTolerance = 2
        finalActionMap = {}

        current_phase = self.get_current_phase(phaseConfig)
        
        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        temp_ok = abs(tentData['temperature'] - current_phase['targetTemp']) <= tempTolerance

        if not temp_ok:
            if tentData['temperature'] < current_phase['targetTemp']:
                finalActionMap["Increase Heater"] = True
                finalActionMap["Reduce Exhaust"] = True
                finalActionMap["Reduce Cooler"] = True
                finalActionMap["Increase Ventilation"] = True
            else:
                finalActionMap["Increase Cooler"] = True
                finalActionMap["Increase Exhaust"] = True
                finalActionMap["Reduce Heater"] = True
                finalActionMap["Reduce Ventilation"] = True
        else:
            if abs(tentData["humidity"] - current_phase["targetHumidity"]) > humTolerance:
                if tentData["humidity"] < current_phase["targetHumidity"]:
                    finalActionMap["Increase Humidifier"] = True
                    finalActionMap["Increase Ventilation"] = True
                    finalActionMap["Reduce Exhaust"] = True
                else:
                    finalActionMap["Increase Dehumidifier"] = True
                    finalActionMap["Increase Ventilation"] = True
                    finalActionMap["Increase Exhaust"] = True

        # Emit all actions in the map
        for action in finalActionMap.keys():
            await self.eventManager.emit(action, None)

        # Send summary to client
        await self.eventManager.emit("LogForClient", finalActionMap, haEvent=True)

    async def handle_5DayDry(self, phaseConfig):
        _LOGGER.debug(f"{self.name} Run Drying '5 Day Dry'")  

        tentData = self.dataStore.get("tentData")
        vpdTolerance = self.dataStore.get("vpd.tolerance") or 3 # %
        capabilities = self.dataStore.getDeep("capabilities")

        current_phase = self.get_current_phase(phaseConfig)
        
        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        current_temp = tentData["temperature"] if "temperature" in tentData else None
        current_humidity = tentData["humidity"] if "humidity" in tentData else None

        if current_temp is None or current_humidity is None:
            _LOGGER.warning(f"{self.room}: Missing tentData values for VPD calculation")
            return

        if isinstance(tentData["temperature"], (list, tuple)):
            temp_value = sum(tentData["temperature"]) / len(tentData["temperature"])
        else:
            temp_value = tentData["temperature"]

        Dry5DaysVPD = calc_Dry5Days_vpd(temp_value, current_humidity)
        self.dataStore.setDeep("drying.5DayDryVPD", Dry5DaysVPD)
        
        target_vpd = current_phase.get("targetVPD")
        if target_vpd is None:
            _LOGGER.error(f"{self.room}: Current phase has no targetVPD key")
            return
        
        delta = Dry5DaysVPD - target_vpd

        if abs(delta) > vpdTolerance:
            if delta < 0:
                _LOGGER.debug(f"{self.room}: Dry5Days VPD {Dry5DaysVPD:.2f} < Target {target_vpd:.2f} → Increase VPD")
                await self.eventManager.emit("increase_vpd", capabilities)
            else:
                _LOGGER.debug(f"{self.room}: Dry5Days VPD {Dry5DaysVPD:.2f} > Target {target_vpd:.2f} → Reduce VPD")
                await self.eventManager.emit("reduce_vpd", capabilities)
        else:
            _LOGGER.debug(f"{self.room}: Dry5Days VPD {Dry5DaysVPD:.2f} within tolerance (±{vpdTolerance}) → No action")

    async def handle_DewBased(self, phaseConfig):
        _LOGGER.debug(f"{self.name}: Run Drying 'Dew Based'")

        tentData = self.dataStore.get("tentData")
        currentDewPoint = tentData.get("dewpoint")
        currenTemperature = tentData.get("temperature")
        
        dewPointTolerance = 0.5
        dew_vps = calc_dew_vpd(currenTemperature,currentDewPoint)
        
        vaporPressureActual = dew_vps.get("vapor_pressure_actual")
        vaporPressureSaturation = dew_vps.get("vapor_pressure_saturation")
        
        self.dataStore.setDeep("drying.vaporPressureActual",vaporPressureActual)
        self.dataStore.setDeep("drying.vaporPressureSaturation",vaporPressureSaturation)

        current_phase = self.get_current_phase(phaseConfig)
        
        if current_phase is None:
            _LOGGER.error(f"{self.name}: Could not determine current phase")
            return

        if currentDewPoint is None or not isinstance(currentDewPoint, (int, float)) or math.isnan(currentDewPoint):
            _LOGGER.warning(f"{self.name}: Current Dew Point is unavailable or invalid.")
            return

        targetDewPoint = current_phase.get("targetDewPoint")
        if targetDewPoint is None:
            _LOGGER.error(f"{self.name}: Current phase has no targetDewPoint key")
            return

        dew_diff = currentDewPoint - targetDewPoint
        vp_low = vaporPressureActual < 0.9 * vaporPressureSaturation if vaporPressureActual and vaporPressureSaturation else False
        vp_high = vaporPressureActual > 1.1 * vaporPressureSaturation if vaporPressureActual and vaporPressureSaturation else False

        if abs(dew_diff) > dewPointTolerance or vp_low or vp_high:
            if dew_diff < -dewPointTolerance or vp_low:
                await self.eventManager.emit("Increase Humidifier", None)
                await self.eventManager.emit("Reduce Dehumidifier", None)
                await self.eventManager.emit("Reduce Exhaust", None)
                await self.eventManager.emit("Increase Ventilation", None)
                _LOGGER.debug(f"{self.room}: Too dry. Humidify ↑, Dehumidifier ↓, Exhaust ↓, Ventilation ↑")
            elif dew_diff > dewPointTolerance or vp_high:
                await self.eventManager.emit("Increase Dehumidifier", None)
                await self.eventManager.emit("Reduce Humidifier", None)
                await self.eventManager.emit("Increase Exhaust", None)
                await self.eventManager.emit("Increase Ventilation", None)
                _LOGGER.debug(f"{self.room}: Too humid. Dehumidify ↑, Humidifier ↓, Exhaust ↑, Ventilation ↑")
        else:
            await self.eventManager.emit("Reduce Humidifier", None)
            await self.eventManager.emit("Reduce Dehumidifier", None)
            await self.eventManager.emit("Reduce Exhaust", None)
            await self.eventManager.emit("Reduce Ventilation", None)
            _LOGGER.debug(f"{self.room}: Dew Point {currentDewPoint:.2f} within ±{dewPointTolerance} → All systems idle")

    def get_current_phase(self, phaseConfig):
        """
        Bestimmt die aktuelle Phase basierend auf der verstrichenen Zeit
        """
        # Holen Sie sich den Startzeitpunkt des Modus (müssen Sie implementieren)
        mode_start_time = self.dataStore.getDeep("drying.mode_start_time")
        
        if mode_start_time is None:
            _LOGGER.warning(f"{self.name}: No mode start time found, defaulting to 'start' phase")
            return phaseConfig['phase']['start']
        
        # Berechne verstrichene Zeit in Stunden
        from datetime import datetime
        current_time = datetime.now()
        elapsed_hours = (current_time - mode_start_time).total_seconds() / 3600
        
        # Bestimme Phase basierend auf verstrichener Zeit
        phases = phaseConfig['phase']
        start_duration = phases['start']['durationHours']
        halfTime_duration = phases['halfTime']['durationHours']
        endTime_duration = phases['endTime']['durationHours']
        
        if elapsed_hours <= start_duration:
            _LOGGER.debug(f"{self.name}: Currently in 'start' phase ({elapsed_hours:.1f}h of {start_duration}h)")
            return phases['start']
        elif elapsed_hours <= start_duration + halfTime_duration:
            _LOGGER.debug(f"{self.name}: Currently in 'halfTime' phase ({elapsed_hours:.1f}h total)")
            return phases['halfTime']
        elif elapsed_hours <= start_duration + halfTime_duration + endTime_duration:
            _LOGGER.debug(f"{self.name}: Currently in 'endTime' phase ({elapsed_hours:.1f}h total)")
            return phases['endTime']
        else:
            _LOGGER.warning(f"{self.name}: All phases completed ({elapsed_hours:.1f}h total), using 'endTime' phase")
            return phases['endTime']

    def start_drying_mode(self, mode_name):
        """
        Startet einen Trocknungsmodus und speichert den Startzeitpunkt
        """
        from datetime import datetime
        self.dataStore.setDeep("drying.mode_start_time", datetime.now())
        self.dataStore.setDeep("drying.currentDryMode", mode_name)
        self.dataStore.setDeep("drying.isRunning", True)
        _LOGGER.warning(f"{self.name}: Started drying mode '{mode_name}' at {datetime.now()}")

    async def handle_drying(self):
        """
        Handhabt den Modus 'Drying'.
        """
        currentDryMode = self.dataStore.getDeep("drying.currentDryMode")
        
        # Prüfen ob ein Startzeitpunkt existiert, falls nicht setzen
        mode_start_time = self.dataStore.getDeep("drying.mode_start_time")
        if mode_start_time is None and currentDryMode != "NO-Dry":
            self.start_drying_mode(currentDryMode)
        
        if currentDryMode == "ElClassico":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}")  
            await self.handle_ElClassico(phaseConfig)
        elif currentDryMode == "DewBased":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}") 
            await self.handle_DewBased(phaseConfig)
        elif currentDryMode == "5DayDry":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}") 
            await self.handle_5DayDry(phaseConfig)
        elif currentDryMode == "NO-Dry":
            return None
        else:
            _LOGGER.debug(f"{self.name} Unknown DryMode Recieved")           
            return None
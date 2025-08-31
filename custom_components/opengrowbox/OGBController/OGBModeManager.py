import logging
import asyncio

from .OGBDataClasses.OGBPublications import OGBModePublication
from .OGBDataClasses.OGBPublications import OGBModeRunPublication,OGBHydroPublication,OGBHydroAction,OGBRetrieveAction,OGBRetrivePublication

from .utils.calcs import calc_dew_vpd,calc_Dry5Days_vpd

_LOGGER = logging.getLogger(__name__)


class OGBModeManager:
    def __init__(self, hass, dataStore, eventManager,room):
        self.name = "OGB Mode Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore  # Bereits bestehendes DataStore-Objekt
        self.eventManager = eventManager
        self.isInitialized = False

        self.currentMode = None
        self._hydro_task: asyncio.Task | None = None    
        self._retrive_task: asyncio.Task | None = None           
        
        ## Events
        self.eventManager.on("selectActionMode", self.selectActionMode)

        # Prem
        self.eventManager.on("PremiumCheck", self.handle_premium_mode)       

        # Water 
        self.eventManager.on("HydroModeChange", self.HydroModeChange)
        self.eventManager.on("HydroModeStart", self.hydro_Mode) 
        self.eventManager.on("PlamtWateringStart", self.hydro_PlantWatering)
        self.eventManager.on("HydroModeRetrieveChange", self.HydroModRetrieveChange)
        self.eventManager.on("HydroRetriveModeStart", self.retrive_Mode) 

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
        elif tentMode == "MCP-Control":
            await self.handle_premium_mode(False)
        elif tentMode == "PID-Control":
            await self.handle_premium_mode(False)
        elif tentMode == "AI-Control":
            await self.handle_premium_mode(False)
        elif tentMode == "OGB-Control":
            await self.handle_premium_mode(False)
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
            #await self.eventManager.emit("LogForClient",{"Name":self.room,"Action":"VPD Check Increasing","currentVPD:":currentVPD,"perfectionVPD":perfectionVPD},haEvent=True)
        elif currentVPD > perfectionMaxVPD:
            _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is above maximum ({perfectionMaxVPD}). Reducing VPD.")
            await self.eventManager.emit("reduce_vpd",capabilities)
            #await self.eventManager.emit("LogForClient",{"Name":self.room,"Action":"VPD Check Reducing","currentVPD:":currentVPD,"perfectionVPD":perfectionVPD},haEvent=True)
        elif currentVPD != perfectionVPD:
            _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is within range but not at perfection ({perfectionVPD}). Fine-tuning.")
            await self.eventManager.emit("FineTune_vpd",capabilities)
            #await self.eventManager.emit("LogForClient",{"Name":self.room,"Action":"VPD Check Fine-Tune","currentVPD:":currentVPD,"perfectionVPD":perfectionVPD},haEvent=True)
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
            
            #from .utils.sensorUpdater import _update_specific_sensor
            #await _update_specific_sensor("sensor.ogb_current_vpd_target_",self.room,targetedVPD,self.hass)
            #await _update_specific_sensor("sensor.ogb_current_vpd_target_min_",self.room,min_vpd,self.hass)
            #await _update_specific_sensor("sensor.ogb_current_vpd_target_max_",self.room,max_vpd,self.hass)
            
            
            _LOGGER.debug(f"{self.room}: Targeted VPD: {targetedVPD}, Tolerance: {tolerance_percent}% "
                        f"-> Min: {min_vpd}, Max: {max_vpd}, Current: {currentVPD}")

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
            else:
                _LOGGER.debug(f"{self.room}: Current VPD ({currentVPD}) is within tolerance range ({min_vpd} - {max_vpd}). No action required.")
        
        except ValueError as e:
            _LOGGER.error(f"ModeManager: Fehler beim Konvertieren der VPD-Werte oder Toleranz in Zahlen. {e}")
        except Exception as e:
            _LOGGER.error(f"ModeManager: Unerwarteter Fehler in 'handle_targeted_vpd': {e}")

    ## Premium Handle
    async def handle_premium_mode(self,data):
        
        if data == False:
            return
        controllerType = data.get("controllerType")
        if controllerType == "PID":
            await self.eventManager.emit("PIDActions",data)
        if controllerType == "MCP":
            await self.eventManager.emit("MCPActions",data)
        if controllerType == "AI":
            await self.eventManager.emit("AIActions",data)
        if controllerType == "OGB":
            await self.eventManager.emit("OGBActions",data)
        return

    ## Drying Modes
    async def handle_drying(self):
        """
        Handhabt den Modus 'Drying'.
        """
        currentDryMode = self.dataStore.getDeep("drying.currentDryMode")
        
        if currentDryMode == "ElClassico":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}")  
            await self.handle_ElClassico(phaseConfig)
        elif currentDryMode == "DewBased":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}") 
            await self.handle_DewBased(phaseConfig)
        elif currentDryMode == "Dry5Days":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}") 
            await self.handle_premium_mode()
        else:
            _LOGGER.debug(f"{self.name} Unknown DryMode Recieved")           
            return None

    async def handle_ElClassico(self,phaseConfig):
        _LOGGER.debug(f"{self.name} Run Drying 'El Classico'")          
        tentData = self.dataStore.get("tentData")     

        # Verfügbare Capabilities abrufen
        capabilities = self.dataStore.getDeep("capabilities")
        
        tempTolerance = 0.5
        humTolerance = 2
        
        
        # Anpassungen basierend auf Temperatur        
        if abs(tentData['temperature'] - phaseConfig['targetTemp']) > tempTolerance:
            if tentData['temperature'] < phaseConfig['targetTemp']:
                await self.eventManager.emit("Increase Heater",None)
                await self.eventManager.emit("Increase Exhaust",None)
            else:
                await self.eventManager.emit("Increase Cooler",None)
                await self.eventManager.emit("Increase Exhaust",None)
                         
        # Anpassungen basierend auf Feuchtigkeit
        if abs(tentData["humidity"] - phaseConfig["targetHumidity"]) > humTolerance:
            if tentData["humidity"] < phaseConfig["targetHumidity"]:
                await self.eventManager.emit("Increase Humidifier",None)
                await self.eventManager.emit("Increase Ventilation",None)
            else:
                await self.eventManager.emit("Increase Dehumidifier",None)
                await self.eventManager.emit("Increase Ventilation",None)

        # Log die Aktion
        _LOGGER.info(f"{self.name}: El Classico Phase ")

    async def handle_Dry5Days(self,phaseConfig):
        _LOGGER.debug(f"{self.name} Run Drying 'Shark Mouse'")  
        tentData = self.dataStore.get("tentData")
        vpdTolerance = self.dataStore.get("vpd.tolerance")
        Dry5DaysVPD = calc_Dry5Days_vpd(tentData["temperatures"],tentData["humidity"])
       
        # Verfügbare Capabilities abrufen
        capabilities = self.dataStore.getDeep("capabilities")
       
        #Anpassungen bassierend auf VPD
        if abs(Dry5DaysVPD - phaseConfig['targetVPD']) > vpdTolerance:
            if Dry5DaysVPD < phaseConfig['targetVPD']: 
                _LOGGER.debug(f"{self.room}: Dry5Days VPD ({Dry5DaysVPD}) nened to 'Increase' for Reaching {phaseConfig['targetVPD']}")
                await self.eventManager.emit("increase_vpd",capabilities)
            elif Dry5DaysVPD > phaseConfig['targetVPD']:
                _LOGGER.debug(f"{self.room}: Dry5Days VPD ({Dry5DaysVPD}) nened to 'Reduce' for Reaching {phaseConfig['targetVPD']}")
                await self.eventManager.emit("reduce_vpd",capabilities)
            else:
                _LOGGER.debug(f"{self.room}: Dry5Days VPD ({Dry5DaysVPD}) Is on Spot. No action required.")
                
    async def handle_DewBased(self,phaseConfig):
        _LOGGER.debug(f"{self.name}: Run Drying 'Dew Based'")

        tentData = self.dataStore.get("tentData")
        dewPointTolerance = 0.5  # Toleranz für Taupunkt
        vaporPressureActual = self.dataStore.getDeep("drying.vaporPressureActual")
        vaporPressureSaturation = self.dataStore.getDeep("drying.vaporPressureSaturation")

        currentDewPoint = tentData["dewpoint"]
    
            # Sicherstellen, dass der Taupunkt eine gültige Zahl ist
        if not isinstance(currentDewPoint, (int, float)) or currentDewPoint is None or currentDewPoint != currentDewPoint:  # NaN-Check
            _LOGGER.debug(f"{self.name}: Current Dew Point is unavailable or invalid.")
            return None
        if (abs(currentDewPoint - phaseConfig["targetDewPoint"]) > dewPointTolerance or vaporPressureActual < 0.9 * vaporPressureSaturation or vaporPressureActual > 1.1 * vaporPressureSaturation):       
            if currentDewPoint < phaseConfig["targetDewPoint"] or vaporPressureActual < 0.9 * vaporPressureSaturation:
                await self.eventManager.emit("Increase Humidifier", None)
                await self.eventManager.emit("Increase Exhaust", None)
                await self.eventManager.emit("Increase Ventilation", None)
                _LOGGER.debug(f"{self.room}: Dew Point ({currentDewPoint}) below target ({phaseConfig['targetDewPoint']}). Actions: Increase humidity.")
            elif currentDewPoint > phaseConfig["targetDewPoint"] or vaporPressureActual > 1.1 * vaporPressureSaturation:
                await self.eventManager.emit("Increase Dehumidifier", None)
                await self.eventManager.emit("Increase Exhaust", None)
                await self.eventManager.emit("Increase Ventilation", None)
                _LOGGER.debug(f"{self.room}: Dew Point ({currentDewPoint}) above target ({phaseConfig['targetDewPoint']}). Actions: Reduce humidity.")
        else:
            _LOGGER.debug(f"{self.room}: Dew Point ({currentDewPoint}) is within tolerance range. No actions required.")

    # Dynamic Device Action Recognition
    def get_relevant_devices(self, vpdStatus: str):
        available_capabilities = self.dataStore.get("capabilities")
        device_profiles = self.dataStore.get("DeviceProfiles")
        result = []

        for dev_name, profile in device_profiles.items():
            cap_key = profile.get("cap")
            if not cap_key:
                continue

            cap_info = available_capabilities.get(cap_key)
            if not cap_info or cap_info["count"] == 0:
                continue

            if vpdStatus == "too_high":
                if (
                    (profile["type"] == "humidity" and profile["direction"] == "increase") or
                    (profile["type"] == "temperature" and profile["direction"] == "reduce") or
                    (profile["type"] == "both" and profile["direction"] == "reduce")
                ):
                    result.extend(cap_info["devEntities"])

            elif vpdStatus == "too_low":
                if (
                    (profile["type"] == "humidity" and profile["direction"] == "reduce") or
                    (profile["type"] == "temperature" and profile["direction"] == "increase") or
                    (profile["type"] == "both" and profile["direction"] == "increase")
                ):
                    result.extend(cap_info["devEntities"])

        return result

    def determine_vpd_state(self,current_vpd: float, target_vpd: float, tolerance: float = 0.1) -> str:   
        tolerance = float(self.dataStore.getDeep("vpd.tolerance"))
        tolerance_value = target_vpd * (tolerance / 100)
        min_vpd = target_vpd - tolerance_value
        max_vpd = target_vpd + tolerance_value
        
        if current_vpd > min_vpd:
            return "too_high"
        elif current_vpd < max_vpd:
            return "too_low"
        return "ok"

    ## Hydro Modes
    async def HydroModeChange(self, pumpAction):
        isActive = self.dataStore.getDeep("Hydro.Active")
        intervall_raw = self.dataStore.getDeep("Hydro.Intervall")
        duration_raw = self.dataStore.getDeep("Hydro.Duration")
        mode = self.dataStore.getDeep("Hydro.Mode")
        cycle = self.dataStore.getDeep("Hydro.Cycle")
        PumpDevices = self.dataStore.getDeep("capabilities.canPump")

        if intervall_raw is None or duration_raw is None:
            return

        intervall = float(intervall_raw)
        duration = float(duration_raw)

        if mode == "OFF":
            sysmessage = "Hydro mode is OFFLINE"
            self.dataStore.setDeep("Hydro.Active",False)
            await self.eventManager.emit("PumpAction", {"action": "off"})
            if self._hydro_task is not None:
                self._hydro_task.cancel()
                try:
                    await self._hydro_task
                except asyncio.CancelledError:
                    pass
                self._hydro_task = None
        elif mode == "Hydro":
            sysmessage = "Hydro mode active"
            self.dataStore.setDeep("Hydro.Active",True)
            await self.hydro_Mode(cycle, intervall, duration, PumpDevices)
        elif mode == "Plant-Watering":
            sysmessage = "Plant watering mode active"
            self.dataStore.setDeep("Hydro.Active",True)
            await self.hydro_PlantWatering(intervall, duration, PumpDevices)
        else:
            sysmessage = f"Unknown mode: {mode}"

        actionMap = OGBHydroPublication(
            Name=self.room,
            Mode=mode,
            Cycle=cycle,
            Active=isActive,
            Message=sysmessage,
            Intervall=intervall,
            Duration=duration,
            Devices=PumpDevices
        )
        await self.eventManager.emit("LogForClient", actionMap, haEvent=True)

    async def hydro_Mode(self, cycle: bool, interval: float, duration: float, pumpDevices, log_prefix: str = "Hydro"):
        """Handle hydro pump operations - for mistpump, waterpump, aeropump, dwcpump, rdwcpump."""
        
        valid_types = ["mistpump", "waterpump", "aeropump", "dwcpump", "rdwcpump","clonerpump"]
        devices = pumpDevices["devEntities"]
        active_pumps = [dev for dev in devices if dev in valid_types]
        await self.eventManager.emit("LogForClient", active_pumps, haEvent=True)

        if not active_pumps: return

        if not active_pumps:
            await self.eventManager.emit(
                "LogForClient",
                f"{log_prefix}: No valid pumps found.",
                haEvent=True
            )
            return

        async def run_cycle():
            try:
                while True:
                    # Turn ON all hydro pumps
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)
      
                    # Wait for duration (pumps ON)
                    await asyncio.sleep(float(duration))
                    
                    # Turn OFF all hydro pumps
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)

                    # Wait for interval (pumps OFF)
                    await asyncio.sleep(float(interval) * 60)
                    
            except asyncio.CancelledError:
                # If cancelled, ensure pumps are turned off
                for dev_id in active_pumps:
                    pumpAction = OGBHydroAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                    await self.eventManager.emit("PumpAction", pumpAction)
                raise

        # Cancel existing task if running
        if self._hydro_task is not None:
            self._hydro_task.cancel()
            try:
                await self._hydro_task
            except asyncio.CancelledError:
                pass
            self._hydro_task = None   
            
        if cycle:
            # Start cycling task
            self._hydro_task = asyncio.create_task(run_cycle())
            msg = (
                f"{log_prefix} mode started: ON for {duration}s, "
                f"OFF for {interval}m, repeating."
            )
        else:
            # One-time or permanent ON: just turn hydro pumps on
            for dev_id in active_pumps:
                pumpAction = OGBHydroAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                await self.eventManager.emit("PumpAction", pumpAction)
            msg = f"{log_prefix} cycle disabled – hydro pumps set to always ON."

        await self.eventManager.emit("LogForClient", msg, haEvent=True)
        
    async def hydro_PlantWatering(self,interval: float, duration: float, pumpDevices, cycle: bool = True,log_prefix: str = "Hydro"):
        valid_types = ["mistpump","waterpump","aeropump","dwcpump","rdwcpump"]
        devices = pumpDevices["devEntities"]
        active_pumps = [dev for dev in devices if any(t in dev for t in valid_types)]

        if not active_pumps:
            await self.eventManager.emit(
                "LogForClient",
                f"{log_prefix}: No valid pumps found.",
                haEvent=True
            )
            return

        async def run_cycle():
            try:
                while True:
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room,Action="on",Device=dev_id,Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)
                    await asyncio.sleep(float(duration))
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room,Action="off",Device=dev_id,Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)
                    await asyncio.sleep(float(interval)*60)
            except asyncio.CancelledError:
                # if we get cancelled, make sure pumps end up off
                for dev_id in active_pumps:
                    pumpAction = OGBHydroAction(Name=self.room,Action="off",Device=dev_id,Cycle=cycle)
                    await self.eventManager.emit("PumpAction", pumpAction)
                raise
        # If there's an existing task, cancel it
        if self._hydro_task is not None:
            self._hydro_task.cancel()
            try:
                await self._hydro_task
            except asyncio.CancelledError:
                pass
            self._hydro_task = None   
            
        if cycle:
            self._hydro_task = asyncio.create_task(run_cycle())
            msg = (
                f"{log_prefix} mode started: ON for {duration}s, "
                f"OFF for {interval}s, repeating."
            )
        else:
            return None

        await self.eventManager.emit("LogForClient", msg, haEvent=True)

    # Hydro Retrive 
    async def HydroModRetrieveChange(self, pumpAction):
        intervall_raw = self.dataStore.getDeep("Hydro.R_Intervall")
        duration_raw = self.dataStore.getDeep("Hydro.R_Duration")
        mode = self.dataStore.getDeep("Hydro.Retrieve")
        isActive = self.dataStore.getDeep("Hydro.R_Active")
        PumpDevices = self.dataStore.getDeep("capabilities.canPump")
        cycle = True

        if intervall_raw is None or duration_raw is None:
            return

        intervall = float(intervall_raw)
        duration = float(duration_raw)

        if mode is False:
            await self.eventManager.emit("RetrieveAction", {"action": "off"})
            if self._retrive_task is not None:
                self._retrive_task.cancel()
                self.dataStore.setDeep("Hydro.R_Active",False)
                try:
                    await self._retrive_task
                except asyncio.CancelledError:
                    pass
                self._retrive_task = None
            return

        sysmessage = "Hydro Retrive mode active"
        self.dataStore.setDeep("Hydro.R_Active",True)
        await self.retrive_Mode(cycle, intervall, duration, PumpDevices)

        actionMap = OGBRetrivePublication(
            Name=self.room,
            Cycle=cycle,
            Active=isActive,
            Mode=mode,
            Message=sysmessage,
            Intervall=intervall,
            Duration=duration,
            Devices=PumpDevices
        )
        await self.eventManager.emit("LogForClient", actionMap, haEvent=True)
            
    async def retrive_Mode(self, cycle: bool, interval: float, duration: float, pumpDevices, log_prefix: str = "Retrive"):
        """Handle retrive pump operations - only for retrievepump devices."""
        
        valid_types = ["retrievepump"]
        devices = pumpDevices["devEntities"]
        active_pumps = [dev for dev in devices if dev in valid_types]
        await self.eventManager.emit("LogForClient", active_pumps, haEvent=True)
  
        
        if not active_pumps: return

        if not active_pumps:
            await self.eventManager.emit(
                "LogForClient",
                f"{log_prefix}: No valid Retrive pumps found.",
                haEvent=True
            )
            return

        async def run_cycle():
            try:
                while True:
                    # Turn ON all retrive pumps
                    for dev_id in active_pumps:
                        retrieveAction = OGBRetrieveAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("RetrieveAction", retrieveAction)
                    
                    # Wait for duration (pumps ON)
                    await asyncio.sleep(float(duration))
                    
                    # Turn OFF all retrive pumps
                    for dev_id in active_pumps:
                        retrieveAction = OGBRetrieveAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("RetrieveAction", retrieveAction)
                    
                    # Wait for interval (pumps OFF)
                    await asyncio.sleep(float(interval) * 60)
                    
            except asyncio.CancelledError:
                # If cancelled, ensure pumps are turned off
                for dev_id in active_pumps:
                    retrieveAction = OGBRetrieveAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                    await self.eventManager.emit("RetrieveAction", retrieveAction)
                raise

        # Cancel existing task if running
        if self._retrive_task is not None:
            self._retrive_task.cancel()
            try:
                await self._retrive_task
            except asyncio.CancelledError:
                pass
            self._retrive_task = None   
            
        if cycle:
            # Start cycling task
            self._retrive_task = asyncio.create_task(run_cycle())
            msg = (
                f"{log_prefix} mode started: ON for {duration}s, "
                f"OFF for {interval}m, repeating."
            )
        else:
            # One-time or permanent ON: just turn retrive pumps on
            for dev_id in active_pumps:
                retrieveAction = OGBRetrieveAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                await self.eventManager.emit("RetrieveAction", retrieveAction)
            msg = f"{log_prefix} cycle disabled – retrive pumps set to always ON."

        await self.eventManager.emit("LogForClient", msg, haEvent=True)

    def log(self, log_message):
        """Logs the performed action."""
        logHeader = f"{self.name}"
        _LOGGER.debug(f" {logHeader} : {log_message} ")

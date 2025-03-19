import logging
import asyncio

from .OGBDataClasses.OGBPublications import OGBModePublication
from .OGBDataClasses.OGBPublications import OGBModeRunPublication

from .utils.calcs import calc_dew_vpd,calc_shark_mouse_vpd

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

        ## Events
        self.eventManager.on("selectActionMode", self.selectActionMode)
        
    async def selectActionMode(self, Publication):
        """
        Handhabt Änderungen des Modus basierend auf `tentMode`.
        """
        
        controlOption = self.dataStore.get("mainControl")
        if controlOption != "HomeAssistant": return
        
        #tentMode = self.dataStore.get("tentMode")
        if isinstance(Publication, OGBModePublication):
            return
        elif isinstance(Publication, OGBModeRunPublication):
            tentMode = Publication.currentMode
            #_LOGGER.warn(f"{self.name}: Run Mode {tentMode} for {self.room}")
        else:
            _LOGGER.warning(f"Unbekannter Datentyp: {type(Publication)} - Daten: {Publication}")      
       
        if tentMode == "VPD Perfection":
            await self.handle_vpd_perfection()
        elif tentMode == "In VPD Range":
            await self.handle_in_range_vpd()
        elif tentMode == "Targeted VPD":
            await self.handle_targeted_vpd()
        elif tentMode == "P.I.D Control":
            await self.handle_pid_control()
        elif tentMode == "M.P.C Control":
            await self.handle_mpc_control()
        elif tentMode == "Drying":
            await self.handle_drying()
        elif tentMode == "Disabled":
            await self.handle_disabled_mode()
        else:
            _LOGGER.warn(f"{self.name}: Unbekannter Modus {tentMode}")

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
            _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is below minimum ({perfectionMinVPD}). Increasing VPD.")
            await self.eventManager.emit("increase_vpd",capabilities)
            await self.eventManager.emit("LogForClient",{"Name":self.room,"Action":"VPD Check Increasing","currentVPD:":currentVPD,"perfectionVPD":perfectionVPD},haEvent=True)
        elif currentVPD > perfectionMaxVPD:
            _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is above maximum ({perfectionMaxVPD}). Reducing VPD.")
            await self.eventManager.emit("reduce_vpd",capabilities)
            await self.eventManager.emit("LogForClient",{"Name":self.room,"Action":"VPD Check Reducing","currentVPD:":currentVPD,"perfectionVPD":perfectionVPD},haEvent=True)
        elif currentVPD != perfectionVPD:
            _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is within range but not at perfection ({perfectionVPD}). Fine-tuning.")
            await self.eventManager.emit("FineTune_vpd",capabilities)
            await self.eventManager.emit("LogForClient",{"Name":self.room,"Action":"VPD Check Fine-Tune","currentVPD:":currentVPD,"perfectionVPD":perfectionVPD},haEvent=True)
        else:
            _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is at perfection ({perfectionVPD}). No action required.")

    async def handle_in_range_vpd(self):
        """
        Handhabt den Modus 'VPD Perfection'.
        """
        _LOGGER.warn(f"ModeManger: {self.room} Running 'In VPD Range'")
        
        currentVPD = self.dataStore.getDeep("vpd.current")
        rangeVPD = self.dataStore.getDeep("vpd.range")
        minVPD = rangeVPD[0]
        maxVPD = rangeVPD[1]
        # Verfügbare Capabilities abrufen
        capabilities = self.dataStore.getDeep("capabilities")
        if currentVPD < minVPD:
            _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is below minimum ({minVPD}). Increasing VPD.")
            await self.eventManager.emit("increase_vpd",capabilities)
        elif currentVPD > maxVPD:
            _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is above maximum ({maxVPD}). Reducing VPD.")
            await self.eventManager.emit("reduce_vpd",capabilities)
        else:
            _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is in Range. Between {minVPD} and {maxVPD}  No action required.")        
         
        pass

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

            _LOGGER.debug(f"{self.room}: Targeted VPD: {targetedVPD}, Tolerance: {tolerance_percent}% "
                        f"-> Min: {min_vpd}, Max: {max_vpd}, Current: {currentVPD}")

            # Verfügbare Capabilities abrufen
            capabilities = self.dataStore.getDeep("capabilities")

            # VPD steuern basierend auf der Toleranz
            if currentVPD < min_vpd:
                _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is below minimum ({min_vpd}). Increasing VPD.")
                await self.eventManager.emit("increase_vpd", capabilities)
            elif currentVPD > max_vpd:
                _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is above maximum ({max_vpd}). Reducing VPD.")
                await self.eventManager.emit("reduce_vpd", capabilities)
            elif currentVPD != targetedVPD:
                _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is within range but not at Targeted ({targetedVPD}). Fine-tuning.")
            else:
                _LOGGER.warn(f"{self.room}: Current VPD ({currentVPD}) is within tolerance range ({min_vpd} - {max_vpd}). No action required.")
        
        except ValueError as e:
            _LOGGER.error(f"ModeManager: Fehler beim Konvertieren der VPD-Werte oder Toleranz in Zahlen. {e}")
        except Exception as e:
            _LOGGER.error(f"ModeManager: Unerwarteter Fehler in 'handle_targeted_vpd': {e}")


    ## Advanced MOdes
    async def handle_pid_control(self):
        """
        Handhabt den Modus 'P.I.D Control'.
        """
        _LOGGER.info(f"ModeManger: {self.room} Modus 'P.I.D Control' aktiviert.")
        await asyncio.sleep(0.001)
        # Füge hier spezifische Logik für diesen Modus ein
        pass

    async def handle_mpc_control(self):
        """
        Handhabt den Modus 'M.P.C Control'.
        """
        _LOGGER.info(f"ModeManger: {self.room} Modus 'M.P.C Control' aktiviert.")
        await asyncio.sleep(0.001)
        # Füge hier spezifische Logik für diesen Modus ein
        pass
      

    ## Drying Modes
    async def handle_drying(self):
        """
        Handhabt den Modus 'Drying'.
        """
        currentDryMode = self.dataStore.getDeep("drying.currentDryMode")
        
        if currentDryMode == "ElClassico":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}")  
            await self.handle_ElClassico(phaseConfig)
        elif currentDryMode == "SharkMouse":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}") 
            await self.handle_ElClassico(phaseConfig)
        elif currentDryMode == "DewBased":
            phaseConfig = self.dataStore.getDeep(f"drying.modes.{currentDryMode}") 
            await self.handle_DewBased(phaseConfig)
        else:
            _LOGGER.warn(f"{self.name} Unknown DryMode Recieved")           
            return None

    async def handle_ElClassico(self,phaseConfig):
        _LOGGER.warn(f"{self.name} Run Drying 'El Classico'")          
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
        _LOGGER.info(f"{self.name}: El Classico Phase {currentPhase}, Aktionen: {actions}")

    async def handle_SharkMouse(self,phaseConfig):
        _LOGGER.warn(f"{self.name} Run Drying 'Shark Mouse'")  
        tentData = self.dataStore.get("tentData")
        vpdTolerance = self.dataStore.get("vpd.tolerance")
        sharkMouseVPD = calc_shark_mouse_vpd(tentData["temperatures"],tentData["humidity"])
       
        # Verfügbare Capabilities abrufen
        capabilities = self.dataStore.getDeep("capabilities")
       
        #Anpassungen bassierend auf VPD
        if abs(sharkMouseVPD - phaseConfig['targetVPD']) > vpdTolerance:
            if sharkMouseVPD < phaseConfig['targetVPD']: 
                _LOGGER.warn(f"{self.room}: SharkMouse VPD ({sharkMouseVPD}) nened to 'Increase' for Reaching {phaseConfig['targetVPD']}")
                await self.eventManager.emit("increase_vpd",capabilities)
            elif sharkMouseVPD > phaseConfig['targetVPD']:
                _LOGGER.warn(f"{self.room}: SharkMouse VPD ({sharkMouseVPD}) nened to 'Reduce' for Reaching {phaseConfig['targetVPD']}")
                await self.eventManager.emit("reduce_vpd",capabilities)
            else:
                _LOGGER.warn(f"{self.room}: SharkMouse VPD ({sharkMouseVPD}) Is on Spot. No action required.")
                
    async def handle_DewBased(self,phaseConfig):
        _LOGGER.warn(f"{self.name}: Run Drying 'Dew Based'")

        tentData = self.dataStore.get("tentData")
        dewPointTolerance = 0.5  # Toleranz für Taupunkt
        vaporPressureActual = self.dataStore.getDeep("drying.vaporPressureActual")
        vaporPressureSaturation = self.dataStore.getDeep("drying.vaporPressureSaturation")

        currentDewPoint = tentData["dewpoint"]
    
            # Sicherstellen, dass der Taupunkt eine gültige Zahl ist
        if not isinstance(currentDewPoint, (int, float)) or currentDewPoint is None or currentDewPoint != currentDewPoint:  # NaN-Check
            _LOGGER.warn(f"{self.name}: Current Dew Point is unavailable or invalid.")
            return None
        if (abs(currentDewPoint - phaseConfig["targetDewPoint"]) > dewPointTolerance or vaporPressureActual < 0.9 * vaporPressureSaturation or vaporPressureActual > 1.1 * vaporPressureSaturation):       
            if currentDewPoint < phaseConfig["targetDewPoint"] or vaporPressureActual < 0.9 * vaporPressureSaturation:
                await self.eventManager.emit("Increase Humidifier", None)
                await self.eventManager.emit("Increase Exhaust", None)
                await self.eventManager.emit("Increase Ventilation", None)
                _LOGGER.warn(f"{self.room}: Dew Point ({currentDewPoint}) below target ({phaseConfig['targetDewPoint']}). Actions: Increase humidity.")
            elif currentDewPoint > phaseConfig["targetDewPoint"] or vaporPressureActual > 1.1 * vaporPressureSaturation:
                await self.eventManager.emit("Increase Dehumidifier", None)
                await self.eventManager.emit("Increase Exhaust", None)
                await self.eventManager.emit("Increase Ventilation", None)
                _LOGGER.warn(f"{self.room}: Dew Point ({currentDewPoint}) above target ({phaseConfig['targetDewPoint']}). Actions: Reduce humidity.")
        else:
            _LOGGER.warn(f"{self.room}: Dew Point ({currentDewPoint}) is within tolerance range. No actions required.")


    def log(self, log_message):
        """Logs the performed action."""
        logHeader = f"{self.name}"
        _LOGGER.warn(f" {logHeader} : {log_message} ")

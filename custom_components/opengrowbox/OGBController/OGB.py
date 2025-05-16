import math
import logging
import asyncio
from datetime import datetime, time, timedelta

from .utils.calcs import calculate_avg_value,calculate_dew_point,calculate_current_vpd,calculate_perfect_vpd

from .OGBDataClasses.OGBPublications import OGBInitData,OGBEventPublication,OGBVPDPublication
from .OGBDataClasses.OGBPublications import OGBModePublication,OGBModeRunPublication,OGBCO2Publication

# OGB IMPORTS
from .OGBDataClasses.OGBData import OGBConf

from .RegistryListener import OGBRegistryEvenListener
from .OGBDatastore import DataStore
from .OGBEventManager import OGBEventManager
from .OGBDeviceManager import OGBDeviceManager
from .OGBModeManager import OGBModeManager
from .OGBActionManager import OGBActionManager

_LOGGER = logging.getLogger(__name__)

class OpenGrowBox:
    def __init__(self, hass, room):
        self.name = "OGB Controller"
        self.hass = hass
        self.room = room

        # Erstelle das zentrale Modell
        self.ogbConfig = OGBConf(hass=self.hass,room=self.room)
        
        # Nutze Singleton-Instanz von DataStore
        self.dataStore = DataStore(self.ogbConfig)

        # Initialisiere EventManager
        self.eventManager = OGBEventManager(self.hass, self.dataStore)

        # Registry Listener für HA Events
        self.registryListener = OGBRegistryEvenListener(self.hass, self.dataStore, self.eventManager, self.room)

        # Initialisiere Manager mit geteiltem Modell
        self.deviceManager = OGBDeviceManager(self.hass, self.dataStore, self.eventManager,self.room,self.registryListener)
        self.modeManager = OGBModeManager(self.hass,self.dataStore, self.eventManager, self.room)
        self.actionManager = OGBActionManager(self.hass, self.dataStore, self.eventManager,self.room)

        #Events Register
        self.eventManager.on("RoomUpdate", self.handleRoomUpdate)
        self.eventManager.on("VPDCreation", self.handleNewVPD)
        
        #LightSheduleUpdate
        self.eventManager.on("LightSheduleUpdate", self.lightSheduleUpdate)
        
        # Plant Times
        self.eventManager.on("PlantTimeChange",self._autoUpdatePlantStages)

            
        
    def __str__(self):
        return (f"{self.name}' Running")
    
    def __repr__(self):
        return (f"{self.name}' Running")  
    
    async def start(self,data):
        try:
            await self.eventManager.emit("OGBStart","START")
            _LOGGER.warn("OpenGrowBox started successfully.")
            return True
        except Exception as e:
            _LOGGER.error(f"Error during Start: {e}")
            return False
    
    async def stop(self,data):
        try:
            await self.eventManager.emit("OGBStop","STOP")
            _LOGGER.info("OpenGrowBox stopped successfully.")
            return True
        except Exception as e:
            _LOGGER.error(f"Error during Stop: {e}")
            return False
    
    async def pause(self,data):
        try:
            await self.eventManager.emit("OGBPause","PAUSE")
            _LOGGER.info("OpenGrowBox paused successfully.")
            return True
        except Exception as e:
            _LOGGER.error(f"Error during Pause: {e}")
            return False

    ## INIT 
    async def firstInit(self):
        _LOGGER.debug(f"OpenGrowBox for {self.room} started successfully State:{self.dataStore}")
        await asyncio.sleep(0)
        return True

    async def handleRoomUpdate(self, entity):
        """
        Update WorkData für Temperatur oder Feuchtigkeit basierend auf einer Entität.
        Ignoriere Entitäten, die 'ogb_' im Namen enthalten.
        """
        # Entitäten mit 'ogb_' im Namen überspringen
        if "ogb_" in entity.Name:
            await self.manager(entity)
            return

        temps = self.dataStore.getDeep("workData.temperature")
        hums = self.dataStore.getDeep("workData.humidity")
        vpd = self.dataStore.getDeep("vpd.current")
        vpdNeeds = ("temperature", "humidity")
        
        # Prüfe, ob die Entität für Temperatur oder Feuchtigkeit relevant ist
        if any(need in entity.Name for need in vpdNeeds):
            # Bestimme, ob es sich um Temperatur oder Feuchtigkeit handelt
            if "temperature" in entity.Name:
                # Update Temperaturdaten
                temps = self._update_work_data_array(temps, entity)
                self.dataStore.setDeep("workData.temperature", temps)
                VPDPub = OGBVPDPublication(Name="HumUpdate",VPD=vpd,AvgDew=None,AvgHum=None,AvgTemp=None)
                await self.eventManager.emit("VPDCreation",VPDPub)
                _LOGGER.debug(f"{self.room} OGB-Manager: Temperaturdaten aktualisiert {temps}")

            elif "humidity" in entity.Name:
                # Update Feuchtigkeitsdaten
                hums = self._update_work_data_array(hums, entity)
                self.dataStore.setDeep("workData.humidity", hums)
                VPDPub = OGBVPDPublication(Name="HumUpdate",VPD=vpd,AvgDew=None,AvgHum=None,AvgTemp=None)
                await self.eventManager.emit("VPDCreation",VPDPub)
                _LOGGER.debug(f"{self.room} OGB-Manager: Feuchtigkeitsdaten aktualisiert {hums}")

            #elif "moisture" in entity.Name:
            #    # Update Feuchtigkeitsdaten
            #    hums = self._update_work_data_array(hums, entity)
            #    self.dataStore.setDeep("workData.moisture", hums)
            #    VPDPub = OGBVPDPublication(Name="MoistureUpdate",VPD=vpd,AvgDew=None,AvgHum=None,AvgTemp=None)
            #    await self.eventManager.emit("VPDCreation",VPDPub)
            #    _LOGGER.debug(f"{self.room} OGB-Manager: Feuchtigkeitsdaten aktualisiert {hums}")


            elif "co2" in entity.Name:
                # Update Feuchtigkeitsdaten
                self.dataStore.setDeep("tentData.co2Level",entity.newState)
                self.dataStore.setDeep("tcontrolOptionData.co2ppm.current",entity.newState)
                
                minPPM =  self.dataStore.getDeep("controlOptionData.co2ppm.minPPM")
                maxPPM =  self.dataStore.getDeep("controlOptionData.co2ppm.maxPPM")
                targetPPM =  self.dataStore.getDeep("controlOptionData.co2ppm.target")
                currentPPM =  self.dataStore.getDeep("controlOptionData.co2ppm.current")


                co2Publication = OGBCO2Publication(Name="CO2",co2Current=currentPPM,co2Target=targetPPM,minCO2=minPPM,maxCO2=maxPPM)
                await self.eventManager.emit("NewCO2Publication",co2Publication)                                
                _LOGGER.debug(f"{self.room} OGB-Manager: CO2 Daten aktualisiert {currentPPM}")               

    async def managerInit(self,ogbEntity):
        for entity in ogbEntity['entities']:
            entity_id = entity['entity_id']
            value = entity['value']
            entityPublication = OGBInitData(Name=entity_id,newState=[value])
            await self.manager(entityPublication) 
        await self.backWork()  
        
    async def manager(self, data):
        """
        Verwalte Aktionen basierend auf den eingehenden Daten mit einer Mapping-Strategie.
        """

        # Entferne Präfixe vor dem ersten Punkt
        entity_key = data.Name.split(".", 1)[-1].lower()

        # Mapping von Namen zu Funktionen
        actions = {
            # Basics
            f"ogb_maincontrol_{self.room.lower()}": self._update_control_option,
            f"ogb_notifications_{self.room.lower()}": self._update_notify_option,
            
            f"ogb_vpdtolerance_{self.room.lower()}": self._update_vpd_tolerance,
            f"ogb_plantstage_{self.room.lower()}": self._update_plant_stage,
            f"ogb_tentmode_{self.room.lower()}": self._update_tent_mode, 
            f"ogb_leaftemp_offset_{self.room.lower()}": self._update_leafTemp_offset,
            f"ogb_vpdtarget_{self.room.lower()}": self._update_vpd_Target,                          

            # LightTimes
            f"ogb_lightontime_{self.room.lower()}": self._update_lightOn_time,
            f"ogb_lightofftime_{self.room.lower()}": self._update_lightOff_time,
            f"ogb_sunrisetime_{self.room.lower()}": self._update_sunrise_time,
            f"ogb_sunsettime_{self.room.lower()}": self._update_sunset_time,
            
            # Control Settings
            f"ogb_lightcontrol_{self.room.lower()}": self._update_ogbLightControl_control,
            f"ogb_holdvpdnight_{self.room.lower()}": self._update_vpdNightHold_control,
            f"ogb_vpdlightcontrol_{self.room.lower()}": self._update_vpdLight_control,
            
            # CO2-Steuerung
            f"ogb_co2_control_{self.room.lower()}": self._update_co2_control,
            f"ogb_co2targetvalue_{self.room.lower()}": self._update_co2Target_value,
            f"ogb_co2minvalue_{self.room.lower()}": self._update_co2Min_value,
            f"ogb_co2maxvalue_{self.room.lower()}": self._update_co2Max_value,  
            
            # Weights
            f"ogb_ownweights_{self.room.lower()}": self._update_ownWeights_control,
            f"ogb_temperatureweight_{self.room.lower()}": self._update_temperature_weight,
            f"ogb_humidityweight_{self.room.lower()}": self._update_humidity_weight,
            
            # PlantDates
            f"ogb_breederbloomdays_{self.room.lower()}": self._update_breederbloomdays_value,
            f"ogb_growstartdate_{self.room.lower()}": self._update_growstartdates_value,
            f"ogb_bloomswitchdate_{self.room.lower()}": self._update_bloomswitchdate_value,
            #f"ogb_planttotaldays{self.room.lower()}": self._updatePlantDates,
            #f"ogb_totalbloomdays{self.room.lower()}": self._updatePlantDates,

            # Drying
            f"ogb_minmax_control_{self.room.lower()}": self._update_MinMax_control, 
            f"ogb_mintemp_{self.room.lower()}": self._update_minTemp,
            f"ogb_minhum_{self.room.lower()}": self._update_minHumidity,
            f"ogb_maxtemp_{self.room.lower()}": self._update_maxTemp,
            f"ogb_maxhum_{self.room.lower()}": self._update_maxHumidity,
            
            # Hydro           
            f"ogb_hydro_mode_{self.room.lower()}": self._update_hydro_mode,
            f"ogb_hydro_cycle_{self.room.lower()}": self._update_hydro_mode_cycle,
            f"ogb_hydropumpduration_{self.room.lower()}": self._update_hydro_duration,
            f"ogb_hydropumpintervall_{self.room.lower()}": self._update_hydro_intervall,          

            # Ambient Borrow Feature
            #f"ogb_ambientborrow_{self.room.lower()}": self._update_ambientBorrow_control,
           
        }

        # Überprüfe, ob der Schlüssel in der Mapping-Tabelle vorhanden ist
        action = actions.get(entity_key)
        if action:
            await action(data)  # Rufe die zugehörige Aktion mit `data` auf
        else:
            _LOGGER.warn(f"OGB-Manager {self.room}: Keine Aktion für {entity_key} gefunden.")
 
 
    ## VPD Sensor Update
    async def handleNewVPD(self, data):

        controlOption = self.dataStore.get("mainControl")
        if controlOption != "HomeAssistant": return
        
        
        # Temperatur- und Feuchtigkeitsdaten laden
        temps = self.dataStore.getDeep("workData.temperature")
        hums = self.dataStore.getDeep("workData.humidity")
        leafTempOffset = self.dataStore.getDeep("tentData.leafTempOffset")
        
        # Durchschnittswerte asynchron berechnen
        avgTemp = calculate_avg_value(temps)
        self.dataStore.setDeep("tentData.temperature", avgTemp)
        avgHum = calculate_avg_value(hums)
        self.dataStore.setDeep("tentData.humidity", avgHum)

        # Taupunkt asynchron berechnen
        avgDew = calculate_dew_point(avgTemp, avgHum) if avgTemp != "unavailable" and avgHum != "unavailable" else "unavailable"
        self.dataStore.setDeep("tentData.dewpoint", avgDew)

        lastVpd = self.dataStore.getDeep("vpd.current")
        currentVPD = calculate_current_vpd(avgTemp, avgHum, leafTempOffset)        
        
        if isinstance(data, OGBInitData):
            _LOGGER.debug(f"OGBInitData erkannt: {data}")
        else:
            _LOGGER.debug(f"OGBEventPublication erkannt: {data}")
            # Spezifische Aktion für OGBEventPublication
            if lastVpd != currentVPD:
                self.dataStore.setDeep("vpd.current", currentVPD)
                vpdPub = OGBVPDPublication(Name=self.room, VPD=currentVPD, AvgTemp=avgTemp, AvgHum=avgHum, AvgDew=avgDew)
                await self.update_sensor_via_service(vpdPub)
                _LOGGER.warn(f"New-VPD: {vpdPub} newStoreVPD:{currentVPD}, lastStoreVPD:{lastVpd}")
                tentMode = self.dataStore.get("tentMode")
                runMode = OGBModeRunPublication(currentMode=tentMode)               
                await self.eventManager.emit("selectActionMode",runMode)
                await self.eventManager.emit("LogForClient",vpdPub,haEvent=True)          
          
                self._DEBUGSTATE()
                return vpdPub     

    async def update_sensor_via_service(self,vpdPub):
        """
        Update Wert eines Sensors über den Home Assistant Service `update_sensor`.
        """
        vpd_value = vpdPub.VPD
        temp_value = vpdPub.AvgTemp        
        hum_value = vpdPub.AvgHum
        dew_value = vpdPub.AvgDew
        vpd_entity = f"sensor.ogb_currentvpd_{self.room.lower()}"  
        avgTemp_entity = f"sensor.ogb_avgtemperature_{self.room.lower()}"  
        avgHum_entity = f"sensor.ogb_avghumidity_{self.room.lower()}"          
        avgDew_entity = f"sensor.ogb_avgdewpoint_{self.room.lower()}"         
        
        
        try:
            # Überprüfe, ob der Wert gültig ist
            new_vpd_value = vpd_value if vpd_value not in (None, "unknown", "unbekannt") else 0.0
            # Rufe den Service auf
            await self.hass.services.async_call(
                domain="opengrowbox",  # Dein Custom Domain-Name
                service="update_sensor",
                service_data={
                    "entity_id": vpd_entity,
                    "value": new_vpd_value
                },
                blocking=True  # Optional: Warte auf Abschluss des Service-Aufrufs
            )
            new_temp_value = temp_value if temp_value not in (None, "unknown", "unbekannt") else 0.0            
            await self.hass.services.async_call(
                domain="opengrowbox",  # Dein Custom Domain-Name
                service="update_sensor",
                service_data={
                    "entity_id": avgTemp_entity,
                    "value": new_temp_value
                },
                blocking=True  # Optional: Warte auf Abschluss des Service-Aufrufs
            )
            new_hum_value = hum_value if hum_value not in (None, "unknown", "unbekannt") else 0.0                        
            await self.hass.services.async_call(
                domain="opengrowbox",  # Dein Custom Domain-Name
                service="update_sensor",
                service_data={
                    "entity_id": avgHum_entity,
                    "value": new_hum_value
                },
                blocking=True  # Optional: Warte auf Abschluss des Service-Aufrufs
            )            
            new_dew_value = dew_value if dew_value not in (None, "unknown", "unbekannt") else 0.0   
            await self.hass.services.async_call(
                domain="opengrowbox",  # Dein Custom Domain-Name
                service="update_sensor",
                service_data={
                    "entity_id": avgDew_entity,
                    "value": new_dew_value
                },
                blocking=True  # Optional: Warte auf Abschluss des Service-Aufrufs
            )           
            _LOGGER.debug(f"Sensor '{vpd_entity}' updated via service with value: {vpd_entity}")
        except Exception as e:
            _LOGGER.error(f"Failed to update sensor '{vpd_entity}' via service: {e}")

    async def lightSheduleUpdate(self,data):
        lightbyOGBControl = self.dataStore.getDeep("controlOptions.lightbyOGBControl")
        if lightbyOGBControl == False: return
        
        isLightNowON = self.dataStore.getDeep("isPlantDay.islightON")
        lightChange = await self.update_light_state()


        if lightChange == None: return
        self.dataStore.setDeep("isPlantDay.islightON",lightChange)
        _LOGGER.debug(f"{self.name}: Lichtstatus geprüft und aktualisiert für {self.room} Lichstatus ist {lightChange}")
        
        await self.eventManager.emit("toggleLight",lightChange)
            
    async def update_minMax_settings(self):
        """
        Update Wert eines number-Entities über den Home Assistant Service `number.set_value`.
        """
        minTemp_entity = f"number.ogb_mintemp_{self.room.lower()}"  
        maxTemp_entity = f"number.ogb_maxtemp_{self.room.lower()}" 
        minHum_entity = f"number.ogb_minhum_{self.room.lower()}"  
        maxHum_entity = f"number.ogb_maxhum_{self.room.lower()}"  
        
        currentPlantStage = self.dataStore.get("plantStage")    
        PlantStageValues = self.dataStore.getDeep(f"plantStages.{currentPlantStage}")
        
        minTemp_value = PlantStageValues["minTemp"]     
        maxTemp_value = PlantStageValues["maxTemp"]
        minHum_value = PlantStageValues["minHumidity"]
        maxHum_value = PlantStageValues["maxHumidity"]
        
        _LOGGER.info(f"Setting defaults for stage '{currentPlantStage}': {PlantStageValues}")

        try:
            async def set_value(entity_id, value):
                safe_value = value if value not in (None, "unknown", "unbekannt") else 0.0
                await self.hass.services.async_call(
                    domain="number",
                    service="set_value",
                    service_data={
                        "entity_id": entity_id,
                        "value": safe_value
                    },
                    blocking=True
                )
                _LOGGER.debug(f"Updated {entity_id} to {safe_value}")

            await set_value(minTemp_entity, minTemp_value)
            await set_value(maxTemp_entity, maxTemp_value)
            await set_value(minHum_entity, minHum_value)
            await set_value(maxHum_entity, maxHum_value)

        except Exception as e:
            _LOGGER.error(f"Failed to update min/max values: {e}")        

    async def backWork(self):
        asyncio.sleep(1)
        value = "CHECK"
        HydroData = self.dataStore.get("Hydro")
        PumpDevice = self.dataStore.getDeep("capabilities.canPump")
        _LOGGER.error(f" {self.room} Check Hydro Settings For Start {HydroData} and {PumpDevice} ")
        await self.eventManager.emit("HydroModeChange",value)
   
   
    # Helpers
    def _stringToBool(self,stringToBool):
        if stringToBool == "YES":
            return True
        if stringToBool == "NO":
            return False
   
    def _update_work_data_array(self, data_array, entity):
        """
        Aktualisiert die WorkData-Array basierend auf der übergebenen Entität.
        :param data_array: Array mit bestehenden Daten.
        :param entity: Entität mit Name und neuen Werten.
        :return: Aktualisiertes Array.
        """
        # Suche nach bestehendem Eintrag
        found = False
        for item in data_array:
            if item["entity_id"] == entity.Name:
                item["value"] = entity.newState[0]  # Setze den neuen Wert
                found = True
                break

        # Falls kein bestehender Eintrag, neuen hinzufügen
        if not found:
            data_array.append({
                "entity_id": entity.Name,
                "value": entity.newState[0]
            })

        return data_array

    async def _plantStageToVPD(self):
        """
        Aktualisiert die VPD-Werte basierend auf dem Pflanzenstadium.
        """
        plantStage = self.dataStore.get("plantStage")
        # Daten aus dem `plantStages`-Dictionary abrufen
        stageValues = self.dataStore.getDeep(f"plantStages.{plantStage}")
        if not stageValues:
            _LOGGER.error(f"{self.room}: Keine Daten für PlantStage '{plantStage}' gefunden.")
            return

        try:
            # Werte aus dem Dictionary extrahieren
            vpd_range = stageValues["vpdRange"]
            max_temp = stageValues["maxTemp"]
            min_temp = stageValues["minTemp"]
            max_humidity = stageValues["maxHumidity"]
            min_humidity = stageValues["minHumidity"]

            tolerance = self.dataStore.getDeep("vpd.tolerance")
            perfections = calculate_perfect_vpd(vpd_range,tolerance)
            
            perfectVPD = perfections["perfection"]
            perfectVPDMin = perfections["perfect_min"]
            perfectVPDMax = perfections["perfect_max"]          


            # Werte in `dataStore` setzen
            self.dataStore.setDeep("vpd.range", vpd_range)
            self.dataStore.setDeep("tentData.maxTemp", max_temp)
            self.dataStore.setDeep("tentData.minTemp", min_temp)
            self.dataStore.setDeep("tentData.maxHumidity", max_humidity)
            self.dataStore.setDeep("tentData.minHumidity", min_humidity)

        
            self.dataStore.setDeep("vpd.perfection",perfectVPD)
            self.dataStore.setDeep("vpd.perfectMin",perfectVPDMin)
            self.dataStore.setDeep("vpd.perfectMax",perfectVPDMax)

            await self.update_minMax_settings()
            _LOGGER.debug(f"{self.room}: PlantStage '{plantStage}' erfolgreich in VPD-Daten übertragen.")
        except KeyError as e:
            _LOGGER.error(f"{self.room}: Fehlender Schlüssel in PlantStage-Daten '{e}'")
        except Exception as e:
            _LOGGER.error(f"{self.room}: Fehler beim Verarbeiten der PlantStage-Daten: {e}")

    async def update_light_state(self):
        """
        Update Status von `lightOn`, basierend auf den Lichtzeiten.
        """

        lightOnTime = self.dataStore.getDeep("isPlantDay.lightOnTime")
        lightOffTime = self.dataStore.getDeep("isPlantDay.lightOffTime")

        try:
            if lightOnTime == "" or lightOffTime == "":
                _LOGGER.debug("Lichtzeiten fehlen. Bitte sicherstellen, dass 'lightOnTime' und 'lightOffTime' gesetzt sind.")
                return None

            # Konvertiere Zeitstrings in `time`-Objekte
            light_on_time = datetime.strptime(lightOnTime, "%H:%M:%S").time()
            light_off_time = datetime.strptime(lightOffTime, "%H:%M:%S").time()

            # Hole die aktuelle Zeit
            current_time = datetime.now().time()

            # Prüfe, ob die aktuelle Zeit im Bereich liegt
            if light_on_time < light_off_time:
                # Normaler Zyklus (z. B. 08:00 bis 20:00)
                is_light_on = light_on_time <= current_time < light_off_time
  
            else:
                # Über Mitternacht (z. B. 20:00 bis 08:00)
                is_light_on = current_time >= light_on_time or current_time < light_off_time
   
            # Update Status im DataStore
            return is_light_on

        except Exception as e:
            _LOGGER.error(f"{self.room} Fehler beim Updaten des Lichtstatus: {e}")       

    async def defaultState(self):
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False:
            controlValues = self._stringToBool(self.dataStore.getDeep("controlOptionData.minmax"))
            controlValues.minTemp = None
            controlValues.minHum = None
            controlValues.maxTemp = None
            controlValues.maxHum = None
            self.dataStore.setDeep("controlOptionData.minmax",controlValues)

   
    ## Controll Update Functions 
    async def _update_control_option(self,data):
        """
        Update ControlOption.
        """
        value = data.newState[0]
        current_stage = self.dataStore.get("mainControl")
        if current_stage != value:
            self.dataStore.set("mainControl",value)
            _LOGGER.warn(f"{self.room}: Steuerung geändert von {current_stage} auf {value}")
            await self.eventManager.emit("mainControlChange",value)

    async def _update_notify_option(self,data):
        """
        Udpate Notify Option.
        """
        value = data.newState[0]
        current_state = self.dataStore.get("notifyControl")
        self.dataStore.set("notifyControl",value)
        _LOGGER.warn(f"{self.room}: Notification geändert von {current_state} auf {value}")
        if value == "Disabled":
            self.eventManager.change_notify_set(False)
        elif value == "Enabled":
            self.eventManager.change_notify_set(True)

    ## MAIN Updaters
    async def _update_plant_stage(self, data):
        """
        Update Pflanzenphase.
        """
        value = data.newState[0]
        current_stage = self.dataStore.get("plantStage")
        if current_stage != value:
            self.dataStore.set("plantStage",value)
            await self._plantStageToVPD()
            _LOGGER.warn(f"{self.room}: Pflanzenphase geändert von {current_stage} auf {value}")
            await self.eventManager.emit("PlantStageChange",value)
  
    async def _update_tent_mode(self, data):
        """
        Update Zeltmodus.
        """
        
        value = data.newState[0]
        current_mode = self.dataStore.get("tentMode")
        
        if isinstance(data, OGBInitData):
            _LOGGER.debug(f"OGBInitData erkannt: {data}")
            self.dataStore.set("tentMode",value)
        elif isinstance(data, OGBEventPublication):
            if value == "": return
            if current_mode != value:
                tentModePublication = OGBModePublication(currentMode=value,previousMode=current_mode)
                _LOGGER.warn(f"{self.room}: Zeltmodus geändert von {current_mode} auf {value}")
                self.dataStore.set("tentMode",value)
                ## Event to Mode Manager 
                await self.eventManager.emit("selectActionMode",tentModePublication)
        else:
            _LOGGER.warning(f"Unbekannter Datentyp: {type(data)} - Daten: {data}")      

    async def _update_leafTemp_offset(self, data):
        """
        Update Blatt Temp Offset.
        """
        value = data.newState[0]
        current_stage = self.dataStore.getDeep("tentData.leafTempOffset")
        
        if isinstance(data, OGBInitData):
            _LOGGER.debug(f"OGBInitData erkannt: {data}")
            self.dataStore.setDeep("tentData.leafTempOffset",value)
        elif isinstance(data, OGBEventPublication):
            if current_stage != value:
                _LOGGER.warn(f"{self.room}: BlattTemp Offset geändert von {current_stage} auf {value}")
                self.dataStore.setDeep("tentData.leafTempOffset",value)
                await self.eventManager.emit("VPDCreation",value)
        else:
            _LOGGER.warning(f"Unbekannter Datentyp: {type(data)} - Daten: {data}")     

    async def _update_vpd_Target(self,data):
        """
        Update Licht Steuerung durch VPD 
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("vpd.targeted")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Target VPD auf {value}")
            self.dataStore.setDeep("vpd.targeted", value)
            await asyncio.sleep(0)

    async def _update_vpd_tolerance(self,data):
        """
        Update VPD Tolerance
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("vpd.tolerance")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: VPD Tolerance Aktualisiert auf {value}")
            self.dataStore.setDeep("vpd.tolerance",value)

    # Lights
    async def _update_lightOn_time(self,data):
        """
        Update Licht Zeit AN
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.lightOnTime")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Licht 'AN' Aktualisiert auf  {value}")
            self.dataStore.setDeep("isPlantDay.lightOnTime",value)

    async def _update_lightOff_time(self,data):
        """
        Update Licht Zeit AUS
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.lightOffTime")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Licht 'AUS' Aktualisiert auf  {value}")
            self.dataStore.setDeep("isPlantDay.lightOffTime",value)
            
    async def _update_sunrise_time(self,data):
        """
        Update Sonnen Aufgang Zeitpunkt
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.sunRiseTime")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Sonnenaufgang endet {value} nach Licht An")
            self.dataStore.setDeep("isPlantDay.sunRiseTime",value)
            await self.eventManager.emit("SunRiseTimeUpdates",value)

    async def _update_sunset_time(self,data):
        """
        Update Sonnen Untergang Zeitpunkt
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.sunSetTime")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Sonnenuntergang beginnt {value} vor Licht Aus")
            self.dataStore.setDeep("isPlantDay.sunSetTime",value)
            await self.eventManager.emit("SunSetTimeUpdates",value)

    ##MINMAX Values
    async def _update_MinMax_control(self,data):
        """
        Update OGB Light Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if current_value != value:
            boolValue = self._stringToBool(value)
            if boolValue == False:
                await self.update_minMax_settings()
                #self.defaultState()
            _LOGGER.warn(f"{self.room}: Update MinMax Control auf {value}")
            self.dataStore.setDeep("controlOptions.minMaxControl", self._stringToBool(value))
            await asyncio.sleep(0)

    async def _update_maxTemp(self,data):
        """
        Aktualisiere OGB Light Control
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.maxTemp")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Aktualisiere MaxTemp auf {value}")
            self.dataStore.setDeep("controlOptionData.minmax.maxTemp", value)
            self.dataStore.setDeep("tentData.maxTemp", value)
            await asyncio.sleep(0)

    async def _update_maxHumidity(self,data):
        """
        Aktualisiere OGB Light Control
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return

        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.maxHum")

        if current_value != value:
            _LOGGER.warn(f"{self.room}: Aktualisiere MaxHum auf {value}")
            self.dataStore.setDeep("controlOptionData.minmax.maxHum", value)
            self.dataStore.setDeep("tentData.maxHumidity", value)
            await asyncio.sleep(0)          
            
    async def _update_minTemp(self,data):
        """
        Aktualisiere OGB Light Control
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return
        
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.minTemp")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Aktualisiere MinTemp auf {value}")
            self.dataStore.setDeep("controlOptionData.minmax.minTemp", value)
            self.dataStore.setDeep("tentData.minTemp", value)
            await asyncio.sleep(0)
            
    async def _update_minHumidity(self,data):
        """
        Aktualisiere OGB Light Control
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return

        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.minHum")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Aktualisiere MinHum auf {value}")
            self.dataStore.setDeep("controlOptionData.minmax.minHum", value)
            self.dataStore.setDeep("tentData.minHumidity", value)
            await asyncio.sleep(0)


    ## Weights   
    async def _update_ownWeights_control(self,data):
        """
        Update OGB Light Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.ownWeights"))
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Weights Control auf {value}")
            self.dataStore.setDeep("controlOptions.ownWeights", self._stringToBool(value))
            await asyncio.sleep(0.001)
              
    async def _update_temperature_weight(self, data):
        """
        Update das Temperaturgewicht.
        """
        value = data.newState[0]  # Beispiel: Extrahiere den neuen Wert
        current_value = self.dataStore.getDeep("controlOptionData.weights.temp")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Temperaturgewicht auf {value}")
            self.dataStore.setDeep("controlOptionData.weights.temp", value)
            await asyncio.sleep(0.001)
            
    async def _update_humidity_weight(self, data):
        """
        Update das Feuchtigkeitsgewicht.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.weights.hum")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Feuchtigkeitsgewicht auf {value}")
            self.dataStore.setDeep("controlOptionData.weights.hum", value)
            await asyncio.sleep(0.0)


    ## HYDRO
    async def _update_hydro_mode(self,data):
        """
        Update OGB Hydro Mode
        """
        controlOption = self.dataStore.get("mainControl")
        if controlOption != "HomeAssistant": return
        
        value = data.newState[0]

        if value == "OFF":
            _LOGGER.warn(f"{self.room}: Deaktiviere Hydro Mode")
            self.dataStore.setDeep("Hydro.Active", False)
            self.dataStore.setDeep("Hydro.Mode", value)
            await self.eventManager.emit("HydroModeChange",value)
        else:
            _LOGGER.warn(f"{self.room}: Update Hydro Mode auf {value}")
            self.dataStore.setDeep("Hydro.Active", True)
            self.dataStore.setDeep("Hydro.Mode", value)
            await self.eventManager.emit("HydroModeChange",value)
            await asyncio.sleep(0)
    
    async def _update_hydro_mode_cycle(self,data):
        """
        Update OGB Hydro Cycle
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("Hydro.Cycle"))
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Hydro Cycle auf {value}")
            self.dataStore.setDeep("Hydro.Cycle", self._stringToBool(value))
            await self.eventManager.emit("HydroModeChange",value)
            await asyncio.sleep(0.001)
    
    async def _update_hydro_duration(self, data):
        """
        Update Hydro Duration
        """
        value = data.newState[0]  # Beispiel: Extrahiere den neuen Wert
        current_value = self.dataStore.getDeep("Hydro.Duration")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Hydro Duration auf {value}")
            self.dataStore.setDeep("Hydro.Duration", value)
            await self.eventManager.emit("HydroModeChange",value)
            await asyncio.sleep(0.001)
            
    async def _update_hydro_intervall(self, data):
        """
        Update Hydro Intervall.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("Hydro.Intervall")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Hydro Intervall auf {value}")
            self.dataStore.setDeep("Hydro.Intervall", value)
            await self.eventManager.emit("HydroModeChange",value)
            await asyncio.sleep(0) 
    
    
    ### Controll Updates           
    async def _update_ogbLightControl_control(self,data):
        """
        Update OGB Light Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.lightbyOGBControl"))
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update OGB Light Control auf {value}")
            self.dataStore.setDeep("controlOptions.lightbyOGBControl", self._stringToBool(value))
            
            await self.eventManager.emit("updateControlModes",self._stringToBool(value))
                  
    async def _update_vpdLight_control(self,data):
        """
        Update Licht Steuerung durch VPD 
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.vpdLightControl"))
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update VPD LichtSteuerung auf {value}")
            self.dataStore.setDeep("controlOptions.vpdLightControl", self._stringToBool(value))
            
            await self.eventManager.emit("updateControlModes",self._stringToBool(value))
             
    async def _update_vpdNightHold_control(self,data):
        """
        Update VPD Nachtsteuerung 
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.nightVPDHold"))
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update VPD Nacht Mode auf {value}")
            self.dataStore.setDeep("controlOptions.nightVPDHold", self._stringToBool(value))
            
            await self.eventManager.emit("updateControlModes",self._stringToBool(value))    
    
    
    #### CO2                 
    async def _update_co2_control(self,data):
        """
        Update OGB Light Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.co2Control"))
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update CO2 Control auf {value}")
            self.dataStore.setDeep("controlOptions.co2Control", self._stringToBool(value))
            await asyncio.sleep(0.001)
  
    async def _update_co2Target_value(self,data):
        """
        Update CO2 Min Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.co2ppm.target")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update CO2 Target Value auf {value}")
            self.dataStore.setDeep("controlOptionData.co2ppm.target", value)
  
    async def _update_co2Min_value(self,data):
        """
        Update CO2 Min Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.co2ppm.minPPM")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update CO2 Min Value auf {value}")
            self.dataStore.setDeep("controlOptionData.co2ppm.minPPM", value)

    async def _update_co2Max_value(self,data):
        """
        Update CO2 Min Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.co2ppm.maxPPM")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update CO2 Max Value auf {value}")
            self.dataStore.setDeep("controlOptionData.co2ppm.maxPPM", value)
 
 
    ## PlantDates
    async def _update_breederbloomdays_value(self,data):
        """
        Update FlowerTime Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("plantDates.breederbloomdays")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Breeder Bloom Days auf {value}")
            self.dataStore.setDeep("plantDates.breederbloomdays", value)
            await self.eventManager.emit("PlantTimeChange",value)
            
    async def _update_growstartdates_value(self,data):
        """
        Update GrowStart Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("plantDates.growstartdate")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Grow Start auf {value}")
            self.dataStore.setDeep("plantDates.growstartdate", value)
            await self.eventManager.emit("PlantTimeChange",value)
    
    async def _update_bloomswitchdate_value(self,data):
        """
        Update Bloom Start Date Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("plantDates.bloomswitchdate")
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Bloom Switch auf {value}")
            self.dataStore.setDeep("plantDates.bloomswitchdate", value)
            await self.eventManager.emit("PlantTimeChange",value)

    async def _update_plantDates(self, data):
        """
        Aktualisiert die Pflanzdaten und die entsprechenden Sensoren in Home Assistant.
        """
        timenow = datetime.now()
        # Definieren der Sensor-Entitäten
        planttotaldays_entity = f"sensor.ogb_planttotaldays_{self.room.lower()}"
        totalbloomdays_entity = f"sensor.ogb_totalbloomdays_{self.room.lower()}"
        remainingTime_entity = f"sensor.ogb_chopchoptime_{self.room.lower()}"
        
        # Abrufen der gespeicherten Pflanzdaten
        bloomSwitch = self.dataStore.getDeep("plantDates.bloomswitchdate")
        growstart = self.dataStore.getDeep("plantDates.growstartdate")
        breederDays = self.dataStore.getDeep("plantDates.breederbloomdays")

        # Überprüfen, ob breederDays ein gültiger Wert ist
        try:
            breeder_bloom_days = float(breederDays)
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.room}: Ungültiger Wert für breederbloomdays: {breederDays}")
            breeder_bloom_days = 0.0

        # Initialisieren der Variablen für die Tage
        planttotaldays = 0
        totalbloomdays = 0
        remaining_bloom_days = 0
        # Aktuelles Datum
        today = datetime.today()

        # Berechnung von planttotaldays
        if growstart:
            try:
                growstart_date = datetime.strptime(growstart, '%Y-%m-%d')
                planttotaldays = (today - growstart_date).days
                self.dataStore.setDeep("plantDates.planttotaldays", planttotaldays)
            except ValueError:
                _LOGGER.warning(f"{self.room}: Ungültiges Datum im growstart: {growstart}")

        # Berechnung von totalbloomdays
        if bloomSwitch:
            try:
                bloomswitch_date = datetime.strptime(bloomSwitch, '%Y-%m-%d')
                totalbloomdays = (today - bloomswitch_date).days
                self.dataStore.setDeep("plantDates.totalbloomdays", totalbloomdays)
            except ValueError:
                _LOGGER.warning(f"{self.room}: Ungültiges Datum im bloomSwitch: {bloomSwitch}")
        # Warnung bezüglich der verbleibenden Blütetage
        if breeder_bloom_days > 0 and totalbloomdays > 0:
            remaining_bloom_days = breeder_bloom_days - totalbloomdays
            if remaining_bloom_days <= 0:
                _LOGGER.warning(f"{self.room}: Die erwartete Blütezeit von {breeder_bloom_days} Tagen ist erreicht oder überschritten.")
            else:
                _LOGGER.info(f"{self.room}: Noch {remaining_bloom_days} Tage bis zum Ende der erwarteten Blütezeit.")
                await self.eventManager.emit("LogForClient",{"Name":self.room,"Message":f"Noch {remaining_bloom_days} Tage bis zum Ende der erwarteten Blütezeit"},haEvent=True)

        # Updaten der Sensoren in Home Assistant
        try:
            await self.hass.services.async_call(
                domain="opengrowbox",
                service="update_sensor",
                service_data={
                    "entity_id": planttotaldays_entity,
                    "value": planttotaldays
                },
                blocking=True
            )
            await self.hass.services.async_call(
                domain="opengrowbox",
                service="update_sensor",
                service_data={
                    "entity_id": totalbloomdays_entity,
                    "value": totalbloomdays
                },
                blocking=True
            )
            await self.hass.services.async_call(
                domain="opengrowbox",
                service="update_sensor",
                service_data={
                    "entity_id": remainingTime_entity,
                    "value": remaining_bloom_days
                },
                blocking=True
            )
            _LOGGER.debug(f"Sensoren '{planttotaldays_entity}' und '{totalbloomdays_entity}' wurden mit Werten aktualisiert: {planttotaldays}, {totalbloomdays}")
        except Exception as e:
            _LOGGER.error(f"Fehler beim Updaten der Sensoren '{planttotaldays_entity}' und '{totalbloomdays_entity}': {e}")

    async def _autoUpdatePlantStages(self,data):
        """ Ruft _update_plantDates auf und plant die nächste Ausführung. """
        timenow = datetime.now() 
        await self._update_plantDates(timenow)
        await asyncio.sleep(8 * 60 * 60)  # 8 Stunden warten
        asyncio.create_task(self._autoUpdatePlantStages(timenow))  # Nächste Ausführung starten
       
       
    ## Drying
    async def _udpate_drying_mode(self, data):
        """
        Update Zeltmodus.
        """
        value = data.newState[0]
        current_mode = self.dataStore.getDeep("drying.currentDryMode")
        if current_mode != value:
            _LOGGER.warn(f"{self.room}: Zelt Dry Modus geändert von {current_mode} auf {value}")
            self.dataStore.setDeep("drying.currentDryMode",value)
            await asyncio.sleep(0.001)
       
            
    ### PID Values
    async def _update_Proportional(self,proportional):
        await asyncio.sleep(0.001)
        pass
    
    async def _update_Integral(self,integral):
        await asyncio.sleep(0.001)
        pass
    
    async def _update_Derivativ(self,derivativ):
        await asyncio.sleep(0.001)
        pass

    ## TESTS      
    async def _update_ambientBorrow_control(self,data):
        """
        Update OGB Light Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.ambientBorrow"))
        if current_value != value:
            _LOGGER.warn(f"{self.room}: Update Ambient Brrow Control auf {value}")
            self.dataStore.setDeep("controlOptions.ambientBorrow", self._stringToBool(value))
            await asyncio.sleep(0.001)            

    ## DEBUG NOTES
    def _DEBUGSTATE(self):
        ##DEBUG
        devices = self.dataStore.get("devices")
        tentData = self.dataStore.get("tentData")
        controlOptions = self.dataStore.get("controlOptions")
        isPlantDay = tentData = self.dataStore.get("isPlantDay")
        vpdData = self.dataStore.get("vpd")
        caps = self.dataStore.get("capabilities")
        _LOGGER.warn(f"DEBUG: {self.room} DEVICES:{devices} TentData {tentData} CONTROLOPTIONS:{controlOptions}  VPDDATA {vpdData} CAPS:{caps} ")

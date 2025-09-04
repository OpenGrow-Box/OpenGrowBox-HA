import math
import logging
import asyncio
from datetime import datetime, time, timedelta

from .utils.calcs import calculate_avg_value,calculate_dew_point,calculate_current_vpd,calculate_perfect_vpd,calc_light_to_ppfd_dli
from .utils.sensorUpdater import update_sensor_via_service,_update_specific_sensor,_update_specific_number
from .utils.lightTimeHelpers import hours_between

from .OGBDataClasses.OGBPublications import OGBInitData,OGBEventPublication,OGBVPDPublication,OGBHydroPublication,OGBModePublication,OGBModeRunPublication,OGBCO2Publication,OGBMoisturePublication,OGBWaterPublication

# OGB IMPORTS
from .OGBDataClasses.OGBData import OGBConf

from .RegistryListener import OGBRegistryEvenListener
from .OGBDatastore import DataStore
from .OGBEventManager import OGBEventManager
from .OGBDeviceManager import OGBDeviceManager
from .OGBModeManager import OGBModeManager
from .OGBActionManager import OGBActionManager
from .OGBPremManager import OGBPremManager
from .OGBFeedManager import OGBFeedManager
from .OGBDSManager import OGBDSManager
from .OGBClientManager import OGBClientManager

_LOGGER = logging.getLogger(__name__)

class OpenGrowBox:
    def __init__(self, hass, room):
        self.name = "OGB Controller"
        self.hass = hass
        self.room = room


        self.ogbConfig = OGBConf(hass=self.hass,room=self.room)
        self.dataStore = DataStore(self.ogbConfig)

        # Init EventManager
        self.eventManager = OGBEventManager(self.hass, self.dataStore)

        # Registry Listener für HA Events
        self.registryListener = OGBRegistryEvenListener(self.hass, self.dataStore, self.eventManager, self.room)

        # Init Managers
        self.dataStoreManager = OGBDSManager(self.hass, self.dataStore, self.eventManager,self.room,self.registryListener)
        self.deviceManager = OGBDeviceManager(self.hass, self.dataStore, self.eventManager,self.room,self.registryListener)
        self.modeManager = OGBModeManager(self.hass,self.dataStore, self.eventManager, self.room)
        self.actionManager = OGBActionManager(self.hass, self.dataStore, self.eventManager,self.room)
        self.feedManager = OGBFeedManager(self.hass, self.dataStore, self.eventManager,self.room)
        self.clientManager = OGBClientManager(self.hass, self.dataStore, self.eventManager,self.room)
         
        # Init Prem Manager
        self.premiumManager = OGBPremManager(self.hass, self.dataStore, self.eventManager,self.room)
        
        #Events Register
        self.eventManager.on("RoomUpdate", self.handleRoomUpdate)
        self.eventManager.on("VPDCreation", self.handleNewVPD)
        
        #LightSheduleUpdate
        self.eventManager.on("LightSheduleUpdate", self.lightSheduleUpdate)
        
        # Plant Times
        self.eventManager.on("PlantTimeChange",self._autoUpdatePlantStages)
       
        # Ambient & Outsite   
        self.hass.bus.async_listen("AmbientData",self._handle_ambient_data)   
        self.hass.bus.async_listen("OutsiteData",self._handle_outsite_data)
               
    def __str__(self):
        return (f"{self.name}' Running")
    
    def __repr__(self):
        return (f"{self.name}' Running")  
    
    ## INIT 
    async def firstInit(self):
        # Watering Initalisation on Device Start based on OGB-Data
        Init=True
        
        ##
        ## TRY RESTORE DATASTORE FUNCTION NEEDED FROM LAST REBOOT!!
        ##

        await self.eventManager.emit("HydroModeChange",Init)
        await self.eventManager.emit("HydroModeRetrieveChange",Init)
        await self.eventManager.emit("PlantTimeChange",Init)
        await self._get_vpd_onStart(Init)

        _LOGGER.info(f"OpenGrowBox for {self.room} started successfully State:{self.dataStore}")
        
        return True

    async def _loadLastData(self,data):
        pass
    
    async def _get_vpd_onStart(self, data):
        if data != True:
            return
        workdataDevices = self.dataStore.getDeep("workData.Devices")
        _LOGGER.debug(f"INT DATA NEED {self.room} --- :{workdataDevices}")     
        temperatures = []
        humidities = []

        for device in workdataDevices:
            for entity in device.get("entities", []):
                entity_id = entity.get("entity_id", "")
                value = entity.get("value")

                # Temperature
                if "temperature" in entity_id:
                    try:
                        temperatures.append(entity)
                    except (ValueError, TypeError):
                        pass

                # Humidity
                if "humidity" in entity_id:
                    try:
                        humidities.append(entity)
                    except (ValueError, TypeError):
                        pass


        # Temperatur- und Feuchtigkeitsdaten laden
        self.dataStore.setDeep("workData.temperature",temperatures)
        self.dataStore.setDeep("workData.humidity",humidities)
        leafTempOffset = self.dataStore.getDeep("tentData.leafTempOffset")
        avgTemp = calculate_avg_value(temperatures)
        self.dataStore.setDeep("tentData.temperature", avgTemp)
        avgHum = calculate_avg_value(humidities)
        self.dataStore.setDeep("tentData.humidity", avgHum)
        avgDew = calculate_dew_point(avgTemp, avgHum) if avgTemp != "unavailable" and avgHum != "unavailable" else "unavailable"
        self.dataStore.setDeep("tentData.dewpoint", avgDew)

        lastVpd = self.dataStore.getDeep("vpd.current")
        currentVPD = calculate_current_vpd(avgTemp, avgHum, leafTempOffset)        
        
        if currentVPD == 0.0 or 0:
            _LOGGER.error(f"VPD 0.0 FOUND {self.room}")
            return
        
        if isinstance(data, OGBInitData):
            _LOGGER.debug(f"OGBInitData erkannt: {data}")
            return
        
        else:
            # Spezifische Aktion für OGBEventPublication
            if currentVPD != lastVpd:
                self.dataStore.setDeep("vpd.current", currentVPD)
                vpdPub = OGBVPDPublication(Name=self.room, VPD=currentVPD, AvgTemp=avgTemp, AvgHum=avgHum, AvgDew=avgDew)


                await update_sensor_via_service(self.room,vpdPub,self.hass)
                _LOGGER.debug(f"New-VPD: {vpdPub} newStoreVPD:{currentVPD}, lastStoreVPD:{lastVpd}")

                tentMode = self.dataStore.get("tentMode")
                runMode = OGBModeRunPublication(currentMode=tentMode)               
                
                if self.room.lower() == "ambient":
                    await self.eventManager.emit("AmbientData",vpdPub,haEvent=True)
                    await self.get_weather_data()
                    return

                await self.eventManager.emit("selectActionMode",runMode)
                await self.eventManager.emit("LogForClient",vpdPub,haEvent=True)
                await self.eventManager.emit("DataRelease",vpdPub)           

                self._debugState()
                return vpdPub
               
            else:
                vpdPub = OGBVPDPublication(Name=self.room, VPD=currentVPD, AvgTemp=avgTemp, AvgHum=avgHum, AvgDew=avgDew)
                _LOGGER.debug(f"Same-VPD: {vpdPub} currentVPD:{currentVPD}, lastStoreVPD:{lastVpd}")
                await update_sensor_via_service(self.room,vpdPub,self.hass)
                await self.eventManager.emit("DataRelease",vpdPub)

    async def handleRoomUpdate(self, entity):
        """
        Update WorkData für Temperatur oder Feuchtigkeit basierend to einer Entität.
        Ignoriere Entitäten, die 'ogb_' im Namen enthalten.
        """
        # Entitäten mit 'ogb_' im Namen überspringen
        if "ogb_" in entity.Name:
            await self.manager(entity)
            return


        _LOGGER.debug(f"{self.room} OGB-Manager: Incomming Event {entity}")
        temps = self.dataStore.getDeep("workData.temperature")
        hums = self.dataStore.getDeep("workData.humidity")

        vpd = self.dataStore.getDeep("vpd.current")
        needs = ("temperature", "humidity","moisture","carbondioxide","water","wasser","plant","pflanzen","soil")
        
        # Prüfe, ob die Entität für Temperatur oder Feuchtigkeit relevant ist
        if any(need in entity.Name for need in needs):
            # Bestimme, ob es sich um Temperatur oder Feuchtigkeit handelt
            # Wasser/Wasser-Entitäten (Hydro)
            if "water" in entity.Name or "wasser" in entity.Name:
                updated = False

                if "_ec" in entity.Name:
                    self.dataStore.setDeep("Hydro.ec_current", entity.newState[0])
                    updated = True
                elif "_tds" in entity.Name:
                    self.dataStore.setDeep("Hydro.tds_current", entity.newState[0])
                    updated = True
                elif "_ph" in entity.Name:
                    self.dataStore.setDeep("Hydro.ph_current", entity.newState[0])
                    updated = True
                elif "_oxidation" in entity.Name:
                    self.dataStore.setDeep("Hydro.oxi_current", entity.newState[0])
                    updated = True
                elif "_salinity" in entity.Name:
                    self.dataStore.setDeep("Hydro.sal_current", entity.newState[0])
                    updated = True
                elif "_temp" in entity.Name:
                    self.dataStore.setDeep("Hydro.WaterTEMP", entity.newState[0])
                    updated = True

                if updated:
                    ec_current = self.dataStore.getDeep("Hydro.ec_current")
                    tds_current = self.dataStore.getDeep("Hydro.tds_current")
                    ph_current = self.dataStore.getDeep("Hydro.ph_current")
                    oxi_current = self.dataStore.getDeep("Hydro.oxi_current")
                    sal_current = self.dataStore.getDeep("Hydro.sal_current")
                    temp_current = self.dataStore.getDeep("Hydro.WaterTEMP")

                    hydroPublication = OGBWaterPublication(
                        Name="HydroUpdate",
                        ecCurrent=ec_current,
                        tdsCurrent=tds_current,
                        phCurrent=ph_current,
                        oxiCurrent=oxi_current,
                        salCurrent=sal_current,
                        waterTemp=temp_current
                    )
                    await self.eventManager.emit("CheckForFeed", hydroPublication)
                    _LOGGER.info(f"{self.room} OGB-Manager: Hydro Daten aktualisiert EC:{ec_current}, TDS:{tds_current}, pH:{ph_current}, OXI:{oxi_current}, SAL:{sal_current}, TEMP:{temp_current}")
                    return
                
            # Wasser/Wasser-Entitäten (Hydro)
            elif "soil" in entity.Name or "boden" in entity.Name:
                updated = False
                return

            elif "_temperature" in entity.Name:
                # Update Temperaturdaten
                temps = self._update_work_data_array(temps, entity)
                self.dataStore.setDeep("workData.temperature", temps)
                VPDPub = OGBVPDPublication(Name="TempUpdate",VPD=vpd,AvgDew=None,AvgHum=None,AvgTemp=None)
                await self.eventManager.emit("VPDCreation",VPDPub)
                _LOGGER.info(f"{self.room} OGB-Manager: Temperaturdaten aktualisiert {temps}")
                return

            elif "_humidity" in entity.Name:
                # Update Feuchtigkeitsdaten
                hums = self._update_work_data_array(hums, entity)
                self.dataStore.setDeep("workData.humidity", hums)
                VPDPub = OGBVPDPublication(Name="HumUpdate",VPD=vpd,AvgDew=None,AvgHum=None,AvgTemp=None)
                await self.eventManager.emit("VPDCreation",VPDPub)
                _LOGGER.info(f"{self.room} OGB-Manager: Feuchtigkeitsdaten aktualisiert {hums}")
                return

            elif "_moisture" in entity.Name:
                # Update Feuchtigkeitsdaten
                moists = self.dataStore.getDeep("workData.moisture")
                moistures = self._update_work_data_array(moists, entity)
                self.dataStore.setDeep("workData.moisture", moistures)
                return
                
            if "_lumen" in entity.Name:
                growSpace = self.dataStore.get("growAreaM2")
                lightStart = self.dataStore.getDeep("isPlantDay.lightOnTime")
                lightStop = self.dataStore.getDeep("isPlantDay.lightOffTime")
                lightDuration = hours_between(lightStart,lightStop)
                ppfd,dli = calc_light_to_ppfd_dli(entity.newState[0],"lumen",lightDuration,growSpace)
                self.dataStore.setDeep("tentData.DLI", dli)
                await _update_specific_sensor("ogb_ppfd_",self.room,ppfd,self.hass)
                self.dataStore.setDeep("tentData.PPFD", ppfd)
                await _update_specific_sensor("ogb_dli_",self.room,dli,self.hass)
                return
                
            elif "_lux" or "_illuminance" in entity.Name:
                growSpace = self.dataStore.get("growAreaM2")
                lightStart = self.dataStore.getDeep("isPlantDay.lightOnTime")
                lightStop = self.dataStore.getDeep("isPlantDay.lightOffTime")
                lightDuration = hours_between(lightStart,lightStop)
                ppfd,dli = calc_light_to_ppfd_dli(entity.newState[0],"lux",lightDuration,growSpace)
                self.dataStore.setDeep("tentData.DLI", dli)
                await _update_specific_sensor("ogb_ppfd_",self.room,ppfd,self.hass)
                self.dataStore.setDeep("tentData.PPFD", ppfd)
                await _update_specific_sensor("ogb_dli_",self.room,dli,self.hass)
                return
            
            # CO2-Entitäten
            elif "_co2" in entity.Name or "_carbondioxide" in entity.Name:
                self.dataStore.setDeep("tentData.co2Level", entity.newState[0])
                self.dataStore.setDeep("controlOptionData.co2ppm.current", entity.newState[0])

                minPPM = self.dataStore.getDeep("controlOptionData.co2ppm.minPPM")
                maxPPM = self.dataStore.getDeep("controlOptionData.co2ppm.maxPPM")
                targetPPM = self.dataStore.getDeep("controlOptionData.co2ppm.target")
                currentPPM = self.dataStore.getDeep("controlOptionData.co2ppm.current")

                co2Publication = OGBCO2Publication(Name="CO2", co2Current=currentPPM, co2Target=targetPPM, minCO2=minPPM, maxCO2=maxPPM)
                await self.eventManager.emit("NewCO2Publication", co2Publication)
                _LOGGER.info(f"{self.room} OGB-Manager: CO2 Daten aktualisiert {currentPPM}")
                return

    async def managerInit(self,ogbEntity):
        for entity in ogbEntity['entities']:
            entity_id = entity['entity_id']
            value = entity['value']
            entityPublication = OGBInitData(Name=entity_id,newState=[value])
            await self.manager(entityPublication) 
     
    async def manager(self, data):
        """
        Verwalte Aktionen basierend to den eingehenden Daten mit einer Mapping-Strategie.
        """

        # Entferne Präfixe vor dem ersten Punkt
        entity_key = data.Name.split(".", 1)[-1].lower()

        # Mapping from Namen zu Funktionen
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

            # Drying
            f"ogb_dryingmodes_{self.room.lower()}": self._udpate_drying_mode,             
            
            # MINMAX
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
                  
            f"ogb_hydro_retrive_{self.room.lower()}": self._update_retrive_mode,
            f"ogb_hydroretriveduration_{self.room.lower()}": self._update_hydro_retrive_duration,
            f"ogb_hydroretriveintervall_{self.room.lower()}": self._update_hydro_retrive_intervall,     

            #Feed
            f"ogb_feed_plan_{self.room.lower()}": self._update_feed_mode,
            f"ogb_feed_ph_target_{self.room.lower()}": self._update_feed_ph_target,
            f"ogb_feed_ec_target_{self.room.lower()}": self._update_feed_ec_target,     
            f"ogb_feed_nutrient_a_{self.room.lower()}": self._update_feed_nut_a_ml,
            f"ogb_feed_nutrient_b_{self.room.lower()}": self._update_feed_nut_b_ml,
            f"ogb_feed_nutrient_c_{self.room.lower()}": self._update_feed_nut_c_ml,
            f"ogb_feed_nutrient_w_{self.room.lower()}": self._update_feed_nut_w_ml,
            f"ogb_feed_nutrient_x_{self.room.lower()}": self._update_feed_nut_x_ml,
            f"ogb_feed_nutrient_y_{self.room.lower()}": self._update_feed_nut_y_ml,
            f"ogb_feed_nutrient_ph_{self.room.lower()}": self._update_feed_nut_ph_ml,

            # Ambient/Outdoor Features
            f"ogb_ambientcontrol_{self.room.lower()}": self._update_ambient_control,
            
            # Devices
            f"ogb_owndevicesets_{self.room.lower()}": self._udpate_own_deviceSelect,            
            
            # Lights Sets
            f"ogb_light_device_select_{self.room.lower()}": self._add_selectedDevice,  
            f"ogb_light_minmax_{self.room.lower()}": self._device_Self_MinMax,
            f"ogb_light_volt_min_{self.room.lower()}": self._device_MinMax_setter,
            f"ogb_light_volt_max_{self.room.lower()}": self._device_MinMax_setter,            
            
            # Exhaust Sets
            f"ogb_exhaust_device_select_{self.room.lower()}": self._add_selectedDevice,
            f"ogb_exhaust_minmax_{self.room.lower()}": self._device_Self_MinMax,
            f"ogb_exhaust_duty_min_{self.room.lower()}": self._device_MinMax_setter,
            f"ogb_exhaust_duty_max_{self.room.lower()}": self._device_MinMax_setter,

            # Intake Sets                                  
            f"ogb_intake_device_select_{self.room.lower()}": self._add_selectedDevice,              
            f"ogb_intake_minmax_{self.room.lower()}": self._device_Self_MinMax,
            f"ogb_intake_duty_min_{self.room.lower()}": self._device_MinMax_setter,
            f"ogb_intake_duty_max_{self.room.lower()}": self._device_MinMax_setter,
            
            # Vents Sets
            f"ogb_vents_device_select_{self.room.lower()}": self._add_selectedDevice, 
            f"ogb_ventilation_minmax_{self.room.lower()}": self._device_Self_MinMax,
            f"ogb_ventilation_duty_min_{self.room.lower()}": self._device_MinMax_setter,
            f"ogb_ventilation_duty_max_{self.room.lower()}": self._device_MinMax_setter,
                                    
            # Device Selects
            f"ogb_heater_device_select_{self.room.lower()}": self._add_selectedDevice, 
            f"ogb_cooler_device_select_{self.room.lower()}": self._add_selectedDevice,            
            f"ogb_climate_device_select_{self.room.lower()}": self._add_selectedDevice,                
            f"ogb_humidifier_device_select_{self.room.lower()}": self._add_selectedDevice,      
            f"ogb_dehumidifier_device_select_{self.room.lower()}": self._add_selectedDevice,      
            f"ogb_co2_device_select_{self.room.lower()}": self._add_selectedDevice,
            f"ogb_waterpump_device_select_{self.room.lower()}": self._add_selectedDevice,
                
            #WorkMode
            f"ogb_workmode_{self.room.lower()}": self._update_WrokMode_control,

            #StrainData
            f"ogb_strainname_{self.room.lower()}": self._update_StrainName,
            
            # Area
            f"ogb_grow_area_m2_{self.room.lower()}": self._update_Grow_Area,

        }

        # Überprüfe, ob der Schlüssel in der Mapping-Tabelle vorhanden ist
        action = actions.get(entity_key)
        if action:
            await action(data)  # Rufe die zugehörige Aktion mit `data` to
        else:
            _LOGGER.info(f"OGB-Manager {self.room}: Keine Aktion für {entity_key} gefunden.")
 
    ## VPD Sensor Update
    async def handleNewVPD(self, data):

        controlOption = self.dataStore.get("mainControl")        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return
        
        # Temperatur- und Feuchtigkeitsdaten laden
        temps = self.dataStore.getDeep("workData.temperature")
        hums = self.dataStore.getDeep("workData.humidity")
        leafTempOffset = self.dataStore.getDeep("tentData.leafTempOffset")
        
        logging.warning(f"Current WorkData-Array TEMP:{temps} : HUMS: {hums}")
        
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
            #_LOGGER.info(f"OGBInitData erkannt: {data}")
            return
        else:
            # Spezifische Aktion für OGBEventPublication
            if currentVPD != lastVpd:
                self.dataStore.setDeep("vpd.current", currentVPD)
                vpdPub = OGBVPDPublication(Name=self.room, VPD=currentVPD, AvgTemp=avgTemp, AvgHum=avgHum, AvgDew=avgDew)
                await update_sensor_via_service(self.room,vpdPub,self.hass)
                _LOGGER.debug(f"New-VPD: {vpdPub} newStoreVPD:{currentVPD}, lastStoreVPD:{lastVpd}")
                tentMode = self.dataStore.get("tentMode")
                runMode = OGBModeRunPublication(currentMode=tentMode)               
                
                if self.room.lower() == "ambient":
                    _LOGGER.debug(f"New-Ambient-VPD: {vpdPub} newStoreVPD:{currentVPD}, lastStoreVPD:{lastVpd}")
                    await self.eventManager.emit("AmbientData",vpdPub,haEvent=True)
                    await self.get_weather_data()
                    return
                
                await self.eventManager.emit("selectActionMode",runMode)
                await self.eventManager.emit("DataRelease",vpdPub,haEvent=True)           
                await self.eventManager.emit("LogForClient",vpdPub,haEvent=True)
               
                self._debugState()
                return vpdPub
               
            else:
                vpdPub = OGBVPDPublication(Name=self.room, VPD=currentVPD, AvgTemp=avgTemp, AvgHum=avgHum, AvgDew=avgDew)
                _LOGGER.debug(f"Same-VPD: {vpdPub} currentVPD:{currentVPD}, lastStoreVPD:{lastVpd}")
                await update_sensor_via_service(self.room,vpdPub,self.hass)
                await self.eventManager.emit("DataRelease",vpdPub,haEvent=True)

    async def get_weather_data(self):
        """Hole aktuelle Temperatur und Luftfeuchtigkeit über Open-Meteo API (kostenlos)."""
        try:
            import aiohttp
            import asyncio
            
            lat = self.hass.config.latitude
            lon = self.hass.config.longitude
            
            url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m&timezone=auto"
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        current = data.get('current', {})
                        temperature = round(current.get('temperature_2m', 20.0), 1)
                        humidity = current.get('relative_humidity_2m', 60)
                        
                        _LOGGER.debug(f"{self.room} Open-Meteo: {temperature}°C, {humidity}%")
                        await self.eventManager.emit("OutsiteData",{"temperature":temperature,"humidity":humidity},haEvent=True)
                    else:
                        _LOGGER.error(f"Open-Meteo API Error: {response.status}")
                        return None, None
                        
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout Open-Meteo")
            return 20.0, 60.0
        except Exception as e:
            _LOGGER.error(f"Fetch Error Open-Meteo: {e}")
            return 20.0, 60.0
    
    async def _handle_ambient_data(self, event):
        if self.room.lower() == "ambient":
            return

        logging.debug(f"Received Ambient Data {self.room}")

        payload = event.data

        temp = payload.get("AvgTemp")
        hum = payload.get("AvgHum")

        self.dataStore.setDeep("tentData.AmbientTemp", temp)
        self.dataStore.setDeep("tentData.AmbientHum", hum)

        await _update_specific_sensor("ogb_ambienttemperature_", self.room, temp, self.hass)
        await _update_specific_sensor("ogb_ambienthumidity_", self.room, hum, self.hass)

    async def _handle_outsite_data(self, event):
        if self.room.lower() == "ambient":
            return

        logging.debug(f"Received Outsite Data {self.room} - {event}")

        payload = event.data

        temp = payload.get("temperature")
        hum = payload.get("humidity")

        self.dataStore.setDeep("tentData.OutsiteTemp", temp)
        self.dataStore.setDeep("tentData.OutsiteHum", hum)

        await _update_specific_sensor("ogb_outsitetemperature_", self.room, temp, self.hass)
        await _update_specific_sensor("ogb_outsitehumidity_", self.room, hum, self.hass)

    async def lightSheduleUpdate(self,data):
        lightbyOGBControl = self.dataStore.getDeep("controlOptions.lightbyOGBControl")
        if lightbyOGBControl == False: return
        
        lightChange = await self.update_light_state()

        if lightChange == None: return
        self.dataStore.setDeep("isPlantDay.islightON",lightChange)
        _LOGGER.info(f"{self.name}: Lichtstatus geprüft und aktualisiert für {self.room} Lichstatus ist {lightChange}")

        await self.eventManager.emit("toggleLight",lightChange)
            
    async def update_minMax_settings(self):
        """
        Update Werte aller relevanten number-Entities über den Home Assistant Service `number.set_value`.
        Ungültige Werte (None, "unknown", "unbekannt") werden übersprungen.
        """
        entities = {
            "minTemp": f"number.ogb_mintemp_{self.room.lower()}",
            "maxTemp": f"number.ogb_maxtemp_{self.room.lower()}",
            "minHum": f"number.ogb_minhum_{self.room.lower()}",
            "maxHum": f"number.ogb_maxhum_{self.room.lower()}",
            "humidityWeight": f"number.ogb_humidityweight_{self.room.lower()}"
        }

        currentPlantStage = self.dataStore.get("plantStage")
        PlantStageValues = self.dataStore.getDeep(f"plantStages.{currentPlantStage}")

        # Werte für die Entities extrahieren
        values = {
            "minTemp": PlantStageValues.get("minTemp"),
            "maxTemp": PlantStageValues.get("maxTemp"),
            "minHum": PlantStageValues.get("minHumidity"),
            "maxHum": PlantStageValues.get("maxHumidity"),
            "humidityWeight": 1.25 if currentPlantStage in ["MidFlower", "LateFlower"] else 1.0
        }

        _LOGGER.info(f"Setting defaults for stage '{currentPlantStage}': {PlantStageValues}")

        async def set_value(entity_id, value):
            if value in (None, "unknown", "unbekannt"):
                _LOGGER.warning(f"Skipping update for {entity_id} because value is invalid: {value}")
                return
            await self.hass.services.async_call(
                domain="number",
                service="set_value",
                service_data={
                    "entity_id": entity_id,
                    "value": value
                },
                blocking=True
            )
            _LOGGER.info(f"Updated {entity_id} to {value}")

        # Alle Entities aktualisieren
        for key, entity_id in entities.items():
            await set_value(entity_id, values.get(key))

    # Helpers
    def _stringToBool(self,stringToBool):
        if stringToBool == "YES":
            return True
        if stringToBool == "NO":
            return False

    def _update_work_data_array(self, data_array, entity):
        """
        Aktualisiert alle passenden Einträge im WorkData-Array basierend to der übergebenen Entität.
        """
        _LOGGER.info(f"{self.room}: Checking Update-ITEM: {entity} in {data_array}")  
        found = False
        for item in data_array:
            if item["entity_id"] == entity.Name:
                item["value"] = entity.newState[0]
                found = True
                _LOGGER.info(f"{self.room}:Update-ITEM Found: {entity} → {item['value']}")
        
        if not found:
            data_array.append({
                "entity_id": entity.Name,
                "value": entity.newState[0]
            })
            _LOGGER.info(f"{self.room}:Update-ITEM NOT Found: {entity} → hinzugefügt")
        
        return data_array

    def _update_work_data_array2(self, data_array, entity):
        """
        Aktualisiert alle passenden Einträge im WorkData-Array basierend to der übergebenen Entität.
        """
        _LOGGER.info(f"{self.room}: Checking Update-ITEM: {entity.Name} newState: {entity.newState}")  
        
        # Wert sicher extrahieren
        try:
            if isinstance(entity.newState, list):
                value = entity.newState[0]  # Array: ersten Wert nehmen
            else:
                value = entity.newState     # Einzelwert direkt verwenden
        except (IndexError, TypeError):
            _LOGGER.error(f"{self.room}: Fehler beim Extrahieren des Wertes aus {entity.newState}")
            return data_array
        
        found = False
        for item in data_array:
            if item["entity_id"] == entity.Name:
                item["value"] = value
                found = True
                _LOGGER.info(f"{self.room}: Update-ITEM Found: {entity.Name} → {value}")
        
        if not found:
            data_array.append({
                "entity_id": entity.Name,
                "value": value
            })
            _LOGGER.info(f"{self.room}: Update-ITEM NOT Found: {entity.Name} → hinzugefügt")
        
        return data_array

    async def _plantStageToVPD(self):
        """
        Aktualisiert die VPD-Werte basierend to dem Pflanzenstadium.
        """
        plantStage = self.dataStore.get("plantStage")
        # Daten aus dem `plantStages`-Dictionary abrufen
        stageValues = self.dataStore.getDeep(f"plantStages.{plantStage}")
        ownControllValues = self.dataStore.getDeep("controlOptions.minMaxControl")
        
        if not stageValues:
            _LOGGER.error(f"{self.room}: Keine Daten für PlantStage '{plantStage}' gefunden.")
            return

        if ownControllValues:
            _LOGGER.error(f"{self.room}: Keine Anpassung Möglich für PlantStage Own MinMax Active")
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
            await _update_specific_sensor("ogb_current_vpd_target_",self.room,perfectVPD,self.hass)
            await _update_specific_sensor("ogb_current_vpd_target_min_",self.room,perfectVPDMin,self.hass)
            await _update_specific_sensor("ogb_current_vpd_target_max_",self.room,perfectVPDMax,self.hass)

            # Werte in `dataStore` setzen
            self.dataStore.setDeep("vpd.range", vpd_range)
            self.dataStore.setDeep("tentData.maxTemp", max_temp)
            self.dataStore.setDeep("tentData.minTemp", min_temp)
            self.dataStore.setDeep("tentData.maxHumidity", max_humidity)
            self.dataStore.setDeep("tentData.minHumidity", min_humidity)

        
            self.dataStore.setDeep("vpd.perfection",perfectVPD)
            self.dataStore.setDeep("vpd.perfectMin",vpd_range[0])
            self.dataStore.setDeep("vpd.perfectMax",vpd_range[1])

            await self.update_minMax_settings()
            await self.eventManager.emit("PlantStageChange",plantStage)
            
            _LOGGER.debug(f"{self.room}: PlantStage '{plantStage}' erfolgreich in VPD-Daten übertragen.")
        except KeyError as e:
            _LOGGER.error(f"{self.room}: Fehlender Schlüssel in PlantStage-Daten '{e}'")
        except Exception as e:
            _LOGGER.error(f"{self.room}: Fehler beim Verarbeiten der PlantStage-Daten: {e}")

    async def update_light_state(self):
        """
        Update Status from `lightOn`, basierend to den Lichtzeiten.
        """

        lightOnTime = self.dataStore.getDeep("isPlantDay.lightOnTime")
        lightOffTime = self.dataStore.getDeep("isPlantDay.lightOffTime")

        try:
            if lightOnTime == "" or lightOffTime == "":
                _LOGGER.error("Lichtzeiten fehlen. Bitte sicherstellen, dass 'lightOnTime' und 'lightOffTime' gesetzt sind.")
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
        current_main_control = self.dataStore.get("mainControl")
        if current_main_control != value:
            self.dataStore.set("mainControl",value)
            await self.eventManager.emit("mainControlChange",value)
            await self.eventManager.emit("PremiumChange",{"currentValue":value,"lastValue":current_main_control}) 
              
    async def _update_notify_option(self,data):
        """
        Udpate Notify Option.
        """
        value = data.newState[0]
        current_state = self.dataStore.get("notifyControl")
        self.dataStore.set("notifyControl",value)
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
            
            ownWeightsActive = self.dataStore.getDeep("controlOptions.ownWeights")

            if not ownWeightsActive and (
                value in ["MidFlower", "LateFlower"] or current_stage in ["MidFlower", "LateFlower"]
            ):
                await _update_specific_sensor("ogb_humidityweight_", self.room, 1.25, self.hass) 
            await self._plantStageToVPD()
            await self.eventManager.emit("PlantStageChange",value)
  
    async def _update_tent_mode(self, data):
        """
        Update Tentmodus.
        """
        
        value = data.newState[0]
        current_mode = self.dataStore.get("tentMode")
        
        if isinstance(data, OGBInitData):
            self.dataStore.set("tentMode",value)
        elif isinstance(data, OGBEventPublication):
            if value == "": return
            if current_mode != value:
                tentModePublication = OGBModePublication(currentMode=value,previousMode=current_mode)
                self.dataStore.set("tentMode",value)
                ## Event to Mode Manager 
                await self.eventManager.emit("selectActionMode",tentModePublication)
        else:
            _LOGGER.error(f"Unkown Tent-Mode check your Select Options: {type(data)} - Data: {data}")      

    async def _update_leafTemp_offset(self, data):
        """
        Update Leaf Temp 
        """
        value = data.newState[0]
        current_stage = self.dataStore.getDeep("tentData.leafTempOffset")
        
        if isinstance(data, OGBInitData):
            self.dataStore.setDeep("tentData.leafTempOffset",value)
        elif isinstance(data, OGBEventPublication):
            if current_stage != value:
                self.dataStore.setDeep("tentData.leafTempOffset",value)
                await self.eventManager.emit("VPDCreation",value)
        else:
            _LOGGER.error(f"Unkown Datatype: {type(data)} - Data: {data}")     

    async def _update_vpd_Target(self, data):
        """
        Update Target VPD Value if running on Targeted VPD 
        """
        value = float(data.newState[0])
        current_value = self.dataStore.getDeep("vpd.targeted")

        if current_value != value:
            _LOGGER.info(f"{self.room}: Update Target VPD to {value}")
            self.dataStore.setDeep("vpd.targeted", value)

            tolerance_percent = float(self.dataStore.getDeep("vpd.tolerance") or 0)
            tolerance_value = value * (tolerance_percent / 100)

            min_vpd = value - tolerance_value
            max_vpd = value + tolerance_value

            await _update_specific_sensor("ogb_current_vpd_target_", self.room, value, self.hass)
            await _update_specific_sensor("ogb_current_vpd_target_min_", self.room, min_vpd, self.hass)
            await _update_specific_sensor("ogb_current_vpd_target_max_", self.room, max_vpd, self.hass)

    async def _update_vpd_tolerance(self,data):
        """
        Update VPD Tolerance
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("vpd.tolerance")
        if current_value != value:
            self.dataStore.setDeep("vpd.tolerance",value)

    # Lights
    async def _update_lightOn_time(self,data):
        """
        Update Light ON Time
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.lightOnTime")
        if current_value != value:

            self.dataStore.setDeep("isPlantDay.lightOnTime",value)
            await self.eventManager.emit("LightTimeChanges",True)

    async def _update_lightOff_time(self,data):
        """
        Update Light OFF Time
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.lightOffTime")
        if current_value != value:
            self.dataStore.setDeep("isPlantDay.lightOffTime",value)
            await self.eventManager.emit("LightTimeChanges",True)
            
    async def _update_sunrise_time(self,data):
        """
        Update Sunrise Time
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.sunRiseTime")
        if current_value != value:
            self.dataStore.setDeep("isPlantDay.sunRiseTime",value)
            await self.eventManager.emit("SunRiseTimeUpdates",value)

    async def _update_sunset_time(self,data):
        """
        Update SunSet Time
        """
        value = data.newState[0]
        if value == None: return
        current_value = self.dataStore.getDeep("isPlantDay.sunSetTime")
        if current_value != value:
            self.dataStore.setDeep("isPlantDay.sunSetTime",value)
            await self.eventManager.emit("SunSetTimeUpdates",value)

    ## Workmode 
    async def _update_WrokMode_control(self,data):
        """
        Update OGB Workmode Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.workMode"))
        if current_value != value:
            boolValue = self._stringToBool(value)
            if boolValue == False:
                await self.update_minMax_settings()
            self.dataStore.setDeep("controlOptions.workMode", self._stringToBool(value))
            await self.eventManager.emit("WorkModeChange",self._stringToBool(value))

    ## Ambnnient/outsite
    async def _update_ambient_control(self,data):
        """
        Update OGB Ambient Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.ambientControl"))
        if current_value != value:
            boolValue = self._stringToBool(value)
            if boolValue == False:
                await self.update_minMax_settings()

            self.dataStore.setDeep("controlOptions.ambientControl", self._stringToBool(value))

    ##MINMAX Values
    async def _update_MinMax_control(self,data):
        """
        Update MinMax Stage Values
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if current_value != value:
            boolValue = self._stringToBool(value)
            if boolValue == False:
                await self.update_minMax_settings()

            self.dataStore.setDeep("controlOptions.minMaxControl", self._stringToBool(value))

    async def _update_maxTemp(self,data):
        """
        Aktualisiere OGB Max Temp
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.maxTemp")
        if current_value != value:
            _LOGGER.info(f"{self.room}: Aktualisiere MaxTemp to {value}")
            self.dataStore.setDeep("controlOptionData.minmax.maxTemp", value)
            self.dataStore.setDeep("tentData.maxTemp", value)

    async def _update_maxHumidity(self,data):
        """
        Aktualisiere OGB Max Humditity
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return

        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.maxHum")

        if current_value != value:
            _LOGGER.info(f"{self.room}: Aktualisiere MaxHum to {value}")
            self.dataStore.setDeep("controlOptionData.minmax.maxHum", value)
            self.dataStore.setDeep("tentData.maxHumidity", value)
            await asyncio.sleep(0)          
            
    async def _update_minTemp(self,data):
        """
        Aktualisiere OGB Min Temp
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return
        
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.minTemp")
        if current_value != value:
            self.dataStore.setDeep("controlOptionData.minmax.minTemp", value)
            self.dataStore.setDeep("tentData.minTemp", value)

            
    async def _update_minHumidity(self,data):
        """
        Aktualisiere OGB Min Humidity
        """
        minMaxControl = self._stringToBool(self.dataStore.getDeep("controlOptions.minMaxControl"))
        if minMaxControl == False: return

        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.minmax.minHum")
        if current_value != value:
            self.dataStore.setDeep("controlOptionData.minmax.minHum", value)
            self.dataStore.setDeep("tentData.minHumidity", value)


    ## Weights   
    async def _update_ownWeights_control(self,data):
        """
        Update OGB Own Weights Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.ownWeights"))
        if current_value != value:
            self.dataStore.setDeep("controlOptions.ownWeights", self._stringToBool(value))
              
    async def _update_temperature_weight(self, data):
        """
        Update Temp Weight
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.weights.temp")
        if current_value != value:
            self.dataStore.setDeep("controlOptionData.weights.temp", value)
           
    async def _update_humidity_weight(self, data):
        """
        Update Humidity Weight
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.weights.hum")
        if current_value != value:
                self.dataStore.setDeep("controlOptionData.weights.hum", value)

    ## HYDRO
    async def _update_hydro_mode(self,data):
        """
        Update OGB Hydro Mode
        """
        controlOption = self.dataStore.get("mainControl")        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return
        
        value = data.newState[0]

        if value == "OFF":
            _LOGGER.info(f"{self.room}: Deaktiviere Hydro Mode")
            self.dataStore.setDeep("Hydro.Active", False)
            self.dataStore.setDeep("Hydro.Mode", value)
            await self.eventManager.emit("HydroModeChange",value)
        else:
            _LOGGER.info(f"{self.room}: Update Hydro Mode to {value}")
            self.dataStore.setDeep("Hydro.Active", True)
            self.dataStore.setDeep("Hydro.Mode", value)
            await self.eventManager.emit("HydroModeChange",value)
    
    async def _update_hydro_mode_cycle(self,data):
        """
        Update OGB Hydro Cycle
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("Hydro.Cycle"))
        if current_value != value:
            self.dataStore.setDeep("Hydro.Cycle", self._stringToBool(value))
            await self.eventManager.emit("HydroModeChange",value)
           
    
    async def _update_hydro_duration(self, data):
        """
        Update Hydro Duration with validation
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("Hydro.Duration")
        
        # Validate the incoming value
        try:
            validated_value = float(value) if value is not None else 30.0
            if validated_value <= 0:
                validated_value = 30.0
        except (ValueError, TypeError):
            validated_value = 30.0  # Default fallback
            _LOGGER.error(f"{self.room}: Invalid duration value '{value}', using default 30s")
        if current_value != validated_value:
            self.dataStore.setDeep("Hydro.Duration", validated_value)
            await self.eventManager.emit("HydroModeChange", validated_value)

            
    async def _update_hydro_intervall(self, data):
        """
        Update Hydro Intervall with validation
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("Hydro.Intervall")
        
        try:
            validated_value = float(value) if value is not None else 60.0
            if validated_value <= 0:
                validated_value = 60.0 
        except (ValueError, TypeError):
            validated_value = 60.0 
            _LOGGER.error(f"{self.room}: Invalid interval value '{value}', using default 60s")
        if current_value != validated_value:
            self.dataStore.setDeep("Hydro.Intervall", validated_value)
            await self.eventManager.emit("HydroModeChange", validated_value)


    ## FEED
    async def _update_feed_mode(self,data):
        """
        Update OGB Feed Modes
        """
        controlOption = self.dataStore.get("mainControl")        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return
        
        value = data.newState[0]

        if value == "Disabled":
            self.dataStore.setDeep("Feed.Active", False)
            self.dataStore.setDeep("Feed.Mode", value)
            await self.eventManager.emit("FeedModeChange",value)
        else:
            self.dataStore.setDeep("Feed.Active", True)
            self.dataStore.setDeep("Feed.Mode", value)
            await self.eventManager.emit("FeedModeChange",value)
    
    async def _update_feed_ec_target(self, data):
        """
        Update Hydro Feed EC 
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.EC_Target")
                
        if current_value != new_value:
            
            self.dataStore.setDeep("Feed.EC_Target", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "ec_target",
                "value": new_value
            })

    async def _update_feed_ph_target(self, data):
        """
        Update Feed Feed PH 
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.PH_Target")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.PH_Target", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "ph_target",
                "value": new_value
            })

    async def _update_feed_nut_a_ml(self,data):
        """
        Update Feed Feed PH 
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.Nut_A_ml")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.Nut_A_ml", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "a_ml",
                "value": new_value
            })

    async def _update_feed_nut_b_ml(self,data):
        """
        Update Feed Feed PH 
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.Nut_B_ml")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.Nut_B_ml", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "b_ml",
                "value": new_value
            })
            
    async def _update_feed_nut_c_ml(self,data):
        """
        Update Feed Feed PH 
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.Nut_C_ml")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.Nut_C_ml", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "c_ml",
                "value": new_value
            })

    async def _update_feed_nut_w_ml(self,data):
        """
        Update Feed Feed PH 
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.Nut_W_ml")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.Nut_W_ml", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "w_ml",
                "value": new_value
            })

    async def _update_feed_nut_x_ml(self,data):
        """
        Update Feed Feed PH 
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.Nut_X_ml")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.Nut_X_ml", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "x_ml",
                "value": new_value
            })
                 
    async def _update_feed_nut_y_ml(self,data):
        """
        Update Feed Feed Y
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.Nut_Y_ml")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.Nut_Y_ml", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "y_ml",
                "value": new_value
            })

    async def _update_feed_nut_ph_ml(self, data):
        """
        Update Feed Nutrient PH ml
        """
        new_value = data.newState[0]
        current_value = self.dataStore.getDeep("Feed.Nut_PH_ml")
                
        if current_value != new_value:
            self.dataStore.setDeep("Feed.Nut_PH_ml", new_value)
            await self.eventManager.emit("FeedModeValueChange", {
                "type": "ph_ml",
                "value": new_value
            })

    ## HYDRO RETRIVE
    async def _update_retrive_mode(self,data):
        """
        Update OGB Water Retrive Hydro Mode
        """
        controlOption = self.dataStore.get("mainControl")        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return
        
        value = self._stringToBool(data.newState[0])
        if value == True:
            self.dataStore.setDeep("Hydro.Retrieve", True)
            self.dataStore.setDeep("Hydro.R_Active", True)
            await self.eventManager.emit("HydroModeRetrieveChange",value)
        else:
            self.dataStore.setDeep("Hydro.Retrieve", False)
            self.dataStore.setDeep("Hydro.R_Active", False)
            await self.eventManager.emit("HydroModeRetrieveChange",value)

    async def _update_hydro_retrive_duration(self, data):
        """
        Update Hydro Duration
        """
        value = data.newState[0]  # Beispiel: Extrahiere den neuen Wert
        current_value = self.dataStore.getDeep("Hydro.R_Duration")
        if current_value != value:
            self.dataStore.setDeep("Hydro.R_Duration", value)
            await self.eventManager.emit("HydroModeRetriveChange",value)
  
    async def _update_hydro_retrive_intervall(self, data):
        """
        Update Hydro Intervall.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("Hydro.R_Intervall")
        if current_value != value:
            self.dataStore.setDeep("Hydro.R_Intervall", value)
            await self.eventManager.emit("HydroModeRetriveChange",value)

    
    ### Controll Updates           
    async def _update_ogbLightControl_control(self,data):
        """
        Update OGB Light Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.lightbyOGBControl"))
        if current_value != value:
            self.dataStore.setDeep("controlOptions.lightbyOGBControl", self._stringToBool(value))
            await self.eventManager.emit("updateControlModes",self._stringToBool(value))
                  
    async def _update_vpdLight_control(self,data):
        """
        OGB VPD Light Controll to dimm Light for better vpd Control
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.vpdLightControl"))

        self.dataStore.setDeep("controlOptions.vpdLightControl", self._stringToBool(value))
        
        await self.eventManager.emit("updateControlModes",self._stringToBool(value))   
        await self.eventManager.emit("VPDLightControl",self._stringToBool(value))
            
    async def _update_vpdNightHold_control(self,data):
        """
        Update VPD Night Control 
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.nightVPDHold"))
        if current_value != value:
            self.dataStore.setDeep("controlOptions.nightVPDHold", self._stringToBool(value))
            await self.eventManager.emit("updateControlModes",self._stringToBool(value))    
    
    
    #### CO2                 
    async def _update_co2_control(self,data):
        """
        Update OGB CO2 Control 
        """
        value = data.newState[0]
        current_value = self._stringToBool(self.dataStore.getDeep("controlOptions.co2Control"))
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update CO2 Control to {value}")
            self.dataStore.setDeep("controlOptions.co2Control", self._stringToBool(value))
           
    async def _update_co2Target_value(self,data):
        """
        Update CO2 Target Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.co2ppm.target")
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update CO2 Target Value to {value}")
            self.dataStore.setDeep("controlOptionData.co2ppm.target", value)
  
    async def _update_co2Min_value(self,data):
        """
        Update CO2 Min Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.co2ppm.minPPM")
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update CO2 Min Value to {value}")
            self.dataStore.setDeep("controlOptionData.co2ppm.minPPM", value)

    async def _update_co2Max_value(self,data):
        """
        Update CO2 Max Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("controlOptionData.co2ppm.maxPPM")
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update CO2 Max Value to {value}")
            self.dataStore.setDeep("controlOptionData.co2ppm.maxPPM", value)
 
 
    ## PlantDates
    async def _update_breederbloomdays_value(self,data):
        """
        Update FlowerTime Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("plantDates.breederbloomdays")
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update Breeder Bloom Days to {value}")
            self.dataStore.setDeep("plantDates.breederbloomdays", value)
            await self.eventManager.emit("PlantTimeChange",value)
            
    async def _update_growstartdates_value(self,data):
        """
        Update GrowStart Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("plantDates.growstartdate")
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update Grow Start to {value}")
            self.dataStore.setDeep("plantDates.growstartdate", value)
            await self.eventManager.emit("PlantTimeChange",value)
    
    async def _update_bloomswitchdate_value(self,data):
        """
        Update Bloom Start Date Value.
        """
        value = data.newState[0]
        current_value = self.dataStore.getDeep("plantDates.bloomswitchdate")
        if current_value != value:
            self.dataStore.setDeep("plantDates.bloomswitchdate", value)
            await self.eventManager.emit("PlantTimeChange",value)

    async def _update_plantDates(self, data):
        """
        Update Plant Grow Times
        """
        planttotaldays_entity = f"sensor.ogb_planttotaldays_{self.room.lower()}"
        totalbloomdays_entity = f"sensor.ogb_totalbloomdays_{self.room.lower()}"
        remainingTime_entity = f"sensor.ogb_chopchoptime_{self.room.lower()}"
        
        bloomSwitch = self.dataStore.getDeep("plantDates.bloomswitchdate")
        growstart = self.dataStore.getDeep("plantDates.growstartdate")
        breederDays = self.dataStore.getDeep("plantDates.breederbloomdays")

        try:
            breeder_bloom_days = float(breederDays)
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.room}: Ungültiger Wert für breederbloomdays: {breederDays}")
            breeder_bloom_days = 0.0

        planttotaldays = 0
        totalbloomdays = 0
        remaining_bloom_days = 0
        
        today = datetime.today()

        try:
            growstart_date = datetime.strptime(growstart, '%Y-%m-%d')
            planttotaldays = (today - growstart_date).days
            self.dataStore.setDeep("plantDates.planttotaldays", planttotaldays)
        except ValueError:
            _LOGGER.error(f"{self.room}: Ungültiges Datum im growstart: {growstart}")

        try:
            bloomswitch_date = datetime.strptime(bloomSwitch, '%Y-%m-%d')
            totalbloomdays = (today - bloomswitch_date).days
            self.dataStore.setDeep("plantDates.totalbloomdays", totalbloomdays)
        except ValueError:
            _LOGGER.error(f"{self.room}: Ungültiges Datum im bloomSwitch: {bloomSwitch}")
        if breeder_bloom_days > 0 and totalbloomdays > 0:
            remaining_bloom_days = breeder_bloom_days - totalbloomdays
            if remaining_bloom_days <= 0:
                _LOGGER.info(f"{self.room}: Die erwartete Blütezeit from {breeder_bloom_days} Tagen ist erreicht oder überschritten.")
                ## Notify User when Notify manager is DONE
            else:
                _LOGGER.info(f"{self.room}: Noch {remaining_bloom_days} Tage bis zum Ende der erwarteten Blütezeit.")

        self.dataStore.setDeep("plantDates.daysToChopChop",remaining_bloom_days)
        
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
        except Exception as e:
            _LOGGER.error(f"Fehler beim Updaten der Sensoren '{planttotaldays_entity}' und '{totalbloomdays_entity}': {e}")

    async def _autoUpdatePlantStages(self,data):
        timenow = datetime.now() 
        await self._update_plantDates(timenow)
        await asyncio.sleep(8 * 60 * 60)  # 8 Stunden warten
        asyncio.create_task(self._autoUpdatePlantStages(timenow))  # Nächste Ausführung starten
 
    ## Area
    
    async def _update_Grow_Area(self,data):
        """
        Update Grow Area Space.
        """
        value = data.newState[0]
        current_value = self.dataStore.get("growAreaM2")
        if current_value != value:
            self.dataStore.set("growAreaM2", value)      
       
    ## Drying
    async def _udpate_drying_mode(self, data):
        """
        Update Current Working Tent Mode
        """
        value = data.newState[0]
        current_mode = self.dataStore.getDeep("drying.currentDryMode")
        if current_mode != value:
            self.dataStore.setDeep("drying.currentDryMode",value)
              
            
    ### Own Device Selects
    async def _udpate_own_deviceSelect(self,data):
        """
        Update Own Device Lists Select
        """
        value = self._stringToBool(data.newState[0])
        self.dataStore.setDeep("controlOptions.ownDeviceSetup", value)
        currentDevices = self.dataStore.getDeep("workData.Devices")
        await self.eventManager.emit("capClean",currentDevices)    
      
    async def _add_selectedDevice(self,data):
        """
        Update New Selected Devices 
        """
        ownDeviceSetup = self.dataStore.getDeep("controlOptions.ownDeviceSetup")
        if ownDeviceSetup:
            await self.eventManager.emit("MapNewDevice",data)
        return

    # Devices 
    async def _device_Self_MinMax(self,data):
        """
        Update Own Device Min Max Activation
        """
        value = self._stringToBool(data.newState[0])      
        if "exhaust" in data.Name:
                self.dataStore.setDeep("DeviceMinMax.Exhaust.active",value)
        if "Intake" in data.Name:
                self.dataStore.setDeep("DeviceMinMax.Intake.active",value)
        if "ventilation" in data.Name:
                self.dataStore.setDeep("DeviceMinMax.Ventilation.active",value)    
        if "light" in data.Name:
                self.dataStore.setDeep("DeviceMinMax.Light.active",value)
        
        await self.eventManager.emit("SetMinMax",data)
   
    async def _device_MinMax_setter(self, data):
        """
        Update OGB Min Max Sets For Devices
        """

        value = data.newState[0]
        name = data.Name.lower()

        # Exhaust
        if "exhaust" in name:
            if "min" in name:
                self.dataStore.setDeep("DeviceMinMax.Exhaust.minDuty", value)
            if "max" in name:
                self.dataStore.setDeep("DeviceMinMax.Exhaust.maxDuty", value)

        if "intake" in name:
            if "min" in name:
                self.dataStore.setDeep("DeviceMinMax.Intake.minDuty", value)
            if "max" in name:
                self.dataStore.setDeep("DeviceMinMax.Intake.maxDuty", value)
        
        # Vents
        if "ventilation" in name:
            if "min" in name:
                self.dataStore.setDeep("DeviceMinMax.Ventilation.minDuty", value)
            if "max" in name:
                self.dataStore.setDeep("DeviceMinMax.Ventilation.maxDuty", value)

        # Lights
        if "light" in name:
            if "min" in name:
                self.dataStore.setDeep("DeviceMinMax.Light.minVoltage", value)
            if "max" in name:
                self.dataStore.setDeep("DeviceMinMax.Light.maxVoltage", value)
        
        await self.eventManager.emit("SetMinMax",data)
    
    async def _update_StrainName(self,data):
        """
        Update OGB Current Strain
        """
        value = data.newState[0]
        current_value = self.dataStore.get("strainName")
        if current_value != value:
            self.dataStore.set("strainName", value)


    ## Debug NOTES
    def _debugState(self):
        ##warning
        devices = self.dataStore.get("devices")
        tentData = self.dataStore.get("tentData")
        controlOptions = self.dataStore.get("controlOptions")
        workdata = self.dataStore.get("workData")
        vpdData = self.dataStore.get("vpd")
        caps = self.dataStore.get("capabilities")
        _LOGGER.debug(f"DEBUGSTATE: {self.room} WorkData: {workdata} DEVICES:{devices} TentData {tentData} CONTROLOPTIONS:{controlOptions}  VPDDATA {vpdData} CAPS:{caps} ")

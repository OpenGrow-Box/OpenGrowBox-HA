import logging
import asyncio

_LOGGER = logging.getLogger(__name__)

from .OGBDataClasses.OGBPublications import OGBActionPublication,OGBWeightPublication,OGBHydroAction

class OGBActionManager:
    def __init__(self, hass, dataStore, eventManager,room):
        self.name = "OGB Device Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore  # Bereits bestehendes DataStore-Objekt
        self.eventManager = eventManager
        self.isInitialized = False
        
        ## Events Register
        self.eventManager.on("increase_vpd", self.increase_vpd)
        self.eventManager.on("reduce_vpd", self.reduce_vpd)
        self.eventManager.on("FineTune_vpd", self.fine_tune_vpd)
        self.eventManager.on("PumpAction", self.PumpAction) 
    
    
    async def increase_vpd(self, capabilities):
        """
        Erhöht den VPD-Wert durch Anpassung der entsprechenden Geräte.
        """
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")
        lightbyOGBControl = self.dataStore.getDeep("controlOptions.lightbyOGBControl")
        islightON = self.dataStore.getDeep("isPlantDay.islightON")
        
        actionMessage = "VPD-Increase Action"
        
        actionMap = []
        if capabilities["canExhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canInhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canInhaust",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canVentilate"]["state"]:
            actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canHumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canHumidify",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canDehumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canDehumidify",action="Increase",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)            
        if capabilities["canHeat"]["state"]:
            actionPublication = OGBActionPublication(capability="canHeat",action="Increase",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)                        
        if capabilities["canCool"]["state"]:
            actionPublication = OGBActionPublication(capability="canCool",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canClimate"]["state"]:
                actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)                  
        if capabilities["canCO2"]["state"]:
                actionPublication = OGBActionPublication(capability="canCO2",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)               
        if vpdLightControl == True:
            if capabilities["canLight"]["state"]:
                actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)               
            else:
                return
            
        await self.checkLimitsAndPublicate(actionMap)
        #await self.eventManager.emit("LogForClient",actionMap,haEvent=True)
            
    async def reduce_vpd(self, capabilities):
        """
        Reduziert den VPD-Wert durch Anpassung der entsprechenden Geräte.
        """
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")
        lightbyOGBControl = self.dataStore.getDeep("controlOptions.lightbyOGBControl")
        islightON = self.dataStore.getDeep("isPlantDay.islightON")
        
        actionMessage = "VPD-Reduce Action"
        
        actionMap = []
        if capabilities["canExhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canExhaust",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canInhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canVentilate"]["state"]:
            actionPublication = OGBActionPublication(capability="canVentilate",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canHumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canHumidify",action="Increase",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canDehumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canDehumidify",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canHeat"]["state"]:
            actionPublication = OGBActionPublication(capability="canHeat",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canCool"]["state"]:
            actionPublication = OGBActionPublication(capability="canCool",action="Increase",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if capabilities["canClimate"]["state"]:
                actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)      
        if capabilities["canCO2"]["state"]:
            actionPublication = OGBActionPublication(capability="canCO2",action="Reduce",Name=self.room,message=actionMessage)
            actionMap.append(actionPublication)
        if vpdLightControl == True:
            if capabilities["canLight"]["state"]:
                actionPublication = OGBActionPublication(capability="canLight",action="Reduce",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            else:
                return
            
        await self.checkLimitsAndPublicate(actionMap)
        #await self.eventManager.emit("LogForClient",actionMap,haEvent=True)       
        
    async def fine_tune_vpd(self, capabilities):
        """
        Feintuning des VPD-Wertes, um den Zielwert zu erreichen.
        """
        
        # Aktuelle VPD-Werte abrufen
        currentVPD = self.dataStore.getDeep("vpd.current")
        perfectionVPD = self.dataStore.getDeep("vpd.perfection")
        
        # Delta berechnen und auf zwei Dezimalstellen runden
        delta = round(perfectionVPD - currentVPD, 2)
    
        if delta > 0:
            _LOGGER.warning(f"Fine-tuning: {self.room}Increasing VPD by {delta}.")
            await self.increase_vpd(capabilities)
        elif delta < 0:
            _LOGGER.warning(f"Fine-tuning: {self.room} Reducing VPD by {-delta}.")
            await self.reduce_vpd(capabilities)

    async def checkLimitsAndPublicate(self,actionMap):
        _LOGGER.warn(f"{self.room}: Action Publication Limits-Validation von {actionMap}")    
        
        ownWeights = self.dataStore.getDeep("controlOptions.ownWeights")
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")
        nightVPDHold = self.dataStore.getDeep("controlOptions.nightVPDHold")
        islightON = self.dataStore.getDeep("isPlantDay.islightON")
        
        if islightON == False and nightVPDHold == False:
            _LOGGER.warn(f"{self.room}: VPD Night Hold Not Activ Ignoring VPD ") 
            await self.NightHoldFallBack(actionMap)
            return None
        
        # Gewichtungen basierend auf eigenen Werten oder Pflanzenphase festlegen
        if ownWeights:
            tempWeight = self.dataStore.getDeep("controlOptionData.weights.temp")
            humWeight = self.dataStore.getDeep("controlOptionData.weights.hum")
        else:
            plantStage = self.dataStore.get("plantStage")
            plantMap = ["LateFlower", "MidFlower"]

        if plantStage in plantMap:
            tempWeight = self.dataStore.getDeep("controlOptionData.weights.defaultValue") * 1
            humWeight = self.dataStore.getDeep("controlOptionData.weights.defaultValue") * 1.25
        else:
            tempWeight = self.dataStore.getDeep("controlOptionData.weights.defaultValue")
            humWeight = self.dataStore.getDeep("controlOptionData.weights.defaultValue")

        # Werte aus tentData abrufen
        tentData = self.dataStore.get("tentData")
        tempDeviation = 0
        humDeviation = 0
        weightMessage = ""
        
        # Temperaturabweichung prüfen
        if tentData["temperature"] > tentData["maxTemp"]:
            tempDeviation = round((tentData["temperature"] - tentData["maxTemp"]) * tempWeight, 2)
            weightMessage = f"Temp To High: Deviation {tempDeviation}"
        
        elif tentData["temperature"] < tentData["minTemp"]:
            tempDeviation = round((tentData["temperature"] - tentData["minTemp"]) * tempWeight, 2)
            weightMessage = f"Temp To Low: Deviation {tempDeviation}"   
        # Feuchtigkeitsabweichung prüfen
        if tentData["humidity"] > tentData["maxHumidity"]:
            humDeviation = round((tentData["humidity"] - tentData["maxHumidity"]) * humWeight, 2)
           
            weightMessage = f"Humidity To High: Deviation {humDeviation}"
        elif tentData["humidity"] < tentData["minHumidity"]:
            humDeviation = round((tentData["humidity"] - tentData["minHumidity"]) * humWeight, 2)
         
            weightMessage = f"Humidity To Low: Deviation {humDeviation}"

        WeightPublication = OGBWeightPublication(Name=self.room,message=weightMessage,tempDeviation=tempDeviation,humDeviation=humDeviation,tempWeight=tempWeight,humWeight=humWeight)
        await self.eventManager.emit("LogForClient",WeightPublication,haEvent=True)   
        
        # **Capabilities abrufen**
        caps = self.dataStore.get("capabilities")

        # Aktionen basierend auf Abweichungen ausführen
        if tempDeviation > 0 or humDeviation > 0:
            # **Hohe Temperatur + Hohe Feuchtigkeit**
            if tempDeviation > 0 and humDeviation > 0:
                actionMessage =f"{self.name} Fall: Hohe Temperatur + Hohe Feuchtigkeit in {self.room}."
                if caps["canHumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHumidify",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canDehumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canDehumidify",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)    
                if caps["canClimate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canCool"]["state"]:
                    actionPublication = OGBActionPublication(capability="canCool",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)    
                if caps["canHeat"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHeat",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canExhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canInhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canVentilate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if vpdLightControl == True:
                    if caps["canLight"]["state"]:
                        actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage)
                        actionMap.append(actionPublication) 
                        
            # **Hohe Temperatur + Niedrige Feuchtigkeit**
            elif tempDeviation > 0 and humDeviation < 0:
                actionMessage = f"{self.name} Fall: Hohe Temperatur + Niedrige Feuchtigkeit in {self.room}."

                if caps["canHumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHumidify",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canDehumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canDehumidify",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canClimate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canHeat"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHeat",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canCool"]["state"]:
                    actionPublication = OGBActionPublication(capability="canCool",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canExhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canInhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canVentilate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if vpdLightControl == True:
                    if caps["canLight"]["state"]:
                        actionPublication = OGBActionPublication(capability="canLight",action="Reduce",Name=self.room,message=actionMessage)
                        actionMap.append(actionPublication)  
            # **Niedrige Temperatur + Hohe Feuchtigkeit**
            elif tempDeviation < 0 and humDeviation > 0:
                actionMessage = f"{self.name} Fall: Niedrige Temperatur + Hohe Feuchtigkeit in {self.room}."

                if caps["canHumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHumidify",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canDehumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canDehumidify",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canClimate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canHeat"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHeat",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canCool"]["state"]:
                    actionPublication = OGBActionPublication(capability="canCool",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canExhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canInhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canVentilate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if vpdLightControl == True:
                    if caps["canLight"]["state"]:
                        actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage)
                        actionMap.append(actionPublication)
                                              
            # **Niedrige Temperatur + Niedrige Feuchtigkeit**
            elif tempDeviation < 0 and humDeviation < 0:
                actionMessage = f"{self.name} Fall: Niedrige Temperatur + Niedrige Feuchtigkeit in {self.room}."
                if caps["canDehumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canDehumidify",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canHumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHumidify",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canClimate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canHeat"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHeat",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canCool"]["state"]:
                    actionPublication = OGBActionPublication(capability="canCool",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canExhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canExhaust",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canInhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canInhaust",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)   
                if caps["canVentilate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canVentilate",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if vpdLightControl == True:
                    if caps["canLight"]["state"]:
                        actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                                     
        # **Notfallmaßnahmen**
        # **Hohe Temperatur > maxTemp + 5**
        if tentData["temperature"] > tentData["maxTemp"] + 3:
            actionMessage = f"{self.name} Kritische Übertemperatur in {self.room}! Notfallmaßnahmen aktiviert."

            if caps["canClimate"]["state"]:
                actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canHeat"]["state"]:
                actionPublication = OGBActionPublication(capability="canHeat",action="Reduce",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication) 
            if caps["canCool"]["state"]:
                actionPublication = OGBActionPublication(capability="canCool",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication) 
            if caps["canExhaust"]["state"]:
                actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canInhaust"]["state"]:
                actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)   
            if caps["canVentilate"]["state"]:
                actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)  
            if vpdLightControl == True:
                if caps["canLight"]["state"]:
                    actionPublication = OGBActionPublication(capability="canLight",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)

        # **Niedrige Temperatur < minTemp - 5**
        if tentData["temperature"] < tentData["minTemp"] - 3:
            actionMessage = f"{self.name} Kritische Untertemperatur in {self.room}! Notfallmaßnahmen aktiviert."
            if caps["canClimate"]["state"]:
                actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canHeat"]["state"]:
                actionPublication = OGBActionPublication(capability="canHeat",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canCool"]["state"]:
                actionPublication = OGBActionPublication(capability="canCool",action="Reduce",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication) 
            if caps["canExhaust"]["state"]:
                actionPublication = OGBActionPublication(capability="canExhaust",action="Reduce",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canInhaust"]["state"]:
                actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)    
            if caps["canVentilate"]["state"]:
                actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)  
            if vpdLightControl == True:
                if caps["canLight"]["state"]:
                    actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                    
                    
        # **CO₂-Management**
        co2Control = self.dataStore.getDeep("controlOptions.co2Control")
        co2Level = int(self.dataStore.getDeep("controlOptionData.co2ppm.current"))
        if co2Control == True:
            if co2Level < 500 and islightON:
                actionMessage = f"{self.name} CO₂-Level zu niedrig in {self.room}, CO₂-Zufuhr erhöht."
                if caps["canClimate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canCO2"]["state"]:
                    actionPublication = OGBActionPublication(capability="canCO2",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canExhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canExhaust",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canInhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canInhaust",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canDehumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canDehumidify",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canHumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHumidify",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canVentilate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if vpdLightControl == True:
                    if caps["canLight"]["state"]:
                        actionPublication = OGBActionPublication(capability="canLight",action="Reduce",Name=self.room,message=actionMessage)
                        actionMap.append(actionPublication)
            elif co2Level > 1200 and islightON:
                actionMessage = f"{self.name} CO₂-Level zu hoch in {self.room}, Abluft erhöht."
                if caps["canClimate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canCO2"]["state"]:
                    actionPublication = OGBActionPublication(capability="canCO2",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if caps["canExhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canInhaust"]["state"]:
                    actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                if caps["canDehumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canDehumidify",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication) 
                if caps["canHumidify"]["state"]:
                    actionPublication = OGBActionPublication(capability="canHumidify",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)    
                if caps["canVentilate"]["state"]:
                    actionPublication = OGBActionPublication(capability="canVentilate",action="Reduce",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)  
                if vpdLightControl == True:
                    if caps["canLight"]["state"]:
                        actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage)
                        actionMap.append(actionPublication)

        # **Taupunkt- und Kondensationsschutz**
        if tentData["dewpoint"] >= tentData["temperature"] - 1:
            actionMessage = f"{self.name} Taupunkt erreicht in {self.room}, Feuchtigkeit reduziert."
            if caps["canClimate"]["state"]:
                actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canExhaust"]["state"]:
                actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canInhaust"]["state"]:
                actionPublication = OGBActionPublication(capability="canInhaust",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if caps["canDehumidify"]["state"]:
                actionPublication = OGBActionPublication(capability="canDehumidify",action="Reduce",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication) 
            if caps["canHumidify"]["state"]:
                actionPublication = OGBActionPublication(capability="canHumidify",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)     
            if caps["canVentilate"]["state"]:
                actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage)
                actionMap.append(actionPublication)
            if vpdLightControl == True:
                if caps["canLight"]["state"]:
                    actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage)
                    actionMap.append(actionPublication)
                
                
        await self.publicationActionHandler(actionMap)
        await self.eventManager.emit("LogForClient",actionMap,haEvent=True)        
    
    async def NightHoldFallBack(self, actionMap):
        _LOGGER.warn(f"{self.room}: VPD Night Hold NOT ACTIVE IGNORING ACTIONS ")
        await self.eventManager.emit("LogForClient",{"Name":self.room,"NightVPDHold":"NotActive Ignoring-VPD"},haEvent=True)     
        
        # Capabilities abrufen
        excludeCaps = {"canHeat", "canCool", "canHumidify", "canClimate", "canDehumidify", "canLight", "canCO2","canInhaust"}
        modCaps = {"canHeat", "canCool", "canHumidify", "canClimate", "canDehumidify", "canCO2"}
        fallBackAction = "Reduce"
        
        # Gefilterte ActionMap erstellen (nur erlaubte Actions)
        filteredActions = [action for action in actionMap if action.capability not in excludeCaps]

        # Neue Action-Liste mit Reduced-Actions für alle anderen Geräte erstellen
        reducedActions = [
            OGBActionPublication(capability=action.capability, action=fallBackAction,Name=self.room,message="VPD-NightHold Device Shutdown")
            for action in actionMap if action.capability in modCaps
        ]

        # Wenn es gefilterte oder reduzierte Aktionen gibt, verarbeiten
        if filteredActions or reducedActions:
            await self.publicationActionHandler(filteredActions + reducedActions)
        
    async def publicationActionHandler(self, actionMap):
        """
        Handhabt die Steuerungsaktionen basierend auf dem actionMap und den Fähigkeiten.
        """
        _LOGGER.warn(f"{self.room}: Validated-Actions-By-Limits: - {actionMap}")

        for action in actionMap:
            actionCap = action.capability
            actionType = action.action
            actionMesage = action.message
            _LOGGER.warn(f"{self.room}: {actionCap} - {actionType} - - {action} -- {actionMesage}")
                    
            # Aktionen basierend auf den Fähigkeiten
            if actionCap == "canExhaust":
                await self.eventManager.emit(f"{actionType} Exhaust", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Exhaust ausgeführt.")
            if actionCap == "canInhaust":
                await self.eventManager.emit(f"{actionType} Inhaust", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Inhaust ausgeführt.")
            if actionCap == "canVentilate":
                await self.eventManager.emit(f"{actionType} Ventilation", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Ventilation ausgeführt.")
            if actionCap == "canHumidify":
                await self.eventManager.emit(f"{actionType} Humidifier", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Humidifier ausgeführt.")
            if actionCap == "canDehumidify":
                await self.eventManager.emit(f"{actionType} Dehumidifier", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Dehumidifier ausgeführt.")
            if actionCap == "canHeat":
                await self.eventManager.emit(f"{actionType} Heater", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Heater ausgeführt.")
            if actionCap == "canCool":
                await self.eventManager.emit(f"{actionType} Cooler", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Cooler ausgeführt.")
            if actionCap == "canClimate":
                await self.eventManager.emit(f"{actionType} Climate", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} CO2 ausgeführt.")
            if actionCap == "canCO2":
                await self.eventManager.emit(f"{actionType} CO2", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} CO2 ausgeführt.")
            if actionCap == "canLight":
                await self.eventManager.emit(f"{actionType} Light", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Light ausgeführt.")
 
    async def PumpAction(self, pumpAction: OGBHydroAction):
        if isinstance(pumpAction, dict):
            dev = pumpAction.get("Device") or pumpAction.get("id") or "<unknown>"
            action = pumpAction.get("Action") or pumpAction.get("action")
        else:
            # your dataclass
            dev = pumpAction.Device
            action = pumpAction.Action
            
        if action == "on":
            await self.eventManager.emit(
                "LogForClient",
                f"Name: {self.room} Start Pump: {dev}",
                haEvent=True
            )
            await self.eventManager.emit("Increase Pump", pumpAction)

        elif action == "off":
            await self.eventManager.emit(
                "LogForClient",
                f"Name: {self.room} Stop Pump: {dev}",
                haEvent=True
            )
            await self.eventManager.emit("Reduce Pump", pumpAction)

        else:
            # unknown action
            return None

import logging
import asyncio
import copy
import dataclasses
import time
from datetime import datetime, timedelta

_LOGGER = logging.getLogger(__name__)

from .OGBDataClasses.OGBPublications import OGBActionPublication,OGBWeightPublication,OGBHydroAction,OGBWaterAction,OGBRetrieveAction

class OGBActionManager:
    def __init__(self, hass, dataStore, eventManager,room):
        self.name = "OGB Device Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.isInitialized = False
    
        self.actionHistory = {}  # {capability: {"last_action": datetime, "action_type": str, "cooldown_until": datetime}}
        self.defaultCooldownMinutes = {
            "canHumidify": 3,      # Befeuchter braucht Zeit
            "canDehumidify": 4,    # Entfeuchter braucht noch mehr Zeit
            "canHeat": 1,          # Heizung reagiert relativ schnell
            "canCool": 2,          # Kühlung braucht etwas Zeit
            "canExhaust": 1,       # Abluft reagiert schnell
            "canIntake": 1,        # Zuluft reagiert schnell
            "canVentilate": 1,     # Ventilation reagiert schnell
            "canLight": 1,         # Licht reagiert sofort, aber VPD-Effekt braucht Zeit
            "canCO2": 2,           # CO2 braucht Zeit zur Verteilung
            "canClimate": 2        # Klima-System braucht Zeit
        }
        
        self.adaptiveCooldownEnabled = True
        
        ## Events Register
        self.eventManager.on("increase_vpd", self.increase_action)
        self.eventManager.on("reduce_vpd", self.reduce_action)
        self.eventManager.on("FineTune_vpd", self.fineTune_action)

        self.eventManager.on("PIDActions",self.PIDActions)    

        # Water Events
        self.eventManager.on("PumpAction", self.PumpAction) 
        self.eventManager.on("RetrieveAction",self.RetrieveAction)
 
    def _isActionAllowed(self, capability, action, deviation=0):
        """Prüft ob eine Aktion erlaubt ist basierend auf Cooldown"""
        now = datetime.now()
        
        if capability not in self.actionHistory:
            return True
            
        history = self.actionHistory[capability]
        
        # NEW: Skip cooldown for emergency actions
        if hasattr(self, '_emergency_mode') and self._emergency_mode:
            _LOGGER.warning(f"{self.room}: Emergency mode - bypassing cooldown for {capability}")
            return True
        
        # Prüfe ob noch im Cooldown
        if now < history.get("cooldown_until", now):
            _LOGGER.debug(f"{self.room}: {capability} noch im Cooldown bis {history['cooldown_until']}")
            return False
            
        # Prüfe ob es die gleiche Aktion ist (verhindert schnelle Wiederholungen)
        if history.get("action_type") == action and now < history.get("repeat_cooldown", now):
            _LOGGER.debug(f"{self.room}: {capability} Wiederholung von '{action}' noch blockiert")
            return False
            
        return True
    
    def _calculateAdaptiveCooldown(self, capability, deviation):
        """Berechnet adaptive Cooldown-Zeit basierend auf Abweichung"""
        baseCooldown = self.defaultCooldownMinutes.get(capability, 2)
        
        if not self.adaptiveCooldownEnabled:
            return baseCooldown
            
        # Je größer die Abweichung, desto länger der Cooldown (mehr Zeit zum Wirken)
        if abs(deviation) > 5:
            return baseCooldown * 1.5
        elif abs(deviation) > 3:
            return baseCooldown * 1.2
        elif abs(deviation) < 1:
            return baseCooldown * 0.8
            
        return baseCooldown

    def _registerAction(self, capability, action, deviation=0):
        """Registriert eine Aktion im History-System"""
        now = datetime.now()
        
        cooldownMinutes = self._calculateAdaptiveCooldown(capability, deviation)
        cooldownUntil = now + timedelta(minutes=cooldownMinutes)
        
        # Längerer Cooldown für Wiederholungen derselben Aktion
        repeatCooldown = now + timedelta(minutes=cooldownMinutes * 0.5)
        
        self.actionHistory[capability] = {
            "last_action": now,
            "action_type": action,
            "cooldown_until": cooldownUntil,
            "repeat_cooldown": repeatCooldown,
            "deviation": deviation
        }
        
        _LOGGER.debug(f"{self.room}: {capability} '{action}' registriert, Cooldown bis {cooldownUntil}")

    def _filterActionsByDampening(self, actionMap, tempDeviation=0, humDeviation=0):
        """Filtert Actions basierend auf Dampening-Regeln"""
        filteredActions = []
        blockedActions = []
        
        for action in actionMap:
            capability = action.capability
            actionType = action.action
            
            # Bestimme relevante Abweichung für diese Capability
            deviation = 0
            if capability in ["canHumidify", "canDehumidify"]:
                deviation = humDeviation
            elif capability in ["canHeat", "canCool","canClimate"]:
                deviation = tempDeviation
            else:
                deviation = max(abs(tempDeviation), abs(humDeviation))
            
            if self._isActionAllowed(capability, actionType, deviation):
                filteredActions.append(action)
                self._registerAction(capability, actionType, deviation)
            else:
                blockedActions.append(action)
                
        if blockedActions:
            _LOGGER.info(f"{self.room}: {len(blockedActions)} Aktionen durch Dampening blockiert")
            
        return filteredActions
    
    def _getEmergencyOverride(self, tentData):
        """Prüft ob Notfall-Überschreibung des Dampening notwendig ist"""
        emergencyConditions = []
        
        # FIXED: Lower threshold for emergency detection
        if tentData["temperature"] > tentData["maxTemp"]:
            emergencyConditions.append("critical_overheat")
        if tentData["temperature"] < tentData["minTemp"]:
            emergencyConditions.append("critical_cold")
        if tentData["dewpoint"] >= tentData["temperature"] - 0.5:
            emergencyConditions.append("immediate_condensation_risk")
        if tentData.get("humidity", 0) > 85:
            emergencyConditions.append("critical_humidity")
            
        return emergencyConditions
    
    def _clearCooldownForEmergency(self, emergencyConditions):
        """Löscht Cooldowns bei Notfällen"""
        if not emergencyConditions:
            return
                   
        # Set emergency mode flag
        self._emergency_mode = True
        
        # Lösche alle Cooldowns
        for capability in self.actionHistory:
            self.actionHistory[capability]["cooldown_until"] = datetime.now()
            
        # Clear emergency mode after short delay to allow actions
        asyncio.create_task(self._clear_emergency_mode())   

    async def _clear_emergency_mode(self):
        """Clear emergency mode after delay"""
        await asyncio.sleep(5)  # 5 seconds
        self._emergency_mode = False
        _LOGGER.info(f"{self.room}: Emergency mode cleared")

    # Hilfsfunktion für Dampening-Status
    def getDampeningStatus(self):
        """Gibt den aktuellen Dampening-Status zurück"""
        now = datetime.now()
        status = {}
        
        for capability, history in self.actionHistory.items():
            cooldownRemaining = history.get("cooldown_until", now) - now
            status[capability] = {
                "last_action": history.get("last_action"),
                "action_type": history.get("action_type"),
                "cooldown_remaining_seconds": max(0, cooldownRemaining.total_seconds()),
                "is_blocked": now < history.get("cooldown_until", now)
            }
            
        return status

    def clearDampeningHistory(self):
        """Löscht die Dampening-Historie (für Debugging/Reset)"""
        self.actionHistory.clear()
        _LOGGER.info(f"{self.room}: Dampening-Historie wurde zurückgesetzt")
    
    # Control Actions
    async def increase_action(self,capabilities):
        isDampeningActive =  self.dataStore.getDeep("controlOptions.vpdDeviceDampening")
       
        if isDampeningActive == True:
           await self.increase_vpd_damping(capabilities)
        else:
           await self.increase_vpd(capabilities)
           
    async def reduce_action(self,capabilities):
       isDampeningActive =  self.dataStore.getDeep("controlOptions.vpdDeviceDampening")
       
       if isDampeningActive == True:
           await self.reduce_vpd_damping(capabilities)
       else:
           await self.reduce_vpd(capabilities)     

    async def fineTune_action(self,capabilities):
        isDampeningActive =  self.dataStore.getDeep("controlOptions.vpdDeviceDampening")
       
        if isDampeningActive == True:
           await self.fine_tune_vpd_damping(capabilities)
        else:
           await self.fine_tune_vpd(capabilities)           

    async def increase_vpd(self, capabilities):
        """
        Erhöht den VPD-Wert durch Anpassung der entsprechenden Geräte.
        """
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")       
        actionMessage = "VPD-Increase Action"
        
        actionMap = []
        if capabilities["canExhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canExhaust",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canIntake"]["state"]:
            actionPublication = OGBActionPublication(capability="canIntake",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canVentilate"]["state"]:
            actionPublication = OGBActionPublication(capability="canVentilate",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canHumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canHumidify",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canDehumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canDehumidify",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)            
        if capabilities["canHeat"]["state"]:
            actionPublication = OGBActionPublication(capability="canHeat",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)                        
        if capabilities["canCool"]["state"]:
            actionPublication = OGBActionPublication(capability="canCool",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canClimate"]["state"]:
            actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)                  
        if capabilities["canCO2"]["state"]:
            actionPublication = OGBActionPublication(capability="canCO2",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)               
        if vpdLightControl == True:
            if capabilities["canLight"]["state"]:
                actionPublication = OGBActionPublication(capability="canLight",action="Increase",Name=self.room,message=actionMessage,priority="")
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
        actionMessage = "VPD-Reduce Action"
        
        actionMap = []
        if capabilities["canExhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canExhaust",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canIntake"]["state"]:
            actionPublication = OGBActionPublication(capability="canIntake",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canVentilate"]["state"]:
            actionPublication = OGBActionPublication(capability="canVentilate",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canHumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canHumidify",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canDehumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canDehumidify",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canHeat"]["state"]:
            actionPublication = OGBActionPublication(capability="canHeat",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canCool"]["state"]:
            actionPublication = OGBActionPublication(capability="canCool",action="Increase",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if capabilities["canClimate"]["state"]:
            actionPublication = OGBActionPublication(capability="canClimate",action="Eval",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)      
        if capabilities["canCO2"]["state"]:
            actionPublication = OGBActionPublication(capability="canCO2",action="Reduce",Name=self.room,message=actionMessage,priority="")
            actionMap.append(actionPublication)
        if vpdLightControl == True:
            if capabilities["canLight"]["state"]:
                actionPublication = OGBActionPublication(capability="canLight",action="Reduce",Name=self.room,message=actionMessage,priority="")
                actionMap.append(actionPublication)
            else:
                return
            
        await self.checkLimitsAndPublicate(actionMap)
        #await self.eventManager.emit("LogForClient",actionMap,haEvent=True)       
        
    async def increase_vpd_damping(self, capabilities):
        """Erhöht den VPD-Wert durch Anpassung der entsprechenden Geräte."""
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")       
        actionMessage = "VPD-Increase Action"
        
        actionMap = []
        if capabilities["canExhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canExhaust", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canIntake"]["state"]:
            actionPublication = OGBActionPublication(capability="canIntake", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canVentilate"]["state"]:
            actionPublication = OGBActionPublication(capability="canVentilate", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canHumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canHumidify", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canDehumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canDehumidify", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)            
        if capabilities["canHeat"]["state"]:
            actionPublication = OGBActionPublication(capability="canHeat", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)                        
        if capabilities["canCool"]["state"]:
            actionPublication = OGBActionPublication(capability="canCool", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canClimate"]["state"]:
            actionPublication = OGBActionPublication(capability="canClimate", action="Eval", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)                  
        if capabilities["canCO2"]["state"]:
            actionPublication = OGBActionPublication(capability="canCO2", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)               
        if vpdLightControl == True:
            if capabilities["canLight"]["state"]:
                actionPublication = OGBActionPublication(capability="canLight", action="Increase", Name=self.room, message=actionMessage, priority="")
                actionMap.append(actionPublication)               
            
        await self.checkLimitsAndPublicateWithDampening(actionMap)

    async def reduce_vpd_damping(self, capabilities):
        """Reduziert den VPD-Wert durch Anpassung der entsprechenden Geräte."""
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")       
        actionMessage = "VPD-Reduce Action"
        
        actionMap = []
        if capabilities["canExhaust"]["state"]:
            actionPublication = OGBActionPublication(capability="canExhaust", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canIntake"]["state"]:
            actionPublication = OGBActionPublication(capability="canIntake", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canVentilate"]["state"]:
            actionPublication = OGBActionPublication(capability="canVentilate", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canHumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canHumidify", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canDehumidify"]["state"]:
            actionPublication = OGBActionPublication(capability="canDehumidify", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canHeat"]["state"]:
            actionPublication = OGBActionPublication(capability="canHeat", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canCool"]["state"]:
            actionPublication = OGBActionPublication(capability="canCool", action="Increase", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if capabilities["canClimate"]["state"]:
            actionPublication = OGBActionPublication(capability="canClimate", action="Eval", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)      
        if capabilities["canCO2"]["state"]:
            actionPublication = OGBActionPublication(capability="canCO2", action="Reduce", Name=self.room, message=actionMessage, priority="")
            actionMap.append(actionPublication)
        if vpdLightControl == True:
            if capabilities["canLight"]["state"]:
                actionPublication = OGBActionPublication(capability="canLight", action="Reduce", Name=self.room, message=actionMessage, priority="")
                actionMap.append(actionPublication)
            
        await self.checkLimitsAndPublicateWithDampening(actionMap)
    
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
            _LOGGER.debug(f"Fine-tuning: {self.room}Increasing VPD by {delta}.")
            await self.increase_vpd(capabilities)
        elif delta < 0:
            _LOGGER.debug(f"Fine-tuning: {self.room} Reducing VPD by {-delta}.")
            await self.reduce_vpd(capabilities)

    async def fine_tune_vpd_damping(self, capabilities):
        """
        Feintuning des VPD-Wertes, um den Zielwert zu erreichen.
        """
        
        # Aktuelle VPD-Werte abrufen
        currentVPD = self.dataStore.getDeep("vpd.current")
        perfectionVPD = self.dataStore.getDeep("vpd.perfection")
        
        # Delta berechnen und auf zwei Dezimalstellen runden
        delta = round(perfectionVPD - currentVPD, 2)
    
        if delta > 0:
            _LOGGER.debug(f"Fine-tuning: {self.room}Increasing VPD by {delta}.")
            await self.increase_vpd_damping(capabilities)
        elif delta < 0:
            _LOGGER.debug(f"Fine-tuning: {self.room} Reducing VPD by {-delta}.")
            await self.reduce_vpd_damping(capabilities)


    # Premium Actions
    async def PIDActions(self, premActions):
        _LOGGER.warning(f"{self.room}: Start PID Actions Handling")
        
        controlData = premActions.get("actionData")
        actionData = controlData.get("controlCommands")
        pidStates = controlData.get("pidStates")
        
        pidStatesCopy = copy.deepcopy(pidStates)
        currentActions = self.dataStore.get("previousActions") 
        currentActions.append(pidStatesCopy)
        controlData["room"] = self.room
        
        if len(currentActions) > 1:
            currentActions = currentActions[-1:]
        
        self.dataStore.set("previousActions", currentActions)

        device_actions = {}
        for action in actionData:
            device = action.get("device").lower()
            if device not in device_actions:
                device_actions[device] = []
            device_actions[device].append(action)

        for device, actions in device_actions.items():
            if len(actions) > 1:
                priority_order = {'high': 1, 'medium': 2, 'low': 3}
                best_action = min(actions, key=lambda x: priority_order.get(x.get('priority', 'medium'), 2))
                actions = [best_action]
            await self.eventManager.emit("LogForClient",controlData,haEvent=True)     
            for action in actions:
                deviceAction = action.get("action")
                requestedDevice = action.get("device").lower()
               
                if requestedDevice == "error":
                    _LOGGER.error(f"{self.deviceName}: Requested CONTROL ERROR {controlData} ")
                    return
                
                #nightVPDHold = self.dataStore.getDeep("controlOptions.nightVPDHold")
                #islightON = self.dataStore.getDeep("isPlantDay.islightON")
                
                #if islightON == False and nightVPDHold == False:
                #    _LOGGER.debug(f"{self.room}: VPD Night Hold Not Activ Ignoring VPD ") 
                #    # Need tu adjust fallback vpd for night times
                #    #await self.NightHoldFallBack(actionMap)
                #    return None    

                # Aktionen basierend auf den Fähigkeiten
                if requestedDevice == "exhaust":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Exhaust", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Exhaust.")
                if requestedDevice == "intake":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Intake", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Intake.")
                if requestedDevice == "ventilate":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Ventilation", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Ventilation.")
                if requestedDevice == "humidify":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Humidifier", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Humidifier.")
                if requestedDevice == "dehumidify":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Dehumidifier", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Dehumidifier.")
                if requestedDevice == "heat":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Heater", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Heater.")
                if requestedDevice == "cool":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Cooler", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Cooler.")
                if requestedDevice == "climate":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Climate", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} CO2.")
                if requestedDevice == "co2":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} CO2", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} CO2.")
                if requestedDevice == "light":
                    await self.eventManager.emit(f"{deviceAction.capitalize()} Light", deviceAction)
                    _LOGGER.warning(f"{self.room}: {deviceAction.capitalize()} Light.")
        
        await self.eventManager.emit("SaveState",True)   

    async def MCPActions(self, premActions):
        actionData = premActions.get("actionData")
        
        # Gruppiere nach Gerät um Konflikte zu erkennen
        device_actions = {}
        for action in actionData:
            device = action.get("device").lower()
            if device not in device_actions:
                device_actions[device] = []
            device_actions[device].append(action)
        
        # Verarbeite jedes Gerät einzeln
        for device, actions in device_actions.items():
            if len(actions) > 1:
                # Bei mehreren Aktionen: höchste Priority gewinnt
                priority_order = {'high': 1, 'medium': 2, 'low': 3}
                best_action = min(actions, key=lambda x: priority_order.get(x.get('priority', 'medium'), 2))
                actions = [best_action]
            
            for action in actions:
                deviceAction = action.get("action")
                requestedDevice = action.get("device").lower()
               
                if requestedDevice == "error":
                    _LOGGER.error(f"{self.deviceName}: Requested CONTROL ERROR {premActions} ")
                    return
                
                nightVPDHold = self.dataStore.getDeep("controlOptions.nightVPDHold")
                islightON = self.dataStore.getDeep("isPlantDay.islightON")
                
                if islightON == False and nightVPDHold == False:
                    _LOGGER.debug(f"{self.room}: VPD Night Hold Not Activ Ignoring VPD ") 
                    # Need tu adjust fallback vpd for night times
                    #await self.NightHoldFallBack(actionMap)
                    return None    

                # Aktionen basierend auf den Fähigkeiten
                if requestedDevice == "exhaust":
                    await self.eventManager.emit(f"{deviceAction} Exhaust", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Exhaust.")
                if requestedDevice == "intake":
                    await self.eventManager.emit(f"{deviceAction} Iintake", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Intake.")
                if requestedDevice == "ventilate":
                    await self.eventManager.emit(f"{deviceAction} Ventilation", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Ventilation.")
                if requestedDevice == "humidify":
                    await self.eventManager.emit(f"{deviceAction} Humidifier", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Humidifier.")
                if requestedDevice == "dehumidify":
                    await self.eventManager.emit(f"{deviceAction} Dehumidifier", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Dehumidifier.")
                if requestedDevice == "heat":
                    await self.eventManager.emit(f"{deviceAction} Heater", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Heater.")
                if requestedDevice == "cool":
                    await self.eventManager.emit(f"{deviceAction} Cooler", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Cooler.")
                if requestedDevice == "climate":
                    await self.eventManager.emit(f"{deviceAction} Climate", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} CO2.")
                if requestedDevice == "co2":
                    await self.eventManager.emit(f"{deviceAction} CO2", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} CO2.")
                if requestedDevice == "light":
                    await self.eventManager.emit(f"{deviceAction} Light", deviceAction)
                    _LOGGER.debug(f"{self.room}: {deviceAction} Light.")

    async def AIActions(self, premActions):
        actionData = premActions.get("actionData")
        await self.eventManager.emit("SaveState",True)
        

    # Action Handling
    async def checkLimitsAndPublicate(self, actionMap):
        _LOGGER.debug(f"{self.room}: Action Publication Limits-Validation von {actionMap}")    
        
        ownWeights = self.dataStore.getDeep("controlOptions.ownWeights")
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")
        nightVPDHold = self.dataStore.getDeep("controlOptions.nightVPDHold")
        islightON = self.dataStore.getDeep("isPlantDay.islightON")
        
        if islightON == False and nightVPDHold == False:
            _LOGGER.debug(f"{self.room}: VPD Night Hold Not Activ Ignoring VPD ") 
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
            
        # Humdiditysabweichung prüfen
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

        # Bestimme den VPD-Status für optimale Geräteauswahl
        vpdStatus = self._determineVPDStatus(tempDeviation, humDeviation, tentData)
        optimalDevices = self.getRoomCaps(vpdStatus)

        # Erweitere actionMap basierend auf Abweichungen - aber intelligent
        enhancedActionMap = self._enhanceActionMap(actionMap, tempDeviation, humDeviation, tentData, caps, vpdLightControl, islightON, optimalDevices)
        
        # Löse Konflikte auf - aber behalte mehrere Actions pro Capability bei
        finalActionMap = self._resolveActionConflicts(enhancedActionMap)
        
        await self.publicationActionHandler(finalActionMap)
        await self.eventManager.emit("LogForClient", finalActionMap, haEvent=True)

    async def checkLimitsAndPublicateWithDampening(self, actionMap):
        """Hauptfunktion mit integriertem Dampening-System"""
        _LOGGER.debug(f"{self.room}: Action Publication mit Dampening von {len(actionMap)} Aktionen")    
        
        ownWeights = self.dataStore.getDeep("controlOptions.ownWeights")
        vpdLightControl = self.dataStore.getDeep("controlOptions.vpdLightControl")
        nightVPDHold = self.dataStore.getDeep("controlOptions.nightVPDHold")
        islightON = self.dataStore.getDeep("isPlantDay.islightON")
        
        if islightON == False and nightVPDHold == False:
            _LOGGER.debug(f"{self.room}: VPD Night Hold Not Active Ignoring VPD ") 
            await self.NightHoldFallBack(actionMap)
            return None
        
        # ... (existing weight calculation code) ...
        # Gewichtungen ermitteln
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

        # Abweichungen berechnen
        tentData = self.dataStore.get("tentData")
        tempDeviation = 0
        humDeviation = 0
        weightMessage = ""
        
        if tentData["temperature"] > tentData["maxTemp"]:
            tempDeviation = round((tentData["temperature"] - tentData["maxTemp"]) * tempWeight, 2)
            weightMessage = f"Temp To High: Deviation {tempDeviation}"
        elif tentData["temperature"] < tentData["minTemp"]:
            tempDeviation = round((tentData["temperature"] - tentData["minTemp"]) * tempWeight, 2)
            weightMessage = f"Temp To Low: Deviation {tempDeviation}"
            
        if tentData["humidity"] > tentData["maxHumidity"]:
            humDeviation = round((tentData["humidity"] - tentData["maxHumidity"]) * humWeight, 2)
            weightMessage = f"Humidity To High: Deviation {humDeviation}"
        elif tentData["humidity"] < tentData["minHumidity"]:
            humDeviation = round((tentData["humidity"] - tentData["minHumidity"]) * humWeight, 2)
            weightMessage = f"Humidity To Low: Deviation {humDeviation}"

        WeightPublication = OGBWeightPublication(Name=self.room, message=weightMessage, tempDeviation=tempDeviation, humDeviation=humDeviation, tempWeight=tempWeight, humWeight=humWeight)
        await self.eventManager.emit("LogForClient", WeightPublication, haEvent=True)   
        
        # Notfall-Prüfung
        emergencyConditions = self._getEmergencyOverride(tentData)
        if emergencyConditions:
            self._clearCooldownForEmergency(emergencyConditions)
        
        # Capabilities abrufen
        caps = self.dataStore.get("capabilities")

        # VPD-Status bestimmen
        vpdStatus = self._determineVPDStatus(tempDeviation, humDeviation, tentData)
        optimalDevices = self.getRoomCaps(vpdStatus)

        # ActionMap erweitern
        enhancedActionMap = self._enhanceActionMap(actionMap, tempDeviation, humDeviation, tentData, caps, vpdLightControl, islightON, optimalDevices)
        
        # Dampening anwenden
        dampenedActionMap = self._filterActionsByDampening(enhancedActionMap, tempDeviation, humDeviation)
        
        # *** NEUE PRÜFUNG: Behandle leere Action-Liste ***
        if not dampenedActionMap:
            _LOGGER.warning(f"{self.room}: Alle {len(enhancedActionMap)} Aktionen durch Dampening blockiert!")
            
            # Option 1: Log und Exit früh
            await self.eventManager.emit("LogForClient", {
                "Name": self.room,
                "message": "All Actions Blocked to Device Dampening - Device CoolDowns",
                "blocked_actions": len(enhancedActionMap),
                "emergency_conditions": emergencyConditions
            }, haEvent=True)
            
            # Option 2: Bei Notfall trotzdem eine minimale Aktion durchlassen
            if emergencyConditions:
                _LOGGER.error(f"{self.room}: NOTFALL erkannt aber alle Aktionen blockiert! Erzwinge kritische Aktion.")
                # Wähle die wichtigste Aktion aus der ursprünglichen Liste
                criticalAction = self._selectCriticalEmergencyAction(enhancedActionMap, emergencyConditions)
                if criticalAction:
                    dampenedActionMap = [criticalAction]
                    # Registriere die Aktion trotz Dampening
                    self._registerAction(criticalAction.capability, criticalAction.action, max(abs(tempDeviation), abs(humDeviation)))
            
            # Wenn immer noch leer, beende früh
            if not dampenedActionMap:
                return
        
        # Konflikte lösen
        finalActionMap = self._resolveActionConflicts(dampenedActionMap)
        
        _LOGGER.info(f"{self.room}: Von {len(enhancedActionMap)} Aktionen werden {len(finalActionMap)} ausgeführt")
        
        # Nur ausführen wenn Aktionen vorhanden sind
        if finalActionMap:
            await self.publicationActionHandler(finalActionMap)
            await self.eventManager.emit("LogForClient", finalActionMap, haEvent=True)
        else:
            _LOGGER.debug(f"{self.room}: Keine Aktionen nach Konfliktlösung übrig")

    def _selectCriticalEmergencyAction(self, actionMap, emergencyConditions):
        """Wählt die kritischste Aktion bei Notfällen aus"""
        
        if not actionMap or not emergencyConditions:
            return None
        
        # Priorisierung basierend auf Notfall-Typ
        emergencyPriority = {
            "critical_overheat": ["canCool", "canExhaust", "canVentilate"],
            "critical_cold": ["canHeat"],
            "immediate_condensation_risk": ["canDehumidify", "canExhaust", "canVentilate"],
            "critical_humidity": ["canDehumidify", "canExhaust"]
        }
        
        # Suche nach höchster Priorität
        for condition in emergencyConditions:
            priorityCaps = emergencyPriority.get(condition, [])
            for cap in priorityCaps:
                for action in actionMap:
                    if action.capability == cap and action.action in ["Increase", "Reduce"]:
                        _LOGGER.critical(f"{self.room}: Notfall-Override für {cap} - {action.action}")
                        return action
        
        # Fallback: Erste verfügbare Aktion
        return actionMap[0] if actionMap else None

    # Water Actions
    async def PumpAction(self, pumpAction: OGBHydroAction):
        if isinstance(pumpAction, dict):
            dev = pumpAction.get("Device") or pumpAction.get("id") or "<unknown>"
            action = pumpAction.get("Action") or pumpAction.get("action")
            cycle = pumpAction.get("Cycle") or pumpAction.get("cycle")
        else:
            # your dataclass
            dev = pumpAction.Device
            action = pumpAction.Action
            cycle = pumpAction.Cycle
            
        if action == "on":
            message = "Start Pump"
            waterAction = OGBWaterAction(Name=self.room,Device=dev,Cycle=cycle,Action=action,Message=message)
            await self.eventManager.emit(
                "LogForClient",
                waterAction,
                haEvent=True
            )
            await self.eventManager.emit("Increase Pump", pumpAction)

        elif action == "off":
            message = "Stop Pump"
            waterAction = OGBWaterAction(Name=self.room,Device=dev,Cycle=cycle,Action=action,Message=message)
            await self.eventManager.emit(
                "LogForClient",
                waterAction,
                haEvent=True
            )
            await self.eventManager.emit("Reduce Pump", pumpAction)

        else:
            # unknown action
            return None
        
    async def RetrieveAction(self, pumpAction: OGBRetrieveAction):
        if isinstance(pumpAction, dict):
            dev = pumpAction.get("Device") or pumpAction.get("id") or "<unknown>"
            action = pumpAction.get("Action") or pumpAction.get("action")
            cycle = pumpAction.get("Cycle") or pumpAction.get("cycle")
        else:
            # your dataclass
            dev = pumpAction.Device
            action = pumpAction.Action
            cycle = pumpAction.Cycle
            
        if action == "on":
            message = "Start Pump"
            waterAction = OGBWaterAction(Name=self.room,Device=dev,Cycle=cycle,Action=action,Message=message)
            await self.eventManager.emit(
                "LogForClient",
                waterAction,
                haEvent=True
            )
            await self.eventManager.emit("Increase Pump", pumpAction)

        elif action == "off":
            message = "Stop Pump"
            waterAction = OGBWaterAction(Name=self.room,Device=dev,Cycle=cycle,Action=action,Message=message)
            await self.eventManager.emit(
                "LogForClient",
                waterAction,
                haEvent=True
            )
            await self.eventManager.emit("Reduce Pump", pumpAction)

        else:
            # unknown action
            return None

    # Action Helpers
    async def publicationActionHandler(self, actionMap):
        """
        Handhabt die Steuerungsaktionen basierend auf dem actionMap und den Fähigkeiten.
        """
        _LOGGER.debug(f"{self.room}: Validated-Actions-By-Limits: - {actionMap}")

        for action in actionMap:
            actionCap = action.capability
            actionType = action.action
            actionMesage = action.message
            _LOGGER.debug(f"{self.room}: {actionCap} - {actionType} - - {action} -- {actionMesage}")
     
            # Aktionen basierend auf den Fähigkeiten
            if actionCap == "canExhaust":
                await self.eventManager.emit(f"{actionType} Exhaust", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Exhaust ausgeführt.")
            if actionCap == "canIntake":
                await self.eventManager.emit(f"{actionType} Intake", actionType)
                _LOGGER.debug(f"{self.room}: {actionType} Intake ausgeführt.")
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

        await self.eventManager.emit("SaveState",True)   

    # Action Utils
    async def NightHoldFallBack(self, actionMap):
        _LOGGER.debug(f"{self.room}: VPD Night Hold NOT ACTIVE IGNORING ACTIONS ")
        await self.eventManager.emit("LogForClient",{"Name":self.room,"NightVPDHold":"NotActive Ignoring-VPD"},haEvent=True)     
        
        # Capabilities abrufen
        excludeCaps = {"canHeat", "canCool", "canHumidify", "canClimate", "canDehumidify", "canLight", "canCO2"}
        modCaps = {"canHeat", "canCool", "canHumidify", "canClimate", "canDehumidify", "canCO2"}
        fallBackAction = "Reduce"
        
        # Gefilterte ActionMap erstellen (nur erlaubte Actions)
        filteredActions = [action for action in actionMap if action.capability not in excludeCaps]

        # Neue Action-Liste mit Reduced-Actions für alle anderen Geräte erstellen
        reducedActions = [
            OGBActionPublication(capability=action.capability, action=fallBackAction,Name=self.room,message="VPD-NightHold Device Shutdown",priority="low")
            for action in actionMap if action.capability in modCaps
        ]

        # Wenn es gefilterte oder reduzierte Aktionen gibt, verarbeiten
        if filteredActions or reducedActions:
            await self.publicationActionHandler(filteredActions + reducedActions)

    def _determineVPDStatus(self, tempDeviation, humDeviation, tentData):
        """Bestimmt den primären VPD-Status basierend auf Abweichungen und kritischen Werten"""
        
        # Notfälle haben Priorität
        if tentData["temperature"] > tentData["maxTemp"]:
            return "critical_hot"
        elif tentData["temperature"] < tentData["minTemp"]:
            return "critical_cold"
        elif tentData["dewpoint"] >= tentData["temperature"]:
            return "dewpoint_risk"
        elif tentData["humidity"] > tentData["maxHumidity"]:
            return "humidity_risk"
        
        # Kombinierte Bewertung
        if tempDeviation > 0 and humDeviation > 0:
            return "hot_humid"
        elif tempDeviation > 0 and humDeviation < 0:
            return "hot_dry"
        elif tempDeviation < 0 and humDeviation > 0:
            return "cold_humid"
        elif tempDeviation < 0 and humDeviation < 0:
            return "cold_dry"
        elif abs(tempDeviation) > abs(humDeviation):
            return "too_hot" if tempDeviation > 0 else "too_cold"
        elif abs(humDeviation) > 0:
            return "too_humid" if humDeviation > 0 else "too_dry"
        else:
            # VPD-Fallback
            currentVPD = self.dataStore.getDeep("vpd.current")
            perfectionVPD = self.dataStore.getDeep("vpd.perfection")
            return "vpd_low" if currentVPD < perfectionVPD else "vpd_high"

    def _enhanceActionMap(self, baseActionMap, tempDeviation, humDeviation, tentData, caps, vpdLightControl, islightON, optimalDevices):
        """Erweitert die ActionMap intelligent basierend auf Bedingungen"""
        
        enhancedMap = list(baseActionMap)  # Kopiere ursprüngliche Actions
        
        # Aktionen basierend auf Abweichungen hinzufügen
        if tempDeviation > 0 or humDeviation > 0:
            enhancedMap.extend(self._getDeviationActions(tempDeviation, humDeviation, caps, vpdLightControl))
        
        # Notfallmaßnahmen hinzufügen
        enhancedMap.extend(self._getEmergencyActions(tentData, caps, vpdLightControl))
        
        # CO2-Management hinzufügen
        enhancedMap.extend(self._getCO2Actions(caps, islightON))
        
        # Priorisiere Actions für optimale Geräte
        return self._prioritizeOptimalDevices(enhancedMap, optimalDevices, caps)

    def _getDeviationActions(self, tempDeviation, humDeviation, caps, vpdLightControl):
        """Erstellt Actions basierend auf Temperatur- und Humdiditysabweichungen"""
        
        actions = []
        
        if tempDeviation > 0 and humDeviation > 0:
            # High Temperatur + High Humdidity
            actionMessage = f"High Temperatur + High Humdidity in {self.room}"
            if caps.get("canDehumidify", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canDehumidify", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if caps.get("canExhaust", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canExhaust", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if caps.get("canVentilate", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canVentilate", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if caps.get("canCool", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canCool", action="Increase", Name=self.room, message=actionMessage, priority=""))
                
        elif tempDeviation > 0 and humDeviation < 0:
            # High Temperatur + Low Humdidity
            actionMessage = f"High Temperatur + Low Humdidity in {self.room}"
            if caps.get("canHumidify", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canHumidify", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if caps.get("canCool", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canCool", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if vpdLightControl and caps.get("canLight", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canLight", action="Reduce", Name=self.room, message=actionMessage, priority=""))
                
        elif tempDeviation < 0 and humDeviation > 0:
            # Low Temperatur + High Humdidity
            actionMessage = f"Low Temperatur + High Humdidity in {self.room}"
            if caps.get("canDehumidify", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canDehumidify", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if caps.get("canHeat", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canHeat", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if caps.get("canExhaust", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canExhaust", action="Increase", Name=self.room, message=actionMessage, priority=""))
                
        elif tempDeviation < 0 and humDeviation < 0:
            # Low Temperatur + Low Humdidity
            actionMessage = f"Low Temperature + Low Humdidity in {self.room}"
            if caps.get("canHumidify", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canHumidify", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if caps.get("canHeat", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canHeat", action="Increase", Name=self.room, message=actionMessage, priority=""))
            if vpdLightControl and caps.get("canLight", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canLight", action="Increase", Name=self.room, message=actionMessage, priority=""))
        
        return actions

    def _getEmergencyActions(self, tentData, caps, vpdLightControl):
        """Erstellt Notfall-Actions für kritische Situationen"""
        
        actions = []
        
        # FIXED: Same threshold as emergency override
        if tentData["temperature"] > tentData["maxTemp"]:  # Changed from > to >=
            actionMessage = f"Critical Over-Temp in {self.room}! Emergency Action activated."
            _LOGGER.warning(actionMessage)  # Add warning log
            
            # Prioritize cooling actions
            if caps.get("canCool", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canCool", action="Increase", 
                                                Name=self.room, message=actionMessage, priority="emergency"))
            if caps.get("canExhaust", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canExhaust", action="Increase", 
                                                Name=self.room, message=actionMessage, priority="emergency"))
            if caps.get("canVentilate", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canVentilate", action="Increase", 
                                                Name=self.room, message=actionMessage, priority="emergency"))
            
            # Reduce heat sources
            if caps.get("canHeat", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canHeat", action="Reduce", 
                                                Name=self.room, message=actionMessage, priority="emergency"))
            if vpdLightControl and caps.get("canLight", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canLight", action="Reduce", 
                                                Name=self.room, message=actionMessage, priority="emergency"))

        # Similar fixes for cold temperatures
        elif tentData["temperature"] < tentData["minTemp"]:  # Changed from < to <=
            actionMessage = f"Critical Under-Temp in {self.room}! Emergency Action activated."
            _LOGGER.warning(actionMessage)
            
            if caps.get("canHeat", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canHeat", action="Increase", 
                                                Name=self.room, message=actionMessage, priority="emergency"))
            if caps.get("canExhaust", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canExhaust", action="Reduce", 
                                                Name=self.room, message=actionMessage, priority="emergency"))
            if vpdLightControl and caps.get("canLight", {}).get("state", False):
                actions.append(OGBActionPublication(capability="canLight", action="Increase", 
                                                Name=self.room, message=actionMessage, priority="emergency"))

        return actions
    
    def _getCO2Actions(self, caps, islightON):
        """Erstellt CO2-Management Actions"""
        
        actions = []
        co2Control = self.dataStore.getDeep("controlOptions.co2Control")
        
        if co2Control and islightON:
            co2Level = int(self.dataStore.getDeep("controlOptionData.co2ppm.current"))
            try:
                co2LevelMin = int(float(self.dataStore.getDeep("controlOptionData.co2ppm.minPPM")))        
                co2LevelMax = int(float(self.dataStore.getDeep("controlOptionData.co2ppm.maxPPM")))
            except (ValueError, TypeError):
                co2LevelMin = 400
                co2LevelMax = 1500
                
            if co2Level < co2LevelMin:
                actionMessage = f"CO₂-Level zu niedrig in {self.room}, CO₂-Zufuhr erhöht."
                if caps.get("canCO2", {}).get("state", False):
                    actions.append(OGBActionPublication(capability="canCO2", action="Increase", Name=self.room, message=actionMessage, priority=""))
                if caps.get("canExhaust", {}).get("state", False):
                    actions.append(OGBActionPublication(capability="canExhaust", action="Reduce", Name=self.room, message=actionMessage, priority=""))
                    
            elif co2Level > co2LevelMax:
                actionMessage = f"CO₂-Level zu hoch in {self.room}, Abluft erhöht."
                if caps.get("canCO2", {}).get("state", False):
                    actions.append(OGBActionPublication(capability="canCO2", action="Reduce", Name=self.room, message=actionMessage, priority=""))
                if caps.get("canExhaust", {}).get("state", False):
                    actions.append(OGBActionPublication(capability="canExhaust", action="Increase", Name=self.room, message=actionMessage, priority=""))
        
        return actions

    def _prioritizeOptimalDevices(self, actionMap, optimalDevices, caps):
        """Priorisiert Actions für optimale Geräte, behält aber alle bei"""
        
        if not optimalDevices:
            return actionMap  # Keine Optimierung möglich
        
        # Create new action list with updated priorities
        prioritizedActions = []
        
        for action in actionMap:
            capability = action.capability
            capDevices = caps.get(capability, {}).get("devEntities", [])
            
            # Prüfe ob diese capability optimale Geräte hat
            hasOptimalDevice = any(device in optimalDevices for device in capDevices)
            
            # Use dataclasses.replace to create a new instance with updated priority
            prioritizedAction = dataclasses.replace(
                action,
                priority="high" if hasOptimalDevice else "normal"
            )
            prioritizedActions.append(prioritizedAction)
        
        return prioritizedActions

    def _resolveActionConflicts(self, actionMap):
        """Löst nur direkte Konflikte auf, behält aber multiple Actions pro Capability"""
        
        # Gruppiere nach capability UND action
        actionGroups = {}
        for action in actionMap:
            key = f"{action.capability}_{action.action}"
            if key not in actionGroups:
                actionGroups[key] = []
            actionGroups[key].append(action)
        
        finalActions = []
        
        # Für jede Gruppe, wähle die beste Action (falls mehrere identische)
        for key, actions in actionGroups.items():
            if len(actions) == 1:
                finalActions.append(actions[0])
            else:
                # Bei identischen Actions, wähle die mit der wichtigsten Message
                priorityKeywords = ["Critical", "Notfall", "Dewpoint", "CO₂"]
                
                bestAction = actions[0]
                for action in actions:
                    if any(keyword in action.message for keyword in priorityKeywords):
                        bestAction = action
                        break
                    elif hasattr(action, 'priority') and action.priority == "high":
                        bestAction = action
                
                finalActions.append(bestAction)
        
        # Prüfe auf direkte Konflikte (Increase vs Reduce für gleiche Capability)
        return self._resolveIncreaseReduceConflicts(finalActions)

    # Enhanced conflict resolution with emergency priority
    def _resolveIncreaseReduceConflicts(self, actions):
        """Löst Increase/Reduce Konflikte für gleiche Capability auf"""
        
        capabilityActions = {}
        for action in actions:
            cap = action.capability
            if cap not in capabilityActions:
                capabilityActions[cap] = []
            capabilityActions[cap].append(action)
        
        resolvedActions = []
        
        for capability, capActions in capabilityActions.items():
            if len(capActions) <= 1:
                resolvedActions.extend(capActions)
                continue
                
            # Check for emergency priority first
            emergencyActions = [a for a in capActions if getattr(a, 'priority', '') == 'emergency']
            if emergencyActions:
                resolvedActions.append(emergencyActions[0])
                continue
                
            # Existing conflict resolution logic...
            increases = [a for a in capActions if a.action == "Increase"]
            reduces = [a for a in capActions if a.action == "Reduce"]
            
            if increases and reduces:
                # Existing priority logic with emergency keywords
                emergencyKeywordActions = [a for a in capActions if any(kw in a.message for kw in ["Critical", "Notfall", "Dewpoint"])]
                
                if emergencyKeywordActions:
                    resolvedActions.append(emergencyKeywordActions[0])
                else:
                    # Original logic continues...
                    highPriorityActions = [a for a in capActions if hasattr(a, 'priority') and a.priority == "high"]
                    if highPriorityActions:
                        resolvedActions.append(highPriorityActions[0])
                    else:
                        if increases:
                            resolvedActions.append(increases[0])
                        else:
                            resolvedActions.append(reduces[0])
            else:
                resolvedActions.extend(capActions)
        
        return resolvedActions
    
    def getRoomCaps(self, vpdStatus: str):
        """Erweiterte Version die auch spezielle VPD-Stati behandelt"""
        
        available_capabilities = self.dataStore.get("capabilities")
        device_profiles = self.dataStore.get("DeviceProfiles")
        result = []

        # Mapping für erweiterte VPD-Stati
        statusMapping = {
            "too_hot": "too_high",
            "too_cold": "too_low", 
            "too_humid": "too_high",
            "too_dry": "too_low",
            "critical_hot": "too_high",
            "critical_cold": "too_low",
            "dewpoint_risk": "too_high",
            "humidity_risk": "too_high",
            "hot_humid": "too_high",
            "hot_dry": "too_high",
            "cold_humid": "too_low",
            "cold_dry": "too_low",
            "vpd_high": "too_high",
            "vpd_low": "too_low"
        }
        
        mappedStatus = statusMapping.get(vpdStatus, "too_high")

        for dev_name, profile in device_profiles.items():
            cap_key = profile.get("cap")
            if not cap_key:
                continue

            cap_info = available_capabilities.get(cap_key)
            if not cap_info or cap_info["count"] == 0:
                continue

            if mappedStatus == "too_high":
                if (
                    (profile["type"] == "humidity" and profile["direction"] == "reduce") or
                    (profile["type"] == "temperature" and profile["direction"] == "reduce") or
                    (profile["type"] == "both" and profile["direction"] == "reduce")
                ):
                    result.extend(cap_info["devEntities"])

            elif mappedStatus == "too_low":
                if (
                    (profile["type"] == "humidity" and profile["direction"] == "increase") or
                    (profile["type"] == "temperature" and profile["direction"] == "increase") or
                    (profile["type"] == "both" and profile["direction"] == "increase")
                ):
                    result.extend(cap_info["devEntities"])

        return list(set(result))
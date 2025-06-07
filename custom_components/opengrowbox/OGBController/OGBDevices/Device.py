import logging
import asyncio

_LOGGER = logging.getLogger(__name__)

class Device:
    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None):
        self.hass = hass
        self.eventManager = eventManager
        self.dataStore = dataStore
        self.deviceName = deviceName
        self.deviceType = deviceType
        self.isTasmota = False
        self.isRunning = False
        self.isDimmable = False
        self.inRoom = inRoom
        self.switches = []
        self.options = []
        self.sensors = []
        self.ogbsettings = []
        self.deviceInit(deviceData)
        self.initialization = False
        self.inWorkMode = False
        
        # EVENTS
        self.eventManager.on("DeviceStateUpdate", self.deviceUpdate)        
        self.eventManager.on("WorkModeChange", self.WorkMode)
        self.eventManager.on("SetMinMax", self.setMinMax)
   
    def __iter__(self):
        return iter(self.__dict__.items())

    def __repr__(self):
        return (f"DeviceName:'{self.deviceName}' Typ:'{self.deviceType}'RunningState:'{self.isRunning}'"
                f"Dimmable:'{self.isDimmable}' Switches:'{self.switches}' Sensors:'{self.sensors}'"
                f"Options:'{self.options}' OGBS:'{self.ogbsettings}'")

    def getEntitys(self):
        """
        Liefert eine Liste aller Entitäten der Sensoren, Optionen, Schalter und OGB-Einstellungen.
        Erwartet, dass die Objekte Dictionaries mit dem Schlüssel 'entity_id' sind.
        """
        entityList = []
        # Iteriere durch die Entitäten in allen Kategorien
        for group in [self.sensors, self.options, self.switches, self.ogbsettings]:
            if group:  # Überprüfen, ob die Gruppe nicht None ist
                for entity in group:   
                    # Überprüfe, ob 'entity_id' im Dictionary vorhanden ist
                    if isinstance(entity, dict) and "entity_id" in entity:
                        entityList.append(entity["entity_id"])
                    else:
                        _LOGGER.error(f"Ungültiges Objekt in {group}: {entity}")
        return entityList
        
    # Initialisiere das Gerät und identifiziere Eigenschaften
    def deviceInit(self, entitys):
        
        self.checkMinMaxSet(False)
                
        self.identifySwitchesAndSensors(entitys)
        self.identifyIfRunningState()
        self.identifDimmable()
        self.identifyCapabilities()
        if(self.initialization == True):
            self.registerListener()
            _LOGGER.warning(f"Device {self.deviceName} Initialization Completed")
            self.initialization = False
        else:
            raise Exception(f"Device could not be Initialized {self.deviceName}")

    async def deviceUpdate(self, updateData):
        """
        Verarbeitet Updates basierend to der `entity_id` und aktualisiert die entsprechenden Werte.
        """

        parts = updateData["entity_id"].split(".")
        device_name = parts[1].split("_")[0] if len(parts) > 1 else "Unknown"

        if self.deviceName != device_name: return

        entity_id = updateData["entity_id"]
        new_value = updateData["newValue"]

        # Asynchrone Helper-Funktion, um die Entität in einer Liste zu finden und den Wert zu aktualisieren
        async def update_entity_value(entity_list, entity_id, new_value):
            for entity in entity_list:
                if entity.get("entity_id") == entity_id:
                    old_value = entity.get("value")
                    entity["value"] = new_value
                    _LOGGER.warning(
                        f"{self.deviceName} Updated {entity_id}: Old Value: {old_value}, New Value: {new_value}."
                    )
                    return True
            return False

        # Aktualisiere Sensor-Werte
        if "sensor." in entity_id:
            _LOGGER.warning(f"{self.deviceName} Start Update Sensor for {updateData}.")
            await update_entity_value(self.sensors, entity_id, new_value)
            _LOGGER.warning(f"{self.deviceName} warning {self.__repr__()}.")

        # Aktualisiere Switch-Werte
        if any(prefix in entity_id for prefix in ["fan.", "light.", "switch.", "humidifier."]):
            _LOGGER.warning(f"{self.deviceName} Start Update Switch for {updateData}.")
            await update_entity_value(self.switches, entity_id, new_value)
            self.identifyIfRunningState()
            
        # Aktualisiere weitere spezifische Werte
        if any(prefix in entity_id for prefix in ["number.", "text.", "time.", "select.", "date."]):
            _LOGGER.warning(f"{self.deviceName} Start Update Switches for {updateData}.")
            await update_entity_value(self.options, entity_id, new_value)
            _LOGGER.warning(f"{self.deviceName} warning {self.__repr__()}.")

        # Aktualisiere spezifische OGB-Entitäten
        if any(prefix in entity_id for prefix in ["ogb_"]):
            _LOGGER.warning(f"{self.deviceName} Start Update OGBS for {updateData}.")
            await update_entity_value(self.sensors, entity_id, new_value)
    
    async def checkMinMaxSet(self,data):
        minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")

        if not minMaxSets or not minMaxSets.get("active", False):
            return  # Nichts aktiv → nichts tun

        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
            self.minVoltage = minMaxSets.get("minVoltage")
            self.maxVoltage = minMaxSets.get("maxVoltage")

        elif "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            self.minDuty = minMaxSets.get("minDuty")
            self.maxDuty = minMaxSets.get("maxDuty")
          
    # Eval sensor if Intressted in 
    def evalSensors(self, sensor_id: str) -> bool:
        """Prüft, ob ein Sensor interessant ist (z. B. temperature, humidity, dewpoint, co2)."""
        interested_mapping = ("temperature", "humidity", "dewpoint", "co2","duty","voltage","moisture")
        return any(keyword in sensor_id for keyword in interested_mapping)

    # Mapp Entity Types to Class vars
    def identifySwitchesAndSensors(self, entitys):
        """Identifiziere Switches und Sensoren aus der Liste der Entitäten und prüfe ungültige Werte."""
        _LOGGER.info(f"Identify all given {entitys}")

        try:
            for entity in entitys:
                entityID = entity.get("entity_id")
                entityValue = entity.get("value")

                # Prüfe, ob die Entität "ogb_" im Namen hat
                if "ogb_" in entityID:
                    _LOGGER.warning(f"Entity {entityID} contains 'ogb_'. Adding to switches.")
                    self.ogbsettings.append(entity)
                    continue  # Überspringe die weitere Verarbeitung für diese Entität

                # Prüfe, ob der Wert ungültig ist
                if entityValue in ("None", "unknown", "Unbekannt", "unavailable"):
                    _LOGGER.warning(f"DEVICE {self.deviceName} Initial invalid value detected for {entityID}. Fetching current state...")
                    continue
                        
                # Sortiere die Entität in die richtige Liste
                if entityID.startswith(("switch.", "light.", "fan.", "climate.", "humidifier.")):
                    self.switches.append(entity)
                elif entityID.startswith(("select.", "number.","date.", "text.", "time.")):
                    self.options.append(entity)
                elif entityID.startswith("sensor."):
                    # Prüfe, ob der Sensor interessant ist
                    if self.evalSensors(entityID):
                        self.sensors.append(entity)
            self.initialization = True
        except:
            _LOGGER.error(f"Device:{self.deviceName} INIT ERROR {self.deviceName}. Fetching current state...")
            self.initialization = False

    # Identify Action Caps 
    def identifyCapabilities(self):
        capMapping = {
            "canHeat": ["heater"],
            "canCool": ["cooler"],
            "canClimate":["cliamte"],
            "canHumidify": ["humidifier"],
            "canDehumidify": ["dehumidifier"],
            "canVentilate": ["ventilation"],
            "canExhaust": ["exhaust"],
            "canInhaust": ["inhaust"],
            "canLight": ["light"],
            "canCO2": ["co2"],
            "canPump":["pump"],
        }

        # Initialisiere capabilities im dataStore, falls nicht vorhanden
        if not self.dataStore.get("capabilities"):
            self.dataStore.setDeep("capabilities", {
                cap: {"state": False, "count": 0, "devEntities": []} for cap in capMapping
            })

        # Durchltoe alle möglichen Capabilities und überprüfe den Gerätetyp
        for cap, deviceTypes in capMapping.items():
            if self.deviceName == "ogb": return
            if self.deviceType.lower() in (dt.lower() for dt in deviceTypes):
                capPath = f"capabilities.{cap}"
                currentCap = self.dataStore.getDeep(capPath)

                # Aktualisiere die Capability-Daten
                if not currentCap["state"]:
                    currentCap["state"] = True
                currentCap["count"] += 1
                if self.deviceType == "Light":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Exhaust":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Inhaust":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Ventilation":
                    currentCap["devEntities"].append(self.deviceName)                    
                if self.deviceType == "Dehumidifier":
                    currentCap["devEntities"].append(self.deviceName)                   
                if self.deviceType == "Humidifier":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Heater":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Cooler":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Humidifier":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Climate":
                    currentCap["devEntities"].append(self.deviceName)
                if self.deviceType == "Pump":
                    currentCap["devEntities"].append(self.deviceName)
                # Schreibe die aktualisierten Daten zurück
                self.dataStore.setDeep(capPath, currentCap)

        # Log die finalen Capabilities
        _LOGGER.warning(f"{self.deviceName}: Capabilities identified: {self.dataStore.get('capabilities')}")

    # Bestimme, ob das Gerät gerade läuft
    def identifyIfRunningState(self):
        for switch in self.switches:
            switch_value = switch.get("value")
            if switch_value == "on":
                self.isRunning = True
            elif switch_value  == "off":
                self.isRunning = False
            elif switch_value in ( None, "unknown", "Unbekannt", "unavailable"):
                raise ValueError(f"Invalid Entity state '{switch_value}' for {self.deviceName}")
            else:
                raise ValueError(f"Invalid  Entity state '{switch_value}' for {self.deviceName}")

    # Überprüfe, ob das Gerät dimmbar ist
    def identifDimmable(self):
        allowedDeviceTypes = ["ventilation", "exhaust","inhaust","light"]

        # Gerät muss in der Liste der erlaubten Typen sein
        if self.deviceType.lower() not in allowedDeviceTypes:
            _LOGGER.warning(f"{self.deviceName}: {self.deviceType} Is not in a list for Dimmable Devices.")
            return

        dimmableKeys = ["fan.", "light.","number.","duty","intensity"]

        # Prüfen, ob ein Schlüssel in switches, options oder sensors vorhanden ist
        for source in (self.switches, self.options, self.sensors):
            for entity in source:
                entity_id = entity.get("entity_id", "").lower()
                if any(key in entity_id for key in dimmableKeys):
                    self.isDimmable = True
                    _LOGGER.warning(f"{self.deviceName}: Device Recognized as Dimmable -  entity_id: {entity_id}")
                    return

        _LOGGER.warning(f"{self.deviceName}: No Dimmable Options Found")
    
    def checkForControlValue(self):
        """Findet und aktualisiert den Duty Cycle oder den Voltage-Wert basierend to Gerätetyp und Daten."""
        if not self.isDimmable:
            _LOGGER.warning(f"{self.deviceName}: Device is not Dimmable")
            return

        if not self.sensors and not self.options:
            _LOGGER.warning(f"{self.deviceName}: NO Sensor data or Options found ")
            return

        relevant_keys = ["duty","intensity"]

        for sensor in self.sensors:
            _LOGGER.warning(f"Prüfe Sensor: {sensor}")

            if any(key in sensor["entity_id"].lower() for key in relevant_keys):
                _LOGGER.warning(f"{self.deviceName}: Relevant Sensor Found: {sensor['entity_id']}")
                try:
                    value = sensor.get("value", None)
                    if value is None:
                        _LOGGER.warning(f"{self.deviceName}: No Value in Sensor: {sensor}")
                        continue
                    if self.deviceType == "Light":
                        self.voltage = value
                        _LOGGER.warning(f"{self.deviceName}: Voltage from Sensor updated to  {self.voltage}%.")
                    elif self.deviceType == "Exhaust" or self.deviceType == "Inhaust" or self.deviceType == "Ventilation":
                        self.dutyCycle = int(value)
                        _LOGGER.warning(f"{self.deviceName}: Duty Cycle from Sensor updated to {self.dutyCycle}%.")
                    else:
                        return
                except ValueError as e:
                    _LOGGER.error(f"{self.deviceName}: Error by Parsing Values from {sensor}: {e}")
                    continue

        for option in self.options:
            _LOGGER.warning(f"Prüfe Option: {option}")
            if any(key in option["entity_id"] for key in relevant_keys):
                raw_value = option.get("value", 0)
                try:
                    if isinstance(raw_value, str):
                        raw_value = float(raw_value)
                        
                    if isinstance(raw_value, float):
                        value = int(raw_value * 10)
                    else:
                        value = int(raw_value)
    
                    if self.deviceType == "Light":
                        self.voltageFromNumber = True # Identifier for number control on as voltage Value
                        self.voltage = value
                        _LOGGER.warning(f"{self.deviceName}: Voltage set from  Options to {self.voltage}%.")
                    else:
                        self.dutyCycle = value
                        _LOGGER.warning(f"{self.deviceName}: Duty Cycle set from Options to {self.dutyCycle}%.")
                    return 

                except (ValueError, TypeError) as e:
                    _LOGGER.error(f"{self.deviceName}: Error Parsing Values from {option}: {e}")
                    continue

    async def turn_on(self, **kwargs):
        """Schaltet das Gerät ein."""
        if not self.switches:
            _LOGGER.error(f"{self.deviceName} has not Switch to Activate or Turn Off")
            return
        try:
            # Prüfen, ob mehrere Entitäten in `self.switches` vorhanden sind
            entity_ids = [switch["entity_id"] for switch in self.switches]

            for entity_id in entity_ids:
                # Prüfen, ob mehrere Entitäten in `self.switches` vorhanden sind
                entity_ids = [switch["entity_id"] for switch in self.switches]
                
                brightness_pct = kwargs.get("brightness_pct")
                percentage = kwargs.get("percentage")

                # Climate-Mode setzen
                if self.deviceType == "Climate":
                    hvac_mode = kwargs.get("hvac_mode", "heat")
                    await self.hass.services.async_call(
                        domain="climate",
                        service="set_hvac_mode",
                        service_data={
                            "entity_id": entity_id,
                            "hvac_mode": hvac_mode,
                        },
                    )
                    self.isRunning = True
                    _LOGGER.warning(f"{self.deviceName}: HVAC-Mode: {hvac_mode} set.")

                # Humidifier einschalten
                elif self.deviceType == "Humidifier":
                    if self.realHumidifier == True:
                        await self.hass.services.async_call(
                            domain="humidifier",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Humdidifier TrunON.")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Default-Swtich TrunON.")
                        return

                # Licht mit Helligkeit einschalten
                elif self.deviceType == "Light":                  
                    if self.isDimmable:
                        if self.voltageFromNumber:
                            if self.islightON :
                                await self.hass.services.async_call(
                                    domain="switch",
                                    service="turn_on",
                                    service_data={"entity_id": entity_id},
                                )
                                self.isRunning = True
                                _LOGGER.warning(f"{self.deviceName}: Licht umgestellt to {float(brightness_pct/10)}.")
                                await self.set_value(float(brightness_pct/10)) # Send in Percent % 
                                return             
                        else:
                            await self.hass.services.async_call(
                                domain="light",
                                service="turn_on",
                                service_data={
                                    "entity_id": entity_id,
                                    "brightness_pct": brightness_pct,
                                },
                            )
                            self.isRunning = True
                            _LOGGER.warning(f"{self.deviceName}: Licht mit {brightness_pct}% Helligkeit TrunON.")        
                            return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Default-Swtich TrunON.")      
                        return
                    
                # Abluft einschalten
                elif self.deviceType == "Exhaust":
                    if self.isTasmota == True:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": brightness_pct,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Abluft mit {brightness_pct}% Geschwindigkeit TrunON.")
                        return
                    
                    if self.isDimmable == True:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="set_percentage",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": percentage,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Abluft mit {percentage}% Geschwindigkeit TrunON.")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Default-Swtich TrunON.")                 
                        return
                    
                # Zuluft einschalten
                elif self.deviceType == "Inhaust":
                    if self.isTasmota == True:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": brightness_pct,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Abluft mit {brightness_pct}% Geschwindigkeit TrunON.")
                        return
                    
                    if self.isDimmable == True:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="set_percentage",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": percentage,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Abluft mit {percentage}% Geschwindigkeit TrunON.")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Default-Swtich TrunON.")
                        return            

                # Ventilator einschalten
                elif self.deviceType == "Ventilation":
                    if self.isTasmota == True:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id,
                                "brightness_pct": brightness_pct,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Tasmota-Ventilator mit {brightness_pct}% Geschwindigkeit TrunON.")
                        return
                    
                    if self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="set_percentage",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": percentage,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Ventilator mit {percentage}% Geschwindigkeit TrunON.")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: Ventilation TrunON.")
                        return
                                          
                # Standard-Switch einschalten
                else:
                    await self.hass.services.async_call(
                        domain="switch",
                        service="turn_on",
                        service_data={"entity_id": entity_id},
                    )
                    self.isRunning = True
                    _LOGGER.warning(f"{self.deviceName}: Default-Swtich TrunON.")
        except Exception as e:
            _LOGGER.error(f"Error TurnON ->  {self.deviceName}: {e}")

    async def turn_off(self, **kwargs):
        """Schaltet das Gerät aus."""
        if not self.switches:
            _LOGGER.error(f"{self.deviceName} has NO Switches to Turn OFF")
            return
        try:
            # Prüfen, ob mehrere Entitäten in `self.switches` vorhanden sind
            entity_ids = [switch["entity_id"] for switch in self.switches]

            for entity_id in entity_ids:
                _LOGGER.warning(f"{self.deviceName}: Service-Call for Entity: {entity_id}")
                
                # Climate ausschalten
                if self.deviceType == "Climate":
                    await self.hass.services.async_call(
                        domain="climate",
                        service="set_hvac_mode",
                        service_data={
                            "entity_id": entity_id,
                            "hvac_mode": "off",
                        },
                    )
                    self.isRunning = False
                    _LOGGER.warning(f"{self.deviceName}: HVAC-Mode to 'off' set.")
                    return
                
                # Humidifier ausschalten
                elif self.deviceType == "Humidifier":
                    #if self.realHumidifier:
                    #    await self.hass.services.async_call(
                    #        domain="humidifier",
                    #        service="turn_off",
                    #        service_data={"entity_id": entity_id},
                    #    )
                    #    self.isRunning = False
                    #    _LOGGER.warning(f"{self.deviceName}: Humdidifier ausgeschaltet.")
                    
                    await self.hass.services.async_call(
                        domain="switch",
                        service="turn_off",
                        service_data={"entity_id": entity_id},
                    )
                    self.isRunning = False
                    _LOGGER.warning(f"{self.deviceName}: Default-Swtich ausgeschaltet.")
                    return

                # Licht ausschalten
                elif self.deviceType == "Light":
                    
                    if self.isDimmable:
                        if self.voltageFromNumber:
                            if not self.islightON :
                                await self.hass.services.async_call(
                                    domain="switch",
                                    service="turn_off",
                                    service_data={"entity_id": entity_id},
                                )
                                self.isRunning = False
                                _LOGGER.warning(f"{self.deviceName}: Light OFF.")
                                await self.set_value(0)
                                return
                        else:
                            await self.hass.services.async_call(
                                domain="light",
                                service="turn_off",
                                service_data={
                                    "entity_id": entity_id},
                            )
                            self.isRunning = False
                            _LOGGER.warning(f"{self.deviceName}:Light OFF.")
                            return  
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.warning(f"{self.deviceName}: Default-Swtich ausgeschaltet.")
                        return                                            
                                                    
                # Abluft ausschalten
                elif self.deviceType == "Exhaust":    
                    if self.isDimmable == True:
                        #await self.hass.services.async_call(
                        #    domain="fan",
                        #    service="turn_off",
                        #    service_data={"entity_id": entity_id},
                        #)
                        #self.isRunning = False
                        #_LOGGER.warning(f"{self.deviceName}: Abluft ausgeschaltet.")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.warning(f"{self.deviceName}: Abluft ausgeschaltet.")
                        return
                
                # Zuluft ausschalten
                elif self.deviceType == "Inhaust":    
                    if self.isDimmable == True:
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                    _LOGGER.warning(f"{self.deviceName}: Abluft ausgeschaltet.")

                # Ventilator ausschalten
                elif self.deviceType == "Ventilation":
                    if self.isTasmota:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.warning(f"{self.deviceName}: Tasmota-Ventilator ausgeschaltet.")
                        return
                    if self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.warning(f"{self.deviceName}: Ventilator ausgeschaltet.")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.warning(f"{self.deviceName}: Ventilator ausgeschaltet.")
                        return                       
                        
                # Standard-Switch ausschalten
                else:
                    await self.hass.services.async_call(
                        domain="switch",
                        service="turn_off",
                        service_data={"entity_id": entity_id},
                    )
                    self.isRunning = False
                    _LOGGER.warning(f"{self.deviceName}: Default-Swtich ausgeschaltet.")
        except Exception as e:
            _LOGGER.error(f"Fehler beim Ausschalten von {self.deviceName}: {e} ")

    ## Special Changes
    async def set_value(self, value):
        """Setzt einen numerischen Wert, falls unterstützt und relevant (duty oder voltage)."""
        if not self.options:
            _LOGGER.error(f"{self.deviceName} unterstützt keine numerischen Werte.")
            return

        # Suche erste passende Option mit 'duty' oder 'voltage' in der entity_id
        for option in self.options:
            entity_id = option.get("entity_id", "")
            if "duty" in entity_id or "intensity" in entity_id:
                try:
                    await self.hass.services.async_call(
                        domain="number",
                        service="set_value",
                        service_data={"entity_id": entity_id, "value": value},
                    )
                    _LOGGER.warning(f"Wert für {self.deviceName} wurde für {entity_id} to {value} set.")
                    return
                except Exception as e:
                    _LOGGER.error(f"Fehler beim Setzen des Wertes für {self.deviceName}: {e}")
                    return

        _LOGGER.error(f"{self.deviceName} hat keine passende Option mit 'duty' oder 'voltage' in der entity_id.")

    async def set_mode(self, mode):
        """Setzt den Mode des Geräts, falls unterstützt."""
        if not self.options:
            _LOGGER.error(f"{self.deviceName} unterstützt keine Modi.")
            return
        try:
            await self.hass.services.async_call(
                domain="select",
                service="select_option",
                service_data={"entity_id": self.options[0]["entity_id"], "option": mode},
            )
            _LOGGER.warning(f"Mode für {self.deviceName} wurde to {mode} set.")
        except Exception as e:
            _LOGGER.error(f"Fehler beim Setzen des Mode für {self.deviceName}: {e}")

    # Modes for all Devices
    async def WorkMode(self,workmode):
        self.inWorkMode = workmode
        if self.inWorkMode:
            if self.isDimmable:
                if self.deviceType == "Light":
                    self.voltage = self.initVoltage
                    await self.turn_on(brightness_pct=self.initVoltage)
                else:
                    self.dutyCycle = self.minDuty
                    await self.turn_on(percentage=self.minDuty)
            else:
                if self.deviceType == "Light":
                    return
                if self.deviceType == "Pump":
                    return    
                else:
                    await self.turn_off()
        else:
            if self.isDimmable:
                if self.deviceType == "Light":
                    self.voltage = self.maxVoltage
                    await self.turn_on(brightness_pct=self.maxVoltage)
                else:
                    self.dutyCycle = self.maxDuty
                    await self.turn_on(percentage=self.maxDuty)
            else:
                if self.deviceType == "Light":
                    return 
                if self.deviceType == "Pump":
                    return
                else:
                    await self.turn_on()           
               
    # Update Listener
    def registerListener(self):
        deviceEntitiys = self.getEntitys()
        _LOGGER.warning(f"UpdateListener für {self.deviceName} registriert for {deviceEntitiys}.")
        
        async def deviceUpdateListner(event):
            
            entity_id = event.data.get("entity_id")
            
            if entity_id in deviceEntitiys:
                old_state = event.data.get("old_state")
                new_state = event.data.get("new_state")
                            
                def parse_state(state):
                    """Konvertiere den Zustand zu float oder lasse ihn als String."""
                    if state and state.state:
                        # Versuche, den Wert in einen Float umzuwandeln
                        try:
                            return float(state.state)
                        except ValueError:
                            # Wenn nicht möglich, behalte den ursprünglichen String
                            return state.state
                    return None
                
                old_state_value = parse_state(old_state)
                new_state_value = parse_state(new_state)
                
                updateData = {"entity_id":entity_id,"newValue":new_state_value,"oldValue":old_state_value}                               
                
                _LOGGER.debug(
                    f"Device State-Change für {self.deviceName} an {entity_id} in {self.inRoom}: "
                    f"Alt: {old_state_value}, Neu: {new_state_value}"
                )
                self.checkForControlValue()

                # Gib das Update-Publication-Objekt weiter
                await self.eventManager.emit("DeviceStateUpdate",updateData)
                
        # Registriere den Listener
        self.hass.bus.async_listen("state_changed", deviceUpdateListner)
        _LOGGER.debug(f"Device-State-Change Listener für {self.deviceName} registriert.")
        

    def setMinMax(self,data):
        minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")

        if not minMaxSets or not minMaxSets.get("active", False):
            return  # Nichts aktiv → nichts tun

        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
            self.minVoltage = float(minMaxSets.get("minVoltage")) 
            self.maxVoltage = float(minMaxSets.get("maxVoltage"))
            self.clamp_voltage(self.voltage)
        elif "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            self.minDuty = float(minMaxSets.get("minDuty"))
            self.maxDuty = float(minMaxSets.get("maxDuty"))
            self.clamp_duty_cycle(self.dutyCycle)
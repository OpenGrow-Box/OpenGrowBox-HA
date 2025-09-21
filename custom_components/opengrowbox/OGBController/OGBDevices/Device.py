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
        self.isAcInfinDev = False
        self.inRoom = inRoom
        self.switches = []
        self.options = []
        self.sensors = []
        self.ogbsettings = []
        self.initialization = False
        self.inWorkMode = False

        # EVENTS
        self.eventManager.on("DeviceStateUpdate", self.deviceUpdate)        
        self.eventManager.on("WorkModeChange", self.WorkMode)
        self.eventManager.on("SetMinMax", self.userSetMinMax)
   
        asyncio.create_task(self.deviceInit(deviceData))
   
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
    async def deviceInit(self, entitys):
    
        self.identifySwitchesAndSensors(entitys)
        self.identifyIfRunningState()
        self.identifDimmable()
        self.checkForControlValue()
        self.checkMinMax(False)
        self.identifyCapabilities()
        if(self.initialization == True):
            self.deviceUpdater()
            _LOGGER.debug(f"Device {self.deviceName} Initialization Completed")
            self.initialization = False
            logging.warning(f"Device: {self.deviceName} Initialization done {self}")
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
                    _LOGGER.debug(
                        f"{self.deviceName} Updated {entity_id}: Old Value: {old_value}, New Value: {new_value}."
                    )
                    return True
            return False

        # Aktualisiere Sensor-Werte
        if "sensor." in entity_id:
            _LOGGER.debug(f"{self.deviceName} Start Update Sensor for {updateData}.")
            await update_entity_value(self.sensors, entity_id, new_value)
            _LOGGER.debug(f"{self.deviceName} warning {self.__repr__()}.")

        # Aktualisiere Switch-Werte
        if any(prefix in entity_id for prefix in ["fan.", "light.", "switch.", "humidifier."]):
            _LOGGER.debug(f"{self.deviceName} Start Update Switch for {updateData}.")
            await update_entity_value(self.switches, entity_id, new_value)
            self.identifyIfRunningState()
            
        # Aktualisiere weitere spezifische Werte
        if any(prefix in entity_id for prefix in ["number.", "text.", "time.", "select.", "date."]):
            _LOGGER.debug(f"{self.deviceName} Start Update Switches for {updateData}.")
            await update_entity_value(self.options, entity_id, new_value)
            _LOGGER.debug(f"{self.deviceName} warning {self.__repr__()}.")

        # Aktualisiere spezifische OGB-Entitäten
        if any(prefix in entity_id for prefix in ["ogb_"]):
            _LOGGER.debug(f"{self.deviceName} Start Update OGBS for {updateData}.")
            await update_entity_value(self.sensors, entity_id, new_value)

    def checkMinMax(self,data):
        minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")

        if not self.isDimmable: 
            return

        if not minMaxSets or not minMaxSets.get("active", False):
            return  # Nichts aktiv → nichts tun

        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
            self.minVoltage = minMaxSets.get("minVoltage")
            self.maxVoltage = minMaxSets.get("maxVoltage")
            logging.debug(f"{self.deviceName} Device MinMax sets: Max-Voltage:{self.maxVoltage} Min-Voltage:{self.minVoltage}")
        
        elif "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            self.minDuty = minMaxSets.get("minDuty")
            self.maxDuty = minMaxSets.get("maxDuty")
            logging.debug(f"{self.deviceName} Device MinMax sets: Max-Duty:{self.maxDuty} Min-Duty:{self.minDuty}")

    def initialize_duty_cycle(self):
        """Initialisiert den Duty Cycle auf 50%, falls nichts anderes vorliegt."""
        
        # Generischer Default
        self.dutyCycle = 50  
        
        if self.isTasmota:
            self.dutyCycle = 50
        elif self.isAcInfinDev:
            self.steps = 10 
            self.maxDuty = 100
            self.minDuty = 0
        
        _LOGGER.debug(f"{self.deviceName}: Duty Cycle Init to {self.dutyCycle}%.")
      
    # Eval sensor if Intressted in 
    def evalSensors(self, sensor_id: str) -> bool:
        interested_mapping = ("_temperature", "_humidity", "_dewpoint", "_co2","_duty","_moisture","_intensity","_ph","_ec","_tds")
        return any(keyword in sensor_id for keyword in interested_mapping)

    # Mapp Entity Types to Class vars
    def identifySwitchesAndSensors(self, entitys):
        """Identifiziere Switches und Sensoren aus der Liste der Entitäten und prüfe ungültige Werte."""
        _LOGGER.info(f"Identify all given {entitys}")

        try:
            for entity in entitys:

                entityID = entity.get("entity_id")
                entityValue = entity.get("value")
                entityPlatform = entity.get("platform")
                
                # Clear OGB Devs out
                if "ogb_" in entityID:
                    _LOGGER.debug(f"Entity {entityID} contains 'ogb_'. Adding to switches.")
                    self.ogbsettings.append(entity)
                    continue  # Überspringe die weitere Verarbeitung für diese Entität

                # Prüfe for special Platform
                if entityPlatform == "ac_infinity":
                    _LOGGER.debug(f"FOUND AC-INFINITY Entity {self.deviceName} Initial value detected {entityValue} from {entity} Full-Entity-List:{entitys}")
                    self.isAcInfinDev = True

                if entityPlatform == "crescontrol":
                    _LOGGER.debug(f"FOUND AC-INFINITY Entity {self.deviceName} Initial value detected {entityValue} from {entity} Full-Entity-List:{entitys}")
                    self.voltageFromNumber = True
                    
                if entityPlatform == "tasmota":
                    _LOGGER.debug(f"FOUND Tasmota Entity {self.deviceName} Initial value detected {entityValue} from {entity} Full-Entity-List:{entitys}")
                    self.isTasmota = True

                if entityValue in ("None", "unknown", "Unbekannt", "unavailable"):
                    _LOGGER.debug(f"DEVICE {self.deviceName} Initial invalid value detected for {entityID}. ")
                    continue
                        
                if entityID.startswith(("switch.", "light.", "fan.", "climate.", "humidifier.")):
                    self.switches.append(entity)
                elif entityID.startswith(("select.", "number.","date.", "text.", "time.")):
                    self.options.append(entity)
                elif entityID.startswith("sensor."):
                    if self.evalSensors(entityID):
                        self.sensors.append(entity)
            self.initialization = True
        except:
            _LOGGER.error(f"Device:{self.deviceName} INIT ERROR {self.deviceName}.")
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
            "canIntake": ["intake"],
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
                if self.deviceType == "Intake":
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
        _LOGGER.debug(f"{self.deviceName}: Capabilities identified: {self.dataStore.get('capabilities')}")

    def identifyIfRunningState(self):
        if self.isAcInfinDev:
            for select in self.options:
                # Nur select-Entitäten prüfen, number-Entitäten überspringen
                entity_id = select.get("entity_id", "")
                if entity_id.startswith("number."):
                    continue  # number-Entitäten überspringen
                    
                option_value = select.get("value")
                if option_value == "on" or option_value == "On":
                    self.isRunning = True
                    return  # Früh beenden, da Zustand gefunden
                elif option_value == "off" or option_value == "Off":
                    self.isRunning = False
                    return
                elif option_value in (None, "unknown", "Unbekannt", "unavailable"):
                    raise ValueError(f"Invalid Entity state '{option_value}' for {self.deviceName}")
                else:
                    raise ValueError(f"Invalid Entity state '{option_value}' for {self.deviceName}")   
        else:
            for switch in self.switches:
                switch_value = switch.get("value")
                if switch_value == "on":
                    self.isRunning = True
                    return
                elif switch_value == "off":
                    self.isRunning = False
                    return
                elif switch_value in (None, "unknown", "Unbekannt", "unavailable"):
                    raise ValueError(f"Invalid Entity state '{switch_value}' for {self.deviceName}")
                else:
                    raise ValueError(f"Invalid Entity state '{switch_value}' for {self.deviceName}")

    # Überprüfe, ob das Gerät dimmbar ist
    def identifDimmable(self):
        allowedDeviceTypes = ["ventilation", "exhaust","intake","light","humdifier","dehumidifier","heater","cooler"]

        # Gerät muss in der Liste der erlaubten Typen sein
        if self.deviceType.lower() not in allowedDeviceTypes:
            _LOGGER.debug(f"{self.deviceName}: {self.deviceType} Is not in a list for Dimmable Devices.")
            return

        dimmableKeys = ["fan.", "light.","number.","_duty","_intensity"]

        # Prüfen, ob ein Schlüssel in switches, options oder sensors vorhanden ist
        for source in (self.switches, self.options, self.sensors):
            for entity in source:
                entity_id = entity.get("entity_id", "").lower()
                if any(key in entity_id for key in dimmableKeys):
                    self.isDimmable = True
                    _LOGGER.debug(f"{self.deviceName}: Device Recognized as Dimmable - DeviceName {self.deviceName} Entity_id: {entity_id}")
                    return

    def checkForControlValue(self):
        """Findet und aktualisiert den Duty Cycle oder den Voltage-Wert basierend to Gerätetyp und Daten."""
        if not self.isDimmable:
            _LOGGER.debug(f"{self.deviceName}: is not Dimmable ")
            return
        
        if not self.sensors and not self.options:
            _LOGGER.debug(f"{self.deviceName}: NO Sensor data or Options found ")
            return

        relevant_keys = ["_duty","_intensity","_dutyCycle"]

        def convert_to_int(value, multiply_by_10=False):
            """Konvertiert einen Wert sicher zu int, mit optionaler Multiplikation."""
            try:
                # Erst zu float konvertieren um alle String/numerischen Werte zu handhaben
                float_value = float(value)
                
                # Optional mit 10 multiplizieren
                if multiply_by_10:
                    float_value *= 10
                    
                # Zu int konvertieren
                return int(float_value)
                
            except (ValueError, TypeError) as e:
                _LOGGER.error(f"Konvertierungsfehler für Wert '{value}': {e}")
                return None

        # Sensoren durchgehen
        for sensor in self.sensors:
            _LOGGER.debug(f"Prüfe Sensor: {sensor}")

            if any(key in sensor["entity_id"].lower() for key in relevant_keys):
                _LOGGER.debug(f"{self.deviceName}: Relevant Sensor Found: {sensor['entity_id']}")
                
                raw_value = sensor.get("value", None)
                if raw_value is None:
                    _LOGGER.debug(f"{self.deviceName}: No Value in Sensor: {sensor}")
                    continue

                # Wert konvertieren
                converted_value = convert_to_int(raw_value, multiply_by_10=self.isAcInfinDev)
                if converted_value is None:
                    continue

                # Wert je nach Gerätetyp setzen
                if self.deviceType == "Light":
                    self.voltage = converted_value
                    _LOGGER.debug(f"{self.deviceName}: Voltage from Sensor updated to {self.voltage}%.")
                elif self.deviceType in ["Exhaust", "Intake", "Ventilation", "Humidifier", "Dehumidifier"]:
                    self.dutyCycle = converted_value
                    _LOGGER.debug(f"{self.deviceName}: Duty Cycle from Sensor updated to {self.dutyCycle}%.")

        # Options durchgehen
        for option in self.options:
            _LOGGER.warning(f"Prüfe Option: {option}")
            
            if any(key in option["entity_id"] for key in relevant_keys):
                raw_value = option.get("value", 0)
                
                # Für Light-Geräte spezielle Logik
                if self.deviceType == "Light":
                    self.voltageFromNumber = True
                    # Für Light: immer mit 10 multiplizieren wenn isAcInfinDev ODER voltageFromNumber
                    multiply_by_10 = self.isAcInfinDev or self.voltageFromNumber
                    converted_value = convert_to_int(raw_value, multiply_by_10=multiply_by_10)
                    
                    if converted_value is not None:
                        self.voltage = converted_value
                        _LOGGER.debug(f"{self.deviceName}: Voltage set from Options to {self.voltage}%.")
                        return
                else:
                    # Für alle anderen Gerätetypen
                    converted_value = convert_to_int(raw_value, multiply_by_10=self.isAcInfinDev)
                    
                    if converted_value is not None:
                        self.dutyCycle = converted_value
                        _LOGGER.debug(f"{self.deviceName}: Duty Cycle set from Options to {self.dutyCycle}%.")
                        return
                
    async def turn_on(self, **kwargs):
        """Schaltet das Gerät ein."""
        try:
            brightness_pct = kwargs.get("brightness_pct")
            percentage = kwargs.get("percentage")

            # === Sonderfall: AcInfinity Geräte ===
            if self.isAcInfinDev:
                entity_ids = []
                if self.switches:
                    entity_ids = [
                        switch["entity_id"] for switch in self.switches 
                        if "select." in switch["entity_id"]
                    ]
                if not entity_ids:
                    _LOGGER.warning(f"{self.deviceName}: Keine passenden Select-Switches, nutze Fallback auf Options")
                    if self.options:
                        entity_ids = [
                            option["entity_id"] for option in self.options
                            if "select." in option["entity_id"]
                        ]

                for entity_id in entity_ids:
                    logging.error(f"{self.deviceName} ON ACTION with ID {entity_id}")
                    if self.isRunning == False:
                        await self.hass.services.async_call(
                            domain="select",
                            service="select_option",
                            service_data={
                                "entity_id": entity_id,
                                "option": "On"
                            },
                        )
                    # Zusatzaktionen je nach Gerätetyp
                    if self.deviceType in ["Light", "Humidifier", "Deumidifier", "Exhaust", "Intake", "Ventilation"]:
                        # Bei AcInfinity wird oft ein Prozentwert extra gesetzt
                        
                        if self.deviceType == "Light":
                            if brightness_pct is not None:
                                _LOGGER.warning(f"{self.deviceName}: set value to {brightness_pct}")
                                await self.set_value(int(brightness_pct/10))                      
                        else:
                            if percentage is not None:
                                _LOGGER.warning(f"{self.deviceName}: set value to {percentage}")
                                await self.set_value(percentage/10)
   
                    self.isRunning = True

                return

            # === Standardgeräte ===
            if not self.switches:
                _LOGGER.error(f"{self.deviceName} has not Switch to Activate or Turn On")
                return

            entity_ids = [switch["entity_id"] for switch in self.switches]

            for entity_id in entity_ids:
                # Climate einschalten
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
                    _LOGGER.debug(f"{self.deviceName}: HVAC-Mode {hvac_mode} ON.")
                    return

                # Humidifier einschalten
                elif self.deviceType == "Humidifier":
                    if self.realHumidifierClass:
                        await self.hass.services.async_call(
                            domain="humidifier",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                    self.isRunning = True
                    _LOGGER.debug(f"{self.deviceName}: Humidifier ON.")
                    return

                # Dehumidifier einschalten
                elif self.deviceType == "Deumidifier":
                    await self.hass.services.async_call(
                        domain="switch",
                        service="turn_on",
                        service_data={"entity_id": entity_id},
                    )
                    self.isRunning = True
                    _LOGGER.debug(f"{self.deviceName}: Dehumidifier ON.")
                    return

                # Light einschalten
                elif self.deviceType == "Light":
                    if self.isDimmable:
                        if self.islightON :
                            await self.hass.services.async_call(
                                domain="switch",
                                service="turn_on",
                                service_data={"entity_id": entity_id},
                            )
                            self.isRunning = True
                            _LOGGER.warning(f"{self.deviceName}: light changed to  {float(brightness_pct/10)}.")
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
                            _LOGGER.warning(f"{self.deviceName}: Light ON ({brightness_pct}%).")
                            return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Light ON (Switch).")
                        return

                # Exhaust einschalten
                elif self.deviceType == "Exhaust":
                    if self.isTasmota:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id,
                                "brightness_pct": brightness_pct,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Exhaust ON ({brightness_pct}%).")
                        return
                    elif self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="set_percentage",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": percentage,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Exhaust ON ({percentage}%).")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Exhaust ON (Switch).")
                        return

                # Intake einschalten
                elif self.deviceType == "Intake":
                    if self.isTasmota:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id,
                                "brightness_pct": brightness_pct,
                            },
                        )
                        self.isRunning = True
                        return
                    elif self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="set_percentage",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": percentage,
                            },
                        )
                        self.isRunning = True
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Intake ON (Switch).")
                        return

                # Ventilation einschalten
                elif self.deviceType == "Ventilation":
                    if self.isTasmota:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id,
                                "brightness_pct": brightness_pct,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Ventilation ON ({brightness_pct}%).")
                        return
                    elif self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="set_percentage",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": percentage,
                            },
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Ventilation ON ({percentage}%).")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: Ventilation ON (Switch).")
                        return

                # Ventilation einschalten
                elif self.deviceType == "CO2":
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
                        _LOGGER.warning(f"{self.deviceName}: Ventilation ON ({percentage}%).")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.warning(f"{self.deviceName}: CO2 ON (Switch).")
                        return

                # Fallback
                else:
                    await self.hass.services.async_call(
                        domain="switch",
                        service="turn_on",
                        service_data={"entity_id": entity_id},
                    )
                    self.isRunning = True
                    _LOGGER.warning(f"{self.deviceName}: Default-Switch ON.")
                    return

        except Exception as e:
            _LOGGER.error(f"Error TurnON -> {self.deviceName}: {e}")

    async def turn_off(self, **kwargs):
        """Schaltet das Gerät aus."""
        try:
            # === Sonderfall: AcInfinity Geräte ===
            if self.isAcInfinDev:
                entity_ids = []
                if self.switches:
                    entity_ids = [
                        switch["entity_id"] for switch in self.switches 
                        if "select." in switch["entity_id"]
                    ]
                if not entity_ids:
                    _LOGGER.warning(f"{self.deviceName}: Keine passenden Select-Switches, nutze Fallback auf Options")
                    if self.options:
                        entity_ids = [
                            option["entity_id"] for option in self.options
                            if "select." in option["entity_id"]
                        ]

                for entity_id in entity_ids:
                    logging.error(f"{self.deviceName} OFF ACTION with ID {entity_id}")
                    await self.hass.services.async_call(
                        domain="select",
                        service="select_option",
                        service_data={
                            "entity_id": entity_id,
                            "option": "Off"
                        },
                    )
                    # Zusatzaktionen je nach Gerätetyp
                    if self.deviceType in ["Light", "Humidifier","Exhaust","Ventilation"]:
                        await self.hass.services.async_call(
                            domain="number",
                            service="set_value",
                            service_data={
                                "entity_id": entity_id,
                                "value": 1
                            },
                        )

                    self.isRunning = False
                    _LOGGER.debug(f"{self.deviceName}: AcInfinity über select OFF.")
                return

            # === Standardgeräte ===
            if not self.switches:
                _LOGGER.error(f"{self.deviceName} has NO Switches to Turn OFF")
                return

            entity_ids = [switch["entity_id"] for switch in self.switches]

            for entity_id in entity_ids:
                _LOGGER.debug(f"{self.deviceName}: Service-Call for Entity: {entity_id}")

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
                    _LOGGER.debug(f"{self.deviceName}: HVAC-Mode OFF.")
                    return

                # Humidifier ausschalten
                elif self.deviceType == "Humidifier":
                    await self.hass.services.async_call(
                        domain="switch",
                        service="turn_off",
                        service_data={"entity_id": entity_id},
                    )
                    self.isRunning = False
                    _LOGGER.debug(f"{self.deviceName}: Humidifier OFF.")
                    return

                # Light ausschalten
                elif self.deviceType == "Light":
                    if self.isDimmable:
                        if self.voltageFromNumber and not self.islightON:
                            await self.hass.services.async_call(
                                domain="switch",
                                service="turn_off",
                                service_data={"entity_id": entity_id},
                            )
                            self.isRunning = False
                            await self.set_value(0)
                            _LOGGER.debug(f"{self.deviceName}: Light OFF (Number-Voltage).")
                            return
                        else:
                            await self.hass.services.async_call(
                                domain="light",
                                service="turn_off",
                                service_data={"entity_id": entity_id},
                            )
                            self.isRunning = False
                            _LOGGER.debug(f"{self.deviceName}: Light OFF.")
                            return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.debug(f"{self.deviceName}: Light OFF (Default-Switch).")
                        return

                # Exhaust ausschalten
                elif self.deviceType == "Exhaust":
                    if self.isDimmable:
                        return  # Deaktiviert
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.debug(f"{self.deviceName}: Exhaust OFF.")
                        return

                # Intake ausschalten
                elif self.deviceType == "Intake":
                    if self.isDimmable:
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.debug(f"{self.deviceName}: Intake OFF.")
                        return

                # Ventilation ausschalten
                elif self.deviceType == "Ventilation":
                    if self.isTasmota:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.debug(f"{self.deviceName}: Ventilation OFF (Tasmota).")
                        return
                    elif self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.debug(f"{self.deviceName}: Ventilation OFF (Fan).")
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.debug(f"{self.deviceName}: Ventilation OFF (Switch).")
                        return
                # Intake ausschalten
                elif self.deviceType == "CO2":
                    if self.isDimmable:
                        return
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        _LOGGER.warning(f"{self.deviceName}: CO2 OFF.")
                        return


                # Fallback: Standard-Switch
                else:
                    await self.hass.services.async_call(
                        domain="switch",
                        service="turn_off",
                        service_data={"entity_id": entity_id},
                    )
                    self.isRunning = False
                    _LOGGER.debug(f"{self.deviceName}: Default-Switch OFF.")
                    return

        except Exception as e:
            _LOGGER.error(f"Fehler beim Ausschalten von {self.deviceName}: {e}")

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
                    if self.isAcInfinDev:
                        await self.hass.services.async_call(
                            domain="number",
                            service="set_value",
                            service_data={"entity_id": entity_id, "value": float(int(value))},
                        )
                        _LOGGER.warning(f"Wert für {self.deviceName} wurde für {entity_id} to {float(int(value))} set.")
                        return                       
                    else:
                        await self.hass.services.async_call(
                            domain="number",
                            service="set_value",
                            service_data={"entity_id": entity_id, "value": value},
                        )
                        _LOGGER.debug(f"Wert für {self.deviceName} wurde für {entity_id} to {value} set.")
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
            _LOGGER.debug(f"Mode für {self.deviceName} wurde to {mode} set.")
        except Exception as e:
            _LOGGER.error(f"Fehler beim Setzen des Mode für {self.deviceName}: {e}")

    # Modes for all Devices
    async def WorkMode(self,workmode):
        self.inWorkMode = workmode
        if self.inWorkMode:
            if self.isDimmable:
                if self.deviceType == "Light":
                    if self.sunPhaseActive:
                        await self.eventManager.emit("pauseSunPhase",False)
                        return
                    self.voltage = self.initVoltage
                    await self.turn_on(brightness_pct=self.initVoltage)
                else:
                    self.dutyCycle = self.minDuty
                    if self.isTasmota:
                        await self.turn_on(brightness_pct=self.minDuty)
                    await self.turn_on(percentage=self.minDuty)
            else:
                if self.deviceType == "Light":
                    return
                if self.deviceType == "Pump":
                    return
                if self.deviceType == "Sensor":
                    return    
                else:
                    await self.turn_off()
        else:
            if self.isDimmable:
                if self.deviceType == "Light":
                    if self.sunPhaseActive:
                        await self.eventManager.emit("resumeSunPhase",False)
                        return
                    self.voltage = self.maxVoltage
                    await self.turn_on(brightness_pct=self.maxVoltage)
                else:
                    self.dutyCycle = self.maxDuty
                    if self.isTasmota:
                        await self.turn_on(brightness_pct=self.maxDuty)
                    await self.turn_on(percentage=self.maxDuty)
            else:
                if self.deviceType == "Light":
                    return 
                if self.deviceType == "Pump":
                    return
                if self.deviceType == "Sensor":
                    return  
                else:
                    await self.turn_on()           
               
    # Update Listener
    def deviceUpdater(self):
        deviceEntitiys = self.getEntitys()
        _LOGGER.debug(f"UpdateListener für {self.deviceName} registriert for {deviceEntitiys}.")
        
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
                
                # Check if this is a switch/control entity that affects running state
                if any(prefix in entity_id for prefix in ["fan.", "light.", "switch.", "humidifier.", "select."]):
                    # Update the entity value first
                    for entity_list in [self.switches, self.options]:
                        for entity in entity_list:
                            if entity.get("entity_id") == entity_id:
                                entity["value"] = new_state_value
                                break
                    
                    # Now update the running state
                    try:
                        self.identifyIfRunningState()
                        _LOGGER.debug(f"{self.deviceName}: Running state updated to {self.isRunning} after {entity_id} changed to {new_state_value}")
                    except Exception as e:
                        _LOGGER.error(f"{self.deviceName}: Error updating running state: {e}")
                
                self.checkForControlValue()

                # Gib das Update-Publication-Objekt weiter
                await self.eventManager.emit("DeviceStateUpdate",updateData)
                
        # Registriere den Listener
        self.hass.bus.async_listen("state_changed", deviceUpdateListner)
        _LOGGER.debug(f"Device-State-Change Listener für {self.deviceName} registriert.")  

    async def userSetMinMax(self,data):
        minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")

        if not self.isDimmable: 
            return

        if not minMaxSets or not minMaxSets.get("active", False):
            return
        
        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
            self.minVoltage = float(minMaxSets.get("minVoltage")) 
            self.maxVoltage = float(minMaxSets.get("maxVoltage"))
            await self.changeMinMaxValues(self.clamp_voltage(self.voltage))
            
        elif "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            self.minDuty = float(minMaxSets.get("minDuty"))
            self.maxDuty = float(minMaxSets.get("maxDuty"))
            await self.changeMinMaxValues(self.clamp_duty_cycle(self.dutyCycle))

    async def changeMinMaxValues(self,newValue):
        if self.isDimmable:
            
            _LOGGER.debug(f"{self.deviceName}:as Type:{self.deviceType} NewValue: {newValue}")
    
            if self.deviceType == "Light":
                if self.isDimmable:
                    self.voltage = newValue
                    await self.turn_on(brightness_pct=newValue)
            else:
                self.dutyCycle = newValue
                if self.isTasmota:
                    await self.turn_on(brightness_pct=float(newValue))
                else:
                    await self.turn_on(percentage=newValue)

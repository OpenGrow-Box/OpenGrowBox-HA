import logging
import asyncio

_LOGGER = logging.getLogger(__name__)

class Device:
    # Optional class attributes - may be set by subclasses
    PlantStageMinMax = None  # type: ignore - Set by Light.py subclass

    def __init__(self, deviceName, deviceData, eventManager,dataStore, deviceType,inRoom, hass=None,deviceLabel="EMPTY",allLabels=[]):
        self.hass = hass
        self.eventManager = eventManager
        self.event_manager = eventManager  # Backwards compatibility alias
        self.dataStore = dataStore
        self.data_store = dataStore  # Backwards compatibility alias
        self.deviceName = deviceName
        self.deviceType = deviceType
        self.deviceLabel = deviceLabel
        self.labelMap = allLabels  # Store labels for propagation to remapped sensors
        self.isSpecialDevice = False
        self.isRunning = False
        self.isDimmable = False
        self.isAcInfinDev = False
        self.inRoom = inRoom
        self.room = inRoom  # Backwards compatibility alias
        self.switches = []
        self.options = []
        self.sensors = []
        self.ogbsettings = []
        self.initialization = False
        self.inWorkMode = False
        self.isInitialized = False
        
        # Additional attributes for compatibility with modular code
        self.voltage = None
        self.dutyCycle = None  # Don't set default, let subclass/setMinMax determine it
        self.minVoltage = None
        self.maxVoltage = None
        self.minDuty = None
        self.maxDuty = None
        self.is_minmax_active = False  # Track if MinMax control is active for this device
        self.voltageFromNumber = False
        
        # EVENTS
        self.eventManager.on("DeviceStateUpdate", self.deviceUpdate)        
        self.eventManager.on("WorkModeChange", self.WorkMode)
        self.eventManager.on("SetMinMax", self.userSetMinMax)
        self.eventManager.on("MinMaxControlDisabled", self.on_minmax_control_disabled)
        self.eventManager.on("MinMaxControlEnabled", self.on_minmax_control_enabled)

    
        self.deviceInit(deviceData)

    @property
    def option_count(self) -> int:
        """Gibt die Anzahl aller Optionen zurück."""
        return len(self.options)
    
    @property
    def switch_count(self) -> int:
        """Gibt die Anzahl aller Optionen zurück."""
        return len(self.switches)

    @property
    def sensor_count(self) -> int:
        """Gibt die Anzahl aller Sensoren zurück."""
        return len(self.sensors)

    def __iter__(self):
        return iter(self.__dict__.items())

    def __repr__(self):
        """Kompakte Darstellung für Debugging."""
        if not self.isInitialized:
            return f"Device(name='{self.deviceName}', room='{self.inRoom}', type='{self.deviceType}', status='NOT_INITIALIZED')"
        
        # Zähle alle Sensoren aus allen Containern
        sensor_count = sum(
            len(getattr(container, "sensors", []))
            for container in (self, *self.switches, *self.options, *self.ogbsettings)
        )
        
        status_flags = []
        if self.isRunning:
            status_flags.append("ACTIVE")
        if self.isDimmable:
            status_flags.append("DIMMABLE")
        if self.isSpecialDevice:
            status_flags.append("SPECIAL")
        if self.isAcInfinDev:
            status_flags.append("AC_INFIN")
        if self.inWorkMode:
            status_flags.append("WORKMODE")
        
        flags_str = f", flags=[{', '.join(status_flags)}]" if status_flags else ""
        
        return (
            f"Device(name='{self.deviceName}', type='{self.deviceType}', room='{self.inRoom}', "
            f"switches={self.switch_count}, options={self.option_count}, sensors={sensor_count}{flags_str})"
        )

    def __str__(self):
        """Detaillierte, lesbare Darstellung für Nutzer."""
        if not self.isInitialized:
            return f"Device '{self.deviceName}' (Room: {self.inRoom}) - NOT INITIALIZED"
        
        # Header
        lines = [
            "╔" + "═" * 80 + "╗",
            f"║ {'DEVICE INFORMATION':^78} ║",
            "╠" + "═" * 80 + "╣",
        ]
        
        # Basis-Informationen
        lines.extend([
            f"║ Name:          {self.deviceName:<65} ║",
            f"║ Type:          {self.deviceType:<65} ║",
            f"║ Room:          {self.inRoom:<65} ║",
            f"║ Label:         {self.deviceLabel:<65} ║",
        ])
        
        # Status Flags
        lines.append("╠" + "─" * 80 + "╣")
        status_items = [
            f"Running: {'✓' if self.isRunning else '✗'}",
            f"Dimmable: {'✓' if self.isDimmable else '✗'}",
            f"Special: {'✓' if self.isSpecialDevice else '✗'}",
            f"AC Infin: {'✓' if self.isAcInfinDev else '✗'}",
            f"WorkMode: {'✓' if self.inWorkMode else '✗'}",
        ]
        lines.append(f"║ Status:        {' | '.join(status_items):<65} ║")
        
        # Komponenten-Übersicht
        lines.append("╠" + "─" * 80 + "╣")
        lines.extend([
            f"║ Switches:      {self.switch_count:<65} ║",
            f"║ Options:       {self.option_count:<65} ║",
            f"║ OGB Settings:  {len(self.ogbsettings):<65} ║",
        ])
        
        # Sensoren Detail
        sensor_count = sum(
            len(getattr(container, "sensors", []))
            for container in (self, *self.switches, *self.options, *self.ogbsettings)
        )
        device_sensors = len(self.sensors)
        child_sensors = sensor_count - device_sensors
        
        lines.extend([
            f"║ Total Sensors: {sensor_count:<65} ║",
            f"║   ├─ Device:   {device_sensors:<65} ║",
            f"║   └─ Children: {child_sensors:<65} ║",
        ])
        
        # Detaillierte Sensor-Liste (optional, wenn nicht zu viele)
        if sensor_count > 0 and sensor_count <= 10:
            lines.append("╠" + "─" * 80 + "╣")
            lines.append(f"║ {'SENSORS':^78} ║")
            lines.append("╠" + "─" * 80 + "╣")
            
            # Device Sensoren
            if self.sensors:
                lines.append(f"║ Device Sensors:                                                              ║")
                for sensor in self.sensors[:5]:  # Max 5 anzeigen
                    sensor_name = getattr(sensor, 'sensorName', str(sensor))[:60]
                    lines.append(f"║   • {sensor_name:<75} ║")
            
            # Switch Sensoren
            for idx, switch in enumerate(self.switches[:3]):  # Max 3 Switches
                if hasattr(switch, 'sensors') and switch.sensors:
                    switch_name = getattr(switch, 'switchName', f'Switch {idx}')[:20]
                    lines.append(f"║ {switch_name} Sensors:                                                      ║")
                    for sensor in switch.sensors[:3]:  # Max 3 Sensoren pro Switch
                        sensor_name = getattr(sensor, 'sensorName', str(sensor))[:60]
                        lines.append(f"║   • {sensor_name:<75} ║")
        
        elif sensor_count > 10:
            lines.append("╠" + "─" * 80 + "╣")
            lines.append(f"║ Too many sensors to display ({sensor_count} total)                                     ║")
        
        # Footer
        lines.append("╚" + "═" * 80 + "╝")
        
        return '\n'.join(lines)

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
    
        # NEU: Suche nach dediziertem Sensor-Device für Sensor Erstellung
        clean_entitys = self.discoverRelatedSensors(entitys)
    
        self.identifySwitchesAndSensors(clean_entitys)
        self.identifyIfRunningState()
        self.identifDimmable()
        self.checkForControlValue()
        self.checkMinMax(False)
        self.identifyCapabilities()
        if(self.initialization == True):
            self.deviceUpdater()
            _LOGGER.debug(f"Device {self.deviceName} Initialization Completed")
            self.initialization = False
            self.isInitialized = True
            logging.warning(f"Device: {self.deviceName} Initialization done {self}")
        else:
            raise Exception(f"Device could not be Initialized {self.deviceName}")

    def discoverRelatedSensors(self, entitys):
        """
        Sucht nach dedizierten Sensor-Devices (temperature, humidity, dewpoint).
        Erstellt zusätzliche Sensor-Objekte und entfernt diese Entities
        aus der Rückgabe-Liste.
        """
        devices = self.dataStore.get("devices") or []
        new_sensors = []
        
        sensor_groups = {
            "temperature": [],
            "humidity": [],
            "dewpoint": [],
            "co2": []
        }

        used_entities = set()

        # Schritt 1: Gruppiere Sensor-Entities nach Typ
        for entity in entitys:
            entity_id = entity.get("entity_id", "")
            for sensor_type in sensor_groups.keys():
                if sensor_type in entity_id and entity_id.startswith("sensor."):
                    sensor_groups[sensor_type].append(entity)
                    used_entities.add(entity_id)
                    _LOGGER.debug(f"[{self.deviceName}] Found {sensor_type} entity: {entity_id}")
                    break

        # Schritt 2: Erstelle Sensor-Objekte für jede Gruppe
        for sensor_type, sensor_entities in sensor_groups.items():
            if not sensor_entities:
                continue

            sensor_name = f"{self.deviceName}_{sensor_type}"
            _LOGGER.debug(
                f"[{self.deviceName}] Creating remapped sensor '{sensor_name}' "
                f"with {len(sensor_entities)} entities"
            )

            try:
                from .Sensor import Sensor
                # Pass device's labels to remapped sensors so medium_label can be detected
                new_sensor = Sensor(
                    sensor_name,
                    sensor_entities,
                    self.eventManager,
                    self.dataStore,
                    "Sensor",
                    self.inRoom,
                    self.hass,
                    sensor_type,
                    self.labelMap,  # Propagate device labels to remapped sensors
                    reMapped=True
                )

                if new_sensor:
                    new_sensors.append(new_sensor)
                    _LOGGER.debug(
                        f"[{self.deviceName}] ✓ Remapped sensor '{sensor_name}' Label:{sensor_type} initialized successfully"
                    )
                    
                else:
                    _LOGGER.error(
                        f"[{self.deviceName}] ✗ Failed to initialize sensor '{sensor_name}' (timeout)"
                    )

            except Exception as e:
                _LOGGER.error(
                    f"[{self.deviceName}] ✗ Error creating sensor '{sensor_name}': {e}",
                    exc_info=True
                )

        # Schritt 3: Neue Sensoren speichern
        if new_sensors:
            devices.extend(new_sensors)
            self.dataStore.set("devices", devices)
            _LOGGER.info(
                f"[{self.deviceName}] Added {len(new_sensors)} remapped sensors to dataStore"
            )

        # Schritt 4: Entferne genutzte Entities aus Rückgabe
        remaining_entities = [
            e for e in entitys if e.get("entity_id", "") not in used_entities
        ]

        return remaining_entities

    async def deviceUpdate(self, updateData):
        """
        Verarbeitet Updates und synchronisiert mit WorkData.
        """
        parts = updateData["entity_id"].split(".")
        device_name = parts[1].split("_")[0] if len(parts) > 1 else "Unknown"

        if self.deviceName != device_name: 
            return

        entity_id = updateData["entity_id"]
        new_value = updateData["newValue"]

        # Helper-Funktion für Entity-Updates
        async def update_entity_value(entity_list, entity_id, new_value):
            for entity in entity_list:
                if entity.get("entity_id") == entity_id:
                    old_value = entity.get("value")
                    entity["value"] = new_value
                    _LOGGER.debug(
                        f"{self.deviceName} Updated {entity_id}: {old_value} → {new_value}"
                    )
                    return True
            return False

        # Update entsprechend der Entity-Kategorie
        updated = False
        
        if "sensor." in entity_id:
            updated = await update_entity_value(self.sensors, entity_id, new_value)
            if updated:
                _LOGGER.debug(f"{self.deviceName} Sensor updated: {entity_id}")
        
        elif any(prefix in entity_id for prefix in ["fan.", "light.", "switch.", "humidifier."]):
            updated = await update_entity_value(self.switches, entity_id, new_value)
            if updated:
                self.identifyIfRunningState()
                _LOGGER.debug(f"{self.deviceName} Switch updated: {entity_id}")
        
        elif any(prefix in entity_id for prefix in ["number.", "text.", "time.", "select.", "date."]):
            updated = await update_entity_value(self.options, entity_id, new_value)
            if updated:
                _LOGGER.debug(f"{self.deviceName} Option updated: {entity_id}")
        
        elif "ogb_" in entity_id:
            updated = await update_entity_value(self.sensors, entity_id, new_value)
            if updated:
                _LOGGER.debug(f"{self.deviceName} OGB sensor updated: {entity_id}")
            
    def checkMinMax(self,data):
        minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")

        if not self.isDimmable: 
            return

        # Guard against None
        if minMaxSets is None:
            self.is_minmax_active = False
            return

        # Check if minmax is active - used for clamping logic
        is_active = minMaxSets.get("active", False)
        self.is_minmax_active = is_active
        
        # Load device-specific values ALWAYS (like original version)
        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
            self.minVoltage = minMaxSets.get("minVoltage")
            self.maxVoltage = minMaxSets.get("maxVoltage")
            _LOGGER.debug(f"{self.deviceName}: Loaded min/max voltage: {self.minVoltage}-{self.maxVoltage}")

        if "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            self.minDuty = minMaxSets.get("minDuty")
            self.maxDuty = minMaxSets.get("maxDuty")
            _LOGGER.debug(f"{self.deviceName}: Loaded min/max duty: {self.minDuty}-{self.maxDuty}")

        # Clamp voltage to min/max range
        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
            if hasattr(self, 'voltage') and self.voltage is not None:
                old_voltage = self.voltage
                self.voltage = self.clamp_voltage(self.voltage)
                _LOGGER.info(f"{self.deviceName}: Voltage clamped from {old_voltage}% to {self.voltage}%")

        # Clamp dutyCycle to min/max range
        if "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            if hasattr(self, 'dutyCycle') and self.dutyCycle is not None:
                old_duty = self.dutyCycle
                self.dutyCycle = max(self.minDuty, min(self.maxDuty, self.dutyCycle))
                _LOGGER.info(
                    f"{self.deviceName}: DutyCycle clamped from {old_duty}% to {self.dutyCycle}% "
                    f"(range: {self.minDuty}-{self.maxDuty}%)"
                )

    def initialize_duty_cycle(self):
        """Initialisiert den Duty Cycle auf die Mitte der min/max Werte, aligned to steps."""

        def calc_middle(min_val, max_val, steps_val):
            if steps_val <= 0:
                return (min_val + max_val) // 2
            range_mid = (max_val - min_val) // 2
            steps_in_range = range_mid // steps_val
            return min_val + steps_in_range * steps_val

        # Generischer Default
        if self.minDuty is not None and self.maxDuty is not None:
            self.dutyCycle = calc_middle(self.minDuty, self.maxDuty, self.steps)
        else:
            self.dutyCycle = 50

        if self.isSpecialDevice:
            if self.minDuty is not None and self.maxDuty is not None:
                self.dutyCycle = calc_middle(self.minDuty, self.maxDuty, self.steps)
            else:
                self.dutyCycle = 50
        elif self.isAcInfinDev:
            self.steps = 10
            self.maxDuty = 100
            self.minDuty = 0
            self.dutyCycle = calc_middle(self.minDuty, self.maxDuty, self.steps)  # 50

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
                entityLabels = entity.get("labels")
                _LOGGER.debug(f"Entity {entityID} Value:{entityValue} Labels:{entityLabels} Platform:{entityPlatform}")
                
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
                    _LOGGER.debug(f"FOUND CRES-CONTROL Entity {self.deviceName} Initial value detected {entityValue} from {entity} Full-Entity-List:{entitys}")
                    self.voltageFromNumber = True
                    
                if any(x in entityPlatform for x in ["tasmota", "shelly"]):
                    _LOGGER.debug(f"FOUND Special Platform:{entityPlatform} Entity {self.deviceName} Initial value detected {entityValue} from {entity} Full-Entity-List:{entitys}")
                    self.isSpecialDevice = True

                if entityValue in ("None", "unknown", "Unbekannt", "unavailable"):
                    _LOGGER.debug(f"DEVICE {self.deviceName} Initial invalid value detected for {entityID}. ")
                    continue
                        
                if entityID.startswith(("switch.", "light.", "fan.", "climate.", "humidifier.")):
                    self.switches.append(entity)
                elif entityID.startswith(("select.", "number.","date.", "text.", "time.","camera.")):
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
        """
        Identify and register device capabilities based on device type.
        Prevents duplicate registrations - each device is only registered once per capability.
        """
        capMapping = {
            "canHeat": ["heater"],
            "canCool": ["cooler"],
            "canClimate": ["climate"],
            "canHumidify": ["humidifier"],
            "canDehumidify": ["dehumidifier"],
            "canVentilate": ["ventilation"],
            "canExhaust": ["exhaust"],
            "canIntake": ["intake"],
            "canLight": ["light"],
            "canCO2": ["co2"],
            "canPump": ["pump"],
        }

        # Skip OGB internal devices
        if self.deviceName == "ogb":
            return

        # Initialize capabilities in dataStore if not present
        if not self.dataStore.get("capabilities"):
            self.dataStore.setDeep("capabilities", {
                cap: {"state": False, "count": 0, "devEntities": []} for cap in capMapping
            })

        # Find matching capability for this device type
        for cap, deviceTypes in capMapping.items():
            if self.deviceType.lower() in (dt.lower() for dt in deviceTypes):
                capPath = f"capabilities.{cap}"
                currentCap = self.dataStore.getDeep(capPath)

                # CRITICAL: Check if device is already registered to prevent duplicates
                if self.deviceName in currentCap["devEntities"]:
                    _LOGGER.debug(f"{self.deviceName}: Already registered for capability {cap}, skipping")
                    continue

                # Register this device for the capability
                if not currentCap["state"]:
                    currentCap["state"] = True
                currentCap["count"] += 1
                currentCap["devEntities"].append(self.deviceName)
                
                # Write updated data back to dataStore
                self.dataStore.setDeep(capPath, currentCap)
                _LOGGER.debug(f"{self.deviceName}: Registered for capability {cap} (count: {currentCap['count']})")

        # Log final capabilities state
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
                elif option_value == "Schedule":
                    self.isRunning = False
                    _LOGGER.warning("AC-INFINTY RUNNING OVER OWN CONTROLLER")
                    return
                elif option_value in (None, "unknown", "Unbekannt", "unavailable"):
                    # Handle unavailable/unknown states gracefully - don't raise, just log and set to None
                    _LOGGER.debug(f"{self.inRoom} - Entity state '{option_value}' for {self.deviceName} - treating as unavailable")
                    self.isRunning = None
                    return
                else:
                    _LOGGER.warning(f"{self.inRoom} - Unexpected Entity state '{option_value}' for {self.deviceName}")
                    self.isRunning = None
                    return   
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
                    # Handle unavailable/unknown states gracefully - don't raise, just log and set to None
                    _LOGGER.debug(f"{self.inRoom} - Switch state '{switch_value}' for {self.deviceName} - treating as unavailable")
                    self.isRunning = None
                    return
                else:
                    _LOGGER.warning(f"{self.inRoom} - Unexpected Switch state '{switch_value}' for {self.deviceName}")
                    self.isRunning = None
                    return

    # Überprüfe, ob das Gerät dimmbar ist
    def identifDimmable(self):
        allowedDeviceTypes = ["ventilation", "exhaust","intake","light","lightfarred","lightuv","lightblue","lightred","humdifier","dehumidifier","heater","cooler","co2"]

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
        # Skip if we're actively controlling the device (e.g., turn_on just ran)
        if getattr(self, '_in_active_control', False):
            _LOGGER.debug(f"{self.deviceName}: Skipping checkForControlValue - device is under active control")
            return
        
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
                    # Always clamp voltage if minVoltage or maxVoltage are set
                    if hasattr(self, 'minVoltage') and hasattr(self, 'maxVoltage') and self.minVoltage is not None and self.maxVoltage is not None:
                        if self.minVoltage > 0 or self.maxVoltage < 100:
                            old_voltage = self.voltage
                            self.voltage = self.clamp_voltage(self.voltage)
                            _LOGGER.debug(f"{self.deviceName}: Voltage clamped from {old_voltage}% to {self.voltage}%.")
                elif self.deviceType in ["Exhaust", "Intake", "Ventilation", "Humidifier", "Dehumidifier"]:
                    self.dutyCycle = converted_value
                    _LOGGER.debug(f"{self.deviceName}: Duty Cycle from Sensor updated to {self.dutyCycle}%.")
                    # Always clamp dutyCycle if minDuty and maxDuty are set
                    if hasattr(self, 'minDuty') and hasattr(self, 'maxDuty') and self.minDuty is not None and self.maxDuty is not None:
                        if self.minDuty > 0 or self.maxDuty < 100:
                            old_duty = self.dutyCycle
                            self.dutyCycle = max(self.minDuty, min(self.maxDuty, self.dutyCycle))
                            _LOGGER.debug(f"{self.deviceName}: Duty Cycle clamped from {old_duty}% to {self.dutyCycle}%.")

        # Options durchgehen
        for option in self.options:
            _LOGGER.debug(f"Prüfe Option: {option}")
            
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
                        if self.is_minmax_active and hasattr(self, 'minVoltage') and hasattr(self, 'maxVoltage') and self.minVoltage is not None and self.maxVoltage is not None:
                            self.voltage = self.clamp_voltage(self.voltage)
                            _LOGGER.debug(f"{self.deviceName}: Voltage clamped to {self.voltage}%.")
                        return
                else:
                    # Für alle anderen Gerätetypen
                    converted_value = convert_to_int(raw_value, multiply_by_10=self.isAcInfinDev)
                    
                    if converted_value is not None:
                        self.dutyCycle = converted_value
                        _LOGGER.debug(f"{self.deviceName}: Duty Cycle set from Options to {self.dutyCycle}%.")
                        if self.is_minmax_active and hasattr(self, 'minDuty') and hasattr(self, 'maxDuty') and self.minDuty is not None and self.maxDuty is not None:
                            self.dutyCycle = max(self.minDuty, min(self.maxDuty, self.dutyCycle))
                            _LOGGER.debug(f"{self.deviceName}: Duty Cycle clamped to {self.dutyCycle}%.")
                        return
                
    def _is_device_online(self) -> bool:
        """Check if the device entity is available (not 'unavailable' or 'unknown').
        
        Returns True if:
        - Device has no switches (no entity to check)
        - All switch entities have a valid state (not unavailable/unknown)
        
        Returns False if any switch entity is offline.
        """
        if not self.switches:
            return True  # No switches to check
        
        for switch in self.switches:
            entity_id = switch.get("entity_id")
            if entity_id and self.hass:
                state = self.hass.states.get(entity_id)
                if state:
                    if state.state in ("unavailable", "unknown", "None"):
                        _LOGGER.debug(f"{self.deviceName}: Entity {entity_id} is {state.state}, device considered offline")
                        return False
        return True

    async def turn_on(self, **kwargs):
        """Schaltet das Gerät ein."""
        import time
        
        # Flag to prevent sensor from overwriting our control value
        self._in_active_control = True
        
        try:
            # Check if device is online before proceeding
            if not self._is_device_online():
                _LOGGER.warning(f"{self.deviceName}: Cannot turn on - device is offline/unavailable")
                self._in_active_control = False
                return
            
            # Rate limiting for all devices to prevent rapid successive calls
            # Prevents device timeout and improves system stability
            now = time.time()
            last_call = getattr(self, '_last_turn_on_time', 0)
            
            # 3 second cooldown for all turn_on calls
            if now - last_call < 3.0:
                _LOGGER.debug(f"{self.deviceName}: turn_on skipped - too rapid ({now - last_call:.2f}s since last call)")
                return
            
            self._last_turn_on_time = now
            
            brightness_pct = kwargs.get("brightness_pct")
            percentage = kwargs.get("percentage")
            
            # Validate and convert brightness_pct to float (default to 100 if None)
            _LOGGER.debug(f"{self.deviceName}: turn_on called with brightness_pct={brightness_pct}, type={type(brightness_pct)}")
            if brightness_pct is not None:
                # Handle list case first
                if isinstance(brightness_pct, list):
                    brightness_pct = brightness_pct[0] if brightness_pct else 100
                try:
                    brightness_pct = float(brightness_pct)
                    # Clamp to valid range
                    brightness_pct = max(0, min(100, brightness_pct))
                except (ValueError, TypeError):
                    _LOGGER.error(f"{self.deviceName}: Invalid brightness_pct value: {brightness_pct}, using device voltage")
                    brightness_pct = getattr(self, 'voltage', 100)
            else:
                # Default: For lights, use current voltage instead of 100%
                if self.deviceType in ["Light", "LightFarRed", "LightUV", "LightBlue", "LightRed"] and hasattr(self, 'voltage') and self.voltage is not None:
                    brightness_pct = self.voltage
                    _LOGGER.debug(f"{self.deviceName}: Using current voltage {brightness_pct}% for turn_on")
                # For special exhausts (light type entities), use current dutyCycle
                elif self.isSpecialDevice and hasattr(self, 'dutyCycle') and self.dutyCycle is not None:
                    brightness_pct = self.dutyCycle
                    _LOGGER.debug(f"{self.deviceName}: Using current dutyCycle {brightness_pct}% for turn_on")
                else:
                    brightness_pct = 100.0
            _LOGGER.debug(f"{self.deviceName}: turn_on processed brightness_pct={brightness_pct}")
            
            # Validate and convert percentage to float (default to 100 if None)
            if percentage is not None:
                try:
                    percentage = float(percentage)
                except (ValueError, TypeError):
                    _LOGGER.error(f"{self.deviceName}: Invalid percentage value: {percentage}, using device dutyCycle")
                    percentage = getattr(self, 'dutyCycle', 50)
            else:
                # Default: For exhaust/intake/ventilation, use current dutyCycle instead of 100%
                if self.deviceType in {"Exhaust", "Intake", "Ventilation"} and hasattr(self, 'dutyCycle') and self.dutyCycle is not None:
                    percentage = self.dutyCycle
                    _LOGGER.debug(f"{self.deviceName}: Using current dutyCycle {percentage}% for turn_on")
                else:
                    percentage = 100.0

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
                    _LOGGER.debug(f"{self.deviceName} ON ACTION with ID {entity_id}")
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
                                self.isRunning = True  
                                return                    
                        else:
                            if percentage is not None:
                                _LOGGER.warning(f"{self.deviceName}: set value to {percentage}")
                                await self.set_value(percentage/10)
                                self.isRunning = True
                                return

            # === Standardgeräte ===
            if not self.switches:
                _LOGGER.warning(f"{self.deviceName} has not Switch to Activate or Turn On")
                return

            entity_ids = [switch["entity_id"] for switch in self.switches]

            for entity_id in entity_ids:
                # Validate and fix entity_id if it's a list
                _LOGGER.debug(f"{self.deviceName}: Processing entity_id={entity_id}, type={type(entity_id)}")
                if isinstance(entity_id, list):
                    entity_id = entity_id[0] if entity_id else "unknown"
                if not isinstance(entity_id, str):
                    entity_id = str(entity_id)
                _LOGGER.debug(f"{self.deviceName}: Using entity_id={entity_id}")

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
                    if hasattr(self, 'realHumidifierClass') and self.realHumidifierClass:
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

                # Light einschalten (alle Light device types)
                elif self.deviceType in ["Light", "LightFarRed", "LightUV", "LightBlue", "LightRed"]:
                    if self.isDimmable:
                        # Prüfe voltageFromNumber Pfad (wie im Original)
                        if self.voltageFromNumber:
                            # Original Pfad für Tuya-Geräte: switch + set_value
                            await self.hass.services.async_call(
                                domain="switch",
                                service="turn_on",
                                service_data={"entity_id": entity_id},
                            )
                            await self.set_value(float(brightness_pct/10))
                            self.isRunning = True
                            _LOGGER.debug(f"{self.deviceName}: Light ON (via Number).")
                            return
                        else:
                            # Standard Pfad: light.turn_on mit brightness_pct (0-100)
                            if isinstance(brightness_pct, list):
                                brightness_pct = brightness_pct[0] if brightness_pct else 100
                            brightness_pct = max(0, min(100, float(brightness_pct)))
                            brightness_pct = int(brightness_pct)
                            _LOGGER.debug(f"{self.deviceName}: Calling HA light.turn_on with entity_id={entity_id}, brightness_pct={brightness_pct}")
                            await self.hass.services.async_call(
                                domain="light",
                                service="turn_on",
                                service_data={
                                    "entity_id": entity_id,
                                    "brightness_pct": brightness_pct,
                                },
                            )
                            self.isRunning = True
                            _LOGGER.debug(f"{self.deviceName}: {self.deviceType} ON ({brightness_pct}%).")
                            return
                    else:
                        # Nicht-dimmable Lichter
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = True
                        _LOGGER.debug(f"{self.deviceName}: {self.deviceType} ON (non-dimmable).")
                        return

                # Exhaust einschalten
                elif self.deviceType == "Exhaust":
                    if self.isSpecialDevice:
                        if self.isDimmable:
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
                        else:
                            await self.hass.services.async_call(
                                domain="switch",
                                service="turn_on",
                                service_data={"entity_id": entity_id},
                            )
                            self.isRunning = True
                            _LOGGER.debug(f"{self.deviceName}: Exhaust ON (Switch).")
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
                    if self.isSpecialDevice:
                        if self.isDimmable:
                            await self.hass.services.async_call(
                                domain="light",
                                service="turn_on",
                                service_data={
                                    "entity_id": entity_id,
                                    "brightness_pct": brightness_pct,
                                },
                            )
                            self.isRunning = True
                            _LOGGER.debug(f"{self.deviceName}: Intake ON ({brightness_pct}%).")
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
                    if self.isSpecialDevice:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_on",
                            service_data={
                                "entity_id": entity_id,
                                "brightness_pct": brightness_pct,
                            },
                        )
                    elif self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="set_percentage",
                            service_data={
                                "entity_id": entity_id,
                                "percentage": percentage,
                            },
                        )
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_on",
                            service_data={"entity_id": entity_id},
                        )

                    # Set state and log once after ALL ventilation entities are processed
                    self.isRunning = True
                    _LOGGER.debug(f"{self.deviceName}: Ventilation ON - {len(self.switches)} entities activated.")

                # CO2 einschalten
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
                        _LOGGER.warning(f"{self.deviceName}: CO2 ON ({percentage}%).")
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
        finally:
            self._in_active_control = False

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
                    _LOGGER.debug(f"{self.deviceName} OFF ACTION with ID {entity_id}")
                    await self.hass.services.async_call(
                        domain="select",
                        service="select_option",
                        service_data={
                            "entity_id": entity_id,
                            "option": "Off"
                        },
                    )
                    self.isRunning = False
                    # Zusatzaktionen je nach Gerätetyp
                    if self.deviceType in ["Light", "Humidifier","Exhaust","Ventilation"]:
                        await self.hass.services.async_call(
                            domain="number",
                            service="set_value",
                            service_data={
                                "entity_id": entity_id,
                                "value": 0  # Use 0 to fully turn off AcInfinity devices
                            },
                        )
                        self.isRunning = False
                    _LOGGER.debug(f"{self.deviceName}: AcInfinity über select OFF.")
                return

            # === Standardgeräte ===
            if not self.switches:
                _LOGGER.debug(f"{self.deviceName} has NO Switches to Turn OFF")
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
                        # For dimmable lights, use brightness_pct=0 to turn off
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                        self.isRunning = False
                        # Reset voltage to 0 for dimmable lights
                        self.voltage = 0
                        _LOGGER.debug(f"{self.deviceName}: Light OFF (dimmable).")
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
                    if self.isSpecialDevice:
                        await self.hass.services.async_call(
                            domain="light",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                    elif self.isDimmable:
                        await self.hass.services.async_call(
                            domain="fan",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )
                    else:
                        await self.hass.services.async_call(
                            domain="switch",
                            service="turn_off",
                            service_data={"entity_id": entity_id},
                        )

                    # Set state and log once after ALL ventilation entities are processed
                    self.isRunning = False
                    _LOGGER.debug(f"{self.deviceName}: Ventilation OFF - {len(self.switches)} entities deactivated.")
                        
                # CO2 ausschalten
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
            _LOGGER.debug(f"{self.deviceName} unterstützt keine numerischen Werte.")
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

        _LOGGER.warning(f"{self.deviceName} hat keine passende Option mit 'duty' oder 'voltage' in der entity_id.")

    async def set_mode(self, mode):
        """Setzt den Mode des Geräts, falls unterstützt."""
        if not self.options:
            _LOGGER.warning(f"{self.deviceName} unterstützt keine Modi.")
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
    async def WorkMode(self, workmode):
        # Special light types that should NOT respond to WorkMode automatic activation
        # These lights have their own dedicated scheduling logic
        special_light_types = {
            "LightFarRed", "LightUV", "LightBlue", "LightRed", "LightSpectrum"
        }

        # For lights, don't activate workmode if light is off
        if hasattr(self, 'islightON') and not self.islightON:
            # Special lights should not save pending workmode - they control themselves
            if self.deviceType in special_light_types:
                _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring WorkMode, using dedicated scheduling")
                return
            self.pendingWorkMode = workmode
            _LOGGER.info(f"{self.deviceName}: WorkMode {workmode} saved, will activate when light turns on")
            return
        self.inWorkMode = workmode
        if self.inWorkMode:
            if self.isDimmable:
                if self.deviceType == "Light":
                    if hasattr(self, 'sunPhaseActive') and self.sunPhaseActive:
                        await self.eventManager.emit("pauseSunPhase", False)
                        return
                    # Use minVoltage if min/max is active, otherwise initVoltage
                    if hasattr(self, 'minVoltage') and hasattr(self, 'maxVoltage') and self.minVoltage is not None and self.maxVoltage is not None:
                        self.voltage = self.minVoltage
                    else:
                        self.voltage = self.initVoltage
                    await self.turn_on(brightness_pct=self.voltage)
                # Special lights should not respond to WorkMode - they use their own scheduling
                elif self.deviceType in special_light_types:
                    _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring WorkMode, using dedicated scheduling")
                    return
                else:
                    self.dutyCycle = self.minDuty
                    if self.isSpecialDevice:
                        await self.turn_on(brightness_pct=int(float(self.minDuty)))
                    await self.turn_on(percentage=int(float(self.minDuty)))
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
                    if hasattr(self, 'sunPhaseActive') and self.sunPhaseActive:
                        await self.eventManager.emit("resumeSunPhase", False)
                        return
                    self.voltage = self.maxVoltage
                    # Return to normal operation: turn on if device was running
                    if self.isRunning:
                        await self.turn_on(brightness_pct=self.maxVoltage)
                else:
                    self.dutyCycle = self.maxDuty
                    # Return to normal operation: turn on if device was running
                    if self.isRunning:
                        if self.isSpecialDevice:
                            await self.turn_on(brightness_pct=int(float(self.maxDuty)))
                        await self.turn_on(percentage=int(float(self.maxDuty)))
            else:
                if self.deviceType == "Light":
                    return
                if self.deviceType == "Pump":
                    return
                if self.deviceType == "Sensor":
                    return
                else:
                    # Return to normal operation: turn on if device was running
                    if self.isRunning:
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
        if hasattr(self, 'sunPhaseActive') and self.sunPhaseActive:
            _LOGGER.info(f"{self.deviceName}: Cannot change min/max during active sunphase")
            return

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
        
    async def changeMinMaxValues(self, newValue):
        if self.isDimmable:
            _LOGGER.debug(f"{self.deviceName}: as Type:{self.deviceType} NewValue: {newValue}")
    
            if self.deviceType == "Light":
                if self.isDimmable:
                    self.voltage = newValue
                    await self.turn_on(brightness_pct=newValue)
            else:
                self.dutyCycle = newValue
                if self.isSpecialDevice:
                    await self.turn_on(brightness_pct=float(newValue))
                else:
                    await self.turn_on(percentage=newValue)

    async def on_minmax_control_disabled(self, data):
        """Reset min/max to defaults when global minMaxControl is disabled.
        
        Only applies to: Light, Exhaust, Intake, Ventilation
        
        Behavior:
        - Light: Uses plant stage-based min/max from PlantStageMinMax (Light.py)
        - Exhaust/Intake/Ventilation: Uses class-defined default values (0-100)
        
        IMPORTANT: Only updates running devices. Does NOT turn on devices that are off.
        """
        # Only handle for specific device types
        minmax_device_types = {"Light", "Exhaust", "Intake", "Ventilation"}
        if self.deviceType not in minmax_device_types:
            _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring MinMaxControlDisabled")
            return
        
        _LOGGER.info(f"{self.deviceName}: MinMax control disabled - resetting to default values")
        
        if self.deviceType == "Light":
            # For Light devices, use plant stage-based min/max from PlantStageMinMax
            plant_stage = self.data_store.get("plantStage") or "LateFlower"
            
            # Import PlantStageMinMax from Light class if available
            if hasattr(self, 'PlantStageMinMax') and plant_stage in self.PlantStageMinMax:
                stage_minmax = self.PlantStageMinMax[plant_stage]
                old_min = self.minVoltage
                old_max = self.maxVoltage
                self.minVoltage = stage_minmax["min"]
                self.maxVoltage = stage_minmax["max"]
                _LOGGER.info(
                    f"{self.deviceName}: Using {plant_stage} min/max: "
                    f"min={old_min}→{self.minVoltage}%, max={old_max}→{self.maxVoltage}%"
                )
            else:
                # Fallback: use initVoltage and 100
                self.minVoltage = getattr(self, 'initVoltage', 20)
                self.maxVoltage = 100
                _LOGGER.warning(
                    f"{self.deviceName}: No PlantStageMinMax found for '{plant_stage}', "
                    f"using fallback: min={self.minVoltage}%, max={self.maxVoltage}%"
                )
            
            # Only update running devices - don't turn on devices that are off
            if self.isRunning and hasattr(self, 'voltage') and self.voltage is not None:
                old_voltage = self.voltage
                # Un-clamp: reset to initVoltage
                self.voltage = self.initVoltage
                _LOGGER.info(f"{self.deviceName}: Running - voltage reset from {old_voltage}% to {self.voltage}%")
                await self.turn_on(brightness_pct=self.voltage)
            else:
                _LOGGER.info(f"{self.deviceName}: Not running - min/max reset, voltage unchanged at {getattr(self, 'voltage', 'N/A')}%")
        
        elif self.deviceType in {"Exhaust", "Intake", "Ventilation"}:
            old_min = self.minDuty
            old_max = self.maxDuty
            
            # Check if device-specific values exist in data store
            minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
            if minMaxSets and minMaxSets.get("active"):
                # User has set device-specific values - preserve them
                self.minDuty = minMaxSets.get("minDuty", old_min)
                self.maxDuty = minMaxSets.get("maxDuty", old_max)
                _LOGGER.info(
                    f"{self.deviceName}: Device-specific min/max active, preserving values: "
                    f"min={self.minDuty}, max={self.maxDuty}"
                )
            else:
                # No device-specific values - use class defaults
                self.minDuty = getattr(self, 'minDuty', 0)
                self.maxDuty = getattr(self, 'maxDuty', 100)
                _LOGGER.info(
                    f"{self.deviceName}: Resetting min/max to defaults: "
                    f"min={old_min}→{self.minDuty}, max={old_max}→{self.maxDuty}"
                )
            
            # Only update running devices - don't turn on devices that are off
            if self.isRunning and hasattr(self, 'dutyCycle') and self.dutyCycle is not None:
                old_duty = self.dutyCycle
                # Calculate midpoint of new range
                midpoint = self.minDuty + ((self.maxDuty - self.minDuty) // 2 // self.steps) * self.steps
                self.dutyCycle = midpoint
                _LOGGER.info(f"{self.deviceName}: Running - dutyCycle reset from {old_duty}% to {self.dutyCycle}%")
                if self.isSpecialDevice:
                    await self.turn_on(brightness_pct=float(self.dutyCycle))
                else:
                    await self.turn_on(percentage=self.dutyCycle)
            else:
                _LOGGER.info(f"{self.deviceName}: Not running - min/max reset, dutyCycle unchanged at {getattr(self, 'dutyCycle', 'N/A')}%")

    async def on_minmax_control_enabled(self, data):
        """Restore user-defined min/max values when global minMaxControl is enabled.
        
        Only applies to: Light, Exhaust, Intake, Ventilation
        
        Behavior:
        - Reads device-specific min/max from dataStore DeviceMinMax.{deviceType}
        - Clamps current voltage/dutyCycle to valid range
        - Updates running device with clamped value
        
        IMPORTANT: Only updates running devices. Does NOT turn on devices that are off.
        """
        minmax_device_types = {"Light", "Exhaust", "Intake", "Ventilation"}
        if self.deviceType not in minmax_device_types:
            _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring MinMaxControlEnabled")
            return
        
        _LOGGER.info(f"{self.deviceName}: MinMax control enabled - restoring user-defined min/max values")
        
        minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
        
        if self.deviceType == "Light":
            if minMaxSets and minMaxSets.get("active", False):
                if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
                    old_min = self.minVoltage
                    old_max = self.maxVoltage
                    self.minVoltage = float(minMaxSets.get("minVoltage"))
                    self.maxVoltage = float(minMaxSets.get("maxVoltage"))
                    _LOGGER.info(
                        f"{self.deviceName}: Restored min/max: "
                        f"min={old_min}→{self.minVoltage}%, max={old_max}→{self.maxVoltage}%"
                    )
                    
                    if self.isRunning and hasattr(self, 'voltage') and self.voltage is not None:
                        old_voltage = self.voltage
                        self.voltage = self.clamp_voltage(self.voltage)
                        _LOGGER.info(f"{self.deviceName}: Voltage clamped from {old_voltage}% to {self.voltage}%")
                        await self.turn_on(brightness_pct=self.voltage)
                else:
                    _LOGGER.warning(f"{self.deviceName}: No min/max values found in dataStore")
            else:
                _LOGGER.info(f"{self.deviceName}: Device-specific minmax not active, using defaults")
        
        elif self.deviceType in {"Exhaust", "Intake", "Ventilation"}:
            if minMaxSets and minMaxSets.get("active", False):
                if "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
                    old_min = self.minDuty
                    old_max = self.maxDuty
                    self.minDuty = float(minMaxSets.get("minDuty"))
                    self.maxDuty = float(minMaxSets.get("maxDuty"))
                    _LOGGER.info(
                        f"{self.deviceName}: Restored min/max: "
                        f"min={old_min}→{self.minDuty}, max={old_max}→{self.maxDuty}"
                    )
                    
                    if self.isRunning and hasattr(self, 'dutyCycle') and self.dutyCycle is not None:
                        old_duty = self.dutyCycle
                        self.dutyCycle = max(self.minDuty, min(self.maxDuty, self.dutyCycle))
                        _LOGGER.info(f"{self.deviceName}: DutyCycle clamped from {old_duty}% to {self.dutyCycle}%")
                        if self.isSpecialDevice:
                            await self.turn_on(brightness_pct=float(self.dutyCycle))
                        else:
                            await self.turn_on(percentage=self.dutyCycle)
                else:
                    _LOGGER.warning(f"{self.deviceName}: No min/max values found in dataStore")
            else:
                _LOGGER.info(f"{self.deviceName}: Device-specific minmax not active, using defaults")

    def clamp_voltage(self, value):
        """Clamp voltage to min/max range."""
        if self.minVoltage is not None and self.maxVoltage is not None:
            return max(self.minVoltage, min(self.maxVoltage, value or 0))
        return value

    def clamp_duty_cycle(self, value):
        """Clamp duty cycle to min/max range."""
        if value is None:
            return 50  # Default fallback
        
        min_duty = float(self.minDuty) if self.minDuty is not None else 0
        max_duty = float(self.maxDuty) if self.maxDuty is not None else 100
        
        clamped = max(min_duty, min(max_duty, value))
        return int(clamped)

    async def changeMinMaxValues(self,newValue):
        if self.isDimmable:
            
            _LOGGER.debug(f"{self.deviceName}:as Type:{self.deviceType} NewValue: {newValue}")
    
            if self.deviceType == "Light":
                if self.isDimmable:
                    # Clamp to min/max range
                    clamped_value = self.clamp_voltage(newValue)
                    self.voltage = clamped_value
                    _LOGGER.info(f"{self.deviceName}: Voltage set to {clamped_value}% (was {newValue}%)")
                    await self.turn_on(brightness_pct=clamped_value)
            else:
                # Clamp to min/max range for duty cycle devices
                clamped_value = max(self.minDuty, min(self.maxDuty, newValue))
                self.dutyCycle = clamped_value
                _LOGGER.info(f"{self.deviceName}: DutyCycle set to {clamped_value}% (was {newValue}%)")
                if self.isSpecialDevice:
                    await self.turn_on(brightness_pct=float(clamped_value))
                else:
                    await self.turn_on(percentage=clamped_value)
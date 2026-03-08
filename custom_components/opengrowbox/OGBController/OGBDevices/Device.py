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
        self.eventManager.on("SetDeviceMinMax", self.DeviceSetMinMax)
        self.eventManager.on("WorkModeChange", self.WorkMode)
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
        clean_entitys = self.discoverRelatedSensors(entitys)
        self.identifySwitchesAndSensors(clean_entitys)
        self.identifyIfRunningState()
        self.identifDimmable()
        self.checkMinMax("Init")        
        self.checkForControlValue()     
        self.identifyCapabilities()
        if(self.initialization == True):
            self.initialization = False
            self.isInitialized = True
            logging.warning(f"Device: {self.deviceName} Initialization done {self}")
            self.deviceUpdater()
            asyncio.create_task(
                self.eventManager.emit(
                    "DeviceInitialized",
                    {
                        "entity_id": f"device.{self.deviceName}",
                        "device_type": self.deviceType,
                        "device_name": self.deviceName,
                        "context": getattr(self, 'deviceLabel', 'unknown'),
                        "room": self.inRoom,
                    },
                )
            )
        else:
            raise Exception(f"Device could not be Initialized {self.deviceName}")

    def discoverRelatedSensors(self, entitys):
        """
        Sucht nach dedizierten Sensor-Devices (temperature, humidity, dewpoint, co2).
        duty und intensity bleiben beim Gerät – werden NICHT als separate Sensoren erstellt.
        """
        devices = self.dataStore.get("devices") or []
        new_sensors = []
        
        sensor_groups = {
            "temperature": [],
            "humidity": [],
            "dewpoint": [],
            "co2": [],
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

            # Duplikat-Check – existiert dieser Sensor bereits im dataStore?
            already_exists = any(
                getattr(d, "deviceName", None) == sensor_name
                for d in devices
            )
            if already_exists:
                _LOGGER.debug(f"[{self.deviceName}] Sensor '{sensor_name}' already exists – skipping")
                for entity in sensor_entities:
                    used_entities.add(entity.get("entity_id", ""))
                continue

            _LOGGER.debug(
                f"[{self.deviceName}] Creating remapped sensor '{sensor_name}' "
                f"with {len(sensor_entities)} entities"
            )

            try:
                from .Sensor import Sensor
                new_sensor = Sensor(
                    sensor_name,
                    sensor_entities,
                    self.eventManager,
                    self.dataStore,
                    "Sensor",
                    self.inRoom,
                    self.hass,
                    sensor_type,
                    self.labelMap,
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
            devices = self.dataStore.get("devices") or []
            devices.extend(new_sensors)
            self.dataStore.set("devices", devices)
            _LOGGER.info(
                f"[{self.deviceName}] Added {len(new_sensors)} remapped sensors to dataStore"
            )

        # Schritt 4: Entferne genutzte Entities aus Rückgabe – duty/intensity bleiben drin
        remaining_entities = [
            e for e in entitys if e.get("entity_id", "") not in used_entities
        ]

        return remaining_entities

    def checkMinMax(self, data) -> None:
        if not self.isDimmable:
            return

        try:
            minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
        except AttributeError:
            _LOGGER.warning(f"{self.deviceName}: dataStore nicht verfügbar in checkMinMax")
            self.is_minmax_active = False
            return

        if not isinstance(minMaxSets, dict):
            self.is_minmax_active = False
            return

        # is_active sicher lesen
        self.is_minmax_active = bool(minMaxSets.get("active", False))


        # Voltage laden
        raw_min_v = minMaxSets.get("minVoltage")
        raw_max_v = minMaxSets.get("maxVoltage")
        if raw_min_v is not None and raw_max_v is not None:
            try:
                self.minVoltage = float(raw_min_v)
                self.maxVoltage = float(raw_max_v)
                _LOGGER.debug(f"{self.deviceName}: Loaded min/max voltage: {self.minVoltage}-{self.maxVoltage}")
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: Ungültige Voltage-Werte: {raw_min_v}, {raw_max_v}")

        # Duty laden
        raw_min_d = minMaxSets.get("minDuty")
        raw_max_d = minMaxSets.get("maxDuty")
        if raw_min_d is not None and raw_max_d is not None:
            try:
                self.minDuty = float(raw_min_d)
                self.maxDuty = float(raw_max_d)
                _LOGGER.debug(f"{self.deviceName}: Loaded min/max duty: {self.minDuty}-{self.maxDuty}")
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: Ungültige Duty-Werte: {raw_min_d}, {raw_max_d}")

        # Voltage clampen - ONLY if actually changed
        if hasattr(self, 'voltage') and self.voltage is not None and self.minVoltage is not None and self.maxVoltage is not None:
            try:
                old_voltage = self.voltage
                # Only clamp if voltage is outside bounds
                if old_voltage < self.minVoltage or old_voltage > self.maxVoltage:
                    self.voltage = self.clamp_voltage(self.voltage)
                    if old_voltage != self.voltage:
                        _LOGGER.info(f"{self.deviceName}: Voltage clamped from {old_voltage}% to {self.voltage}% (range: {self.minVoltage}-{self.maxVoltage}%)")
                    else:
                        _LOGGER.debug(f"{self.deviceName}: Voltage {old_voltage}% already within bounds {self.minVoltage}-{self.maxVoltage}%")
                else:
                    _LOGGER.debug(f"{self.deviceName}: Voltage {old_voltage}% preserved (within bounds {self.minVoltage}-{self.maxVoltage}%)")
            except Exception as e:
                _LOGGER.warning(f"{self.deviceName}: Fehler beim Voltage-Clamping: {e}")

        # DutyCycle clampen - ONLY if actually changed
        if hasattr(self, 'dutyCycle') and self.dutyCycle is not None and self.minDuty is not None and self.maxDuty is not None:
            try:
                old_duty = self.dutyCycle
                # Only clamp if duty cycle is outside bounds
                if old_duty < self.minDuty or old_duty > self.maxDuty:
                    self.dutyCycle = self.clamp_duty_cycle(self.dutyCycle)
                    if old_duty != self.dutyCycle:
                        _LOGGER.info(
                            f"{self.deviceName}: DutyCycle clamped from {old_duty}% to {self.dutyCycle}% "
                            f"(range: {self.minDuty}-{self.maxDuty}%)"
                        )
                    else:
                        _LOGGER.debug(f"{self.deviceName}: DutyCycle {old_duty}% already within bounds {self.minDuty}-{self.maxDuty}%")
                else:
                    _LOGGER.debug(f"{self.deviceName}: DutyCycle {old_duty}% preserved (within bounds {self.minDuty}-{self.maxDuty}%)")
            except Exception as e:
                _LOGGER.warning(f"{self.deviceName}: Fehler beim DutyCycle-Clamping: {e}")

    def is_tent_mode_disabled(self) -> bool:
        """Check if tent mode is disabled in datastore."""
        try:
            tent_mode = self.dataStore.getDeep("tentMode")
            return tent_mode == "Disabled"
        except (AttributeError, TypeError):
            _LOGGER.debug(f"{self.deviceName}: Could not check tentMode, assuming enabled")
            return False

    async def safe_turn_on(self, **kwargs):
        """Only turn on device if tent mode is not disabled."""
        if self.is_tent_mode_disabled():
            _LOGGER.debug(f"{self.deviceName}: Tent mode disabled - setting values but not turning on device")
            # Just set the values but don't actually turn on
            return False
        else:
            return await self.turn_on(**kwargs)

    def initialize_duty_cycle(self) -> None:
        """Initialisiert den Duty Cycle auf die Mitte der min/max Werte, aligned to steps."""

        def to_float(val, default: float) -> float:
            if val is None:
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        def calc_middle(min_val: float, max_val: float, steps_val: float) -> int:
            try:
                steps_val = float(steps_val) if steps_val is not None else 0.0
                min_val, max_val = float(min_val), float(max_val)
                if steps_val <= 0:
                    return int((min_val + max_val) // 2)
                range_mid = (max_val - min_val) / 2
                steps_in_range = int(range_mid // steps_val)
                return int(min_val + steps_in_range * steps_val)
            except (ValueError, TypeError) as e:
                _LOGGER.warning(f"{self.deviceName}: calc_middle Fehler ({e}), fallback 50")
                return 50

        min_duty = to_float(self.minDuty, None)
        max_duty = to_float(self.maxDuty, None)
        steps    = to_float(self.steps, 0.0)

        if self.isAcInfinDev:
            # Feste Werte für AcInfin
            self.steps    = 10
            self.minDuty  = 0.0
            self.maxDuty  = 100.0
            self.dutyCycle = calc_middle(0.0, 100.0, 10.0)  # → 50

        elif min_duty is not None and max_duty is not None:
            # isSpecialDevice + Generischer Fall – identische Logik, kein Duplikat nötig
            self.dutyCycle = calc_middle(min_duty, max_duty, steps)

        else:
            self.dutyCycle = 50

        _LOGGER.debug(f"{self.deviceName}: Duty Cycle Init to {self.dutyCycle}%.")

    # Eval sensor if Intressted in 
    def evalSensors(self, sensor_id: str) -> bool:
        interested_mapping = ("_temperature", "_humidity", "_dewpoint", "_co2","_duty","_moisture","_intensity","_ph","_ec","_tds",'_conductivity')
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

    def checkForControlValue(self, force_update: bool = False) -> None:
        if getattr(self, '_in_active_control', False) and not force_update:
            _LOGGER.warning(f"{self.deviceName}: Skipping checkForControlValue - device is under active control")
            return

        if not self.isDimmable:
            _LOGGER.debug(f"{self.deviceName}: is not Dimmable")
            return

        if not self.sensors and not self.options:
            _LOGGER.debug(f"{self.deviceName}: NO Sensor data or Options found")
            self._set_default_control_values()
            return

        relevant_keys = ["_duty", "_intensity", "_dutyCycle"]
        duty_types    = {"Exhaust", "Intake", "Ventilation", "Humidifier", "Dehumidifier"}

        def convert_to_int(value, multiply_by_10: bool = False) -> int | None:
            try:
                result = float(value)
                if multiply_by_10:
                    result *= 10
                return int(result)
            except (ValueError, TypeError) as e:
                _LOGGER.error(f"{self.deviceName}: Konvertierungsfehler für Wert '{value}': {e}")
                return None

        def update_sensor_value_in_list(new_value) -> None:
            for s in self.sensors:
                if any(key in s.get("entity_id", "").lower() for key in relevant_keys):
                    s["value"] = new_value
                    _LOGGER.warning(f"{self.deviceName}: Sensor value pre-set to {new_value} (race-condition prevention)")
                    break
            for o in self.options:
                if any(key in o.get("entity_id", "").lower() for key in relevant_keys):
                    o["value"] = new_value
                    break

        control_value_found = False
        needs_turn_on = False
        turn_on_kwargs = {}

        # ── Sensoren ──────────────────────────────────────────────────────────────
        for sensor in self.sensors:
            entity_id = sensor.get("entity_id", "")
            if not any(key in entity_id.lower() for key in relevant_keys):
                continue

            if self.deviceType == "Light" and "_duty" in entity_id.lower():
                _LOGGER.debug(f"{self.deviceName}: Skipping duty sensor for Light: {entity_id}")
                continue

            raw_value = sensor.get("value")
            _LOGGER.warning(f"{self.deviceName}: checkForControlValue sensor {entity_id} raw_value={raw_value}")
            if raw_value is None:
                continue

            converted = convert_to_int(raw_value, multiply_by_10=self.isAcInfinDev)
            if converted is None:
                continue

            # Beim Init (force_update=False): Wert 0 überspringen – noch kein echter HA-Wert
            if not force_update and converted == 0:
                _LOGGER.warning(f"{self.deviceName}: Init – sensor {entity_id} hat Wert 0, überspringe (warte auf echten Wert via deviceUpdater)")
                continue

            if self.deviceType == "Light":
                old = self.voltage
                self.voltage = converted
                control_value_found = True
                _LOGGER.debug(f"{self.deviceName}: Voltage from Sensor updated to {self.voltage}%.")

                # Clampen NUR wenn MinMax aktiv
                if self.is_minmax_active and self.minVoltage is not None and self.maxVoltage is not None:
                    clamped = self.clamp_voltage(self.voltage)
                    if clamped != self.voltage:
                        _LOGGER.warning(f"{self.deviceName}: MinMax aktiv – voltage {self.voltage}% → clamp {clamped}%")
                        self.voltage = clamped
                        update_sensor_value_in_list(self.voltage)
                        if force_update and self.isRunning:
                            needs_turn_on = True
                            turn_on_kwargs = {"brightness_pct": self.voltage}

                if force_update and self.isRunning and old != self.voltage and not needs_turn_on:
                    needs_turn_on = True
                    turn_on_kwargs = {"brightness_pct": self.voltage}

                break

            elif self.deviceType in duty_types:
                old = self.dutyCycle
                self.dutyCycle = converted
                control_value_found = True
                _LOGGER.debug(f"{self.deviceName}: Duty Cycle from Sensor updated to {self.dutyCycle}%.")

                # Clampen NUR wenn MinMax aktiv
                if self.is_minmax_active and self.minDuty is not None and self.maxDuty is not None:
                    clamped = self.clamp_duty_cycle(self.dutyCycle)
                    if clamped != self.dutyCycle:
                        _LOGGER.warning(f"{self.deviceName}: MinMax aktiv – dutyCycle {self.dutyCycle}% → clamp {clamped}%")
                        self.dutyCycle = clamped
                        update_sensor_value_in_list(self.dutyCycle)
                        if force_update and self.isRunning:
                            needs_turn_on = True
                            if self.isSpecialDevice:
                                turn_on_kwargs = {"brightness_pct": float(self.dutyCycle)}
                            else:
                                turn_on_kwargs = {"percentage": self.dutyCycle}

                if force_update and self.isRunning and old != self.dutyCycle and not needs_turn_on:
                    needs_turn_on = True
                    if self.isSpecialDevice:
                        turn_on_kwargs = {"brightness_pct": float(self.dutyCycle)}
                    else:
                        turn_on_kwargs = {"percentage": self.dutyCycle}

                break

        # ── Options ───────────────────────────────────────────────────────────────
        if not control_value_found:
            for option in self.options:
                entity_id = option.get("entity_id", "")
                if not any(key in entity_id for key in relevant_keys):
                    continue

                raw_value = option.get("value", 0)

                if self.deviceType == "Light":
                    self.voltageFromNumber = True
                    converted = convert_to_int(raw_value, multiply_by_10=True)
                    if converted is None:
                        continue

                    # Beim Init: Wert 0 überspringen
                    if not force_update and converted == 0:
                        _LOGGER.warning(f"{self.deviceName}: Init – option {entity_id} hat Wert 0, überspringe")
                        continue

                    old = self.voltage
                    self.voltage = converted
                    control_value_found = True
                    _LOGGER.debug(f"{self.deviceName}: Voltage set from Options to {self.voltage}%.")

                    if self.is_minmax_active and self.minVoltage is not None and self.maxVoltage is not None:
                        clamped = self.clamp_voltage(self.voltage)
                        if clamped != self.voltage:
                            _LOGGER.warning(f"{self.deviceName}: MinMax aktiv – voltage {self.voltage}% → clamp {clamped}%")
                            self.voltage = clamped
                            update_sensor_value_in_list(self.voltage)
                            if force_update and self.isRunning:
                                needs_turn_on = True
                                turn_on_kwargs = {"brightness_pct": self.voltage}

                    if force_update and self.isRunning and old != self.voltage and not needs_turn_on:
                        needs_turn_on = True
                        turn_on_kwargs = {"brightness_pct": self.voltage}
                    break

                elif self.deviceType in duty_types:
                    converted = convert_to_int(raw_value, multiply_by_10=self.isAcInfinDev)
                    if converted is None:
                        continue

                    # Beim Init: Wert 0 überspringen
                    if not force_update and converted == 0:
                        _LOGGER.warning(f"{self.deviceName}: Init – option {entity_id} hat Wert 0, überspringe")
                        continue

                    old = self.dutyCycle
                    self.dutyCycle = converted
                    control_value_found = True
                    _LOGGER.debug(f"{self.deviceName}: Duty Cycle set from Options to {self.dutyCycle}%.")

                    if self.is_minmax_active and self.minDuty is not None and self.maxDuty is not None:
                        clamped = self.clamp_duty_cycle(self.dutyCycle)
                        if clamped != self.dutyCycle:
                            _LOGGER.warning(f"{self.deviceName}: MinMax aktiv – dutyCycle {self.dutyCycle}% → clamp {clamped}%")
                            self.dutyCycle = clamped
                            update_sensor_value_in_list(self.dutyCycle)
                            if force_update and self.isRunning:
                                needs_turn_on = True
                                if self.isSpecialDevice:
                                    turn_on_kwargs = {"brightness_pct": float(self.dutyCycle)}
                                else:
                                    turn_on_kwargs = {"percentage": self.dutyCycle}

                    if force_update and self.isRunning and old != self.dutyCycle and not needs_turn_on:
                        needs_turn_on = True
                        if self.isSpecialDevice:
                            turn_on_kwargs = {"brightness_pct": float(self.dutyCycle)}
                        else:
                            turn_on_kwargs = {"percentage": self.dutyCycle}
                    break

        # ── Set defaults ONLY if no control value was found ────────────────────────
        if not control_value_found:
            self._set_default_control_values()

        # ── turn_on wenn force_update und Wert geändert oder geclampt ─────────────
        if needs_turn_on:
            _LOGGER.warning(f"{self.deviceName}: External state change detected - forcing control value update")
            self._last_turn_on_time = 0
            asyncio.ensure_future(self.safe_turn_on(**turn_on_kwargs))

    def _set_default_control_values(self) -> None:
        """Set default control values ONLY when no actual control values were found."""
        if self.deviceType == "Light":
            if self.voltage is None:
                self.voltage = 60.0  # Default light voltage (midpoint of 20-100% range)
                _LOGGER.info(f"{self.deviceName}: No control value found - using default voltage: {self.voltage}%")
            else:
                _LOGGER.debug(f"{self.deviceName}: Using existing voltage: {self.voltage}%")
                
        elif self.deviceType in {"Exhaust", "Intake", "Ventilation", "Humidifier", "Dehumidifier"}:
            if self.dutyCycle is None:
                self.dutyCycle = 50.0  # Default duty cycle
                _LOGGER.info(f"{self.deviceName}: No control value found - using default duty cycle: {self.dutyCycle}%")
            else:
                _LOGGER.debug(f"{self.deviceName}: Using existing duty cycle: {self.dutyCycle}%")

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

    async def set_value(self, value: int | float | str | None) -> None:
        """Setzt einen numerischen Wert, falls unterstützt und relevant (duty oder voltage)."""
        if not self.options:
            _LOGGER.debug(f"{self.deviceName}: unterstützt keine numerischen Werte.")
            return

        # value sicher konvertieren
        try:
            value = float(value)
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.deviceName}: set_value ungültiger Wert '{value}'")
            return

        for option in self.options:
            entity_id = option.get("entity_id", "")
            if "duty" in entity_id or "intensity" in entity_id:
                try:
                    send_value = float(int(value)) if self.isAcInfinDev else value
                    await self.hass.services.async_call(
                        domain="number",
                        service="set_value",
                        service_data={"entity_id": entity_id, "value": send_value},
                    )
                    _LOGGER.debug(f"{self.deviceName}: Wert {send_value} für {entity_id} gesetzt.")
                except Exception as e:
                    _LOGGER.error(f"{self.deviceName}: Fehler beim Setzen des Wertes für {entity_id}: {e}")
                return  # immer nach erster passender Option abbrechen

        _LOGGER.warning(f"{self.deviceName}: keine Option mit 'duty' oder 'intensity' in entity_id gefunden.")

    async def set_mode(self, mode: str) -> None:
        """Setzt den Mode des Geräts, falls unterstützt."""
        if not self.options:
            _LOGGER.warning(f"{self.deviceName}: unterstützt keine Modi.")
            return

        if not isinstance(mode, str) or not mode.strip():
            _LOGGER.error(f"{self.deviceName}: set_mode ungültiger Mode '{mode}'")
            return

        try:
            entity_id = self.options[0].get("entity_id", "")
            if not entity_id:
                _LOGGER.error(f"{self.deviceName}: options[0] hat keine entity_id")
                return
            await self.hass.services.async_call(
                domain="select",
                service="select_option",
                service_data={"entity_id": entity_id, "option": mode},
            )
            _LOGGER.debug(f"{self.deviceName}: Mode '{mode}' für {entity_id} gesetzt.")
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Fehler beim Setzen des Mode: {e}")

    async def WorkMode(self, workmode) -> None:
        special_light_types = {"LightFarRed", "LightUV", "LightBlue", "LightRed", "LightSpectrum"}
        no_action_types     = {"Light", "Pump", "Sensor"}

        # Lights die aus sind: WorkMode speichern oder ignorieren
        if hasattr(self, 'islightON') and not self.islightON:
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
                    # minVoltage nutzen wenn gesetzt, sonst initVoltage
                    try:
                        if self.minVoltage is not None and self.maxVoltage is not None:
                            self.voltage = float(self.minVoltage)
                        else:
                            self.voltage = float(self.initVoltage)
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"{self.deviceName}: Ungültiger Voltage-Wert, nutze initVoltage Fallback")
                        self.voltage = float(getattr(self, 'initVoltage', 20))
                    await self.safe_turn_on(brightness_pct=self.voltage)

                elif self.deviceType in special_light_types:
                    _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring WorkMode, using dedicated scheduling")
                    return

                else:
                    try:
                        min_duty = int(float(self.minDuty))
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"{self.deviceName}: Ungültiger minDuty-Wert, nutze 0")
                        min_duty = 0
                    self.dutyCycle = min_duty
                    if self.isSpecialDevice:
                        await self.safe_turn_on(brightness_pct=min_duty)
                    else:
                        await self.safe_turn_on(percentage=min_duty)

            else:
                if self.deviceType not in no_action_types:
                    await self.turn_off()

        else:  # WorkMode deaktiviert
            if self.isDimmable:
                if self.deviceType == "Light":
                    if hasattr(self, 'sunPhaseActive') and self.sunPhaseActive:
                        await self.eventManager.emit("resumeSunPhase", False)
                        return
                    try:
                        self.voltage = float(self.maxVoltage)
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"{self.deviceName}: Ungültiger maxVoltage-Wert")
                        self.voltage = 100.0
                    if self.isRunning:
                        await self.safe_turn_on(brightness_pct=self.voltage)

                else:
                    try:
                        max_duty = int(float(self.maxDuty))
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"{self.deviceName}: Ungültiger maxDuty-Wert, nutze 100")
                        max_duty = 100
                    self.dutyCycle = max_duty
                    if self.isRunning:
                        if self.isSpecialDevice:
                            await self.safe_turn_on(brightness_pct=max_duty)
                        else:
                            await self.safe_turn_on(percentage=max_duty)

            else:
                if self.deviceType not in no_action_types:
                    if self.isRunning:
                        await self.turn_on()

    async def changeMinMaxValues(self, newValue: int | float | str | None) -> None:
        _LOGGER.debug(f"{self.deviceName}: Type:{self.deviceType} NewValue: {newValue}")

        if self.deviceType == "Light":
            clamped_value = self.clamp_voltage(newValue)
            self.voltage = clamped_value
            _LOGGER.info(f"{self.deviceName}: Voltage set to {clamped_value} (was {newValue})")
            await self.turn_on(brightness_pct=clamped_value)
            return

        else:
            clamped_value = self.clamp_duty_cycle(newValue)
            self.dutyCycle = clamped_value
            _LOGGER.info(f"{self.deviceName}: DutyCycle set to {clamped_value}% (was {newValue}%)")
            if self.isSpecialDevice:
                await self.turn_on(brightness_pct=float(clamped_value))
                return
            else:
                await self.turn_on(percentage=clamped_value)
                return

    async def DeviceSetMinMax(self, data) -> None:
        # KEIN isInitialized check – Bounds müssen auch beim Init geladen werden
        if hasattr(self, 'sunPhaseActive') and self.sunPhaseActive:
            _LOGGER.info(f"{self.deviceName}: Cannot change min/max during active sunphase")
            return

        if not self.isDimmable:
            return
        
        _LOGGER.warning(f"{self.deviceName}: Processing SetMinMax event: {data}")

        event_device_type = None
        if isinstance(data, str):
            event_device_type = data
        elif isinstance(data, dict):
            event_device_type = data.get("deviceType", "")
        
        if event_device_type and event_device_type.lower() != self.deviceType.lower():
            _LOGGER.debug(f"{self.deviceName}: ignoring SetMinMax – event for '{event_device_type}', I am '{self.deviceType}'")
            return

        try:
            minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
        except AttributeError:
            _LOGGER.warning(f"{self.deviceName}: dataStore nicht verfügbar in DeviceSetMinMax")
            return

        if not isinstance(minMaxSets, dict):
            _LOGGER.warning(f"{self.deviceName}: minMaxSets is not a dict for {self.deviceType}")
            return
            
        if not minMaxSets.get("active", False):
            _LOGGER.debug(f"{self.deviceName}: min/max control is not active for {self.deviceType}")
            return

        if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets and self.deviceType == "Light":
            try:
                old_min, old_max = self.minVoltage, self.maxVoltage
                self.minVoltage = float(minMaxSets.get("minVoltage"))
                self.maxVoltage = float(minMaxSets.get("maxVoltage"))
                _LOGGER.info(f"{self.deviceName}: Updated voltage min/max: min={old_min}→{self.minVoltage}%, max={old_max}→{self.maxVoltage}%")
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: Ungültige Voltage-Werte: {minMaxSets.get('minVoltage')}, {minMaxSets.get('maxVoltage')}")
                return

            # Nur clampen + turn_on wenn bereits initialisiert
            if self.isInitialized:
                if self.isRunning:
                    await self.changeMinMaxValues(self.clamp_voltage(self.voltage))
                else:
                    self.voltage = self.clamp_voltage(self.voltage)
            else:
                _LOGGER.debug(f"{self.deviceName}: Bounds geladen aber kein turn_on – noch nicht initialisiert")

        elif "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
            try:
                old_min, old_max = self.minDuty, self.maxDuty
                self.minDuty = float(minMaxSets.get("minDuty"))
                self.maxDuty = float(minMaxSets.get("maxDuty"))
                _LOGGER.info(f"{self.deviceName}: Updated duty min/max: min={old_min}→{self.minDuty}%, max={old_max}→{self.maxDuty}%")
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: Ungültige Duty-Werte: {minMaxSets.get('minDuty')}, {minMaxSets.get('maxDuty')}")
                return

            # Nur clampen + turn_on wenn bereits initialisiert
            if self.isInitialized:
                await self.changeMinMaxValues(self.clamp_duty_cycle(self.dutyCycle))
            else:
                _LOGGER.debug(f"{self.deviceName}: Bounds geladen aber kein turn_on – noch nicht initialisiert")


    async def on_minmax_control_enabled(self, data) -> None:
        # KEIN isInitialized check – gleiche Logik wie DeviceSetMinMax
        _LOGGER.warning(f"{self.deviceName}: === MINMAX CONTROL ENABLED START ===")
        
        minmax_device_types = {"Light", "Exhaust", "Intake", "Ventilation"}
        if self.deviceType not in minmax_device_types:
            _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring MinMaxControlEnabled")
            return

        if isinstance(data, dict):
            event_device_type = data.get("deviceType", "")
            if event_device_type and event_device_type.lower() != self.deviceType.lower():
                _LOGGER.debug(f"{self.deviceName}: ignoring MinMaxControlEnabled – event for '{event_device_type}', I am '{self.deviceType}'")
                return

        try:
            minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
        except AttributeError as e:
            _LOGGER.warning(f"{self.deviceName}: dataStore nicht verfügbar: {e}")
            return

        if not isinstance(minMaxSets, dict):
            return

        if self.deviceType == "Light":
            if "minVoltage" in minMaxSets and "maxVoltage" in minMaxSets:
                try:
                    old_min, old_max = self.minVoltage, self.maxVoltage
                    self.minVoltage = float(minMaxSets.get("minVoltage"))
                    self.maxVoltage = float(minMaxSets.get("maxVoltage"))
                    _LOGGER.warning(f"{self.deviceName}: Restored voltage min/max: min={old_min}→{self.minVoltage}%, max={old_max}→{self.maxVoltage}%")
                except (ValueError, TypeError):
                    return

                if self.isInitialized and self.voltage is not None:
                    old_voltage = self.voltage
                    new_voltage = self.clamp_voltage(self.voltage)
                    if old_voltage != new_voltage:
                        self.voltage = new_voltage
                        if self.isRunning:
                            await self.turn_on(brightness_pct=self.voltage)

        elif self.deviceType in {"Exhaust", "Intake", "Ventilation"}:
            if "minDuty" in minMaxSets and "maxDuty" in minMaxSets:
                try:
                    old_min, old_max = self.minDuty, self.maxDuty
                    self.minDuty = float(minMaxSets.get("minDuty"))
                    self.maxDuty = float(minMaxSets.get("maxDuty"))
                    _LOGGER.warning(f"{self.deviceName}: Restored duty min/max: min={old_min}→{self.minDuty}, max={old_max}→{self.maxDuty}")
                except (ValueError, TypeError):
                    return

                if self.isInitialized and self.dutyCycle is not None:
                    old_duty = self.dutyCycle
                    new_duty = self.clamp_duty_cycle(self.dutyCycle)
                    if old_duty != new_duty:
                        self.dutyCycle = new_duty
                        _LOGGER.warning(f"{self.deviceName}: DutyCycle clamped from {old_duty}% to {self.dutyCycle}%")
                        if self.isRunning:
                            if self.isSpecialDevice:
                                await self.turn_on(brightness_pct=float(self.dutyCycle))
                            else:
                                await self.turn_on(percentage=self.dutyCycle)
                else:
                    _LOGGER.debug(f"{self.deviceName}: Bounds geladen aber kein turn_on – noch nicht initialisiert")


    async def on_minmax_control_disabled(self, data) -> None:
        if not self.isInitialized:
            _LOGGER.debug(f"{self.deviceName}: ignoring MinMaxControlDisabled – not yet initialized")
            return

        minmax_device_types = {"Light", "Exhaust", "Intake", "Ventilation"}
        if self.deviceType not in minmax_device_types:
            _LOGGER.debug(f"{self.deviceName}: ({self.deviceType}) ignoring MinMaxControlDisabled")
            return

        if isinstance(data, dict):
            event_device_type = data.get("deviceType", "")
            if event_device_type and event_device_type.lower() != self.deviceType.lower():
                _LOGGER.debug(f"{self.deviceName}: ignoring MinMaxControlDisabled – event for '{event_device_type}', I am '{self.deviceType}'")
                return

        _LOGGER.warning(f"{self.deviceName}: MinMax control disabled - resetting to default values")

        if self.deviceType == "Light":
            try:
                plant_stage = self.dataStore.get("plantStage")
            except AttributeError:
                plant_stage = None

            if hasattr(self, 'PlantStageMinMax') and plant_stage in self.PlantStageMinMax:
                stage_minmax = self.PlantStageMinMax[plant_stage]
                old_min, old_max = self.minVoltage, self.maxVoltage
                try:
                    self.minVoltage = float(stage_minmax["min"])
                    self.maxVoltage = float(stage_minmax["max"])
                except (ValueError, TypeError, KeyError):
                    self.minVoltage = float(getattr(self, 'initVoltage', 20))
                    self.maxVoltage = 100.0
                _LOGGER.info(f"{self.deviceName}: Using {plant_stage} min/max: min={old_min}→{self.minVoltage}%, max={old_max}→{self.maxVoltage}%")
            else:
                self.minVoltage = 20.0
                self.maxVoltage = 100.0

            if self.isRunning and self.voltage is not None:
                old_voltage = self.voltage
                if old_voltage < self.minVoltage or old_voltage > self.maxVoltage:
                    try:
                        self.voltage = self.clamp_voltage(old_voltage)
                        await self.turn_on(brightness_pct=self.voltage)
                    except (ValueError, TypeError):
                        self.voltage = 60.0
                        await self.turn_on(brightness_pct=self.voltage)
            else:
                try:
                    self.voltage = float(getattr(self, 'initVoltage', 20))
                except (ValueError, TypeError):
                    self.voltage = 20.0

        elif self.deviceType in {"Exhaust", "Intake", "Ventilation"}:
            try:
                minMaxSets = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
            except AttributeError:
                minMaxSets = None

            if isinstance(minMaxSets, dict):
                try:
                    raw_min = minMaxSets.get("minDuty")
                    raw_max = minMaxSets.get("maxDuty")
                    if raw_min is not None:
                        self.minDuty = float(raw_min)
                    if raw_max is not None:
                        self.maxDuty = float(raw_max)
                except (ValueError, TypeError):
                    pass
            else:
                self.minDuty = 0.0
                self.maxDuty = 100.0

            if self.isRunning and self.dutyCycle is not None:
                old_duty = self.dutyCycle
                if old_duty < self.minDuty or old_duty > self.maxDuty:
                    try:
                        self.dutyCycle = self.clamp_duty_cycle(old_duty)
                    except (ValueError, TypeError):
                        self.dutyCycle = 50.0
                    if old_duty != self.dutyCycle:
                        if self.isSpecialDevice:
                            await self.turn_on(brightness_pct=float(self.dutyCycle))
                        else:
                            await self.turn_on(percentage=self.dutyCycle)


    def clamp(self, value: int | float | str | None) -> float | int:
        """Genereller Clamper – wählt automatisch den richtigen Clamper basierend auf deviceType."""
        voltage_types = {"Light", "LightFarRed", "LightUV", "LightBlue", "LightRed", "LightSpectrum"}
        duty_types    = {"Exhaust", "Intake", "Ventilation"}
        duty_types_generic = {"Humidifier", "Dehumidifier", "Heater", "Cooler"}

        if self.deviceType in voltage_types:
            return self.clamp_voltage(value)
        elif self.deviceType in duty_types:
            return self.clamp_duty_cycle(value)
        elif self.deviceType in duty_types_generic:
            return self.clamp_duty_cycle(value)
        else:
            raise NotImplementedError(
                f"{self.deviceName}: clamp() nicht implementiert für deviceType '{self.deviceType}'"
            )

    def clamp_voltage(self, value: int | float | str | None) -> float:
        """Clamp voltage to min/max range."""
        try:
            v = float(value) if value is not None else 0.0
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: clamp_voltage ungültiger Wert '{value}', nutze 0.0")
            v = 0.0

        try:
            min_v = float(self.minVoltage) if self.minVoltage is not None else None
            max_v = float(self.maxVoltage) if self.maxVoltage is not None else None
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: Ungültige min/max Voltage-Werte, kein Clamping")
            return v

        if min_v is not None and max_v is not None:
            return max(min_v, min(max_v, v))
        return v

    def clamp_duty_cycle(self, value: int | float | str | None) -> int:
        """Clamp duty cycle to min/max range."""
        if value is None:
            _LOGGER.debug(f"{self.deviceName}: clamp_duty_cycle None, nutze 50%")
            value = 50.0
        else:
            try:
                value = float(value)
            except (ValueError, TypeError):
                _LOGGER.warning(f"{self.deviceName}: clamp_duty_cycle ungültiger Wert '{value}', nutze 50%")
                value = 50.0

        try:
            min_duty = float(self.minDuty) if self.minDuty is not None else 0.0
            max_duty = float(self.maxDuty) if self.maxDuty is not None else 100.0
        except (ValueError, TypeError):
            _LOGGER.warning(f"{self.deviceName}: Ungültige min/max Duty-Werte, nutze 0-100")
            min_duty, max_duty = 0.0, 100.0

        return int(max(min_duty, min(max_duty, value)))

    def deviceUpdater(self):
        # Duplikat-Schutz – Listener nur einmal registrieren
        if getattr(self, '_deviceUpdater_registered', False):
            _LOGGER.warning(f"{self.deviceName}: deviceUpdater bereits registriert – skip")
            return
        self._deviceUpdater_registered = True

        deviceEntitiys = self.getEntitys()
        _LOGGER.debug(f"UpdateListener für {self.deviceName} registriert for {deviceEntitiys}.")

        async def deviceUpdateListner(event):
            if not getattr(self, 'isInitialized', False):
                _LOGGER.warning(f"{self.deviceName}: Update ignoriert – noch nicht initialisiert")
                return
            if getattr(self, 'initialization', False):
                _LOGGER.warning(f"{self.deviceName}: Update ignoriert – Initialisierung läuft gerade")
                return

            entity_id = event.data.get("entity_id")
            if entity_id not in deviceEntitiys:
                return

            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")

            def parse_state(state) -> float | str | None:
                if state and state.state:
                    try:
                        return float(state.state)
                    except (ValueError, TypeError):
                        return state.state
                return None

            old_state_value = parse_state(old_state)
            new_state_value = parse_state(new_state)

            if old_state_value == new_state_value:
                return

            _LOGGER.warning(
                f"Device State-Change für {self.deviceName} an {entity_id} in {self.inRoom}: "
                f"Alt: {old_state_value}, Neu: {new_state_value}"
            )

            def update_entity_in_lists(entity_lists: list, entity_id: str, new_value) -> bool:
                """Sucht entity_id in allen übergebenen Listen und setzt den neuen Wert."""
                for entity_list in entity_lists:
                    for entity in entity_list:
                        if entity.get("entity_id") == entity_id:
                            entity["value"] = new_value
                            _LOGGER.warning(f"{self.deviceName}: Entity {entity_id} → {new_value}")
                            return True
                return False

            # Sensor Entitäten → Wert updaten dann checkForControlValue
            if "sensor." in entity_id:
                updated = update_entity_in_lists([self.sensors, self.options], entity_id, new_state_value)
                if updated:
                    try:
                        self.checkForControlValue(force_update=True)
                        _LOGGER.warning(f"{self.deviceName}: dutyCycle={self.dutyCycle} voltage={self.voltage} nach Sensor-Update")
                    except Exception as e:
                        _LOGGER.error(f"{self.deviceName}: Fehler checkForControlValue: {e}")

            # Number/Option Entitäten → Wert updaten dann checkForControlValue
            elif any(prefix in entity_id for prefix in ["number.", "text.", "time.", "date."]):
                updated = update_entity_in_lists([self.options], entity_id, new_state_value)
                if updated:
                    try:
                        self.checkForControlValue(force_update=True)
                        _LOGGER.warning(f"{self.deviceName}: dutyCycle={self.dutyCycle} voltage={self.voltage} nach Option-Update")
                    except Exception as e:
                        _LOGGER.error(f"{self.deviceName}: Fehler checkForControlValue: {e}")

            # Switch/Control Entitäten → Wert updaten dann Running-State aktualisieren
            elif any(prefix in entity_id for prefix in ["fan.", "light.", "switch.", "humidifier.", "select."]):
                updated = update_entity_in_lists([self.switches, self.options, self.sensors], entity_id, new_state_value)
                if updated:
                    try:
                        self.identifyIfRunningState()
                        _LOGGER.warning(f"{self.deviceName}: Running state → {self.isRunning} nach {entity_id} = {new_state_value}")
                    except Exception as e:
                        _LOGGER.error(f"{self.deviceName}: Fehler beim Aktualisieren des Running-State: {e}")

            # OGB Entitäten → nur updaten, kein checkForControlValue nötig
            elif "ogb_" in entity_id:
                update_entity_in_lists([self.sensors, self.ogbsettings], entity_id, new_state_value)
                _LOGGER.warning(f"{self.deviceName}: OGB entity {entity_id} → {new_state_value}")

        self.hass.bus.async_listen("state_changed", deviceUpdateListner)
        _LOGGER.debug(f"Device-State-Change Listener für {self.deviceName} registriert.")
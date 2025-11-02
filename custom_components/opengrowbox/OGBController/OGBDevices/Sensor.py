import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from ..utils.lightTimeHelpers import hours_between
from ..utils.calcs import calc_light_to_ppfd_dli,calculate_orp
from ..utils.sensorUpdater import _update_specific_sensor
from ..OGBDataClasses.OGBPublications import OGBPPFDPublication,OGBDLIPublication,OGBWaterPublication
from ..OGBParams.OGBTranslations import SENSOR_TRANSLATIONS
from ..OGBParams.OGBParams import (
    SENSOR_CONTEXTS, 
    extract_context_from_entity, 
    get_sensor_config
)

_LOGGER = logging.getLogger(__name__)


class Sensor():
    """Sensor-Klasse mit Context-Support und Event-basiertem Update."""  
    def __init__(self, deviceName, deviceData, eventManager, dataStore, deviceType, room, hass=None, deviceLabel="EMPTY", allLabels=[], reMapped=False):
        self.hass = hass
        self.eventManager = eventManager
        self.dataStore = dataStore
        self.deviceName = deviceName
        self.deviceType = deviceType
        self.room = room
        self.deviceData = deviceData
        self.deviceLabel = deviceLabel
        self.isRemapped = reMapped

        self.devicePlatform = None
        self.sensorMap = None
        self.labelMap = allLabels

        
        # NEUE STRUKTUR: Gruppiert nach Kontext, dann nach Sensor-Typ
        self.sensorReadings = {
            "air": {},
            "water": {},
            "soil": {},
            "light":{}
        }
        
        # Entity-ID zu Sensor-Config Mapping für schnellen Zugriff
        self._entity_to_config = {}
        
        self.isRunning = None
        self._alert_active = False
        self.isInitialized = False
        
        self._translation_cache = self._build_translation_cache()
        
        self.medium_label = self._extract_medium_label(deviceLabel)
        self.ppfdDLI_label = None
        
        # Events registrieren
        self.eventManager.on("ReadSensor", self.readSensor)
        self.eventManager.on("ReadAllSensors", self.readAllSensors)
        self.eventManager.on("GetSensorValue", self.getSensorValue)
        self.eventManager.on("CheckSensor", self.checkSensor)
        self.eventManager.on("CalibrateSensor", self.calibrateSensor)
        self.eventManager.on("SetThresholds", self.setThresholds)
        
        # NEU: Event für automatische Updates registrieren
        self.eventManager.on("SensorUpdate", self.handleSensorUpdate)
    
        asyncio.create_task(self.sensorInit())

    def __repr__(self):
        """Entwickler-freundliche Repräsentation."""
        if not self.isInitialized:
            return f"Sensor(name='{self.deviceName}', room='{self.room}', status='NOT_INITIALIZED')"
        
        sensor_count = sum(
            len(sensors) 
            for context in self.sensorReadings.values() 
            for sensors in context.values()
        )
        
        return (
            f"Sensor(name='{self.deviceName}', "
            f"room='{self.room}', "
            f"count={sensor_count}, "
            f"initialized={self.isInitialized})"
        )

    def __str__(self):
        """Benutzer-freundliche String-Repräsentation mit Kontext-Gruppierung."""
        if not self.isInitialized:
            return f"Sensor '{self.deviceName}' (Room: {self.room}) - NOT INITIALIZED"
        
        lines = [
            f"║ Sensor Device: {self.deviceName} ║ Room: {self.room} ║ Platform: {self.devicePlatform} ║ Status: {'✓ Initialized' if self.isInitialized else '✗ Not Initialized'}",
        ]
        
        # Nach Kontext gruppieren
        for context in ["air", "water", "soil","light"]:
            context_sensors = self.sensorReadings[context]
            
            if not context_sensors:
                continue
            
            # Kontext-Header
            context_name = SENSOR_CONTEXTS[context]["name"]
            context_icon = SENSOR_CONTEXTS[context]["icon"]
            lines.append(f"║ ")
            lines.append(f"║ {context_name.upper()}")
            lines.append(f"║ {'─' * 56}")
            
            # Sensoren in diesem Kontext
            for sensor_type, sensors in context_sensors.items():
                for sensor_config in sensors:
                    entity_id = sensor_config['entity_id'].split('.')[-1]
                    value = sensor_config.get('last_reading', 'N/A')
                    unit = sensor_config.get('unit', '')
                    alert = '⚠' if sensor_config.get('alert_active') else '✓'
                    
                    if isinstance(value, (int, float)):
                        value_str = f"{value:.{sensor_config.get('precision', 2)}f}"
                    else:
                        value_str = str(value)
                    
                    lines.append(f"║   {alert} [{sensor_type}] {entity_id}: {value_str} {unit}")
                    
                    # Zeige Schwellwerte falls gesetzt
                    if sensor_config.get('threshold_min') is not None or sensor_config.get('threshold_max') is not None:
                        thresh_min = sensor_config.get('threshold_min', '-')
                        thresh_max = sensor_config.get('threshold_max', '-')
                        optimal_min = sensor_config.get('optimal_min', '-')
                        optimal_max = sensor_config.get('optimal_max', '-')
                        lines.append(f"║      Current: [{thresh_min} - {thresh_max}] | Optimal: [{optimal_min} - {optimal_max}]")
        
        lines.append(f"═══════════════════════════════════════════════════")
        
        return '\n'.join(lines)

    async def sensorInit(self):
        """Initialisiere den Sensor mit Daten aus deviceData."""
        self.sensorPlatformIdent()
        await self.sensorDataGetter()

    def sensorPlatformIdent(self):
        """Analysiert deviceData und identifiziert Sensortypen MIT Kontext UND Label-Mapping."""
        if not hasattr(self, "deviceData") or not self.deviceData:
            _LOGGER.warning("Keine deviceData vorhanden.")
            return None

        sensor_map = {"air": {}, "water": {}, "soil": {},"light":{}}
        platform_set = set()
        unrecognized_suffixes = []

        for entry in self.deviceData:
            entity_id = entry.get("entity_id", "")
            value = entry.get("value")
            platform = entry.get("platform", "unknown")
            platform_set.add(platform)

            # Labels für diese Entity herausfiltern
            entity_labels = [lbl for lbl in self.labelMap if lbl.get("entity") == entity_id]
            label_ids = [lbl["id"].lower() for lbl in entity_labels]

            # Medium ermitteln
            medium_label = next((lid for lid in label_ids if "medium" in lid), None)

            # Kontext bestimmen
            if "soil" in label_ids or medium_label:
                context = "soil"
            elif "air" in label_ids:
                context = "air"
            elif "water" in label_ids:
                context = "water"
            elif "dli" in label_ids or "ppfd" in label_ids:
                context = "light"
            else:
                context = extract_context_from_entity(entity_id) or "air"

            # Sensor-Typen anhand Label + Entity bestimmen
            sensor_types = []
            for lid in label_ids:
                if lid in self._translation_cache:
                    sensor_types.append(self._translation_cache[lid])

            # Fallback, falls kein Label matcht → aus Entity ableiten
            if not sensor_types:
                suffix = entity_id.split("_")[-1].lower()
                sensor_type = self._identify_sensor_type(suffix)
                if sensor_type:
                    sensor_types.append(sensor_type)
                else:
                    unrecognized_suffixes.append(suffix)

            # Sensoren registrieren
            for sensor_type in sensor_types:
                sensor_entry = {
                    "entity_id": entity_id,
                    "value": value,
                    "platform": platform,
                    "medium_label": medium_label,
                    "context": context
                }

                if sensor_type not in sensor_map[context]:
                    sensor_map[context][sensor_type] = [sensor_entry]
                else:
                    sensor_map[context][sensor_type].append(sensor_entry)

        # Plattform setzen
        self.devicePlatform = platform_set.pop() if len(platform_set) == 1 else list(platform_set)
        self.sensorMap = {"sensors": sensor_map, "unrecognized": list(set(unrecognized_suffixes))}

        _LOGGER.info(f"{self.deviceName} - SensorMap mit Label-Mapping erstellt")
        return self.sensorMap

    def _identify_sensor_type(self, entity_suffix):
        """Identifiziert den kanonischen Sensortyp."""
        normalized_suffix = entity_suffix.lower().strip()
        
        if normalized_suffix in self._translation_cache:
            return self._translation_cache[normalized_suffix]
        
        for translation, canonical_type in self._translation_cache.items():
            if translation in normalized_suffix or normalized_suffix in translation:
                return canonical_type
        
        return None 

    async def sensorDataGetter(self):
        """Initialisiere alle Sensoren aus der sensorMap mit Kontext."""
        try:
            if not self.sensorMap or "sensors" not in self.sensorMap:
                _LOGGER.warning(f"Keine SensorMap für {self.deviceName} vorhanden")
                return
            
            # Initialisiere jeden Kontext
            for context in ["air", "water", "soil","light"]:
                context_sensors = self.sensorMap["sensors"][context]
                
                for sensor_type, sensor_entries in context_sensors.items():
                    for sensor_entry in sensor_entries:
                        await self._initializeSensorType(sensor_type, sensor_entry, context)
            
            self.isInitialized = True
            _LOGGER.info(f"Sensor-Device {self.deviceName} erfolgreich initialisiert mit {len(self._entity_to_config)} Sensoren")
            
        except Exception as e:
            _LOGGER.error(f"Fehler bei Initialisierung von Sensor {self.deviceName}: {e}")
            self.isInitialized = False
            
    def _build_translation_cache(self):
        """Erstellt einen Reverse-Lookup-Cache."""
        cache = {}
        for canonical_type, translations in SENSOR_TRANSLATIONS.items():
            for translation in translations:
                cache[translation.lower()] = canonical_type
        return cache

    async def _initializeSensorType(self, sensor_type, sensor_entry, context):
        """
        Initialisiert einen einzelnen Sensortyp MIT Kontext.
        
        Args:
            sensor_type: Der kanonische Sensortyp
            sensor_entry: Das sensor_entry Dictionary
            context: Der Kontext (air/water/soil)
        """
        entity_id = sensor_entry["entity_id"]
        
        # Kontext-spezifische Konfiguration laden
        config = get_sensor_config(sensor_type, context)
        
        if not config:
            _LOGGER.warning(f"Keine Konfiguration für '{sensor_type}' im Kontext '{context}'")
            config = {
                "unit": "",
                "device_class": None,
                "state_class": "measurement",
                "min_value": None,
                "max_value": None,
                "precision": 2
            }
        
        # Erweiterte Sensor-Konfiguration
        sensor_config = {
            "entity_id": entity_id,
            "sensor_type": sensor_type,
            "context": context,
            "unit": config.get("unit", ""),
            "device_class": config.get("device_class"),
            "state_class": config.get("state_class", "measurement"),
            "min_value": config.get("min_value"),
            "max_value": config.get("max_value"),
            "optimal_min": config.get("optimal_min"),
            "optimal_max": config.get("optimal_max"),
            "precision": config.get("precision", 2),
            "calibration_offset": 0,
            "threshold_min": None,
            "threshold_max": None,
            "last_reading": None,
            "last_update": None,
            "state": sensor_entry.get("value")
        }
        
        # In sensorReadings speichern (nach Kontext gruppiert)
        if sensor_type not in self.sensorReadings[context]:
            self.sensorReadings[context][sensor_type] = []
        
        self.sensorReadings[context][sensor_type].append(sensor_config)
        self._entity_to_config[entity_id] = sensor_config
        
        if self.medium_label and context == "soil":
            logging.debug(f"Medium Sensor Detect: {self.medium_label} {self.deviceLabel} - {entity_id} {sensor_type} - {config}")       
            await self._register_sensor_to_medium(entity_id, sensor_type)
            
        if self.medium_label and context == "light":
            await self._register_sensor_to_medium(entity_id, sensor_type)

        _LOGGER.warning(f" ✓ Sensor {sensor_type} ({context}) ({entity_id}) {self.medium_label} {self.deviceLabel} initialisiert")

    ## MEDIUM 
    def _extract_medium_label(self, device_label: str) -> Optional[str]:
        """
        Sucht Medium-Label in self.labelMap.

        Beispiele:
            labelMap enthält {'id': 'medium_1'} -> Rückgabe 'medium_1'
            keine Medium-Labels -> Rückgabe None
        """

        if not getattr(self, "labelMap", None):
            return None

        for label_entry in self.labelMap:
            label_id = str(label_entry.get("id", "")).strip().lower()
            if re.match(r"^medium[_\-]?\d*$", label_id):
                return label_id

        return None

    async def _register_sensor_to_medium(self, entity_id: str, sensor_type: str):
        """Registriert diesen Sensor bei seinem Medium."""
        try:
            # Sensor-Config aus Entity-Mapping holen
            sensor_config = self._entity_to_config.get(entity_id)
            
            if not sensor_config:
                _LOGGER.error(f"NO Sensor Config {entity_id} found")
                return
            
            # Aktuellen Wert und zusätzliche Infos holen
            current_value = sensor_config.get('state') or sensor_config.get('last_reading')
            
            await self.eventManager.emit("RegisterSensorToMedium", {
                "entity_id": entity_id,
                "sensor_type": sensor_type,
                "medium_label": self.medium_label,
                "room": self.room,
                "value": current_value,
                "unit": sensor_config.get('unit', ''),
                "context": sensor_config.get('context', 'unknown'),
                "device_name": self.deviceName
            })

            
        except Exception as e:
            _LOGGER.error(f"Fehler bei Medium-Registrierung für {entity_id}: {e}")
 
    async def handleSensorUpdate(self, event_data):
            """
            Verarbeitet eingehende SensorUpdate-Events.
            
            Args:
                event_data: OGBEventPublication mit Name, oldState, newState
            """
            try:
                entity_id = event_data.Name
                new_state = event_data.newState[0] if event_data.newState else None
                
                # Prüfe ob dieser Sensor zu diesem Device gehört
                if entity_id not in self._entity_to_config:
                    return  # Nicht unser Sensor
                
                sensor_config = self._entity_to_config[entity_id]
                
                # Update den Sensor-Wert
                if new_state is not None and new_state not in ["unavailable", "unknown"]:
                    # Versuche String zu float zu konvertieren
                    try:
                        numeric_value = float(new_state)
                        await self._updateSensorValue(sensor_config, numeric_value)
                        
                        _LOGGER.debug(
                            f"{self.deviceName}: Sensor {entity_id} aktualisiert auf {numeric_value} "
                            f"({sensor_config['sensor_type']} / {sensor_config['context']})"
                        )
                        
                        if sensor_config['context'] == "soil":
                            #await self.eventManager.emit("MediumSensorUpdate",{"from":self.room,"sensor_type":sensor_config['sensor_type'],"value":numeric_value})
                            await self.eventManager.emit("MediumSensorUpdate",sensor_config)

                        if sensor_config['sensor_type'] == "co2":
                            pass
                         
                        if sensor_config['sensor_type'] == "light":
                            if "_lumen" in entity_id:
                                unit = "lumen"
                            else:
                                unit = "lux"
                            growSpace = self.dataStore.get("growAreaM2")
                            lightStart = self.dataStore.getDeep("isPlantDay.lightOnTime")
                            lightStop = self.dataStore.getDeep("isPlantDay.lightOffTime")
                            lightDuration = hours_between(lightStart, lightStop)        
                                            
                            # Get LED type and factor from dataStore
                            led_type = self.dataStore.getDeep("Light.ledType") or "fullspektrum_grow"
                            lux_factor = self.dataStore.getDeep("Light.luxToPPFDFactor") or 15.0 
                                            
                            ppfd, dli = calc_light_to_ppfd_dli(
                                numeric_value, 
                                unit, 
                                lightDuration, 
                                growSpace,
                                led_type=led_type,
                                factor=lux_factor
                            )

                            self.dataStore.setDeep("tentData.DLI", dli)
                            self.dataStore.setDeep("tentData.PPFD", ppfd)

                            await _update_specific_sensor("ogb_ppfd_", self.room, ppfd, self.hass)
                            await _update_specific_sensor("ogb_dli_", self.room, dli, self.hass)
                            
                            self.dataStore.setDeep("Light.DLICurrent",dli)
                            self.dataStore.setDeep("Light.PPFDCurrent",ppfd)
                            
                            DLIPub = OGBDLIPublication(Name="DLIUpdate", DLI=dli)
                            await self.eventManager.emit("DLIUpdate", DLIPub)
                            PPFDPub = OGBPPFDPublication(Name="PPFDUpdate", PPFD=ppfd)
                            await self.eventManager.emit("PPFDUpdate", PPFDPub)
                            return                       
                              
                        if sensor_config['context'] == "water":
                            updated = False
                            if "_ec" in entity_id:
                                self.dataStore.setDeep("Hydro.ec_current", numeric_value)
                                updated = True
                            elif "_tds" in entity_id:
                                self.dataStore.setDeep("Hydro.tds_current", numeric_value)
                                updated = True
                            elif "_ph" in entity_id:
                                self.dataStore.setDeep("Hydro.ph_current", numeric_value)
                                updated = True
                            elif "_oxi" in entity_id:
                                self.dataStore.setDeep("Hydro.oxi_current", numeric_value)
                                updated = True
                            elif "_sal" in entity_id:
                                self.dataStore.setDeep("Hydro.sal_current", numeric_value)
                                updated = True
                            elif "_temp" in entity_id:
                                self.dataStore.setDeep("Hydro.current_temp", numeric_value)
                                updated = True
                            
                            newOrp = None
                            
                            if updated:
                                ec_current = self.dataStore.getDeep("Hydro.ec_current")
                                tds_current = self.dataStore.getDeep("Hydro.tds_current")
                                ph_current = self.dataStore.getDeep("Hydro.ph_current")
                                oxi_current = self.dataStore.getDeep("Hydro.oxi_current")
                                sal_current = self.dataStore.getDeep("Hydro.sal_current")
                                temp_current = self.dataStore.getDeep("Hydro.current_temp")

                                if temp_current and ph_current is not None:
                                    newOrp = calculate_orp(ph_current, temp_current)
                                    await _update_specific_sensor("ogb_waterorp_", self.room, newOrp, self.hass)                                             

                                hydroPublication = OGBWaterPublication(
                                    Name="HydroUpdate",
                                    ecCurrent=ec_current,
                                    tdsCurrent=tds_current,
                                    phCurrent=ph_current,
                                    oxiCurrent=oxi_current if oxi_current not in (0, None) else newOrp,
                                    salCurrent=sal_current,
                                    waterTemp=temp_current
                                )
                                await self.eventManager.emit("CheckForFeed", hydroPublication)
                                return                        
                        
                    except (ValueError, TypeError):
                        # Falls nicht numerisch, als String speichern
                        await self._updateSensorValue(sensor_config, new_state)
                        
                        _LOGGER.debug(
                            f"{self.deviceName}: Sensor {entity_id} aktualisiert auf '{new_state}' "
                            f"(nicht-numerisch)"
                        )
                
            except Exception as e:
                _LOGGER.error(f"Fehler beim Verarbeiten von SensorUpdate für {entity_id}: {e}", exc_info=True)
                
    async def _updateSensorValue(self, sensor_config, new_value):
        """
        Aktualisiert einen Sensor-Wert und prüft Schwellwerte.
        
        Args:
            sensor_config: Die Sensor-Konfiguration
            new_value: Der neue Wert
        """
        try:
            sensor_config["state"] = new_value
            
            # Wenn numerischer Wert: Kalibrierung und Validierung
            if isinstance(new_value, (int, float)):
                calibrated_value = new_value + sensor_config["calibration_offset"]
                calibrated_value = round(calibrated_value, sensor_config["precision"])
                
                if not self._validateValue(calibrated_value, sensor_config):
                    _LOGGER.error(
                        f"Sensor {sensor_config['entity_id']}: "
                        f"Wert {calibrated_value} außerhalb des gültigen Bereichs"
                    )
                
                sensor_config["last_reading"] = calibrated_value
                sensor_config["last_update"] = datetime.now()              
                
                # Schwellwert-Prüfung
                self._checkThresholdsForSensor(calibrated_value, sensor_config)
            else:
                # Nicht-numerischer Wert
                sensor_config["last_reading"] = new_value
                sensor_config["last_update"] = datetime.now()
                
        except Exception as e:
            _LOGGER.error(f"Fehler beim Update von {sensor_config['entity_id']}: {e}")

    def getSensorValue(self, sensor_type, context=None, event_data=None):
        """
        Gibt die aktuellen Werte für einen Sensortyp zurück.
        
        Args:
            sensor_type: Der kanonische Sensortyp
            context: Optional - spezifischer Kontext (air/water/soil)
            
        Returns:
            list: Liste mit aktuellen Readings (aus Cache)
        """
        readings = []
        
        # Wenn Kontext angegeben, nur diesen durchsuchen
        contexts_to_search = [context] if context else ["air", "water", "soil"]
        
        for ctx in contexts_to_search:
            if sensor_type in self.sensorReadings[ctx]:
                for sensor_config in self.sensorReadings[ctx][sensor_type]:
                    # Gebe gecachte Werte zurück (keine HA-Abfrage mehr nötig)
                    reading = {
                        "sensor_type": sensor_config["sensor_type"],
                        "context": sensor_config["context"],
                        "entity_id": sensor_config["entity_id"],
                        "value": sensor_config.get("last_reading"),
                        "unit": sensor_config["unit"],
                        "device_class": sensor_config["device_class"],
                        "timestamp": sensor_config.get("last_update").isoformat() if sensor_config.get("last_update") else None,
                        "alert_active": sensor_config.get("alert_active", False)
                    }
                    readings.append(reading)
        
        return readings if readings else None
    
    def getSensorsByContext(self, context):
        """
        Gibt alle Sensoren eines bestimmten Kontexts zurück.
        
        Args:
            context: "air", "water" oder "soil"
            
        Returns:
            dict: Alle Sensor-Typen in diesem Kontext
        """
        if context not in self.sensorReadings:
            return {}
        
        return self.sensorReadings[context]
    
    def getSensorTypes(self):
        """
        Gibt alle verfügbaren Sensortypen des Devices zurück.
        
        Returns:
            list: Liste der kanonischen Sensortypen
        """
        sensor_types = set()
        for context_sensors in self.sensorReadings.values():
            sensor_types.update(context_sensors.keys())
        return list(sensor_types)
    
    def getAllContexts(self):
        """
        Gibt alle verwendeten Kontexte zurück.
        
        Returns:
            list: Liste der Kontexte die Sensoren haben
        """
        contexts = []
        for context in ["air", "water", "soil"]:
            if self.sensorReadings[context]:
                contexts.append(context)
        return contexts

    async def _readSingleSensor(self, sensor_config):
        """
        Liest einen einzelnen Sensor (jetzt aus Cache statt HA).
        
        Returns gecachte Werte, die durch Events aktualisiert werden.
        """
        try:
            value = sensor_config.get("last_reading")
            
            if value is None:
                # Falls noch kein Update kam, einmalig von HA laden
                await self._loadStateForSensor(sensor_config)
                value = sensor_config.get("last_reading")
            
            return {
                "sensor_type": sensor_config["sensor_type"],
                "context": sensor_config["context"],
                "entity_id": sensor_config["entity_id"],
                "value": value,
                "unit": sensor_config["unit"],
                "device_class": sensor_config["device_class"],
                "timestamp": sensor_config.get("last_update").isoformat() if sensor_config.get("last_update") else None,
                "alert_active": sensor_config.get("alert_active", False)
            }
                
        except Exception as e:
            _LOGGER.error(f"Fehler beim Lesen von Sensor {sensor_config['entity_id']}: {e}")
            return None
    
    async def _loadStateForSensor(self, sensor_config):
        """Lädt den aktuellen State für einen Sensor (nur bei Initialisierung)."""
        if not self.hass:
            return
        
        entity_id = sensor_config["entity_id"]
        state = self.hass.states.get(entity_id)
        
        if state and state.state not in ["unavailable", "unknown"]:
            try:
                value = float(state.state)
                await self._updateSensorValue(sensor_config, value)
            except (ValueError, TypeError):
                await self._updateSensorValue(sensor_config, state.state)
    
    def _validateValue(self, value, sensor_config):
        """Validiere einen Sensor-Wert."""
        min_val = sensor_config.get("min_value")
        max_val = sensor_config.get("max_value")
        
        if min_val is not None and value < min_val:
            return False
        if max_val is not None and value > max_val:
            return False
        return True
    
    def _checkThresholdsForSensor(self, value, sensor_config):
        """Prüfe Schwellwerte für einen Sensor."""
        threshold_min = sensor_config.get("threshold_min")
        threshold_max = sensor_config.get("threshold_max")
        alert_triggered = False
        
        if threshold_min is not None and value < threshold_min:
            alert_triggered = True
            self.eventManager.emit("SensorAlert", {
                "device": self.deviceName,
                "sensor_type": sensor_config["sensor_type"],
                "context": sensor_config["context"],
                "entity_id": sensor_config["entity_id"],
                "alert_type": "below_minimum",
                "value": value,
                "threshold": threshold_min,
                "room": self.room
            })
        
        if threshold_max is not None and value > threshold_max:
            alert_triggered = True
            self.eventManager.emit("SensorAlert", {
                "device": self.deviceName,
                "sensor_type": sensor_config["sensor_type"],
                "context": sensor_config["context"],
                "entity_id": sensor_config["entity_id"],
                "alert_type": "above_maximum",
                "value": value,
                "threshold": threshold_max,
                "room": self.room
            })
        
        sensor_config["alert_active"] = alert_triggered
    
    def checkSensor(self, event_data=None):
        """Überprüfe alle Sensoren auf Verfügbarkeit."""
        if not self.isInitialized:
            _LOGGER.warning(f"Sensor-Device {self.deviceName} ist nicht initialisiert")
            return False
        
        all_ok = True
        for context_sensors in self.sensorReadings.values():
            for sensors in context_sensors.values():
                for sensor_config in sensors:
                    if not self._checkSingleSensor(sensor_config):
                        all_ok = False
        
        return all_ok
    
    def _checkSingleSensor(self, sensor_config):
        """Prüfe einen einzelnen Sensor."""
        entity_id = sensor_config["entity_id"]
        
        # Prüfe Alter der letzten Lesung
        last_update = sensor_config.get("last_update")
        if last_update:
            age = datetime.now() - last_update
            if age > timedelta(minutes=5):
                _LOGGER.warning(f"Sensor {entity_id}: Letzte Lesung ist {age.seconds}s alt")
                return False
        else:
            _LOGGER.warning(f"Sensor {entity_id}: Keine Lesung vorhanden")
            return False
        
        return True
    
    def calibrateSensor(self, event_data=None):
        """
        Kalibriere einen oder mehrere Sensoren.
        
        Args:
            event_data: Dict mit "sensor_type" und "offset" oder "entity_id" und "offset"
        """
        if not event_data:
            return False
        
        offset = event_data.get("offset", 0)
        sensor_type = event_data.get("sensor_type")
        entity_id = event_data.get("entity_id")
        context = event_data.get("context")
        
        try:
            if entity_id:
                # Kalibriere spezifischen Sensor
                if entity_id in self._entity_to_config:
                    self._entity_to_config[entity_id]["calibration_offset"] = float(offset)
                    _LOGGER.info(f"Sensor {entity_id} kalibriert mit Offset: {offset}")
                    return True
            
            elif sensor_type:
                # Kalibriere alle Sensoren dieses Typs
                contexts_to_search = [context] if context else ["air", "water", "soil"]
                count = 0
                
                for ctx in contexts_to_search:
                    if sensor_type in self.sensorReadings[ctx]:
                        for sensor_config in self.sensorReadings[ctx][sensor_type]:
                            sensor_config["calibration_offset"] = float(offset)
                            count += 1
                
                if count > 0:
                    _LOGGER.info(f"{count} Sensoren vom Typ {sensor_type} kalibriert mit Offset: {offset}")
                    return True
            
            return False
        except Exception as e:
            _LOGGER.error(f"Fehler bei Kalibrierung: {e}")
            return False
    
    def setThresholds(self, event_data=None):
        """
        Setze Schwellwerte für Sensoren.
        
        Args:
            event_data: Dict mit "sensor_type", "min", "max" oder "entity_id", "min", "max"
        """
        if not event_data:
            return False
        
        sensor_type = event_data.get("sensor_type")
        entity_id = event_data.get("entity_id")
        context = event_data.get("context")
        threshold_min = event_data.get("min")
        threshold_max = event_data.get("max")
        
        try:
            if entity_id:
                # Setze für spezifischen Sensor
                if entity_id in self._entity_to_config:
                    config = self._entity_to_config[entity_id]
                    if threshold_min is not None:
                        config["threshold_min"] = float(threshold_min)
                    if threshold_max is not None:
                        config["threshold_max"] = float(threshold_max)
                    _LOGGER.info(f"Schwellwerte für {entity_id} gesetzt")
                    return True
            
            elif sensor_type:
                # Setze für alle Sensoren dieses Typs
                contexts_to_search = [context] if context else ["air", "water", "soil"]
                count = 0
                
                for ctx in contexts_to_search:
                    if sensor_type in self.sensorReadings[ctx]:
                        for sensor_config in self.sensorReadings[ctx][sensor_type]:
                            if threshold_min is not None:
                                sensor_config["threshold_min"] = float(threshold_min)
                            if threshold_max is not None:
                                sensor_config["threshold_max"] = float(threshold_max)
                            count += 1
                
                if count > 0:
                    _LOGGER.info(f"Schwellwerte für {count} Sensoren vom Typ {sensor_type} gesetzt")
                    return True
            
            return False
        except Exception as e:
            _LOGGER.error(f"Fehler beim Setzen der Schwellwerte: {e}")
            return False
    
    def readSensor(self, event_data=None):
        """
        Liest einen spezifischen Sensortyp (aus Cache).
        
        Args:
            event_data: Optional dict mit "sensor_type" und optional "context"
            
        Returns:
            Reading(s) für den/die Sensor(en)
        """
        if event_data and "sensor_type" in event_data:
            sensor_type = event_data["sensor_type"]
            context = event_data.get("context")
            return self.getSensorValue(sensor_type, context)
        else:
            return self.readAllSensors()

    async def readAllSensors(self, event_data=None):
        """
        Gibt alle aktuellen Sensor-Werte zurück (aus Cache).
        
        Returns:
            dict: {context: {sensor_type: [readings]}}
        """
        if not self.isInitialized:
            _LOGGER.warning(f"Sensor-Device {self.deviceName} ist nicht initialisiert")
            return {}
        
        all_readings = {}
        
        for context in ["air", "water", "soil"]:
            context_readings = {}
            
            for sensor_type, sensors in self.sensorReadings[context].items():
                readings = []
                
                for sensor_config in sensors:
                    reading = await self._readSingleSensor(sensor_config)
                    if reading:
                        readings.append(reading)
                
                if readings:
                    context_readings[sensor_type] = readings
            
            if context_readings:
                all_readings[context] = context_readings
        
        # Event auslösen
        self.eventManager.emit("AllSensorsRead", {
            "device": self.deviceName,
            "room": self.room,
            "readings": all_readings,
            "timestamp": datetime.now().isoformat()
        })
        
        return all_readings

    # Hilfsmethoden
    def get_attributes(self):
        """Gibt alle Sensor-Device-Attribute zurück."""
        attrs = super().get_attributes() if hasattr(super(), 'get_attributes') else {}
        
        # Zähle Sensoren pro Kontext
        context_counts = {}
        for context in ["air", "water", "soil"]:
            count = sum(len(sensors) for sensors in self.sensorReadings[context].values())
            if count > 0:
                context_counts[context] = count
        
        attrs.update({
            "device_name": self.deviceName,
            "device_type": self.deviceType,
            "platform": self.devicePlatform,
            "room": self.room,
            "sensor_types": self.getSensorTypes(),
            "contexts": self.getAllContexts(),
            "total_sensors": len(self._entity_to_config),
            "context_counts": context_counts,
            "is_initialized": self.isInitialized,
            "readings_by_context": {
                context: {
                    sensor_type: [
                        {
                            "entity_id": s["entity_id"],
                            "value": s.get("last_reading"),
                            "unit": s["unit"],
                            "last_update": s.get("last_update").isoformat() if s.get("last_update") else None
                        }
                        for s in sensors
                    ]
                    for sensor_type, sensors in context_sensors.items()
                }
                for context, context_sensors in self.sensorReadings.items()
                if context_sensors
            }
        })
        return attrs
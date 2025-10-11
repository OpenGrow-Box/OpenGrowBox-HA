import logging
from .OGBDevices.Device import Device
from .OGBDevices.Sensor import Sensor
from .OGBDevices.Light import Light
from .OGBDevices.Exhaust import Exhaust
from .OGBDevices.Intake import Intake
from .OGBDevices.Ventilation import Ventilation
from .OGBDevices.Climate import Climate
from .OGBDevices.Cooler import Cooler
from .OGBDevices.Heater import Heater
from .OGBDevices.Humidifier import Humidifier
from .OGBDevices.Dehumidifier import Dehumidifier
from .OGBDevices.GenericSwitch import GenericSwitch
from .OGBDevices.Pump import Pump
from .OGBDevices.CO2 import CO2
from .OGBDataClasses.OGBPublications import OGBownDeviceSetup
import asyncio

_LOGGER = logging.getLogger(__name__)

class OGBDeviceManager:
    def __init__(self, hass, dataStore, eventManager,room,regListener):
        self.name = "OGB Device Manager"
        self.hass = hass
        self.room = room
        self.regListener = regListener
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False
        self._devicerefresh_task: asyncio.Task | None = None 
        self.init()

        #EVENTS
        self.eventManager.on("capClean",self.capCleaner)
          
    def init(self):
        """initialized Device Manager."""
        self.device_Worker()
        self.is_initialized = True
        _LOGGER.info("OGBDeviceManager initialized with event listeners.")

    async def setupDevice(self,device):            

        #ownDeviceSetup = self.dataStore.getDeep("controlOptions.ownDeviceSetup")
           
        controlOption = self.dataStore.get("mainControl")        
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        await self.addDevice(device)   

    async def addDevice(self,device):
        """Gerät aus eigener Geräteliste hinzufügen."""
        logging.error(f"DEVICE:{device}")              
        deviceName = device["name"]
        deviceData = device["entities"]
        deviceLabels = device["labels"]
        

        deviceLabelIdent = self.dataStore.get("DeviceLabelIdent")        
        
        if deviceLabelIdent == True:
            identified_device = await self.identify_device(deviceName, deviceData,deviceLabels)
        else:
            identified_device = await self.identify_device(deviceName, deviceData)
                      
        if not identified_device:
            _LOGGER.error(f"Failed to identify device: {deviceName}")
            return
        
        _LOGGER.debug(f"Device:->{identified_device} identification Success")
        
        devices = self.dataStore.get("devices")
        devices.append(identified_device)
        self.dataStore.set("devices",devices)
        _LOGGER.info(f"Added new device From List: {identified_device}")    
                
    
        return identified_device
    
    async def removeDevice(self, deviceName: str):
        """Entfernt ein Gerät anhand des Gerätenamens aus der Geräteliste."""

        controlOption = self.dataStore.get("mainControl")
        devices = self.dataStore.get("devices")
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        # Gerät anhand des Namens finden
        deviceToRemove = next((device for device in devices if device.deviceName == deviceName), None)

        if not deviceToRemove:
            _LOGGER.debug(f"Device not found for remove: {deviceName}")
            return False

        devices.remove(deviceToRemove)
        self.dataStore.set("devices", devices)

        _LOGGER.debug(f"Removed device: {deviceName}")

        # ➕ Capability-Cleanup
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

        for cap, deviceTypes in capMapping.items():
            if deviceToRemove.deviceType.lower() in (dt.lower() for dt in deviceTypes):
                capPath = f"capabilities.{cap}"
                currentCap = self.dataStore.getDeep(capPath)

                if currentCap and deviceToRemove.deviceName in currentCap["devEntities"]:
                    currentCap["devEntities"].remove(deviceToRemove.deviceName)
                    currentCap["count"] = max(0, currentCap["count"] - 1)
                    currentCap["state"] = currentCap["count"] > 0
                    self.dataStore.setDeep(capPath, currentCap)
                    _LOGGER.debug(f"Updated capability '{cap}' after removing device {deviceToRemove.deviceName}")

        return True

    async def identify_device(self, device_name, device_data, device_labels=None):
        """
        Gerät anhand von Namen, Labels und Typzuordnung identifizieren.
        Wenn Labels vorhanden sind, werden sie bevorzugt zur Geräteerkennung genutzt.
        """
        device_type_mapping = {
            "Sensor": ["ogb", "sensor", "temperature", "temp", "humidity", "moisture", "dewpoint", "illuminance", "ppfd", "dli", "h5179", "govee", "ens160", "tasmota"],
            "Exhaust": ["exhaust", "abluft"],
            "Intake": ["intake", "zuluft"],
            "Ventilation": ["vent", "vents", "venti", "ventilation", "inlet"],
            "Dehumidifier": ["dehumidifier", "entfeuchter"],
            "Humidifier": ["humidifier", "befeuchter"],
            "Heater": ["heater", "heizung"],
            "Cooler": ["cooler", "kuehler"],
            "Climate": ["climate", "klima"],
            "Light": ["light", "lamp", "led"],
            "CO2": ["co2", "carbon"],
            "Pump": ["pump"],
            "Switch": ["generic", "switch"],
        }

        # 🏷️ Schritt 1: Labels prüfen
        label_matches = []
        if device_labels:
            for lbl in device_labels:
                label_name = lbl.get("name", "").lower()
                if not label_name:
                    continue
                for device_type, keywords in device_type_mapping.items():
                    if any(keyword in label_name for keyword in keywords):
                        label_matches.append(device_type)

        # Falls mehrere Labels passen → das häufigste nehmen
        detected_type = None
        if label_matches:
            from collections import Counter
            detected_type = Counter(label_matches).most_common(1)[0][0]
            _LOGGER.debug(f"Device '{device_name}' identified via label as {detected_type}")

        # 🧠 Schritt 2: Wenn kein Label passt, Name prüfen
        if not detected_type:
            for device_type, keywords in device_type_mapping.items():
                if any(keyword in device_name.lower() for keyword in keywords):
                    detected_type = device_type
                    _LOGGER.debug(f"Device '{device_name}' identified via name as {detected_type}")
                    break

        # 🪫 Fallback
        if not detected_type:
            _LOGGER.warning(f"Device '{device_name}' could not be identified. Returning generic Device.")
            return Device(device_name, device_data, self.eventManager, self.dataStore, "UNKNOWN", self.room, self.hass)

        # 🧩 Schritt 3: Device-Klasse instanziieren
        DeviceClass = self.get_device_class(detected_type)
        return DeviceClass(device_name, device_data, self.eventManager, self.dataStore, detected_type, self.room, self.hass)

    def get_device_class(self, device_type):
        """Geräteklasse erhalten."""
        device_classes = {
            "Humidifier": Humidifier,
            "Dehumidifier": Dehumidifier,
            "Exhaust": Exhaust,
            "Intake":Intake,
            "Ventilation": Ventilation,
            "Heater": Heater,
            "Cooler": Cooler,
            "Light": Light,
            "Climate": Climate,
            "Switch": GenericSwitch,
            "Sensor": Sensor,
            "Pump": Pump,
            "C02":CO2,
        }
        return device_classes.get(device_type, Device)

    async def DeviceUpdater(self):
        controlOption = self.dataStore.get("mainControl")

        
        # Hole alle bekannten Geräte aus Home Assistant (z. B. Sensoren, Schalter etc.)
        groupedRoomEntities = await self.regListener.get_filtered_entities_with_valueForDevice(self.room.lower())
        
        # Filtere Geräte ohne "ogb" im Namen
        allDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]
        self.dataStore.setDeep("workData.Devices", allDevices)
        
        # Update Event auslösen
        await self.eventManager.emit("UpdateDeviceList", allDevices)       
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        # Hole aktuelle Geräteinstanzen aus dem Speicher (Objekte, keine Dicts!)
        currentDevices = self.dataStore.get("devices") or []

        # Sichere Extraktion von Gerätenamen aus aktuellen Geräteobjekten
        knownDeviceNames = {device.deviceName for device in currentDevices if hasattr(device, "deviceName")}
        
        # Extrahiere Gerätenamen aus der allDevices-Liste (diese besteht aus dicts)
        realDeviceNames = {device["name"] for device in allDevices}

        # Finde neue Geräte
        newDevices = [device for device in allDevices if device["name"] not in knownDeviceNames]
        
        # Finde entfernte Geräte
        removedDevices = [device for device in currentDevices if hasattr(device, "deviceName") and device.deviceName not in realDeviceNames]

        # Entfernte Geräte entfernen
        if removedDevices:
            _LOGGER.info(f"Removing devices no longer found: {removedDevices}")
            for device in removedDevices:
                await self.removeDevice(device.deviceName)

        # Neue Geräte initialisieren
        if newDevices:
            _LOGGER.info(f"Found {len(newDevices)} new devices, initializing...")
            for device in newDevices:
                _LOGGER.info(f"Registering new device: {device}")
                await self.setupDevice(device)
        else:
            _LOGGER.debug("Device-Check: No new devices found.")

    def device_Worker(self):
        if self._devicerefresh_task and not self._devicerefresh_task.done():
            _LOGGER.debug("Device refresh task is already running. Skipping start.")
            return
        
        async def periodicWorker():
            while True:
                try:
                    await self.DeviceUpdater()
                except Exception as e:
                    _LOGGER.exception(f"Error during device refresh: {e}")
                await asyncio.sleep(175)

        # Starte den Task und speichere ihn zur Kontrolle
        self._devicerefresh_task = asyncio.create_task(periodicWorker())

    def capCleaner(self,data):
        """Setzt alle Capabilities im DataStore auf den Ursprungszustand zurück."""
        capabilities = self.dataStore.get("capabilities")

        self.dataStore.set("Devices",[])

        for key in capabilities:
            capabilities[key] = {
                "state": False,
                "count": 0,
                "devEntities": []
            }

        self.dataStore.set("capabilities", capabilities)
        _LOGGER.debug(f"{self.room}: Cleared Caps and Devices")
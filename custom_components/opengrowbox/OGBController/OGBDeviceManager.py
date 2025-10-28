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
from .OGBDevices.Fridge import Fridge


from .OGBParams.OGBParams import DEVICE_TYPE_MAPPING, CAP_MAPPING

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
        _LOGGER.debug("OGBDeviceManager initialized with event listeners.")

    async def setupDevice(self,device):            
   
        controlOption = self.dataStore.get("mainControl")        
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        await self.addDevice(device)   

    async def addDevice(self, device):
        """Gerät aus eigener Geräteliste hinzufügen."""
        logging.debug(f"DEVICE:{device}")

        deviceName = device.get("name", "unknown_device")
        deviceData = device.get("entities", [])

        allLabels = []
        deviceLabels = device.get("labels", [])
        
        # Labels direkt am Device (ohne entity_id)
        for lbl in deviceLabels:
            new_lbl = {
                "id": lbl.get("id"),
                "name": lbl.get("name"),
                "scope": lbl.get("scope", "device"),
                "entity": None  # Keine direkte Entity-Zuordnung
            }
            allLabels.append(new_lbl)

        # Labels von Entities mit Entity-Zuordnung
        for entity in deviceData:
            entity_id = entity.get("entity_id")
            for lbl in entity.get("labels", []):
                new_lbl = {
                    "id": lbl.get("id"),
                    "name": lbl.get("name"),
                    "scope": lbl.get("scope", "device"),
                    "entity": entity_id
                }
                allLabels.append(new_lbl)

        # Duplikate entfernen (nach id + entity)
        uniqueLabels = []
        seen = set()
        for lbl in allLabels:
            key = (lbl["id"], lbl["entity"])
            if lbl["id"] and key not in seen:
                seen.add(key)
                uniqueLabels.append(lbl)

        identified_device = await self.identify_device(deviceName, deviceData, uniqueLabels)
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
        
        # Add-on Sensoren sollen nicht entfernt werden
        if any(deviceName.endswith(suffix) for suffix in ["_humidity", "_temperature", "_dewpoint"]):
            _LOGGER.debug(f"Skipped remove for derived sensor device: {deviceName}")
            return False

        deviceToRemove = next((device for device in devices if device.deviceName == deviceName), None)

        if not deviceToRemove:
            _LOGGER.debug(f"Device not found for remove: {deviceName}")
            return False

        devices.remove(deviceToRemove)
        self.dataStore.set("devices", devices)

        _LOGGER.warning(f"Removed device: {deviceName}")

        # Capability-Mapping anpassen
        for cap, deviceTypes in CAP_MAPPING.items():
            if deviceToRemove.deviceType.lower() in (dt.lower() for dt in deviceTypes):
                capPath = f"capabilities.{cap}"
                currentCap = self.dataStore.getDeep(capPath)

                if currentCap and deviceToRemove.deviceName in currentCap["devEntities"]:
                    currentCap["devEntities"].remove(deviceToRemove.deviceName)
                    currentCap["count"] = max(0, currentCap["count"] - 1)
                    currentCap["state"] = currentCap["count"] > 0
                    self.dataStore.setDeep(capPath, currentCap)
                    _LOGGER.warning(f"Updated capability '{cap}' after removing device {deviceToRemove.deviceName}")

        return True

    async def identify_device(self, device_name, device_data, device_labels=None):
        """
        Gerät anhand von Namen, Labels und Typzuordnung identifizieren.
        Wenn Labels vorhanden sind, werden sie bevorzugt zur Geräteerkennung genutzt.
        """

        label_matches = []
        if device_labels:
            for lbl in device_labels:
                label_name = lbl.get("name", "").lower()
                if not label_name:
                    continue
                for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                    if any(keyword in label_name for keyword in keywords):
                        label_matches.append(device_type)


        detected_type = None
        detected_label = None
        
        if label_matches:
            from collections import Counter
            detected_type = Counter(label_matches).most_common(1)[0][0]
            detected_label = detected_type
            _LOGGER.warning(f"Device '{device_name}' identified via label as {detected_type}")


        if not detected_type:
            for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                if any(keyword in device_name.lower() for keyword in keywords):
                    detected_type = device_type
                    # Wenn Labels existieren, nimm den ersten Label-Namen als Fallback
                    if device_labels:
                        detected_label = device_labels[0].get("name", "unknown")
                    else:
                        detected_label = "EMPTY"
                    _LOGGER.warning(f"Device '{device_name}' identified via name as {detected_type}")
                    break

        
        if not detected_type:
            _LOGGER.error(f"Device '{device_name}' could not be identified. Returning generic Device.")
            return

        DeviceClass = self.get_device_class(detected_type)
        return DeviceClass(device_name, device_data, self.eventManager, self.dataStore, detected_type, self.room, self.hass, detected_label,device_labels)

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
            "Generic": GenericSwitch,
            "Sensor": Sensor,
            "Pump": Pump,
            "C02":CO2,
            "Fridge":Fridge
        }
        return device_classes.get(device_type, Device)

    async def DeviceUpdater(self):
        controlOption = self.dataStore.get("mainControl")

        groupedRoomEntities = await self.regListener.get_filtered_entities_with_valueForDevice(self.room.lower())
        
        allDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]
        self.dataStore.setDeep("workData.Devices", allDevices)
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        currentDevices = self.dataStore.get("devices") or []
        deviceLabelIdent = self.dataStore.get("DeviceLabelIdent")

        knownDeviceNames = {device.deviceName for device in currentDevices if hasattr(device, "deviceName")}
        
        realDeviceNames = {device["name"] for device in allDevices}

        newDevices = [device for device in allDevices if device["name"] not in knownDeviceNames]
        
        removedDevices = [device for device in currentDevices if hasattr(device, "deviceName") and device.deviceName not in realDeviceNames]

        # Geräte mit geänderten Labels erkennen (nur wenn DeviceLabelIdent aktiv ist)
        devicesToReidentify = []
        if deviceLabelIdent:
            for realDevice in allDevices:
                currentDevice = next((d for d in currentDevices if hasattr(d, "deviceName") and d.deviceName == realDevice["name"]), None)
                if currentDevice:
                    # Vergleiche Labels
                    realLabels = set(lbl.get("name", "").lower() for lbl in realDevice.get("labels", []))
                    currentLabel = getattr(currentDevice, "deviceLabel", "EMPTY")
                    
                    # Bestimme das Label, das bei der aktuellen Identifizierung erkannt würde
                    from collections import Counter
                    label_matches = []
                    for lbl in realDevice.get("labels", []):
                        label_name = lbl.get("name", "").lower()
                        if label_name:
                            for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                                if any(keyword in label_name for keyword in keywords):
                                    label_matches.append(device_type)
                    
                    expected_label = Counter(label_matches).most_common(1)[0][0] if label_matches else "EMPTY"
                    
                    # Nur neu identifizieren, wenn sich das erkannte Label tatsächlich geändert hat
                    if currentLabel != expected_label:
                        devicesToReidentify.append(realDevice)
                        _LOGGER.warning(f"Device '{realDevice['name']}' label changed from '{currentLabel}' to '{expected_label}', will be re-identified")
                        
        if removedDevices:
            _LOGGER.debug(f"Removing devices no longer found: {removedDevices}")
            for device in removedDevices:
                await self.removeDevice(device.deviceName)

        # Geräte mit geänderten Labels entfernen und neu hinzufügen
        if devicesToReidentify:
            _LOGGER.warning(f"Re-identifying {len(devicesToReidentify)} devices due to label changes")
            for device in devicesToReidentify:
                await self.removeDevice(device["name"])
                await self.setupDevice(device)

        if newDevices:
            _LOGGER.warning(f"Found {len(newDevices)} new devices, initializing...")
            for device in newDevices:
                _LOGGER.debug(f"Registering new device: {device}")
                await self.setupDevice(device)
        else:
            _LOGGER.warning("Device-Check: No new devices found.")

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
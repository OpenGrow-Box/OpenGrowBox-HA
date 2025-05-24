import logging
from .OGBDevices.Device import Device
from .OGBDevices.Sensor import Sensor
from .OGBDevices.Light import Light
from .OGBDevices.Exhaust import Exhaust
from .OGBDevices.Inhaust import Inhaust
from .OGBDevices.Ventilation import Ventilation
from .OGBDevices.Climate import Climate
from .OGBDevices.Cooler import Cooler
from .OGBDevices.Heater import Heater
from .OGBDevices.Humidifier import Humidifier
from .OGBDevices.Dehumidifier import Dehumidifier
from .OGBDevices.GenericSwitch import GenericSwitch
from .OGBDevices.Pump import Pump
from .OGBDevices.CO2 import CO2
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

    def init(self):
        """initialized Device Manager."""
        self.device_Worker()
        self.is_initialized = True
        _LOGGER.info("OGBDeviceManager initialized with event listeners.")

    async def addDevice(self,entity):
        """Gerät aus eigener Geräteliste hinzufügen."""
 
        controlOption = self.dataStore.get("mainControl")
        if controlOption != "HomeAssistant": return
                      
        deviceName = entity["name"]
        deviceData = entity["entities"]
            
        identified_device = await self.identify_device(deviceName, deviceData)
        
        if not identified_device:
            _LOGGER.error(f"Failed to identify device: {deviceName}")
            return
        
        _LOGGER.debug(f"Device:->{identified_device} identification Success")
        
        devices = self.dataStore.get("devices")
        devices.append(identified_device)
        self.dataStore.set("devices",devices)
                   
        _LOGGER.warn(f"Added new device: {identified_device}")        
        return identified_device
    
    async def removeDevice(self, entity):
        """Entfernt ein Gerät aus der eigenen Geräteliste."""
        
        controlOption = self.dataStore.get("mainControl")
        if controlOption != "HomeAssistant":
            return
        
        deviceName = entity.deviceName
        
        devices = self.dataStore.get("devices")
        
        deviceToRemove = next((device for device in devices if device.deviceName == deviceName), None)

        if not deviceToRemove:
            _LOGGER.warning(f"Device not found: {deviceName}")
            return False

        devices.remove(deviceToRemove)
        self.dataStore.set("devices", devices)

        _LOGGER.info(f"Removed device: {deviceName}")

        # ➕ Capability-Cleanup
        capMapping = {
            "canHeat": ["heater"],
            "canCool": ["cooler"],
            "canClimate": ["climate"],
            "canHumidify": ["humidifier"],
            "canDehumidify": ["dehumidifier"],
            "canVentilate": ["ventilation"],
            "canExhaust": ["exhaust"],
            "canInhaust":["inhaust"],
            "canLight": ["light"],
            "canCO2": ["co2"],
            "canPump": ["pump"],
        }

        for cap, deviceTypes in capMapping.items():
            if deviceToRemove.deviceType.lower() in (dt.lower() for dt in deviceTypes):
                capPath = f"capabilities.{cap}"
                currentCap = self.dataStore.getDeep(capPath)

                if currentCap:
                    if deviceToRemove.deviceName in currentCap["devEntities"]:
                        currentCap["devEntities"].remove(deviceToRemove.deviceName)
                        currentCap["count"] = max(0, currentCap["count"] - 1)
                        currentCap["state"] = currentCap["count"] > 0
                        self.dataStore.setDeep(capPath, currentCap)
                        _LOGGER.debug(f"Updated capability '{cap}' after removing device {deviceToRemove.deviceName}")

        return True

    async def identify_device(self, device_name, device_data):
        """Gerät anhand des Namens und Typs identifizieren."""
        device_type_mapping = {
            "Sensor": ["ogb","sun","sensor","water","root","wurzel","blatt","leaf","mode", "plant", "temperature", "temp", "humidity", "moisture", "dewpoint", "illuminance", "ppfd", "dli", "h5179","govee","ens160","tasmota"],
            "Exhaust": ["exhaust", "abluft"],
            "Inhaust": ["inhaust", "zuluft"],
            "Ventilation": ["vent", "vents", "venti", "ventilation", "inlet", "outlet"],
            "Dehumidifier": ["dehumidifier", "drying", "dryer", "entfeuchter"],
            "Humidifier": ["humidifier","befeuchter"],
            "Heater": ["heater", "heizung"],
            "Cooler": ["cooler", "kuehler"],
            "Climate": ["climate", "klima"],
            "Light": ["light", "lamp", "led"],
            "Co2": ["co2", "carbon"],
            "Pump":["pump","airpump","mistpump","waterpump","co2pump"],
            "Switch": ["generic", "switch"],
        }

        for device_type, keywords in device_type_mapping.items():
            if any(keyword in device_name.lower() for keyword in keywords):
                DeviceClass = self.get_device_class(device_type)
                return DeviceClass(device_name,device_data,self.eventManager,self.dataStore, device_type,self.room, self.hass)

        _LOGGER.error(f"Device {device_name} not recognized, returning unknown device.")
        return Device(device_name, "unknown")

    def get_device_class(self, device_type):
        """Geräteklasse erhalten."""
        device_classes = {
            "Humidifier": Humidifier,
            "Dehumidifier": Dehumidifier,
            "Exhaust": Exhaust,
            "Inhaust":Inhaust,
            "Ventilation": Ventilation,
            "Heater": Heater,
            "Cooler": Cooler,
            "Light": Light,
            "Climate": Climate,
            "Switch": GenericSwitch,
            "Sensor": Sensor,
            "Pump": Pump,
            "co2":CO2,
        }
        return device_classes.get(device_type, Device)

    def device_Worker(self):
        if self._devicerefresh_task and not self._devicerefresh_task.done():
            _LOGGER.debug("Device refresh task is already running. Skipping start.")
            return

        async def registerWorker():
            currentDevices = self.dataStore.get("devices")
            groupedRoomEntities = await self.regListener.get_filtered_entities_with_value(self.room.lower())
            realDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]

            knownDeviceNames = {device.deviceName for device in currentDevices}
            realDeviceNames = {device["name"] for device in realDevices}

            newDevices = [device for device in realDevices if device["name"] not in knownDeviceNames]
            removedDevices = [device for device in currentDevices if device.deviceName not in realDeviceNames]

            if removedDevices:
                _LOGGER.warning(f"Removing devices no longer found: {removedDevices}")
                for device in removedDevices:
                    await self.removeDevice(device)

            if newDevices:
                _LOGGER.warning(f"Found {len(newDevices)} new devices, initializing...")
                for device in newDevices:
                    _LOGGER.warning(f"Registering new device: {device}")
                    await self.addDevice(device)
            else:
                _LOGGER.warning("Device-Check: No new devices found.")

        async def periodicWorker():
            while True:
                try:
                    await registerWorker()
                except Exception as e:
                    _LOGGER.exception(f"Error during device refresh: {e}")
                await asyncio.sleep(60)

        # Starte den Task und speichere ihn zur Kontrolle
        self._devicerefresh_task = asyncio.create_task(periodicWorker())

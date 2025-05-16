import logging
from .OGBDevices.Device import Device
from .OGBDevices.Sensor import Sensor
from .OGBDevices.Light import Light
from .OGBDevices.Exhaust import Exhaust
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
        self.init()

    def init(self):
        """Asynchrone Initialisierung des Device Managers."""
        #self.device_Worker()
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
        
        deviceName = entity.deviceName
        
        devices = self.dataStore.get("devices")
        
        deviceToRemove = next((device for device in devices if device.deviceName == deviceName), None)

        if not deviceToRemove:
            _LOGGER.warning(f"Device not found: {deviceName}")
            return False

        devices.remove(deviceToRemove)
        self.dataStore.set("devices", devices)
        
        _LOGGER.info(f"Removed device: {deviceName}")
        return True

    async def identify_device(self, device_name, device_data):
        """Gerät anhand des Namens und Typs identifizieren."""
        device_type_mapping = {
            "Sensor": ["ogb","sun","sensor","water","root","wurzel","blatt","leaf","mode", "plant", "temperature", "temp", "humidity", "moisture", "dewpoint", "illuminance", "ppfd", "dli", "h5179","govee"],
            "Dehumidifier": ["dehumidifier", "drying", "dryer", "entfeuchter", "removehumidity"],
            "Humidifier": ["humidifier","befeuchter"],
            "Exhaust": ["exhaust", "abluft", "ruck", "fan"],
            "Ventilation": ["vent", "vents", "venti", "ventilation", "inlet", "outlet"],
            "Heater": ["heater", "heizung", "warm"],
            "Climate": ["climate", "klima"],
            "Cooler": ["cooler", "kühler"],
            "Light": ["light", "lamp", "led", "switch.light"],
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


    ## work on that automatic device refesh and remove 
    def device_Worker(self):
        async def registerWorker():
            currentDevices = self.dataStore.get("devices")
            groupedRoomEntities = await self.regListener.get_filtered_entities_with_value(self.room.lower())
            realDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]

            # Geräte in currentDevices extrahieren
            knownDeviceNames = {device.deviceName for device in currentDevices}
            realDeviceNames = {device["name"] for device in realDevices}

            # Neue Geräte identifizieren
            newDevices = [device for device in realDevices if device["name"] not in knownDeviceNames]

            # Geräte entfernen, die nicht mehr existieren (außer "ogb"-Geräte)
            removedDevices = [device for device in currentDevices if device.deviceName not in realDeviceNames]

            if removedDevices:
                _LOGGER.warning(f"Removing devices no longer found: {removedDevices}")
                for device in removedDevices:
                    _LOGGER.warning(f"Removing device: {device}")
                    await self.removeDevice(device)

            if newDevices:
                _LOGGER.warn(f"Found {len(newDevices)} new devices, initializing...")
                for device in newDevices:
                    _LOGGER.warning(f"Registering new device: {device}")
                    await self.addDevice(device)
            else:
                _LOGGER.debug("No new devices found.")

        async def periodicWorker():
            while True:
                await registerWorker()
                await asyncio.sleep(300)  

        asyncio.create_task(periodicWorker())
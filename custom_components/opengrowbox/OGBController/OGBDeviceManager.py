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
        self.eventManager.on("UpdateDeviceList",self._update_ownDeviceLists)
        self.eventManager.on("MapNewDevice",self.mapDeviceFromList)
        self.eventManager.on("capClean",self.capCleaner)
          
    def init(self):
        """initialized Device Manager."""
        self.device_Worker()
        self.is_initialized = True
        _LOGGER.info("OGBDeviceManager initialized with event listeners.")

    async def setupDevice(self,device):            

        ownDeviceSetup = self.dataStore.getDeep("controlOptions.ownDeviceSetup")
           
        controlOption = self.dataStore.get("mainControl")        
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        if ownDeviceSetup:
           return
        else:
            await self.addDevice(device)       

    async def addDevice(self,device):
        """Gerät aus eigener Geräteliste hinzufügen."""
                      
        deviceName = device["name"]
        deviceData = device["entities"]
        ownDeviceSetup = self.dataStore.getDeep("controlOptions.ownDeviceSetup")

        identified_device = await self.identify_device(deviceName, deviceData)
                      
        if not identified_device:
            _LOGGER.error(f"Failed to identify device: {deviceName}")
            return
        
        _LOGGER.debug(f"Device:->{identified_device} identification Success")
        
        if ownDeviceSetup:
            devices = self.dataStore.get("ownDeviceList")
            devices.append(identified_device)
            self.dataStore.set("ownDeviceList",devices)
            _LOGGER.info(f"Added new device: {identified_device}")    
        else:
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

    async def identify_device(self, device_name, device_data):
        """Gerät anhand des Namens und Typs identifizieren."""
        device_type_mapping = {
            "Sensor": ["ogb","sun","sensor","water","wasser","root","wurzel","blatt","leaf","mode", "plant", "temperature", 
                       "temp", "humidity", "moisture", "dewpoint", "illuminance", "ppfd", "dli", "h5179","govee","ens160","tasmota"],
            "Exhaust": ["exhaust", "abluft"],
            "Intake": ["intake", "zuluft"],
            "Ventilation": ["vent", "vents", "venti", "ventilation", "inlet", "outlet"],
            "Dehumidifier": ["dehumidifier", "drying", "dryer", "entfeuchter"],
            "Humidifier": ["humidifier","befeuchter"],
            "Heater": ["heater", "heizung"],
            "Cooler": ["cooler", "kuehler"],
            "Climate": ["climate", "klima"],
            "Light": ["light", "lamp", "led"],
            "Co2": ["co2", "carbon","co2pump"],
            "Pump":["airpump","mistpump","waterpump","airpump","clonerpump","retrievepump","aeropump","dwcpump","rdwcpump"],
            "Switch": ["generic", "switch"],
        }

        for device_type, keywords in device_type_mapping.items():
            if any(keyword in device_name.lower() for keyword in keywords):
                DeviceClass = self.get_device_class(device_type)
                return DeviceClass(device_name,device_data,self.eventManager,self.dataStore,device_type,self.room, self.hass)

        _LOGGER.error(f"Device {device_name} not recognized, returning unknown device.")
        return Device(device_name, "unknown", self.eventManager,self.dataStore, "UNKOWN",self.room, self.hass)

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
            "co2":CO2,
        }
        return device_classes.get(device_type, Device)

    async def DeviceUpdater(self):
        controlOption = self.dataStore.get("mainControl")
        ownDeviceSetup = self.dataStore.getDeep("controlOptions.ownDeviceSetup")
        
        # Hole alle bekannten Geräte aus Home Assistant (z. B. Sensoren, Schalter etc.)
        groupedRoomEntities = await self.regListener.get_filtered_entities_with_valueForDevice(self.room.lower())
        
        # Filtere Geräte ohne "ogb" im Namen
        allDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]
        self.dataStore.setDeep("workData.Devices", allDevices)
        
        # Update Event auslösen
        await self.eventManager.emit("UpdateDeviceList", allDevices)       
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        if ownDeviceSetup:
            # Hole aktuelle Geräteinstanzen aus dem Speicher (Objekte, keine Dicts!)
            currentDevices = self.dataStore.get("ownDeviceList") or []
        else:
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

    async def _update_ownDeviceLists(self, device_info_list):
        """
        Extrahiert entity_ids aus device_info_list und schreibt sie in die *_device_select_{room}-Entities.
        """
        
        if device_info_list == None: return
        
        controlOption = self.dataStore.get("mainControl")        
        
        if controlOption not in ["HomeAssistant", "Premium"]:
            return False
        
        #groupedRoomEntities = await self.regListener.get_filtered_entities_with_value(self.room.lower())
        #realDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]

        # Alle entity_ids sammeln
        deviceList = []
        for device in device_info_list:
            for entity in device.get("entities", []):
                entity_id = entity.get("entity_id")
                if entity_id and entity_id not in deviceList:
                    deviceList.append(entity_id)

        _LOGGER.debug(f"[{self.room}] deviceList: {deviceList} from {device_info_list}")

        # Deine festen Ziel-Entities mit Room-Namen
        ownLightDevice_entity        = f"select.ogb_light_device_select_{self.room.lower()}"
        ownClimateDevice_entity      = f"select.ogb_climate_device_select_{self.room.lower()}"
        ownHumidiferDevice_entity    = f"select.ogb_humidifier_device_select_{self.room.lower()}"
        ownDehumidiferDevice_entity  = f"select.ogb_dehumidifier_device_select_{self.room.lower()}"
        ownExhaustDevice_entity      = f"select.ogb_exhaust_device_select_{self.room.lower()}"
        ownIntakeDevice_entity      = f"select.ogb_intake_device_select_{self.room.lower()}"
        ownVentilationDevice_entity  = f"select.ogb_vents_device_select_{self.room.lower()}"
        ownHeaterDevice_entity       = f"select.ogb_heater_device_select_{self.room.lower()}"
        ownCoolerDevice_entity       = f"select.ogb_cooler_device_select_{self.room.lower()}"
        ownco2PumpDevice_entity      = f"select.ogb_co2_device_select_{self.room.lower()}"
        ownwaterPumpDevice_entity    = f"select.ogb_waterpump_device_select_{self.room.lower()}"

        try:
            async def set_value(entity_id, value):
                safe_value = value if value else []
                await self.hass.services.async_call(
                    domain="opengrowbox",
                    service="add_select_options",
                    service_data={
                        "entity_id": entity_id,
                        "options": safe_value  # <--- hier liegt der Fix
                    },
                    blocking=True
                )
                _LOGGER.debug(f"Updated {entity_id} to {safe_value}")

            # An jede Entity die gesammelte Liste schreiben
            await set_value(ownLightDevice_entity, deviceList)
            await set_value(ownClimateDevice_entity, deviceList)
            await set_value(ownHumidiferDevice_entity, deviceList)
            await set_value(ownDehumidiferDevice_entity, deviceList)
            await set_value(ownExhaustDevice_entity, deviceList)
            await set_value(ownIntakeDevice_entity, deviceList)
            await set_value(ownVentilationDevice_entity, deviceList)
            await set_value(ownHeaterDevice_entity, deviceList)
            await set_value(ownCoolerDevice_entity, deviceList)
            await set_value(ownco2PumpDevice_entity, deviceList)
            await set_value(ownwaterPumpDevice_entity, deviceList)

        except Exception as e:
            _LOGGER.error(f"Failed to update device select options for room {self.room}: {e}")

    async def mapDeviceFromList(self, device):
        """
        Extrahiert entity_ids aus device_info_list und schreibt sie in die *_device_select_{room}-Entities.
        """
        if device.newState[0] == "unknown":
            return

        currentDevices = self.dataStore.getDeep("workData.Devices")    
           
        controlOption = self.dataStore.get("mainControl")        

        if controlOption not in ["HomeAssistant", "Premium"]:
            return False

        pub_name = device.Name  # z. B. select.ogb_humidifier_device_select_dryingtent
        try:
            cleaned_name = pub_name.split("select.ogb_")[1].split("_device_select_")[0]
        except IndexError:
            _LOGGER.error(f"Could not extract name from: {pub_name}")
            return

        # Entity-ID aus dem neuen Status
        target_entity = device.newState[0]  # z. B. "switch.heater"

        # Finde das zugehörige Device in currentDevices
        matched_device = None
        for d in currentDevices:
            entity_ids = [e.get("entity_id") for e in d.get("entities", [])]
            if target_entity in entity_ids:
                matched_device = d
                break

        if not matched_device:
            _LOGGER.error(f"No matching device found for entity {target_entity}")
            return

        # Neue Struktur mit umbenanntem Namen, aber gleichen Entities
        new_device = {
            "name": cleaned_name,
            "entities": matched_device.get("entities", [])
        }

        _LOGGER.info(f"Mapped new device structure: {new_device}")

        # Hier kannst du dann z. B. await self.addOwnDevicList(new_device) machen
        await self.addDevice(new_device)
          
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
        ownDeviceSetup = self.dataStore.getDeep("controlOptions.ownDeviceSetup")

        if ownDeviceSetup:
            self.dataStore.set("Devices",[])
        else:
            self.dataStore.set("ownDeviceList",[])

        for key in capabilities:
            capabilities[key] = {
                "state": False,
                "count": 0,
                "devEntities": []
            }

        self.dataStore.set("capabilities", capabilities)
        _LOGGER.debug(f"{self.room}: Cleared Caps and Devices")
import logging
import asyncio
from datetime import datetime
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

_LOGGER = logging.getLogger(__name__)

class OGBDeviceManager:
    def __init__(self, hass, dataStore, eventManager, room, regListener, auto_start_worker=True):
        self.name = "OGB Device Manager"
        self.hass = hass
        self.room = room
        self.regListener = regListener
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False
        self._devicerefresh_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self._last_refresh = None
        self._refresh_errors = 0
        self._max_refresh_errors = 5
        self.auto_start_worker = True
        self.init()

    def init(self):
        """Initialize Device Manager."""
        self.is_initialized = True
        _LOGGER.info("OGBDeviceManager initialized with event listeners.")
        
        # Starte den device_Worker automatisch, falls gewünscht
        if self.auto_start_worker:
            asyncio.create_task(self.device_Worker())
            _LOGGER.info("Device worker started")
        else:
            _LOGGER.info("Device worker auto-start disabled")

    async def shutdown(self):
        """Gracefully shutdown the device manager."""
        _LOGGER.info("Shutting down OGBDeviceManager...")
        self._shutdown_event.set()
        
        if self._devicerefresh_task and not self._devicerefresh_task.done():
            self._devicerefresh_task.cancel()
            try:
                await asyncio.wait_for(self._devicerefresh_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                _LOGGER.warning("Device refresh task cancelled during shutdown")

    async def addDevice(self, entity):
        """Gerät aus eigener Geräteliste hinzufügen."""
        try:
            controlOption = self.dataStore.get("mainControl")
            if controlOption != "HomeAssistant": 
                return

            deviceName = entity["name"]
            deviceData = entity["entities"]
                
            identified_device = await asyncio.wait_for(
                self.identify_device(deviceName, deviceData), 
                timeout=10.0
            )
            
            if not identified_device:
                _LOGGER.error(f"Failed to identify device: {deviceName}")
                return
            
            _LOGGER.debug(f"Device:->{identified_device} identification Success")
            
            devices = self.dataStore.get("devices")
            devices.append(identified_device)
            self.dataStore.set("devices", devices)
                       
            _LOGGER.info(f"Added new device: {identified_device}")        
            return identified_device
            
        except asyncio.TimeoutError:
            _LOGGER.error(f"Timeout while adding device: {entity.get('name', 'unknown')}")
            return None
        except Exception as e:
            _LOGGER.exception(f"Error adding device: {e}")
            return None
    
    async def removeDevice(self, entity):
        """Entfernt ein Gerät aus der eigenen Geräteliste."""
        try:
            controlOption = self.dataStore.get("mainControl")
            if controlOption != "HomeAssistant":
                return False
            
            deviceName = entity.deviceName
            
            devices = self.dataStore.get("devices")
            
            deviceToRemove = next((device for device in devices if device.deviceName == deviceName), None)

            if not deviceToRemove:
                _LOGGER.warning(f"Device not found: {deviceName}")
                return False

            devices.remove(deviceToRemove)
            self.dataStore.set("devices", devices)

            _LOGGER.info(f"Removed device: {deviceName}")

            # Capability-Cleanup
            await self._cleanup_capabilities(deviceToRemove)
            return True
            
        except Exception as e:
            _LOGGER.exception(f"Error removing device: {e}")
            return False

    async def _cleanup_capabilities(self, deviceToRemove):
        """Cleanup capabilities after device removal."""
        try:
            capMapping = {
                "canHeat": ["heater"],
                "canCool": ["cooler"],
                "canClimate": ["climate"],
                "canHumidify": ["humidifier"],
                "canDehumidify": ["dehumidifier"],
                "canVentilate": ["ventilation"],
                "canExhaust": ["exhaust"],
                "canInhaust": ["inhaust"],
                "canLight": ["light"],
                "canCO2": ["co2"],
                "canPump": ["pump"],
            }

            for cap, deviceTypes in capMapping.items():
                if deviceToRemove.deviceType.lower() in (dt.lower() for dt in deviceTypes):
                    capPath = f"capabilities.{cap}"
                    currentCap = self.dataStore.getDeep(capPath)

                    if currentCap and deviceToRemove.deviceName in currentCap.get("devEntities", []):
                        currentCap["devEntities"].remove(deviceToRemove.deviceName)
                        currentCap["count"] = max(0, currentCap["count"] - 1)
                        currentCap["state"] = currentCap["count"] > 0
                        self.dataStore.setDeep(capPath, currentCap)
                        _LOGGER.debug(f"Updated capability '{cap}' after removing device {deviceToRemove.deviceName}")
                        
        except Exception as e:
            _LOGGER.exception(f"Error during capability cleanup: {e}")

    async def identify_device(self, device_name, device_data):
        """Gerät anhand des Namens und Typs identifizieren."""
        try:
            device_type_mapping = {
                "Sensor": ["ogb", "sun", "sensor", "water", "root", "wurzel", "blatt", "leaf", "mode", "plant", "temperature", "temp", "humidity", "moisture", "dewpoint", "illuminance", "ppfd", "dli", "h5179", "govee", "ens160", "tasmota"],
                "Exhaust": ["exhaust", "abluft"],
                "Inhaust": ["inhaust", "zuluft"],
                "Ventilation": ["vent", "vents", "venti", "ventilation", "inlet", "outlet"],
                "Dehumidifier": ["dehumidifier", "drying", "dryer", "entfeuchter"],
                "Humidifier": ["humidifier", "befeuchter"],
                "Heater": ["heater", "heizung"],
                "Cooler": ["cooler", "kuehler"],
                "Climate": ["climate", "klima"],
                "Light": ["light", "lamp", "led"],
                "Co2": ["co2", "carbon"],
                "Pump": ["pump", "airpump", "mistpump", "waterpump", "co2pump"],
                "Switch": ["generic", "switch"],
            }

            for device_type, keywords in device_type_mapping.items():
                if any(keyword in device_name.lower() for keyword in keywords):
                    DeviceClass = self.get_device_class(device_type)
                    return DeviceClass(device_name, device_data, self.eventManager, self.dataStore, device_type, self.room, self.hass)

            _LOGGER.warning(f"Device {device_name} not recognized, returning unknown device.")
            return Device(device_name, "unknown")
            
        except Exception as e:
            _LOGGER.exception(f"Error identifying device {device_name}: {e}")
            return None

    def get_device_class(self, device_type):
        """Geräteklasse erhalten."""
        device_classes = {
            "Humidifier": Humidifier,
            "Dehumidifier": Dehumidifier,
            "Exhaust": Exhaust,
            "Inhaust": Inhaust,
            "Ventilation": Ventilation,
            "Heater": Heater,
            "Cooler": Cooler,
            "Light": Light,
            "Climate": Climate,
            "Switch": GenericSwitch,
            "Sensor": Sensor,
            "Pump": Pump,
            "co2": CO2,
        }
        return device_classes.get(device_type, Device)

    async def DeviceCleaner(self):
        """Clean up devices with improved error handling and timeout."""
        try:
            _LOGGER.debug("Starting device cleanup...")
            
            # Timeout für die gesamte Operation
            async with asyncio.timeout(60):  # 60 Sekunden Timeout
                currentDevices = self.dataStore.get("devices") or []
                
                # Timeout für Entity-Abfrage
                groupedRoomEntities = await asyncio.wait_for(
                    self.regListener.get_filtered_entities_with_valueForDevice(self.room.lower()),
                    timeout=30.0
                )
                
                realDevices = [group for group in groupedRoomEntities if "ogb" not in group["name"].lower()]

                knownDeviceNames = {device.deviceName for device in currentDevices}
                realDeviceNames = {device["name"] for device in realDevices}

                newDevices = [device for device in realDevices if device["name"] not in knownDeviceNames]
                removedDevices = [device for device in currentDevices if device.deviceName not in realDeviceNames]

                # Removed devices verarbeiten
                for device in removedDevices:
                    _LOGGER.info(f"Removing device no longer found: {device.deviceName}")
                    await self.removeDevice(device)

                # New devices verarbeiten
                if newDevices:
                    _LOGGER.info(f"Found {len(newDevices)} new devices, initializing...")
                    for device in newDevices:
                        if self._shutdown_event.is_set():
                            _LOGGER.info("Shutdown requested, stopping device addition")
                            break
                            
                        _LOGGER.info(f"Registering new device: {device['name']}")
                        result = await self.addDevice(device)
                        if not result:
                            _LOGGER.warning(f"Failed to add device: {device['name']}")
                else:
                    _LOGGER.debug("Device-Check: No new devices found.")

            self._last_refresh = datetime.now()
            self._refresh_errors = 0  # Reset error counter on success
            
        except asyncio.TimeoutError:
            _LOGGER.error("Device cleanup timed out")
            self._refresh_errors += 1
        except Exception as e:
            _LOGGER.exception(f"Error during device cleanup: {e}")
            self._refresh_errors += 1

    async def device_Worker(self):
        """Improved device worker with better error handling and shutdown support."""
        if self._devicerefresh_task and not self._devicerefresh_task.done():
            _LOGGER.debug("Device refresh task is already running. Skipping start.")
            return
        
        # Cancel previous task if exists
        if self._devicerefresh_task is not None:
            self._devicerefresh_task.cancel()
            try:
                await asyncio.wait_for(self._devicerefresh_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._devicerefresh_task = None   

        async def periodicWorker():
            """Periodic worker with improved error handling."""
            refresh_interval = 150  # 2.5 minutes
            error_backoff = 60     # 1 minute backoff on error
            
            _LOGGER.info("Starting device worker periodic task")
            
            while not self._shutdown_event.is_set():
                try:
                    # Check if too many consecutive errors
                    if self._refresh_errors >= self._max_refresh_errors:
                        _LOGGER.error(f"Too many consecutive refresh errors ({self._refresh_errors}). Pausing worker.")
                        wait_time = error_backoff * (2 ** min(self._refresh_errors - self._max_refresh_errors, 3))  # Exponential backoff
                        await asyncio.wait_for(self._shutdown_event.wait(), timeout=wait_time)
                        continue
                    
                    # Perform device cleanup
                    await self.DeviceCleaner()
                    
                    # Wait for next cycle or shutdown
                    try:
                        await asyncio.wait_for(self._shutdown_event.wait(), timeout=refresh_interval)
                        break  # Shutdown requested
                    except asyncio.TimeoutError:
                        continue  # Normal timeout, continue loop
                        
                except asyncio.CancelledError:
                    _LOGGER.info("Device worker cancelled")
                    break
                except Exception as e:
                    _LOGGER.exception(f"Unexpected error in device worker: {e}")
                    self._refresh_errors += 1
                    
                    # Wait before retry with backoff
                    try:
                        await asyncio.wait_for(self._shutdown_event.wait(), timeout=error_backoff)
                        break  # Shutdown requested
                    except asyncio.TimeoutError:
                        continue
            
            _LOGGER.info("Device worker stopped")

        # Start the task
        self._devicerefresh_task = asyncio.create_task(periodicWorker())
        _LOGGER.info("Device worker task started")

    async def manual_device_refresh(self):
        """Manually trigger a device refresh."""
        try:
            _LOGGER.info("Manual device refresh triggered")
            await self.DeviceCleaner()
            return True
        except Exception as e:
            _LOGGER.exception(f"Manual device refresh failed: {e}")
            return False


    @property
    def is_worker_running(self):
        """Check if the device worker is running."""
        return self._devicerefresh_task is not None and not self._devicerefresh_task.done()

    @property
    def worker_status(self):
        """Get worker status information."""
        return {
            "running": self.is_worker_running,
            "last_refresh": self._last_refresh,
            "error_count": self._refresh_errors,
            "max_errors": self._max_refresh_errors
        }
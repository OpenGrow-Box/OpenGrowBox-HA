import asyncio
import logging

from ..OGBDevices.Device import Device
from ..OGBDevices.Climate import Climate
from ..OGBDevices.CO2 import CO2
from ..OGBDevices.Cooler import Cooler
from ..OGBDevices.Dehumidifier import Dehumidifier
from ..OGBDevices.Exhaust import Exhaust
from ..OGBDevices.Fridge import Fridge
from ..OGBDevices.Camera import Camera
from ..OGBDevices.GenericSwitch import GenericSwitch
from ..OGBDevices.Heater import Heater
from ..OGBDevices.Humidifier import Humidifier
from ..OGBDevices.Intake import Intake
from ..OGBDevices.Light import Light
from ..OGBDevices.LightFarRed import LightFarRed
from ..OGBDevices.LightUV import LightUV
from ..OGBDevices.LightSpectrum import LightBlue, LightRed
from ..OGBDevices.ModbusDevice import OGBModbusDevice
from ..OGBDevices.Pump import Pump
from ..OGBDevices.FridgeGrow.FridgeGrowDevice import FridgeGrowDevice
from ..OGBDevices.Ventilation import Ventilation
from ..data.OGBParams.OGBParams import CAP_MAPPING, DEVICE_TYPE_MAPPING

_LOGGER = logging.getLogger(__name__)


class OGBDeviceManager:
    def __init__(self, hass, dataStore, event_manager, room, regListener):
        self.name = "OGB Device Manager"
        self.hass = hass
        self.room = room
        self.regListener = regListener
        self.data_store = dataStore
        self.event_manager = event_manager
        self.event_manager = event_manager  # Also provide snake_case version
        self.is_initialized = False
        self._devicerefresh_task: asyncio.Task | None = None
        self.init()

        # EVENTS
        self.event_manager.on("capClean", self.capCleaner)

    def init(self):
        """initialized Device Manager."""
        # Clean up any duplicate capabilities from previous sessions
        self.deduplicateCapabilities()
        self.device_Worker()
        self.is_initialized = True
        _LOGGER.debug("OGBDeviceManager initialized with event listeners.")

    async def setupDevice(self, device):

        controlOption = self.data_store.get("mainControl")

        if controlOption not in ["HomeAssistant", "Premium"]:
            _LOGGER.warning(f"Device setup skipped - mainControl '{controlOption}' not valid")
            return False

        _LOGGER.info(f"🔧 Setting up device: {device.get('name', 'unknown')}")
        await self.addDevice(device)
        _LOGGER.info(f"✅ Device setup completed: {device.get('name', 'unknown')}")

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
                "entity": None,  # Keine direkte Entity-Zuordnung
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
                    "entity": entity_id,
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

        identified_device = await self.identify_device(
            deviceName, deviceData, uniqueLabels
        )
        if not identified_device:
            _LOGGER.error(f"Failed to identify device: {deviceName}")
            return

        _LOGGER.debug(f"Device:->{identified_device} identification Success")

        devices = self.data_store.get("devices")
        devices.append(identified_device)
        self.data_store.set("devices", devices)
        _LOGGER.info(f"Added new device From List: {identified_device}")
        return identified_device

    async def removeDevice(self, deviceName: str):
        """Entfernt ein Gerät anhand des Gerätenamens aus der Geräteliste."""

        controlOption = self.data_store.get("mainControl")
        devices = self.data_store.get("devices")

        if controlOption not in ["HomeAssistant", "Premium"]:
            return False

        # Add-on Sensoren sollen nicht entfernt werden
        if any(
            deviceName.endswith(suffix)
            for suffix in ["_humidity", "_temperature", "_dewpoint"]
        ):
            _LOGGER.debug(f"Skipped remove for derived sensor device: {deviceName}")
            return False

        deviceToRemove = next(
            (device for device in devices if device.deviceName == deviceName), None
        )

        if not deviceToRemove:
            _LOGGER.debug(f"Device not found for remove: {deviceName}")
            return False

        devices.remove(deviceToRemove)
        self.data_store.set("devices", devices)

        _LOGGER.warning(f"{self.room} - Removed device: {deviceName}")

        # Capability-Mapping anpassen
        for cap, deviceTypes in CAP_MAPPING.items():
            if deviceToRemove.deviceType.lower() in (dt.lower() for dt in deviceTypes):
                capPath = f"capabilities.{cap}"
                currentCap = self.data_store.getDeep(capPath)

                if (
                    currentCap
                    and deviceToRemove.deviceName in currentCap["devEntities"]
                ):
                    currentCap["devEntities"].remove(deviceToRemove.deviceName)
                    currentCap["count"] = max(0, currentCap["count"] - 1)
                    currentCap["state"] = currentCap["count"] > 0
                    self.data_store.setDeep(capPath, currentCap)
                    _LOGGER.warning(
                        f"{self.room} - Updated capability '{cap}' after removing device {deviceToRemove.deviceName}"
                    )

        return True

    async def identify_device(self, device_name, device_data, device_labels=None):
        """
        Gerät anhand von Namen, Labels und Typzuordnung identifizieren.
        Wenn Labels vorhanden sind, werden sie bevorzugt zur Geräteerkennung genutzt.
        
        IMPORTANT: Special light types (LightFarRed, LightUV, etc.) must be matched
        BEFORE generic Light type. We use EXACT matching first, then fallback to
        contains matching with priority ordering.
        """

        detected_type = None
        detected_label = None

        # Priority-ordered list for special types that need exact/priority matching
        # More specific types MUST come before generic types
        PRIORITY_DEVICE_TYPES = [
            "LightFarRed",  # Must match before "Light"
            "LightUV",      # Must match before "Light"
            "LightBlue",    # Must match before "Light"
            "LightRed",     # Must match before "Light"
        ]

        # FRIDGEGROW CHECK: If device has "fridgegrow" or "plantalytix" label,
        # it's a FridgeGrow device regardless of other labels
        if device_labels:
            label_names = [lbl.get("name", "").lower() for lbl in device_labels]
            fridgegrow_keywords = DEVICE_TYPE_MAPPING.get("FridgeGrow", [])
            
            if any(kw in label_names for kw in fridgegrow_keywords):
                detected_type = "FridgeGrow"
                detected_label = "FridgeGrow"
                _LOGGER.info(
                    f"Device '{device_name}' identified as FridgeGrow via label "
                    f"(labels: {label_names})"
                )
                
                DeviceClass = self.get_device_class(detected_type)
                return DeviceClass(
                    device_name,
                    device_data,
                    self.event_manager,
                    self.data_store,
                    detected_type,
                    self.room,
                    self.hass,
                    detected_label,
                    device_labels,
                )

        if device_labels:
            for lbl in device_labels:
                label_name = lbl.get("name", "").lower()
                if not label_name:
                    continue
                
                # First pass: Try EXACT match for special types (highest priority)
                for device_type in PRIORITY_DEVICE_TYPES:
                    keywords = DEVICE_TYPE_MAPPING.get(device_type, [])
                    # Check for exact match first
                    if label_name in keywords:
                        detected_type = device_type
                        detected_label = device_type
                        _LOGGER.info(
                            f"Device '{device_name}' identified via EXACT label match as {detected_type} (label: {label_name})"
                        )
                        break
                
                if detected_type:
                    break
                
                # Second pass: Check contains match with priority ordering (skip generic Light if special lights exist)
                for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                    if device_type in PRIORITY_DEVICE_TYPES:
                        continue  # Skip special lights, will check all labels next
                    if any(keyword == label_name for keyword in keywords):
                        # Exact keyword match
                        detected_type = device_type
                        detected_label = device_type
                        _LOGGER.info(
                            f"Device '{device_name}' identified via label keyword '{label_name}' as {detected_type}"
                        )
                        break
                
                if detected_type:
                    break

        # Fallback: No exact label match found, try contains matching with priority
        if not detected_type and device_labels:
            for lbl in device_labels:
                label_name = lbl.get("name", "").lower()
                if not label_name:
                    continue
                
                # Check priority types first (special lights before generic)
                for device_type in PRIORITY_DEVICE_TYPES:
                    keywords = DEVICE_TYPE_MAPPING.get(device_type, [])
                    if any(keyword in label_name for keyword in keywords):
                        detected_type = device_type
                        detected_label = device_type
                        _LOGGER.info(
                            f"Device '{device_name}' identified via label contains-match as {detected_type} (label: {label_name})"
                        )
                        break
                
                if detected_type:
                    break
                
                # Then check all other types
                for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                    if device_type in PRIORITY_DEVICE_TYPES:
                        continue  # Already checked
                    if any(keyword in label_name for keyword in keywords):
                        detected_type = device_type
                        detected_label = device_type
                        _LOGGER.debug(
                            f"Device '{device_name}' identified via label as {detected_type}"
                        )
                        break
                
                if detected_type:
                    break

        # Fallback: Name-based identification with priority ordering
        if not detected_type:
            device_name_lower = device_name.lower()
            
            # Check priority types first (special lights before generic Light)
            for device_type in PRIORITY_DEVICE_TYPES:
                keywords = DEVICE_TYPE_MAPPING.get(device_type, [])
                if any(keyword in device_name_lower for keyword in keywords):
                    detected_type = device_type
                    detected_label = device_type if not device_labels else device_labels[0].get("name", device_type)
                    _LOGGER.info(
                        f"Device '{device_name}' identified via name as {detected_type} (priority match)"
                    )
                    break
            
            # Then check all other types
            if not detected_type:
                for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                    if device_type in PRIORITY_DEVICE_TYPES:
                        continue  # Already checked
                    if any(keyword in device_name_lower for keyword in keywords):
                        detected_type = device_type
                        if device_labels:
                            detected_label = device_labels[0].get("name", "unknown")
                        else:
                            detected_label = "EMPTY"
                        _LOGGER.warning(
                            f"Device '{device_name}' identified via name as {detected_type}"
                        )
                        break

        if not detected_type:
            _LOGGER.error(
                f"Device '{device_name}' could not be identified. Returning generic Device."
            )
            return

        DeviceClass = self.get_device_class(detected_type)
        return DeviceClass(
            device_name,
            device_data,
            self.event_manager,
            self.data_store,
            detected_type,
            self.room,
            self.hass,
            detected_label,
            device_labels,
        )

    def get_device_class(self, device_type):
        """Geräteklasse erhalten."""
        if device_type == "Sensor":
            from ..OGBDevices.Sensor import Sensor
            return Sensor
        if device_type in ("ModbusSensor", "Modbus", "ModbusDevice"):
            from ..OGBDevices.ModbusSensor import ModbusSensor
            return ModbusSensor
        
        # Klassen ohne zyklische Abhängigkeiten
        device_classes = {
            "Humidifier": Humidifier,
            "Dehumidifier": Dehumidifier,
            "Exhaust": Exhaust,
            "Intake": Intake,
            "Ventilation": Ventilation,
            "Heater": Heater,
            "Cooler": Cooler,
            "LightFarRed": LightFarRed,
            "LightUV": LightUV,
            "LightBlue": LightBlue,
            "LightRed": LightRed,
            "Light": Light,
            "Climate": Climate,
            "Generic": GenericSwitch,
            "CO2": CO2,
            "Camera": Camera,
            "Fridge": Fridge,
            "Modbus": OGBModbusDevice,
            "ModbusDevice": OGBModbusDevice,
            "FridgeGrow": FridgeGrowDevice,
            "Pump": Pump,
        }
        return device_classes.get(device_type, Device)

    async def DeviceUpdater(self):
        controlOption = self.data_store.get("mainControl")

        groupedRoomEntities = (
            await self.regListener.get_filtered_entities_with_valueForDevice(
                self.room.lower()
            )
        )

        allDevices = [
            group for group in groupedRoomEntities if "ogb" not in group["name"].lower()
        ]
        self.data_store.setDeep("workData.Devices", allDevices)

        if controlOption not in ["HomeAssistant", "Premium"]:
            return False

        currentDevices = self.data_store.get("devices") or []
        deviceLabelIdent = self.data_store.get("DeviceLabelIdent")

        knownDeviceNames = {
            device.deviceName
            for device in currentDevices
            if hasattr(device, "deviceName")
        }

        realDeviceNames = {device["name"] for device in allDevices}

        newDevices = [
            device for device in allDevices if device["name"] not in knownDeviceNames
        ]

        removedDevices = [
            device
            for device in currentDevices
            if hasattr(device, "deviceName")
            and device.deviceName not in realDeviceNames
        ]

        # Geräte mit geänderten Labels erkennen (nur wenn DeviceLabelIdent aktiv ist)
        devicesToReidentify = []
        if deviceLabelIdent:
            for realDevice in allDevices:
                currentDevice = next(
                    (
                        d
                        for d in currentDevices
                        if hasattr(d, "deviceName")
                        and d.deviceName == realDevice["name"]
                    ),
                    None,
                )
                if currentDevice:
                    # Vergleiche Labels
                    realLabels = set(
                        lbl.get("name", "").lower()
                        for lbl in realDevice.get("labels", [])
                    )
                    currentLabel = getattr(currentDevice, "deviceLabel", "EMPTY")

                    # Bestimme das Label, das bei der aktuellen Identifizierung erkannt würde
                    # Use same priority logic as identify_device()
                    expected_label = self._determine_device_type_from_labels(
                        realDevice.get("labels", [])
                    )

                    # Nur neu identifizieren, wenn sich das erkannte Label tatsächlich geändert hat
                    if currentLabel != expected_label:
                        devicesToReidentify.append(realDevice)
                        _LOGGER.warning(
                            f"Device '{realDevice['name']}' label changed from '{currentLabel}' to '{expected_label}', will be re-identified"
                        )

        if removedDevices:
            _LOGGER.debug(f"Removing devices no longer found: {removedDevices}")
            for device in removedDevices:
                await self.removeDevice(device.deviceName)

        # Geräte mit geänderten Labels entfernen und neu hinzufügen
        if devicesToReidentify:
            _LOGGER.warning(
                f"Re-identifying {len(devicesToReidentify)} devices due to label changes"
            )
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

    def capCleaner(self, data):
        """Setzt alle Capabilities im DataStore auf den Ursprungszustand zurück."""
        capabilities = self.data_store.get("capabilities")

        self.data_store.set("devices", [])

        for key in capabilities:
            capabilities[key] = {"state": False, "count": 0, "devEntities": []}

        self.data_store.set("capabilities", capabilities)
        _LOGGER.debug(f"{self.room}: Cleared Caps and Devices")

    def deduplicateCapabilities(self):
        """
        Remove duplicate device entries from capabilities.
        Called on startup to clean up any existing duplicates.
        """
        capabilities = self.data_store.get("capabilities")
        if not capabilities:
            return
        
        cleaned = False
        for cap_name, cap_data in capabilities.items():
            if not isinstance(cap_data, dict):
                continue
            
            dev_entities = cap_data.get("devEntities", [])
            if not dev_entities:
                continue
            
            # Remove duplicates while preserving order
            unique_entities = list(dict.fromkeys(dev_entities))
            
            if len(unique_entities) != len(dev_entities):
                _LOGGER.warning(
                    f"{self.room}: Cleaning duplicates in {cap_name}: "
                    f"{len(dev_entities)} -> {len(unique_entities)} devices"
                )
                cap_data["devEntities"] = unique_entities
                cap_data["count"] = len(unique_entities)
                cap_data["state"] = len(unique_entities) > 0
                cleaned = True
        
        if cleaned:
            self.data_store.set("capabilities", capabilities)
            _LOGGER.info(f"{self.room}: Capability duplicates cleaned")

    def _determine_device_type_from_labels(self, labels: list) -> str:
        """
        Determine device type from labels using priority-based matching.
        
        Special light types (LightFarRed, LightUV, etc.) must be matched
        before generic Light type.
        """
        
        # Priority-ordered list - special types before generic (includes LightSpectrum)
        PRIORITY_DEVICE_TYPES = [
            "LightFarRed",
            "LightUV",
            "LightBlue",
            "LightRed",
            "LightSpectrum",  # Handles blue/red spectrum lights
        ]
        
        for lbl in labels:
            label_name = lbl.get("name", "").lower()
            if not label_name:
                continue
            
            # First: Check exact match for priority types
            for device_type in PRIORITY_DEVICE_TYPES:
                keywords = DEVICE_TYPE_MAPPING.get(device_type, [])
                if label_name in keywords:
                    return device_type
            
            # Second: Check exact match for all types
            for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                if label_name in keywords:
                    return device_type

            # Check if we have special light labels - if so, don't fall back to generic Light
            special_light_labels = [lbl.get("name", "").lower() for lbl in labels if lbl.get("name", "").lower() in ["light_blue", "light_red", "light_uv", "light_fr", "light_farred", "light_uvb", "light_uva", "uvlight"]]
            if any(keyword in special_light_labels for keyword in ["blue", "red", "uv", "uvb", "uva", "farred"]):
                # Already identified as special light, don't fall back to generic Light
                return "Light", None  # Return None for detected_label to avoid overwriting

            # Third: Contains matching with priority ordering
        for lbl in labels:
            label_name = lbl.get("name", "").lower()
            if not label_name:
                continue
            
            # Check priority types first
            for device_type in PRIORITY_DEVICE_TYPES:
                keywords = DEVICE_TYPE_MAPPING.get(device_type, [])
                if any(keyword in label_name for keyword in keywords):
                    return device_type
            
            # Then check all other types
            for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                if device_type in PRIORITY_DEVICE_TYPES:
                    continue
                if any(keyword in label_name for keyword in keywords):
                    return device_type
        
        return "EMPTY"

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
from ..OGBDevices.Door import Door
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
from ..OGBDevices.Window import Window
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

        self.is_initialized = False
        self._devicerefresh_task: asyncio.Task | None = None
        self.init()

        # EVENTS
        self.event_manager.on("capClean", self.capCleaner)

    def init(self):
        """initialized Device Manager."""
        # Clean up any duplicate capabilities from previous sessions
        self.deduplicateCapabilities()
        # DON'T start device_Worker() yet - it will be started after coordinator finishes initialization
        # This prevents race condition with coordinator's parallel device setup
        self.is_initialized = True
        _LOGGER.debug("OGBDeviceManager initialized (periodic refresh not started yet)")

    def start_periodic_refresh(self):
        """Start the periodic device refresh worker.
        
        CRITICAL: This should be called AFTER the coordinator has completed
        initial device setup to prevent race conditions and duplicate device creation.
        """
        if self._devicerefresh_task and not self._devicerefresh_task.done():
            _LOGGER.debug("Device refresh task is already running. Skipping start.")
            return
        
        _LOGGER.info(f"🔄 {self.room}: Starting periodic device refresh")
        self.device_Worker()

    async def setupDevice(self, device):
        controlOption = self.data_store.get("mainControl")
        device_name = device.get("name", "unknown")
        entity_ids = [entity.get("entity_id") for entity in device.get("entities", [])]

        if controlOption not in ["HomeAssistant", "Premium"]:
            _LOGGER.warning(f"Device setup skipped - mainControl '{controlOption}' not valid")
            return False

        _LOGGER.info(f"🔧 Setting up device: {device_name}")

        try:
            identified_device = await self.addDevice(device)
            if not identified_device:
                _LOGGER.warning(
                    f"⚠️ {self.room}: Device setup skipped/failed for '{device_name}'"
                )
                self._record_failed_device(device, "identify_or_add_failed")
                return False

            self._clear_failed_device(device_name)
            _LOGGER.info(f"✅ Device setup completed: {device_name}")
            return True
        except Exception as e:
            _LOGGER.error(
                f"❌ {self.room}: Device setup failed for '{device_name}' with entities {entity_ids}: {e}",
                exc_info=True,
            )
            self._record_failed_device(device, str(e))
            return False

    async def addDevice(self, device):
        """Gerät aus eigener Geräteliste hinzufügen."""
        logging.debug(f"DEVICE:{device}")

        deviceName = device.get("name", "unknown_device")
        deviceData = device.get("entities", [])

        # Duplikat-Check – selber Name darf nicht zweimal rein
        current_devices = self.data_store.get("devices") or []
        if any(getattr(d, "deviceName", None) == deviceName for d in current_devices):
            _LOGGER.debug(f"{self.room}: Device '{deviceName}' bereits in devices – addDevice abgebrochen")
            return None

        allLabels = []
        deviceLabels = device.get("labels", [])

        # Labels direkt am Device (ohne entity_id)
        for lbl in deviceLabels:
            new_lbl = {
                "id": lbl.get("id"),
                "name": lbl.get("name"),
                "scope": lbl.get("scope", "device"),
                "entity": None,
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
            return None

        _LOGGER.debug(f"Device:->{identified_device} identification Success")

        # Nochmal prüfen – Race-Condition zwischen identify und append
        current_devices = self.data_store.get("devices") or []
        if any(getattr(d, "deviceName", None) == deviceName for d in current_devices):
            _LOGGER.warning(f"{self.room}: Device '{deviceName}' wurde während identify hinzugefügt – abgebrochen")
            return None

        current_devices.append(identified_device)
        self.data_store.set("devices", current_devices)

        _LOGGER.info(f"Added new device From List: {identified_device}")
        return identified_device

    def _record_failed_device(self, device, error):
        failed_devices = self.data_store.getDeep("workData.failedDevices") or {}
        device_name = device.get("name", "unknown")
        failed_devices[device_name] = {
            "error": str(error),
            "entity_ids": [
                entity.get("entity_id") for entity in device.get("entities", [])
            ],
            "platforms": sorted(
                {
                    entity.get("platform", "unknown")
                    for entity in device.get("entities", [])
                }
            ),
        }
        self.data_store.setDeep("workData.failedDevices", failed_devices)

    def _clear_failed_device(self, device_name):
        failed_devices = self.data_store.getDeep("workData.failedDevices") or {}
        if device_name in failed_devices:
            failed_devices.pop(device_name, None)
            self.data_store.setDeep("workData.failedDevices", failed_devices)

    async def removeDevice(self, deviceName: str):
        """Entfernt ein Gerät anhand des Gerätenamens aus der Geräteliste."""

        controlOption = self.data_store.get("mainControl")
        devices = self.data_store.get("devices")

        if controlOption not in ["HomeAssistant", "Premium"]:
            return False

        # Add-on Sensoren sollen nicht entfernt werden
        if any(
            deviceName.endswith(suffix)
            for suffix in ["_humidity", "_temperature", "_dewpoint", "_co2"]
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

                    # Remove from deviceData
                    if "deviceData" in currentCap and deviceToRemove.deviceName in currentCap["deviceData"]:
                        del currentCap["deviceData"][deviceToRemove.deviceName]

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
                        _LOGGER.warning(
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
                        _LOGGER.debug(
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
            "Window": Window,
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
            "Door": Door,
            "Fridge": Fridge,
            "Modbus": OGBModbusDevice,
            "ModbusDevice": OGBModbusDevice,
            "FridgeGrow": FridgeGrowDevice,
            "Pump": Pump,
            # Pump types - all use the same Pump class
            "FeedPump": Pump,
            "ReservoirPump": Pump,
            "RetrievePump": Pump,
            "WateringPump": Pump,
            "AeroPump": Pump,
            "DWCPump": Pump,
            "ClonerPump": Pump,
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
                    currentLabel = getattr(currentDevice, "deviceLabel", "EMPTY")
                    expected_label = self._determine_device_type_from_labels(
                        realDevice.get("labels", [])
                    )

                    normalized_current = self._normalize_device_label_for_compare(
                        currentLabel
                    )
                    normalized_expected = self._normalize_device_label_for_compare(
                        expected_label
                    )

                    # Re-Identification nur wenn neuer Label gültig UND unterschiedlich
                    # Wenn neuer Label EMPTY ist (keine Labels), aber aktueller gültig → NICHT re-identifizieren
                    if normalized_expected != "EMPTY" and normalized_current != normalized_expected:
                        devicesToReidentify.append(realDevice)
                        _LOGGER.warning(
                            f"Device '{realDevice['name']}' label changed from '{currentLabel}' to '{expected_label}' "
                            f"(normalized: '{normalized_current}' -> '{normalized_expected}'), will be re-identified"
                        )

        if removedDevices:
            _LOGGER.debug(f"Removing devices no longer found: {removedDevices}")
            for device in removedDevices:
                await self.removeDevice(device.deviceName)

        # Geräte mit geänderten Labels entfernen und neu hinzufügen
        reidentify_names = set()  # ← immer initialisieren, auch wenn devicesToReidentify leer
        if devicesToReidentify:
            _LOGGER.warning(
                f"Re-identifying {len(devicesToReidentify)} devices due to label changes"
            )
            for device in devicesToReidentify:
                reidentify_names.add(device["name"])
                await self.removeDevice(device["name"])
                await self.setupDevice(device)

        if newDevices:
            _LOGGER.warning(f"Found {len(newDevices)} new devices, initializing...")
            for device in newDevices:
                if device["name"] in reidentify_names:  # ← verhindert doppeltes Init
                    _LOGGER.debug(f"'{device['name']}' bereits via reidentify hinzugefügt – übersprungen")
                    continue
                _LOGGER.debug(f"Registering new device: {device}")
                await self.setupDevice(device)
        else:
            _LOGGER.warning("Device-Check: No new devices found.")
    
    def device_Worker(self):
        if self._devicerefresh_task and not self._devicerefresh_task.done():
            _LOGGER.debug("Device refresh task is already running. Skipping start.")
            return

        async def periodicWorker():
            # ARCHITECTURAL FIX: Start periodic refresh loop AFTER coordinator setup
            # Don't call DeviceUpdater() immediately - devices are already initialized
            # by coordinator. Only run periodic refresh to detect new/removed devices.
            _LOGGER.info(f"{self.room}: Periodic device refresh loop started")

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
            capabilities[key] = {"state": False, "count": 0, "devEntities": [], "deviceData": {}}

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
        priority_device_types = [
            "LightFarRed",
            "LightUV",
            "LightBlue",
            "LightRed",
            "Window",
            "Door",
        ]

        # Exact matches first
        for lbl in labels:
            label_name = lbl.get("name", "").lower()
            if not label_name:
                continue

            for device_type in priority_device_types:
                keywords = DEVICE_TYPE_MAPPING.get(device_type, [])
                if label_name in keywords:
                    return device_type

            for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                if label_name in keywords:
                    return device_type

        # Fallback contains matching
        for lbl in labels:
            label_name = lbl.get("name", "").lower()
            if not label_name:
                continue

            for device_type in priority_device_types:
                keywords = DEVICE_TYPE_MAPPING.get(device_type, [])
                if any(keyword in label_name for keyword in keywords):
                    return device_type

            for device_type, keywords in DEVICE_TYPE_MAPPING.items():
                if device_type in priority_device_types:
                    continue
                if any(keyword in label_name for keyword in keywords):
                    return device_type

        return "EMPTY"

    def _normalize_device_label_for_compare(self, label: str) -> str:
        """Normalize free-form labels to canonical device-type labels for stable compare."""
        if not label:
            return "EMPTY"

        label_lower = str(label).strip().lower()
        if not label_lower:
            return "EMPTY"

        # Direct device-type name (e.g. "Exhaust")
        for device_type in DEVICE_TYPE_MAPPING:
            if label_lower == device_type.lower():
                return device_type

        # Exact keyword match
        for device_type, keywords in DEVICE_TYPE_MAPPING.items():
            if any(label_lower == str(keyword).lower() for keyword in keywords):
                return device_type

        # Contains keyword match (fallback)
        for device_type, keywords in DEVICE_TYPE_MAPPING.items():
            if any(str(keyword).lower() in label_lower for keyword in keywords):
                return device_type

        # Keep unknown/context labels (e.g. Medium_1) as EMPTY for reidentify compare
        return "EMPTY"

"""OpenGrowBox Device Recognition and Auto-Discovery System.

Handles automatic device discovery via Zeroconf, network scanning, and Bluetooth LE.
Provides device proposals to the frontend and supports direct API communication
as fallback when Home Assistant is unavailable.
"""

import asyncio
import json
import logging
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from .....const import DOMAIN
from ....utils.ambient import is_ambient_room

_LOGGER = logging.getLogger(__name__)

# Device name blacklist - these services/devices are never useful for OGB
_DISCOVERY_BLACKLIST = {
    "octoprint",
    "openmediavault",
    "plex",
    "homeassistant",
    "hass",
}

# Active device types that are blocked for ambient rooms
_AMBIENT_BLOCKED_DEVICE_TYPES = {"switch", "light", "relay", "pump", "fan", "valve", "climate", "cover", "generic_http"}


@dataclass
class DeviceProposal:
    """Represents a discovered device waiting for user confirmation."""
    
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    device_type: str = ""  # switch, sensor, light, pump, etc.
    manufacturer: str = ""  # Tasmota, Shelly, ESPHome, Govee
    model: str = ""
    capabilities: List[str] = field(default_factory=list)
    suggested_room: Optional[str] = None
    discovery_method: str = ""  # zeroconf, network_scan, bluetooth, ha_event
    confidence_score: float = 0.0
    raw_data: Dict[str, Any] = field(default_factory=dict)
    preview_entities: List[Dict[str, Any]] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "pending"  # pending, accepted, ignored, expired


class OGBDeviceRecognitionManager:
    """Manages automatic device discovery and direct API communication."""
    
    def __init__(self, hass, data_store, event_manager, room, config_entry_id=None):
        """Initialize the device recognition manager.
        
        Args:
            hass: Home Assistant instance
            data_store: Reference to the data store
            event_manager: Reference to the event manager
            room: Room identifier
            config_entry_id: Home Assistant config entry ID for device registry
        """
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.config_entry_id = config_entry_id
        
        # Discovery state
        self._proposals: Dict[str, DeviceProposal] = {}
        self._discovered_devices: Dict[str, Dict[str, Any]] = {}  # Persisted discoveries
        self._ignored_devices: Set[str] = set()  # MAC addresses or IDs to ignore
        self._shutdown = False
        
        # Discovery engines
        self._zeroconf_discovery = None
        self._network_scanner = None
        self._bluetooth_discovery = None
        
        # Direct API controller
        self._direct_api = None
        
        # Background tasks
        self._discovery_task = None
        self._cleanup_task = None
        
        _LOGGER.info(f"[{self.room}] OGBDeviceRecognitionManager initialized")
    
    async def start_discovery(self):
        """Start all discovery engines."""
        if self._shutdown:
            return
        
        _LOGGER.info(f"[{self.room}] Starting device discovery")
        
        # Start discovery engines based on configuration
        config = self.data_store.getDeep("config.discovery", {})
        
        if config.get("zeroconf_enabled", True):
            await self._start_zeroconf()
        
        if config.get("network_scan_enabled", True):
            await self._start_network_scanner()
        
        if config.get("bluetooth_enabled", True):
            await self._start_bluetooth_discovery()
        
        # Start background tasks
        self._discovery_task = asyncio.create_task(self._discovery_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def _start_zeroconf(self):
        """Start Zeroconf/mDNS discovery."""
        try:
            from .discovery.zeroconf_discovery import ZeroconfDiscovery

            self._zeroconf_discovery = ZeroconfDiscovery(
                self._on_device_discovered,
                self.room,
                self.hass
            )
            await self._zeroconf_discovery.start()
            _LOGGER.warning(f"[{self.room}] Zeroconf discovery started")
        except ImportError:
            _LOGGER.warning(f"[{self.room}] Zeroconf not available, skipping")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error starting Zeroconf: {e}")
    
    async def _start_network_scanner(self):
        """Start network IP range scanner."""
        try:
            from .discovery.network_scanner import NetworkScanner
            
            config = self.data_store.getDeep("config.discovery", {})
            ip_ranges = config.get("ip_ranges", ["192.168.1.0/24"])
            
            self._network_scanner = NetworkScanner(
                self._on_device_discovered,
                ip_ranges,
                self.room
            )
            await self._network_scanner.start()
            _LOGGER.warning(f"[{self.room}] Network scanner started")
        except ImportError:
            _LOGGER.warning(f"[{self.room}] Network scanner not available")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error starting network scanner: {e}")
    
    async def _start_bluetooth_discovery(self):
        """Start Bluetooth LE discovery."""
        try:
            from .discovery.bluetooth_discovery import BluetoothDiscovery
            
            self._bluetooth_discovery = BluetoothDiscovery(
                self._on_device_discovered,
                self.room
            )
            await self._bluetooth_discovery.start()
            _LOGGER.warning(f"[{self.room}] Bluetooth discovery started")
        except ImportError:
            _LOGGER.warning(f"[{self.room}] Bluetooth not available, skipping")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error starting Bluetooth discovery: {e}")
    
    async def _on_device_discovered(self, discovery_data: Dict[str, Any]):
        """Handle discovered device from any discovery engine.
        
        Args:
            discovery_data: Raw discovery data from the engine
        """
        try:
            # Check if device is ignored
            device_id = discovery_data.get("mac") or discovery_data.get("ip")
            if device_id and device_id in self._ignored_devices:
                return
            
            # Try to recognize the device
            proposal = await self._recognize_device(discovery_data)
            
            # If not recognized by any recognizer, create a minimal generic proposal
            # so blacklist, ambient filter, and frontend notifications still work
            if not proposal:
                proposal = self._create_generic_proposal(discovery_data)
                if not proposal:
                    return
            
            # Check blacklist by device name
            name_lower = proposal.name.lower()
            for blocked in _DISCOVERY_BLACKLIST:
                if blocked in name_lower:
                    _LOGGER.warning(
                        f"[{self.room}] BLOCKED device (blacklist): {proposal.name} "
                        f"({proposal.manufacturer}) via {proposal.discovery_method}"
                    )
                    return
            
            # For ambient rooms, only allow sensor-type devices
            if is_ambient_room(self.room) and proposal.device_type in _AMBIENT_BLOCKED_DEVICE_TYPES:
                _LOGGER.warning(
                    f"[{self.room}] BLOCKED active device for ambient: {proposal.name} "
                    f"(type={proposal.device_type}) via {proposal.discovery_method}"
                )
                return
            
            # Check if we already have this device
            if device_id and device_id in self._discovered_devices:
                existing = self._discovered_devices[device_id]
                if existing.get("status") == "accepted":
                    # Update existing device info
                    await self._update_existing_device(device_id, proposal)
                    return
            
            # Add to proposals
            self._proposals[proposal.id] = proposal
            
            # Emit event to frontend
            await self.event_manager.emit(
                "DeviceDiscovered",
                {
                    "room": self.room,
                    "proposal": {
                        "id": proposal.id,
                        "name": proposal.name,
                        "device_type": proposal.device_type,
                        "manufacturer": proposal.manufacturer,
                        "model": proposal.model,
                        "capabilities": proposal.capabilities,
                        "suggested_room": proposal.suggested_room,
                        "confidence": proposal.confidence_score,
                        "preview_entities": proposal.preview_entities,
                        "discovery_method": proposal.discovery_method,
                    }
                }
            )
            
            # Also fire on HA event bus so the frontend can subscribe
            if self.hass:
                event_data = {
                    "room": self.room,
                    "proposal": {
                        "id": proposal.id,
                        "name": proposal.name,
                        "ip_address": proposal.ip_address,
                        "mac_address": proposal.mac_address,
                        "device_type": proposal.device_type,
                        "manufacturer": proposal.manufacturer,
                        "model": proposal.model,
                        "capabilities": proposal.capabilities,
                        "suggested_room": proposal.suggested_room,
                        "confidence": proposal.confidence_score,
                        "discovery_method": proposal.discovery_method,
                    }
                }
                self.hass.bus.async_fire("ogb_device_discovered", event_data)
            
            _LOGGER.warning(
                f"[{self.room}] New device proposal: {proposal.name} "
                f"({proposal.manufacturer} {proposal.model}) via {proposal.discovery_method}"
            )
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling discovered device: {e}")
    
    async def _recognize_device(self, discovery_data: Dict[str, Any]) -> Optional[DeviceProposal]:
        """Recognize device type and create proposal.
        
        Args:
            discovery_data: Raw discovery data
            
        Returns:
            DeviceProposal or None if not recognized
        """
        # Try each recognizer
        recognizers = [
            ("tasmota", self._recognize_tasmota),
            ("shelly", self._recognize_shelly),
            ("esphome", self._recognize_esphome),
            ("govee", self._recognize_govee),
        ]
        
        for name, recognizer in recognizers:
            try:
                proposal = await recognizer(discovery_data)
                if proposal:
                    return proposal
            except Exception as e:
                _LOGGER.debug(f"[{self.room}] {name} recognizer failed: {e}")
        
        return None
    
    def _create_generic_proposal(self, discovery_data: Dict[str, Any]) -> Optional[DeviceProposal]:
        """Create a minimal proposal for unrecognized devices.
        
        This ensures all discovered devices (even unknown ones) go through
        the blacklist, ambient filter, and frontend notification pipeline.
        
        Args:
            discovery_data: Raw discovery data from the engine
            
        Returns:
            DeviceProposal or None if data is insufficient
        """
        name = discovery_data.get("name", "")
        ip = discovery_data.get("ip", "")
        if not name and not ip:
            return None
        
        service_type = discovery_data.get("service_type", "")
        device_type = discovery_data.get("type", "") or "generic_http"
        
        # Clean up service name for display (remove service type suffix)
        display_name = name
        if service_type:
            suffix = f".{service_type}"
            if display_name.endswith(suffix):
                display_name = display_name[:-len(suffix)]
        
        # Extract hostname from name if possible
        hostname = discovery_data.get("hostname", display_name.split(".")[0] if "." in display_name else display_name)
        
        _LOGGER.debug(
            f"[{self.room}] Created generic proposal for: {display_name} "
            f"(type={device_type}, ip={ip})"
        )
        
        return DeviceProposal(
            name=display_name,
            ip_address=ip if ip else None,
            device_type=device_type,
            manufacturer=device_type.capitalize(),
            model=hostname,
            capabilities=[],
            discovery_method=discovery_data.get("method", "unknown"),
            confidence_score=0.3,
            raw_data=discovery_data,
        )
    
    async def _recognize_tasmota(self, data: Dict[str, Any]) -> Optional[DeviceProposal]:
        """Recognize Tasmota device."""
        if data.get("type") != "tasmota":
            return None
        
        ip = data.get("ip")
        if not ip:
            return None
        
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"http://{ip}/cm?cmnd=Status%200") as resp:
                    if resp.status != 200:
                        return None
                    status = await resp.json()
            
            status_net = status.get("StatusNET", {})
            status_stk = status.get("StatusSTS", {})
            status_fwr = status.get("StatusFWR", {})
            
            # Determine capabilities
            capabilities = ["switch", "relay"]
            
            # Check for sensors
            if "StatusSNS" in status:
                sensors = status["StatusSNS"]
                if any(k in sensors for k in ["ENERGY", "Power", "Voltage"]):
                    capabilities.append("power_metering")
                if any(k in sensors for k in ["DHT", "SI7021", "BME280"]):
                    capabilities.append("temperature")
                    capabilities.append("humidity")
            
            # Check for power monitoring
            if "StatusSTS" in status and any(k in status_stk for k in ["POWER", "ENERGY"]):
                capabilities.append("power_metering")
            
            # Preview entities
            preview = []
            relays = status_stk.get("POWER", "")
            if isinstance(relays, str):
                preview.append({
                    "entity_id": f"switch.tasmota_{status_net.get('Mac', '').replace(':', '')[-6:]}",
                    "type": "switch",
                    "name": "Relay"
                })
            elif isinstance(relays, dict):
                for i in range(len(relays)):
                    preview.append({
                        "entity_id": f"switch.tasmota_{i+1}",
                        "type": "switch",
                        "name": f"Relay {i+1}"
                    })
            
            if "power_metering" in capabilities:
                preview.append({
                    "entity_id": f"sensor.tasmota_power",
                    "type": "sensor",
                    "name": "Power",
                    "unit": "W"
                })
            
            return DeviceProposal(
                name=f"Tasmota {status_net.get('Hostname', 'Device')}",
                ip_address=ip,
                mac_address=status_net.get("Mac"),
                device_type="switch",
                manufacturer="Tasmota",
                model=status_fwr.get("Version", "Unknown"),
                capabilities=list(set(capabilities)),
                suggested_room=self._infer_room_from_name(status_net.get("Hostname", "")),
                discovery_method=data.get("method", "network_scan"),
                confidence_score=0.95,
                raw_data=status,
                preview_entities=preview
            )
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Tasmota recognition failed for {ip}: {e}")
            return None
    
    async def _recognize_shelly(self, data: Dict[str, Any]) -> Optional[DeviceProposal]:
        """Recognize Shelly device."""
        if data.get("type") != "shelly":
            return None
        
        ip = data.get("ip")
        if not ip:
            return None
        
        try:
            import aiohttp
            
            # Try Gen2 API first
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                try:
                    async with session.post(
                        f"http://{ip}/rpc",
                        json={"id": 1, "method": "Shelly.GetDeviceInfo"}
                    ) as resp:
                        if resp.status == 200:
                            info = await resp.json()
                            return await self._parse_shelly_gen2(ip, info, data)
                except:
                    pass
                
                # Fallback to Gen1
                async with session.get(f"http://{ip}/shelly") as resp:
                    if resp.status == 200:
                        info = await resp.json()
                        return await self._parse_shelly_gen1(ip, info, data)
            
            return None
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Shelly recognition failed for {ip}: {e}")
            return None
    
    async def _parse_shelly_gen2(self, ip: str, info: Dict, data: Dict) -> DeviceProposal:
        """Parse Shelly Gen2 device info."""
        result = info.get("result", {})
        
        capabilities = ["switch", "relay"]
        if "pm" in result.get("app", "") or "plus" in result.get("app", ""):
            capabilities.append("power_metering")
        
        preview = []
        for i in range(result.get("num_outputs", 1)):
            preview.append({
                "entity_id": f"switch.shelly_{result.get('id', 'unknown')}_channel_{i}",
                "type": "switch",
                "name": f"Channel {i}"
            })
        
        if "power_metering" in capabilities:
            preview.append({
                "entity_id": f"sensor.shelly_{result.get('id', 'unknown')}_power",
                "type": "sensor",
                "name": "Power",
                "unit": "W"
            })
        
        return DeviceProposal(
            name=f"Shelly {result.get('name', result.get('app', 'Device'))}",
            ip_address=ip,
            mac_address=result.get("mac"),
            device_type="switch",
            manufacturer="Shelly",
            model=result.get("app", "Unknown"),
            capabilities=list(set(capabilities)),
            suggested_room=self._infer_room_from_name(result.get("name", "")),
            discovery_method=data.get("method", "network_scan"),
            confidence_score=0.95,
            raw_data=info,
            preview_entities=preview
        )
    
    async def _parse_shelly_gen1(self, ip: str, info: Dict, data: Dict) -> DeviceProposal:
        """Parse Shelly Gen1 device info."""
        capabilities = ["switch", "relay"]
        if info.get("type") in ["SHPLG-S", "SHPLG2-1", "SHPLG-U1"]:
            capabilities.append("power_metering")
        
        preview = []
        num_outputs = info.get("num_outputs", 1)
        for i in range(num_outputs):
            preview.append({
                "entity_id": f"switch.shelly_{info.get('mac', '').replace(':', '')[-6:]}_{i}",
                "type": "switch",
                "name": f"Relay {i}"
            })
        
        if "power_metering" in capabilities:
            preview.append({
                "entity_id": f"sensor.shelly_{info.get('mac', '').replace(':', '')[-6:]}_power",
                "type": "sensor",
                "name": "Power",
                "unit": "W"
            })
        
        return DeviceProposal(
            name=f"Shelly {info.get('type', 'Device')}",
            ip_address=ip,
            mac_address=info.get("mac"),
            device_type="switch",
            manufacturer="Shelly",
            model=info.get("type", "Unknown"),
            capabilities=list(set(capabilities)),
            suggested_room=None,
            discovery_method=data.get("method", "network_scan"),
            confidence_score=0.9,
            raw_data=info,
            preview_entities=preview
        )
    
    async def _recognize_esphome(self, data: Dict[str, Any]) -> Optional[DeviceProposal]:
        """Recognize ESPHome device."""
        if data.get("type") != "esphome":
            return None
        
        ip = data.get("ip")
        if not ip:
            return None
        
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"http://{ip}") as resp:
                    if resp.status != 200:
                        return None
                    text = await resp.text()
                    
                    # Check for ESPHome signature
                    if "esphome" not in text.lower() and "esp" not in text.lower():
                        return None
            
            # Try to get device info via API
            # ESPHome native API requires special client, use basic info
            return DeviceProposal(
                name=f"ESPHome {data.get('hostname', 'Device')}",
                ip_address=ip,
                device_type="sensor",
                manufacturer="ESPHome",
                model="Custom",
                capabilities=["sensor", "custom"],
                suggested_room=self._infer_room_from_name(data.get("hostname", "")),
                discovery_method=data.get("method", "zeroconf"),
                confidence_score=0.7,
                raw_data=data
            )
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] ESPHome recognition failed for {ip}: {e}")
            return None
    
    async def _recognize_govee(self, data: Dict[str, Any]) -> Optional[DeviceProposal]:
        """Recognize Govee device via Bluetooth."""
        if data.get("type") != "govee":
            return None
        
        try:
            # Govee devices broadcast via BLE
            capabilities = ["light", "led"]
            
            # Check for sensor variants
            model = data.get("model", "")
            if "H507" in model or "H510" in model:
                capabilities = ["temperature", "humidity", "sensor"]
            
            return DeviceProposal(
                name=f"Govee {data.get('name', 'Device')}",
                mac_address=data.get("mac"),
                device_type="light" if "light" in capabilities else "sensor",
                manufacturer="Govee",
                model=model,
                capabilities=capabilities,
                suggested_room=self.room,
                discovery_method="bluetooth",
                confidence_score=0.85,
                raw_data=data
            )
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Govee recognition failed: {e}")
            return None
    
    def _infer_room_from_name(self, name: str) -> Optional[str]:
        """Try to infer room from device name."""
        if not name:
            return None
        
        name_lower = name.lower()
        room_keywords = {
            "living": "Living Room",
            "bedroom": "Bedroom",
            "kitchen": "Kitchen",
            "bathroom": "Bathroom",
            "office": "Office",
            "garage": "Garage",
            "basement": "Basement",
            "tent": self.room,  # Grow tents
            "grow": self.room,
            "flower": self.room,
            "veg": self.room,
        }
        
        for keyword, room in room_keywords.items():
            if keyword in name_lower:
                return room
        
        return None
    
    async def _update_existing_device(self, device_id: str, proposal: DeviceProposal):
        """Update info for an already accepted device."""
        # Update IP if changed
        existing = self._discovered_devices.get(device_id, {})
        if proposal.ip_address and existing.get("ip") != proposal.ip_address:
            existing["ip"] = proposal.ip_address
            existing["last_seen"] = datetime.now(timezone.utc).isoformat()
            self._discovered_devices[device_id] = existing
            
            _LOGGER.debug(
                f"[{self.room}] Updated IP for {existing.get('name')}: "
                f"{proposal.ip_address}"
            )
    
    async def accept_proposal(self, proposal_id: str, room: str = None, labels: list = None, name: str = None) -> bool:
        """Accept a device proposal and register it.
        
        Args:
            proposal_id: ID of the proposal to accept
            room: Target room (from ogb_rooms), optional
            labels: List of labels to apply (e.g. ["light", "ogb_device"]), optional
            name: Custom device name, optional
            
        Returns:
            True if successful
        """
        try:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                _LOGGER.warning(f"[{self.room}] Proposal {proposal_id} not found")
                return False
            
            proposal.status = "accepted"
            target_room = room or proposal.suggested_room or self.room
            device_name = name or proposal.name
            
            # Register in HA with labels and area
            await self._register_in_ha(proposal, target_room, labels or [], device_name)
            
            # Store as discovered device
            device_key = proposal.mac_address or proposal.ip_address or proposal_id
            self._discovered_devices[device_key] = {
                "id": proposal_id,
                "name": proposal.name,
                "ip": proposal.ip_address,
                "mac": proposal.mac_address,
                "type": proposal.device_type,
                "manufacturer": proposal.manufacturer,
                "model": proposal.model,
                "capabilities": proposal.capabilities,
                "room": target_room,
                "labels": labels or [],
                "status": "accepted",
                "accepted_at": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
            }
            
            # Remove from proposals
            del self._proposals[proposal_id]
            
            # Emit event
            await self.event_manager.emit(
                "DeviceAccepted",
                {
                    "room": target_room,
                    "device": {
                        "id": proposal_id,
                        "name": proposal.name,
                        "type": proposal.device_type,
                        "manufacturer": proposal.manufacturer,
                        "labels": labels or [],
                    }
                }
            )
            
            _LOGGER.info(f"[{self.room}] Accepted device: {proposal.name} in room {target_room} with labels {labels}")
            return True
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error accepting proposal {proposal_id}: {e}")
            return False
    
    async def ignore_proposal(self, proposal_id: str) -> bool:
        """Ignore a device proposal.
        
        Args:
            proposal_id: ID of the proposal to ignore
            
        Returns:
            True if successful
        """
        try:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return False
            
            proposal.status = "ignored"
            
            # Add to ignore list
            device_key = proposal.mac_address or proposal.ip_address
            if device_key:
                self._ignored_devices.add(device_key)
            
            # Remove from proposals
            del self._proposals[proposal_id]
            
            _LOGGER.info(f"[{self.room}] Ignored device: {proposal.name}")
            return True
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error ignoring proposal {proposal_id}: {e}")
            return False
    
    async def _auto_setup_device(self, proposal: DeviceProposal) -> bool:
        """Try to auto-setup device via its native integration.
        
        Returns:
            True if auto-setup was triggered
        """
        try:
            if not self.hass:
                return False
            
            # Check if we can auto-setup this device type
            if proposal.manufacturer and "shelly" in proposal.manufacturer.lower():
                return await self._setup_shelly(proposal)
            elif proposal.manufacturer and "tasmota" in proposal.manufacturer.lower():
                return await self._setup_tasmota(proposal)
            elif proposal.manufacturer and "esphome" in proposal.manufacturer.lower():
                return await self._setup_esphome(proposal)
            elif proposal.manufacturer and "govee" in proposal.manufacturer.lower():
                return await self._setup_govee(proposal)
            
            return False
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Auto-setup failed: {e}")
            return False
    
    async def _setup_shelly(self, proposal: DeviceProposal) -> bool:
        """Trigger Shelly integration config flow."""
        try:
            # Get device info from raw data
            raw = proposal.raw_data or {}
            host = proposal.ip_address
            device_id = raw.get("id") or proposal.mac_address
            model = raw.get("app", "").replace("shelly", "").upper()
            gen = 2 if "plus" in raw.get("app", "") or "pro" in raw.get("app", "") else 1
            
            if not host:
                return False
            
            _LOGGER.info(f"[{self.room}] Triggering Shelly auto-setup for {proposal.name} at {host}")
            
            # Try to use real ZeroconfServiceInfo if available
            try:
                from homeassistant.components.zeroconf import ZeroconfServiceInfo
                
                discovery_info = ZeroconfServiceInfo(
                    host=host,
                    addresses=[host],
                    port=raw.get("port", 80) if isinstance(raw, dict) else 80,
                    hostname=raw.get("hostname", "") if isinstance(raw, dict) else proposal.name,
                    type="_http._tcp.local.",
                    name=raw.get("hostname", proposal.name) if isinstance(raw, dict) else proposal.name,
                    properties={
                        "id": device_id,
                        "arch": raw.get("app", "") if isinstance(raw, dict) else "",
                        "fw_id": raw.get("fw_id", "") if isinstance(raw, dict) else "",
                        "gen": str(gen),
                    }
                )
                _LOGGER.info(f"[{self.room}] Using real ZeroconfServiceInfo for Shelly")
            except (ImportError, TypeError) as import_err:
                _LOGGER.info(f"[{self.room}] ZeroconfServiceInfo not available ({import_err}), using mock")
                # Fallback: Create comprehensive mock with ALL possible attributes
                from types import SimpleNamespace
                
                discovery_info = SimpleNamespace()
                import ipaddress
                discovery_info.host = host
                discovery_info.ip_address = ipaddress.ip_address(host)
                discovery_info.addresses = [host]
                discovery_info.port = raw.get("port", 80) if isinstance(raw, dict) else 80
                discovery_info.hostname = raw.get("hostname", "") if isinstance(raw, dict) else proposal.name
                discovery_info.type = "_http._tcp.local."
                discovery_info.name = raw.get("hostname", proposal.name) if isinstance(raw, dict) else proposal.name
                discovery_info.properties = {
                    "id": device_id,
                    "arch": raw.get("app", "") if isinstance(raw, dict) else "",
                    "fw_id": raw.get("fw_id", "") if isinstance(raw, dict) else "",
                    "gen": str(gen),
                }
            
            # Trigger Shelly config flow
            _LOGGER.info(f"[{self.room}] Calling async_init for shelly with source=zeroconf")
            result = await self.hass.config_entries.flow.async_init(
                "shelly",
                context={"source": "zeroconf"},
                data=discovery_info
            )
            
            _LOGGER.info(f"[{self.room}] Shelly config flow result: {result}")
            return result is not None
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Shelly setup failed: {e}", exc_info=True)
            return False
    
    async def _setup_tasmota(self, proposal: DeviceProposal) -> bool:
        """Trigger Tasmota integration config flow."""
        try:
            raw = proposal.raw_data or {}
            host = proposal.ip_address
            
            if not host:
                return False
            
            _LOGGER.info(f"[{self.room}] Triggering Tasmota auto-setup for {proposal.name}")
            
            from types import SimpleNamespace
            discovery_info = SimpleNamespace()
            discovery_info.ip = host
            discovery_info.topic = raw.get("topic", "")
            discovery_info.device_name = proposal.name
            
            result = await self.hass.config_entries.flow.async_init(
                "tasmota",
                context={"source": "discovery"},
                data=discovery_info
            )
            
            return result is not None
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Tasmota setup failed: {e}")
            return False
    
    async def _setup_esphome(self, proposal: DeviceProposal) -> bool:
        """Trigger ESPHome integration config flow."""
        try:
            host = proposal.ip_address
            
            if not host:
                return False
            
            _LOGGER.info(f"[{self.room}] Triggering ESPHome auto-setup for {proposal.name}")
            
            from types import SimpleNamespace
            discovery_info = SimpleNamespace()
            discovery_info.host = host
            
            result = await self.hass.config_entries.flow.async_init(
                "esphome",
                context={"source": "discovery"},
                data=discovery_info
            )
            
            return result is not None
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] ESPHome setup failed: {e}")
            return False
    
    async def _setup_govee(self, proposal: DeviceProposal) -> bool:
        """Trigger Govee integration config flow."""
        try:
            raw = proposal.raw_data or {}
            
            # Govee uses Bluetooth MAC address
            mac = proposal.mac_address
            if not mac:
                return False
            
            _LOGGER.info(f"[{self.room}] Triggering Govee auto-setup for {proposal.name}")
            
            from types import SimpleNamespace
            discovery_info = SimpleNamespace()
            discovery_info.address = mac
            discovery_info.name = proposal.name
            discovery_info.model = proposal.model or raw.get("model", "")
            discovery_info.manufacturer_data = raw.get("manufacturer_data", {})
            
            result = await self.hass.config_entries.flow.async_init(
                "govee",
                context={"source": "bluetooth"},
                data=discovery_info
            )
            
            return result is not None
            
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Govee setup failed: {e}")
            return False
    
    async def _register_in_ha(self, proposal: DeviceProposal, room: str, labels: list, name: str = None):
        """Register accepted device in Home Assistant with labels and area.
        
        Args:
            proposal: The device proposal
            room: Target room name
            labels: List of labels to apply
            name: Custom device name
        """
        try:
            if not self.hass:
                return
            
            # First try auto-setup via native integration
            auto_setup = await self._auto_setup_device(proposal)
            if auto_setup:
                _LOGGER.info(f"[{self.room}] Auto-setup triggered for {proposal.name}, waiting for device to appear...")
                # Wait a moment for the device to be registered
                await asyncio.sleep(2)
            
            from homeassistant.helpers.label_registry import async_get as async_get_label_registry
            from homeassistant.helpers.area_registry import async_get as async_get_area_registry
            from homeassistant.helpers.device_registry import async_get as async_get_device_registry
            from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
            
            # Get registries
            label_registry = async_get_label_registry(self.hass)
            area_registry = async_get_area_registry(self.hass)
            device_registry = async_get_device_registry(self.hass)
            entity_registry = async_get_entity_registry(self.hass)
            
            device_name = name or proposal.name
            
            # Find or create area
            area = None
            for existing_area in area_registry.areas.values():
                if existing_area.name.lower() == room.lower():
                    area = existing_area
                    break
            
            if not area:
                area = area_registry.async_create(room)
                _LOGGER.debug(f"[{self.room}] Created area: {room}")
            
            # Ensure labels exist
            for label_name in labels:
                if label_name not in label_registry.labels:
                    label_registry.async_create(label_name)
            
            # Try to find existing device in HA registry
            # Shelly devices have identifiers like ("shelly", "shellyplus1-...")
            # not ("opengrowbox", ...), so we need to search by name or IP
            device = None
            
            _LOGGER.debug(
                f"[{self.room}] Searching for device: name={proposal.name}, "
                f"ip={proposal.ip_address}, mac={proposal.mac_address}"
            )
            
            # Method 1: Search by IP in connections
            if proposal.ip_address and not device:
                for dev in device_registry.devices.values():
                    if hasattr(dev, 'connections') and dev.connections:
                        for conn in dev.connections:
                            if len(conn) >= 2:
                                conn_type, conn_value = conn[0], conn[1]
                                if conn_type == "ip" and conn_value == proposal.ip_address:
                                    device = dev
                                    _LOGGER.debug(f"[{self.room}] Found device by IP: {dev.name} (ID: {dev.id})")
                                    break
                    if device:
                        break
            
            # Method 2: Search by name (partial match)
            if proposal.name and not device:
                search_name = proposal.name.lower()
                for dev in device_registry.devices.values():
                    dev_name = (dev.name or "").lower()
                    dev_name_by_user = (dev.name_by_user or "").lower()
                    if search_name in dev_name or search_name in dev_name_by_user:
                        device = dev
                        _LOGGER.debug(f"[{self.room}] Found device by name: {dev.name} (ID: {dev.id})")
                        break
            
            # Method 3: Search by hostname from raw_data
            if not device and proposal.raw_data and proposal.raw_data.get("hostname"):
                hostname = proposal.raw_data.get("hostname", "").lower()
                for dev in device_registry.devices.values():
                    dev_name = (dev.name or "").lower()
                    if hostname in dev_name:
                        device = dev
                        _LOGGER.debug(f"[{self.room}] Found device by hostname: {dev.name} (ID: {dev.id})")
                        break
            
            # Update device with area, labels and name
            if device:
                update_kwargs = {
                    "area_id": area.id,
                    "labels": set(labels) | set(device.labels or []),
                }
                if name:
                    update_kwargs["name_by_user"] = name
                
                device_registry.async_update_device(device.id, **update_kwargs)
                _LOGGER.info(f"[{self.room}] Updated existing device {device_name} (ID: {device.id}) with area {room}, labels {labels}" + (f" and name {name}" if name else ""))
            else:
                # Device not found in HA registry - create it!
                _LOGGER.info(
                    f"[{self.room}] Device {device_name} not found in HA registry, "
                    f"creating new device entry..."
                )
                
                # Build identifiers
                identifiers = set()
                if proposal.mac_address:
                    identifiers.add((DOMAIN, proposal.mac_address))
                if proposal.ip_address:
                    identifiers.add((DOMAIN, proposal.ip_address))
                if not identifiers:
                    identifiers.add((DOMAIN, proposal.id))
                
                # Build connections
                connections = set()
                if proposal.mac_address:
                    connections.add(("mac", proposal.mac_address))
                if proposal.ip_address:
                    connections.add(("ip", proposal.ip_address))
                
                # Create the device in HA registry
                new_device = device_registry.async_get_or_create(
                    config_entry_id=self.config_entry_id,
                    identifiers=identifiers,
                    connections=connections if connections else None,
                    name=device_name,
                    manufacturer=proposal.manufacturer or "Unknown",
                    model=proposal.model or proposal.device_type or "Unknown",
                    sw_version=proposal.raw_data.get("fw_version") if proposal.raw_data else None,
                    suggested_area=room,
                )
                
                # Set area and labels
                if new_device:
                    device_registry.async_update_device(
                        new_device.id,
                        area_id=area.id,
                        labels=set(labels),
                    )
                    _LOGGER.info(
                        f"[{self.room}] Created new HA device {device_name} "
                        f"(ID: {new_device.id}) with area {room}, labels {labels}"
                    )
                    device = new_device
                else:
                    _LOGGER.error(f"[{self.room}] Failed to create device {device_name} in HA registry")
            
            # Also emit event so DeviceManager can pick it up
            self.hass.bus.async_fire("ogb_device_accepted", {
                "room": room,
                "device_name": device_name,
                "labels": labels,
                "ip": proposal.ip_address,
                "mac": proposal.mac_address,
            })
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error registering in HA: {e}")
    
    async def _discovery_loop(self):
        """Background loop for periodic discovery."""
        while not self._shutdown:
            try:
                # Periodic network scan
                if self._network_scanner:
                    await self._network_scanner.scan()
                
                # Wait before next scan
                await asyncio.sleep(300)  # 5 minutes
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"[{self.room}] Discovery loop error: {e}")
                await asyncio.sleep(60)
    
    async def _cleanup_loop(self):
        """Background loop to clean up expired proposals."""
        while not self._shutdown:
            try:
                now = datetime.now(timezone.utc)
                expired = []
                
                for prop_id, proposal in list(self._proposals.items()):
                    if proposal.status != "pending":
                        continue
                    
                    # Expire proposals older than 7 days
                    age = now - proposal.discovered_at
                    if age > timedelta(days=7):
                        expired.append(prop_id)
                
                for prop_id in expired:
                    proposal = self._proposals[prop_id]
                    proposal.status = "expired"
                    del self._proposals[prop_id]
                    _LOGGER.debug(f"[{self.room}] Expired proposal: {proposal.name}")
                
                await asyncio.sleep(3600)  # Check every hour
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"[{self.room}] Cleanup loop error: {e}")
                await asyncio.sleep(300)
    
    async def get_direct_api(self, device_id: str) -> Optional[Any]:
        """Get direct API controller for a device.
        
        Args:
            device_id: Device identifier (MAC or IP)
            
        Returns:
            DirectAPI instance or None
        """
        try:
            device = self._discovered_devices.get(device_id)
            if not device:
                return None
            
            if not self._direct_api:
                from .direct_api import DirectAPIController
                self._direct_api = DirectAPIController(self.room)
            
            return await self._direct_api.get_device_api(device)
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error getting direct API: {e}")
            return None
    
    async def shutdown(self):
        """Shutdown the discovery manager."""
        self._shutdown = True
        
        # Cancel tasks
        if self._discovery_task:
            self._discovery_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        # Stop discovery engines
        if self._zeroconf_discovery:
            await self._zeroconf_discovery.stop()
        if self._network_scanner:
            await self._network_scanner.stop()
        if self._bluetooth_discovery:
            await self._bluetooth_discovery.stop()
        
        _LOGGER.info(f"[{self.room}] OGBDeviceRecognitionManager shutdown")
    
    @property
    def pending_proposals(self) -> List[DeviceProposal]:
        """Get list of pending proposals."""
        return [p for p in self._proposals.values() if p.status == "pending"]
    
    @property
    def discovered_devices_count(self) -> int:
        """Get count of discovered devices."""
        return len(self._discovered_devices)

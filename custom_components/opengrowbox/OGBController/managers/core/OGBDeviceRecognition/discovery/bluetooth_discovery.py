"""Bluetooth LE discovery for Govee and other BLE devices."""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

_LOGGER = logging.getLogger(__name__)

# Govee BLE UUIDs and identifiers
GOVEE_MANUFACTURER_IDS = [0x0001, 0x004C]  # Common manufacturer IDs
GOVEE_SERVICE_UUIDS = [
    "0000180a-0000-1000-8000-00805f9b34fb",  # Device Information
    "0000fef5-0000-1000-8000-00805f9b34fb",  # Govee specific
]

# Known Govee model prefixes
GOVEE_MODEL_PREFIXES = ["H507", "H510", "H517", "H605", "H606", "H607", "H619", "H70"]


class BluetoothDiscovery:
    """Discovers Bluetooth LE devices."""
    
    def __init__(self, callback: Callable[[Dict[str, Any]], None], room: str):
        """Initialize Bluetooth discovery.
        
        Args:
            callback: Function to call when device is discovered
            room: Room identifier
        """
        self._callback = callback
        self.room = room
        self._shutdown = False
        self._scanner = None
        
    async def start(self):
        """Start Bluetooth discovery."""
        try:
            from bleak import BleakScanner
            
            self._scanner = BleakScanner(detection_callback=self._on_device_detected)
            await self._scanner.start()
            _LOGGER.info(f"[{self.room}] Bluetooth discovery started")
            
        except ImportError:
            _LOGGER.warning(f"[{self.room}] bleak package not installed, Bluetooth discovery unavailable")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error starting Bluetooth discovery: {e}")
    
    def _on_device_detected(self, device, advertisement_data):
        """Handle detected BLE device.
        
        Args:
            device: BLE device object
            advertisement_data: Advertisement data
        """
        try:
            if self._shutdown:
                return
            
            name = device.name or advertisement_data.local_name
            if not name:
                return
            
            # Check if it's a Govee device
            is_govee = self._is_govee_device(name, advertisement_data)
            
            if is_govee:
                model = self._extract_govee_model(name)
                
                discovery_data = {
                    "type": "govee",
                    "mac": device.address,
                    "name": name,
                    "model": model,
                    "rssi": advertisement_data.rssi,
                    "method": "bluetooth",
                    "raw_data": {
                        "manufacturer_data": dict(advertisement_data.manufacturer_data),
                        "service_uuids": advertisement_data.service_uuids,
                        "tx_power": advertisement_data.tx_power,
                    }
                }
                
                _LOGGER.debug(f"[{self.room}] Bluetooth found: {name} ({device.address})")
                
                # Call callback
                asyncio.create_task(self._callback(discovery_data))
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling BLE device: {e}")
    
    def _is_govee_device(self, name: str, advertisement_data: Any) -> bool:
        """Check if device is a Govee device.
        
        Args:
            name: Device name
            advertisement_data: Advertisement data
            
        Returns:
            True if Govee device
        """
        name_upper = (name or "").upper()
        
        # Check name prefixes
        if "GOVEE" in name_upper:
            return True
        
        for prefix in GOVEE_MODEL_PREFIXES:
            if prefix in name_upper:
                return True
        
        # Check manufacturer data
        if hasattr(advertisement_data, 'manufacturer_data'):
            for manufacturer_id in advertisement_data.manufacturer_data.keys():
                if manufacturer_id in GOVEE_MANUFACTURER_IDS:
                    return True
        
        # Check service UUIDs
        if hasattr(advertisement_data, 'service_uuids'):
            for uuid in advertisement_data.service_uuids:
                if any(govee_uuid in uuid for govee_uuid in GOVEE_SERVICE_UUIDS):
                    return True
        
        return False
    
    def _extract_govee_model(self, name: str) -> str:
        """Extract Govee model from name.
        
        Args:
            name: Device name
            
        Returns:
            Model string
        """
        name_upper = name.upper()
        
        for prefix in GOVEE_MODEL_PREFIXES:
            if prefix in name_upper:
                # Extract model number
                start = name_upper.find(prefix)
                end = start + len(prefix)
                # Try to get additional digits
                while end < len(name) and name[end].isdigit():
                    end += 1
                return name[start:end]
        
        return "Unknown"
    
    async def stop(self):
        """Stop Bluetooth discovery."""
        self._shutdown = True
        
        if self._scanner:
            try:
                await self._scanner.stop()
            except Exception as e:
                _LOGGER.debug(f"[{self.room}] Error stopping Bluetooth scanner: {e}")
        
        _LOGGER.info(f"[{self.room}] Bluetooth discovery stopped")

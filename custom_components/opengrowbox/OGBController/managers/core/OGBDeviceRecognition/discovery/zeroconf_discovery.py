"""Zeroconf/mDNS discovery engine for OpenGrowBox."""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

_LOGGER = logging.getLogger(__name__)

# Service types to discover
DISCOVERED_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_hap._tcp.local.",  # HomeKit (Govee, etc.)
    "_esphomelib._tcp.local.",  # ESPHome
    "_mqtt._tcp.local.",  # MQTT devices
]

# Manufacturer specific service types
MANUFACTURER_SERVICES = {
    "shelly": "_shelly._tcp.local.",
    "tasmota": "_tasmota._tcp.local.",
    "esphome": "_esphomelib._tcp.local.",
}


class ZeroconfDiscovery:
    """Discovers devices via Zeroconf/mDNS."""
    
    def __init__(self, callback: Callable[[Dict[str, Any]], None], room: str, hass=None):
        """Initialize Zeroconf discovery.
        
        Args:
            callback: Async function to call when device is discovered
            room: Room identifier
            hass: Home Assistant instance (optional, for shared Zeroconf)
        """
        self._callback = callback
        self.room = room
        self.hass = hass
        self._zeroconf = None
        self._listener = None
        self._shutdown = False
        self._event_loop = None
        
    async def start(self):
        """Start Zeroconf discovery."""
        try:
            from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
            
            # Store event loop for thread-safe callbacks
            try:
                self._event_loop = asyncio.get_event_loop()
            except RuntimeError:
                self._event_loop = None
            
            # Use shared HA Zeroconf if available, otherwise create own
            if self.hass:
                # Try to get HA's shared Zeroconf instance
                try:
                    from homeassistant.components import zeroconf
                    self._zeroconf = await zeroconf.async_get_instance(self.hass)
                    _LOGGER.debug(f"[{self.room}] Using shared HA Zeroconf instance")
                except Exception:
                    self._zeroconf = Zeroconf()
                    _LOGGER.debug(f"[{self.room}] Created own Zeroconf instance")
            else:
                self._zeroconf = Zeroconf()
            
            self._listener = OGBZeroconfListener(self._on_service_found, self.room)
            
            # Browse all service types
            for service_type in DISCOVERED_SERVICE_TYPES:
                try:
                    ServiceBrowser(self._zeroconf, service_type, self._listener)
                    _LOGGER.debug(f"[{self.room}] Browsing {service_type}")
                except Exception as e:
                    _LOGGER.debug(f"[{self.room}] Error browsing {service_type}: {e}")
            
            _LOGGER.info(f"[{self.room}] Zeroconf discovery started")
            
        except ImportError:
            _LOGGER.warning(f"[{self.room}] zeroconf package not installed")
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error starting Zeroconf: {e}")
    
    def _on_service_found(self, name: str, service_type: str, info: Any):
        """Handle discovered service.
        
        Args:
            name: Service name
            service_type: Service type
            info: Service info object
        """
        try:
            if self._shutdown:
                return
            
            # Extract IP address
            ip_address = None
            if hasattr(info, 'parsed_addresses'):
                addresses = info.parsed_addresses()
                if addresses:
                    ip_address = addresses[0]
            elif hasattr(info, 'address'):
                import socket
                ip_address = socket.inet_ntoa(info.address)
            
            if not ip_address:
                return
            
            # Determine device type from service
            device_type = self._get_device_type(service_type, name)
            
            discovery_data = {
                "type": device_type,
                "ip": ip_address,
                "name": name,
                "service_type": service_type,
                "method": "zeroconf",
                "port": getattr(info, 'port', None),
                "properties": dict(getattr(info, 'properties', {})),
            }
            
            _LOGGER.debug(f"[{self.room}] Zeroconf found: {name} at {ip_address}")
            
            # Call callback - must schedule async callback from sync context
            if self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(self._callback(discovery_data), self._event_loop)
            elif self.hass:
                self.hass.add_job(self._callback, discovery_data)
            else:
                _LOGGER.warning(f"[{self.room}] Cannot schedule callback - no event loop")
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling Zeroconf service: {e}")
    
    def _get_device_type(self, service_type: str, name: str) -> str:
        """Determine device type from service info.
        
        Args:
            service_type: Zeroconf service type
            name: Service name
            
        Returns:
            Device type string
        """
        name_lower = name.lower()
        
        if "shelly" in name_lower or "shelly" in service_type:
            return "shelly"
        elif "tasmota" in name_lower or "tasmota" in service_type:
            return "tasmota"
        elif "esphome" in name_lower or "esphome" in service_type:
            return "esphome"
        elif "govee" in name_lower or "govee" in service_type:
            return "govee"
        elif "hap" in service_type:
            return "homekit"  # Could be Govee or other HomeKit devices
        else:
            return "generic_http"
    
    async def stop(self):
        """Stop Zeroconf discovery."""
        self._shutdown = True
        
        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception as e:
                _LOGGER.debug(f"[{self.room}] Error closing Zeroconf: {e}")
        
        _LOGGER.info(f"[{self.room}] Zeroconf discovery stopped")


class OGBZeroconfListener:
    """Zeroconf service listener."""
    
    def __init__(self, callback: Callable, room: str):
        """Initialize listener.
        
        Args:
            callback: Callback function
            room: Room identifier
        """
        self._callback = callback
        self.room = room
    
    def add_service(self, zc, type_, name):
        """Handle service addition."""
        try:
            info = zc.get_service_info(type_, name)
            if info:
                self._callback(name, type_, info)
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Error adding service {name}: {e}")
    
    def remove_service(self, zc, type_, name):
        """Handle service removal."""
        _LOGGER.debug(f"[{self.room}] Service removed: {name}")
    
    def update_service(self, zc, type_, name):
        """Handle service update."""
        try:
            info = zc.get_service_info(type_, name)
            if info:
                self._callback(name, type_, info)
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Error updating service {name}: {e}")

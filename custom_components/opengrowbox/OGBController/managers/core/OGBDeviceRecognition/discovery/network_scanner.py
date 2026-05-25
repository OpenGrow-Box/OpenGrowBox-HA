"""Network scanner for IP-range device discovery."""

import asyncio
import ipaddress
import logging
from typing import Any, Callable, Dict, List, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Known device endpoints to check
DEVICE_ENDPOINTS = {
    "tasmota": {
        "url": "http://{ip}/cm?cmnd=Status%200",
        "check": lambda data: "Status" in data,
    },
    "shelly_gen2": {
        "url": "http://{ip}/rpc",
        "method": "POST",
        "json": {"id": 1, "method": "Shelly.GetDeviceInfo"},
        "check": lambda data: "result" in data,
    },
    "shelly_gen1": {
        "url": "http://{ip}/shelly",
        "check": lambda data: "type" in data and "mac" in data,
    },
    "esphome": {
        "url": "http://{ip}",
        "check": lambda data: isinstance(data, str) and ("esphome" in data.lower() or "esp" in data.lower()),
        "text": True,
    },
    "wled": {
        "url": "http://{ip}/json/info",
        "check": lambda data: "ver" in data and "leds" in data,
    },
}


class NetworkScanner:
    """Scans network ranges for smart devices."""
    
    def __init__(self, callback: Callable[[Dict[str, Any]], None], 
                 ip_ranges: List[str], room: str):
        """Initialize network scanner.
        
        Args:
            callback: Function to call when device is discovered
            ip_ranges: List of IP ranges to scan (e.g., ["192.168.1.0/24"])
            room: Room identifier
        """
        self._callback = callback
        self.ip_ranges = ip_ranges
        self.room = room
        self._shutdown = False
        self._scanning = False
        
    async def start(self):
        """Start network scanner."""
        _LOGGER.info(f"[{self.room}] Network scanner started, ranges: {self.ip_ranges}")
        # Initial scan
        await self.scan()
    
    async def scan(self):
        """Perform network scan."""
        if self._scanning:
            return
        
        self._scanning = True
        _LOGGER.debug(f"[{self.room}] Starting network scan")
        
        try:
            # Generate IP list from ranges
            ips = []
            for ip_range in self.ip_ranges:
                try:
                    network = ipaddress.ip_network(ip_range, strict=False)
                    # Scan only host addresses (exclude network and broadcast)
                    hosts = list(network.hosts())
                    # Limit scan to reasonable number
                    if len(hosts) > 254:
                        _LOGGER.warning(f"[{self.room}] IP range {ip_range} too large, limiting to first 254 hosts")
                        hosts = hosts[:254]
                    ips.extend([str(ip) for ip in hosts])
                except ValueError as e:
                    _LOGGER.error(f"[{self.room}] Invalid IP range {ip_range}: {e}")
            
            if not ips:
                return
            
            # Scan IPs concurrently with semaphore to limit connections
            semaphore = asyncio.Semaphore(50)  # Max 50 concurrent connections
            tasks = [self._scan_ip(ip, semaphore) for ip in ips]
            
            await asyncio.gather(*tasks, return_exceptions=True)
            
            _LOGGER.debug(f"[{self.room}] Network scan complete, checked {len(ips)} IPs")
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error during network scan: {e}")
        finally:
            self._scanning = False
    
    async def _scan_ip(self, ip: str, semaphore: asyncio.Semaphore):
        """Scan a single IP address.
        
        Args:
            ip: IP address to scan
            semaphore: Semaphore to limit concurrent connections
        """
        async with semaphore:
            if self._shutdown:
                return
            
            # Check if IP responds to HTTP
            try:
                timeout = aiohttp.ClientTimeout(total=2)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    # Quick check if device responds
                    try:
                        async with session.get(f"http://{ip}", allow_redirects=True) as resp:
                            if resp.status not in [200, 401]:
                                return
                    except:
                        return
                    
                    # Try to identify device by checking endpoints
                    for device_type, endpoint in DEVICE_ENDPOINTS.items():
                        try:
                            url = endpoint["url"].format(ip=ip)
                            method = endpoint.get("method", "GET")
                            
                            if method == "POST":
                                async with session.post(url, json=endpoint.get("json")) as resp:
                                    if resp.status == 200:
                                        if endpoint.get("text"):
                                            data = await resp.text()
                                        else:
                                            data = await resp.json()
                                        
                                        if endpoint["check"](data):
                                            await self._callback({
                                                "type": device_type.replace("_gen1", "").replace("_gen2", ""),
                                                "ip": ip,
                                                "method": "network_scan",
                                                "endpoint": device_type,
                                                "raw_data": data if not endpoint.get("text") else {"html": data[:500]},
                                            })
                                            return
                            else:
                                async with session.get(url) as resp:
                                    if resp.status == 200:
                                        if endpoint.get("text"):
                                            data = await resp.text()
                                        else:
                                            data = await resp.json()
                                        
                                        if endpoint["check"](data):
                                            await self._callback({
                                                "type": device_type.replace("_gen1", "").replace("_gen2", ""),
                                                "ip": ip,
                                                "method": "network_scan",
                                                "endpoint": device_type,
                                                "raw_data": data if not endpoint.get("text") else {"html": data[:500]},
                                            })
                                            return
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            continue
                            
            except Exception as e:
                _LOGGER.debug(f"[{self.room}] Error scanning {ip}: {e}")
    
    async def stop(self):
        """Stop network scanner."""
        self._shutdown = True
        _LOGGER.info(f"[{self.room}] Network scanner stopped")

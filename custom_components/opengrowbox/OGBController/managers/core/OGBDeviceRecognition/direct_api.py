"""Direct API controller for device communication bypassing Home Assistant."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)


class BaseDeviceAPI(ABC):
    """Base class for device-specific APIs."""
    
    def __init__(self, ip_address: str, room: str):
        """Initialize device API.
        
        Args:
            ip_address: Device IP address
            room: Room identifier
        """
        self.ip_address = ip_address
        self.room = room
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if not self._session or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    @abstractmethod
    async def turn_on(self, **kwargs) -> bool:
        """Turn device on."""
        pass
    
    @abstractmethod
    async def turn_off(self, **kwargs) -> bool:
        """Turn device off."""
        pass
    
    @abstractmethod
    async def get_power(self) -> Optional[float]:
        """Get current power consumption in watts."""
        pass
    
    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Get device status."""
        pass
    
    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()


class TasmotaAPI(BaseDeviceAPI):
    """Direct API for Tasmota devices."""
    
    async def turn_on(self, **kwargs) -> bool:
        """Turn Tasmota device on."""
        try:
            session = await self._get_session()
            async with session.get(
                f"http://{self.ip_address}/cm?cmnd=Power%20On"
            ) as resp:
                return resp.status == 200
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Tasmota turn_on failed: {e}")
            return False
    
    async def turn_off(self, **kwargs) -> bool:
        """Turn Tasmota device off."""
        try:
            session = await self._get_session()
            async with session.get(
                f"http://{self.ip_address}/cm?cmnd=Power%20Off"
            ) as resp:
                return resp.status == 200
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Tasmota turn_off failed: {e}")
            return False
    
    async def get_power(self) -> Optional[float]:
        """Get current power from Tasmota device."""
        try:
            session = await self._get_session()
            async with session.get(
                f"http://{self.ip_address}/cm?cmnd=Status%208"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    status_sns = data.get("StatusSNS", {})
                    energy = status_sns.get("ENERGY", {})
                    return float(energy.get("Power", 0))
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Tasmota get_power failed: {e}")
        return None
    
    async def get_status(self) -> Dict[str, Any]:
        """Get Tasmota device status."""
        try:
            session = await self._get_session()
            async with session.get(
                f"http://{self.ip_address}/cm?cmnd=Status%200"
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Tasmota get_status failed: {e}")
        return {}
    
    async def set_teleperiod(self, seconds: int) -> bool:
        """Set telemetry period for faster updates.
        
        Args:
            seconds: Telemetry period in seconds (10-3600)
        """
        try:
            session = await self._get_session()
            async with session.get(
                f"http://{self.ip_address}/cm?cmnd=TelePeriod%20{seconds}"
            ) as resp:
                return resp.status == 200
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Tasmota set_teleperiod failed: {e}")
            return False
    
    async def set_powerdelta(self, percentage: int) -> bool:
        """Set power delta for immediate reporting.
        
        Args:
            percentage: Power change percentage to trigger report (0-100)
        """
        try:
            session = await self._get_session()
            async with session.get(
                f"http://{self.ip_address}/cm?cmnd=PowerDelta%20{percentage}"
            ) as resp:
                return resp.status == 200
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Tasmota set_powerdelta failed: {e}")
            return False
    
    async def set_light_schedule(self, on_time: str, off_time: str) -> bool:
        """Set light schedule directly on device.
        
        Args:
            on_time: Turn on time (HH:MM)
            off_time: Turn off time (HH:MM)
        """
        try:
            session = await self._get_session()
            
            # Parse times
            on_hour, on_min = on_time.split(":")
            off_hour, off_min = off_time.split(":")
            
            # Set timer 1 for ON
            await session.get(
                f"http://{self.ip_address}/cm?cmnd=Timer1%20{{\"Enable\":1,\"Time\":\"{on_hour}:{on_min}\",\"Action\":1}}"
            )
            
            # Set timer 2 for OFF
            await session.get(
                f"http://{self.ip_address}/cm?cmnd=Timer2%20{{\"Enable\":1,\"Time\":\"{off_hour}:{off_min}\",\"Action\":0}}"
            )
            
            return True
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Tasmota set_light_schedule failed: {e}")
            return False


class ShellyAPI(BaseDeviceAPI):
    """Direct API for Shelly devices (Gen2)."""
    
    async def turn_on(self, **kwargs) -> bool:
        """Turn Shelly device on."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self.ip_address}/rpc",
                json={"id": 1, "method": "Switch.Set", "params": {"id": 0, "on": True}}
            ) as resp:
                return resp.status == 200
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Shelly turn_on failed: {e}")
            return False
    
    async def turn_off(self, **kwargs) -> bool:
        """Turn Shelly device off."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self.ip_address}/rpc",
                json={"id": 1, "method": "Switch.Set", "params": {"id": 0, "on": False}}
            ) as resp:
                return resp.status == 200
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Shelly turn_off failed: {e}")
            return False
    
    async def get_power(self) -> Optional[float]:
        """Get current power from Shelly device."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self.ip_address}/rpc",
                json={"id": 1, "method": "Switch.GetStatus", "params": {"id": 0}}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    return result.get("apower", 0)
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Shelly get_power failed: {e}")
        return None
    
    async def get_status(self) -> Dict[str, Any]:
        """Get Shelly device status."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self.ip_address}/rpc",
                json={"id": 1, "method": "Shelly.GetStatus"}
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Shelly get_status failed: {e}")
        return {}
    
    async def set_light_schedule(self, on_time: str, off_time: str) -> bool:
        """Set schedule directly on Shelly device."""
        try:
            session = await self._get_session()
            
            # Parse times to minutes since midnight
            on_hour, on_min = map(int, on_time.split(":"))
            off_hour, off_min = map(int, off_time.split(":"))
            
            on_minutes = on_hour * 60 + on_min
            off_minutes = off_hour * 60 + off_min
            
            # Set schedule via RPC
            await session.post(
                f"http://{self.ip_address}/rpc",
                json={
                    "id": 1,
                    "method": "Schedule.Create",
                    "params": {
                        "id": 1,
                        "enable": True,
                        "timespec": f"0 {on_min} {on_hour} * * *",
                        "calls": [
                            {
                                "method": "Switch.Set",
                                "params": {"id": 0, "on": True}
                            }
                        ]
                    }
                }
            )
            
            await session.post(
                f"http://{self.ip_address}/rpc",
                json={
                    "id": 1,
                    "method": "Schedule.Create",
                    "params": {
                        "id": 2,
                        "enable": True,
                        "timespec": f"0 {off_min} {off_hour} * * *",
                        "calls": [
                            {
                                "method": "Switch.Set",
                                "params": {"id": 0, "on": False}
                            }
                        ]
                    }
                }
            )
            
            return True
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Shelly set_light_schedule failed: {e}")
            return False


class DirectAPIController:
    """Controller for direct device API communication."""
    
    def __init__(self, room: str):
        """Initialize direct API controller.
        
        Args:
            room: Room identifier
        """
        self.room = room
        self._apis: Dict[str, BaseDeviceAPI] = {}
    
    async def get_device_api(self, device: Dict[str, Any]) -> Optional[BaseDeviceAPI]:
        """Get API instance for a device.
        
        Args:
            device: Device info dict
            
        Returns:
            Device API instance or None
        """
        device_id = device.get("mac") or device.get("ip")
        if not device_id:
            return None
        
        # Return cached API
        if device_id in self._apis:
            return self._apis[device_id]
        
        # Create new API instance
        manufacturer = device.get("manufacturer", "").lower()
        ip = device.get("ip")
        
        if not ip:
            return None
        
        api = None
        if manufacturer == "tasmota":
            api = TasmotaAPI(ip, self.room)
        elif manufacturer == "shelly":
            api = ShellyAPI(ip, self.room)
        
        if api:
            self._apis[device_id] = api
        
        return api
    
    async def get_fallback_sensor_values(self, device_id: str) -> Dict[str, Any]:
        """Get sensor values directly from device when HA is unavailable.
        
        Args:
            device_id: Device identifier
            
        Returns:
            Dict of sensor values
        """
        api = self._apis.get(device_id)
        if not api:
            return {}
        
        try:
            status = await api.get_status()
            values = {}
            
            # Extract common sensor values
            if isinstance(api, TasmotaAPI):
                status_sns = status.get("StatusSNS", {})
                if "ENERGY" in status_sns:
                    energy = status_sns["ENERGY"]
                    values["power"] = energy.get("Power")
                    values["voltage"] = energy.get("Voltage")
                    values["current"] = energy.get("Current")
                
                # Temperature/humidity sensors
                for sensor_name in ["DHT", "SI7021", "BME280", "SHT3X"]:
                    if sensor_name in status_sns:
                        sensor = status_sns[sensor_name]
                        values["temperature"] = sensor.get("Temperature")
                        values["humidity"] = sensor.get("Humidity")
                        break
            
            elif isinstance(api, ShellyAPI):
                result = status.get("result", {})
                switch = result.get("switch:0", {})
                values["power"] = switch.get("apower")
                values["voltage"] = switch.get("voltage")
                values["current"] = switch.get("current")
            
            return values
            
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error getting fallback sensors: {e}")
            return {}
    
    async def send_command(self, device_id: str, command: str, **kwargs) -> bool:
        """Send command directly to device.
        
        Args:
            device_id: Device identifier
            command: Command to send (turn_on, turn_off, etc.)
            **kwargs: Additional command arguments
            
        Returns:
            True if successful
        """
        api = self._apis.get(device_id)
        if not api:
            _LOGGER.warning(f"[{self.room}] No API for device {device_id}")
            return False
        
        try:
            if command == "turn_on":
                return await api.turn_on(**kwargs)
            elif command == "turn_off":
                return await api.turn_off(**kwargs)
            else:
                _LOGGER.warning(f"[{self.room}] Unknown command: {command}")
                return False
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error sending command: {e}")
            return False
    
    async def close_all(self):
        """Close all API connections."""
        for api in self._apis.values():
            await api.close()
        self._apis.clear()

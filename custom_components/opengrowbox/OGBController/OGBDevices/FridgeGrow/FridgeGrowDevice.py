"""
FridgeGrow / Plantalytix Device Integration for OpenGrowBox.

This module provides complete support for FridgeGrow 2.0 and Plantalytix devices.
Devices are recognized via Home Assistant labels and controlled through this
isolated class without polluting the existing OGB codebase.

Recognition: Labels "fridgegrow" + output type ("heater", "light", etc.)
Control: Uses HA services (number.set_value, switch.turn_on/off)

Author: OpenGrowBox Team
License: MIT
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from ..Device import Device

_LOGGER = logging.getLogger(__name__)


class FridgeGrowDevice(Device):
    """
    FridgeGrow/Plantalytix device handler.
    
    This class handles all FridgeGrow outputs (heater, dehumidifier, light, etc.)
    by detecting the output type from labels and adapting the control methods
    accordingly.
    
    Attributes:
        isFridgeGrowDevice (bool): Always True for FridgeGrow devices
        output_type (str): The detected output type ("heater", "light", etc.)
        fridgegrow_device_id (str): Device ID extracted from entity name
        mqtt_control_enabled (bool): Whether MQTT direct control is active
        
    Example:
        A HA entity with labels ["fridgegrow", "heater"] will be recognized
        as a FridgeGrow heater, register the "canHeat" capability, and
        respond to "Increase Heater" / "Reduce Heater" events.
    """
    
    # Output configuration: maps output label to OGB capability and control settings
    OUTPUT_CONFIG = {
        "heater": {
            "capability": "canHeat",
            "event_increase": "Increase Heater",
            "event_reduce": "Reduce Heater",
            "entity_type": "number",
            "value_range": (0.0, 1.0),
            "dimmable": True,
        },
        "dehumidifier": {
            "capability": "canDehumidify",
            "event_increase": "Increase Dehumidifier",
            "event_reduce": "Reduce Dehumidifier",
            "entity_type": "switch",
            "dimmable": False,
        },
        "humidifier": {
            "capability": "canHumidify",
            "event_increase": "Increase Humidifier",
            "event_reduce": "Reduce Humidifier",
            "entity_type": "switch",
            "dimmable": False,
        },
        "light": {
            "capability": "canLight",
            "event_increase": "toggleLight",
            "event_reduce": None,
            "entity_type": "number",
            "value_range": (0.0, 1.0),
            "dimmable": True,
        },
        "co2": {
            "capability": "canCO2",
            "event_increase": "Increase CO2",
            "event_reduce": "Reduce CO2",
            "entity_type": "switch",
            "dimmable": False,
        },
        "exhaust": {
            "capability": "canExhaust",
            "event_increase": "Increase Exhaust",
            "event_reduce": "Reduce Exhaust",
            "entity_type": "number",
            "value_range": (0.0, 1.0),
            "dimmable": True,
        },
        "ventilation": {
            "capability": "canVentilate",
            "event_increase": "Increase Ventilation",
            "event_reduce": "Reduce Ventilation",
            "entity_type": "number",
            "value_range": (0.0, 1.0),
            "dimmable": True,
        },
        "intake": {
            "capability": "canIntake",
            "event_increase": "Increase Intake",
            "event_reduce": "Reduce Intake",
            "entity_type": "number",
            "value_range": (0.0, 1.0),
            "dimmable": True,
        },
        "cooler": {
            "capability": "canCool",
            "event_increase": "Increase Cooler",
            "event_reduce": "Reduce Cooler",
            "entity_type": "number",
            "value_range": (0.0, 1.0),
            "dimmable": True,
        },
        "sensor": {
            "capability": None,
            "entity_type": "sensor",
            "dimmable": False,
        },
    }
    
    # Default step size for dimming (percentage points)
    DEFAULT_STEP_SIZE = 10
    
    def __init__(
        self,
        deviceName: str,
        deviceData: List[Dict],
        eventManager,
        dataStore,
        deviceType: str,
        inRoom: str,
        hass=None,
        deviceLabel: str = "EMPTY",
        allLabels: List[Dict] = None,
    ):
        """
        Initialize FridgeGrow device.
        
        Args:
            deviceName: Name of the device (from HA)
            deviceData: List of entity data dicts
            eventManager: OGB event manager
            dataStore: OGB data store
            deviceType: Device type (will be "FridgeGrow")
            inRoom: Room/area name
            hass: Home Assistant instance
            deviceLabel: Primary device label
            allLabels: All labels from device and entities
        """
        # FridgeGrow-specific attributes (set before parent __init__)
        self.isFridgeGrowDevice = True
        self.output_type: Optional[str] = None
        self.fridgegrow_device_id: Optional[str] = None
        self.mqtt_control_enabled = False
        self._keepalive_task: Optional[asyncio.Task] = None
        
        # Identify output type from labels BEFORE parent init
        # (because parent init calls deviceInit which needs output_type)
        self._identify_output_type(allLabels or [])
        
        # Extract device ID from entity name for keepalive
        self._extract_device_id(deviceData)
        
        _LOGGER.info(
            f"FridgeGrow device '{deviceName}' initializing "
            f"(output_type={self.output_type}, device_id={self.fridgegrow_device_id})"
        )
        
        # Call parent init (triggers deviceInit)
        super().__init__(
            deviceName,
            deviceData,
            eventManager,
            dataStore,
            deviceType,
            inRoom,
            hass,
            deviceLabel,
            allLabels or [],
        )
        
        # Register FridgeGrow-specific event listeners
        self._register_fridgegrow_events()
        
        _LOGGER.info(
            f"FridgeGrow device '{deviceName}' initialized successfully "
            f"(capability={self._get_capability()}, dimmable={self.isDimmable})"
        )
    
    def _identify_output_type(self, labels: List[Dict]) -> None:
        """
        Identify the output type from device labels.
        
        Looks for a label that matches one of the OUTPUT_CONFIG keys
        (heater, dehumidifier, light, etc.)
        
        Args:
            labels: List of label dicts with 'name' key
        """
        for label in labels:
            label_name = label.get("name", "").lower()
            
            # Skip the "fridgegrow" / "plantalytix" identifier labels
            if label_name in ("fridgegrow", "plantalytix"):
                continue
            
            # Check if label matches an output type
            if label_name in self.OUTPUT_CONFIG:
                self.output_type = label_name
                _LOGGER.debug(f"FridgeGrow output type identified: {self.output_type}")
                return
        
        _LOGGER.warning(
            f"FridgeGrow device has no output type label. "
            f"Labels found: {[l.get('name') for l in labels]}"
        )
    
    def _extract_device_id(self, deviceData: List[Dict]) -> None:
        """
        Extract FridgeGrow device ID from entity names.
        
        Expected format: number.fridgegrow_ABC123_heater
        Device ID would be: ABC123
        
        Args:
            deviceData: List of entity data dicts
        """
        for entity in deviceData:
            entity_id = entity.get("entity_id", "")
            
            # Try to extract device ID from entity name
            # Format: domain.fridgegrow_DEVICEID_output
            if "fridgegrow_" in entity_id.lower():
                parts = entity_id.split(".")
                if len(parts) > 1:
                    name_parts = parts[1].split("_")
                    if len(name_parts) >= 2:
                        # Second part should be device ID
                        self.fridgegrow_device_id = name_parts[1]
                        return
    
    def _get_capability(self) -> Optional[str]:
        """Get the OGB capability for this output type."""
        if self.output_type and self.output_type in self.OUTPUT_CONFIG:
            return self.OUTPUT_CONFIG[self.output_type].get("capability")
        return None
    
    def _get_config(self) -> Dict[str, Any]:
        """Get the configuration for this output type."""
        return self.OUTPUT_CONFIG.get(self.output_type, {})
    
    # =========================================================================
    # Override Device methods
    # =========================================================================
    
    def identifyCapabilities(self) -> None:
        """
        Register the appropriate capability based on output type.
        
        Overrides Device.identifyCapabilities() to use FridgeGrow-specific
        capability mapping instead of the default deviceType-based mapping.
        """
        capability = self._get_capability()
        
        if not capability:
            _LOGGER.debug(f"FridgeGrow '{self.deviceName}' has no capability to register")
            return
        
        # Initialize capabilities in dataStore if not present
        if not self.dataStore.get("capabilities"):
            self.dataStore.set("capabilities", {})
        
        capPath = f"capabilities.{capability}"
        currentCap = self.dataStore.getDeep(capPath)
        
        if not currentCap:
            currentCap = {"state": False, "count": 0, "devEntities": []}
        
        # Check for duplicates
        if self.deviceName in currentCap.get("devEntities", []):
            _LOGGER.debug(
                f"FridgeGrow '{self.deviceName}' already registered for {capability}"
            )
            return
        
        # Register device
        currentCap["state"] = True
        currentCap["count"] = currentCap.get("count", 0) + 1
        currentCap["devEntities"].append(self.deviceName)
        
        self.dataStore.setDeep(capPath, currentCap)
        
        _LOGGER.info(
            f"FridgeGrow '{self.deviceName}' registered for capability '{capability}' "
            f"(count: {currentCap['count']})"
        )
    
    def identifDimmable(self) -> None:
        """
        Determine if this FridgeGrow output is dimmable.
        
        Overrides Device.identifDimmable() to use FridgeGrow config
        instead of entity-based detection.
        """
        config = self._get_config()
        self.isDimmable = config.get("dimmable", False)
        
        if self.isDimmable:
            # Set step size for dimming
            self.steps = self.DEFAULT_STEP_SIZE
            self.dutyCycle = 50  # Default to 50%
            
            _LOGGER.debug(
                f"FridgeGrow '{self.deviceName}' is dimmable (steps={self.steps})"
            )
    
    def _register_fridgegrow_events(self) -> None:
        """
        Register event listeners for FridgeGrow-specific events.
        
        Based on output type, registers handlers for increase/reduce events.
        """
        config = self._get_config()
        
        event_increase = config.get("event_increase")
        event_reduce = config.get("event_reduce")
        
        if event_increase:
            self.eventManager.on(event_increase, self.increaseAction)
            _LOGGER.debug(
                f"FridgeGrow '{self.deviceName}' listening for '{event_increase}'"
            )
        
        if event_reduce:
            self.eventManager.on(event_reduce, self.reduceAction)
            _LOGGER.debug(
                f"FridgeGrow '{self.deviceName}' listening for '{event_reduce}'"
            )
    
    # =========================================================================
    # Control methods
    # =========================================================================
    
    async def turn_on(self, **kwargs) -> None:
        """
        Turn on the FridgeGrow output.
        
        For number entities: Sets value (converts OGB 0-100% to FG 0-1)
        For switch entities: Calls turn_on service
        
        Args:
            percentage: Target value in OGB range (0-100)
            brightness_pct: Alternative to percentage (for lights)
            value: Direct value in FridgeGrow range (0-1)
        """
        config = self._get_config()
        entity_type = config.get("entity_type", "switch")
        
        # Get entity ID
        entity_id = self._get_primary_entity_id()
        if not entity_id:
            _LOGGER.error(f"FridgeGrow '{self.deviceName}' has no entity to control")
            return
        
        try:
            if entity_type == "number":
                # Get value - prefer direct value, then percentage, then brightness_pct
                if "value" in kwargs:
                    # Direct FridgeGrow value (0-1)
                    fg_value = float(kwargs["value"])
                else:
                    # OGB percentage (0-100) -> FridgeGrow (0-1)
                    percentage = kwargs.get("percentage") or kwargs.get("brightness_pct", 100)
                    fg_value = float(percentage) / 100.0
                
                # Clamp to valid range
                value_range = config.get("value_range", (0.0, 1.0))
                fg_value = max(value_range[0], min(value_range[1], fg_value))
                
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": fg_value},
                )
                
                # Update internal state
                self.dutyCycle = fg_value * 100
                self.isRunning = fg_value > 0
                
                _LOGGER.debug(
                    f"FridgeGrow '{self.deviceName}' set to {fg_value} "
                    f"({self.dutyCycle}%)"
                )
                
            elif entity_type == "switch":
                await self.hass.services.async_call(
                    "switch",
                    "turn_on",
                    {"entity_id": entity_id},
                )
                
                self.isRunning = True
                _LOGGER.debug(f"FridgeGrow '{self.deviceName}' turned ON")
            
            else:
                _LOGGER.warning(
                    f"FridgeGrow '{self.deviceName}' has unsupported entity type: {entity_type}"
                )
                
        except Exception as e:
            _LOGGER.error(f"FridgeGrow '{self.deviceName}' turn_on failed: {e}")
    
    async def turn_off(self, **kwargs) -> None:
        """
        Turn off the FridgeGrow output.
        
        For number entities: Sets value to 0
        For switch entities: Calls turn_off service
        """
        config = self._get_config()
        entity_type = config.get("entity_type", "switch")
        
        entity_id = self._get_primary_entity_id()
        if not entity_id:
            _LOGGER.error(f"FridgeGrow '{self.deviceName}' has no entity to control")
            return
        
        try:
            if entity_type == "number":
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": 0},
                )
                
                self.dutyCycle = 0
                self.isRunning = False
                _LOGGER.debug(f"FridgeGrow '{self.deviceName}' set to 0 (OFF)")
                
            elif entity_type == "switch":
                await self.hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": entity_id},
                )
                
                self.isRunning = False
                _LOGGER.debug(f"FridgeGrow '{self.deviceName}' turned OFF")
                
        except Exception as e:
            _LOGGER.error(f"FridgeGrow '{self.deviceName}' turn_off failed: {e}")
    
    async def set_value(self, value: float) -> None:
        """
        Set the output value directly.
        
        Args:
            value: Value in OGB range (0-100) - will be converted to FG range (0-1)
        """
        await self.turn_on(percentage=value)
    
    async def increaseAction(self, data: Any) -> None:
        """
        Handle increase action event.
        
        For dimmable outputs: Increases value by step size
        For switches: Turns on
        """
        if self.isDimmable:
            current = self.dutyCycle or 0
            new_value = min(100, current + self.steps)
            self.dutyCycle = new_value
            
            await self.turn_on(percentage=new_value)
            
            _LOGGER.debug(
                f"FridgeGrow '{self.deviceName}' increased: {current}% -> {new_value}%"
            )
        else:
            await self.turn_on()
    
    async def reduceAction(self, data: Any) -> None:
        """
        Handle reduce action event.
        
        For dimmable outputs: Decreases value by step size
        For switches: Turns off
        """
        if self.isDimmable:
            current = self.dutyCycle or 0
            new_value = max(0, current - self.steps)
            self.dutyCycle = new_value
            
            if new_value > 0:
                await self.turn_on(percentage=new_value)
            else:
                await self.turn_off()
            
            _LOGGER.debug(
                f"FridgeGrow '{self.deviceName}' reduced: {current}% -> {new_value}%"
            )
        else:
            await self.turn_off()
    
    def _get_primary_entity_id(self) -> Optional[str]:
        """
        Get the primary entity ID for control.
        
        Returns the first switch entity, or first option entity if no switch.
        """
        if self.switches:
            return self.switches[0].get("entity_id")
        if self.options:
            return self.options[0].get("entity_id")
        return None
    
    # =========================================================================
    # MQTT Control Mode (Keepalive)
    # =========================================================================
    
    async def enable_mqtt_control(self) -> None:
        """
        Enable FridgeGrow MQTT direct control mode.
        
        Starts sending keepalive messages every 30 seconds to maintain
        direct control over the device.
        """
        if self.mqtt_control_enabled:
            return
        
        if not self.fridgegrow_device_id:
            _LOGGER.warning(
                f"FridgeGrow '{self.deviceName}' cannot enable MQTT control: "
                "device_id not found"
            )
            return
        
        self.mqtt_control_enabled = True
        
        # Send initial keepalive
        await self._send_keepalive()
        
        # Start keepalive task
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        
        _LOGGER.info(
            f"FridgeGrow '{self.deviceName}' MQTT control enabled "
            f"(device_id={self.fridgegrow_device_id})"
        )
    
    async def disable_mqtt_control(self) -> None:
        """
        Disable FridgeGrow MQTT direct control mode.
        
        Stops the keepalive task. Device will return to internal control
        after 60 seconds.
        """
        self.mqtt_control_enabled = False
        
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        
        self._keepalive_task = None
        
        _LOGGER.info(f"FridgeGrow '{self.deviceName}' MQTT control disabled")
    
    async def _keepalive_loop(self) -> None:
        """
        Keepalive loop - sends mqttcontrol=true every 30 seconds.
        """
        while self.mqtt_control_enabled:
            try:
                await self._send_keepalive()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"FridgeGrow keepalive error: {e}")
                await asyncio.sleep(5)
    
    async def _send_keepalive(self) -> None:
        """
        Send MQTT keepalive message via Home Assistant mqtt.publish service.
        """
        if not self.fridgegrow_device_id:
            return
        
        topic = f"devices/{self.fridgegrow_device_id}/configuration"
        payload = '{"mqttcontrol": true}'
        
        try:
            await self.hass.services.async_call(
                "mqtt",
                "publish",
                {"topic": topic, "payload": payload},
            )
            _LOGGER.debug(
                f"FridgeGrow keepalive sent for device {self.fridgegrow_device_id}"
            )
        except Exception as e:
            _LOGGER.error(f"FridgeGrow keepalive failed: {e}")
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    async def cleanup(self) -> None:
        """Clean up resources when device is removed."""
        await self.disable_mqtt_control()
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"FridgeGrowDevice("
            f"name='{self.deviceName}', "
            f"output_type='{self.output_type}', "
            f"capability='{self._get_capability()}', "
            f"dimmable={self.isDimmable}, "
            f"running={self.isRunning}"
            f")"
        )

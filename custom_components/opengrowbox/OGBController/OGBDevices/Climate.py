import logging

from .Device import Device
from ..data.OGBParams.OGBParams import CAP_MAPPING

_LOGGER = logging.getLogger(__name__)


class Climate(Device):
    # Mapping of climate modes to capability names
    CLIMATE_MODE_TO_CAP = {
        "cool": "canCool",
        "heat": "canHeat",
        "dry": "canDehumidify",
    }
    
    # Priority order for each situation (first available will be used)
    MODE_PRIORITIES = {
        # too_hot + too_humid -> try these in order
        "hot_humid": ["cool", "heat"],
        # too_warm + too_dry -> try these in order  
        "warm_dry": ["cool", "heat"],
        # too_cold + too_dry -> try these in order
        "cold_dry": ["heat", "cool"],
        # too_cold + too_humid -> try these in order
        "cold_humid": ["dry", "heat", "cool"],
        # only too_cold
        "cold": ["heat"],
        # only too_humid
        "humid": ["dry", "cool"],
        # only too_warm
        "warm": ["cool", "heat"],
    }

    def __init__(
        self,
        deviceName,
        deviceData,
        eventManager,
        dataStore,
        deviceType,
        inRoom,
        hass,
        deviceLabel="EMPTY",
        allLabels=[],
    ):
        super().__init__(
            deviceName,
            deviceData,
            eventManager,
            dataStore,
            deviceType,
            inRoom,
            hass,
            deviceLabel,
            allLabels,
        )
        self.currentHAVOC = "off"
        self.havocs = {
            "dry": "dry",
            "cool": "cool", 
            "heat": "heat",
            "off": "off",
        }
        self.isRunning = False

        # Zustände
        self.Heat = False
        self.Dehum = False
        self.Cool = False

        # Event Listener registrieren
        self.event_manager.on("Increase Climate", self.increaseAction)
        self.event_manager.on("Reduce Climate", self.reduceAction)
        self.event_manager.on("Eval Climate", self.evalAction)
        self.event_manager.on("Disable Climate Mode", self.disableMode)

    def getRoomCaps(self):
        self.roomCaps = self.dataStore.get("capabilities")

    def _get_used_capabilities(self, capabilities: dict) -> set:
        """Get set of capability names that are already in use by OTHER devices."""
        used = set()
        for cap_name, cap_data in capabilities.items():
            if isinstance(cap_data, dict) and cap_data.get("state"):
                dev_entities = cap_data.get("devEntities", [])
                # Check if any OTHER device is using this capability
                for dev_name in dev_entities:
                    if dev_name != self.deviceName:
                        used.add(cap_name)
        return used

    def _get_available_modes(self, capabilities: dict, used_caps: set) -> list:
        """Get list of available climate modes that are NOT already in use."""
        available = []
        for mode, cap_name in self.CLIMATE_MODE_TO_CAP.items():
            cap_data = capabilities.get(cap_name, {})
            if cap_data.get("state") and cap_name not in used_caps:
                available.append(mode)
        return available

    def _get_situation_key(self, temp_too_high: bool, temp_too_low: bool, hum_too_high: bool, hum_too_low: bool) -> str:
        """Determine the situation key for mode priority lookup."""
        if temp_too_high and hum_too_high:
            return "hot_humid"
        if temp_too_high and hum_too_low:
            return "warm_dry"
        if temp_too_low and hum_too_low:
            return "cold_dry"
        if temp_too_low and hum_too_high:
            return "cold_humid"
        if temp_too_low:
            return "cold"
        if hum_too_high:
            return "humid"
        if temp_too_high:
            return "warm"
        return "none"

    def decideClimateMode(self, action: str, capabilities: dict) -> str | None:
        """
        Smart decision based on current state, plant stages, and already used capabilities.
        """
        # Aktuelle Werte aus Datastore
        current_temp = self.dataStore.getDeep("tentData.temperature")
        current_hum = self.dataStore.getDeep("tentData.humidity")
        
        # Plant Stage und Zielwerte
        plant_stage = self.dataStore.getDeep("tentData.plantStage") or "EarlyVeg"
        plant_stages = self.dataStore.getDeep("plantStages") or {}
        stage_data = plant_stages.get(plant_stage, {})
        
        # Zielwerte aus Plant Stage
        min_temp = stage_data.get("minTemp")
        max_temp = stage_data.get("maxTemp")
        min_hum = stage_data.get("minHumidity") or stage_data.get("minHum")
        max_hum = stage_data.get("maxHumidity") or stage_data.get("maxHum")
        
        # Fallback auf tentData
        if min_temp is None:
            min_temp = self.dataStore.getDeep("tentData.minTemp")
        if max_temp is None:
            max_temp = self.dataStore.getDeep("tentData.maxTemp")
        if min_hum is None:
            min_hum = self.dataStore.getDeep("tentData.minHumidity")
        if max_hum is None:
            max_hum = self.dataStore.getDeep("tentData.maxHumidity")
        
        # Zustand analysieren
        temp_too_high = max_temp is not None and current_temp is not None and current_temp > max_temp
        temp_too_low = min_temp is not None and current_temp is not None and current_temp < min_temp
        hum_too_high = max_hum is not None and current_hum is not None and current_hum > max_hum
        hum_too_low = min_hum is not None and current_hum is not None and current_hum < min_hum
        
        # Get already used capabilities by OTHER devices
        used_caps = self._get_used_capabilities(capabilities)
        
        # Get available modes (not already in use)
        available_modes = self._get_available_modes(capabilities, used_caps)
        
        situation = self._get_situation_key(temp_too_high, temp_too_low, hum_too_high, hum_too_low)
        
        _LOGGER.warning(
            f"{self.deviceName}: State - "
            f"Temp: {current_temp}°C (target: {min_temp}-{max_temp}), "
            f"Hum: {current_hum}% (target: {min_hum}-{max_hum}), "
            f"Stage: {plant_stage}, "
            f"Used caps: {used_caps}, Available modes: {available_modes}, Situation: {situation}"
        )
        
        if action in ("increase", "eval", "evalu", "on", "start"):
            # Determine situation and get priority modes
            situation = self._get_situation_key(temp_too_high, temp_too_low, hum_too_high, hum_too_low)
            
            # Get priority modes for this situation
            priority_modes = self.MODE_PRIORITIES.get(situation, [])
            
            # Find first available mode from priority list
            for mode in priority_modes:
                if mode in available_modes:
                    cap_name = self.CLIMATE_MODE_TO_CAP[mode]
                    _LOGGER.debug(f"{self.deviceName}: Smart decision -> {mode} (situation: {situation}, cap: {cap_name})")
                    return mode
            
            # If no priority mode available, try any available mode
            if available_modes:
                mode = available_modes[0]
                cap_name = self.CLIMATE_MODE_TO_CAP.get(mode)
                _LOGGER.debug(f"{self.deviceName}: Fallback -> {mode} (any available, cap: {cap_name})")
                return mode
            
            # LAST RESORT: All caps in use - force a mode anyway (climate can override)
            # Find any capability that exists
            for mode in priority_modes:
                cap_name = self.CLIMATE_MODE_TO_CAP.get(mode)
                if capabilities.get(cap_name, {}).get("state"):
                    _LOGGER.warning(f"{self.deviceName}: FORCE -> {mode} (all in use but needed, cap: {cap_name})")
                    return mode
            
            # Final fallback: if situation is not "none", try any climate-capable mode
            if situation != "none":
                for mode in ["cool", "heat", "dry"]:
                    cap_name = self.CLIMATE_MODE_TO_CAP.get(mode)
                    if capabilities.get(cap_name, {}).get("state"):
                        _LOGGER.warning(f"{self.deviceName}: FINAL FALLBACK -> {mode}")
                        return mode
            
            # If no priority mode available, try any available mode
            if available_modes:
                mode = available_modes[0]
                cap_name = self.CLIMATE_MODE_TO_CAP.get(mode)
                _LOGGER.debug(f"{self.deviceName}: Fallback -> {mode} (any available, cap: {cap_name})")
                return mode
            
            # Last resort: check if any capability exists even if in use (force it)
            for mode in priority_modes:
                cap_name = self.CLIMATE_MODE_TO_CAP.get(mode)
                if capabilities.get(cap_name, {}).get("state"):
                    _LOGGER.debug(f"{self.deviceName}: Force -> {mode} (no alternatives, cap in use)")
                    return mode
            
            # If situation is "none" but we need to turn ON (action=increase/eval) -> use any available mode
            if available_modes:
                mode = available_modes[0]
                cap_name = self.CLIMATE_MODE_TO_CAP.get(mode)
                _LOGGER.debug(f"{self.deviceName}: In range but turning on -> {mode}")
                return mode
                    
        elif action == "reduce":
            # Reduce = ausschalten
            return "off"
        
        return None

    async def evalAction(self, data):
        """
        Evaluates and selects the necessary mode based on smart analysis.
        """
        # Handle both string and dict data
        if isinstance(data, str):
            action = data
        elif isinstance(data, dict):
            action = data.get("action", "unknown")
        else:
            action = "unknown"
        
        # Normalize action
        action = action.lower() if action else "unknown"
        
        roomCapabilities = self.dataStore.get("capabilities")

        _LOGGER.warning(f"Eval Action '{action}': {self.deviceName} CurrentHAVOC: {self.currentHAVOC}")

        new_mode = self.decideClimateMode(action, roomCapabilities)

        if new_mode and self.currentHAVOC != new_mode:
            await self.activateMode(new_mode)
        else:
            _LOGGER.warning(
                f"{self.deviceName}: No suitable mode for '{action}' or already active (current: {self.currentHAVOC})."
            )

    async def increaseAction(self, data):
        """Handles Increase action."""
        self.log_action("Increase Action / Turn On")
        await self.evalAction({"action": "increase"})

    async def reduceAction(self, data):
        """Handles Reduce action."""
        self.log_action("Reduce Action / Turn Off")
        await self.evalAction({"action": "reduce"})

    async def activateMode(self, mode):
        """
        Activates the specified mode on the device.
        """
        self.currentHAVOC = mode
        self.isRunning = mode != "off"
        
        # Get entity_id from switches
        entity_id = None
        if self.switches:
            entity_id = self.switches[0].get("entity_id")
        
        # Call HA climate service
        if entity_id and self.hass:
            try:
                await self.hass.services.async_call(
                    domain="climate",
                    service="set_hvac_mode",
                    service_data={"entity_id": entity_id, "hvac_mode": mode},
                )
                _LOGGER.warning(f"{self.deviceName} Activated HVAC mode: {mode} via HA")
            except Exception as e:
                _LOGGER.error(f"{self.deviceName} Failed to activate mode {mode}: {e}")
        else:
            _LOGGER.warning(f"{self.deviceName} No entity_id found for climate control")
        
        _LOGGER.warning(f"{self.deviceName} Activating mode: {mode}")

    async def disableMode(self, data):
        """
        Disables a specific mode permanently by updating capabilities.
        Example data: {"mode": "canHeat"}
        """
        mode_key = data.get("mode")
        capabilities = self.dataStore.get("capabilities") or {}
        if mode_key in capabilities:
            capabilities[mode_key]["state"] = False
            capabilities[mode_key]["devEntities"] = []
            self.dataStore.set("capabilities", capabilities)
            _LOGGER.warning(
                f"{self.deviceName}: Mode '{mode_key}' permanently disabled."
            )
        else:
            _LOGGER.error(f"{self.deviceName}: Unknown mode '{mode_key}' to disable.")

    def log_action(self, action_name):
        """Logs the performed action."""
        log_message = f"{self.deviceName} CurrentHAVOC: {self.currentHAVOC}"
        _LOGGER.warning(f"{action_name}: {log_message}")

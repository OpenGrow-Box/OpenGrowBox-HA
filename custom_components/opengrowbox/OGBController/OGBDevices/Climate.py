from .Device import Device
import logging

_LOGGER = logging.getLogger(__name__)


class Climate(Device):
    def __init__(self, deviceName, deviceData, eventManager, dataStore, deviceType, inRoom, hass=None):
        super().__init__(deviceName, deviceData, eventManager, dataStore, deviceType, inRoom, hass)
        self.currentHAVOC = "off"
        self.havocs = {
            "dry": "dry",    # Entfeuchten
            "cool": "cool",  # Kühlen
            "heat": "heat",  # Heizen (optional, falls du es nutzen willst)
            "off": "off",    # Aus
        }
        self.isRunning = False

        # Zustände
        self.Heat = False
        self.Dehum = False
        self.Cool = False

        # Event Listener registrieren
        self.eventManager.on("Increase Climate", self.increaseAction)
        self.eventManager.on("Reduce Climate", self.reduceAction)
        self.eventManager.on("Eval Climate", self.evalAction)
        self.eventManager.on("Disable Climate Mode", self.disableMode)

    def getRoomCaps(self):
        self.roomCaps = self.dataStore.get("capabilities")

    def decideClimateMode(self, action: str, capabilities: dict) -> str | None:
        """
        Entscheidet anhand der Aktion und der Capabilities den besten Klimamodus.
        Gibt den zu aktivierenden Modus zurück oder None.
        """
        if action == "increase":
            # Ziel: Luftfeuchtigkeit senken oder Temperatur erhöhen
            if capabilities.get("canDehumidify", {}).get("state"):
                return "dry"
            elif capabilities.get("canHeat", {}).get("state"):
                return "heat"
        elif action == "reduce":
            # Ziel: Temperatur senken
            if capabilities.get("canCool", {}).get("state"):
                return "cool"
        return None

    async def evalAction(self, data):
        """
        Evaluates and selects the necessary mode based on action and capabilities.
        """
        action = data.get("action", "unknown")
        roomCapabilities = self.dataStore.get("capabilities")

        self.log_action(f"Eval Action '{action}'")

        new_mode = self.decideClimateMode(action, roomCapabilities)

        if new_mode and self.currentHAVOC != new_mode:
            self.activateMode(new_mode)
        else:
            _LOGGER.warning(f"{self.deviceName}: No suitable mode for '{action}' or already active.")

    async def increaseAction(self, data):
        """Handles Increase action."""
        self.log_action("Increase Action / Turn On")
        await self.evalAction({"action": "increase"})

    async def reduceAction(self, data):
        """Handles Reduce action."""
        self.log_action("Reduce Action / Turn Off")
        await self.evalAction({"action": "reduce"})

    def activateMode(self, mode):
        """
        Activates the specified mode on the device.
        """
        self.currentHAVOC = mode
        self.isRunning = mode != "off"
        _LOGGER.warning(f"{self.deviceName} Activating mode: {mode}")

    async def disableMode(self, data):
        """
        Disables a specific mode permanently by updating capabilities.
        Example data: {"mode": "canHeat"}
        """
        mode_key = data.get("mode")
        if mode_key in self.capabilities:
            self.capabilities[mode_key]["state"] = False
            self.capabilities[mode_key]["devEntities"] = []
            _LOGGER.warning(f"{self.deviceName}: Mode '{mode_key}' permanently disabled.")
        else:
            _LOGGER.error(f"{self.deviceName}: Unknown mode '{mode_key}' to disable.")

    def log_action(self, action_name):
        """Logs the performed action."""
        log_message = f"{self.deviceName} CurrentHAVOC: {self.currentHAVOC}"
        _LOGGER.warning(f"{action_name}: {log_message}")

import asyncio
from datetime import datetime
import logging
from .OGBParams.OGBParams import DEFAULT_DEVICE_COOLDOWNS
_LOGGER = logging.getLogger(__name__)

class OGBConsoleManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Console Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False
        self.last_command = ""

        # Command-Mapping
        self.command_map = {
            "version": self.cmd_version,
            "test": self.cmd_test,
            "gcd": self.cmd_gcd,
            # neue Befehle hier eintragen
        }

        self.hass.bus.async_listen("ogb_console_command", self._command_income)
        asyncio.create_task(self.init())

    async def init(self):
        self.is_initialized = True
        await self._send_response("🟢 Console Manager initialized.")
        _LOGGER.info(f"OGBConsoleManager initialized for room: {self.room}")

    async def _command_income(self, event):
        event_room = event.data.get("room")
        event_command = event.data.get("command")
        if event_room != self.room:
            return

        self.last_command = event_command
        _LOGGER.warning(f"[{self.room}] Received command: {event_command}")
        await self._handle_command(event_command)

    async def _handle_command(self, command: str):
        """Allgemeines Command-Handling"""
        parts = command.strip().split()
        if not parts:
            await self._send_response("⚠️ Empty command.")
            return

        cmd_name = parts[0].lower()
        params = parts[1:]

        cmd_func = self.command_map.get(cmd_name)
        if cmd_func:
            try:
                await cmd_func(params)
            except Exception as e:
                await self._send_response(f"⚠️ Error executing command '{cmd_name}': {e}")
        else:
            await self._send_response(f"⚠️ Unknown command: {cmd_name}")

    # --- Beispielbefehle ---
    async def cmd_version(self, params):
        await self._send_response("OGB Console Version 🍀 1.0.1 🍀")

    async def cmd_test(self, params):
        await self._send_response("✅ Test command executed successfully.")

    async def cmd_gcd(self, params):
        """
        Setzt oder zeigt den Global Cooldown für ein Gerät.
        Usage: gcd <capability> <minutes>
        """
        if len(params) != 2:
            await self._send_response("⚠️ Usage: gcd <capability> <minutes>")
            return

        capability = params[0]
        try:
            minutes = int(params[1])
        except ValueError:
            await self._send_response("⚠️ Minutes must be an integer.")
            return

        if capability not in DEFAULT_DEVICE_COOLDOWNS:
            await self._send_response(f"⚠️ Unknown capability: {capability}")
            return

        DEFAULT_DEVICE_COOLDOWNS[capability] = minutes
        gcdAdjustment = {"cap":capability,"minutes":minutes}
        await self.eventManager.emit("AdjustDeviceGCD",gcdAdjustment)
        await self._send_response(f"✅ Global Cooldown for '{capability}' set to {minutes} minute(s).")

    async def _send_response(self, message: str):
        event_type = "ogb_console_response"
        event_data = {
            "room": self.room,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        _LOGGER.debug(f"[{self.room}] Sending console response: {message}")
        self.hass.bus.async_fire(event_type, event_data)

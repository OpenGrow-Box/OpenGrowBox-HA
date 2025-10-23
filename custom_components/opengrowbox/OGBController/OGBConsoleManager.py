import asyncio
from datetime import datetime
import logging
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass
from .OGBParams.OGBParams import DEFAULT_DEVICE_COOLDOWNS

_LOGGER = logging.getLogger(__name__)


@dataclass
class CommandInfo:
    """Speichert Metadaten über einen Command"""
    func: Callable
    description: str
    usage: str
    examples: List[str] = None
    
    def __post_init__(self):
        if self.examples is None:
            self.examples = []


class OGBConsoleManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Console Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.is_initialized = False
        self.last_command = ""

        # Command-Registry mit Metadaten
        self.commands: Dict[str, CommandInfo] = {}
        self._register_commands()

        self.hass.bus.async_listen("ogb_console_command", self._command_income)
        asyncio.create_task(self.init())

    def _register_commands(self):
        """Registriert alle verfügbaren Commands mit ihren Metadaten"""
        self.register_command(
            "help",
            self.cmd_help,
            "Zeigt verfügbare Befehle oder Details zu einem Befehl",
            "help [command]",
            ["help", "help gcd"]
        )
        
        self.register_command(
            "version",
            self.cmd_version,
            "Zeigt die Console-Version an",
            "version",
            ["version"]
        )
        
        self.register_command(
            "test",
            self.cmd_test,
            "Führt einen Test-Befehl aus",
            "test",
            ["test"]
        )
        
        self.register_command(
            "gcd",
            self.cmd_gcd,
            "Setzt oder zeigt den Global Cooldown für ein Gerät",
            "gcd <capability> <minutes>",
            ["gcd light 5", "gcd cover 10"]
        )
        
        self.register_command(
            "list",
            self.cmd_list,
            "Listet verfügbare Capabilities auf",
            "list [capabilities|devices]",
            ["list capabilities", "list devices"]
        )

    def register_command(self, name: str, func: Callable, description: str, 
                        usage: str, examples: List[str] = None):
        """
        Öffentliche Methode zum Registrieren neuer Commands.
        Kann von außen verwendet werden, um die Console zu erweitern.
        """
        self.commands[name.lower()] = CommandInfo(
            func=func,
            description=description,
            usage=usage,
            examples=examples or []
        )
        _LOGGER.debug(f"Registered command: {name}")

    async def init(self):
        self.is_initialized = True
        await self._send_response("🟢 Console Manager initialized. Type 'help' for available commands.")
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
        """Allgemeines Command-Handling mit automatischer Help-Ausgabe"""
        parts = command.strip().split()
        if not parts:
            await self._send_response("⚠️ Empty command. Type 'help' for available commands.")
            return

        cmd_name = parts[0].lower()
        params = parts[1:]

        # Check für -h oder --help Flag
        if "-h" in params or "--help" in params:
            await self.cmd_help([cmd_name])
            return

        cmd_info = self.commands.get(cmd_name)
        if cmd_info:
            try:
                await cmd_info.func(params)
            except TypeError as e:
                # Fehlerhafte Parameter
                await self._send_response(f"⚠️ Invalid arguments for '{cmd_name}'. Use '{cmd_name} -h' for help.")
                _LOGGER.error(f"Command execution error: {e}")
            except Exception as e:
                await self._send_response(f"⚠️ Error executing command '{cmd_name}': {e}")
                _LOGGER.error(f"Command execution error: {e}", exc_info=True)
        else:
            await self._send_response(
                f"⚠️ Unknown command: '{cmd_name}'\n"
                f"Type 'help' to see available commands."
            )

    # =========================
    # COMMAND IMPLEMENTATIONS
    # =========================

    async def cmd_help(self, params: List[str]):
        """Zeigt Help-Informationen an"""
        if not params:
            # Zeige alle Commands
            response = "📖 Available Commands:\n" + "="*50 + "\n"
            for cmd_name, cmd_info in sorted(self.commands.items()):
                response += f"\n• {cmd_name:<12} - {cmd_info.description}"
            response += f"\n\n{'='*50}\n"
            response += "Type 'help <command>' or '<command> -h' for details."
            await self._send_response(response)
        else:
            # Zeige Details für spezifischen Command
            cmd_name = params[0].lower()
            cmd_info = self.commands.get(cmd_name)
            
            if not cmd_info:
                await self._send_response(f"⚠️ Unknown command: '{cmd_name}'")
                return
            
            response = f"📖 Help: {cmd_name}\n" + "="*50 + "\n"
            response += f"\nDescription: {cmd_info.description}\n"
            response += f"\nUsage: {cmd_info.usage}\n"
            
            if cmd_info.examples:
                response += "\nExamples:\n"
                for example in cmd_info.examples:
                    response += f"  $ {example}\n"
            
            response += "="*50
            await self._send_response(response)

    async def cmd_version(self, params: List[str]):
        """Zeigt Version an"""
        await self._send_response("🍀 OGB Console Version 1.0.1 🍀")

    async def cmd_test(self, params: List[str]):
        """Test-Command"""
        await self._send_response("✅ Test command executed successfully.")

    async def cmd_gcd(self, params: List[str]):
        """
        Setzt oder zeigt den Global Cooldown für ein Gerät.
        Usage: gcd <capability> <minutes>
        """
        if len(params) == 0:
            # Zeige alle aktuellen GCDs
            response = "📊 Current Global Cooldowns:\n" + "="*50 + "\n"
            for cap, minutes in DEFAULT_DEVICE_COOLDOWNS.items():
                response += f"  {cap:<15} : {minutes} min\n"
            response += "="*50
            await self._send_response(response)
            return
        
        if len(params) != 2:
            await self._send_response(
                "⚠️ Invalid arguments.\n"
                f"Usage: gcd <capability> <minutes>\n"
                f"Use 'gcd -h' for help."
            )
            return

        capability = params[0]
        try:
            minutes = int(params[1])
        except ValueError:
            await self._send_response("⚠️ Minutes must be an integer.")
            return

        if capability not in DEFAULT_DEVICE_COOLDOWNS:
            available = ", ".join(DEFAULT_DEVICE_COOLDOWNS.keys())
            await self._send_response(
                f"⚠️ Unknown capability: '{capability}'\n"
                f"Available: {available}"
            )
            return

        DEFAULT_DEVICE_COOLDOWNS[capability] = minutes
        gcdAdjustment = {"cap": capability, "minutes": minutes}
        await self.eventManager.emit("AdjustDeviceGCD", gcdAdjustment)
        await self._send_response(
            f"✅ Global Cooldown for '{capability}' set to {minutes} minute(s)."
        )

    async def cmd_list(self, params: List[str]):
        """Listet verschiedene Informationen auf"""
        if not params:
            await self._send_response(
                "⚠️ Specify what to list.\n"
                "Usage: list <capabilities|devices>\n"
                "Use 'list -h' for help."
            )
            return
        
        list_type = params[0].lower()
        
        if list_type == "capabilities":
            caps = list(DEFAULT_DEVICE_COOLDOWNS.keys())
            response = f"📋 Available Capabilities ({len(caps)}):\n" + "="*50 + "\n"
            for cap in sorted(caps):
                response += f"  • {cap}\n"
            response += "="*50
            await self._send_response(response)
            
        elif list_type == "devices":
            # Beispiel - hier würdest du echte Geräte auflisten
            await self._send_response("📋 Device listing not yet implemented.")
            
        else:
            await self._send_response(
                f"⚠️ Unknown list type: '{list_type}'\n"
                "Available: capabilities, devices"
            )

    # =========================
    # UTILITY METHODS
    # =========================

    async def _send_response(self, message: str):
        """Sendet eine Response zurück zur Console"""
        event_type = "ogb_console_response"
        event_data = {
            "room": self.room,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        _LOGGER.debug(f"[{self.room}] Sending console response: {message}")
        self.hass.bus.async_fire(event_type, event_data)

    def get_command_list(self) -> List[str]:
        """Gibt Liste aller registrierten Commands zurück"""
        return list(self.commands.keys())

    def command_exists(self, command: str) -> bool:
        """Prüft, ob ein Command existiert"""
        return command.lower() in self.commands
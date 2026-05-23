import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from ..data.OGBParams.OGBParams import DEFAULT_DEVICE_COOLDOWNS
from ..utils.ambient import is_ambient_room

_LOGGER = logging.getLogger(__name__)


@dataclass
class CommandInfo:
    """Stores metadata about a command"""

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
        self.data_store = dataStore
        self.event_manager = eventManager
        self.is_initialized = False
        self.last_command = ""

        # Skip for ambient room - no console needed
        if is_ambient_room(self.room):
            _LOGGER.debug(f"{self.room}: Console Manager disabled - ambient room")
            return

        # Command registry with metadata
        self.commands: Dict[str, CommandInfo] = {}
        self._register_commands()

        self.hass.bus.async_listen("ogb_console_command", self._command_income)
        self.hass.bus.async_listen("ogb_get_commands", self._handle_get_commands)
        asyncio.create_task(self.init())
        
        # Reference to data_store_manager (set after initialization)
        self.data_store_manager = None
        self.calib_manager = None
    
    def set_data_store_manager(self, data_store_manager):
        """Set the data store manager for script storage access."""
        self.data_store_manager = data_store_manager

    def set_calib_manager(self, calib_manager):
        """Set the calibration manager for cap calibration commands."""
        self.calib_manager = calib_manager

    def _register_commands(self):
        """Registers all available commands with their metadata"""
        self.register_command(
            "help",
            self.cmd_help,
            "Shows available commands or details about a command",
            "help [command]",
            ["help", "help gcd"],
        )

        self.register_command(
            "version",
            self.cmd_version,
            "Shows the console version",
            "version",
            ["version"],
        )

        self.register_command(
            "test", self.cmd_test, "Executes a test command", "test", ["test"]
        )

        self.register_command(
            "gcd",
            self.cmd_gcd,
            "Sets or shows the global cooldown for a device capability",
            "gcd <capability> <minutes>",
            ["gcd light 5", "gcd cover 10"],
        )

        self.register_command(
            "list",
            self.cmd_list,
            "Lists available capabilities or devices",
            "list [caps|devices]",
            ["list caps", "list capabilities", "list devices"],
        )

        self.register_command(
            "device_states",
            self.cmd_device_states,
            "Shows current device states for debugging",
            "device_states",
            [],
        )

        # CropSteering Calibration Commands
        self.register_command(
            "cs_calibrate",
            self.cmd_cs_calibrate,
            "Starts VWC calibration for CropSteering",
            "cs_calibrate <max|min|stop> [phase]",
            ["cs_calibrate max", "cs_calibrate max p1", "cs_calibrate min p2", "cs_calibrate stop"],
        )

        self.register_command(
            "cs_status",
            self.cmd_cs_status,
            "Shows CropSteering status and calibration values",
            "cs_status",
            ["cs_status"],
        )

        # Script Mode Commands
        self.register_command(
            "script",
            self.cmd_script,
            "Script mode management",
            "script <status|save|load|template|backup|restore|validate>",
            ["script status", "script save", "script template basic_vpd_control"],
        )

        # Capability Calibration Commands
        self.register_command(
            "cap_calibrate",
            self.cmd_cap_calibrate,
            "Starts capability calibration (canHeat, canCool, canHumidify, canCO2, etc.)",
            "cap_calibrate <capability>",
            ["cap_calibrate canHeat", "cap_calibrate canCO2"],
        )

        self.register_command(
            "cap_cal_status",
            self.cmd_cap_cal_status,
            "Shows capability calibration status and stored results",
            "cap_cal_status",
            ["cap_cal_status"],
        )

        self.register_command(
            "calibrate_all_caps",
            self.cmd_calibrate_all_caps,
            "Calibrates all capabilities in order (humidify → dehumidify → heat → cool → light)",
            "calibrate_all_caps",
            ["calibrate_all_caps"],
        )

        self.register_command(
            "cap_cal_stop",
            self.cmd_cap_cal_stop,
            "Stops an ongoing capability calibration",
            "cap_cal_stop",
            ["cap_cal_stop"],
        )

        self.register_command(
            "get_tentdata",
            self.cmd_get_tentdata,
            "Shows current tentData and VPD values",
            "get_tentdata",
            ["get_tentdata"],
        )

        self.register_command(
            "get_week",
            self.cmd_get_week,
            "Shows current grow plan week data",
            "get_week",
            ["get_week"],
        )

        self.register_command(
            "get_costs",
            self.cmd_get_costs,
            "Shows energy consumption and costs for today, week, and month",
            "get_costs",
            ["get_costs"],
        )

        self.register_command(
            "reset_costs",
            self.cmd_reset_costs,
            "Resets all energy consumption data (daily, weekly, monthly)",
            "reset_costs [today|week|month|all]",
            ["reset_costs", "reset_costs today", "reset_costs all"],
        )

    def register_command(
        self,
        name: str,
        func: Callable,
        description: str,
        usage: str,
        examples: List[str] = None,
    ):
        """
        Public method to register new commands.
        Can be used externally to extend the console.
        """
        self.commands[name.lower()] = CommandInfo(
            func=func, description=description, usage=usage, examples=examples or []
        )
        _LOGGER.debug(f"Registered command: {name}")

    async def init(self):
        self.is_initialized = True
        await self._send_response(
            "🟢 Console Manager initialized. Type 'help' for available commands."
        )
        _LOGGER.debug(f"OGBConsoleManager initialized for room: {self.room}")

    async def _command_income(self, event):
        event_room = event.data.get("room")
        event_command = event.data.get("command")
        if event_room != self.room:
            return

        self.last_command = event_command
        _LOGGER.warning(f"[{self.room}] Received command: {event_command}")
        await self._handle_command(event_command)

    async def _handle_get_commands(self, event):
        """Handle request for available commands list."""
        event_room = event.data.get("room")
        request_id = event.data.get("request_id")
        
        if event_room != self.room:
            return
        
        _LOGGER.debug(f"[{self.room}] Received get_commands request")
        
        # Build commands dictionary
        commands = {}
        for cmd_name, cmd_info in self.commands.items():
            commands[cmd_name] = {
                "description": cmd_info.description,
                "usage": cmd_info.usage,
                "examples": cmd_info.examples,
            }
        
        # Send response event
        event_type = "ogb_commands_response"
        event_data = {
            "room": self.room,
            "commands": commands,
            "request_id": request_id,
        }
        self.hass.bus.async_fire(event_type, event_data)
        _LOGGER.debug(f"[{self.room}] Sent {len(commands)} commands")

    async def _handle_command(self, command: str):
        """General command handling with automatic help output"""
        parts = command.strip().split()
        if not parts:
            await self._send_response(
                "⚠️ Empty command. Type 'help' for available commands."
            )
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
                await self._send_response(
                    f"⚠️ Invalid arguments for '{cmd_name}'. Use '{cmd_name} -h' for help."
                )
                _LOGGER.error(f"Command execution error: {e}")
            except Exception as e:
                await self._send_response(
                    f"⚠️ Error executing command '{cmd_name}': {e}"
                )
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
        """Shows help information"""
        if not params:
            # Show all commands
            response = "📖 Available Commands:\n" + "=" * 50 + "\n"
            for cmd_name, cmd_info in sorted(self.commands.items()):
                response += f"\n• {cmd_name:<12} - {cmd_info.description}"
            response += f"\n\n{'='*50}\n"
            response += "Type 'help <command>' or '<command> -h' for details."
            await self._send_response(response)
        else:
            # Show details for specific command
            cmd_name = params[0].lower()
            cmd_info = self.commands.get(cmd_name)

            if not cmd_info:
                await self._send_response(f"⚠️ Unknown command: '{cmd_name}'")
                return

            response = f"📖 Help: {cmd_name}\n" + "=" * 50 + "\n"
            response += f"\nDescription: {cmd_info.description}\n"
            response += f"\nUsage: {cmd_info.usage}\n"

            if cmd_info.examples:
                response += "\nExamples:\n"
                for example in cmd_info.examples:
                    response += f"  $ {example}\n"

            response += "=" * 50
            await self._send_response(response)

    async def cmd_version(self, params: List[str]):
        """Shows version"""
        await self._send_response("🍀 OGB Console Version 1.0.1 🍀")

    async def cmd_test(self, params: List[str]):
        """Test command"""
        await self._send_response("✅ Test command executed successfully.")

    async def cmd_gcd(self, params: List[str]):
        """
        Sets or shows the global cooldown for a device capability.
        Usage: gcd <capability> <minutes>
        """
        if len(params) == 0:
            # Show all current GCDs from datastore
            response = "📊 Current Global Cooldowns:\n" + "=" * 50 + "\n"
            stored_cooldowns = self.data_store.get("deviceCooldowns")
            current_cooldowns = stored_cooldowns if stored_cooldowns else DEFAULT_DEVICE_COOLDOWNS
            for cap, minutes in current_cooldowns.items():
                response += f"  {cap:<15} : {minutes} min\n"
            response += "=" * 50
            await self._send_response(response)
            return

        if len(params) != 2:
            await self._send_response(
                "⚠️ Invalid arguments.\n"
                "Usage: gcd <capability> <minutes>\n"
                "Use 'gcd -h' for help."
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
                f"⚠️ Unknown capability: '{capability}'\n" f"Available: {available}"
            )
            return

        gcdAdjustment = {"cap": capability, "minutes": minutes}
        await self.event_manager.emit("AdjustDeviceGCD", gcdAdjustment)
        await self._send_response(
            f"✅ Global Cooldown for '{capability}' set to {minutes} minute(s)."
        )

    async def cmd_list(self, params: List[str]):
        """Listet verschiedene Informationen auf"""
        if not params:
            await self._send_response(
                "⚠️ Specify what to list.\n"
                "Usage: list <caps|devices>\n"
                "Use 'list -h' for help."
            )
            return

        list_type = params[0].lower()

        if list_type in ("capabilities", "caps"):
            # Get actual capabilities from datastore
            capabilities = self.data_store.get("capabilities") or {}
            
            # Filter active capabilities (state=True and count > 0)
            active_caps = []
            inactive_caps = []
            
            for cap_name, cap_data in capabilities.items():
                if isinstance(cap_data, dict):
                    state = cap_data.get("state", False)
                    count = cap_data.get("count", 0)
                    entities = cap_data.get("devEntities", [])
                    
                    if state and count > 0:
                        active_caps.append((cap_name, count, entities))
                    else:
                        inactive_caps.append(cap_name)
            
            response = f"📋 Active Capabilities ({len(active_caps)}):\n" + "=" * 50 + "\n"
            for cap_name, count, entities in sorted(active_caps):
                response += f"  ✅ {cap_name} ({count} devices)\n"
                for entity in entities[:3]:  # Show max 3 entities
                    response += f"      • {entity}\n"
                if len(entities) > 3:
                    response += f"      ... and {len(entities) - 3} more\n"
            
            if inactive_caps:
                response += f"\n📋 Inactive Capabilities ({len(inactive_caps)}):\n"
                for cap in sorted(inactive_caps):
                    response += f"  ❌ {cap}\n"
            
            response += "=" * 50
            await self._send_response(response)

        elif list_type == "devices":
            # Get devices from datastore
            devices = self.data_store.get("devices") or []
            
            if not devices:
                await self._send_response("📋 No devices registered.")
                return
            
            response = f"📋 Registered Devices ({len(devices)}):\n" + "=" * 70 + "\n"
            
            for idx, device in enumerate(devices, 1):
                device_name = getattr(device, 'deviceName', 'Unknown')
                device_type = getattr(device, 'deviceType', 'Unknown')
                room = getattr(device, 'inRoom', 'Unknown')
                is_running = getattr(device, 'isRunning', False)
                is_dimmable = getattr(device, 'isDimmable', False)
                is_special = getattr(device, 'isSpecialDevice', False)
                device_label = getattr(device, 'deviceLabel', 'N/A')
                
                response += f"\n{idx}. 📱 {device_name}\n"
                response += f"   📋 Type: {device_type}\n"
                response += f"   🏠 Room: {room}\n"
                response += f"   🏷️  Label: {device_label}\n"
                response += f"   🟢 Status: {'Running' if is_running else 'Stopped'}\n"
                response += f"   🎚️  Dimmable: {'Yes' if is_dimmable else 'No'}\n"
                response += f"   ⭐ Special: {'Yes' if is_special else 'No'}\n"
                
                # Duty cycle for dimmable devices
                if is_dimmable:
                    duty_cycle = getattr(device, 'dutyCycle', None)
                    if duty_cycle is not None:
                        response += f"   ⚡ Duty Cycle: {duty_cycle}%\n"
                
                # Get switches with details
                switches = getattr(device, 'switches', [])
                if switches:
                    response += f"\n   🔌 Switches ({len(switches)}):\n"
                    for switch in switches:
                        entity_id = switch.get('entity_id', 'Unknown')
                        value = switch.get('value', 'unknown')
                        friendly_name = switch.get('friendly_name', entity_id.split('.')[-1] if '.' in entity_id else entity_id)
                        response += f"      • {friendly_name}: {value}\n"
                
                # Get sensors with details
                sensors = getattr(device, 'sensors', [])
                if sensors:
                    response += f"\n   📊 Sensors ({len(sensors)}):\n"
                    for sensor in sensors:
                        entity_id = sensor.get('entity_id', 'Unknown')
                        value = sensor.get('value', 'unknown')
                        friendly_name = sensor.get('friendly_name', entity_id.split('.')[-1] if '.' in entity_id else entity_id)
                        unit = sensor.get('unit_of_measurement', '')
                        response += f"      • {friendly_name}: {value} {unit}\n"
                
                # Options if available
                options = getattr(device, 'options', [])
                if options:
                    response += f"\n   ⚙️  Options ({len(options)}):\n"
                    for opt in options:
                        opt_name = opt.get('name', 'Unknown')
                        opt_value = opt.get('value', 'N/A')
                        response += f"      • {opt_name}: {opt_value}\n"
            
            response += "\n" + "=" * 70
            await self._send_response(response)

        else:
            await self._send_response(
                f"⚠️ Unknown list type: '{list_type}'\n"
                "Available: capabilities, devices"
            )

    async def cmd_device_states(self, params: List[str]):
        """Shows current device states for debugging"""
        devices = self.data_store.get("devices") or []

        if not devices:
            await self._send_response("📋 No devices found.")
            return

        response = "🔍 Current Device States:\n" + "=" * 60 + "\n"

        for device in devices:
            response += f"\n📱 {device.deviceName} ({device.deviceType})\n"
            response += f"   Running: {'Yes' if device.isRunning else 'No'}\n"
            is_dimmable = getattr(device, 'isDimmable', False)
            response += f"   Dimmable: {'Yes' if is_dimmable else 'No'}\n"

            if is_dimmable:
                if hasattr(device, "voltage") and device.voltage is not None:
                    response += f"   Voltage: {device.voltage}%\n"
                if hasattr(device, "dutyCycle") and device.dutyCycle is not None:
                    response += f"   Duty Cycle: {device.dutyCycle}%\n"

            # Add entity states
            switches = getattr(device, 'switches', [])
            if switches:
                response += f"   Switches ({len(switches)}):\n"
                for switch in switches:
                    value = switch.get("value", "unknown")
                    response += f"     • {switch['entity_id']}: {value}\n"

            sensors = getattr(device, 'sensors', [])
            if sensors:
                response += f"   Sensors ({len(sensors)}):\n"
                for sensor in sensors:
                    value = sensor.get("value", "unknown")
                    response += f"     • {sensor['entity_id']}: {value}\n"

            response += "\n"

        response += "=" * 60
        await self._send_response(response)

    # =========================
    # CROP STEERING COMMANDS
    # =========================

    async def cmd_cs_calibrate(self, params: List[str]):
        """
        Starts or stops VWC calibration.
        Usage: cs_calibrate <max|min|stop> [phase]
        """
        if not params:
            await self._send_response(
                "⚠️ Missing action.\n"
                "Usage: cs_calibrate <max|min|stop> [phase]\n"
                "Actions:\n"
                "  max  - Kalibriert VWC Maximum (Sättigung)\n"
                "  min  - Kalibriert VWC Minimum (Dryback)\n"
                "  stop - Stops ongoing calibration\n"
                "Phases: p1, p2, p3 (default: p1)"
            )
            return

        action = params[0].lower()
        phase = params[1].lower() if len(params) > 1 else "p1"

        # Validate action
        if action not in ["max", "min", "stop"]:
            await self._send_response(
                f"⚠️ Unknown action: '{action}'\n"
                "Valid actions: max, min, stop"
            )
            return

        # Validate phase
        if phase not in ["p1", "p2", "p3"]:
            await self._send_response(
                f"⚠️ Invalid phase: '{phase}'\n"
                "Valid phases: p1, p2, p3"
            )
            return

        # Check if CropSteering is in Automatic mode
        current_mode = self.data_store.getDeep("CropSteering.ActiveMode") or ""
        if "Automatic" not in current_mode and action != "stop":
            await self._send_response(
                "⚠️ VWC Calibration only available in Automatic Mode.\n"
                f"Current mode: {current_mode or 'Not set'}"
            )
            return

        # Build command data
        if action == "stop":
            command_data = {"action": "stop"}
            await self._send_response("🛑 Stopping VWC calibration...")
        else:
            command_data = {"action": f"start_{action}", "phase": phase}
            cal_type = "Maximum (Sättigung)" if action == "max" else "Minimum (Dryback)"
            await self._send_response(
                f"🔄 Starting VWC {cal_type} calibration for phase {phase.upper()}...\n"
                "This may take several minutes. Watch the logs for progress."
            )

        # Emit calibration command
        await self.event_manager.emit("VWCCalibrationCommand", command_data)

    async def cmd_cs_status(self, params: List[str]):
        """Shows CropSteering status and calibration values"""
        response = "🌱 CropSteering Status:\n" + "=" * 50 + "\n"

        # Mode info
        mode = self.data_store.getDeep("CropSteering.Mode") or "Not set"
        active_mode = self.data_store.getDeep("CropSteering.ActiveMode") or "Not set"
        active = self.data_store.getDeep("CropSteering.Active") or False
        current_phase = self.data_store.getDeep("CropSteering.currentPhase") or "Unknown"

        response += f"\n📊 Mode: {mode}\n"
        response += f"   Active Mode: {active_mode}\n"
        response += f"   Active: {'Yes' if active else 'No'}\n"
        response += f"   Current Phase: {current_phase}\n"

        # Current sensor values
        vwc = self.data_store.getDeep("CropSteering.vwc_current")
        ec = self.data_store.getDeep("CropSteering.ec_current")
        
        response += f"\n📈 Current Readings:\n"
        response += f"   VWC: {vwc:.1f}%\n" if vwc else "   VWC: N/A\n"
        response += f"   EC: {ec:.2f} mS/cm\n" if ec else "   EC: N/A\n"

        # Calibration values
        response += f"\n🔧 Calibration Values:\n"
        for phase in ["p1", "p2", "p3"]:
            vwc_max = self.data_store.getDeep(f"CropSteering.Calibration.{phase}.VWCMax")
            vwc_min = self.data_store.getDeep(f"CropSteering.Calibration.{phase}.VWCMin")
            timestamp = self.data_store.getDeep(f"CropSteering.Calibration.{phase}.timestamp")
            
            if vwc_max or vwc_min:
                response += f"   {phase.upper()}:\n"
                if vwc_max:
                    response += f"      VWC Max: {vwc_max:.1f}%\n"
                if vwc_min:
                    response += f"      VWC Min: {vwc_min:.1f}%\n"
                if timestamp:
                    response += f"      Last Cal: {timestamp[:16]}\n"
            else:
                response += f"   {phase.upper()}: Not calibrated\n"

        response += "\n" + "=" * 50
        response += "\n💡 Use 'cs_calibrate max' or 'cs_calibrate min' to calibrate"
        
        await self._send_response(response)

    # =========================
    # SCRIPT MODE COMMANDS
    # =========================

    async def cmd_script(self, params: List[str]):
        """Script mode management"""
        if not params:
            await self._send_response(
                "⚠️ Missing subcommand.\n"
                "Usage: script <status|save|load|template|backup|restore|validate>\n"
                "Use 'script -h' for help."
            )
            return
        
        if not self.data_store_manager:
            await self._send_response(
                "❌ Script storage not available.\n"
                "DataStoreManager not initialized."
            )
            return
        
        subcommand = params[0].lower()
        
        if subcommand == "status":
            # Check if script exists
            script = await self.data_store_manager.load_script(self.room)
            if script:
                enabled = "✅ Enabled" if script.get("enabled") else "❌ Disabled"
                script_type = script.get("type", "dsl")
                await self._send_response(
                    f"📜 Script Status for {self.room}:\n"
                    f"   Status: {enabled}\n"
                    f"   Type: {script_type}\n"
                    f"   Script file exists: Yes"
                )
            else:
                await self._send_response(
                    f"📜 Script Status for {self.room}:\n"
                    f"   Status: ❌ No script configured\n"
                    f"   Use 'script template <name>' to load a template."
                )
        
        elif subcommand == "template":
            if len(params) < 2:
                await self._send_response(
                    "⚠️ Missing template name.\n"
                    "Available templates: basic_vpd_control, advanced_environment"
                )
                return
            
            template_name = params[1]
            template = self.data_store_manager.load_template(template_name)
            
            if template:
                # Save template as current script
                success = await self.data_store_manager.save_script(self.room, template)
                if success:
                    await self._send_response(
                        f"✅ Template '{template_name}' loaded and saved.\n"
                        f"   Script Mode will use this script on next cycle."
                    )
                else:
                    await self._send_response(
                        f"❌ Failed to save template '{template_name}'."
                    )
            else:
                await self._send_response(
                    f"❌ Template '{template_name}' not found.\n"
                    f"Available: basic_vpd_control, advanced_environment"
                )
        
        elif subcommand == "load":
            # Force reload from file
            script = await self.data_store_manager.load_script(self.room)
            if script:
                await self._send_response(
                    f"✅ Script reloaded from file for {self.room}."
                )
            else:
                await self._send_response(
                    f"❌ No script file found for {self.room}."
                )
        
        elif subcommand == "backup":
            # Show backup status
            backup_path = self.data_store_manager._get_script_path(self.room, backup=True)
            if os.path.exists(backup_path):
                await self._send_response(
                    f"✅ Backup exists for {self.room}.\n"
                    f"   Use 'script restore' to restore it."
                )
            else:
                await self._send_response(
                    f"ℹ️ No backup found for {self.room}.\n"
                    f"   A backup is created automatically when saving."
                )
        
        elif subcommand == "restore":
            success = await self.data_store_manager.restore_script_backup(self.room)
            if success:
                await self._send_response(
                    f"✅ Script restored from backup for {self.room}."
                )
            else:
                await self._send_response(
                    f"❌ Failed to restore backup for {self.room}.\n"
                    f"   No backup file found."
                )
        
        elif subcommand == "validate":
            script = await self.data_store_manager.load_script(self.room)
            if not script:
                await self._send_response(
                    f"❌ No script found for {self.room}."
                )
                return
            
            # Basic validation
            script_code = script.get("script", "")
            lines = script_code.strip().split("\n")
            
            await self._send_response(
                f"✅ Script validation for {self.room}:\n"
                f"   Lines: {len(lines)}\n"
                f"   Type: {script.get('type', 'dsl')}\n"
                f"   Enabled: {script.get('enabled', False)}"
            )
        
        elif subcommand == "save":
            await self._send_response(
                "ℹ️ Scripts are saved automatically when using 'script template'.\n"
                f"   Current script for {self.room} is already persisted to file."
            )
        
        else:
            await self._send_response(
                f"⚠️ Unknown subcommand: '{subcommand}'\n"
                f"Available: status, template, load, backup, restore, validate"
            )

    # =========================
    # CAPABILITY CALIBRATION COMMANDS
    # =========================

    def _resolve_cap(self, cap_input: str) -> str:
        """Case-insensitive capability lookup. Returns exact key or empty string."""
        capabilities = self.data_store.get("capabilities") or {}
        for key in capabilities:
            if key.lower() == cap_input.lower():
                return key
        return ""

    async def cmd_cap_calibrate(self, params: List[str]):
        """Starts capability calibration."""
        if not params:
            await self._send_response(
                "⚠️ Missing capability.\n"
                "Usage: cap_calibrate <capability>\n"
                "Examples: cap_calibrate canHeat, cap_calibrate canCO2"
            )
            return

        cap = self._resolve_cap(params[0])
        if not cap:
            capabilities = self.data_store.get("capabilities") or {}
            available = ", ".join(sorted(capabilities.keys()))
            await self._send_response(
                f"⚠️ Unknown capability: '{params[0]}'\n"
                f"Available: {available}"
            )
            return

        # Emit calibration start event
        event_type = "ogb_cap_calibration_command"
        event_data = {"room": self.room, "action": "start", "cap": cap}
        _LOGGER.debug(f"[Console {self.room}] Firing event {event_type} with {event_data}")
        self.hass.bus.async_fire(event_type, event_data)

        await self._send_response(
            f"🔄 Calibration start requested for '{cap}'.\n"
            "Check 'cap_cal_status' for progress."
        )

    async def cmd_cap_cal_status(self, params: List[str]):
        """Shows capability calibration status and stored results."""
        raw_active = self.data_store.getDeep("capCalibration.active")
        _LOGGER.debug(f"[Console {self.room}] cap_cal_status read capCalibration.active = {raw_active}")
        response = "🔧 Capability Calibration Status:\n" + "=" * 50 + "\n"

        # Active calibration
        active = self.data_store.getDeep("capCalibration.active")
        if active:
            response += (
                f"\n▶️  Active calibration:\n"
                f"   Capability: {active.get('cap')}\n"
                f"   State: {active.get('state')}\n"
                f"   Started: {active.get('started_at', 'N/A')[:19]}\n"
            )
        else:
            response += "\nℹ️  No calibration running.\n"

        # Stored results
        results = self.data_store.getDeep("capCalibration.results", {})
        if results:
            response += "\n📊 Stored Results:\n"
            for cap, data in sorted(results.items()):
                response += f"\n   {cap}:\n"
                response += f"      Timestamp: {data.get('timestamp', 'N/A')[:19]}\n"
                for metric_key, metric_data in data.items():
                    if metric_key == "timestamp":
                        continue
                    if isinstance(metric_data, dict):
                        dpm = metric_data.get('delta_per_min', 0)
                        conf = metric_data.get('confidence', 0)
                        response += (
                            f"      {metric_key}: {dpm:+.3f}/min "
                            f"(confidence: {conf})\n"
                        )
                    else:
                        response += f"      {metric_key}: {metric_data}\n"
        else:
            response += "\n📊 No calibration results stored yet.\n"

        response += "\n" + "=" * 50
        response += "\n💡 Use 'cap_calibrate <cap>' to start a new calibration"
        await self._send_response(response)

    async def cmd_cap_cal_stop(self, params: List[str]):
        """Stops an ongoing capability calibration."""
        active = self.data_store.getDeep("capCalibration.active")
        if not active:
            await self._send_response("ℹ️ No calibration is currently running.")
            return

        event_type = "ogb_cap_calibration_command"
        event_data = {"room": self.room, "action": "stop"}
        self.hass.bus.async_fire(event_type, event_data)

        await self._send_response("🛑 Stop signal sent for active calibration.")

    async def cmd_get_tentdata(self, params: List[str]):
        """Shows current tentData and VPD values."""
        tent_data = self.data_store.get("tentData") or {}
        vpd_data = self.data_store.get("vpd") or {}
        
        # Format the data nicely for console output
        lines = [
            "📊 Current Tent Data:",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"🌡️  Temperature: {tent_data.get('temperature', 'N/A')}°C",
            f"🍃 Leaf Temp: {tent_data.get('leafTemperature', 'N/A')}°C",
            f"🍃 Leaf Temp Offset: {tent_data.get('leafTempOffset', 'N/A')}°C",
            f"💧 Humidity: {tent_data.get('humidity', 'N/A')}%",
            f"🌫️  Dew Point: {tent_data.get('dewpoint', 'N/A')}°C",
            f"📈  CO2 Level: {tent_data.get('co2Level', 'N/A')}PPM",
            "",
            "📏 Limits:",
            f"   Temp: {tent_data.get('minTemp', 'N/A')}°C - {tent_data.get('maxTemp', 'N/A')}°C",
            f"   Humidity: {tent_data.get('minHumidity', 'N/A')}% - {tent_data.get('maxHumidity', 'N/A')}%",
            "",
            "📈 VPD Data:",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"   Current: {vpd_data.get('current', 'N/A')} kPa",
            f"   Target: {vpd_data.get('targeted', 'N/A')} kPa",
            f"   Target Range: {vpd_data.get('targetedMin', 'N/A')} - {vpd_data.get('targetedMax', 'N/A')} kPa",
            f"   Perfection: {vpd_data.get('perfection', 'N/A')} kPa",
            f"   Perfect Range: {vpd_data.get('perfectMin', 'N/A')} - {vpd_data.get('perfectMax', 'N/A')} kPa",
            f"   Tolerance: {vpd_data.get('tolerance', 'N/A')}%",
            "",
            "🌡️  Environment:",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"   Ambient Temp: {tent_data.get('AmbientTemp', 'N/A')}°C",
            f"   Ambient Hum: {tent_data.get('AmbientHum', 'N/A')}%",
            f"   Outside Temp: {tent_data.get('OutsiteTemp', 'N/A')}°C",
            f"   Outside Hum: {tent_data.get('OutsiteHum', 'N/A')}%",
        ]
        
        # Add CO2 if available
        co2_level = tent_data.get('co2Level')
        if co2_level is not None:
            lines.extend([
                "",
                "💨 CO2:",
                f"   Level: {co2_level} ppm",
            ])
        
        # Add light data if available
        ppfd = tent_data.get('PPFD')
        dli = tent_data.get('DLI')
        if ppfd is not None or dli is not None:
            lines.extend([
                "",
                "💡 Light:",
            ])
            if ppfd is not None:
                lines.append(f"   PPFD: {ppfd} µmol/m²/s")
            if dli is not None:
                lines.append(f"   DLI: {dli} mol/m²/day")
        
        response = "\n".join(lines)
        await self._send_response(response)

    async def cmd_get_week(self, params: List[str]):
        """Shows current grow plan week data from the API."""
        grow_plan = self.data_store.getDeep("growPlan") or {}
        week_data = grow_plan.get("currentWeekData")
        current_week = grow_plan.get("currentWeek")
        plan_id = grow_plan.get("id")
        plan_name = grow_plan.get("name")
        total_weeks = grow_plan.get("totalWeeks")

        if not plan_id:
            await self._send_response("📋 Kein aktiver GrowPlan.")
            return

        lines = [
            f"🌱 {plan_name or 'Unnamed'} — Woche {current_week}/{total_weeks}",
            f"   ID: {plan_id}",
        ]

        if week_data:
            env = week_data.get("environment", {})
            temp = env.get("temperature", {})
            humidity = env.get("humidity", {})
            vpd = env.get("vpd", {})
            co2 = env.get("co2", {})
            light_cycle = env.get("lightCycle", {})

            day_temp = temp.get("day", {})
            if isinstance(day_temp, dict):
                day_temp_val = day_temp.get("max", "?")
            else:
                day_temp_val = day_temp

            night_temp = temp.get("night", {})
            if isinstance(night_temp, dict):
                night_temp_val = night_temp.get("min", "?")
            else:
                night_temp_val = night_temp

            lines += [
                "",
                f"Stage: {week_data.get('stage', '?')}   Mode: {week_data.get('tentMode', '?')}",
                "",
                f"🌡️  Tag: {day_temp_val}°C  Nacht: {night_temp_val}°C",
                f"💧 Tag: {humidity.get('day', '?')}%  Nacht: {humidity.get('night', '?')}%",
                f"🎯 VPD: {vpd.get('target', '?')} kPa",
                f"💨 CO₂: {co2.get('optimal', '?')} ppm ({co2.get('min', '?')}–{co2.get('max', '?')})",
                f"💡 Licht: {light_cycle.get('on', '?')}h an / {light_cycle.get('off', '?')}h aus",
            ]

            tc = week_data.get("tentControls", {})
            if tc:
                lines += [
                    "",
                    "🎛️  Controls:",
                    f"   Night VPD Hold: {tc.get('nightVpdHold', {}).get('enabled', '?')}",
                    f"   Dampening: {tc.get('deviceDampening', {}).get('enabled', '?')}",
                    f"   VPD Mode: {tc.get('vpdDetermination', {}).get('mode', '?')}",
                ]
        else:
            lines.append("\n⚠️  Keine Wochendaten")

        await self._send_response("\n".join(lines))

    async def cmd_get_costs(self, params: List[str]):
        """Shows detailed energy consumption, costs, and device statistics."""
        from datetime import datetime, timedelta
        
        energy_data = self.data_store.getDeep("Energy", {})
        today_str = datetime.now().strftime("%Y-%m-%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        week_str = datetime.now().strftime("%Y-W%W")
        month_str = datetime.now().strftime("%Y-%m")
        
        daily = energy_data.get("daily", {}).get(today_str, {})
        yesterday = energy_data.get("daily", {}).get(yesterday_str, {})
        weekly = energy_data.get("weekly", {}).get(week_str, {})
        monthly = energy_data.get("monthly", {}).get(month_str, {})
        price = energy_data.get("price_per_kwh", 0.35)
        currency = energy_data.get("currency", "EUR")
        
        today_kwh = daily.get('kwh', 0.0)
        yesterday_kwh = yesterday.get('kwh', 0.0)
        
        lines = [
            f"💡 Energy Dashboard - {self.room}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 Today ({today_str}):",
            f"   ⚡ Energy: {today_kwh:.3f} kWh",
            f"   💰 Cost: {daily.get('cost', 0.0):.2f} {currency}",
        ]
        
        # Compare with yesterday
        if yesterday_kwh > 0:
            diff = today_kwh - yesterday_kwh
            diff_pct = (diff / yesterday_kwh) * 100
            arrow = "📈" if diff >= 0 else "📉"
            lines.append(f"   {arrow} vs Yesterday: {abs(diff):.3f} kWh ({abs(diff_pct):.1f}%)")
        
        # Runtime stats
        runtime = daily.get("runtime", {})
        if runtime:
            total_runtime = sum(runtime.values())
            avg_runtime = total_runtime / len(runtime) if runtime else 0
            lines.append(f"   ⏱️  Total Runtime: {total_runtime:.1f}h (avg: {avg_runtime:.1f}h/device)")
        
        lines.extend([
            "",
            f"📊 This Week ({week_str}):",
            f"   ⚡ Energy: {weekly.get('kwh', 0.0):.3f} kWh",
            f"   💰 Cost: {weekly.get('cost', 0.0):.2f} {currency}",
            "",
            f"📊 This Month ({month_str}):",
            f"   ⚡ Energy: {monthly.get('kwh', 0.0):.3f} kWh",
            f"   💰 Cost: {monthly.get('cost', 0.0):.2f} {currency}",
            "",
            f"⚙️  Price: {price:.2f} {currency}/kWh",
        ])
        
        # Device Ranking & Statistics
        if runtime and today_kwh > 0:
            lines.extend(["", "🏆 Device Ranking (Today):"])
            
            # Calculate per-device energy from runtime proportion
            device_stats = []
            for device_name, hours in runtime.items():
                device_kwh = today_kwh * (hours / max(total_runtime, 0.01))
                pct = (device_kwh / max(today_kwh, 0.001)) * 100
                avg_power = (device_kwh / max(hours, 0.01)) * 1000  # Watts
                device_stats.append({
                    'name': device_name,
                    'kwh': device_kwh,
                    'hours': hours,
                    'pct': pct,
                    'avg_power': avg_power,
                })
            
            # Sort by energy consumption (descending)
            device_stats.sort(key=lambda x: x['kwh'], reverse=True)
            
            # Top consumer highlight
            if device_stats:
                top = device_stats[0]
                lines.append(f"   👑 Top Consumer: {top['name']} ({top['pct']:.1f}%)")
                lines.append("")
            
            # All devices table
            lines.append("   Device          kWh     %    Hours  Avg W")
            lines.append("   ─────────────────────────────────────────")
            for stat in device_stats:
                lines.append(
                    f"   {stat['name']:<14} {stat['kwh']:>6.3f} {stat['pct']:>5.1f}% "
                    f"{stat['hours']:>5.1f}h {stat['avg_power']:>6.1f}W"
                )
        
        # Currently Active Devices with Real-Time Power
        try:
            main_controller = None
            for entry_id, coord in self.hass.data.get("opengrowbox", {}).items():
                if entry_id != "sensors" and hasattr(coord, 'room_name'):
                    if coord.room_name == self.room and hasattr(coord, 'OGB'):
                        main_controller = coord.OGB
                        break
            
            if main_controller and hasattr(main_controller, 'energy_manager'):
                energy_mgr = main_controller.energy_manager
                active_devices = energy_mgr._device_tracking
                
                if active_devices:
                    lines.extend(["", "⚡ Currently Active:"])
                    
                    # Sort by power consumption
                    sorted_active = sorted(
                        active_devices.items(),
                        key=lambda x: x[1].get('power_watts', 0),
                        reverse=True
                    )
                    
                    total_power = sum(t.get('power_watts', 0) for t in active_devices.values())
                    lines.append(f"   Total Power Draw: {total_power:.1f}W")
                    lines.append("")
                    lines.append("   Device          Power   Type    Session kWh")
                    lines.append("   ───────────────────────────────────────────")
                    
                    for device_name, tracking in sorted_active:
                        power = tracking.get("power_watts", 0.0)
                        is_estimated = tracking.get("is_estimated", True)
                        session_kwh = tracking.get("session_kwh", 0.0)
                        source = "EST" if is_estimated else "SNS"
                        lines.append(
                            f"   {device_name:<14} {power:>6.1f}W {source:>6} {session_kwh:>9.4f}"
                        )
        except Exception as e:
            _LOGGER.debug(f"[{self.room}] Could not get active device info: {e}")
        
        response = "\n".join(lines)
        await self._send_response(response)

    async def cmd_reset_costs(self, params: List[str]):
        """Reset energy consumption data."""
        scope = params[0].lower() if params else "all"
        valid_scopes = ["today", "week", "month", "all"]
        
        if scope not in valid_scopes:
            await self._send_response(
                f"⚠️ Invalid scope: '{scope}'\n"
                f"Usage: reset_costs [today|week|month|all]\n"
                f"Examples: reset_costs, reset_costs today, reset_costs all"
            )
            return
        
        energy_data = self.data_store.getDeep("Energy", {})
        today_str = datetime.now().strftime("%Y-%m-%d")
        week_str = datetime.now().strftime("%Y-W%W")
        month_str = datetime.now().strftime("%Y-%m")
        
        reset_items = []
        
        if scope in ["today", "all"]:
            if today_str in energy_data.get("daily", {}):
                energy_data["daily"][today_str] = {"kwh": 0.0, "cost": 0.0, "runtime": {}}
                reset_items.append("Today's data")
        
        if scope in ["week", "all"]:
            if week_str in energy_data.get("weekly", {}):
                energy_data["weekly"][week_str] = {"kwh": 0.0, "cost": 0.0}
                reset_items.append("This week's data")
        
        if scope in ["month", "all"]:
            if month_str in energy_data.get("monthly", {}):
                energy_data["monthly"][month_str] = {"kwh": 0.0, "cost": 0.0}
                reset_items.append("This month's data")
        
        # Reset active device session tracking
        if scope == "all":
            try:
                main_controller = None
                for entry_id, coord in self.hass.data.get("opengrowbox", {}).items():
                    if entry_id != "sensors" and hasattr(coord, 'room_name'):
                        if coord.room_name == self.room and hasattr(coord, 'OGB'):
                            main_controller = coord.OGB
                            break
                
                if main_controller and hasattr(main_controller, 'energy_manager'):
                    energy_mgr = main_controller.energy_manager
                    for device_name in list(energy_mgr._device_tracking.keys()):
                        energy_mgr._device_tracking[device_name]["session_kwh"] = 0.0
                    reset_items.append("Active device sessions")
            except Exception as e:
                _LOGGER.debug(f"[{self.room}] Could not reset active sessions: {e}")
        
        # Save back to datastore
        self.data_store.setDeep("Energy", energy_data)
        
        if reset_items:
            response = f"✅ Reset {scope} energy data:\n"
            response += "\n".join(f"   • {item}" for item in reset_items)
        else:
            response = f"ℹ️ No {scope} energy data found to reset"
        
        await self._send_response(response)

    async def cmd_calibrate_all_caps(self, params: List[str]):
        """Calibrates all capabilities in sequence: humidify → dehumidify → heat → cool → light."""
        if not self.calib_manager:
            await self._send_response("❌ Calibration manager not available.")
            return

        # Check if calibration already running
        active = self.data_store.getDeep("capCalibration.active")
        if active:
            await self._send_response(
                "⚠️ Calibration already running.\n"
                "Use 'cap_cal_stop' to abort current calibration first."
            )
            return

        # Define calibration order - light last (takes 15 min!)
        CALIBRATION_ORDER = [
            "canHumidify",
            "canDehumidify",
            "canHeat",
            "canCool",
            "canIntake",
            "canExhaust",
            "canVentilation",
            "canLight",
        ]

        capabilities = self.data_store.get("capabilities") or {}

        # Build list of caps that have devices
        caps_to_calibrate = []
        skipped = []
        for cap in CALIBRATION_ORDER:
            cap_data = capabilities.get(cap, {})
            dev_entities = cap_data.get("devEntities", [])
            if dev_entities:
                caps_to_calibrate.append(cap)
            else:
                skipped.append(cap)

        if not caps_to_calibrate:
            await self._send_response(
                "ℹ️ No devices found for calibration.\n"
                "Assign devices to capabilities first."
            )
            return

        # Send initial message
        total_time = 0
        for cap in caps_to_calibrate:
            if cap == "canLight":
                total_time += 900  # 15 min
            elif cap in ("canHeat", "canCool", "canHumidify", "canDehumidify", "canIntake", "canExhaust"):
                total_time += 300  # 5 min each
            elif cap == "canVentilation":
                total_time += 180  # 3 min (faster)
        total_time += 180  # baseline
        total_time += 60   # cooldown

        await self._send_response(
            f"🔄 Starting full calibration sequence...\n"
            f"📋 Capabilities: {', '.join(caps_to_calibrate)}\n"
            f"⏱️ Estimated time: ~{total_time // 60} minutes\n"
            f"🚫 Skipped (no devices): {', '.join(skipped) if skipped else 'none'}\n\n"
            f"Tent mode will stay disabled during entire sequence!"
        )

        # Run calibrations sequentially
        completed = []
        failed = []

        for i, cap in enumerate(caps_to_calibrate):
            # Verify devices exist for this capability
            cap_data = capabilities.get(cap, {})
            dev_entities = cap_data.get("devEntities", [])
            if not dev_entities:
                await self._send_response(f"⚠️ No devices for {cap}, skipping.")
                continue

            # Show starting message FIRST
            await self._send_response(
                f"\n{'='*50}\n"
                f"🔧 Calibrating {cap} ({i+1}/{len(caps_to_calibrate)})...\n"
                f"{'='*50}"
            )

            # Start calibration using sequence method (keeps tent mode disabled)
            task = asyncio.create_task(self.calib_manager.start_calibration_for_sequence(cap))

            # Wait for calibration to actually start (poll for up to 10 seconds)
            started = False
            for _ in range(20):  # 20 * 0.5s = 10 seconds
                await asyncio.sleep(0.5)
                if self.data_store.getDeep("capCalibration.active"):
                    started = True
                    break

            if not started:
                await task  # Ensure task completes
                failed.append(cap)
                await self._send_response(f"❌ {cap} failed to start!")
                continue

            # Wait for calibration to complete
            wait_count = 0
            while True:
                active = self.data_store.getDeep("capCalibration.active")
                if not active:
                    break
                await asyncio.sleep(5)
                wait_count += 1
                # Timeout after 20 minutes per calibration
                if wait_count > 240:
                    await self._send_response(f"⚠️ Timeout waiting for {cap}, stopping...")
                    await self.calib_manager.stop_calibration()
                    break

            # Check results AFTER calibration is complete
            results = self.data_store.getDeep(f"capCalibration.results.{cap}")
            if results:
                completed.append(cap)
                await self._send_response(f"✅ {cap} completed!")
            else:
                failed.append(cap)
                await self._send_response(f"❌ {cap} failed! Check HA logs for details.")

            # Small delay between calibrations to let system stabilize
            await asyncio.sleep(2)

        # Restore original tent mode after sequence is complete
        await self.calib_manager.finish_sequence()

        # Summary
        summary = [
            "\n" + "="*50,
            "📊 FULL CALIBRATION COMPLETE",
            "="*50,
        ]
        if completed:
            summary.append(f"✅ Completed: {', '.join(completed)}")
        if failed:
            summary.append(f"❌ Failed: {', '.join(failed)}")

        summary.append("="*50)
        await self._send_response("\n".join(summary))

    # =========================
    # UTILITY METHODS
    # =========================

    async def _send_response(self, message: str):
        """Sends a response back to the console"""
        event_type = "ogb_console_response"
        event_data = {
            "room": self.room,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        _LOGGER.debug(f"[{self.room}] Sending console response: {message}")
        self.hass.bus.async_fire(event_type, event_data)

    def get_command_list(self) -> List[str]:
        """Returns list of all registered commands"""
        return list(self.commands.keys())

    def command_exists(self, command: str) -> bool:
        """Checks if a command exists"""
        return command.lower() in self.commands

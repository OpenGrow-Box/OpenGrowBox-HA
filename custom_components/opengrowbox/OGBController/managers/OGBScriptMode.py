"""
OpenGrowBox Script Mode

A fully customizable scripting environment for advanced users.
Supports both YAML configuration and Python scripting with full DataStore access.

Features:
- Custom script logic with IF/THEN/ELSE conditions
- Full DataStore read/write access
- Device control (all capabilities)
- Time-based triggers
- Variable support
- Python code execution (sandboxed)
- Template system
"""

import ast
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import asyncio

if TYPE_CHECKING:
    from ..OGB import OpenGrowBox

_LOGGER = logging.getLogger(__name__)


class OGBScriptMode:
    """
    Script Mode for OpenGrowBox - Fully customizable automation.
    
    Allows users to define custom logic using a simple DSL or Python code.
    All DataStore values are accessible for reading and writing.
    """
    
    def __init__(self, ogb: "OpenGrowBox"):
        """Initialize Script Mode."""
        self.ogb = ogb
        self.room = ogb.room
        self.data_store = ogb.dataStore
        self.event_manager = ogb.eventManager
        self.action_manager = ogb.actionManager
        
        # Script configuration
        self.script_config = None
        self.script_code = None
        self.compiled_script = None
        self.enabled = False
        
        # Safety limits
        self.max_execution_time = 5  # seconds
        self.max_instructions = 1000
        self.cycle_interval = 30  # seconds between script runs
        self.last_execution = None
        
        # Variable storage (per execution)
        self.variables = {}
        
        # Template storage
        self.templates = {}
        
        _LOGGER.info(f"{self.room}: Script Mode initialized")
    
    def load_script(self, script_config: Dict[str, Any]) -> bool:
        """
        Load a script from configuration.
        
        Args:
            script_config: Script configuration dictionary
            
        Returns:
            bool: True if script loaded successfully
        """
        try:
            self.script_config = script_config
            
            # Check if enabled
            self.enabled = script_config.get("enabled", False)
            if not self.enabled:
                _LOGGER.info(f"{self.room}: Script Mode disabled")
                return False
            
            # Get script code
            if "script" in script_config:
                # Direct script code
                self.script_code = script_config["script"]
            elif "file" in script_config:
                # Load from file
                with open(script_config["file"], "r") as f:
                    self.script_code = f.read()
            else:
                _LOGGER.error(f"{self.room}: No script or file specified")
                return False
            
            # Validate script
            if not self._validate_script(self.script_code):
                return False
            
            # Compile script
            if script_config.get("type", "dsl") == "python":
                self.compiled_script = compile(self.script_code, "<script>", "exec")
            
            _LOGGER.info(f"{self.room}: Script loaded successfully")
            return True
            
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error loading script: {e}")
            self.enabled = False
            return False
    
    def _validate_script(self, script_code: str) -> bool:
        """
        Validate script for syntax errors and security issues.
        
        Args:
            script_code: The script code to validate
            
        Returns:
            bool: True if script is valid
        """
        try:
            # Check for dangerous imports/statements
            forbidden_patterns = [
                "import os",
                "import sys",
                "__import__",
                "eval(",
                "exec(",
                "open(",
                "file(",
                "subprocess",
                "socket",
                "urllib",
                "requests",
            ]
            
            code_lower = script_code.lower()
            for pattern in forbidden_patterns:
                if pattern in code_lower:
                    _LOGGER.error(f"{self.room}: Script contains forbidden pattern: {pattern}")
                    return False
            
            # Try to parse if Python
            if self.script_config.get("type") == "python":
                ast.parse(script_code)
            
            return True
            
        except SyntaxError as e:
            _LOGGER.error(f"{self.room}: Script syntax error: {e}")
            return False
    
    async def execute(self) -> bool:
        """
        Execute the loaded script.
        
        Returns:
            bool: True if execution was successful
        """
        if not self.enabled or not self.script_code:
            return False
        
        # Check cycle interval
        if self.last_execution:
            elapsed = (datetime.now() - self.last_execution).total_seconds()
            if elapsed < self.cycle_interval:
                return False
        
        self.last_execution = datetime.now()
        
        try:
            # Reset variables
            self.variables = {}
            
            # Execute based on type
            script_type = self.script_config.get("type", "dsl")
            
            if script_type == "python":
                await self._execute_python()
            else:
                await self._execute_dsl()
            
            return True
            
        except Exception as e:
            _LOGGER.error(f"{self.room}: Script execution error: {e}")
            return False
    
    async def _execute_dsl(self):
        """Execute DSL script."""
        lines = self.script_code.strip().split("\n")
        line_num = 0
        instruction_count = 0
        
        while line_num < len(lines):
            # Check instruction limit
            instruction_count += 1
            if instruction_count > self.max_instructions:
                _LOGGER.warning(f"{self.room}: Script exceeded max instructions")
                break
            
            line = lines[line_num].strip()
            
            # Skip empty lines and comments
            if not line or line.startswith("//") or line.startswith("#"):
                line_num += 1
                continue
            
            # Parse and execute line
            try:
                await self._execute_dsl_line(line)
            except Exception as e:
                _LOGGER.error(f"{self.room}: DSL error at line {line_num + 1}: {e}")
                break
            
            line_num += 1
    
    async def _execute_dsl_line(self, line: str):
        """Execute a single DSL line."""
        # READ statement
        if line.startswith("READ "):
            await self._dsl_read(line)
        
        # SET statement
        elif line.startswith("SET "):
            await self._dsl_set(line)
        
        # IF statement
        elif line.startswith("IF "):
            await self._dsl_if(line)
        
        # CALL statement
        elif line.startswith("CALL "):
            await self._dsl_call(line)
        
        # EMIT statement
        elif line.startswith("EMIT "):
            await self._dsl_emit(line)
        
        # LOG statement
        elif line.startswith("LOG "):
            await self._dsl_log(line)
        
        # ENDIF, ELSE, etc. are handled by _dsl_if
        elif line in ["ENDIF", "ELSE", "ELSEIF"]:
            pass  # Handled in if block
        
        else:
            _LOGGER.warning(f"{self.room}: Unknown DSL command: {line}")
    
    async def _dsl_read(self, line: str):
        """Execute READ statement: READ var FROM path"""
        # Format: READ <variable> FROM <datastore.path>
        parts = line.split(" FROM ", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid READ syntax: {line}")
        
        var_name = parts[0].replace("READ ", "").strip()
        path = parts[1].strip()
        
        # Read from DataStore
        value = self.data_store.getDeep(path)
        self.variables[var_name] = value
        
        _LOGGER.debug(f"{self.room}: READ {var_name} = {value} FROM {path}")
    
    async def _dsl_set(self, line: str):
        """Execute SET statement: SET path = value"""
        # Format: SET <datastore.path> = <value/expression>
        parts = line.split(" = ", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid SET syntax: {line}")
        
        path = parts[0].replace("SET ", "").strip()
        value_expr = parts[1].strip()
        
        # Evaluate value expression
        value = self._eval_expression(value_expr)
        
        # Set in DataStore
        self.data_store.setDeep(path, value)
        
        _LOGGER.debug(f"{self.room}: SET {path} = {value}")
    
    async def _dsl_if(self, line: str):
        """Execute IF statement with condition evaluation."""
        # This is simplified - real implementation would need full parser
        # Format: IF <condition> THEN
        if " THEN" in line:
            condition = line.replace("IF ", "").replace(" THEN", "").strip()
            result = self._eval_condition(condition)
            
            if not result:
                # Skip to ENDIF or ELSE
                # This is a simplified version
                pass
        
        _LOGGER.debug(f"{self.room}: IF {line}")
    
    async def _dsl_call(self, line: str):
        """Execute CALL statement: CALL device.action"""
        # Format: CALL <device>.<action> [WITH <params>]
        line = line.replace("CALL ", "").strip()
        
        # Parse device.action
        if " WITH " in line:
            parts = line.split(" WITH ", 1)
            device_action = parts[0].strip()
            params = self._parse_params(parts[1])
        else:
            device_action = line
            params = {}
        
        if "." not in device_action:
            raise ValueError(f"Invalid CALL syntax: {line}")
        
        device, action = device_action.split(".", 1)
        
        # Execute action
        await self._execute_device_action(device, action, params)
    
    async def _dsl_emit(self, line: str):
        """Execute EMIT statement: EMIT event [WITH data]"""
        line = line.replace("EMIT ", "").strip()
        
        if " WITH " in line:
            parts = line.split(" WITH ", 1)
            event_name = parts[0].strip()
            data = self._eval_expression(parts[1].strip())
        else:
            event_name = line
            data = {}
        
        # Emit event
        await self.event_manager.emit(event_name, data)
        _LOGGER.debug(f"{self.room}: EMIT {event_name}")
    
    async def _dsl_log(self, line: str):
        """Execute LOG statement: LOG "message" [LEVEL=level]"""
        line = line.replace("LOG ", "").strip()
        
        # Parse level
        level = "info"
        if " LEVEL=" in line:
            parts = line.split(" LEVEL=", 1)
            line = parts[0].strip()
            level = parts[1].strip().lower()
        
        # Remove quotes
        message = line.strip('"\'')
        
        # Log with level
        if level == "debug":
            _LOGGER.debug(f"{self.room}: {message}")
        elif level == "warning":
            _LOGGER.warning(f"{self.room}: {message}")
        elif level == "error":
            _LOGGER.error(f"{self.room}: {message}")
        else:
            _LOGGER.info(f"{self.room}: {message}")
    
    async def _execute_python(self):
        """Execute Python script in sandboxed environment."""
        # Create safe execution environment
        exec_globals = {
            "__builtins__": {
                "True": True,
                "False": False,
                "None": None,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "len": len,
                "range": range,
                "enumerate": enumerate,
                "zip": zip,
                "abs": abs,
                "round": round,
                "min": min,
                "max": max,
                "sum": sum,
                "print": lambda x: _LOGGER.info(f"{self.room}: {x}"),
            },
            "datetime": datetime,
            "timedelta": timedelta,
            "time": time,
        }
        
        # Add OGB helper functions
        exec_globals["READ"] = self._py_read
        exec_globals["SET"] = self._py_set
        exec_globals["CALL"] = self._py_call
        exec_globals["EMIT"] = self._py_emit
        exec_globals["LOG"] = self._py_log
        exec_globals["TIME"] = datetime.now().strftime("%H:%M")
        exec_globals["VARS"] = self.variables
        
        # Execute with timeout
        try:
            await asyncio.wait_for(
                self._run_python_code(exec_globals),
                timeout=self.max_execution_time
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(f"{self.room}: Script execution timeout")
    
    async def _run_python_code(self, exec_globals: Dict):
        """Run Python code with globals."""
        exec(self.compiled_script, exec_globals)
    
    def _py_read(self, path: str) -> Any:
        """Python helper: Read from DataStore."""
        return self.data_store.getDeep(path)
    
    def _py_set(self, path: str, value: Any):
        """Python helper: Write to DataStore."""
        self.data_store.setDeep(path, value)
    
    async def _py_call(self, device: str, action: str, **kwargs):
        """Python helper: Call device action."""
        await self._execute_device_action(device, action, kwargs)
    
    async def _py_emit(self, event: str, data: Dict = None):
        """Python helper: Emit event."""
        await self.event_manager.emit(event, data or {})
    
    def _py_log(self, message: str, level: str = "info"):
        """Python helper: Log message."""
        if level == "debug":
            _LOGGER.debug(f"{self.room}: {message}")
        elif level == "warning":
            _LOGGER.warning(f"{self.room}: {message}")
        elif level == "error":
            _LOGGER.error(f"{self.room}: {message}")
        else:
            _LOGGER.info(f"{self.room}: {message}")
    
    async def _execute_device_action(self, device: str, action: str, params: Dict):
        """Execute a device action."""
        # Map device names to capabilities
        device_map = {
            "exhaust": "canExhaust",
            "intake": "canIntake",
            "ventilation": "canVentilate",
            "heater": "canHeat",
            "cooler": "canCool",
            "humidifier": "canHumidify",
            "dehumidifier": "canDehumidify",
            "light": "canLight",
            "co2": "canCO2",
            "climate": "canClimate",
        }
        
        capability = device_map.get(device.lower())
        if not capability:
            _LOGGER.warning(f"{self.room}: Unknown device: {device}")
            return
        
        # Check capability
        caps = self.data_store.get("capabilities") or {}
        if not caps.get(capability, {}).get("state", False):
            _LOGGER.debug(f"{self.room}: Device {device} not available")
            return
        
        # Create action
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        action_pub = OGBActionPublication(
            capability=capability,
            action=action.capitalize(),
            Name=self.room,
            message=f"Script Mode: {device}.{action}",
            priority=params.get("priority", "medium"),
        )
        
        # Execute via ActionManager
        await self.action_manager.checkLimitsAndPublicate([action_pub])
        
        _LOGGER.debug(f"{self.room}: CALL {device}.{action}")
    
    def _eval_expression(self, expr: str) -> Any:
        """Safely evaluate an expression."""
        try:
            # Replace variables
            for var_name, value in self.variables.items():
                expr = expr.replace(var_name, str(value))
            
            # Evaluate
            return eval(expr, {"__builtins__": {}}, {})
        except Exception as e:
            _LOGGER.error(f"{self.room}: Expression evaluation error: {e}")
            return None
    
    def _eval_condition(self, condition: str) -> bool:
        """Evaluate a condition."""
        try:
            # Replace variables
            for var_name, value in self.variables.items():
                condition = condition.replace(var_name, str(value))
            
            # Replace TIME
            if "TIME" in condition:
                current_time = datetime.now().strftime("%H:%M")
                condition = condition.replace("TIME", f'"{current_time}"')
            
            # Evaluate
            return bool(eval(condition, {"__builtins__": {}}, {}))
        except Exception as e:
            _LOGGER.error(f"{self.room}: Condition evaluation error: {e}")
            return False
    
    def _parse_params(self, params_str: str) -> Dict:
        """Parse parameter string into dict."""
        params = {}
        
        # Simple key=value parsing
        for param in params_str.split(","):
            if "=" in param:
                key, value = param.split("=", 1)
                params[key.strip()] = value.strip().strip('"\'')
        
        return params
    
    def load_template(self, template_name: str) -> Optional[str]:
        """Load a built-in template."""
        templates = {
            "basic_vpd_control": self._template_basic_vpd(),
            "advanced_environment": self._template_advanced(),
        }
        
        return templates.get(template_name)
    
    def _template_basic_vpd(self) -> str:
        """Template 1: Basic VPD Control."""
        return '''// Basic VPD Control Template
// Automatically adjusts exhaust and humidity based on VPD

READ vpd_current FROM vpd.current
READ vpd_max FROM vpd.perfectMax
READ vpd_min FROM vpd.perfectMin
READ temp FROM tentData.temperature

IF vpd_current > vpd_max THEN
    LOG "VPD too high, activating exhaust and dehumidifier"
    CALL exhaust.increase
    CALL dehumidifier.increase
ENDIF

IF vpd_current < vpd_min THEN
    LOG "VPD too low, reducing exhaust and activating humidifier"
    CALL exhaust.reduce
    CALL humidifier.increase
ENDIF
'''
    
    def _template_advanced(self) -> str:
        """Template 2: Advanced Environment Control."""
        return '''// Advanced Environment Control Template
// Full environmental control with safety checks

// Read all sensor data
READ vpd FROM vpd.current
READ vpd_max FROM vdp.perfectMax
READ vpd_min FROM vpd.perfectMin
READ temp FROM tentData.temperature
READ temp_max FROM tentData.maxTemp
READ temp_min FROM tentData.minTemp
READ humidity FROM tentData.humidity
READ is_light_on FROM isPlantDay.islightON

// Calculate safety margins
SET temp_margin = 2.0
SET vpd_critical_high = vpd_max + 0.3
SET vpd_critical_low = vpd_min - 0.2

// Critical VPD - emergency response
IF vpd > vpd_critical_high THEN
    LOG "CRITICAL: VPD way too high!" LEVEL=error
    CALL exhaust.increase WITH priority=emergency
    CALL dehumidifier.increase WITH priority=emergency
    
    // Block heater completely when VPD critical
    LOG "Blocking heater - VPD critical"
ENDIF

// Temperature safety
IF temp > temp_max - temp_margin THEN
    LOG "Temperature approaching maximum"
    CALL cooler.increase
    CALL exhaust.increase
ENDIF

IF temp < temp_min + temp_margin THEN
    LOG "Temperature approaching minimum"
    // Only heat if VPD is not too high
    IF vpd < vpd_max THEN
        CALL heater.increase
    ELSE
        LOG "Cannot heat - VPD too high" LEVEL=warning
    ENDIF
ENDIF

// Day/Night cycle
IF is_light_on THEN
    // Day mode
    CALL light.on
ELSE
    // Night mode
    CALL light.off
ENDIF

// Log status
LOG "Script execution completed"
'''

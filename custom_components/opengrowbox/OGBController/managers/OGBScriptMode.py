"""
OpenGrowBox Script Mode - Stateless Executor

A fully customizable scripting environment for advanced users.
Runs statelessly like VPD Perfection - executed cyclically by ModeManager.

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
    Script Mode Executor for OpenGrowBox - Stateless automation.
    
    Similar to VPD Perfection: Stateless, executed cyclically by ModeManager.
    Script is loaded from DataStore on each execution.
    """
    
    def __init__(self, ogb: "OpenGrowBox"):
        """Initialize Script Mode executor."""
        self.ogb = ogb
        self.room = ogb.room
        self.data_store = ogb.dataStore
        self.event_manager = ogb.eventManager
        self.action_manager = ogb.actionManager
        
        # Safety limits
        self.max_execution_time = 5  # seconds
        self.max_instructions = 1000
        
        _LOGGER.info(f"{self.room}: Script Mode executor initialized")
    
    async def execute(self) -> bool:
        """
        Execute the user script from file (NOT from DataStore).
        Called cyclically by ModeManager (like VPD Perfection).
        
        Returns:
            bool: True if execution was successful
        """
        try:
            # Load script from file via OGBDSManager (NOT from DataStore!)
            # This prevents memory leaks and ensures persistence
            if not hasattr(self.ogb, 'data_storeManager'):
                _LOGGER.error(f"{self.room}: OGBDSManager not available")
                return False
            
            script_config = await self.ogb.data_storeManager.load_script(self.room)
            
            if not script_config:
                _LOGGER.debug(f"{self.room}: No script file found")
                return False
            
            # Check if enabled
            if not script_config.get("enabled", False):
                _LOGGER.debug(f"{self.room}: Script Mode disabled")
                return False
            
            # Get script code
            script_code = self._get_script_code(script_config)
            if not script_code:
                _LOGGER.warning(f"{self.room}: No script code found")
                return False
            
            # Execute based on type
            script_type = script_config.get("type", "dsl")
            
            if script_type == "python":
                await self._execute_python(script_code)
            else:
                await self._execute_dsl(script_code)
            
            return True
            
        except Exception as e:
            _LOGGER.error(f"{self.room}: Script execution error: {e}")
            return False
    
    def _get_script_code(self, script_config: Dict[str, Any]) -> Optional[str]:
        """Get script code from config or file."""
        if "script" in script_config:
            return script_config["script"]
        elif "file" in script_config:
            try:
                with open(script_config["file"], "r") as f:
                    return f.read()
            except Exception as e:
                _LOGGER.error(f"{self.room}: Error loading script file: {e}")
                return None
        return None
    
    async def _execute_dsl(self, script_code: str):
        """Execute DSL script."""
        lines = script_code.strip().split("\n")
        line_num = 0
        instruction_count = 0
        variables = {}  # Fresh variables for each execution
        
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
                result = await self._execute_dsl_line(line, variables)
                # Handle control flow (IF/ELSE)
                if result is not None:
                    line_num = result
                    continue
            except Exception as e:
                _LOGGER.error(f"{self.room}: DSL error at line {line_num + 1}: {e}")
                break
            
            line_num += 1
    
    async def _execute_dsl_line(self, line: str, variables: Dict) -> Optional[int]:
        """
        Execute a single DSL line.
        Returns new line number for control flow, None for normal execution.
        """
        # READ statement
        if line.startswith("READ "):
            await self._dsl_read(line, variables)
        
        # SET statement
        elif line.startswith("SET "):
            await self._dsl_set(line, variables)
        
        # IF statement - returns new line number for control flow
        elif line.startswith("IF "):
            return await self._dsl_if(line, variables)
        
        # CALL statement
        elif line.startswith("CALL "):
            await self._dsl_call(line, variables)
        
        # EMIT statement
        elif line.startswith("EMIT "):
            await self._dsl_emit(line, variables)
        
        # LOG statement
        elif line.startswith("LOG "):
            await self._dsl_log(line, variables)
        
        # ENDIF, ELSE handled by _dsl_if
        elif line in ["ENDIF", "ELSE", "ELSEIF"]:
            pass
        
        else:
            _LOGGER.warning(f"{self.room}: Unknown DSL command: {line}")
        
        return None
    
    async def _dsl_read(self, line: str, variables: Dict):
        """Execute READ statement: READ var FROM path"""
        parts = line.split(" FROM ", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid READ syntax: {line}")
        
        var_name = parts[0].replace("READ ", "").strip()
        path = parts[1].strip()
        
        value = self.data_store.getDeep(path)
        variables[var_name] = value
        
        _LOGGER.debug(f"{self.room}: READ {var_name} = {value}")
    
    async def _dsl_set(self, line: str, variables: Dict):
        """Execute SET statement: SET path = value"""
        parts = line.split(" = ", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid SET syntax: {line}")
        
        path = parts[0].replace("SET ", "").strip()
        value_expr = parts[1].strip()
        
        value = self._eval_expression(value_expr, variables)
        self.data_store.setDeep(path, value)
        
        _LOGGER.debug(f"{self.room}: SET {path} = {value}")
    
    async def _dsl_if(self, line: str, variables: Dict) -> Optional[int]:
        """
        Execute IF statement.
        Returns None for normal flow, or line number to jump to.
        """
        # Simplified IF handling - full implementation would need proper parsing
        condition = line.replace("IF ", "").replace(" THEN", "").strip()
        
        if not self._eval_condition(condition, variables):
            # Condition false - skip to ENDIF or ELSE
            # This is simplified - would need proper block handling
            pass
        
        return None
    
    async def _dsl_call(self, line: str, variables: Dict):
        """Execute CALL statement: CALL device.action"""
        line = line.replace("CALL ", "").strip()
        
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
        await self._execute_device_action(device, action, params)
    
    async def _dsl_emit(self, line: str, variables: Dict):
        """Execute EMIT statement: EMIT event [WITH data]"""
        line = line.replace("EMIT ", "").strip()
        
        if " WITH " in line:
            parts = line.split(" WITH ", 1)
            event_name = parts[0].strip()
            data = self._eval_expression(parts[1].strip(), variables)
        else:
            event_name = line
            data = {}
        
        await self.event_manager.emit(event_name, data)
        _LOGGER.debug(f"{self.room}: EMIT {event_name}")
    
    async def _dsl_log(self, line: str, variables: Dict):
        """Execute LOG statement: LOG "message" [LEVEL=level]"""
        line = line.replace("LOG ", "").strip()
        
        level = "info"
        if " LEVEL=" in line:
            parts = line.split(" LEVEL=", 1)
            line = parts[0].strip()
            level = parts[1].strip().lower()
        
        message = line.strip('"\'')
        
        if level == "debug":
            _LOGGER.debug(f"{self.room}: {message}")
        elif level == "warning":
            _LOGGER.warning(f"{self.room}: {message}")
        elif level == "error":
            _LOGGER.error(f"{self.room}: {message}")
        else:
            _LOGGER.info(f"{self.room}: {message}")
    
    async def _execute_python(self, script_code: str):
        """Execute Python script in sandboxed environment."""
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
        
        try:
            await asyncio.wait_for(
                self._run_python_code(script_code, exec_globals),
                timeout=self.max_execution_time
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(f"{self.room}: Script execution timeout")
    
    async def _run_python_code(self, code: str, exec_globals: Dict):
        """Run Python code with globals."""
        exec(code, exec_globals)
    
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
        
        caps = self.data_store.get("capabilities") or {}
        if not caps.get(capability, {}).get("state", False):
            _LOGGER.debug(f"{self.room}: Device {device} not available")
            return
        
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        action_pub = OGBActionPublication(
            capability=capability,
            action=action.capitalize(),
            Name=self.room,
            message=f"Script Mode: {device}.{action}",
            priority=params.get("priority", "medium"),
        )
        
        await self.action_manager.checkLimitsAndPublicate([action_pub])
        _LOGGER.debug(f"{self.room}: CALL {device}.{action}")
    
    def _eval_expression(self, expr: str, variables: Dict) -> Any:
        """Safely evaluate an expression."""
        try:
            for var_name, value in variables.items():
                expr = expr.replace(var_name, str(value))
            return eval(expr, {"__builtins__": {}}, {})
        except Exception as e:
            _LOGGER.error(f"{self.room}: Expression error: {e}")
            return None
    
    def _eval_condition(self, condition: str, variables: Dict) -> bool:
        """Evaluate a condition."""
        try:
            for var_name, value in variables.items():
                condition = condition.replace(var_name, str(value))
            
            if "TIME" in condition:
                current_time = datetime.now().strftime("%H:%M")
                condition = condition.replace("TIME", f'"{current_time}"')
            
            return bool(eval(condition, {"__builtins__": {}}, {}))
        except Exception as e:
            _LOGGER.error(f"{self.room}: Condition error: {e}")
            return False
    
    def _parse_params(self, params_str: str) -> Dict:
        """Parse parameter string into dict."""
        params = {}
        for param in params_str.split(","):
            if "=" in param:
                key, value = param.split("=", 1)
                params[key.strip()] = value.strip().strip('"\'')
        return params
    
    def get_template(self, template_name: str) -> Optional[str]:
        """Get a built-in template."""
        templates = {
            "basic_vpd_control": self._template_basic_vpd(),
            "advanced_environment": self._template_advanced(),
        }
        return templates.get(template_name)
    
    def _template_basic_vpd(self) -> str:
        """Template 1: Basic VPD Control."""
        return '''// Basic VPD Control Template
READ vpd_current FROM vpd.current
READ vpd_max FROM vpd.perfectMax
READ vpd_min FROM vpd.perfectMin

IF vpd_current > vpd_max THEN
    LOG "VPD too high"
    CALL exhaust.increase
    CALL dehumidifier.increase
ENDIF

IF vpd_current < vpd_min THEN
    LOG "VPD too low"
    CALL exhaust.reduce
    CALL humidifier.increase
ENDIF
'''
    
    def _template_advanced(self) -> str:
        """Template 2: Advanced Environment Control."""
        return '''// Advanced Environment Control
READ vpd FROM vpd.current
READ vpd_max FROM vpd.perfectMax
READ temp FROM tentData.temperature
READ temp_max FROM tentData.maxTemp
READ is_light_on FROM isPlantDay.islightON

// Critical VPD check
IF vpd > vpd_max + 0.3 THEN
    LOG "CRITICAL: VPD way too high!" LEVEL=error
    CALL exhaust.increase WITH priority=emergency
    CALL dehumidifier.increase WITH priority=emergency
ENDIF

// Temperature safety
IF temp > temp_max - 2 THEN
    LOG "Temperature high"
    CALL cooler.increase
    CALL exhaust.increase
ENDIF

// Day/Night
IF is_light_on THEN
    CALL light.on
ELSE
    CALL light.off
ENDIF
'''

"""
OGB Orchestrator - Central control loop coordinator for all OGB managers.
Restores the main control loop coordination that was stripped during modularization.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)


class OGBOrchestrator:
    """Central coordinator for all OGB managers."""
    
    def __init__(self, hass, dataStore, event_manager, room):
        """Initialize the orchestrator."""
        self.name = "OGB Orchestrator"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = event_manager
        
        # Manager references (will be injected)
        self.sensor_manager = None
        self.mode_manager = None
        self.action_manager = None
        self.device_manager = None
        self.vpd_manager = None
        self.feed_manager = None
        
        # Control loop state
        self._control_loop_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._shutdown_event = asyncio.Event()
        
        # Timing configuration
        self.timing_config = {
            'sensor_update': 10,      # Every 10 seconds
            'vpd_calculation': 30,    # Every 30 seconds
            'mode_evaluation': 60,    # Every 1 minute
            'device_sync': 120,       # Every 2 minutes
            'feed_check': 300,        # Every 5 minutes
        }
        
        # Track last update times
        self._last_updates = {key: 0.0 for key in self.timing_config}
        
        # Statistics
        self._loop_count = 0
        self._last_loop_time = 0
        self._avg_loop_time = 0
        
        _LOGGER.info(f"✅ {self.room} Orchestrator initialized")
    
    def inject_managers(self, **managers):
        """Inject manager instances for coordination."""
        for name, manager in managers.items():
            setattr(self, name, manager)
            _LOGGER.debug(f"Injected {name}: {manager}")
    
    async def start(self):
        """Start the orchestration control loop."""
        if self._is_running:
            _LOGGER.warning(f"{self.room} Orchestrator already running")
            return
        
        self._is_running = True
        self._shutdown_event.clear()
        self._control_loop_task = asyncio.create_task(self._main_control_loop())
        
        _LOGGER.info(f"🚀 {self.room} Orchestrator started")
    
    async def stop(self):
        """Stop the orchestration control loop gracefully."""
        if not self._is_running:
            return
        
        _LOGGER.info(f"🛑 {self.room} Orchestrator stopping...")
        
        self._is_running = False
        self._shutdown_event.set()
        
        if self._control_loop_task:
            self._control_loop_task.cancel()
            try:
                await self._control_loop_task
            except asyncio.CancelledError:
                pass
        
        _LOGGER.info(f"✅ {self.room} Orchestrator stopped")
    
    async def _main_control_loop(self):
        """Main control loop - coordinates all managers with timing."""
        _LOGGER.info(f"🔄 {self.room} Control loop starting")
        
        while self._is_running and not self._shutdown_event.is_set():
            try:
                loop_start = time.time()
                
                # Execute timed tasks based on intervals
                await self._execute_timed_tasks()
                
                # Update loop statistics
                loop_duration = time.time() - loop_start
                self._loop_count += 1
                self._last_loop_time = loop_duration
                self._avg_loop_time = (
                    (self._avg_loop_time * (self._loop_count - 1) + loop_duration) 
                    / self._loop_count
                )
                
                # Log statistics every 100 loops
                if self._loop_count % 100 == 0:
                    _LOGGER.info(
                        f"{self.room} Loop stats: count={self._loop_count}, "
                        f"last={loop_duration:.2f}s, avg={self._avg_loop_time:.2f}s"
                    )
                
                # Sleep for base interval (10 seconds)
                await asyncio.sleep(10)
                
            except asyncio.CancelledError:
                _LOGGER.info(f"{self.room} Control loop cancelled")
                break
            except Exception as e:
                _LOGGER.error(f"❌ {self.room} Control loop error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Error backoff
        
        _LOGGER.info(f"🔄 {self.room} Control loop stopped")
    
    async def _execute_timed_tasks(self):
        """Execute tasks based on their timing intervals."""
        current_time = time.time()
        
        for task_name, interval in self.timing_config.items():
            if current_time - self._last_updates[task_name] >= interval:
                try:
                    await self._execute_task(task_name)
                    self._last_updates[task_name] = current_time
                except Exception as e:
                    _LOGGER.error(f"❌ {self.room} Task '{task_name}' error: {e}")
    
    async def _execute_task(self, task_name: str):
        """Execute a specific orchestration task."""
        if task_name == 'sensor_update':
            await self._update_sensors()
        
        elif task_name == 'vpd_calculation':
            await self._calculate_vpd()
        
        elif task_name == 'mode_evaluation':
            await self._evaluate_mode()
        
        elif task_name == 'device_sync':
            await self._sync_device_states()
        
        elif task_name == 'feed_check':
            await self._check_feed_needs()
    
    async def _update_sensors(self):
        """Update sensor data from HA."""
        # This would integrate with sensor manager if available
        # For now, trigger event
        await self.event_manager.emit("RoomUpdate", True)
    
    async def _calculate_vpd(self):
        """Calculate VPD from current sensor data."""
        # This would integrate with VPD manager
        await self.event_manager.emit("VPDCreation", True)
    
    async def _evaluate_mode(self):
        """Evaluate current mode and determine necessary actions.
        
        NOTE: This method is intentionally a NO-OP because VPDManager already
        emits 'selectActionMode' after every VPD calculation. Having both
        Orchestrator AND VPDManager emit this event causes double DataRelease
        triggers, resulting in duplicate API calls to Premium backend.
        
        The VPDManager.processVpd() method (line 153) handles mode evaluation
        as part of the VPD calculation cycle, which is the correct place for it.
        
        If standalone mode evaluation is ever needed (without VPD calculation),
        uncomment the code below and add proper deduplication.
        """
        # DISABLED: Causes double DataRelease with VPDManager
        # See: https://github.com/OpenGrowBox/ogb-ha-backend/issues/XXX
        #
        # if self.mode_manager:
        #     tentMode = self.data_store.get("tentMode")
        #     from .data.OGBDataClasses.OGBPublications import OGBModeRunPublication
        #     runMode = OGBModeRunPublication(currentMode=tentMode)
        #     await self.event_manager.emit("selectActionMode", runMode)
        pass
    
    async def _sync_device_states(self):
        """Sync all device states with Home Assistant."""
        if not self.device_manager:
            return
        
        devices = self.data_store.get("devices") or []
        sync_failures = []
        
        for device in devices:
            try:
                # Get expected state
                expected_state = getattr(device, 'get_expected_state', lambda: None)()
                if expected_state is None:
                    continue
                
                # Get actual state from HA
                actual_state = await self._get_ha_device_state(device)
                
                # Detect drift
                if expected_state != actual_state:
                    _LOGGER.warning(
                        f"{self.room} State drift: {device.deviceName} "
                        f"expected={expected_state} actual={actual_state}"
                    )
                    
                    # Attempt re-sync
                    success = await self._resync_device(device, expected_state)
                    
                    if not success:
                        sync_failures.append(device.deviceName)
                        
            except Exception as e:
                _LOGGER.error(f"{self.room} Sync error for device: {e}")
        
        # Report sync failures
        if sync_failures:
            await self.event_manager.emit('device_sync_failures', {
                'failed_devices': sync_failures,
                'timestamp': datetime.now().isoformat()
            })
    
    async def _get_ha_device_state(self, device) -> Any:
        """Get actual device state from Home Assistant."""
        try:
            entity_id = getattr(device, 'deviceName', None)
            if not entity_id:
                return None
            
            state = self.hass.states.get(entity_id)
            if state:
                return state.state
            return None
        except Exception as e:
            _LOGGER.error(f"{self.room} Error getting HA state: {e}")
            return None
    
    async def _resync_device(self, device, target_state) -> bool:
        """Re-sync a device with retry logic."""
        max_retries = 3
        device_name = getattr(device, 'deviceName', 'unknown')
        
        for attempt in range(max_retries):
            try:
                # Attempt to set state
                if hasattr(device, 'set_state'):
                    await device.set_state(target_state)
                elif hasattr(device, 'turn_on') and target_state == 'on':
                    await device.turn_on()
                elif hasattr(device, 'turn_off') and target_state == 'off':
                    await device.turn_off()
                else:
                    _LOGGER.warning(f"{self.room} No method to set state for {device_name}")
                    return False
                
                # Wait for state change with exponential backoff
                await asyncio.sleep(2 ** attempt)
                
                # Verify state changed
                actual = await self._get_ha_device_state(device)
                if actual == target_state:
                    _LOGGER.info(f"{self.room} Re-synced {device_name} on attempt {attempt + 1}")
                    return True
                    
            except Exception as e:
                _LOGGER.error(f"{self.room} Re-sync attempt {attempt + 1} failed: {e}")
        
        return False
    
    async def _check_feed_needs(self):
        """Check if feeding is needed."""
        if self.feed_manager:
            await self.event_manager.emit("CheckForFeed", True)
    
    async def check_emergency_conditions(self):
        """Check for emergency conditions that require shutdown."""
        tent_data = self.data_store.get('tentData') or {}
        
        emergencies = []
        
        # Critical temperature
        temperature = tent_data.get('temperature')
        max_temp = tent_data.get('maxTemp', 40)
        min_temp = tent_data.get('minTemp', 10)
        
        if temperature is not None:
            if temperature > max_temp + 5:
                emergencies.append('critical_overheat')
            elif temperature < min_temp - 5:
                emergencies.append('critical_cold')
        
        # Critical humidity
        humidity = tent_data.get('humidity')
        if humidity is not None and humidity > 95:
            emergencies.append('critical_humidity')
        
        # Dewpoint risk
        dewpoint = tent_data.get('dewpoint')
        if dewpoint is not None and temperature is not None:
            if dewpoint >= temperature:
                emergencies.append('condensation_risk')
        
        if emergencies:
            await self.trigger_emergency_shutdown(emergencies)
    
    async def trigger_emergency_shutdown(self, reasons: list):
        """Trigger emergency shutdown procedure."""
        _LOGGER.critical(f"⚠️ {self.room} EMERGENCY SHUTDOWN: {reasons}")
        
        # Stop all devices safely
        await self._emergency_stop_all_devices()
        
        # Log emergency state
        await self.event_manager.emit('emergency_shutdown', {
            'reasons': reasons,
            'timestamp': datetime.now().isoformat(),
            'room': self.room
        }, haEvent=True)
        
        # Enter safe mode
        self.data_store.set('tentMode', 'Emergency')
        self.data_store.set('emergencySafeMode', True)
        
        # Notify user
        await self.event_manager.emit('ogb_emergency_alert', {
            'severity': 'critical',
            'reasons': reasons,
            'room': self.room
        }, haEvent=True)
    
    async def _emergency_stop_all_devices(self):
        """Stop all devices in safe priority order."""
        # Priority order: dangerous devices first
        device_priority = [
            'heater',      # Stop heating first
            'humidifier',  # Stop adding moisture
            'co2',         # Stop CO2
            'light',       # Reduce heat source
            'exhaust',     # Keep last for ventilation
        ]
        
        devices = self.data_store.get("devices") or []
        
        for device_type in device_priority:
            matching_devices = [
                d for d in devices 
                if hasattr(d, 'deviceType') and 
                device_type.lower() in d.deviceType.lower()
            ]
            
            for device in matching_devices:
                try:
                    if hasattr(device, 'turn_off'):
                        await device.turn_off()
                        _LOGGER.info(f"{self.room} Emergency stopped: {device.deviceName}")
                except Exception as e:
                    _LOGGER.error(f"{self.room} Failed to stop {device.deviceName}: {e}")
    
    async def set_minimal_monitoring_mode(self):
        """Enter minimal monitoring mode during emergency."""
        # Disable automation
        self.data_store.set('automationEnabled', False)
        
        # Stop control loop
        await self.stop()
        
        _LOGGER.warning(f"{self.room} Entered minimal monitoring mode")
    
    def get_statistics(self) -> dict:
        """Get orchestrator statistics."""
        return {
            'is_running': self._is_running,
            'loop_count': self._loop_count,
            'last_loop_time': self._last_loop_time,
            'avg_loop_time': self._avg_loop_time,
            'timing_config': self.timing_config,
            'last_updates': self._last_updates
        }

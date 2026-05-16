import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List

_LOGGER = logging.getLogger(__name__)


# Default power estimates for devices without power sensors (watts)
DEFAULT_POWER_ESTIMATES = {
    "light": 100.0,
    "fan": 30.0,
    "vent": 40.0,
    "exhaust": 50.0,
    "intake": 30.0,
    "humidifier": 50.0,
    "dehumidifier": 200.0,
    "heater": 500.0,
    "cooler": 300.0,
    "pump": 10.0,
    "mistpump": 15.0,
    "returnpump": 10.0,
    "waterpump": 10.0,
    "airpump": 5.0,
}


class OGBEnergyManager:
    """Enterprise-safe energy consumption tracking per room.
    
    Architecture:
    - Event-based device state tracking (ON/OFF)
    - Power sensor updates update wattage readings
    - Periodic aggregation (every 5 min) calculates accumulated kWh
    - Startup scan catches devices already running
    - Estimated power fallback for devices without power sensors
    - UTC timestamps for daylight-saving safety
    - Atomic updates to prevent data corruption
    """

    def __init__(self, hass, data_store, event_manager, room):
        """Initialize the energy manager.

        Args:
            hass: Home Assistant instance
            data_store: Reference to the data store
            event_manager: Reference to the event manager
            room: Room identifier
        """
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room

        # Active tracking state per device
        # {
        #   device_name: {
        #     "power_watts": float,       # Current power reading
        #     "last_update": datetime,     # Last energy calculation timestamp (UTC)
        #     "start_time": datetime,      # When device turned ON (UTC)
        #     "session_kwh": float,        # kWh accumulated this session
        #     "is_estimated": bool,        # Whether using estimated power
        #   }
        # }
        self._device_tracking: Dict[str, Dict[str, Any]] = {}

        # Background task for periodic aggregation
        self._update_task = None
        self._shutdown = False

        # Register event handlers
        self.event_manager.on("DeviceStateChange", self._on_device_state_change)
        self.event_manager.on("PowerSensorUpdate", self._on_power_sensor_update)

        # Start background tasks
        self._start_background_tasks()

        _LOGGER.info(f"[{self.room}] OGBEnergyManager initialized")

    def _start_background_tasks(self):
        """Start background update and persistence loops."""
        if self._update_task is None or self._update_task.done():
            self._update_task = asyncio.create_task(self._background_loop())

    async def _background_loop(self):
        """Background loop for periodic aggregation and persistence."""
        # Wait a bit for devices to initialize before scanning
        await asyncio.sleep(5)
        
        # Initial scan for already-running devices
        await self._scan_initial_devices()
        
        while not self._shutdown:
            try:
                # Check for day rollover
                await self._check_day_rollover()
                
                # Calculate energy for all tracked devices
                await self._calculate_all_tracked_devices()
                
                # Aggregate and persist
                await self._aggregate_and_persist()
                
                # Update HA sensor entities
                await self._update_sensor_entities()
                
                await asyncio.sleep(300)  # 5 minutes
            except Exception as e:
                _LOGGER.error(f"[{self.room}] Energy background loop error: {e}")
                await asyncio.sleep(60)  # Retry after 1 minute on error

    async def _scan_initial_devices(self):
        """Scan all devices and start tracking for those already running."""
        try:
            devices = self.data_store.get("devices") or []
            if not devices:
                _LOGGER.debug(f"[{self.room}] No devices found for initial scan")
                return

            started_count = 0
            for device in devices:
                if not hasattr(device, 'isRunning'):
                    continue
                if not device.isRunning:
                    continue

                device_name = getattr(device, 'deviceName', None)
                if not device_name:
                    continue

                # Skip if already tracking
                if device_name in self._device_tracking:
                    continue

                # Start tracking with current power reading
                await self._start_device_tracking(device)
                started_count += 1

            if started_count > 0:
                _LOGGER.info(
                    f"[{self.room}] Started energy tracking for {started_count} "
                    f"already-running devices"
                )

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error scanning initial devices: {e}")

    async def _start_device_tracking(self, device):
        """Start energy tracking for a device.
        
        Args:
            device: Device object with deviceName, isRunning, etc.
        """
        try:
            device_name = device.deviceName
            
            # Read current power value
            power_watts, is_estimated = await self._read_device_power(device)
            
            now = datetime.now(timezone.utc)
            self._device_tracking[device_name] = {
                "power_watts": power_watts,
                "last_update": now,
                "start_time": now,
                "session_kwh": 0.0,
                "is_estimated": is_estimated,
            }
            
            source = "estimated" if is_estimated else "sensor"
            _LOGGER.info(
                f"[{self.room}] Energy tracking started for {device_name} "
                f"at {power_watts}W ({source})"
            )

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error starting tracking for {device_name}: {e}")

    async def _read_device_power(self, device) -> tuple[float, bool]:
        """Read current power consumption from device.
        
        Returns:
            tuple: (power_watts, is_estimated)
        """
        device_name = device.deviceName
        
        # Try to find power sensor
        power_sensor = None
        if hasattr(device, '_find_power_sensor'):
            power_sensor = device._find_power_sensor()
        elif hasattr(device, 'sensors'):
            # Fallback: search in device sensors
            for sensor in device.sensors:
                entity_id = sensor.get("entity_id", "")
                if any(suffix in entity_id for suffix in ["_power", "_energy_power"]):
                    if self.hass and self.hass.states.get(entity_id):
                        power_sensor = entity_id
                        break
        
        # Read power value if sensor found
        if power_sensor and self.hass:
            state = self.hass.states.get(power_sensor)
            if state and state.state not in ("unknown", "unavailable", None, ""):
                try:
                    power = float(state.state)
                    if power >= 0:
                        return power, False
                except (ValueError, TypeError):
                    pass
        
        # Fallback: use estimated power based on device type
        device_type = getattr(device, 'deviceType', '').lower()
        if hasattr(device, 'deviceLabel'):
            device_label = getattr(device, 'deviceLabel', '').lower()
        else:
            device_label = ''
        
        # Check estimates by device type or label
        for key, estimate in DEFAULT_POWER_ESTIMATES.items():
            if key in device_type or key in device_label or key in device_name.lower():
                _LOGGER.debug(
                    f"[{self.room}] Using estimated power for {device_name}: "
                    f"{estimate}W (type={device_type}, label={device_label})"
                )
                return estimate, True
        
        # Ultimate fallback: 50W
        _LOGGER.warning(
            f"[{self.room}] No power estimate found for {device_name} "
            f"(type={device_type}), using default 50W"
        )
        return 50.0, True

    async def _on_device_state_change(self, event_data):
        """Handle device on/off state changes."""
        try:
            device_name = event_data.get("device_name")
            is_running = event_data.get("is_running", False)
            entity_id = event_data.get("entity_id", "")

            if not device_name:
                return

            if is_running:
                # Device turned on - start tracking if not already
                if device_name not in self._device_tracking:
                    # Need device object to read power
                    device = self._find_device_by_name(device_name)
                    if device:
                        await self._start_device_tracking(device)
                    else:
                        # Fallback: start with estimated power
                        await self._start_tracking_with_estimate(device_name)
            else:
                # Device turned off - finalize tracking
                if device_name in self._device_tracking:
                    # Calculate final energy for this session
                    await self._calculate_device_energy(device_name)
                    
                    tracking = self._device_tracking[device_name]
                    session_kwh = tracking.get("session_kwh", 0.0)
                    duration = datetime.now(timezone.utc) - tracking["start_time"]
                    hours = duration.total_seconds() / 3600.0
                    
                    _LOGGER.info(
                        f"[{self.room}] Energy tracking stopped for {device_name}: "
                        f"{session_kwh:.4f} kWh in {hours:.1f}h"
                    )
                    
                    # Remove from active tracking
                    del self._device_tracking[device_name]
                    
                    # Persist immediately on state change
                    await self._aggregate_and_persist()
                    await self._update_sensor_entities()

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling device state change: {e}")

    def _find_device_by_name(self, device_name: str):
        """Find device object by name."""
        devices = self.data_store.get("devices") or []
        for device in devices:
            if getattr(device, 'deviceName', None) == device_name:
                return device
        return None

    async def _start_tracking_with_estimate(self, device_name: str):
        """Start tracking with estimated power when device object not found."""
        # Try to estimate from name
        power = 50.0
        for key, estimate in DEFAULT_POWER_ESTIMATES.items():
            if key in device_name.lower():
                power = estimate
                break
        
        now = datetime.now(timezone.utc)
        self._device_tracking[device_name] = {
            "power_watts": power,
            "last_update": now,
            "start_time": now,
            "session_kwh": 0.0,
            "is_estimated": True,
        }
        
        _LOGGER.info(
            f"[{self.room}] Energy tracking started for {device_name} "
            f"at {power}W (estimated, device not found)"
        )

    async def _on_power_sensor_update(self, event_data):
        """Handle power sensor updates from devices.
        
        Only updates the power reading. Energy calculation happens
        in periodic aggregation to avoid double-counting.
        """
        try:
            device_name = event_data.get("device_name")
            power_watts = event_data.get("power_watts", 0.0)

            if not device_name:
                return

            if device_name not in self._device_tracking:
                # Device might be on but we missed the startup
                # Check if device is running and start tracking
                device = self._find_device_by_name(device_name)
                if device and getattr(device, 'isRunning', False):
                    await self._start_device_tracking(device)
                return

            tracking = self._device_tracking[device_name]
            
            # Validate power value
            try:
                power = float(power_watts)
                if power < 0:
                    _LOGGER.warning(
                        f"[{self.room}] Negative power reading from {device_name}: "
                        f"{power}W, ignoring"
                    )
                    return
                tracking["power_watts"] = power
                tracking["is_estimated"] = False
                
                _LOGGER.debug(
                    f"[{self.room}] {device_name} power updated: {power}W"
                )
            except (ValueError, TypeError):
                _LOGGER.warning(
                    f"[{self.room}] Invalid power reading from {device_name}: "
                    f"{power_watts}"
                )

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error handling power sensor update: {e}")

    async def _calculate_device_energy(self, device_name: str) -> float:
        """Calculate accumulated energy for a device since last update.
        
        Returns:
            Energy added in this calculation (kWh)
        """
        if device_name not in self._device_tracking:
            return 0.0

        tracking = self._device_tracking[device_name]
        last_update = tracking.get("last_update")
        power_watts = tracking.get("power_watts", 0.0)

        if not last_update or power_watts <= 0:
            return 0.0

        now = datetime.now(timezone.utc)
        time_delta_hours = (now - last_update).total_seconds() / 3600.0

        if time_delta_hours <= 0:
            return 0.0

        # Calculate energy: kWh = W * h / 1000
        energy_kwh = (power_watts * time_delta_hours) / 1000.0
        
        # Validate: should not be negative or unreasonably high
        if energy_kwh < 0:
            _LOGGER.error(
                f"[{self.room}] Negative energy calculated for {device_name}: "
                f"{energy_kwh} kWh"
            )
            return 0.0
        
        max_expected = power_watts * 5.1 / 1000.0  # Max 5+ minutes worth
        if energy_kwh > max_expected:
            _LOGGER.warning(
                f"[{self.room}] Unusually high energy for {device_name}: "
                f"{energy_kwh:.4f} kWh (expected max {max_expected:.4f}). "
                f"Time delta: {time_delta_hours:.2f}h"
            )

        tracking["session_kwh"] += energy_kwh
        tracking["last_update"] = now

        return energy_kwh

    async def _calculate_all_tracked_devices(self):
        """Calculate energy for all currently tracked devices."""
        total_added = 0.0
        for device_name in list(self._device_tracking.keys()):
            added = await self._calculate_device_energy(device_name)
            total_added += added
        
        if total_added > 0:
            _LOGGER.debug(
                f"[{self.room}] Calculated {total_added:.4f} kWh for "
                f"{len(self._device_tracking)} tracked devices"
            )

    async def _aggregate_and_persist(self):
        """Aggregate tracking data and persist to data store."""
        try:
            energy_data = self.data_store.getDeep("Energy", {})
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            
            # Ensure today's data structure exists
            if "daily" not in energy_data:
                energy_data["daily"] = {}
            if today_str not in energy_data["daily"]:
                energy_data["daily"][today_str] = {
                    "kwh": 0.0,
                    "cost": 0.0,
                    "runtime": {},
                }
            
            daily_data = energy_data["daily"][today_str]
            price_per_kwh = energy_data.get("price_per_kwh", 0.35)

            # Aggregate from all tracked devices
            total_today_kwh = daily_data.get("kwh", 0.0)
            total_runtime = daily_data.get("runtime", {})

            for device_name, tracking in self._device_tracking.items():
                session_kwh = tracking.get("session_kwh", 0.0)
                
                # Add session energy to today's total
                # Use device name as key to accumulate across sessions
                device_key = f"{device_name}_today"
                current_device_kwh = daily_data.get(device_key, 0.0)
                daily_data[device_key] = current_device_kwh + session_kwh
                
                # Accumulate total
                total_today_kwh += session_kwh
                
                # Reset session counter (we've accounted for it)
                tracking["session_kwh"] = 0.0
                
                # Update runtime
                start_time = tracking.get("start_time")
                if start_time:
                    runtime_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600.0
                    total_runtime[device_name] = round(runtime_hours, 2)

            # Update today's totals
            daily_data["kwh"] = round(total_today_kwh, 4)
            daily_data["cost"] = round(total_today_kwh * price_per_kwh, 4)
            daily_data["runtime"] = total_runtime
            energy_data["daily"][today_str] = daily_data
            energy_data["current_day"] = today_str
            energy_data["last_update"] = datetime.now(timezone.utc).isoformat()

            # Update weekly and monthly
            await self._update_weekly_monthly(energy_data)
            
            # Cleanup old data
            await self._cleanup_old_data(energy_data)
            
            # Atomically persist
            self.data_store.setDeep("Energy", energy_data)

            _LOGGER.debug(
                f"[{self.room}] Energy persisted: {total_today_kwh:.4f} kWh today, "
                f"cost: {daily_data['cost']:.2f} {energy_data.get('currency', 'EUR')}"
            )

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error aggregating energy data: {e}")

    async def _check_day_rollover(self):
        """Check if day has changed and finalize previous day's data."""
        energy_data = self.data_store.getDeep("Energy", {})
        current_day = energy_data.get("current_day")
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if current_day and current_day != today_str:
            # Day changed - log the transition
            _LOGGER.info(f"[{self.room}] Day rollover: {current_day} -> {today_str}")
            
            # Reset all session tracking (new day)
            for device_name in self._device_tracking:
                tracking = self._device_tracking[device_name]
                tracking["session_kwh"] = 0.0
                tracking["start_time"] = datetime.now(timezone.utc)

    async def _update_weekly_monthly(self, energy_data: Dict[str, Any]):
        """Update weekly and monthly aggregate data from daily data."""
        now = datetime.now(timezone.utc)
        week_str = now.strftime("%Y-W%W")
        month_str = now.strftime("%Y-%m")

        # Calculate weekly total
        week_start = now - timedelta(days=now.weekday())
        week_dates = []
        for i in range(7):
            day = week_start + timedelta(days=i)
            if day.month == now.month:  # Only current month days
                week_dates.append(day.strftime("%Y-%m-%d"))

        weekly_kwh = sum(
            energy_data.get("daily", {}).get(date, {}).get("kwh", 0.0)
            for date in week_dates
        )

        price_per_kwh = energy_data.get("price_per_kwh", 0.35)
        currency = energy_data.get("currency", "EUR")
        
        if "weekly" not in energy_data:
            energy_data["weekly"] = {}
        energy_data["weekly"][week_str] = {
            "kwh": round(weekly_kwh, 4),
            "cost": round(weekly_kwh * price_per_kwh, 4),
            "currency": currency,
        }

        # Calculate monthly total
        month_days = []
        day = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        while day.month == now.month:
            month_days.append(day.strftime("%Y-%m-%d"))
            day += timedelta(days=1)

        monthly_kwh = sum(
            energy_data.get("daily", {}).get(date, {}).get("kwh", 0.0)
            for date in month_days
        )

        if "monthly" not in energy_data:
            energy_data["monthly"] = {}
        energy_data["monthly"][month_str] = {
            "kwh": round(monthly_kwh, 4),
            "cost": round(monthly_kwh * price_per_kwh, 4),
            "currency": currency,
        }

    async def _cleanup_old_data(self, energy_data: Dict[str, Any]):
        """Remove data older than 6 months."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=180)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")
        cutoff_week = cutoff_date.strftime("%Y-W%W")
        cutoff_month = cutoff_date.strftime("%Y-%m")

        # Clean daily data
        if "daily" in energy_data:
            energy_data["daily"] = {
                k: v for k, v in energy_data["daily"].items()
                if k >= cutoff_str
            }

        # Clean weekly data
        if "weekly" in energy_data:
            energy_data["weekly"] = {
                k: v for k, v in energy_data["weekly"].items()
                if k >= cutoff_week
            }

        # Clean monthly data
        if "monthly" in energy_data:
            energy_data["monthly"] = {
                k: v for k, v in energy_data["monthly"].items()
                if k >= cutoff_month
            }

    async def _update_sensor_entities(self):
        """Update HA sensor entities with current energy data."""
        try:
            energy_data = self.data_store.getDeep("Energy", {})
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            week_str = datetime.now(timezone.utc).strftime("%Y-W%W")
            month_str = datetime.now(timezone.utc).strftime("%Y-%m")

            daily = energy_data.get("daily", {}).get(today_str, {})
            weekly = energy_data.get("weekly", {}).get(week_str, {})
            monthly = energy_data.get("monthly", {}).get(month_str, {})

            # Calculate total runtime today
            runtime = daily.get("runtime", {})
            total_runtime = sum(runtime.values())

            # Emit event to update sensors
            await self.event_manager.emit("EnergyUpdate", {
                "room": self.room,
                "today_kwh": daily.get("kwh", 0.0),
                "today_cost": daily.get("cost", 0.0),
                "today_runtime_hours": round(total_runtime, 2),
                "week_kwh": weekly.get("kwh", 0.0),
                "week_cost": weekly.get("cost", 0.0),
                "month_kwh": monthly.get("kwh", 0.0),
                "month_cost": monthly.get("cost", 0.0),
                "price_per_kwh": energy_data.get("price_per_kwh", 0.35),
                "currency": energy_data.get("currency", "EUR"),
            })

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error updating sensor entities: {e}")

    def get_energy_summary(self) -> Dict[str, Any]:
        """Get current energy summary for the room."""
        energy_data = self.data_store.getDeep("Energy", {})
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        week_str = datetime.now(timezone.utc).strftime("%Y-W%W")
        month_str = datetime.now(timezone.utc).strftime("%Y-%m")

        daily = energy_data.get("daily", {}).get(today_str, {})
        weekly = energy_data.get("weekly", {}).get(week_str, {})
        monthly = energy_data.get("monthly", {}).get(month_str, {})

        # Get active tracking info
        active_devices = {}
        for device_name, tracking in self._device_tracking.items():
            active_devices[device_name] = {
                "power_watts": tracking.get("power_watts", 0.0),
                "is_estimated": tracking.get("is_estimated", True),
                "session_kwh": round(tracking.get("session_kwh", 0.0), 4),
            }

        return {
            "today": {
                "kwh": daily.get("kwh", 0.0),
                "cost": daily.get("cost", 0.0),
                "runtime": daily.get("runtime", {}),
            },
            "week": {
                "kwh": weekly.get("kwh", 0.0),
                "cost": weekly.get("cost", 0.0),
            },
            "month": {
                "kwh": monthly.get("kwh", 0.0),
                "cost": monthly.get("cost", 0.0),
            },
            "price_per_kwh": energy_data.get("price_per_kwh", 0.35),
            "currency": energy_data.get("currency", "EUR"),
            "active_devices": active_devices,
        }

    async def set_price_per_kwh(self, price: float):
        """Update the electricity price per kWh."""
        try:
            if price < 0:
                _LOGGER.error(f"[{self.room}] Invalid negative price: {price}")
                return

            energy_data = self.data_store.getDeep("Energy", {})
            energy_data["price_per_kwh"] = round(price, 4)
            self.data_store.setDeep("Energy", energy_data)

            # Recalculate costs with new price
            await self._aggregate_and_persist()
            await self._update_sensor_entities()

            _LOGGER.info(f"[{self.room}] Energy price updated to {price:.4f} EUR/kWh")

        except Exception as e:
            _LOGGER.error(f"[{self.room}] Error setting energy price: {e}")

    async def shutdown(self):
        """Shutdown the energy manager gracefully."""
        self._shutdown = True
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        # Final calculation and persistence before shutdown
        await self._calculate_all_tracked_devices()
        await self._aggregate_and_persist()

        _LOGGER.info(f"[{self.room}] OGBEnergyManager shutdown complete")

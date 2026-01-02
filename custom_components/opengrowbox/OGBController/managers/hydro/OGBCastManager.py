import asyncio
import logging

from ...data.OGBDataClasses.OGBPublications import (OGBHydroAction,
                                              OGBHydroPublication,
                                              OGBRetrieveAction,
                                              OGBRetrivePublication)
from .crop_steering.OGBCSManager import OGBCSManager
from ..medium.OGBMediumManager import OGBMediumManager

_LOGGER = logging.getLogger(__name__)


class OGBCastManager:
    def __init__(self, hass, dataStore, eventManager, room, medium_manager=None):
        self.name = "OGB Plant Cast Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager
        self.isInitialized = False

        # Use shared medium_manager if provided, otherwise create new one
        self.mediumManager = medium_manager if medium_manager else OGBMediumManager(hass, dataStore, eventManager, room)
        self.CropSteeringManager = OGBCSManager(hass, dataStore, eventManager, room)

        self.currentMode = None
        self._hydro_task: asyncio.Task | None = None
        self._retrive_task: asyncio.Task | None = None
        self._crop_steering_task: asyncio.Task | None = None
        self._plant_watering_task: asyncio.Task | None = None

        # Pump state registry for conflict prevention
        self.active_pumps = (
            {}
        )  # device_id -> {"operation": str, "start_time": datetime, "task": Task}

        # Pump compatibility matrix - operations that can run simultaneously
        self.PUMP_COMPATIBILITY = {
            "retrieve": [
                "hydro",
                "crop_steering",
                "plant_watering",
            ],  # Retrieve can work with any hydro operation
            "hydro": ["retrieve"],  # Hydro can work with retrieve only
            "crop_steering": ["retrieve"],  # Crop-steering can work with retrieve only
            "plant_watering": [
                "retrieve"
            ],  # Plant-watering can work with retrieve only
            "feed_nutrients": [],  # Feed operations cannot work with others (tank operations)
        }

        self.event_manager.on("HydroModeChange", self.HydroModeChange)
        self.event_manager.on("HydroModeStart", self.hydro_Mode)
        self.event_manager.on("PlamtWateringStart", self.hydro_PlantWatering)
        self.event_manager.on("HydroModeRetrieveChange", self.HydroModRetrieveChange)
        self.event_manager.on("HydroRetriveModeStart", self.retrive_Mode)
        self.event_manager.on(
            "CropSteeringChanges", self.CropSteeringManager.handle_mode_change
        )

    ## Pump State Management
    async def _register_pump_operation(
        self, device_id: str, operation: str, task: asyncio.Task = None
    ):
        """Register a pump operation to prevent conflicts."""
        from datetime import datetime

        self.active_pumps[device_id] = {
            "operation": operation,
            "start_time": datetime.now(),
            "task": task or asyncio.current_task(),
        }
        _LOGGER.debug(
            f"[{self.room}] Registered pump operation: {device_id} -> {operation}"
        )

    async def _unregister_pump_operation(self, device_id: str):
        """Unregister a completed pump operation."""
        if device_id in self.active_pumps:
            del self.active_pumps[device_id]
            _LOGGER.debug(f"[{self.room}] Unregistered pump operation: {device_id}")

    def _check_pump_compatibility(self, device_id: str, new_operation: str) -> bool:
        """Check if new operation can run alongside existing ones."""
        if device_id not in self.active_pumps:
            return True

        current_operation = self.active_pumps[device_id]["operation"]

        # Check compatibility matrix
        compatible_ops = self.PUMP_COMPATIBILITY.get(new_operation, [])
        return current_operation in compatible_ops

    async def _cancel_conflicting_operations(self, device_id: str, new_operation: str):
        """Cancel operations that conflict with the new one, except retrieve."""
        if device_id not in self.active_pumps:
            return

        current_op = self.active_pumps[device_id]["operation"]

        # Don't cancel retrieve operations - they're compatible with everything
        if current_op == "retrieve":
            return

        # Cancel non-retrieve operations when starting anything except retrieve
        if new_operation != "retrieve":
            await self._safe_cancel_task(self.active_pumps[device_id]["task"])
            del self.active_pumps[device_id]
            _LOGGER.info(
                f"[{self.room}] Cancelled conflicting operation {current_op} for {device_id}"
            )

    async def _safe_cancel_task(self, task: asyncio.Task, timeout: float = 5.0):
        """Safely cancel a task with timeout and cleanup."""
        if not task or task.done():
            return

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                f"[{self.room}] Task did not cancel within {timeout}s timeout"
            )
            # Force cleanup of pump states
            await self._emergency_pump_cleanup()
        except asyncio.CancelledError:
            pass

    async def _emergency_pump_cleanup(self):
        """Emergency cleanup when tasks don't cancel properly."""
        _LOGGER.warning(f"[{self.room}] Performing emergency pump cleanup")
        for device_id, pump_info in list(self.active_pumps.items()):
            try:
                # Force pumps off
                pump_action = OGBHydroAction(
                    Name=self.room, Action="off", Device=device_id, Cycle=False
                )
                await self.event_manager.emit("PumpAction", pump_action)
                _LOGGER.info(f"[{self.room}] Emergency shutdown: {device_id}")
            except Exception as e:
                _LOGGER.error(
                    f"[{self.room}] Emergency cleanup failed for {device_id}: {e}"
                )

        self.active_pumps.clear()

    ## Task Helper
    async def _cancel_all_tasks(self):
        """Cancels all running hydro/soil related tasks with proper cleanup"""
        tasks = [
            (self._hydro_task, "hydro"),
            (self._retrive_task, "retrieve"),
            (self._plant_watering_task, "plant_watering"),
            (self._crop_steering_task, "crop_steering"),
        ]

        for task, operation_type in tasks:
            if task is not None and not task.done():
                _LOGGER.info(f"[{self.room}] Cancelling {operation_type} task")
                await self._safe_cancel_task(task)

        # Clear all pump registrations
        self.active_pumps.clear()

        # Reset task references
        self._hydro_task = None
        self._retrive_task = None
        self._plant_watering_task = None
        self._crop_steering_task = None

        await self.CropSteeringManager.stop_all_operations()

        await self.CropSteeringManager.stop_all_operations()

        # Reset all task references
        self._hydro_task = None
        self._retrive_task = None
        self._plant_watering_task = None

    async def _ensure_retrieve_system(self, primary_mode: str):
        """Ensure retrieve system is running alongside the primary hydro mode."""
        retrieve_config = self.data_store.getDeep("Hydro.Retrieve")
        retrieve_active = self.data_store.getDeep("Hydro.R_Active")

        # Start retrieve if configured and not already running
        if retrieve_config and retrieve_config != "OFF" and retrieve_active:
            if not hasattr(self, "_retrive_task") or (
                hasattr(self, "_retrive_task")
                and (not self._retrive_task or self._retrive_task.done())
            ):

                _LOGGER.info(
                    f"[{self.room}] Starting retrieve system alongside {primary_mode}"
                )
                PumpDevices = self.data_store.getDeep("capabilities.canPump")
                intervall_raw = self.data_store.getDeep("Hydro.R_Intervall")
                duration_raw = self.data_store.getDeep("Hydro.R_Duration")

                if intervall_raw and duration_raw:
                    intervall = float(intervall_raw)
                    duration = float(duration_raw)
                    cycle = True  # Retrieve should always cycle

                    await self.retrive_Mode(cycle, intervall, duration, PumpDevices)

    ## Hydro Modes
    async def HydroModeChange(self, pumpAction):
        _LOGGER.debug(f"🔍 {self.room} HydroModeChange called with: {pumpAction}")
        
        isActive = self.data_store.getDeep("Hydro.Active")
        intervall_raw = self.data_store.getDeep("Hydro.Intervall")
        duration_raw = self.data_store.getDeep("Hydro.Duration")
        mode = self.data_store.getDeep("Hydro.Mode")
        cycle = self.data_store.getDeep("Hydro.Cycle")
        PumpDevices = self.data_store.getDeep("capabilities.canPump")

        _LOGGER.debug(f"🔍 {self.room} Hydro Config: Active={isActive}, Mode={mode}, Cycle={cycle}")
        _LOGGER.debug(f"🔍 {self.room} Hydro Timing: Intervall={intervall_raw}, Duration={duration_raw}")
        _LOGGER.debug(f"🔍 {self.room} Pump Devices: {PumpDevices}")

        # Convert and validate values (0 or None means not configured)
        try:
            intervall = float(intervall_raw) if intervall_raw not in (None, 0, 0.0) else None
            duration = float(duration_raw) if duration_raw not in (None, 0, 0.0) else None
        except (ValueError, TypeError):
            intervall = None
            duration = None

        if intervall is None or duration is None or intervall <= 0 or duration <= 0:
            _LOGGER.error(f"❌ {self.room} HYDRO NOT CONFIGURED: Intervall={intervall_raw}, Duration={duration_raw}")
            _LOGGER.error(f"💡 {self.room} Set values via: number.ogb_hydropumpintervall_{self.room.lower()} and number.ogb_hydropumpduration_{self.room.lower()}")
            return

        # Stoppe ALLE laufenden Tasks bei jedem Moduswechsel
        await self._cancel_all_tasks()

        if mode == "OFF":
            sysmessage = "Hydro mode is OFFLINE"
            self.data_store.setDeep("Hydro.Active", False)
            await self.event_manager.emit("PumpAction", {"action": "off"})

        elif mode == "Hydro":
            sysmessage = "Hydro mode active"
            self.data_store.setDeep("Hydro.Active", True)
            self.data_store.setDeep("Hydro.Mode", mode)
            self.data_store.setDeep("CropSteering.Mode", None)
            self.data_store.setDeep("CropSteering.Active", False)
            await self.hydro_Mode(cycle, intervall, duration, PumpDevices)
            # Start retrieve system alongside hydro
            await self._ensure_retrieve_system("hydro")

        elif mode == "Crop-Steering":
            sysmessage = "Crop-Steering mode active"
            self.data_store.setDeep("CropSteering.Active", True)
            self.data_store.setDeep("CropSteering.Mode", mode)
            self.data_store.setDeep("Hydro.Mode", mode)
            self.data_store.setDeep("Hydro.Active", False)
            await self.CropSteeringManager.handle_mode_change(pumpAction)
            # Start retrieve system alongside crop-steering
            await self._ensure_retrieve_system("crop_steering")

        elif mode == "Plant-Watering":
            sysmessage = "Plant watering mode active"
            self.data_store.setDeep("CropSteering.Active", False)
            self.data_store.setDeep("CropSteering.Mode", mode)
            self.data_store.setDeep("Hydro.Mode", mode)
            self.data_store.setDeep("Hydro.Active", False)
            await self.hydro_PlantWatering(intervall, duration, PumpDevices)
            # Start retrieve system alongside plant-watering
            await self._ensure_retrieve_system("plant_watering")
        elif mode == "Config":
            return
        else:
            sysmessage = f"Unknown mode: {mode}"

        actionMap = OGBHydroPublication(
            Name=self.room,
            Mode=mode,
            Cycle=cycle,
            Active=isActive,
            Message=sysmessage,
            Intervall=intervall,
            Duration=duration,
            Devices=PumpDevices,
        )
        await self.event_manager.emit("LogForClient", actionMap, haEvent=True)

    async def hydro_Mode(
        self,
        cycle: bool,
        interval: float,
        duration: float,
        pumpDevices,
        log_prefix: str = "Hydro",
    ):
        """Handle hydro pump operations - for mistpump, waterpump, aeropump, dwcpump, rdwcpump."""
        
        _LOGGER.debug(f"🔍 {self.room} hydro_Mode called: cycle={cycle}, interval={interval}, duration={duration}")
        _LOGGER.debug(f"🔍 {self.room} pumpDevices: {pumpDevices}")

        valid_keywords = ["mist", "water", "aero", "cloner", "dwc", "rdwc"]
        devices = pumpDevices["devEntities"]
        _LOGGER.debug(f"🔍 {self.room} All pump devices: {devices}")
        
        active_pumps = [
            dev
            for dev in devices
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]
        _LOGGER.debug(f"🔍 {self.room} Filtered active pumps: {active_pumps}")
        await self.event_manager.emit("LogForClient", active_pumps, haEvent=True)

        if not active_pumps:
            _LOGGER.error(f"❌ {self.room} NO ACTIVE PUMPS FOUND! Stopping.")
            return

        if not active_pumps:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "INVALID PUMPS",
                    "message": f"{log_prefix}: No valid pumps found.",
                },
                haEvent=True,
            )
            return

        async def run_cycle():
            try:
                while True:
                    # Turn ON all hydro pumps
                    for dev_id in active_pumps:
                        # Register pump operation
                        await self._register_pump_operation(dev_id, "hydro")

                        pumpAction = OGBHydroAction(
                            Name=self.room, Action="on", Device=dev_id, Cycle=cycle
                        )
                        await self.event_manager.emit("PumpAction", pumpAction)

                    # Wait for duration (pumps ON)
                    await asyncio.sleep(float(duration))

                    # Turn OFF all hydro pumps
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(
                            Name=self.room, Action="off", Device=dev_id, Cycle=cycle
                        )
                        await self.event_manager.emit("PumpAction", pumpAction)

                        # Unregister pump operation
                        await self._unregister_pump_operation(dev_id)

                    # Wait for interval (pumps OFF)
                    await asyncio.sleep(float(interval) * 60)

            except asyncio.CancelledError:
                # If cancelled, ensure pumps are turned off
                for dev_id in active_pumps:
                    pumpAction = OGBHydroAction(
                        Name=self.room, Action="off", Device=dev_id, Cycle=cycle
                    )
                    await self.event_manager.emit("PumpAction", pumpAction)
                raise

        # Cancel existing task if running
        if self._hydro_task is not None:
            self._hydro_task.cancel()
            try:
                await self._hydro_task
            except asyncio.CancelledError:
                pass
            self._hydro_task = None

        if cycle:
            # Start cycling task
            self._hydro_task = asyncio.create_task(run_cycle())
            msg = (
                f"{log_prefix} mode started: ON for {duration}s, "
                f"OFF for {interval}m, repeating."
            )
        else:
            # One-time or permanent ON: just turn hydro pumps on
            for dev_id in active_pumps:
                # Register pump operation
                await self._register_pump_operation(dev_id, "hydro")

                pumpAction = OGBHydroAction(
                    Name=self.room, Action="on", Device=dev_id, Cycle=cycle
                )
                await self.event_manager.emit("PumpAction", pumpAction)
            msg = f"{log_prefix} cycle disabled – hydro pumps set to always ON."

        await self.event_manager.emit("LogForClient", msg, haEvent=True)

    async def hydro_PlantWatering(
        self,
        interval: float,
        duration: float,
        pumpDevices,
        cycle: bool = True,
        log_prefix: str = "Hydro",
    ):
        valid_keywords = ["water", "cast"]
        devices = pumpDevices["devEntities"]
        active_pumps = [
            dev
            for dev in devices
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]

        if not active_pumps:
            await self.event_manager.emit(
                "LogForClient", f"{log_prefix}: No valid pumps found.", haEvent=True
            )
            return

        async def run_cycle():
            try:
                while True:
                    for dev_id in active_pumps:
                        # Register pump operation
                        await self._register_pump_operation(dev_id, "plant_watering")

                        pumpAction = OGBHydroAction(
                            Name=self.room, Action="on", Device=dev_id, Cycle=cycle
                        )
                        await self.event_manager.emit("PumpAction", pumpAction)
                    await asyncio.sleep(float(duration))
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(
                            Name=self.room, Action="off", Device=dev_id, Cycle=cycle
                        )
                        await self.event_manager.emit("PumpAction", pumpAction)

                        # Unregister pump operation
                        await self._unregister_pump_operation(dev_id)
                    await asyncio.sleep(
                        float(interval) * 60
                    )  # interval is in minutes for plant watering
            except asyncio.CancelledError:
                # if we get cancelled, make sure pumps end up off
                for dev_id in active_pumps:
                    pumpAction = OGBHydroAction(
                        Name=self.room, Action="off", Device=dev_id, Cycle=cycle
                    )
                    await self.event_manager.emit("PumpAction", pumpAction)
                raise

        # Cancel existing plant watering task (not hydro task)
        if self._plant_watering_task is not None:
            await self._safe_cancel_task(self._plant_watering_task)
            self._plant_watering_task = None

        if cycle:
            self._plant_watering_task = asyncio.create_task(run_cycle())
            msg = (
                f"{log_prefix} mode started: ON for {duration}s, "
                f"OFF for {interval}m, repeating."
            )
        else:
            # Permanent ON mode
            for dev_id in active_pumps:
                # Register pump operation
                await self._register_pump_operation(dev_id, "plant_watering")

                pumpAction = OGBHydroAction(
                    Name=self.room, Action="on", Device=dev_id, Cycle=cycle
                )
                await self.event_manager.emit("PumpAction", pumpAction)
            msg = (
                f"{log_prefix} cycle disabled – plant watering pumps set to always ON."
            )

        await self.event_manager.emit("LogForClient", msg, haEvent=True)

    # Hydro Retrive
    async def HydroModRetrieveChange(self, pumpAction):
        intervall_raw = self.data_store.getDeep("Hydro.R_Intervall")
        duration_raw = self.data_store.getDeep("Hydro.R_Duration")
        mode = self.data_store.getDeep("Hydro.Retrieve")
        isActive = self.data_store.getDeep("Hydro.R_Active")
        PumpDevices = self.data_store.getDeep("capabilities.canPump")
        cycle = True

        # Convert and validate values (0 or None means not configured)
        try:
            intervall = float(intervall_raw) if intervall_raw not in (None, 0, 0.0) else None
            duration = float(duration_raw) if duration_raw not in (None, 0, 0.0) else None
        except (ValueError, TypeError):
            intervall = None
            duration = None

        if intervall is None or duration is None or intervall <= 0 or duration <= 0:
            _LOGGER.debug(f"{self.room} Hydro Retrieve not configured (Intervall={intervall_raw}, Duration={duration_raw})")
            return

        if mode is False:
            await self.event_manager.emit("RetrieveAction", {"action": "off"})
            if self._retrive_task is not None:
                self._retrive_task.cancel()
                self.data_store.setDeep("Hydro.R_Active", False)
                try:
                    await self._retrive_task
                except asyncio.CancelledError:
                    pass
                self._retrive_task = None
            return

        sysmessage = "Hydro Retrive mode active"
        self.data_store.setDeep("Hydro.R_Active", True)
        await self.retrive_Mode(cycle, intervall, duration, PumpDevices)

        actionMap = OGBRetrivePublication(
            Name=self.room,
            Cycle=cycle,
            Active=isActive,
            Mode=mode,
            Message=sysmessage,
            Intervall=intervall,
            Duration=duration,
            Devices=PumpDevices,
        )
        await self.event_manager.emit("LogForClient", actionMap, haEvent=True)

    async def retrive_Mode(
        self,
        cycle: bool,
        interval: float,
        duration: float,
        pumpDevices,
        log_prefix: str = "Retrive",
    ):
        """Handle retrive pump operations - only for retrievepump devices."""

        valid_keywords = ["return", "retrieve"]

        devices = pumpDevices["devEntities"]
        active_pumps = [
            dev
            for dev in devices
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]
        await self.event_manager.emit("LogForClient", active_pumps, haEvent=True)

        if not active_pumps:
            await self.event_manager.emit(
                "LogForClient",
                f"{log_prefix}: No valid Retrive pumps found.",
                haEvent=True,
            )
            return

        async def run_cycle():
            try:
                while True:
                    # Turn ON all retrive pumps
                    for dev_id in active_pumps:
                        # Register pump operation
                        await self._register_pump_operation(dev_id, "retrieve")

                        retrieveAction = OGBRetrieveAction(
                            Name=self.room, Action="on", Device=dev_id, Cycle=cycle
                        )
                        await self.event_manager.emit("RetrieveAction", retrieveAction)

                    # Wait for duration (pumps ON)
                    await asyncio.sleep(float(duration))

                    # Turn OFF all retrive pumps
                    for dev_id in active_pumps:
                        retrieveAction = OGBRetrieveAction(
                            Name=self.room, Action="off", Device=dev_id, Cycle=cycle
                        )
                        await self.event_manager.emit("RetrieveAction", retrieveAction)

                        # Unregister pump operation
                        await self._unregister_pump_operation(dev_id)

                    # Wait for interval (pumps OFF)
                    await asyncio.sleep(float(interval) * 60)

            except asyncio.CancelledError:
                # If cancelled, ensure pumps are turned off
                for dev_id in active_pumps:
                    retrieveAction = OGBRetrieveAction(
                        Name=self.room, Action="off", Device=dev_id, Cycle=cycle
                    )
                    await self.event_manager.emit("RetrieveAction", retrieveAction)
                raise

        # Cancel existing task if running
        if self._retrive_task is not None:
            self._retrive_task.cancel()
            try:
                await self._retrive_task
            except asyncio.CancelledError:
                pass
            self._retrive_task = None

        if cycle:
            # Start cycling task
            self._retrive_task = asyncio.create_task(run_cycle())
            msg = (
                f"{log_prefix} mode started: ON for {duration}s, "
                f"OFF for {interval}m, repeating."
            )
        else:
            # One-time or permanent ON: just turn retrive pumps on
            for dev_id in active_pumps:
                # Register pump operation
                await self._register_pump_operation(dev_id, "retrieve")

                retrieveAction = OGBRetrieveAction(
                    Name=self.room, Action="on", Device=dev_id, Cycle=cycle
                )
                await self.event_manager.emit("RetrieveAction", retrieveAction)
            msg = f"{log_prefix} cycle disabled – retrive pumps set to always ON."

        await self.event_manager.emit("LogForClient", msg, haEvent=True)

    def log(self, log_message):
        """Logs the performed action."""
        logHeader = f"{self.name}"
        _LOGGER.debug(f" {logHeader} : {log_message} ")

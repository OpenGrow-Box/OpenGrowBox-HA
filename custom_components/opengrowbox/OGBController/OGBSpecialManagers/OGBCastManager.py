import logging
import asyncio

from ..OGBSpecialManagers.CropSteering.OGBCSManager import OGBCSManager
from ..OGBSpecialManagers.OGBMediumManager import OGBMediumManager
from ..OGBDataClasses.OGBPublications import OGBHydroPublication,OGBHydroAction,OGBRetrieveAction,OGBRetrivePublication


_LOGGER = logging.getLogger(__name__)


class OGBCastManager:
    def __init__(self, hass, dataStore, eventManager,room):
        self.name = "OGB Plant Cast Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore 
        self.eventManager = eventManager
        self.isInitialized = False

        self.mediumManager = OGBMediumManager(hass,dataStore,eventManager,room)
        self.CropSteeringManager = OGBCSManager(hass,dataStore,eventManager,room)

        self.currentMode = None
        self._hydro_task: asyncio.Task | None = None    
        self._retrive_task: asyncio.Task | None = None           
        self._crop_steering_task: asyncio.Task | None = None
        self._plant_watering_task: asyncio.Task | None = None
        
        self.eventManager.on("HydroModeChange", self.HydroModeChange)
        self.eventManager.on("HydroModeStart", self.hydro_Mode) 
        self.eventManager.on("PlamtWateringStart", self.hydro_PlantWatering)
        self.eventManager.on("HydroModeRetrieveChange", self.HydroModRetrieveChange)
        self.eventManager.on("HydroRetriveModeStart", self.retrive_Mode)
        self.eventManager.on("CropSteeringChange", self.CropSteeringManager.CropSteeringChanges) 

    ## Task Helper
    async def _cancel_all_tasks(self):
        """Cancels all running hydro/soil related tasks"""
        tasks = [
            self._hydro_task,
            self._retrive_task,
            self._plant_watering_task,
            self._crop_steering_task
        ]
        
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Reset all task references
        self._hydro_task = None
        self._retrive_task = None
        self._plant_watering_task = None
        self._crop_steering_task = None
  
    ## Hydro Modes
    async def HydroModeChange(self, pumpAction):
        isActive = self.dataStore.getDeep("Hydro.Active")
        intervall_raw = self.dataStore.getDeep("Hydro.Intervall")
        duration_raw = self.dataStore.getDeep("Hydro.Duration")
        mode = self.dataStore.getDeep("Hydro.Mode")
        cycle = self.dataStore.getDeep("Hydro.Cycle")
        PumpDevices = self.dataStore.getDeep("capabilities.canPump")

        if intervall_raw is None or duration_raw is None:
            return

        intervall = float(intervall_raw)
        duration = float(duration_raw)

        # Stoppe ALLE laufenden Tasks bei jedem Moduswechsel
        await self._cancel_all_tasks()

        if mode == "OFF":
            sysmessage = "Hydro mode is OFFLINE"
            self.dataStore.setDeep("Hydro.Active", False)
            await self.eventManager.emit("PumpAction", {"action": "off"})
            
        elif mode == "Hydro":
            sysmessage = "Hydro mode active"
            self.dataStore.setDeep("Hydro.Active", True)
            self.dataStore.setDeep("Hydro.Mode", mode)
            self.dataStore.setDeep("Soil.Mode", None)
            self.dataStore.setDeep("Soil.Active", False)
            await self.hydro_Mode(cycle, intervall, duration, PumpDevices)
            
        elif mode == "Crop-Steering":
            sysmessage = "Crop-Steering mode active"
            self.dataStore.setDeep("Soil.Active", True)
            self.dataStore.setDeep("Soil.Mode", mode)
            self.dataStore.setDeep("Hydro.Mode", mode)
            self.dataStore.setDeep("Hydro.Active", False)
            await self.CropSteeringManager.CropSteeringChanges(pumpAction)

        elif mode == "Plant-Watering":
            sysmessage = "Plant watering mode active"
            self.dataStore.setDeep("Soil.Active", True)
            self.dataStore.setDeep("Soil.Mode", mode)
            self.dataStore.setDeep("Hydro.Mode", mode)
            self.dataStore.setDeep("Hydro.Active", False)
            await self.hydro_PlantWatering(intervall, duration, PumpDevices)

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
            Devices=PumpDevices
        )
        await self.eventManager.emit("LogForClient", actionMap, haEvent=True)

    async def hydro_Mode(self, cycle: bool, interval: float, duration: float, pumpDevices, log_prefix: str = "Hydro"):
        """Handle hydro pump operations - for mistpump, waterpump, aeropump, dwcpump, rdwcpump."""
        
        valid_types = ["mistpump", "waterpump", "aeropump", "dwcpump", "rdwcpump","clonerpump"]
        devices = pumpDevices["devEntities"]
        active_pumps = [dev for dev in devices if dev in valid_types]
        await self.eventManager.emit("LogForClient", active_pumps, haEvent=True)

        if not active_pumps: return

        if not active_pumps:
            await self.eventManager.emit(
                "LogForClient",
                f"{log_prefix}: No valid pumps found.",
                haEvent=True
            )
            return

        async def run_cycle():
            try:
                while True:
                    # Turn ON all hydro pumps
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)
      
                    # Wait for duration (pumps ON)
                    await asyncio.sleep(float(duration))
                    
                    # Turn OFF all hydro pumps
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)

                    # Wait for interval (pumps OFF)
                    await asyncio.sleep(float(interval) * 60)
                    
            except asyncio.CancelledError:
                # If cancelled, ensure pumps are turned off
                for dev_id in active_pumps:
                    pumpAction = OGBHydroAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                    await self.eventManager.emit("PumpAction", pumpAction)
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
                pumpAction = OGBHydroAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                await self.eventManager.emit("PumpAction", pumpAction)
            msg = f"{log_prefix} cycle disabled – hydro pumps set to always ON."

        await self.eventManager.emit("LogForClient", msg, haEvent=True)
        
    async def hydro_PlantWatering(self,interval: float, duration: float, pumpDevices, cycle: bool = True,log_prefix: str = "Hydro"):
        valid_types = ["waterpump"]
        devices = pumpDevices["devEntities"]
        active_pumps = [dev for dev in devices if any(t in dev for t in valid_types)]

        if not active_pumps:
            await self.eventManager.emit(
                "LogForClient",
                f"{log_prefix}: No valid pumps found.",
                haEvent=True
            )
            return

        async def run_cycle():
            try:
                while True:
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room,Action="on",Device=dev_id,Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)
                    await asyncio.sleep(float(duration))
                    for dev_id in active_pumps:
                        pumpAction = OGBHydroAction(Name=self.room,Action="off",Device=dev_id,Cycle=cycle)
                        await self.eventManager.emit("PumpAction", pumpAction)
                    await asyncio.sleep(float(interval)*60)
            except asyncio.CancelledError:
                # if we get cancelled, make sure pumps end up off
                for dev_id in active_pumps:
                    pumpAction = OGBHydroAction(Name=self.room,Action="off",Device=dev_id,Cycle=cycle)
                    await self.eventManager.emit("PumpAction", pumpAction)
                raise
        # If there's an existing task, cancel it
        if self._hydro_task is not None:
            self._hydro_task.cancel()
            try:
                await self._hydro_task
            except asyncio.CancelledError:
                pass
            self._hydro_task = None   
            
        if cycle:
            self._hydro_task = asyncio.create_task(run_cycle())
            msg = (
                f"{log_prefix} mode started: ON for {duration}s, "
                f"OFF for {interval}s, repeating."
            )
        else:
            return None

        await self.eventManager.emit("LogForClient", msg, haEvent=True)

    # Hydro Retrive 
    async def HydroModRetrieveChange(self, pumpAction):
        intervall_raw = self.dataStore.getDeep("Hydro.R_Intervall")
        duration_raw = self.dataStore.getDeep("Hydro.R_Duration")
        mode = self.dataStore.getDeep("Hydro.Retrieve")
        isActive = self.dataStore.getDeep("Hydro.R_Active")
        PumpDevices = self.dataStore.getDeep("capabilities.canPump")
        cycle = True

        if intervall_raw is None or duration_raw is None:
            return

        intervall = float(intervall_raw)
        duration = float(duration_raw)

        if mode is False:
            await self.eventManager.emit("RetrieveAction", {"action": "off"})
            if self._retrive_task is not None:
                self._retrive_task.cancel()
                self.dataStore.setDeep("Hydro.R_Active",False)
                try:
                    await self._retrive_task
                except asyncio.CancelledError:
                    pass
                self._retrive_task = None
            return

        sysmessage = "Hydro Retrive mode active"
        self.dataStore.setDeep("Hydro.R_Active",True)
        await self.retrive_Mode(cycle, intervall, duration, PumpDevices)

        actionMap = OGBRetrivePublication(
            Name=self.room,
            Cycle=cycle,
            Active=isActive,
            Mode=mode,
            Message=sysmessage,
            Intervall=intervall,
            Duration=duration,
            Devices=PumpDevices
        )
        await self.eventManager.emit("LogForClient", actionMap, haEvent=True)
            
    async def retrive_Mode(self, cycle: bool, interval: float, duration: float, pumpDevices, log_prefix: str = "Retrive"):
        """Handle retrive pump operations - only for retrievepump devices."""
        
        valid_types = ["retrievepump","returnpump"]
        devices = pumpDevices["devEntities"]
        active_pumps = [dev for dev in devices if dev in valid_types]
        await self.eventManager.emit("LogForClient", active_pumps, haEvent=True)
  
        
        if not active_pumps: return

        if not active_pumps:
            await self.eventManager.emit(
                "LogForClient",
                f"{log_prefix}: No valid Retrive pumps found.",
                haEvent=True
            )
            return

        async def run_cycle():
            try:
                while True:
                    # Turn ON all retrive pumps
                    for dev_id in active_pumps:
                        retrieveAction = OGBRetrieveAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("RetrieveAction", retrieveAction)
                    
                    # Wait for duration (pumps ON)
                    await asyncio.sleep(float(duration))
                    
                    # Turn OFF all retrive pumps
                    for dev_id in active_pumps:
                        retrieveAction = OGBRetrieveAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                        await self.eventManager.emit("RetrieveAction", retrieveAction)
                    
                    # Wait for interval (pumps OFF)
                    await asyncio.sleep(float(interval) * 60)
                    
            except asyncio.CancelledError:
                # If cancelled, ensure pumps are turned off
                for dev_id in active_pumps:
                    retrieveAction = OGBRetrieveAction(Name=self.room, Action="off", Device=dev_id, Cycle=cycle)
                    await self.eventManager.emit("RetrieveAction", retrieveAction)
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
                retrieveAction = OGBRetrieveAction(Name=self.room, Action="on", Device=dev_id, Cycle=cycle)
                await self.eventManager.emit("RetrieveAction", retrieveAction)
            msg = f"{log_prefix} cycle disabled – retrive pumps set to always ON."

        await self.eventManager.emit("LogForClient", msg, haEvent=True)

    def log(self, log_message):
        """Logs the performed action."""
        logHeader = f"{self.name}"
        _LOGGER.debug(f" {logHeader} : {log_message} ")
    

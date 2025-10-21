import logging
import asyncio
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

class OGBCSManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Crop Steering Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore 
        self.eventManager = eventManager
        self.isInitialized = False

        # Calibration parameters
        self.calibration_readings = []
        self.calibration_threshold = 2
        self.stability_tolerance = 0.5  # % change threshold for "stable"
        self.max_irrigation_attempts = 5

        self._crop_steering_task = None
        self._vwc_calibration_task = None

        self.eventManager.on("CropSteeringChange", self.CropSteeringChanges)
        self.eventManager.on("VWCCalibrationCommand", self.handle_vwc_calibration_command)

    # ==================== CROP STEERING MAIN ====================
    
    async def CropSteeringChanges(self, data):
        """Main handler for crop steering mode changes"""
        _LOGGER.error(f"CropSteeringPhase-Change: {data}")
        
        mode = self.dataStore.getDeep("Soil.Mode")
        isActive = self.dataStore.getDeep("Soil.Active")
        cropMode = self.dataStore.getDeep("Soil.ActiveMode")
        
        if "Automatic" in cropMode:
            await self.start_automatic_mode()
        elif "Disabled" in cropMode or "Config" in cropMode:
            await self.stop_crop_steering()
        elif "Manual" in cropMode:
            for phase in ["p0", "p1", "p2", "p3"]:
                if phase in cropMode:
                    _LOGGER.error(f"{self.room} CS-Phase: {phase} Start")
                    await self.start_manual_mode(phase)
                    break
        else:
            return

        soilMaxMoisture = self.dataStore.getDeep("isPlantDay.SoilMaxMoisture")
        soilMinMoisture = self.dataStore.getDeep("isPlantDay.SoilMinMoisture")
        plantPhase = self.dataStore.getDeep("isPlantDay.plantPhase")
        generativeWeek = self.dataStore.getDeep("isPlantDay.generativeWeek")
        dripperDevices = self.dataStore.getDeep("capabilities.canPump")
        
        sysmessage = "Crop Steering mode active"
        self.dataStore.setDeep("isPlantDay.CS_Active", True)
        
        await self.start_crop_steering_mode(
            soilMaxMoisture, 
            soilMinMoisture, 
            plantPhase, 
            generativeWeek,
            dripperDevices
        )
        
        await self.eventManager.emit(
            "LogForClient",
            f"CS Active: Phase={plantPhase}, Week={generativeWeek}",
            haEvent=True
        )

    async def start_crop_steering_mode(self, soilMaxMoisture, soilMinMoisture, 
                                       plantPhase, generativeWeek, dripperDevices):
        """Start the crop steering cycle"""
        
        valid_types = ["dripper"]
        devices = dripperDevices.get("devEntities", [])
        active_drippers = [dev for dev in devices if dev in valid_types]
        
        if not active_drippers:
            await self.eventManager.emit(
                "LogForClient",
                "CropSteering: No valid dripper devices found.",
                haEvent=True
            )
            return
        
        if self._crop_steering_task is not None:
            self._crop_steering_task.cancel()
            try:
                await self._crop_steering_task
            except asyncio.CancelledError:
                pass
            self._crop_steering_task = None
        
        self._crop_steering_task = asyncio.create_task(
            self.crop_steering_cycle(
                soilMaxMoisture, 
                soilMinMoisture, 
                plantPhase, 
                generativeWeek,
                active_drippers
            )
        )
        
        msg = f"CropSteering started: Phase={plantPhase}, Week={generativeWeek}"
        await self.eventManager.emit("LogForClient", msg, haEvent=True)

    async def crop_steering_cycle(self, soilMaxMoisture, soilMinMoisture, 
                                   plantPhase, generativeWeek, active_drippers):
        """Main crop steering cycle with phase management"""
        
        blockCheckIntervall = 5
        
        try:
            self.dataStore.setDeep("cropSteering.currentPhase", "P0")
            
            while True:
                lightOnTime = self.dataStore.getDeep("isPlantDay.lightOnTime")
                lightOffTime = self.dataStore.getDeep("isPlantDay.lightOffTime")
                currentMoisture = self.dataStore.getDeep("Soil.moist_current")
                currentEC = self.dataStore.getDeep("Soil.ec_current")
                
                if plantPhase == "veg":
                    targetDryBack = 15
                else:
                    if generativeWeek <= 4:
                        targetDryBack = 30
                    else:
                        targetDryBack = 25
                
                minMoistureAfterDryBack = soilMaxMoisture * (1 - targetDryBack / 100)
                
                currentTime = datetime.now().time()
                isLightOn = lightOnTime <= currentTime < lightOffTime
                
                currentPhase = self.dataStore.getDeep("cropSteering.currentPhase")
                
                if currentPhase == "P0":
                    if currentMoisture < soilMinMoisture:
                        self.dataStore.setDeep("cropSteering.currentPhase", "P1")
                        self.dataStore.setDeep("cropSteering.phaseStartTime", datetime.now())
                        await self.eventManager.emit("LogForClient", "CropSteering: P0 -> P1 (Plant thirsty)", haEvent=True)
                
                elif currentPhase == "P1":
                    if currentMoisture < soilMaxMoisture:
                        await self.irrigate(active_drippers, duration=30)
                    else:
                        self.dataStore.setDeep("cropSteering.currentPhase", "P2")
                        self.dataStore.setDeep("cropSteering.phaseStartTime", datetime.now())
                        await self.eventManager.emit("LogForClient", "CropSteering: P1 -> P2 (Block full)", haEvent=True)
                
                elif currentPhase == "P2":
                    if isLightOn:
                        holdThreshold = soilMaxMoisture * 0.95
                        if currentMoisture < holdThreshold:
                            await self.irrigate(active_drippers, duration=15)
                    else:
                        self.dataStore.setDeep("cropSteering.currentPhase", "P3")
                        self.dataStore.setDeep("cropSteering.phaseStartTime", datetime.now())
                        self.dataStore.setDeep("cropSteering.startNightMoisture", currentMoisture)
                        await self.eventManager.emit("LogForClient", "CropSteering: P2 -> P3 (Night DryBack)", haEvent=True)
                
                elif currentPhase == "P3":
                    startNightMoisture = self.dataStore.getDeep("cropSteering.startNightMoisture")
                    
                    if not isLightOn:
                        currentDryBack = ((startNightMoisture - currentMoisture) / startNightMoisture) * 100
                        
                        if plantPhase == "gen":
                            if generativeWeek <= 4:
                                if currentDryBack < targetDryBack * 0.8:
                                    await self.adjust_ec(increase=True)
                            else:
                                if currentDryBack > targetDryBack * 1.1:
                                    await self.adjust_ec(increase=False)
                        
                        if currentMoisture < minMoistureAfterDryBack * 0.9:
                            await self.irrigate(active_drippers, duration=20)
                            await self.eventManager.emit("LogForClient", "CropSteering: Emergency irrigation in P3", haEvent=True)
                    else:
                        self.dataStore.setDeep("cropSteering.currentPhase", "P0")
                        await self.eventManager.emit("LogForClient", "CropSteering: P3 -> P0 (Day starts)", haEvent=True)
                
                self.dataStore.setDeep("cropSteering.lastCheck", datetime.now())
                self.dataStore.setDeep("cropSteering.currentMoisture", currentMoisture)
                self.dataStore.setDeep("cropSteering.currentEC", currentEC)
                
                await asyncio.sleep(blockCheckIntervall)
                
        except asyncio.CancelledError:
            for dev_id in active_drippers:
                await self.eventManager.emit("DripperAction", {
                    "Name": self.room,
                    "Action": "off",
                    "Device": dev_id
                })
            await self.eventManager.emit("LogForClient", "CropSteering: Stopped, drippers turned off", haEvent=True)
            raise

    async def stop_crop_steering(self):
        """Stoppe Crop Steering Task"""
        if self._crop_steering_task is not None:
            self._crop_steering_task.cancel()
            try:
                await self._crop_steering_task
            except asyncio.CancelledError:
                pass
            self._crop_steering_task = None

    # ==================== AUTOMATIC MODE ====================
    
    async def start_automatic_mode(self):
        """Starte Automatic Mode - Sensor-gesteuert"""
        await self.stop_crop_steering()
        
        self._crop_steering_task = asyncio.create_task(
            self.automatic_crop_steering_cycle()
        )
        
        await self.eventManager.emit(
            "LogForClient",
            "CropSteering: Automatic mode started",
            haEvent=True
        )

    async def automatic_crop_steering_cycle(self):
        """AUTOMATIC MODE: Sensor-gesteuert"""
        try:
            self.dataStore.setDeep("Soil.CropPhase", "p0")
            
            while True:
                currentMoisture = self.dataStore.getDeep("Soil.moist_current")
                currentEC = self.dataStore.getDeep("Soil.ec_current")
                currentPhase = self.dataStore.getDeep("Soil.CropPhase")
                
                settings = self.get_phase_settings(currentPhase)
                
                lightOnTime = self.dataStore.getDeep("isPlantDay.lightOnTime")
                lightOffTime = self.dataStore.getDeep("isPlantDay.lightOffTime")
                currentTime = datetime.now().time()
                isLightOn = lightOnTime <= currentTime < lightOffTime
                
                if currentPhase == "p0":
                    if currentMoisture < settings["MinMoisture"]["value"]:
                        self.dataStore.setDeep("Soil.CropPhase", "p1")
                        self.dataStore.setDeep("Soil.phaseStartTime", datetime.now())
                        await self.log_phase_change("p0", "p1", "Plant thirsty")
                
                elif currentPhase == "p1":
                    if currentMoisture < settings["MaxMoisture"]["value"]:
                        dripperDevices = self.dataStore.getDeep("capabilities.canPump")
                        await self.irrigate(dripperDevices, duration=30)
                    else:
                        self.dataStore.setDeep("Soil.CropPhase", "p2")
                        self.dataStore.setDeep("Soil.phaseStartTime", datetime.now())
                        await self.log_phase_change("p1", "p2", "Block full")
                
                elif currentPhase == "p2":
                    if isLightOn:
                        holdThreshold = settings["MaxMoisture"]["value"] * 0.95
                        if currentMoisture < holdThreshold:
                            dripperDevices = self.dataStore.getDeep("capabilities.canPump")
                            await self.irrigate(dripperDevices, duration=15)
                    else:
                        self.dataStore.setDeep("Soil.CropPhase", "p3")
                        self.dataStore.setDeep("Soil.phaseStartTime", datetime.now())
                        self.dataStore.setDeep("Soil.startNightMoisture", currentMoisture)
                        await self.log_phase_change("p2", "p3", "Night DryBack")
                
                elif currentPhase == "p3":
                    if not isLightOn:
                        startNightMoisture = self.dataStore.getDeep("Soil.startNightMoisture")
                        targetDryBack = settings["MoistureDryBack"]["value"]
                        currentDryBack = ((startNightMoisture - currentMoisture) / startNightMoisture) * 100
                        
                        if currentDryBack < targetDryBack * 0.8:
                            await self.adjust_ec_to_target(settings["ECTarget"]["value"], increase=True)
                        elif currentDryBack > targetDryBack * 1.2:
                            await self.adjust_ec_to_target(settings["ECTarget"]["value"], increase=False)
                        
                        minAllowed = settings["MinMoisture"]["value"] * 0.9
                        if currentMoisture < minAllowed:
                            dripperDevices = self.dataStore.getDeep("capabilities.canPump")
                            await self.irrigate(dripperDevices, duration=20)
                            await self.eventManager.emit(
                                "LogForClient",
                                "CropSteering: Emergency irrigation in P3",
                                haEvent=True
                            )
                    else:
                        self.dataStore.setDeep("Soil.CropPhase", "p0")
                        await self.log_phase_change("p3", "p0", "Day starts")
                
                await asyncio.sleep(5)
                
        except asyncio.CancelledError:
            await self.turn_off_drippers()
            raise

    # ==================== MANUAL MODE ====================
    
    async def start_manual_mode(self, phase):
        """Starte Manual Mode - Zeit-gesteuert für eine Phase"""
        await self.stop_crop_steering()
        
        self._crop_steering_task = asyncio.create_task(
            self.manual_phase_cycle(phase)
        )
        
        await self.eventManager.emit(
            "LogForClient",
            f"CropSteering: Manual mode started - Phase {phase}",
            haEvent=True
        )

    async def manual_phase_cycle(self, phase):
        """MANUAL MODE: Zeit-gesteuert"""
        try:
            settings = self.get_phase_settings(phase)
            
            shot_intervall = settings["ShotIntervall"]["value"]
            shot_count = settings["ShotSum"]["value"]
            
            if shot_intervall <= 0 or shot_count <= 0:
                await self.eventManager.emit(
                    "LogForClient",
                    f"CropSteering: Invalid settings for {phase}",
                    haEvent=True
                )
                return
            
            self.dataStore.setDeep("Soil.shotCounter", 0)
            self.dataStore.setDeep("Soil.phaseStartTime", datetime.now())
            
            await self.eventManager.emit(
                "LogForClient",
                f"CropSteering Manual {phase}: {shot_count} shots every {shot_intervall} min",
                haEvent=True
            )
            
            while True:
                currentMoisture = self.dataStore.getDeep("Soil.moist_current")
                currentEC = self.dataStore.getDeep("Soil.ec_current")
                shotCounter = self.dataStore.getDeep("Soil.shotCounter")
                
                ec_target = settings["ECTarget"]["value"]
                ec_min = settings["MinEC"]["value"]
                ec_max = settings["MaxEC"]["value"]
                
                if ec_target > 0:
                    if currentEC < ec_min:
                        await self.adjust_ec_to_target(ec_target, increase=True)
                    elif currentEC > ec_max:
                        await self.adjust_ec_to_target(ec_target, increase=False)
                
                min_moisture = settings["MinMoisture"]["value"]
                
                if currentMoisture < min_moisture * 0.9:
                    await self.eventManager.emit(
                        "LogForClient",
                        f"CropSteering {phase}: Emergency irrigation",
                        haEvent=True
                    )
                    dripperDevices = self.dataStore.getDeep("capabilities.canPump")
                    await self.irrigate(dripperDevices, duration=30)
                
                lastIrrigation = self.dataStore.getDeep("Soil.lastIrrigationTime")
                now = datetime.now()
                
                if lastIrrigation is None:
                    should_irrigate = True
                else:
                    time_since_last = (now - lastIrrigation).total_seconds() / 60
                    should_irrigate = time_since_last >= shot_intervall
                
                if should_irrigate and shotCounter < shot_count:
                    dripperDevices = self.dataStore.getDeep("capabilities.canPump")
                    await self.irrigate(dripperDevices, duration=30)
                    shotCounter += 1
                    self.dataStore.setDeep("Soil.shotCounter", shotCounter)
                    self.dataStore.setDeep("Soil.lastIrrigationTime", now)
                    
                    await self.eventManager.emit(
                        "LogForClient",
                        f"CropSteering {phase}: Shot {shotCounter}/{shot_count}",
                        haEvent=True
                    )
                
                if shotCounter >= shot_count:
                    phaseStartTime = self.dataStore.getDeep("Soil.phaseStartTime")
                    elapsed_minutes = (now - phaseStartTime).total_seconds() / 60
                    
                    if elapsed_minutes >= shot_intervall:
                        self.dataStore.setDeep("Soil.shotCounter", 0)
                        self.dataStore.setDeep("Soil.phaseStartTime", now)
                        await self.eventManager.emit(
                            "LogForClient",
                            f"CropSteering {phase}: New cycle started",
                            haEvent=True
                        )
                
                await asyncio.sleep(10)
                
        except asyncio.CancelledError:
            await self.turn_off_drippers()
            raise

    # ==================== VWC CALIBRATION ====================
    
    async def handle_vwc_calibration_command(self, command_data):
        """
        Handle VWC calibration commands
        
        Expected:
        {
            "action": "start_max" | "start_min" | "stop",
            "phase": "p0" | "p1" | "p2" | "p3",
            "duration": 3600
        }
        """
        
        action = command_data.get("action")
        phase = command_data.get("phase", "p1")
        duration = command_data.get("duration", 3600)
        
        if action == "start_max":
            await self.start_vwc_max_calibration(phase=phase)
        elif action == "start_min":
            await self.start_vwc_min_calibration(phase=phase, dry_back_duration=duration)
        elif action == "stop":
            await self.stop_vwc_calibration()
        else:
            await self.eventManager.emit(
                "LogForClient",
                f"VWC Calibration: Unknown action '{action}'",
                haEvent=True
            )

    async def start_vwc_max_calibration(self, phase="p1"):
        """Start VWC max calibration"""
        
        if self._vwc_calibration_task is not None:
            await self.stop_vwc_calibration()
        
        self._vwc_calibration_task = asyncio.create_task(
            self._vwc_max_calibration_cycle(phase)
        )
        
        await self.eventManager.emit(
            "LogForClient",
            f"VWC Calibration started for {phase}",
            haEvent=True
        )

    async def _vwc_max_calibration_cycle(self, phase):
        """Main VWC max calibration cycle"""
        
        try:
            calibration_complete = False
            irrigation_count = 0
            previous_max_vwc = 0
            stable_readings = []
            
            await self.eventManager.emit(
                "LogForClient",
                "VWC Calibration: Starting auto-calibration",
                haEvent=True
            )
            
            while not calibration_complete and irrigation_count < self.max_irrigation_attempts:
                irrigation_count += 1
                
                await self.eventManager.emit(
                    "LogForClient",
                    f"VWC Calibration: Irrigation {irrigation_count}/{self.max_irrigation_attempts}",
                    haEvent=True
                )
                
                dripperDevices = self.dataStore.getDeep("capabilities.canPump")
                await self.irrigate(dripperDevices, duration=45)
                
                await self.eventManager.emit(
                    "LogForClient",
                    "VWC Calibration: Waiting for stabilization...",
                    haEvent=True
                )
                
                stable_vwc = await self._wait_for_vwc_stabilization(timeout=300)
                
                if stable_vwc is None:
                    await self.eventManager.emit(
                        "LogForClient",
                        "VWC Calibration: Timeout waiting for stabilization",
                        haEvent=True
                    )
                    break
                
                stable_readings.append(stable_vwc)
                
                vwc_increase = stable_vwc - previous_max_vwc
                vwc_increase_percent = (vwc_increase / previous_max_vwc * 100) if previous_max_vwc > 0 else 100
                
                await self.eventManager.emit(
                    "LogForClient",
                    f"VWC Calibration: VWC={stable_vwc:.1f}%, Increase={vwc_increase_percent:.1f}%",
                    haEvent=True
                )
                
                if len(stable_readings) >= 2:
                    last_increase = stable_readings[-1] - stable_readings[-2]
                    last_increase_percent = (last_increase / stable_readings[-2] * 100) if stable_readings[-2] > 0 else 0
                    
                    if abs(last_increase_percent) < self.stability_tolerance:
                        calibration_complete = True
                        max_vwc = stable_vwc
                        
                        await self.eventManager.emit(
                            "LogForClient",
                            f"VWC Calibration: COMPLETE! VWCMax={max_vwc:.1f}%",
                            haEvent=True
                        )
                        
                        self.dataStore.setDeep(f"Soil.VWCMax.{phase}.value", max_vwc)
                        
                        suggested_max_moisture = max_vwc * 0.95
                        self.dataStore.setDeep(f"Soil.MaxMoisture.{phase}.value", suggested_max_moisture)
                        
                        # Save to growMediums
                        await self.save_to_grow_mediums(
                            phase=phase,
                            vwc_max=max_vwc,
                            vwc_min=None,
                            calibration_type="max"
                        )
                        
                        calibration_result = {
                            "phase": phase,
                            "vwc_max": max_vwc,
                            "suggested_max_moisture": suggested_max_moisture,
                            "irrigation_attempts": irrigation_count,
                            "readings": stable_readings
                        }
                        
                        await self.eventManager.emit(
                            "VWCCalibrationComplete",
                            calibration_result,
                            haEvent=True
                        )
                        
                        break
                
                previous_max_vwc = stable_vwc
                await asyncio.sleep(60)
            
            if not calibration_complete and stable_readings:
                max_vwc = max(stable_readings)
                self.dataStore.setDeep(f"Soil.VWCMax.{phase}.value", max_vwc)
                suggested_max_moisture = max_vwc * 0.95
                self.dataStore.setDeep(f"Soil.MaxMoisture.{phase}.value", suggested_max_moisture)
                
                await self.eventManager.emit(
                    "LogForClient",
                    f"VWC Calibration: Estimated VWCMax={max_vwc:.1f}%",
                    haEvent=True
                )
                    
        except asyncio.CancelledError:
            await self.turn_off_drippers()
            await self.eventManager.emit(
                "LogForClient",
                "VWC Calibration: Cancelled",
                haEvent=True
            )
            raise
        except Exception as e:
            await self.turn_off_drippers()
            await self.eventManager.emit(
                "LogForClient",
                f"VWC Calibration: Error - {str(e)}",
                haEvent=True
            )

    async def _wait_for_vwc_stabilization(self, timeout=300, check_interval=10):
        """Wait until VWC reading stabilizes"""
        
        start_time = datetime.now()
        readings = []
        min_readings = 3
        
        while (datetime.now() - start_time).total_seconds() < timeout:
            current_vwc = self.dataStore.getDeep("Soil.moist_current")
            
            if current_vwc is None or current_vwc == 0:
                await asyncio.sleep(check_interval)
                continue
            
            readings.append(current_vwc)
            
            if len(readings) > min_readings:
                readings.pop(0)
            
            if len(readings) >= min_readings:
                avg_vwc = sum(readings) / len(readings)
                max_deviation = max(abs(r - avg_vwc) for r in readings)
                deviation_percent = (max_deviation / avg_vwc * 100) if avg_vwc > 0 else 100
                
                if deviation_percent < self.stability_tolerance:
                    await self.eventManager.emit(
                        "LogForClient",
                        f"VWC Calibration: Stable at {avg_vwc:.1f}%",
                        haEvent=True
                    )
                    return avg_vwc
            
            await asyncio.sleep(check_interval)
        
        return None

    async def start_vwc_min_calibration(self, phase="p1", dry_back_duration=3600):
        """Calibrate VWC minimum by monitoring dry-back"""
        
        if self._vwc_calibration_task is not None:
            await self.stop_vwc_calibration()
        
        self._vwc_calibration_task = asyncio.create_task(
            self._vwc_min_calibration_cycle(phase, dry_back_duration)
        )
        
        await self.eventManager.emit(
            "LogForClient",
            f"VWC Min Calibration: Starting for {phase} ({dry_back_duration}s)",
            haEvent=True
        )

    async def _vwc_min_calibration_cycle(self, phase, dry_back_duration):
        """Monitor dry-back to find VWC minimum"""
        
        start_time = datetime.now()
        min_vwc_observed = float('inf')
        readings = []
        
        try:
            while (datetime.now() - start_time).total_seconds() < dry_back_duration:
                current_vwc = self.dataStore.getDeep("Soil.moist_current")
                
                if current_vwc is not None and current_vwc > 0:
                    readings.append(current_vwc)
                    min_vwc_observed = min(min_vwc_observed, current_vwc)
                
                await asyncio.sleep(30)
            
            if min_vwc_observed < float('inf'):
                safe_min_vwc = min_vwc_observed * 1.1
                
                self.dataStore.setDeep(f"Soil.VWCMin.{phase}.value", safe_min_vwc)
                
                suggested_min_moisture = safe_min_vwc * 1.05
                self.dataStore.setDeep(f"Soil.MinMoisture.{phase}.value", suggested_min_moisture)
                
                await self.eventManager.emit(
                    "LogForClient",
                    f"VWC Min Calibration: COMPLETE! VWCMin={safe_min_vwc:.1f}%",
                    haEvent=True
                )
                
                result = {
                    "phase": phase,
                    "vwc_min": safe_min_vwc,
                    "vwc_min_observed": min_vwc_observed,
                    "suggested_min_moisture": suggested_min_moisture,
                    "readings_count": len(readings)
                }
                
                await self.eventManager.emit(
                    "VWCMinCalibrationComplete",
                    result,
                    haEvent=True
                )
            else:
                await self.eventManager.emit(
                    "LogForClient",
                    "VWC Min Calibration: No valid readings",
                    haEvent=True
                )
                
        except asyncio.CancelledError:
            await self.eventManager.emit(
                "LogForClient",
                "VWC Min Calibration: Cancelled",
                haEvent=True
            )
            raise

    async def stop_vwc_calibration(self):
        """Stop VWC calibration"""
        
        if self._vwc_calibration_task is not None:
            self._vwc_calibration_task.cancel()
            try:
                await self._vwc_calibration_task
            except asyncio.CancelledError:
                pass
            self._vwc_calibration_task = None
            
            await self.eventManager.emit(
                "LogForClient",
                "VWC Calibration: Stopped",
                haEvent=True
            )

    # ==================== HELPER METHODS ====================

    async def irrigate(self, active_drippers, duration=30):
        """Führt Bewässerung durch"""
        try:
            valid_types = ["dripper"]
            devices = active_drippers.get("devEntities", [])
            available_drippers = [dev for dev in devices if dev in valid_types]
            
            if not available_drippers:
                await self.eventManager.emit(
                    "LogForClient",
                    "CropSteering: No drippers available",
                    haEvent=True
                )
                return
            
            # Dripper einschalten
            for dev_id in available_drippers:
                await self.eventManager.emit("DripperAction", {
                    "Name": self.room,
                    "Action": "on",
                    "Device": dev_id
                })
            
            await self.eventManager.emit(
                "LogForClient",
                f"CropSteering: Irrigation started ({duration}s)",
                haEvent=True
            )
            
            await asyncio.sleep(duration)
            
            # Dripper ausschalten
            for dev_id in available_drippers:
                await self.eventManager.emit("DripperAction", {
                    "Name": self.room,
                    "Action": "off",
                    "Device": dev_id
                })
            
            await self.eventManager.emit(
                "LogForClient",
                "CropSteering: Irrigation completed",
                haEvent=True
            )
            
        except Exception as e:
            # Emergency shutoff
            if available_drippers:
                for dev_id in available_drippers:
                    try:
                        await self.eventManager.emit("DripperAction", {
                            "Name": self.room,
                            "Action": "off",
                            "Device": dev_id
                        })
                    except:
                        pass
            
            await self.eventManager.emit(
                "LogForClient",
                f"CropSteering: Irrigation error - {str(e)}",
                haEvent=True
            )

    async def turn_off_drippers(self):
        """Schaltet alle Dripper aus"""
        dripperDevices = self.dataStore.getDeep("capabilities.canPump")
        
        if not dripperDevices:
            return
        
        valid_types = ["dripper"]
        devices = dripperDevices.get("devEntities", [])
        available_drippers = [dev for dev in devices if dev in valid_types]
        
        for dev_id in available_drippers:
            try:
                await self.eventManager.emit("DripperAction", {
                    "Name": self.room,
                    "Action": "off",
                    "Device": dev_id
                })
            except Exception as e:
                _LOGGER.error(f"Error turning off dripper {dev_id}: {e}")

    def get_phase_settings(self, phase):
        """Hole alle Settings für eine Phase"""
        soil = self.dataStore.getDeep("Soil")
        return {
            "ShotIntervall": soil["ShotIntervall"][phase],
            "ShotSum": soil["ShotSum"][phase],
            "ECTarget": soil["ECTarget"][phase],
            "ECDryBack": soil["ECDryBack"][phase],
            "MoistureDryBack": soil["MoistureDryBack"][phase],
            "MinMoisture": soil["MinMoisture"][phase],
            "MaxMoisture": soil["MaxMoisture"][phase],
            "MaxEC": soil["MaxEC"][phase],
            "MinEC": soil["MinEC"][phase],
            "VWCMax": soil["VWCMax"][phase],
            "VWCMin": soil["VWCMin"][phase],
        }

    async def adjust_ec(self, increase=True):
        """Passt EC-Wert an"""
        currentEC = self.dataStore.getDeep("Soil.ec_current")
        targetEC = self.dataStore.getDeep("Soil.ec_target")
        step = 0.1
        
        if increase:
            newEC = targetEC + step
            direction = "increased"
        else:
            newEC = targetEC - step
            direction = "decreased"
        
        minEC = 0.8
        maxEC = 3.0
        newEC = max(minEC, min(maxEC, newEC))
        
        self.dataStore.setDeep("irrigation.targetEC", newEC)
        
        await self.eventManager.emit(
            "LogForClient",
            f"CropSteering: EC {direction} from {targetEC:.2f} to {newEC:.2f}",
            haEvent=True
        )
        
        await self.eventManager.emit("ECAction", {
            "Name": self.room,
            "TargetEC": newEC,
            "CurrentEC": currentEC
        })

    async def adjust_ec_to_target(self, target_ec, increase=True):
        """Passt EC zum Ziel-Wert an"""
        direction = "increase" if increase else "decrease"
        await self.eventManager.emit(
            "LogForClient",
            f"CropSteering: Adjusting EC {direction} towards {target_ec}",
            haEvent=True
        )

    async def log_phase_change(self, from_phase, to_phase, reason):
        """Loggt Phasenwechsel"""
        await self.eventManager.emit(
            "LogForClient",
            f"CropSteering: {from_phase} -> {to_phase} ({reason})",
            haEvent=True
        )
import logging
import asyncio
from datetime import datetime
from enum import Enum

_LOGGER = logging.getLogger(__name__)

class CSMode(Enum):
    DISABLED = "Disabled"
    CONFIG = "Config"
    AUTOMATIC = "Automatic"
    MANUAL_P0 = "Manual-p0"
    MANUAL_P1 = "Manual-p1"
    MANUAL_P2 = "Manual-p2"
    MANUAL_P3 = "Manual-p3"

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
        self.stability_tolerance = 0.5
        self.max_irrigation_attempts = 5
        self.blockCheckIntervall = 60

        # Single task for any CS operation
        self._main_task = None
        self._calibration_task = None
        
        # Event subscriptions
        self.eventManager.on("CropSteeringChanges", self.handle_mode_change)
        self.eventManager.on("VWCCalibrationCommand", self.handle_vwc_calibration_command)
 
    # ==================== ENTRY POINT ====================
    
    async def handle_mode_change(self, data):
        """SINGLE entry point for all mode changes"""
        _LOGGER.debug(f"CropSteering mode change: {data}")
        
        # Stop any existing operation first
        await self.stop_all_operations()
        
        # Parse mode
        cropMode = self.dataStore.getDeep("CropSteering.ActiveMode")
        mode = self._parse_mode(cropMode)
        
        if mode == CSMode.DISABLED or mode == CSMode.CONFIG:
            _LOGGER.info(f"{self.room} - CropSteering {mode.value}")
            return
        
        # Get sensor data
        sensor_data = await self._get_sensor_averages()
        if not sensor_data:
            await self._log_missing_sensors()
            return
        
        # Update current values
        self.dataStore.setDeep("CropSteering.vwc_current", sensor_data['vwc'])
        self.dataStore.setDeep("CropSteering.ec_current", sensor_data['ec'])
        
        # Get configuration
        config = await self._get_configuration(mode)
        if not config:
            return
        
        # Log start
        await self._log_mode_start(mode, config, sensor_data)
        
        # Start appropriate mode
        if mode == CSMode.AUTOMATIC:
            self._main_task = asyncio.create_task(
                self._automatic_cycle()
            )
        elif mode.value.startswith("Manual"):
            phase = mode.value.split("-")[1]  # Extract "p0", "p1", etc.
            self._main_task = asyncio.create_task(
                self._manual_cycle(phase)
            )

    async def handle_stop(self, event=None):
        """Stop handler for external stop events"""
        await self.stop_all_operations()

    # ==================== MODE PARSING ====================
    
    def _parse_mode(self, cropMode: str) -> CSMode:
        """Parse mode string to enum"""
        if "Automatic" in cropMode:
            return CSMode.AUTOMATIC
        elif "Disabled" in cropMode:
            return CSMode.DISABLED
        elif "Config" in cropMode:
            return CSMode.CONFIG
        elif "Manual" in cropMode:
            for phase in ["p0", "p1", "p2", "p3"]:
                if phase in cropMode:
                    return CSMode[f"MANUAL_{phase.upper()}"]
            return CSMode.MANUAL_P0  # Default
        return CSMode.DISABLED

    # ==================== SENSOR DATA ====================
    
    async def _get_sensor_averagesNew(self):
        """Get averaged sensor data from dataStore moisture + ec arrays"""

        vwc_values = []
        ec_values = []

        # Moisture
        moistures = self.dataStore.getDeep("workData.moisture") or []
        for item in moistures:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            vwc_values.append(val)

        # EC
        ecs = self.dataStore.getDeep("workData.ec") or []
        for item in ecs:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                val = float(raw)
            except ValueError:
                continue
            ec_values.append(val)

        if not vwc_values and not ec_values:
            return None

        result = {}

        if vwc_values:
            result['vwc'] = sum(vwc_values) / len(vwc_values)

        if ec_values:
            result['ec'] = sum(ec_values) / len(ec_values)

        return result if result else None

    
    async def _get_sensor_averages(self):
        """Get averaged sensor data"""
        growMediums = self.dataStore.get("growMediums")
        multiMediumCtrl = self.dataStore.getDeep("controlOptions.multiMediumControl")
        
        if not growMediums:
            return None
        
        if multiMediumCtrl == True:
            pass
        
        vwc_values = []
        ec_values = []
        
        for medium in growMediums:
            sensors = medium.get('sensor_readings', {})

            for sensor_id, sensor in sensors.items():
                sensor_type = sensor["sensor_type"]
                raw_value = sensor["value"]
                if raw_value is None:
                    continue

                try:
                    val = float(raw_value)
                except ValueError:
                    continue

                if sensor_type == "moisture":
                    vwc_values.append(val)

                elif sensor_type == "ec":
                    ec_values.append(val)
                

        if not vwc_values or not ec_values:
            return None
        
        return {
            'vwc': sum(vwc_values) / len(vwc_values),
            'ec': sum(ec_values) / len(ec_values)
        }

    # ==================== CONFIGURATION ====================
    
    async def _get_configuration(self, mode: CSMode):
        """Get configuration for mode"""
        config = {
            'mode': mode,
            'drippers': self._get_drippers(),
            'plant_phase': self.dataStore.getDeep("isPlantDay.plantPhase"),
            'generative_week': self.dataStore.getDeep("isPlantDay.generativeWeek"),
        }
        
        if not config['drippers']:
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "INVALID PUMPS",
                    "message": "No valid dripper devices found"
                },
                haEvent=True
            )
            return None
        
        if mode == CSMode.AUTOMATIC:
            # Will use phase settings dynamically
            pass
        elif mode.value.startswith("Manual"):
            phase = mode.value.split("-")[1]
            config['phase_settings'] = self._get_phase_settings(phase)
        
        return config

    def _get_drippers(self):
        """Get valid dripper devices"""
        dripperDevices = self.dataStore.getDeep("capabilities.canPump")
        if not dripperDevices:
            return []
        
        devices = dripperDevices.get("devEntities", [])
        valid_keywords = ["dripper", "pump"]
        
        return [
            dev for dev in devices
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]

    def _get_phase_settings(self, phase):
        """Get settings for specific phase"""
        cs = self.dataStore.getDeep("CropSteering")
        return {
            "ShotIntervall": cs["ShotIntervall"][phase],
            "ShotDuration": cs["ShotDuration"][phase],
            "ShotSum": cs["ShotSum"][phase],
            
            "MoistureDryBack": cs["MoistureDryBack"][phase],
            "ECDryBack": cs["ECDryBack"][phase],
            
            "ECTarget": cs["ECTarget"][phase],
            "MaxEC": cs["MaxEC"][phase],
            "MinEC": cs["MinEC"][phase],
            
            "VWCTarget": cs["VWCTarget"][phase],
            "VWCMax": cs["VWCMax"][phase],
            "VWCMin": cs["VWCMin"][phase],
        }

    # ==================== AUTOMATIC MODE ====================
    
    async def _automatic_cycle(self):
        """Automatic sensor-based cycle"""
        try:
            self.dataStore.setDeep("CropSteering.CropPhase", "p0")
            _LOGGER.info(f"{self.room} - Automatic CS cycle started")
            
            while True:
                current_phase = self.dataStore.getDeep("CropSteering.CropPhase")
                settings = self._get_phase_settings(current_phase)
                
                vwc = float(self.dataStore.getDeep("CropSteering.vwc_current"))
                ec = int(float(self.dataStore.getDeep("CropSteering.ec_current")))
                is_light_on = self.dataStore.getDeep("isPlantDay.islightON")
                
                if vwc is None:
                    await asyncio.sleep(self.blockCheckIntervall)
                    continue
                
                # Phase logic
                if current_phase == "p0":
                    await self._handle_phase_p0_auto(vwc, settings)
                elif current_phase == "p1":
                    await self._handle_phase_p1_auto(vwc, settings)
                elif current_phase == "p2":
                    await self._handle_phase_p2_auto(vwc, is_light_on, settings)
                elif current_phase == "p3":
                    await self._handle_phase_p3_auto(vwc, is_light_on, settings)
                
                await asyncio.sleep(self.blockCheckIntervall)
                
        except asyncio.CancelledError:
            await self._emergency_stop()
            raise
        except Exception as e:
            _LOGGER.error(f"Automatic cycle error: {e}", exc_info=True)
            await self._emergency_stop()

    async def _handle_phase_p0_auto(self, vwc, settings):
        """P0: Monitoring phase"""
        if vwc < float(settings["VWCMin"]["value"]):
            self.dataStore.setDeep("CropSteering.CropPhase", "p1")
            self.dataStore.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change("p0", "p1", f"Plant thirsty Current:{vwc} Target:{float(settings["VWCMax"]["value"])}%")

    async def _handle_phase_p1_auto(self, vwc, settings):
        """P1: Saturation phase"""
        if vwc < float(settings["VWCMax"]["value"]):
            await self._irrigate(duration=30)
        else:
            self.dataStore.setDeep("CropSteering.CropPhase", "p2")
            self.dataStore.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change("p1", "p2", f"Block full Current:{vwc} Target:{float(settings["VWCMax"]["value"])}%")

    async def _handle_phase_p2_auto(self, vwc, is_light_on, settings):
        """P2: Maintenance phase"""
        if is_light_on:
            hold_threshold = float(settings["VWCMax"]["value"]) * 0.95
            if vwc < hold_threshold:
                await self._irrigate(duration=15)
        else:
            self.dataStore.setDeep("CropSteering.CropPhase", "p3")
            self.dataStore.setDeep("CropSteering.phaseStartTime", datetime.now())
            self.dataStore.setDeep("CropSteering.startNightMoisture", vwc)
            await self._log_phase_change("p2", "p3", f"Night DryBack Current:{vwc} Target:{float(settings["VWCMax"]["value"])}%")

    async def _handle_phase_p3_auto(self, vwc, is_light_on, settings):
        """P3: Night dry-back phase"""
        if not is_light_on:
            start_night = self.dataStore.getDeep("CropSteering.startNightMoisture")
            target_dryback = float(settings["MoistureDryBack"]["value"])
            current_dryback = ((start_night - vwc) / start_night) * 100 if start_night else 0
            
            # EC adjustment based on dryback
            if current_dryback < target_dryback * 0.8:
                await self._adjust_ec_to_target(settings["ECTarget"]["value"], increase=True)
            elif current_dryback > target_dryback * 1.2:
                await self._adjust_ec_to_target(settings["ECTarget"]["value"], increase=False)
            
            # Emergency irrigation
            min_allowed = float(settings["VWCMax"]["value"]) * 0.9
            if vwc < min_allowed:
                await self._irrigate(duration=15)
                await self.eventManager.emit(
                    "LogForClient",
                    "CropSteering: Emergency irrigation in P3",
                    haEvent=True
                )
        else:
            self.dataStore.setDeep("CropSteering.CropPhase", "p0")
            await self._log_phase_change("p3", "p0", "Day starts {vwc} % DryBackTarget {target_dryback} CurrentDryBack:}{current_dryback}")

    # ==================== MANUAL MODE ====================
    
    async def _manual_cycle(self, phase):
        """Manual time-based cycle"""
        try:
            settings = self._get_phase_settings(phase)
            
            shot_duration = settings["ShotDuration"]["value"]
            shot_interval = settings["ShotIntervall"]["value"]
            shot_count = settings["ShotSum"]["value"]
            
            if shot_interval <= 0 or int(float(shot_count)) <= 0:
                await self.eventManager.emit(
                    "LogForClient",
                    f"CropSteering: Invalid settings for {phase}",
                    haEvent=True
                )
                return
            
            self.dataStore.setDeep("CropSteering.shotCounter", 0)
            self.dataStore.setDeep("CropSteering.phaseStartTime", datetime.now())
            
            _LOGGER.info(f"{self.room} - Manual {phase}: {shot_count} shots every {shot_interval}min")
            
            while True:
                vwc = int(float(self.dataStore.getDeep("CropSteering.vwc_current")))
                ec = int(float(self.dataStore.getDeep("CropSteering.ec_current")))
                shot_counter = int(float(self.dataStore.getDeep("CropSteering.shotCounter")))
                
                # EC management
                ec_target = int(float(settings["ECTarget"]["value"]))
                if ec_target > 0 and ec:
                    if ec < int(float(settings["MinEC"]["value"])):
                        await self._adjust_ec_to_target(ec_target, increase=True)
                    elif ec > settings["MaxEC"]["value"]:
                        await self._adjust_ec_to_target(ec_target, increase=False)
                
                # Emergency irrigation
                if vwc and vwc < int(float(settings["VWCMin"]["value"])) * 0.9:
                    await self._irrigate(duration=shot_duration)
                    await self.eventManager.emit(
                        "LogForClient",{
                            "Name":self.room,
                            "Type":"Emergency irrigation",
                            "Message":f"CropSteering {phase}: Emergency irrigation",
                        },
                        haEvent=True
                    )
                
                # Scheduled irrigation
                last_irrigation = self.dataStore.getDeep("CropSteering.lastIrrigationTime")
                now = datetime.now()
                
                should_irrigate = (
                    last_irrigation is None or
                    (now - last_irrigation).total_seconds() / 60 >= shot_interval
                )
                
                if should_irrigate and shot_counter < shot_count:
                    await self._irrigate(duration=shot_duration)
                    shot_counter += 1
                    self.dataStore.setDeep("CropSteering.shotCounter", shot_counter)
                    self.dataStore.setDeep("CropSteering.lastIrrigationTime", now)
                    
                    await self.eventManager.emit(
                        "LogForClient",
                        f"CropSteering {phase}: Shot {shot_counter}/{shot_count}",
                        haEvent=True
                    )
                
                # Reset counter after full cycle
                if shot_counter >= shot_count:
                    phase_start = self.dataStore.getDeep("CropSteering.phaseStartTime")
                    elapsed = (now - phase_start).total_seconds() / 60
                    
                    if elapsed >= shot_interval:
                        self.dataStore.setDeep("CropSteering.shotCounter", 0)
                        self.dataStore.setDeep("CropSteering.phaseStartTime", now)
                        await self.eventManager.emit(
                            "LogForClient",
                            f"CropSteering {phase}: New cycle started",
                            haEvent=True
                        )
                
                await asyncio.sleep(10)
                
        except asyncio.CancelledError:
            await self._emergency_stop()
            raise
        except Exception as e:
            _LOGGER.error(f"Manual cycle error: {e}", exc_info=True)
            await self._emergency_stop()

    # ==================== IRRIGATION ====================
    
    async def _irrigate(self, duration=30):
        """Execute irrigation"""
        drippers = self._get_drippers()
        
        if not drippers:
            return
        
        try:
            # Turn on
            for dev_id in drippers:
                from ...OGBDataClasses.OGBPublications import OGBHydroAction
                action = OGBHydroAction(
                    Name=self.room,
                    Action="on",
                    Device=dev_id,
                    Cycle=False
                )
                await self.eventManager.emit("PumpAction", action)
            
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CS-Irrigation",
                    "Message": f"Irrigation started ({duration}s)"
                },
                haEvent=True
            )
            
            await asyncio.sleep(duration)
            
            # Turn off
            for dev_id in drippers:
                action = OGBHydroAction(
                    Name=self.room,
                    Action="off",
                    Device=dev_id,
                    Cycle=False
                )
                await self.eventManager.emit("PumpAction", action)
            
        except Exception as e:
            _LOGGER.error(f"Irrigation error: {e}")
            await self._emergency_stop()

    # ==================== STOP & CLEANUP ====================
    
    async def stop_all_operations(self):
        """Stop all running operations"""
        tasks_to_cancel = []
        
        if self._main_task and not self._main_task.done():
            tasks_to_cancel.append(self._main_task)
        
        if self._calibration_task and not self._calibration_task.done():
            tasks_to_cancel.append(self._calibration_task)
        
        for task in tasks_to_cancel:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._main_task = None
        self._calibration_task = None
        
        await self._turn_off_all_drippers()
        _LOGGER.info(f"{self.room} - All CS operations stopped")

    async def _emergency_stop(self):
        """Emergency stop all operations"""
        await self._turn_off_all_drippers()
        await self.eventManager.emit(
            "LogForClient",
            f"{self.room}: Emergency stop activated",
            haEvent=True
        )

    async def _turn_off_all_drippers(self):
        """Turn off all drippers"""
        drippers = self._get_drippers()
        
        for dev_id in drippers:
            try:
                from ...OGBDataClasses.OGBPublications import OGBHydroAction
                action = OGBHydroAction(
                    Name=self.room,
                    Action="off",
                    Device=dev_id,
                    Cycle=False
                )
                await self.eventManager.emit("PumpAction", action)
            except Exception as e:
                _LOGGER.error(f"Error turning off {dev_id}: {e}")

    # ==================== LOGGING ====================
    
    async def _log_mode_start(self, mode, config, sensor_data):
        """Log mode start"""
        await self.eventManager.emit(
            "LogForClient", {
                "Name": self.room,
                "Type": "csaction",
                "Message": f"CropSteering {mode.value} started",
                "VWC": sensor_data['vwc'],
                "EC": sensor_data['ec'],
                "PlantPhase": config['plant_phase'],
                "Week": config['generative_week']
            },
            haEvent=True
        )

    async def _log_phase_change(self, from_phase, to_phase, reason):
        """Log phase change"""
        await self.eventManager.emit(
            "LogForClient",
            {
                "Name":self.room,
                "Type":"CSLOG",
                "Message":f"CropSteering: {from_phase} -> {to_phase} ({reason})",
            },
            haEvent=True
        )

    async def _log_missing_sensors(self):
        """Log missing sensor data"""
        logging.error(f"{self.room} Message: CropSteering: Waiting for sensor data (VWC/EC missing)")
        await self.eventManager.emit(
            "LogForClient", {
                "Name": self.room,
                "type": "missingno",
                "Message": "CropSteering: Waiting for sensor data (VWC/EC missing)"
            },
            haEvent=True
        )

    # ==================== EC ADJUSTMENT ====================
    
    async def _adjust_ec_to_target(self, target_ec, increase=True):
        """Adjust EC towards target"""
        direction = "increase" if increase else "decrease"
        await self.eventManager.emit(
            "LogForClient",
            f"CropSteering: Adjusting EC {direction} towards {target_ec}",
            haEvent=True
        )

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
                        
                        self.dataStore.setDeep(f"CropSteering.VWCMax.{phase}.value", max_vwc)
                        
                        suggested_max_moisture = max_vwc * 0.95
                        self.dataStore.setDeep(f"CropSteering.VWCMax.{phase}.value", suggested_max_moisture)
                        
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
                self.dataStore.setDeep(f"CropSteering.VWCMax.{phase}.value", max_vwc)
                suggested_max_moisture = max_vwc * 0.95
                self.dataStore.setDeep(f"CropSteering.VWCMax.{phase}.value", suggested_max_moisture)
                
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
            current_vwc = self.dataStore.getDeep("CropSteering.vwc_current")
            
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
                current_vwc = self.dataStore.getDeep("CropSteering.vwc_current")
                
                if current_vwc is not None and current_vwc > 0:
                    readings.append(current_vwc)
                    min_vwc_observed = min(min_vwc_observed, current_vwc)
                
                await asyncio.sleep(30)
            
            if min_vwc_observed < float('inf'):
                safe_min_vwc = min_vwc_observed * 1.1
                
                self.dataStore.setDeep(f"CropSteering.VWCMin.{phase}.value", safe_min_vwc)
                
                suggested_min_moisture = safe_min_vwc * 1.05
                self.dataStore.setDeep(f"CropSteering.VWCMax.{phase}.value", suggested_min_moisture)
                
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

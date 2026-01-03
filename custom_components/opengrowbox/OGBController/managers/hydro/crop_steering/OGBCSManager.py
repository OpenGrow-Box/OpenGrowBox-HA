import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ....data.OGBDataClasses.OGBPublications import OGBWaterAction, OGBWaterPublication
# OGBHydroAction temporarily unavailable, will use dict fallback

from .OGBAdvancedSensor import OGBAdvancedSensor
from .OGBCSCalibrationManager import OGBCSCalibrationManager
from .OGBCSConfigurationManager import CSMode, OGBCSConfigurationManager
from .OGBCSIrrigationManager import OGBCSIrrigationManager
from .OGBCSPhaseManager import OGBCSPhaseManager

_LOGGER = logging.getLogger(__name__)


class OGBCSManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Crop Steering Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager
        self.isInitialized = False

        # Advanced sensor processing for TDR/VWC/EC calculations
        self.advanced_sensor = OGBAdvancedSensor()
        self.medium_type = "rockwool"  # Default, will be synced from medium manager

        # Initialize specialized managers
        self.config_manager = OGBCSConfigurationManager(
            data_store=dataStore,
            room=room
        )
        
        self.phase_manager = OGBCSPhaseManager(
            data_store=dataStore,
            room=room,
            event_manager=eventManager
        )
        
        self.irrigation_manager = OGBCSIrrigationManager(
            room=room,
            data_store=dataStore,
            event_manager=eventManager,
            hass=hass
        )
        
        # Calibration Manager - handles VWC max/min calibration
        self.calibration_manager = OGBCSCalibrationManager(
            room=room,
            data_store=dataStore,
            event_manager=eventManager,
            advanced_sensor=self.advanced_sensor
        )

        # Default values for missing attributes (normally set by modular managers)
        # Medium-specific preset adjustments for CropSteering
        self._medium_adjustments = {
            "rockwool": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
            "coco": {"vwc_offset": 3, "ec_offset": -0.1, "drainage_factor": 0.9},
            "soil": {"vwc_offset": -5, "ec_offset": 0.2, "drainage_factor": 0.7},
            "perlite": {"vwc_offset": -8, "ec_offset": 0.1, "drainage_factor": 1.2},
            "aero": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
            "water": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
            "custom": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
        }
        self.blockCheckIntervall = 300  # 5 minutes
        self.max_irrigation_attempts = 5
        self.stability_tolerance = 0.1

        # Single task for any CS operation
        self._main_task = None
        self._calibration_task = None

        # Event subscriptions
        self.event_manager.on("CropSteeringChanges", self.handle_mode_change)
        self.event_manager.on(
            "VWCCalibrationCommand", self.handle_vwc_calibration_command
        )
        self.event_manager.on("MediumChange", self._on_medium_change)

    # ==================== MEDIUM SYNC ====================

    async def _sync_medium_type(self):
        """Sync medium type from medium manager or dataStore"""
        try:
            # Try to get from growMediums in dataStore
            grow_mediums = self.data_store.get("growMediums") or []
            if grow_mediums and len(grow_mediums) > 0:
                first_medium = grow_mediums[0]
                if hasattr(first_medium, "medium_type"):
                    self.medium_type = first_medium.medium_type.value
                elif isinstance(first_medium, dict) and "type" in first_medium:
                    self.medium_type = first_medium["type"]

            # Fallback to dataStore CropSteering settings
            stored_medium = self.data_store.getDeep("CropSteering.MediumType")
            if stored_medium:
                self.medium_type = stored_medium.lower()

        except Exception as e:
            _LOGGER.warning(f"{self.room} - Could not sync medium type: {e}")
            self.medium_type = "rockwool"

        _LOGGER.info(
            f"{self.room} - CropSteering using medium type: {self.medium_type}"
        )

    async def _on_medium_change(self, data):
        """Handle medium type changes from medium manager"""
        # Defensive: Handle both dict and string formats
        if isinstance(data, str):
            # Legacy format: just the medium type string
            new_medium = data.lower()
        elif isinstance(data, dict):
            # Proper format: dict with room and medium_type
            if data.get("room") != self.room:
                return
            new_medium = data.get("medium_type", "").lower()
        else:
            _LOGGER.warning(f"{self.room} - MediumChange event with invalid data type: {type(data)}")
            return

        if new_medium and new_medium != self.medium_type:
            old_medium = self.medium_type
            self.medium_type = new_medium
            self.data_store.setDeep("CropSteering.MediumType", new_medium)

            _LOGGER.info(
                f"{self.room} - CropSteering medium changed: {old_medium} → {new_medium}"
            )

            # Notify about recalibration
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Medium changed to {new_medium}. Sensor calibrations updated.",
                },
                haEvent=True,
            )

    # ==================== AUTOMATIC PRESETS ====================

    def _get_base_presets(self) -> Dict[str, Dict[str, Any]]:
        """
        Base presets for automatic mode (rockwool defaults).
        These are adjusted based on medium type.
        """
        return {
             "p0": {
                 # P0: Monitoring - Wait for Dryback Signal
                 "description": "Initial Monitoring Phase",
                 "VWCTarget": 58.0,
                 "VWCMin": 55.0,
                 "VWCMax": 65.0,
                 "ECTarget": 2.0,
                 "MinEC": 1.8,
                 "MaxEC": 2.2,
                 "trigger_condition": "vwc_below_min",
             },
             "p1": {
                 # P1: Saturation - Rapid saturation of the block
                 "description": "Saturation Phase",
                 "VWCTarget": 70.0,
                 "VWCMax": 68.0,
                 "VWCMin": 55.0,
                 "ECTarget": 1.8,
                 "MinEC": 1.6,
                 "MaxEC": 2.0,
                 "irrigation_duration": 45,
                 "max_cycles": 10,
                 "wait_between": 180,
                 "trigger_condition": "vwc_above_target",
             },
             "p2": {
                 # P2: Maintenance - Maintain level during light phase
                 "description": "Day Maintenance Phase",
                 "VWCTarget": 65.0,
                 "VWCMax": 68.0,
                 "VWCMin": 62.0,
                 "hold_percentage": 0.95,
                 "ECTarget": 2.0,
                 "MinEC": 1.8,
                 "MaxEC": 2.2,
                 "irrigation_duration": 20,
                 "irrigation_interval": 1800,  # 30 min between maintenance shots
                 "check_light": True,
                 "trigger_condition": "light_off",
             },
             "p3": {
                 # P3: Night Dryback - Controlled nightly dryback
                 "description": "Night Dryback Phase",
                 "VWCTarget": 60.0,
                 "VWCMax": 68.0,
                 "VWCMin": 52.0,  # Lower for night dryback
                 "target_dryback_percent": 10.0,
                 "min_dryback_percent": 8.0,
                 "max_dryback_percent": 12.0,
                 "emergency_threshold": 0.85,  # 85% of VWCMin = emergency
                 "ECTarget": 2.2,
                 "MinEC": 2.0,
                 "MaxEC": 2.5,
                 "ec_increase_step": 0.1,
                 "ec_decrease_step": 0.1,
                 "irrigation_duration": 15,
                 "irrigation_interval": 3600,  # 1 hour between P3 emergency shots
                 "max_emergency_shots": 2,  # Max 2 emergency irrigations per night
                 "trigger_condition": "light_on",
             },
        }

    def _get_automatic_presets(self) -> Dict[str, Dict[str, Any]]:
        """
        Get medium-adjusted automatic presets.
        Applies medium-specific adjustments to base presets.
        """
        base_presets = self._get_base_presets()

        # Get medium-specific adjustments with safe fallback
        default_adjustments = {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0}
        adjustments = self._medium_adjustments.get(
            self.medium_type, self._medium_adjustments.get("rockwool", default_adjustments)
        )

        vwc_offset = adjustments["vwc_offset"]
        ec_offset = adjustments["ec_offset"]
        drainage_factor = adjustments["drainage_factor"]

        adjusted_presets = {}
        for phase, preset in base_presets.items():
            adjusted_presets[phase] = preset.copy()

            # Adjust VWC targets based on medium
            for key in ["VWCTarget", "VWCMin", "VWCMax"]:
                if key in preset:
                    adjusted_presets[phase][key] = preset[key] + vwc_offset

            # Adjust EC targets based on medium
            for key in ["ECTarget", "MinEC", "MaxEC"]:
                if key in preset:
                    adjusted_presets[phase][key] = preset[key] + ec_offset

            # Adjust irrigation timing based on drainage
            if "irrigation_duration" in preset:
                adjusted_presets[phase]["irrigation_duration"] = int(
                    preset["irrigation_duration"] * drainage_factor
                )
            if "wait_between" in preset:
                adjusted_presets[phase]["wait_between"] = int(
                    preset["wait_between"] / drainage_factor
                )

        _LOGGER.debug(
            f"{self.room} - Presets adjusted for {self.medium_type}: vwc_offset={vwc_offset}, ec_offset={ec_offset}"
        )

        return adjusted_presets

    def _get_phase_growth_adjustments(self, plant_phase, generative_week):
        """
        Growth phase-specific adjustments

        Vegetative: More moisture, less dryback (generative)
        Generative: Less moisture, more dryback (vegetative)
        """
        adjustments = {"vwc_modifier": 0.0, "dryback_modifier": 0.0, "ec_modifier": 0.0}

        if plant_phase == "veg":
            # Vegetative Phase: Promote growth
            adjustments["vwc_modifier"] = 2.0  # +2% moisture
            adjustments["dryback_modifier"] = -2.0  # -2% dryback (less stress)
            adjustments["ec_modifier"] = -0.1  # Slightly lower EC

        elif plant_phase == "gen":
            # Flowering Phase: Promote flowering
            if generative_week <= 3:
                # Early Flower: Transition
                adjustments["vwc_modifier"] = 1.0
                adjustments["dryback_modifier"] = -1.0
                adjustments["ec_modifier"] = 0.05
            elif generative_week <= 5:
                # Mid Flower: Enhanced generative
                adjustments["vwc_modifier"] = -2.0  # -2% moisture
                adjustments["dryback_modifier"] = 2.0  # +2% dryback (more stress)
                adjustments["ec_modifier"] = 0.2  # Higher EC
            elif generative_week <= 7:
                # Mid Flower: Enhanced generative
                adjustments["vwc_modifier"] = 2.0  # -2% moisture
                adjustments["dryback_modifier"] = -2.0  # +2% dryback (more stress)
                adjustments["ec_modifier"] = 0.1  # Higher EC
            else:
                # Late Flower: Maximum generative
                adjustments["vwc_modifier"] = -3.0  # -3% moisture
                adjustments["dryback_modifier"] = 3.0  # +3% dryback
                adjustments["ec_modifier"] = 0.3  # Even higher EC

        return adjustments

    def _get_adjusted_preset(self, phase, plant_phase, generative_week):
        """
        Get preset and apply growth phase adjustments
        """
        base_preset = self._get_automatic_presets()[phase].copy()
        adjustments = self._get_phase_growth_adjustments(plant_phase, generative_week)

        # Wende Anpassungen an
        if "VWCTarget" in base_preset:
            base_preset["VWCTarget"] += adjustments["vwc_modifier"]
        if "VWCMax" in base_preset:
            base_preset["VWCMax"] += adjustments["vwc_modifier"]
        if "VWCMin" in base_preset:
            base_preset["VWCMin"] += adjustments["vwc_modifier"]

        if "target_dryback_percent" in base_preset:
            base_preset["target_dryback_percent"] += adjustments["dryback_modifier"]

        if "ECTarget" in base_preset:
            base_preset["ECTarget"] += adjustments["ec_modifier"]

        return base_preset

    # ==================== ENTRY POINT ====================
    async def handle_mode_change(self, data):
        """SINGLE entry point for all mode changes"""
        _LOGGER.debug(f"CropSteering mode change: {data}")

        # Stop any existing operation first
        await self.stop_all_operations()

        # Parse mode
        multimediumCtrl = self.data_store.getDeep("controlOptions.multiMediumControl")

        if multimediumCtrl == False:
            _LOGGER.error(
                f"{self.room} - CropSteering Single Medium Control No working Switch the button only multi control working right now"
            )
            return

        cropMode = self.data_store.getDeep("CropSteering.ActiveMode")
        mode = self._parse_mode(cropMode)

        if mode == CSMode.DISABLED or mode == CSMode.CONFIG:
            _LOGGER.debug(f"{self.room} - CropSteering {mode.value}")
            return

        # Get sensor data
        sensor_data = await self._get_sensor_averages()
        if not sensor_data:
            await self._log_missing_sensors()
            return

        # Update current values
        self.data_store.setDeep("CropSteering.vwc_current", sensor_data["vwc"])
        self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])

        # Get configuration
        config = await self._get_configuration(mode)
        if not config:
            return

        # Log start
        await self._log_mode_start(mode, config, sensor_data)

        # Start appropriate mode
        if mode == CSMode.AUTOMATIC:
            self._main_task = asyncio.create_task(self._automatic_cycle())
        elif mode.value.startswith("Manual"):
            phase = mode.value.split("-")[1]  # Extract "p0", "p1", etc.
            self._main_task = asyncio.create_task(self._manual_cycle(phase))

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

    async def _get_sensor_averages(self) -> Optional[Dict[str, Any]]:
        """
        Get averaged sensor data with advanced processing.

        Uses TDR-style calibration with:
        - Medium-specific VWC polynomial calibration
        - Temperature-normalized EC
        - Pore water EC calculation (Hilhorst/mass-balance hybrid)
        - Validation and anomaly detection
        """
        vwc_values = []
        bulk_ec_values = []
        temp_values = []

        # Sync medium type if not initialized
        if not self.isInitialized:
            await self._sync_medium_type()
            self.isInitialized = True

        # Moisture/VWC sensors
        moistures = self.data_store.getDeep("workData.moisture") or []
        for item in moistures:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                raw_val = float(raw)
                # Apply medium-specific VWC calibration
                if self.advanced_sensor:
                    calibrated_vwc = self.advanced_sensor.calculate_vwc(
                        raw_val, self.medium_type
                    )
                else:
                    calibrated_vwc = raw_val  # Fallback to raw value
                vwc_values.append(calibrated_vwc)
            except (ValueError, TypeError) as e:
                _LOGGER.debug(f"{self.room} - VWC conversion error: {e}")
                continue

        # EC sensors
        ecs = self.data_store.getDeep("workData.ec") or []
        for item in ecs:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                bulk_ec_values.append(float(raw))
            except (ValueError, TypeError):
                continue

        # Temperature sensors (for EC normalization)
        temps = self.data_store.getDeep("workData.temperature") or []
        for item in temps:
            raw = item.get("value")
            if raw is None:
                continue
            try:
                temp_values.append(float(raw))
            except (ValueError, TypeError):
                continue

        if not vwc_values and not bulk_ec_values:
            return None

        # Calculate averages
        avg_vwc = sum(vwc_values) / len(vwc_values) if vwc_values else 0
        avg_bulk_ec = sum(bulk_ec_values) / len(bulk_ec_values) if bulk_ec_values else 0
        avg_temp = (
            sum(temp_values) / len(temp_values) if temp_values else 25.0
        )  # Default 25C

        # Calculate pore water EC using hybrid model
        pore_ec = 0
        validation = None
        if self.advanced_sensor and avg_bulk_ec > 0 and avg_vwc > 0:
            pore_ec = self.advanced_sensor.calculate_pore_ec(
                avg_bulk_ec, avg_vwc, avg_temp, self.medium_type
            )

            # Validate readings
            validation = self.advanced_sensor.validate_readings(
                avg_vwc, avg_bulk_ec, pore_ec, avg_temp, self.medium_type
            )
        else:
            # Create a mock validation object when sensor is disabled
            from types import SimpleNamespace
            validation = SimpleNamespace(issues=[], warnings=[], recommendations=[])

        if validation.issues:
            _LOGGER.warning(
                f"{self.room} - Sensor validation issues: {validation.issues}"
            )
            # Apply corrections if available
            if "vwc" in validation.corrected_values:
                avg_vwc = validation.corrected_values["vwc"]
            if "pore_ec" in validation.corrected_values:
                pore_ec = validation.corrected_values["pore_ec"]

        result = {
            "vwc": avg_vwc,
            "ec": avg_bulk_ec,  # Keep 'ec' key for backward compatibility
            "bulk_ec": avg_bulk_ec,
            "pore_ec": pore_ec,
            "temperature": avg_temp,
            "medium_type": self.medium_type,
            "validation_valid": validation.is_valid,
            "sensor_count": {
                "vwc": len(vwc_values),
                "ec": len(bulk_ec_values),
                "temp": len(temp_values),
            },
        }

        _LOGGER.debug(
            f"{self.room} - Sensor data: VWC={avg_vwc:.1f}%, EC={avg_bulk_ec:.2f}/{pore_ec:.2f} (bulk/pore), T={avg_temp:.1f}C [{self.medium_type}]"
        )

        return result

    # ==================== CONFIGURATION ====================

    async def _get_configuration(self, mode: CSMode):
        """Get configuration for mode"""
        config = {
            "mode": mode,
            "drippers": self._get_drippers(),
            "plant_phase": self.data_store.getDeep("isPlantDay.plantPhase"),
            "generative_week": self.data_store.getDeep("isPlantDay.generativeWeek"),
        }

        if not config["drippers"]:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "INVALID PUMPS",
                    "message": "No valid dripper devices found",
                },
                haEvent=True,
            )
            return None

        # Manual Mode uses user settings
        if mode.value.startswith("Manual"):
            phase = mode.value.split("-")[1]
            config["phase_settings"] = self._get_manual_phase_settings(phase)

        return config

    def _get_drippers(self):
        """Get valid dripper devices"""
        dripperDevices = self.data_store.getDeep("capabilities.canPump")
        if not dripperDevices:
            return []

        devices = dripperDevices.get("devEntities", [])
        valid_keywords = ["dripper"]

        return [
            dev
            for dev in devices
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]

    def _get_manual_phase_settings(self, phase):
        """Get USER settings für Manual Mode"""
        cs = self.data_store.getDeep("CropSteering")
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

    async def _determine_initial_phase(self):
        """
        Intelligente Bestimmung der Start-Phase basierend auf:
        - Aktueller VWC
        - Licht-Status
        - Kalibrierte/Preset Werte
        """
        vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
        is_light_on = self.data_store.getDeep("isPlantDay.islightON")

        plant_phase = self.data_store.getDeep("isPlantDay.plantPhase")
        gen_week = self.data_store.getDeep("isPlantDay.generativeWeek")

        # Get adjusted presets
        p0_preset = self._get_adjusted_preset("p0", plant_phase, gen_week)
        p2_preset = self._get_adjusted_preset("p2", plant_phase, gen_week)

        # Decision logic
        if vwc == 0:
            return "p0"  # No data, start in monitoring

        if is_light_on:
            # Day time
            if vwc >= p2_preset["VWCMax"] * 0.90:
                # Block is relatively full -> P2 Maintenance
                return "p2"
            elif vwc < p0_preset["VWCMin"]:
                # Block is dry -> P1 Saturation
                return "p1"
            else:
                # Somewhere in between -> P0 Monitoring
                return "p0"
        else:
            # Night time
            if vwc >= p2_preset["VWCMax"] * 0.90:
                # Block full, night -> P3 Dryback
                # Set startNightMoisture for dryback calculation
                self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
                return "p3"
            elif vwc < p0_preset["VWCMin"]:
                # Block too dry even at night -> P1 Emergency Saturation
                return "p1"
            else:
                # Normal at night -> P3 Dryback
                self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
                return "p3"

    async def _automatic_cycle(self):
        """Automatic sensor-based cycle mit festen Presets"""
        try:
            plant_phase = self.data_store.getDeep("isPlantDay.plantPhase")
            generative_week = self.data_store.getDeep("isPlantDay.generativeWeek")

            # IMPORTANT: Determine start phase based on current conditions
            initial_phase = await self._determine_initial_phase()
            self.data_store.setDeep("CropSteering.CropPhase", initial_phase)

            _LOGGER.warning(
                f"{self.room} - Automatic CS cycle started in phase {initial_phase}"
            )

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Started in {initial_phase} - {plant_phase} week {generative_week}",
                },
                haEvent=True,
            )

            while True:
                # === CRITICAL: Read sensor data NEWLY! ===
                sensor_data = await self._get_sensor_averages()
                if sensor_data:
                    self.data_store.setDeep(
                        "CropSteering.vwc_current", sensor_data["vwc"]
                    )
                    self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])

                current_phase = self.data_store.getDeep("CropSteering.CropPhase")

                # Get adjusted presets based on growth phase
                preset = self._get_adjusted_preset(
                    current_phase, plant_phase, generative_week
                )

                vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                ec = float(self.data_store.getDeep("CropSteering.ec_current") or 0)
                is_light_on = self.data_store.getDeep("isPlantDay.islightON")

                if vwc == 0:
                    await asyncio.sleep(self.blockCheckIntervall)
                    continue

                # Emit sensor update for AI learning
                if sensor_data:
                    # Get environmental data for AI context
                    env_data = self.data_store.getDeep("workData") or {}
                    await self.event_manager.emit(
                        "CSSensorUpdate",
                        {
                            "room": self.room,
                            "vwc": sensor_data.get("vwc"),
                            "vwc_raw": sensor_data.get("vwc"),
                            "ec": sensor_data.get("ec"),
                            "ec_raw": sensor_data.get("bulk_ec"),
                            "pore_ec": sensor_data.get("pore_ec"),
                            "temperature": sensor_data.get("temperature"),
                            "soil_temp": sensor_data.get("temperature"),
                            "vwc_min": preset.get("VWCMin"),
                            "vwc_max": preset.get("VWCMax"),
                            "ec_target": preset.get("ECTarget"),
                            "air_temp": self._get_env_avg(env_data, "temperature"),
                            "humidity": self._get_env_avg(env_data, "humidity"),
                            "vpd": self._get_env_avg(env_data, "vpd"),
                            "light_intensity": self._get_env_avg(env_data, "lightPPFD"),
                            "light_status": "on" if is_light_on else "off",
                        },
                    )

                # Phase logic with presets
                if current_phase == "p0":
                    await self._handle_phase_p0_auto(vwc, ec, preset)
                elif current_phase == "p1":
                    await self._handle_phase_p1_auto(vwc, ec, preset)
                elif current_phase == "p2":
                    await self._handle_phase_p2_auto(vwc, ec, is_light_on, preset)
                elif current_phase == "p3":
                    await self._handle_phase_p3_auto(vwc, ec, is_light_on, preset)

                await asyncio.sleep(self.blockCheckIntervall)

        except asyncio.CancelledError:
            await self._emergency_stop()
            raise
        except Exception as e:
            _LOGGER.error(f"Automatic cycle error: {e}", exc_info=True)
            await self._emergency_stop()

    async def _handle_phase_p0_auto(self, vwc, ec, preset):
        """P0: Monitoring phase - Wait for Dryback Signal"""
        # P0 is simple: Wait until VWC falls below minimum
        if vwc < preset["VWCMin"]:
            _LOGGER.info(
                f"{self.room} - P0: VWC {vwc:.1f}% < Min {preset['VWCMin']:.1f}% → Switching to P1"
            )
            self.data_store.setDeep("CropSteering.CropPhase", "p1")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change(
                "p0",
                "p1",
                f"Dryback detected - VWC: {vwc:.1f}% < Min: {preset['VWCMin']:.1f}%",
            )
        else:
            # Debug: Show current VWC in P0
            _LOGGER.debug(
                f"{self.room} - P0 monitoring: VWC {vwc:.1f}% (waiting for < {preset['VWCMin']:.1f}%)"
            )

    async def _handle_phase_p1_auto(self, vwc, ec, preset):
        """
        P1: Saturation phase - Saturate block quickly
        WITH OWN INTERVAL TRACKING (not blockCheckIntervall!)
        """
        # Check if calibrated max value already exists
        calibrated_max = self.data_store.getDeep(f"CropSteering.Calibration.p1.VWCMax")
        target_max = float(calibrated_max) if calibrated_max else preset["VWCMax"]

        # === P1 State Tracking ===
        p1_start_vwc = self.data_store.getDeep("CropSteering.p1_start_vwc")
        p1_irrigation_count = (
            self.data_store.getDeep("CropSteering.p1_irrigation_count") or 0
        )
        p1_last_vwc = self.data_store.getDeep("CropSteering.p1_last_vwc") or vwc
        last_irrigation_time = self.data_store.getDeep(
            "CropSteering.p1_last_irrigation_time"
        )

        now = datetime.now()

        # Initialize on first entry into P1
        if p1_start_vwc is None:
            self.data_store.setDeep("CropSteering.p1_start_vwc", vwc)
            self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
            self.data_store.setDeep("CropSteering.p1_last_vwc", vwc)
            self.data_store.setDeep(
                "CropSteering.p1_last_irrigation_time",
                now - timedelta(seconds=preset.get("wait_between", 180)),
            )
            p1_start_vwc = vwc
            p1_last_vwc = vwc
            last_irrigation_time = now - timedelta(
                seconds=preset.get("wait_between", 180)
            )

        # === 1. Target reached? ===
        if vwc >= target_max:
            _LOGGER.info(
                f"{self.room} - P1: Target reached {vwc:.1f}% >= {target_max:.1f}%"
            )
            await self._complete_p1_saturation(vwc, target_max, success=True)
            return

        # === 2. Stagnation detected? ===
        vwc_increase_since_last = vwc - p1_last_vwc
        if p1_irrigation_count >= 3 and vwc_increase_since_last < 0.5:
            _LOGGER.info(
                f"{self.room} - P1: Stagnation at {vwc:.1f}% (no increase since last shot)"
            )
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Block full at {vwc:.1f}% (no more increase)",
                },
                haEvent=True,
            )
            self.data_store.setDeep("CropSteering.Calibration.p1.VWCMax", vwc)
            self.data_store.setDeep("CropSteering.Calibration.p1.timestamp", datetime.now().isoformat())
            await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})
            await self._complete_p1_saturation(vwc, vwc, success=True, updated_max=True)
            return

        # === 3. Max Attempts? ===
        max_attempts = preset.get("max_cycles", 10)
        if p1_irrigation_count >= max_attempts:
            _LOGGER.info(f"{self.room} - P1: Max attempts reached ({max_attempts})")
            self.data_store.setDeep("CropSteering.Calibration.p1.VWCMax", vwc)
            self.data_store.setDeep("CropSteering.Calibration.p1.timestamp", datetime.now().isoformat())
            await self.event_manager.emit("SaveState", {"source": "CropSteeringCalibration"})
            await self._complete_p1_saturation(vwc, vwc, success=True, updated_max=True)
            return

        # === 4. Check interval ===
        wait_time = preset.get("wait_between", 180)
        time_since_last = (
            (now - last_irrigation_time).total_seconds()
            if last_irrigation_time
            else float("inf")
        )

        if time_since_last >= wait_time:
            # Time for next shot!
            await self._irrigate(duration=preset.get("irrigation_duration", 45))

            # Update state
            p1_irrigation_count += 1
            self.data_store.setDeep(
                "CropSteering.p1_irrigation_count", p1_irrigation_count
            )
            self.data_store.setDeep("CropSteering.p1_last_vwc", vwc)
            self.data_store.setDeep("CropSteering.p1_last_irrigation_time", now)

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"P1 Shot {p1_irrigation_count}/{max_attempts} → VWC: {vwc:.1f}% (target: {target_max:.1f}%)",
                },
                haEvent=True,
            )
            _LOGGER.info(
                f"{self.room} - P1: Shot {p1_irrigation_count}/{max_attempts}, VWC now {vwc:.1f}%"
            )

    async def _complete_p1_saturation(
        self, vwc, target_max, success=True, updated_max=False
    ):
        """
        Complete P1 saturation phase and transition to P2.
        Called when target VWC is reached, stagnation detected, or max attempts reached.
        """
        # Clear P1 state tracking
        self.data_store.setDeep("CropSteering.p1_start_vwc", None)
        self.data_store.setDeep("CropSteering.p1_irrigation_count", 0)
        self.data_store.setDeep("CropSteering.p1_last_vwc", None)
        self.data_store.setDeep("CropSteering.p1_last_irrigation_time", None)

        # Transition to P2
        self.data_store.setDeep("CropSteering.CropPhase", "p2")
        self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

        # Log the transition
        message = f"Saturation complete - VWC: {vwc:.1f}%"
        if updated_max:
            message += f" (new calibrated max)"

        await self._log_phase_change("p1", "p2", message)

        await self.event_manager.emit(
            "LogForClient",
            {"Name": self.room, "Type": "CSLOG", "Message": f"P1 → P2: {message}"},
            haEvent=True,
        )

        _LOGGER.info(
            f"{self.room} - P1 complete: VWC={vwc:.1f}%, target={target_max:.1f}%, success={success}"
        )

    async def _handle_phase_p2_auto(self, vwc, ec, is_light_on, preset):
        """
        P2: Maintenance phase - Maintain level during light phase
        WITH STAGE-CHECKER for light change
        """
        if is_light_on:
            # Normal day maintenance

            # Use calibrated max if available
            calibrated_max = self.data_store.getDeep(
                f"CropSteering.Calibration.p1.VWCMax"
            )
            effective_max = (
                float(calibrated_max) if calibrated_max else preset["VWCMax"]
            )

            hold_threshold = effective_max * preset.get("hold_percentage", 0.95)

            if vwc < hold_threshold:
                await self._irrigate(duration=preset.get("irrigation_duration", 20))
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P2 Maintenance: VWC {vwc:.1f}% < Hold {hold_threshold:.1f}% → Irrigation",
                    },
                    haEvent=True,
                )
                _LOGGER.info(
                    f"{self.room} - P2: Irrigated (VWC {vwc:.1f}% < {hold_threshold:.1f}%)"
                )
            else:
                # Debug: Show status in P2
                _LOGGER.debug(
                    f"{self.room} - P2 maintenance: VWC {vwc:.1f}% (hold at {hold_threshold:.1f}%, OK)"
                )
        else:
            # STAGE-CHECKER: Light is off -> Switch to P3
            _LOGGER.info(f"{self.room} - P2: Light OFF → Switching to P3")
            self.data_store.setDeep("CropSteering.CropPhase", "p3")
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())
            self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
            await self._log_phase_change(
                "p2", "p3", f"Night begins - Starting VWC: {vwc:.1f}%"
            )

    async def _handle_phase_p3_auto(self, vwc, ec, is_light_on, preset):
        """
        P3: Night dry-back phase - Controlled nightly dryback
        WITH STAGE-CHECKER for light change and calibrated values
        """
        if not is_light_on:
            # Normal night phase
            start_night = self.data_store.getDeep("CropSteering.startNightMoisture")

            # If startNightMoisture is missing (e.g. after restart), set it now
            if start_night is None or start_night == 0:
                self.data_store.setDeep("CropSteering.startNightMoisture", vwc)
                start_night = vwc
                _LOGGER.info(
                    f"{self.room} - P3: Initialized startNightMoisture to {vwc:.1f}%"
                )

            target_dryback = preset["target_dryback_percent"]
            current_dryback = (
                ((start_night - vwc) / start_night) * 100 if start_night else 0
            )

            _LOGGER.debug(
                f"{self.room} - P3: Dryback {current_dryback:.1f}% (target {target_dryback:.1f}%, start {start_night:.1f}%, current {vwc:.1f}%)"
            )

            # EC adjustment based on dryback
            if current_dryback < preset.get("min_dryback_percent", 8.0):
                # Too little dryback -> increase EC (more stress)
                await self._adjust_ec_for_dryback(
                    preset["ECTarget"],
                    increase=True,
                    step=preset.get("ec_increase_step", 0.1),
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P3 Low dryback {current_dryback:.1f}% < {preset.get('min_dryback_percent', 8.0):.1f}% → Increasing EC",
                    },
                    haEvent=True,
                )
                _LOGGER.info(f"{self.room} - P3: Low dryback, EC increased")

            elif current_dryback > preset.get("max_dryback_percent", 12.0):
                # Too much dryback -> decrease EC (less stress)
                await self._adjust_ec_for_dryback(
                    preset["ECTarget"],
                    increase=False,
                    step=preset.get("ec_decrease_step", 0.1),
                )
                await self.event_manager.emit(
                    "LogForClient",
                    {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P3 High dryback {current_dryback:.1f}% > {preset.get('max_dryback_percent', 12.0):.1f}% → Decreasing EC",
                    },
                    haEvent=True,
                )
                _LOGGER.info(f"{self.room} - P3: High dryback, EC decreased")
            else:
                _LOGGER.debug(
                    f"{self.room} - P3: Dryback optimal at {current_dryback:.1f}%"
                )

            # Emergency irrigation if too dry
            calibrated_max = self.data_store.getDeep(
                f"CropSteering.Calibration.p1.VWCMax"
            )
            effective_max = (
                float(calibrated_max) if calibrated_max else preset["VWCMax"]
            )

            emergency_level = effective_max * preset.get("emergency_threshold", 0.90)
            if vwc < emergency_level:
                p3_emergency_count = (
                    self.data_store.getDeep("CropSteering.p3_emergency_count") or 0
                )
                max_emergency = preset.get("max_emergency_shots", 2)

                if p3_emergency_count < max_emergency:
                    await self._irrigate(
                        duration=preset.get("irrigation_duration", 15),
                        is_emergency=True,
                    )
                    self.data_store.setDeep(
                        "CropSteering.p3_emergency_count", p3_emergency_count + 1
                    )
                    await self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "Type": "CSLOG",
                            "Message": f"P3 Emergency irrigation {p3_emergency_count + 1}/{max_emergency}: VWC {vwc:.1f}% < {emergency_level:.1f}%",
                        },
                        haEvent=True,
                    )
                    _LOGGER.warning(
                        f"{self.room} - P3: Emergency irrigation {p3_emergency_count + 1}/{max_emergency} (VWC {vwc:.1f}% < {emergency_level:.1f}%)"
                    )
                else:
                    _LOGGER.warning(
                        f"{self.room} - P3: Max emergency irrigations reached ({max_emergency}), skipping"
                    )
        else:
            # STAGE-CHECKER: Light is on -> Back to P0
            start_night = self.data_store.getDeep("CropSteering.startNightMoisture")
            current_dryback = (
                ((start_night - vwc) / start_night) * 100 if start_night else 0
            )
            night_start_time = self.data_store.getDeep("CropSteering.phaseStartTime")

            _LOGGER.info(
                f"{self.room} - P3: Light ON → Switching to P0 (Dryback was {current_dryback:.1f}%)"
            )
            self.data_store.setDeep("CropSteering.CropPhase", "p0")
            self.data_store.setDeep(
                "CropSteering.startNightMoisture", None
            )  # Reset for next night

            # Emit dryback complete event for AI learning
            night_duration = None
            if night_start_time:
                night_duration = (datetime.now() - night_start_time).total_seconds()

            await self.event_manager.emit(
                "CSDrybackComplete",
                {
                    "room": self.room,
                    "start_time": (
                        night_start_time.timestamp() * 1000
                        if night_start_time
                        else None
                    ),
                    "end_time": datetime.now().timestamp() * 1000,
                    "duration": night_duration,
                    "vwc_start": start_night,
                    "vwc_end": vwc,
                    "vwc_min": preset.get("VWCMin"),
                    "vwc_max": preset.get("VWCMax"),
                    "dryback_percent": current_dryback,
                    "target_dryback": preset.get("target_dryback_percent"),
                    "irrigation_count": self.data_store.getDeep(
                        "CropSteering.p3_emergency_count"
                    )
                    or 0,
                },
            )

            # Reset P3 tracking
            self.data_store.setDeep("CropSteering.p3_emergency_count", 0)

            await self._log_phase_change(
                "p3",
                "p0",
                f"Day starts - Final VWC: {vwc:.1f}%, Dryback: {current_dryback:.1f}%",
            )

    # ==================== MANUAL MODE ====================
    async def _manual_cycle(self, phase):
        """Manual time-based cycle (uses USER settings)"""
        _LOGGER.warning(f"{self.room} - CS - Manual {phase}: Started")
        try:
            settings = self._get_manual_phase_settings(phase)

            shot_duration = settings["ShotDuration"]["value"]
            shot_interval = settings["ShotIntervall"]["value"]
            shot_count = settings["ShotSum"]["value"]

            if shot_interval <= 0 or int(float(shot_count)) <= 0:
                await self.event_manager.emit(
                    "LogForClient",
                    f"CropSteering: Invalid settings for {phase}",
                    haEvent=True,
                )
                return

            self.data_store.setDeep("CropSteering.shotCounter", 0)
            self.data_store.setDeep("CropSteering.phaseStartTime", datetime.now())

            _LOGGER.warning(
                f"{self.room} - Manual {phase}: {shot_count} shots every {shot_interval}min"
            )

            while True:
                # === CRITICAL: Read sensor data NEWLY! ===
                sensor_data = await self._get_sensor_averages()
                if sensor_data:
                    self.data_store.setDeep(
                        "CropSteering.vwc_current", sensor_data["vwc"]
                    )
                    self.data_store.setDeep("CropSteering.ec_current", sensor_data["ec"])

                vwc = float(self.data_store.getDeep("CropSteering.vwc_current") or 0)
                ec = float(self.data_store.getDeep("CropSteering.ec_current") or 0)
                shot_counter = int(
                    float(self.data_store.getDeep("CropSteering.shotCounter"))
                )

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
                    await self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "Type": "Emergency irrigation",
                            "Message": f"CropSteering {phase}: Emergency irrigation",
                        },
                        haEvent=True,
                    )

                # Scheduled irrigation
                last_irrigation = self.data_store.getDeep(
                    "CropSteering.lastIrrigationTime"
                )
                now = datetime.now()

                should_irrigate = (
                    last_irrigation is None
                    or (now - last_irrigation).total_seconds() / 60 >= shot_interval
                )

                if should_irrigate and shot_counter < shot_count:
                    await self._irrigate(duration=shot_duration)
                    shot_counter += 1
                    self.data_store.setDeep("CropSteering.shotCounter", shot_counter)
                    self.data_store.setDeep("CropSteering.lastIrrigationTime", now)

                    await self.event_manager.emit(
                        "LogForClient",
                        f"CropSteering {phase}: Shot {shot_counter}/{shot_count}",
                        haEvent=True,
                    )

                # Reset counter after full cycle
                if shot_counter >= shot_count:
                    phase_start = self.data_store.getDeep("CropSteering.phaseStartTime")
                    elapsed = (now - phase_start).total_seconds() / 60

                    if elapsed >= shot_interval:
                        self.data_store.setDeep("CropSteering.shotCounter", 0)
                        self.data_store.setDeep("CropSteering.phaseStartTime", now)
                        await self.event_manager.emit(
                            "LogForClient",
                            f"CropSteering {phase}: New cycle started",
                            haEvent=True,
                        )

                await asyncio.sleep(10)

        except asyncio.CancelledError:
            await self._emergency_stop()
            raise
        except Exception as e:
            _LOGGER.error(f"Manual cycle error: {e}", exc_info=True)
            await self._emergency_stop()

    # ==================== IRRIGATION ====================

    async def _irrigate(self, duration=30, is_emergency=False):
        """Execute irrigation"""
        drippers = self._get_drippers()

        if not drippers:
            return

        # Capture pre-irrigation sensor data for AI learning
        pre_sensor_data = await self._get_sensor_averages()
        pre_vwc = pre_sensor_data.get("vwc") if pre_sensor_data else None
        pre_ec = pre_sensor_data.get("ec") if pre_sensor_data else None
        pre_pore_ec = pre_sensor_data.get("pore_ec") if pre_sensor_data else None
        pre_temp = pre_sensor_data.get("temperature") if pre_sensor_data else None

        current_phase = self.data_store.getDeep("CropSteering.CropPhase") or "p0"
        plant_phase = self.data_store.getDeep("isPlantDay.plantPhase")
        gen_week = self.data_store.getDeep("isPlantDay.generativeWeek")
        preset = self._get_adjusted_preset(current_phase, plant_phase, gen_week)

        try:
            # Turn on
            for dev_id in drippers:
                action = {
                    "Name": self.room, "Action": "on", "Device": dev_id, "Cycle": False
                }
                await self.event_manager.emit("PumpAction", action)

            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Irrigation started ({duration}s)",
                },
                haEvent=True,
            )

            await asyncio.sleep(duration)

            # Turn off
            for dev_id in drippers:
                action = {
                    "Name": self.room, "Action": "off", "Device": dev_id, "Cycle": False
                }
                await self.event_manager.emit("PumpAction", action)

            # Emit AI irrigation event
            await self.event_manager.emit(
                "CSIrrigation",
                {
                    "room": self.room,
                    "shot_number": self.data_store.getDeep("CropSteering.shotCounter")
                    or 1,
                    "duration": duration,
                    "pre_vwc": pre_vwc,
                    "pre_ec": pre_ec,
                    "pre_pore_ec": pre_pore_ec,
                    "pre_temperature": pre_temp,
                    "interval": preset.get("irrigation_interval")
                    or preset.get("wait_between"),
                    "target_vwc": preset.get("VWCTarget"),
                    "max_shots": preset.get("max_cycles") or preset.get("ShotSum"),
                    "is_emergency": is_emergency,
                },
            )

        except Exception as e:
            _LOGGER.error(f"Irrigation error: {e}")
            await self._emergency_stop()

    # ==================== EC ADJUSTMENT ====================

    async def _adjust_ec_for_dryback(self, target_ec, increase=True, step=0.1):
        """
        Adjust EC based on dryback performance
        Only used in Automatic Mode P3
        """
        direction = "increase" if increase else "decrease"
        new_ec = target_ec + step if increase else target_ec - step

        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"EC {direction}: {target_ec:.1f} -> {new_ec:.1f} (Dryback control)",
            },
            haEvent=True,
        )

        # Here the actual EC adjustment would take place via fertilizer dosing
        # TODO: Integration with Nutrient-System

    async def _adjust_ec_to_target(self, target_ec, increase=True):
        """EC adjustment for Manual Mode"""
        direction = "increase" if increase else "decrease"
        await self.event_manager.emit(
            "LogForClient",
            f"CropSteering: Adjusting EC {direction} towards {target_ec}",
            haEvent=True,
        )

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
        await self.event_manager.emit(
            "LogForClient", f"{self.room}: Emergency stop activated", haEvent=True
        )

    async def _turn_off_all_drippers(self):
        """Turn off all drippers"""
        drippers = self._get_drippers()

        for dev_id in drippers:
            try:
                action = {
                    "Name": self.room, "Action": "off", "Device": dev_id, "Cycle": False
                }
                await self.event_manager.emit("PumpAction", action)
            except Exception as e:
                _LOGGER.error(f"Error turning off {dev_id}: {e}")

    # ==================== HELPERS ====================

    def _get_env_avg(self, work_data: Dict, key: str) -> Optional[float]:
        """Get average value from workData for a sensor type"""
        try:
            values = work_data.get(key) or []
            if not values:
                return None
            numeric_values = []
            for item in values:
                val = item.get("value") if isinstance(item, dict) else item
                if val is not None:
                    try:
                        numeric_values.append(float(val))
                    except (ValueError, TypeError):
                        continue
            return sum(numeric_values) / len(numeric_values) if numeric_values else None
        except Exception:
            return None

    # ==================== LOGGING ====================

    async def _log_mode_start(self, mode, config, sensor_data):
        """Log mode start"""
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"CropSteering {mode.value} started",
                "VWC": sensor_data.get("vwc"),
                "EC": sensor_data.get("ec"),
                "PlantPhase": config["plant_phase"],
                "Week": config["generative_week"],
            },
            haEvent=True,
        )

    async def _log_phase_change(self, from_phase, to_phase, reason):
        """Log phase change"""
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"{from_phase} -> {to_phase}: {reason}",
            },
            haEvent=True,
        )

        # Emit AI event for learning
        sensor_data = await self._get_sensor_averages()
        is_light_on = self.data_store.getDeep("isPlantDay.islightON")
        plant_phase = self.data_store.getDeep("isPlantDay.plantPhase")
        gen_week = self.data_store.getDeep("isPlantDay.generativeWeek")
        preset = self._get_adjusted_preset(to_phase, plant_phase, gen_week)

        await self.event_manager.emit(
            "CSPhaseChange",
            {
                "room": self.room,
                "from_phase": from_phase,
                "to_phase": to_phase,
                "trigger": reason,
                "vwc": sensor_data.get("vwc") if sensor_data else None,
                "ec": sensor_data.get("ec") if sensor_data else None,
                "pore_ec": sensor_data.get("pore_ec") if sensor_data else None,
                "temperature": sensor_data.get("temperature") if sensor_data else None,
                "vwc_min": preset.get("VWCMin"),
                "vwc_max": preset.get("VWCMax"),
                "ec_target": preset.get("ECTarget"),
                "light_status": "on" if is_light_on else "off",
            },
        )

    async def _log_missing_sensors(self):
        """Log missing sensor data"""
        _LOGGER.debug(
            f"{self.room} Message: CropSteering: Waiting for sensor data (VWC/EC missing)"
        )
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": "Waiting for sensor data (VWC/EC missing)",
            },
            haEvent=True,
        )

    # ==================== VWC CALIBRATION (ONLY FOR AUTOMATIC MODE) ====================

    async def handle_vwc_calibration_command(self, command_data):
        """
        Handle VWC calibration commands - delegates to CalibrationManager
        Calibration only runs in Automatic Mode

        Expected:
        {
            "action": "start_max" | "start_min" | "stop",
            "phase": "p0" | "p1" | "p2" | "p3"
        }
        """

        # Prüfe ob Automatic Mode aktiv
        current_mode = self.data_store.getDeep("CropSteering.ActiveMode") or ""
        if "Automatic" not in current_mode:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "VWC Calibration only available in Automatic Mode",
                },
                haEvent=True,
            )
            return

        # Delegate to CalibrationManager
        if self.calibration_manager:
            await self.calibration_manager.handle_vwc_calibration_command(command_data)
        else:
            _LOGGER.error(f"{self.room} - CalibrationManager not initialized")

    # NOTE: VWC calibration methods (start_vwc_max_calibration, start_vwc_min_calibration, 
    # stop_vwc_calibration, etc.) are now handled by OGBCSCalibrationManager
    # See handle_vwc_calibration_command() which delegates to self.calibration_manager

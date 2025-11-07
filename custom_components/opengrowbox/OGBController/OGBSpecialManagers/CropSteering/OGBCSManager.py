import logging
import asyncio
from datetime import datetime
from enum import Enum
from datetime import datetime, timedelta
from ...OGBDataClasses.OGBPublications import OGBHydroAction

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
        self.blockCheckIntervall = 120

        # Single task for any CS operation
        self._main_task = None
        self._calibration_task = None
        
        # Event subscriptions
        self.eventManager.on("CropSteeringChanges", self.handle_mode_change)
        self.eventManager.on("VWCCalibrationCommand", self.handle_vwc_calibration_command)
 
    # ==================== AUTOMATIC PRESETS ====================
    
    def _get_automatic_presets(self):
        """
        Cannabis-optimierte Presets für Automatic Mode
        Basierend auf wissenschaftlichen Crop Steering Prinzipien
        """
        return {
            "p0": {
                # P0: Monitoring - Warte auf Dryback Signal
                "description": "Initial Monitoring Phase",
                "VWCTarget": 58.0,      # Ziel-Feuchtigkeit für Start P1
                "VWCMin": 55.0,         # Untere Grenze triggert P1
                "VWCMax": 65.0,         # Obere Grenze (Referenz)
                "ECTarget": 2.0,        # Basis EC
                "MinEC": 1.8,
                "MaxEC": 2.2,
                "trigger_condition": "vwc_below_min"
            },
            
            "p1": {
                # P1: Saturation - Schnelle Sättigung des Blocks
                "description": "Saturation Phase",
                "VWCTarget": 70.0,      # Ziel: Block voll sättigen
                "VWCMax": 68.0,         # Trigger für Wechsel zu P2
                "VWCMin": 55.0,         # Sicherheitsgrenze
                "ECTarget": 1.8,        # Niedrigere EC beim Sättigen
                "MinEC": 1.6,
                "MaxEC": 2.0,
                "irrigation_duration": 45,  # Längere Shots für Sättigung
                "max_cycles": 10,       # Max Bewässerungszyklen
                "wait_between": 1800,    # 3 Min zwischen Shots
                "trigger_condition": "vwc_above_target"
            },
            
            "p2": {
                # P2: Maintenance - Halte Level während Lichtphase
                "description": "Day Maintenance Phase",
                "VWCTarget": 65.0,      # Halte bei 95% von Max
                "VWCMax": 68.0,         # Referenz Max
                "VWCMin": 62.0,         # Trigger für Nachbewässerung
                "hold_percentage": 0.95, # Halte bei 95% vom Max
                "ECTarget": 2.0,        # Standard EC
                "MinEC": 1.8,
                "MaxEC": 2.2,
                "irrigation_duration": 20,  # Kurze Maintenance-Shots
                "check_light": True,    # Nur bei Licht aktiv
                "trigger_condition": "light_off"
            },
            
            "p3": {
                # P3: Night Dryback - Kontrollierter nächtlicher Dryback
                "description": "Night Dryback Phase",
                "VWCTarget": 60.0,      # Ziel für Dryback
                "VWCMax": 68.0,         # Start-Referenz
                "VWCMin": 58.0,         # Minimum für Notfall-Irrigation
                "target_dryback_percent": 10.0,  # Ziel: 10% Dryback
                "min_dryback_percent": 8.0,      # Generativ
                "max_dryback_percent": 12.0,     # Vegetativ
                "emergency_threshold": 0.90,     # 90% von Max = Notfall
                "ECTarget": 2.2,        # Höhere EC für Dryback-Kontrolle
                "MinEC": 2.0,
                "MaxEC": 2.5,
                "ec_increase_step": 0.1,
                "ec_decrease_step": 0.1,
                "irrigation_duration": 15,  # Kurze Notfall-Shots
                "trigger_condition": "light_on"
            }
        }
    
    def _get_phase_growth_adjustments(self, plant_phase, generative_week):
        """
        Wachstumsphasen-spezifische Anpassungen
        
        Vegetativ: Mehr Feuchtigkeit, weniger Dryback (generativ)
        Generativ: Weniger Feuchtigkeit, mehr Dryback (vegetativ)
        """
        adjustments = {
            "vwc_modifier": 0.0,
            "dryback_modifier": 0.0,
            "ec_modifier": 0.0
        }
        
        if plant_phase == "veg":
            # Vegetative Phase: Fördern Wachstum
            adjustments["vwc_modifier"] = 2.0      # +2% Feuchtigkeit
            adjustments["dryback_modifier"] = -2.0  # -2% Dryback (weniger Stress)
            adjustments["ec_modifier"] = -0.1       # Etwas niedrigere EC
            
        elif plant_phase == "gen":
            # Flowering Phase: Fördern Blütenbildung
            if generative_week <= 3:
                # Early Flower: Übergang
                adjustments["vwc_modifier"] = 1.0
                adjustments["dryback_modifier"] = -1.0
                adjustments["ec_modifier"] = 0.05
            elif generative_week <= 5:
                # Mid Flower: Verstärkt generativ
                adjustments["vwc_modifier"] = -2.0     # -2% Feuchtigkeit
                adjustments["dryback_modifier"] = 2.0   # +2% Dryback (mehr Stress)
                adjustments["ec_modifier"] = 0.2        # Höhere EC
            elif generative_week <= 7:
                # Mid Flower: Verstärkt generativ
                adjustments["vwc_modifier"] = 2.0     # -2% Feuchtigkeit
                adjustments["dryback_modifier"] = -2.0   # +2% Dryback (mehr Stress)
                adjustments["ec_modifier"] = 0.1        # Höhere EC
            else:
                # Late Flower: Maximal generativ
                adjustments["vwc_modifier"] = -3.0     # -3% Feuchtigkeit
                adjustments["dryback_modifier"] = 3.0   # +3% Dryback
                adjustments["ec_modifier"] = 0.3        # Noch höhere EC
        
        return adjustments
    
    def _get_adjusted_preset(self, phase, plant_phase, generative_week):
        """
        Hole Preset und wende Wachstumsphasen-Anpassungen an
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
        multimediumCtrl = self.dataStore.getDeep("controlOptions.multiMediumControl")
        
        if multimediumCtrl == False:
            _LOGGER.error(f"{self.room} - CropSteering Single Medium Control No working Switch the button only multi control working right now")
            return
        
        cropMode = self.dataStore.getDeep("CropSteering.ActiveMode")
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
    
    async def _get_sensor_averages(self):
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
        
        # Manual Mode verwendet User-Settings
        if mode.value.startswith("Manual"):
            phase = mode.value.split("-")[1]
            config['phase_settings'] = self._get_manual_phase_settings(phase)
        
        return config

    def _get_drippers(self):
        """Get valid dripper devices"""
        dripperDevices = self.dataStore.getDeep("capabilities.canPump")
        if not dripperDevices:
            return []
        
        devices = dripperDevices.get("devEntities", [])
        valid_keywords = ["dripper"]
        
        return [
            dev for dev in devices
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]

    def _get_manual_phase_settings(self, phase):
        """Get USER settings für Manual Mode"""
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
    
    async def _determine_initial_phase(self):
        """
        Intelligente Bestimmung der Start-Phase basierend auf:
        - Aktueller VWC
        - Licht-Status
        - Kalibrierte/Preset Werte
        """
        vwc = float(self.dataStore.getDeep("CropSteering.vwc_current") or 0)
        is_light_on = self.dataStore.getDeep("isPlantDay.islightON")
        
        plant_phase = self.dataStore.getDeep("isPlantDay.plantPhase")
        gen_week = self.dataStore.getDeep("isPlantDay.generativeWeek")
        
        # Hole angepasste Presets
        p0_preset = self._get_adjusted_preset("p0", plant_phase, gen_week)
        p2_preset = self._get_adjusted_preset("p2", plant_phase, gen_week)
        
        # Entscheidungslogik
        if vwc == 0:
            return "p0"  # Keine Daten, starte in Monitoring
        
        if is_light_on:
            # Tag-Zeit
            if vwc >= p2_preset["VWCMax"] * 0.90:
                # Block ist relativ voll -> P2 Maintenance
                return "p2"
            elif vwc < p0_preset["VWCMin"]:
                # Block ist trocken -> P1 Saturation
                return "p1"
            else:
                # Irgendwo dazwischen -> P0 Monitoring
                return "p0"
        else:
            # Nacht-Zeit
            if vwc >= p2_preset["VWCMax"] * 0.90:
                # Block voll, Nacht -> P3 Dryback
                # Setze startNightMoisture für Dryback-Berechnung
                self.dataStore.setDeep("CropSteering.startNightMoisture", vwc)
                return "p3"
            elif vwc < p0_preset["VWCMin"]:
                # Block zu trocken auch nachts -> P1 Emergency Saturation
                return "p1"
            else:
                # Normal nachts -> P3 Dryback
                self.dataStore.setDeep("CropSteering.startNightMoisture", vwc)
                return "p3"
    
    async def _automatic_cycle(self):
        """Automatic sensor-based cycle mit festen Presets"""
        try:
            plant_phase = self.dataStore.getDeep("isPlantDay.plantPhase")
            generative_week = self.dataStore.getDeep("isPlantDay.generativeWeek")
            
            # WICHTIG: Bestimme Start-Phase basierend auf aktuellen Bedingungen
            initial_phase = await self._determine_initial_phase()
            self.dataStore.setDeep("CropSteering.CropPhase", initial_phase)
            
            _LOGGER.warning(f"{self.room} - Automatic CS cycle started in phase {initial_phase}")
            
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Started in {initial_phase} - {plant_phase} week {generative_week}"
                },
                haEvent=True
            )
            
            while True:
                # === KRITISCH: Sensordaten NEU auslesen! ===
                sensor_data = await self._get_sensor_averages()
                if sensor_data:
                    self.dataStore.setDeep("CropSteering.vwc_current", sensor_data['vwc'])
                    self.dataStore.setDeep("CropSteering.ec_current", sensor_data['ec'])
                
                current_phase = self.dataStore.getDeep("CropSteering.CropPhase")
                
                # Hole angepasste Presets basierend auf Wachstumsphase
                preset = self._get_adjusted_preset(current_phase, plant_phase, generative_week)
                
                vwc = float(self.dataStore.getDeep("CropSteering.vwc_current") or 0)
                ec = float(self.dataStore.getDeep("CropSteering.ec_current") or 0)
                is_light_on = self.dataStore.getDeep("isPlantDay.islightON")
                
                if vwc == 0:
                    await asyncio.sleep(self.blockCheckIntervall)
                    continue
                
                # Phase logic mit Presets
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
        """P0: Monitoring phase - Warte auf Dryback Signal"""
        # P0 ist einfach: Warte bis VWC unter Minimum fällt
        if vwc < preset["VWCMin"]:
            _LOGGER.info(f"{self.room} - P0: VWC {vwc:.1f}% < Min {preset['VWCMin']:.1f}% → Switching to P1")
            self.dataStore.setDeep("CropSteering.CropPhase", "p1")
            self.dataStore.setDeep("CropSteering.phaseStartTime", datetime.now())
            await self._log_phase_change(
                "p0", "p1", 
                f"Dryback detected - VWC: {vwc:.1f}% < Min: {preset['VWCMin']:.1f}%"
            )
        else:
            # Debug: Zeige aktuelle VWC in P0
            _LOGGER.debug(f"{self.room} - P0 monitoring: VWC {vwc:.1f}% (waiting for < {preset['VWCMin']:.1f}%)")

    async def _handle_phase_p1_auto(self, vwc, ec, preset):
        """
        P1: Saturation phase - Sättige Block schnell
        MIT EIGENEM INTERVALL-TRACKING (nicht blockCheckIntervall!)
        """
        # Prüfe ob bereits kalibrierter Max-Wert existiert
        calibrated_max = self.dataStore.getDeep(f"CropSteering.Calibration.p1.VWCMax")
        target_max = float(calibrated_max) if calibrated_max else preset["VWCMax"]

        # === P1 State Tracking ===
        p1_start_vwc = self.dataStore.getDeep("CropSteering.p1_start_vwc")
        p1_irrigation_count = self.dataStore.getDeep("CropSteering.p1_irrigation_count") or 0
        p1_last_vwc = self.dataStore.getDeep("CropSteering.p1_last_vwc") or vwc
        last_irrigation_time = self.dataStore.getDeep("CropSteering.p1_last_irrigation_time")

        now = datetime.now()

        # Initialisiere beim ersten Eintritt in P1
        if p1_start_vwc is None:
            self.dataStore.setDeep("CropSteering.p1_start_vwc", vwc)
            self.dataStore.setDeep("CropSteering.p1_irrigation_count", 0)
            self.dataStore.setDeep("CropSteering.p1_last_vwc", vwc)
            self.dataStore.setDeep("CropSteering.p1_last_irrigation_time", now - timedelta(seconds=preset.get("wait_between", 180)))
            p1_start_vwc = vwc
            p1_last_vwc = vwc
            last_irrigation_time = now - timedelta(seconds=preset.get("wait_between", 180))

        # === 1. Ziel erreicht? ===
        if vwc >= target_max:
            _LOGGER.info(f"{self.room} - P1: Target reached {vwc:.1f}% >= {target_max:.1f}%")
            await self._complete_p1_saturation(vwc, target_max, success=True)
            return

        # === 2. Stagnation erkannt? ===
        vwc_increase_since_last = vwc - p1_last_vwc
        if p1_irrigation_count >= 3 and vwc_increase_since_last < 0.5:
            _LOGGER.info(f"{self.room} - P1: Stagnation at {vwc:.1f}% (no increase since last shot)")
            await self.eventManager.emit("LogForClient", {
                "Name": self.room, "Type": "CSLOG",
                "Message": f"Block voll bei {vwc:.1f}% (kein Anstieg mehr)"
            }, haEvent=True)
            self.dataStore.setDeep("CropSteering.Calibration.p1.VWCMax", vwc)
            await self._complete_p1_saturation(vwc, vwc, success=True, updated_max=True)
            return

        # === 3. Max Attempts? ===
        max_attempts = preset.get("max_cycles", 10)
        if p1_irrigation_count >= max_attempts:
            _LOGGER.info(f"{self.room} - P1: Max attempts reached ({max_attempts})")
            self.dataStore.setDeep("CropSteering.Calibration.p1.VWCMax", vwc)
            await self._complete_p1_saturation(vwc, vwc, success=True, updated_max=True)
            return

        # === 4. Intervall prüfen ===
        wait_time = preset.get("wait_between", 180)
        time_since_last = (now - last_irrigation_time).total_seconds() if last_irrigation_time else float('inf')

        if time_since_last >= wait_time:
            # Zeit für nächsten Shot!
            await self._irrigate(duration=preset.get("irrigation_duration", 45))

            # Update state
            p1_irrigation_count += 1
            self.dataStore.setDeep("CropSteering.p1_irrigation_count", p1_irrigation_count)
            self.dataStore.setDeep("CropSteering.p1_last_vwc", vwc)
            self.dataStore.setDeep("CropSteering.p1_last_irrigation_time", now)

            await self.eventManager.emit("LogForClient", {
                "Name": self.room, "Type": "CSLOG",
                "Message": f"P1 Shot {p1_irrigation_count}/{max_attempts} → VWC: {vwc:.1f}% (target: {target_max:.1f}%)"
            }, haEvent=True)
            _LOGGER.info(f"{self.room} - P1: Shot {p1_irrigation_count}/{max_attempts}, VWC now {vwc:.1f}%")

    async def _handle_phase_p2_auto(self, vwc, ec, is_light_on, preset):
        """
        P2: Maintenance phase - Halte Level während Lichtphase
        MIT STAGE-CHECKER für Lichtwechsel
        """
        if is_light_on:
            # Normale Tag-Wartung
            
            # Verwende kalibrierten Max falls vorhanden
            calibrated_max = self.dataStore.getDeep(f"CropSteering.Calibration.p1.VWCMax")
            effective_max = float(calibrated_max) if calibrated_max else preset["VWCMax"]
            
            hold_threshold = effective_max * preset.get("hold_percentage", 0.95)
            
            if vwc < hold_threshold:
                await self._irrigate(duration=preset.get("irrigation_duration", 20))
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P2 Maintenance: VWC {vwc:.1f}% < Hold {hold_threshold:.1f}% → Irrigation"
                    },
                    haEvent=True
                )
                _LOGGER.info(f"{self.room} - P2: Irrigated (VWC {vwc:.1f}% < {hold_threshold:.1f}%)")
            else:
                # Debug: Zeige Status in P2
                _LOGGER.debug(f"{self.room} - P2 maintenance: VWC {vwc:.1f}% (hold at {hold_threshold:.1f}%, OK)")
        else:
            # STAGE-CHECKER: Licht ist aus -> Wechsel zu P3
            _LOGGER.info(f"{self.room} - P2: Light OFF → Switching to P3")
            self.dataStore.setDeep("CropSteering.CropPhase", "p3")
            self.dataStore.setDeep("CropSteering.phaseStartTime", datetime.now())
            self.dataStore.setDeep("CropSteering.startNightMoisture", vwc)
            await self._log_phase_change(
                "p2", "p3", 
                f"Night begins - Starting VWC: {vwc:.1f}%"
            )

    async def _handle_phase_p3_auto(self, vwc, ec, is_light_on, preset):
        """
        P3: Night dry-back phase - Kontrollierter nächtlicher Dryback
        MIT STAGE-CHECKER für Lichtwechsel und kalibrierten Werten
        """
        if not is_light_on:
            # Normale Nacht-Phase
            start_night = self.dataStore.getDeep("CropSteering.startNightMoisture")
            
            # Falls startNightMoisture fehlt (z.B. nach Neustart), setze es jetzt
            if start_night is None or start_night == 0:
                self.dataStore.setDeep("CropSteering.startNightMoisture", vwc)
                start_night = vwc
                _LOGGER.info(f"{self.room} - P3: Initialized startNightMoisture to {vwc:.1f}%")
            
            target_dryback = preset["target_dryback_percent"]
            current_dryback = ((start_night - vwc) / start_night) * 100 if start_night else 0
            
            _LOGGER.debug(f"{self.room} - P3: Dryback {current_dryback:.1f}% (target {target_dryback:.1f}%, start {start_night:.1f}%, current {vwc:.1f}%)")
            
            # EC-Anpassung basierend auf Dryback
            if current_dryback < preset.get("min_dryback_percent", 8.0):
                # Zu wenig Dryback -> EC erhöhen (mehr Stress)
                await self._adjust_ec_for_dryback(
                    preset["ECTarget"], 
                    increase=True, 
                    step=preset.get("ec_increase_step", 0.1)
                )
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P3 Low dryback {current_dryback:.1f}% < {preset.get('min_dryback_percent', 8.0):.1f}% → Increasing EC"
                    },
                    haEvent=True
                )
                _LOGGER.info(f"{self.room} - P3: Low dryback, EC increased")
            
            elif current_dryback > preset.get("max_dryback_percent", 12.0):
                # Zu viel Dryback -> EC senken (weniger Stress)
                await self._adjust_ec_for_dryback(
                    preset["ECTarget"], 
                    increase=False, 
                    step=preset.get("ec_decrease_step", 0.1)
                )
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P3 High dryback {current_dryback:.1f}% > {preset.get('max_dryback_percent', 12.0):.1f}% → Decreasing EC"
                    },
                    haEvent=True
                )
                _LOGGER.info(f"{self.room} - P3: High dryback, EC decreased")
            else:
                _LOGGER.debug(f"{self.room} - P3: Dryback optimal at {current_dryback:.1f}%")
            
            # Notfall-Bewässerung wenn zu trocken
            calibrated_max = self.dataStore.getDeep(f"CropSteering.Calibration.p1.VWCMax")
            effective_max = float(calibrated_max) if calibrated_max else preset["VWCMax"]
            
            emergency_level = effective_max * preset.get("emergency_threshold", 0.90)
            if vwc < emergency_level:
                await self._irrigate(duration=preset.get("irrigation_duration", 15))
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"P3 Emergency irrigation: VWC {vwc:.1f}% < {emergency_level:.1f}%"
                    },
                    haEvent=True
                )
                _LOGGER.warning(f"{self.room} - P3: Emergency irrigation (VWC {vwc:.1f}% < {emergency_level:.1f}%)")
        else:
            # STAGE-CHECKER: Licht ist an -> Zurück zu P0
            start_night = self.dataStore.getDeep("CropSteering.startNightMoisture")
            current_dryback = ((start_night - vwc) / start_night) * 100 if start_night else 0
            
            _LOGGER.info(f"{self.room} - P3: Light ON → Switching to P0 (Dryback was {current_dryback:.1f}%)")
            self.dataStore.setDeep("CropSteering.CropPhase", "p0")
            self.dataStore.setDeep("CropSteering.startNightMoisture", None)  # Reset für nächste Nacht
            
            await self._log_phase_change(
                "p3", "p0", 
                f"Day starts - Final VWC: {vwc:.1f}%, Dryback: {current_dryback:.1f}%"
            )

    # ==================== MANUAL MODE ====================
    async def _manual_cycle(self, phase):
        """Manual time-based cycle (verwendet USER Settings)"""
        _LOGGER.warning(f"{self.room} - CS - Manual {phase}: Started")
        try:
            settings = self._get_manual_phase_settings(phase)
            
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
            
            _LOGGER.warning(f"{self.room} - Manual {phase}: {shot_count} shots every {shot_interval}min")
            
            while True:
                # === KRITISCH: Sensordaten NEU auslesen! ===
                sensor_data = await self._get_sensor_averages()
                if sensor_data:
                    self.dataStore.setDeep("CropSteering.vwc_current", sensor_data['vwc'])
                    self.dataStore.setDeep("CropSteering.ec_current", sensor_data['ec'])
                
                vwc = float(self.dataStore.getDeep("CropSteering.vwc_current") or 0)
                ec = float(self.dataStore.getDeep("CropSteering.ec_current") or 0)
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
                    "Type": "CSLOG",
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

    # ==================== EC ADJUSTMENT ====================
    
    async def _adjust_ec_for_dryback(self, target_ec, increase=True, step=0.1):
        """
        Passe EC basierend auf Dryback-Performance an
        Wird nur in Automatic Mode P3 verwendet
        """
        direction = "erhöhen" if increase else "senken"
        new_ec = target_ec + step if increase else target_ec - step
        
        await self.eventManager.emit(
            "LogForClient", {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"EC {direction}: {target_ec:.1f} -> {new_ec:.1f} (Dryback-Steuerung)"
            },
            haEvent=True
        )
        
        # Hier würde die tatsächliche EC-Anpassung über Dünger-Dosierung erfolgen
        # TODO: Integration mit Nutrient-System

    async def _adjust_ec_to_target(self, target_ec, increase=True):
        """EC adjustment für Manual Mode"""
        direction = "increase" if increase else "decrease"
        await self.eventManager.emit(
            "LogForClient",
            f"CropSteering: Adjusting EC {direction} towards {target_ec}",
            haEvent=True
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
                "Type": "CSLOG",
                "Message": f"CropSteering {mode.value} started",
                "VWC": sensor_data.get('vwc'),
                "EC": sensor_data.get('ec'),
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
                "Message":f"{from_phase} -> {to_phase}: {reason}",
            },
            haEvent=True
        )

    async def _log_missing_sensors(self):
        """Log missing sensor data"""
        _LOGGER.debug(f"{self.room} Message: CropSteering: Waiting for sensor data (VWC/EC missing)")
        await self.eventManager.emit(
            "LogForClient", {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": "Waiting for sensor data (VWC/EC missing)"
            },
            haEvent=True
        )

    # ==================== VWC CALIBRATION (NUR FÜR AUTOMATIC MODE) ====================
    
    async def handle_vwc_calibration_command(self, command_data):
        """
        Handle VWC calibration commands
        Kalibrierung läuft NUR im Automatic Mode
        
        Expected:
        {
            "action": "start_max" | "start_min" | "stop",
            "phase": "p0" | "p1" | "p2" | "p3"
        }
        """
        
        # Prüfe ob Automatic Mode aktiv
        current_mode = self.dataStore.getDeep("CropSteering.ActiveMode")
        if "Automatic" not in current_mode:
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "VWC Calibration only available in Automatic Mode"
                },
                haEvent=True
            )
            return
        
        action = command_data.get("action")
        phase = command_data.get("phase", "p1")
        
        if action == "start_max":
            await self.start_vwc_max_calibration(phase=phase)
        elif action == "start_min":
            await self.start_vwc_min_calibration(phase=phase)
        elif action == "stop":
            await self.stop_vwc_calibration()
        else:
            await self.eventManager.emit(
                "LogForClient",
                f"VWC Calibration: Unknown action '{action}'",
                haEvent=True
            )

    async def start_vwc_max_calibration(self, phase="p1"):
        """
        Start VWC max calibration
        Findet das Maximum VWC durch schrittweise Sättigung
        """
        
        if self._calibration_task is not None:
            await self.stop_vwc_calibration()
        
        self._calibration_task = asyncio.create_task(
            self._vwc_max_calibration_cycle(phase)
        )
        
        await self.eventManager.emit(
            "LogForClient", {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"VWC Max Calibration started for {phase}"
            },
            haEvent=True
        )

    async def _vwc_max_calibration_cycle(self, phase):
        """Main VWC max calibration cycle"""
        
        try:
            calibration_complete = False
            irrigation_count = 0
            previous_vwc = 0
            stable_readings = []
            
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "Starting VWC Max auto-calibration"
                },
                haEvent=True
            )
            
            while not calibration_complete and irrigation_count < self.max_irrigation_attempts:
                irrigation_count += 1
                
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"Irrigation cycle {irrigation_count}/{self.max_irrigation_attempts}"
                    },
                    haEvent=True
                )
                
                # Bewässere
                await self._irrigate(duration=45)
                
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": "Waiting for VWC stabilization..."
                    },
                    haEvent=True
                )
                
                # Warte auf Stabilisierung
                stable_vwc = await self._wait_for_vwc_stabilization(timeout=300)
                
                if stable_vwc is None:
                    await self.eventManager.emit(
                        "LogForClient", {
                            "Name": self.room,
                            "Type": "CSLOG",
                            "Message": "Timeout waiting for stabilization"
                        },
                        haEvent=True
                    )
                    break
                
                stable_readings.append(stable_vwc)
                
                # Prüfe Zunahme
                vwc_increase = stable_vwc - previous_vwc
                vwc_increase_percent = (vwc_increase / previous_vwc * 100) if previous_vwc > 0 else 100
                
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"VWC={stable_vwc:.1f}%, Increase={vwc_increase_percent:.1f}%"
                    },
                    haEvent=True
                )
                
                # Prüfe ob Maximum erreicht (keine signifikante Zunahme mehr)
                if len(stable_readings) >= 2:
                    last_increase = stable_readings[-1] - stable_readings[-2]
                    last_increase_percent = (last_increase / stable_readings[-2] * 100) if stable_readings[-2] > 0 else 0
                    
                    if abs(last_increase_percent) < self.stability_tolerance:
                        calibration_complete = True
                        max_vwc = stable_vwc
                        
                        await self.eventManager.emit(
                            "LogForClient", {
                                "Name": self.room,
                                "Type": "CSLOG",
                                "Message": f"✓ COMPLETE! VWCMax={max_vwc:.1f}%"
                            },
                            haEvent=True
                        )
                        
                        # Aktualisiere Preset-Werte
                        plant_phase = self.dataStore.getDeep("isPlantDay.plantPhase")
                        gen_week = self.dataStore.getDeep("isPlantDay.generativeWeek")
                        
                        # Speichere kalibrierte Werte
                        self.dataStore.setDeep(f"CropSteering.Calibration.{phase}.VWCMax", max_vwc)
                        self.dataStore.setDeep(f"CropSteering.Calibration.{phase}.timestamp", datetime.now().isoformat())
                        
                        calibration_result = {
                            "phase": phase,
                            "vwc_max": max_vwc,
                            "irrigation_attempts": irrigation_count,
                            "readings": stable_readings,
                            "plant_phase": plant_phase,
                            "generative_week": gen_week
                        }
                        
                        await self.eventManager.emit(
                            "VWCCalibrationComplete",
                            calibration_result,
                            haEvent=True
                        )
                        
                        break
                
                previous_vwc = stable_vwc
                await asyncio.sleep(60)
            
            if not calibration_complete and stable_readings:
                max_vwc = max(stable_readings)
                self.dataStore.setDeep(f"CropSteering.Calibration.{phase}.VWCMax", max_vwc)
                
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"Estimated VWCMax={max_vwc:.1f}% (max attempts reached)"
                    },
                    haEvent=True
                )
                    
        except asyncio.CancelledError:
            await self._turn_off_all_drippers()
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "Calibration cancelled"
                },
                haEvent=True
            )
            raise
        except Exception as e:
            await self._turn_off_all_drippers()
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Calibration error: {str(e)}"
                },
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
            
            readings.append(float(current_vwc))
            
            if len(readings) > min_readings:
                readings.pop(0)
            
            if len(readings) >= min_readings:
                avg_vwc = sum(readings) / len(readings)
                max_deviation = max(abs(r - avg_vwc) for r in readings)
                deviation_percent = (max_deviation / avg_vwc * 100) if avg_vwc > 0 else 100
                
                if deviation_percent < self.stability_tolerance:
                    await self.eventManager.emit(
                        "LogForClient", {
                            "Name": self.room,
                            "Type": "CSLOG",
                            "Message": f"Stable at {avg_vwc:.1f}%"
                        },
                        haEvent=True
                    )
                    return avg_vwc
            
            await asyncio.sleep(check_interval)
        
        return None

    async def start_vwc_min_calibration(self, phase="p1"):
        """
        Calibrate VWC minimum by monitoring dry-back
        Findet das Minimum VWC durch überwachten Dryback
        """
        
        if self._calibration_task is not None:
            await self.stop_vwc_calibration()
        
        # Verwende Preset für Dryback-Dauer
        preset = self._get_automatic_presets()[phase]
        dry_back_duration = 3600  # 1 Stunde Standard
        
        self._calibration_task = asyncio.create_task(
            self._vwc_min_calibration_cycle(phase, dry_back_duration)
        )
        
        await self.eventManager.emit(
            "LogForClient", {
                "Name": self.room,
                "Type": "CSLOG",
                "Message": f"VWC Min Calibration started for {phase} ({dry_back_duration}s)"
            },
            haEvent=True
        )

    async def _vwc_min_calibration_cycle(self, phase, dry_back_duration):
        """Monitor dry-back to find VWC minimum"""
        
        start_time = datetime.now()
        start_vwc = self.dataStore.getDeep("CropSteering.vwc_current")
        min_vwc_observed = float('inf')
        readings = []
        
        try:
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": f"Monitoring dryback from {start_vwc:.1f}% for {dry_back_duration/60:.0f} minutes"
                },
                haEvent=True
            )
            
            while (datetime.now() - start_time).total_seconds() < dry_back_duration:
                current_vwc = self.dataStore.getDeep("CropSteering.vwc_current")
                
                if current_vwc is not None and current_vwc > 0:
                    readings.append(float(current_vwc))
                    min_vwc_observed = min(min_vwc_observed, float(current_vwc))
                    
                    # Periodisches Update
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if elapsed % 300 == 0:  # Alle 5 Minuten
                        dryback_percent = ((start_vwc - current_vwc) / start_vwc * 100) if start_vwc else 0
                        await self.eventManager.emit(
                            "LogForClient", {
                                "Name": self.room,
                                "Type": "CSLOG",
                                "Message": f"Current: {current_vwc:.1f}%, Dryback: {dryback_percent:.1f}%"
                            },
                            haEvent=True
                        )
                
                await asyncio.sleep(30)
            
            if min_vwc_observed < float('inf'):
                # Sicherheits-Puffer: 10% über beobachtetem Minimum
                safe_min_vwc = min_vwc_observed * 1.1
                
                self.dataStore.setDeep(f"CropSteering.Calibration.{phase}.VWCMin", safe_min_vwc)
                self.dataStore.setDeep(f"CropSteering.Calibration.{phase}.timestamp", datetime.now().isoformat())
                
                final_dryback = ((start_vwc - min_vwc_observed) / start_vwc * 100) if start_vwc else 0
                
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": f"✓ COMPLETE! VWCMin={safe_min_vwc:.1f}% (observed: {min_vwc_observed:.1f}%, dryback: {final_dryback:.1f}%)"
                    },
                    haEvent=True
                )
                
                result = {
                    "phase": phase,
                    "vwc_min": safe_min_vwc,
                    "vwc_min_observed": min_vwc_observed,
                    "start_vwc": start_vwc,
                    "final_dryback_percent": final_dryback,
                    "readings_count": len(readings)
                }
                
                await self.eventManager.emit(
                    "VWCMinCalibrationComplete",
                    result,
                    haEvent=True
                )
            else:
                await self.eventManager.emit(
                    "LogForClient", {
                        "Name": self.room,
                        "Type": "CSLOG",
                        "Message": "VWC Min Calibration: No valid readings"
                    },
                    haEvent=True
                )
                
        except asyncio.CancelledError:
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "Min Calibration cancelled"
                },
                haEvent=True
            )
            raise

    async def stop_vwc_calibration(self):
        """Stop VWC calibration"""
        
        if self._calibration_task is not None:
            self._calibration_task.cancel()
            try:
                await self._calibration_task
            except asyncio.CancelledError:
                pass
            self._calibration_task = None
            
            await self.eventManager.emit(
                "LogForClient", {
                    "Name": self.room,
                    "Type": "CSLOG",
                    "Message": "Calibration stopped"
                },
                haEvent=True
            )
import asyncio
import logging
from datetime import datetime

from ..actions.DryingActions import DryingActions
from ..data.OGBDataClasses.OGBPublications import (OGBCropSteeringPublication,
                                             OGBDripperAction, OGBECAction,
                                             OGBHydroAction,
                                             OGBHydroPublication,
                                             OGBModePublication,
                                             OGBModeRunPublication,
                                             OGBRetrieveAction,
                                             OGBRetrivePublication)
from ..premium.analytics.OGBAIDataBridge import OGBAIDataBridge
from .hydro.crop_steering.OGBCSManager import OGBCSManager
from .ClosedEnvironmentManager import ClosedEnvironmentManager
from .OGBScriptMode import OGBScriptMode

_LOGGER = logging.getLogger(__name__)


class OGBModeManager:
    def __init__(self, hass, dataStore, event_manager, room):
        self.name = "OGB Mode Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = event_manager
        self.isInitialized = False

        self.CropSteeringManager = OGBCSManager(hass, dataStore, self.event_manager, room)

        # Closed Environment Manager for ambient-enhanced control
        self.closedEnvironmentManager = ClosedEnvironmentManager(dataStore, self.event_manager, room, hass)

        # Drying Actions for drying mode handling
        self.dryingActions = DryingActions(dataStore, self.event_manager, room)

        # Script Mode Manager for custom user scripts
        self.scriptModeManager: OGBScriptMode | None = None

        # AI Data Bridge for cropsteering learning integration
        # Will be started when premium control is active
        self.aiDataBridge = OGBAIDataBridge(hass, self.event_manager, dataStore, room)
        self._ai_bridge_started = False

        self.currentMode = None
        self._hydro_task: asyncio.Task | None = None
        self._retrive_task: asyncio.Task | None = None
        self._crop_steering_task: asyncio.Task | None = None
        self._plant_watering_task: asyncio.Task | None = None
        
        # Deadband hold tracking for smart deadband (2-3 minutes hold time)
        self._deadband_hold_start: float | None = None
        self._deadband_hold_duration: float = 150  # 2.5 minutes (150 seconds)
        self._deadband_active_devices: set = set()  # Track which devices are in deadband hold
        self._is_in_deadband: bool = False
        
        # Device categories for deadband handling
        self._deadband_devices = {
            # Climate-Geräte
            "canHumidify", "canDehumidify", "canHeat", "canCool", "canClimate",
            # Ventilations-Geräte die Außenluft bringen (beeinflussen VPD!)
            "canExhaust", "canIntake", "canWindow"
        }
        # Ventilation (Umluft) wird vom Deadband ignoriert (regelt Mikroklima)
        self._ventilation_devices = {"canVentilate"}

        ## Events
        self.event_manager.on("selectActionMode", self.selectActionMode)

        # Prem
        self.event_manager.on("PremiumCheck", self.handle_premium_modes)

    async def _handle_smart_deadband(self, current_vpd: float, target_vpd: float, deadband: float, mode_name: str):
        """
        Smart Deadband Handler - Mischung aus Soft Minimum und Hold & Fade.
        
        Wenn VPD im Deadband ist:
        1. Sofort: Climate-Geräte auf Minimum (dimmbar) oder Aus (nicht dimmbar)
        2. Hold-Phase: 2-3 Minuten auf Minimum halten
        3. Ventilation läuft weiter
        4. Licht bleibt unberührt
        
        WICHTIG: Wenn VPD den Deadband während der Hold-Zeit verlässt,
        wird der Deadband SOFORT beendet und die Geräte auf Normalzustand zurückgesetzt.
        
        Args:
            current_vpd: Aktueller VPD Wert
            target_vpd: Ziel VPD Wert
            deadband: Deadband Toleranz
            mode_name: Name des Modus (für Logging)
        """
        import time
        from ..data.OGBDataClasses.OGBPublications import OGBActionPublication
        
        now = time.time()
        deviation = abs(current_vpd - target_vpd)
        
        # KRITISCH: Prüfe ob VPD immer noch im Deadband ist
        # Wenn nicht, sofort beenden und normalen Betrieb ermöglichen
        if deviation > deadband:
            if self._is_in_deadband:
                _LOGGER.info(
                    f"{self.room}: VPD {current_vpd} left deadband (target: {target_vpd}, "
                    f"deviation: {deviation:.3f} > deadband: {deadband:.3f}) - "
                    f"exiting deadband immediately"
                )
                self._reset_deadband_state()
            return
        
        # Prüfen ob wir bereits im Deadband sind
        if not self._is_in_deadband:
            # Erster Eintritt in Deadband
            self._is_in_deadband = True
            self._deadband_hold_start = now
            self._deadband_active_devices.clear()
            
            # WICHTIG: Speichere Deadband State im DataStore für andere Komponenten
            self.data_store.setDeep("controlOptionData.deadband.active", True)
            self.data_store.setDeep("controlOptionData.deadband.target_vpd", target_vpd)
            self.data_store.setDeep("controlOptionData.deadband.deadband_value", deadband)
            self.data_store.setDeep("controlOptionData.deadband.entered_at", now)
            self.data_store.setDeep("controlOptionData.deadband.mode", mode_name)
            
            _LOGGER.info(
                f"{self.room}: VPD {current_vpd} entered deadband ±{deadband} of target {target_vpd} - "
                f"starting smart deadband (hold: {self._deadband_hold_duration}s)"
            )
            
            # Emit SmartDeadbandEntered Events für alle Deadband-Geräte
            deadband_device_types = {
                "Heater", "Cooler", "Humidifier", "Dehumidifier", "Climate",
                "Exhaust", "Intake", "Window"
            }
            for device_type in deadband_device_types:
                await self.event_manager.emit("SmartDeadbandEntered", {"deviceType": device_type})
        
        # Berechne verbleibende Hold-Zeit
        hold_elapsed = now - (self._deadband_hold_start or now)
        hold_remaining = max(0, self._deadband_hold_duration - hold_elapsed)
        
        # Aktualisiere DataStore mit verbleibender Zeit
        self.data_store.setDeep("controlOptionData.deadband.hold_remaining", hold_remaining)
        
        # Hole verfügbare Capabilities
        caps = self.data_store.get("capabilities") or {}
        
        # Baue Actions für Smart Deadband
        deadband_actions = []
        devices_dimmed = []
        devices_turned_off = []
        
        # 1. Deadband-Geräte: Minimum (wenn dimmbar) oder Aus (wenn nicht dimmbar)
        # Inkl. Climate-Geräte + Luftaustausch-Geräte (Exhaust, Intake, Window)
        for cap in self._deadband_devices:
            if caps.get(cap, {}).get("state", False):
                # Prüfe ob Gerät dimmbar ist
                is_dimmable = caps.get(cap, {}).get("isDimmable", False)
                
                if is_dimmable:
                    # Dimmbar → auf Minimum (10%)
                    action = OGBActionPublication(
                        capability=cap,
                        action="Reduce",  # Reduce to minimum
                        Name=self.room,
                        message=f"Deadband: {cap} dimmed to minimum (dimmable device)",
                        priority="low"
                    )
                    devices_dimmed.append(cap)
                else:
                    # Nicht dimmbar → Aus
                    action = OGBActionPublication(
                        capability=cap,
                        action="Reduce",  # Turn off
                        Name=self.room,
                        message=f"Deadband: {cap} turned off (non-dimmable device)",
                        priority="low"
                    )
                    devices_turned_off.append(cap)
                
                deadband_actions.append(action)
                self._deadband_active_devices.add(cap)
        
        # 2. Ventilation (Umluft): Weiterlaufen (nur canVentilate, nicht mehr Exhaust/Intake/Window - diese sind jetzt in Deadband!)
        ventilation_running = []
        for cap in self._ventilation_devices:
            if caps.get(cap, {}).get("state", False):
                ventilation_running.append(cap)
        
        # 3. Licht: Unberührt (keine Actions)
        light_status = "unchanged"
        if caps.get("canLight", {}).get("state", False):
            light_status = "running (unchanged)"
        
        # Sende LogForClient mit Deadband Status
        await self.event_manager.emit("LogForClient", {
            "Name": self.room,
            "message": f"Smart Deadband active - Devices reduced, hold: {hold_remaining:.0f}s remaining",
            "VPDStatus": "InDeadband",
            "currentVPD": current_vpd,
            "targetVPD": target_vpd,
            "deadband": deadband,
            "deviation": deviation,
            "holdTimeRemaining": hold_remaining,
            "holdDuration": self._deadband_hold_duration,
            "mode": mode_name,
            "devicesDimmed": devices_dimmed,
            "devicesTurnedOff": devices_turned_off,
            "ventilationRunning": ventilation_running,
            "lightStatus": light_status,
            "deadbandActive": True
        }, haEvent=True, debug_type="INFO")
        
        # Führe Deadband Actions aus (Alle Geräte reduzieren)
        if deadband_actions:
            _LOGGER.info(
                f"{self.room}: Smart Deadband executing {len(deadband_actions)} device actions"
            )
            
            # Mapping von Capability zu korrektem Device-Name für Events
            capability_to_device = {
                'canHeat': 'Heater',
                'canCool': 'Cooler', 
                'canHumidify': 'Humidifier',
                'canDehumidify': 'Dehumidifier',
                'canClimate': 'Climate',
                'canExhaust': 'Exhaust',
                'canIntake': 'Intake',
                'canWindow': 'Window',
            }
            
            # Wir müssen die actions über den event_manager emittieren
            for action in deadband_actions:
                cap = getattr(action, 'capability', None)
                action_type = getattr(action, 'action', None)
                if cap and action_type:
                    # Emit device-specific reduce events with CORRECT device names
                    device_name = capability_to_device.get(cap, cap.replace('can', ''))
                    await self.event_manager.emit(f"Reduce {device_name}", action_type)
        
        # Prüfe ob Hold-Zeit abgelaufen ist und VPD immer noch im Deadband
        if hold_remaining <= 0:
            _LOGGER.info(
                f"{self.room}: Deadband hold time elapsed ({self._deadband_hold_duration}s) - "
                f"checking if still in deadband"
            )
            # Hold-Zeit abgelaufen, prüfe ob VPD immer noch im Deadband
            if deviation <= deadband:
                # Verlängere Hold-Zeit um weitere 2.5 Minuten
                self._deadband_hold_start = now
                _LOGGER.info(
                    f"{self.room}: Still in deadband - extending hold time"
                )
    
    def _reset_deadband_state(self):
        """Reset deadband state when leaving deadband."""
        if self._is_in_deadband:
            _LOGGER.info(f"{self.room}: Leaving deadband - resetting state and restoring devices")
            self._is_in_deadband = False
            self._deadband_hold_start = None
            self._deadband_active_devices.clear()
            
            # WICHTIG: Lösche Deadband State aus DataStore
            self.data_store.setDeep("controlOptionData.deadband.active", False)
            self.data_store.delete("controlOptionData.deadband.target_vpd")
            self.data_store.delete("controlOptionData.deadband.deadband_value")
            self.data_store.delete("controlOptionData.deadband.entered_at")
            self.data_store.delete("controlOptionData.deadband.hold_remaining")
            self.data_store.delete("controlOptionData.deadband.mode")
            
            # Emit SmartDeadbandExited Events für alle Deadband-Geräte
            deadband_device_types = {
                "Heater", "Cooler", "Humidifier", "Dehumidifier", "Climate",
                "Exhaust", "Intake", "Window"
            }
            for device_type in deadband_device_types:
                asyncio.create_task(self.event_manager.emit("SmartDeadbandExited", {"deviceType": device_type}))
            
            # WICHTIG: Emit ein Event damit die Geräte wissen, dass sie aus dem Deadband sind
            # Die Geräte werden dann beim nächsten VPD Control Zyklus normal gesteuert
            _LOGGER.debug(f"{self.room}: Deadband state reset complete - devices will resume normal operation")

    async def selectActionMode(self, Publication):
        """
        Handhabt Änderungen des Modus basierend auf `tentMode`.
        """
        controlOption = self.data_store.get("mainControl")

        if controlOption not in ["HomeAssistant", "Premium"]:
            return False

        # tentMode = self.data_store.get("tentMode")
        tentMode = None
        if isinstance(Publication, OGBModePublication):
            return
        elif isinstance(Publication, OGBModeRunPublication):
            tentMode = Publication.currentMode
            # _LOGGER.debug(f"{self.name}: Run Mode {tentMode} for {self.room}")
        else:
            _LOGGER.debug(
                f"Unbekannter Datentyp: {type(Publication)} - Daten: {Publication}"
            )
            return

        if tentMode == "VPD Perfection":
            await self.handle_vpd_perfection()
        elif tentMode == "VPD Target":
            await self.handle_targeted_vpd()
        elif tentMode == "Drying":
            await self.handle_drying()
        elif tentMode == "MPC Control":
            await self.handle_premium_mode_cycle(tentMode)
        elif tentMode == "PID Control":
            await self.handle_premium_mode_cycle(tentMode)
        elif tentMode == "AI Control":
            await self.handle_premium_mode_cycle(tentMode)
        elif tentMode == "Closed Environment":
            await self.handle_closed_environment()
        elif tentMode == "Script Mode":
            await self.handle_script_mode()
        elif tentMode == "Disabled":
            await self.handle_disabled_mode()

        else:
            _LOGGER.debug(f"{self.name}: Unbekannter Modus {tentMode}")

    async def handle_disabled_mode(self):
        """
        Handhabt den Modus 'Disabled'.
        Stops all active control actions and ensures devices are in safe state.
        """
        _LOGGER.info(f"🔴 {self.room}: Tent mode set to Disabled - stopping all control actions")
        
        # Emit disabled event for other managers to clean up
        await self.event_manager.emit("TentModeDisabled", {"room": self.room})
        
        # Log the disabled state
        await self.event_manager.emit(
            "LogForClient", {"Name": self.room, "Mode": "Disabled"}
        )
        
        # Emit MinMaxControlDisabled for all device types to ensure safe state
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Light"})
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Ventilation"})
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Exhaust"})
        await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": "Intake"})
        
        _LOGGER.info(f"🔴 {self.room}: All control actions disabled")
        
        return None

    async def handle_closed_environment(self):
        """
        Handhabt den Modus 'Closed Environment' für sealed grow chambers (stateless).
        Executes one control cycle with ambient-enhanced logic.
        """
        # Ambient room should never trigger Closed Environment actions - only used as reference
        if self.room.lower() == "ambient":
            _LOGGER.debug(
                f"{self.room}: Ambient room - skipping Closed Environment mode, "
                f"only used as reference for other rooms"
            )
            return

        _LOGGER.debug(f"ModeManager: {self.room} executing Closed Environment cycle")

        # SMART DEADBAND CHECK für Closed Environment (VPD-basiert)
        currentVPD = self.data_store.getDeep("vpd.current")
        targetVPD = self.data_store.getDeep("vpd.targeted") or self.data_store.getDeep("vpd.perfection")
        
        if currentVPD is not None and targetVPD is not None:
            deadband = self.data_store.getDeep("controlOptionData.deadband.vpdTargetDeadband") or 0.05
            deviation = abs(float(currentVPD) - float(targetVPD))
            
            if deviation <= deadband:
                # Im Deadband - Smart Deadband Handler aufrufen
                await self._handle_smart_deadband(float(currentVPD), float(targetVPD), deadband, "Closed Environment")
                # Trotzdem CO2 Control ausführen (wichtig für Closed Environment)
                if self.data_store.getDeep("controlOptions.co2Control"):
                    capabilities = self.data_store.get("capabilities") or {}
                    await self.event_manager.emit("maintain_co2", capabilities)
                return  # Keine normalen Closed Environment Actions ausführen
            else:
                # Außerhalb Deadband - Reset Deadband State
                self._reset_deadband_state()

        # Execute single control cycle (stateless like VPD Perfection)
        await self.closedEnvironmentManager.execute_cycle()

        # Log mode activation
        await self.event_manager.emit(
            "LogForClient", {"Name": self.room, "Mode": "Closed Environment"}
        )

    ## VPD Modes
    async def handle_vpd_perfection(self):
        """
        Handhabt den Modus 'VPD Perfection' und steuert die Geräte basierend auf dem aktuellen VPD-Wert.
        """
        # Ambient room should never trigger VPD actions - only used as reference for Closed Environment
        if self.room.lower() == "ambient":
            _LOGGER.debug(
                f"{self.room}: Ambient room - skipping VPD Perfection mode, "
                f"only used as reference for Closed Environment"
            )
            return

        # Aktuelle VPD-Werte abrufen
        currentVPD = self.data_store.getDeep("vpd.current")
        perfectionVPD = self.data_store.getDeep("vpd.perfection")
        perfectionMinVPD = self.data_store.getDeep("vpd.perfectMin")
        perfectionMaxVPD = self.data_store.getDeep("vpd.perfectMax")

        # Validierung: Alle Werte müssen gesetzt sein
        if currentVPD is None or perfectionMinVPD is None or perfectionMaxVPD is None or perfectionVPD is None:
            _LOGGER.warning(
                f"{self.room}: VPD values not initialized (current={currentVPD}, min={perfectionMinVPD}, max={perfectionMaxVPD}, perfect={perfectionVPD}). Skipping VPD control."
            )
            return

        capabilities = self.data_store.get("capabilities")

        # SMART DEADBAND CHECK für VPD Perfection
        deadband = self.data_store.getDeep("controlOptionData.deadband.vpdDeadband") or 0.05
        deviation = abs(float(currentVPD) - float(perfectionVPD))
        
        if deviation <= deadband:
            # Im Deadband - Smart Deadband Handler aufrufen
            await self._handle_smart_deadband(float(currentVPD), float(perfectionVPD), deadband, "VPD Perfection")
            return  # Keine normalen VPD Actions ausführen
        else:
            # Außerhalb Deadband - Reset Deadband State
            self._reset_deadband_state()

        if currentVPD < perfectionMinVPD:
            _LOGGER.debug(
                f"{self.room}: Current VPD ({currentVPD}) is below minimum ({perfectionMinVPD}). Increasing VPD."
            )
            await self.event_manager.emit("increase_vpd", capabilities)
        elif currentVPD > perfectionMaxVPD:
            _LOGGER.debug(
                f"{self.room}: Current VPD ({currentVPD}) is above maximum ({perfectionMaxVPD}). Reducing VPD."
            )
            await self.event_manager.emit("reduce_vpd", capabilities)
        elif currentVPD != perfectionVPD:
            _LOGGER.debug(
                f"{self.room}: VPD {currentVPD} within range but not at perfection {perfectionVPD}. Fine-tuning."
            )
            await self.event_manager.emit("FineTune_vpd", capabilities)

        if self.data_store.getDeep("controlOptions.co2Control"):
            await self.event_manager.emit("maintain_co2", capabilities)

    async def handle_targeted_vpd(self):
        """
        Handhabt den Modus 'Targeted VPD' mit Toleranz.
        """
        # Ambient room should never trigger VPD actions - only used as reference for Closed Environment
        if self.room.lower() == "ambient":
            _LOGGER.debug(
                f"{self.room}: Ambient room - skipping VPD Target mode, "
                f"only used as reference for Closed Environment"
            )
            return

        _LOGGER.info(f"ModeManager: {self.room} Modus 'Targeted VPD' aktiviert.")
        _LOGGER.debug(
            f"{self.room} VPD Target state: "
            f"current={self.data_store.getDeep('vpd.current')}, "
            f"targeted={self.data_store.getDeep('vpd.targeted')}, "
            f"min={self.data_store.getDeep('vpd.targetedMin')}, "
            f"max={self.data_store.getDeep('vpd.targetedMax')}"
        )

        try:
            # Aktuelle VPD-Werte abrufen
            currentVPD_raw = self.data_store.getDeep("vpd.current")
            targetedVPD_raw = self.data_store.getDeep("vpd.targeted")
            tolerance_raw = self.data_store.getDeep("vpd.tolerance")
            min_vpd_raw = self.data_store.getDeep("vpd.targetedMin")
            max_vpd_raw = self.data_store.getDeep("vpd.targetedMax")

            # Validierung: current/targeted müssen gesetzt sein
            if None in (currentVPD_raw, targetedVPD_raw):
                _LOGGER.warning(
                    f"{self.room}: VPD values not initialized (current={currentVPD_raw}, targeted={targetedVPD_raw}, min={min_vpd_raw}, max={max_vpd_raw}, tolerance={tolerance_raw}). Skipping VPD control."
                )
                return

            currentVPD = float(currentVPD_raw)
            targetedVPD = float(targetedVPD_raw)

            if min_vpd_raw is None or max_vpd_raw is None:
                if tolerance_raw is None:
                    _LOGGER.warning(
                        f"{self.room}: Missing targeted min/max and tolerance is not set. Skipping VPD control."
                    )
                    return

                tolerance_percent = float(tolerance_raw)
                tolerance_value = targetedVPD * (tolerance_percent / 100)
                min_vpd = round(targetedVPD - tolerance_value, 2)
                max_vpd = round(targetedVPD + tolerance_value, 2)

                self.data_store.setDeep("vpd.targetedMin", min_vpd)
                self.data_store.setDeep("vpd.targetedMax", max_vpd)
            else:
                min_vpd = float(min_vpd_raw)
                max_vpd = float(max_vpd_raw)

            # Verfügbare Capabilities abrufen
            capabilities = self.data_store.get("capabilities")

            # Validate capabilities exist
            if not capabilities:
                _LOGGER.warning(
                    f"{self.room}: No capabilities available. Skipping VPD control."
                )
                return

            # SMART DEADBAND CHECK - Wenn VPD im Deadband ist
            deadband = self.data_store.getDeep("controlOptionData.deadband.vpdTargetDeadband") or 0.05
            deviation = abs(currentVPD - targetedVPD)
            
            if deviation <= deadband:
                # Im Deadband - Smart Deadband Handler aufrufen
                await self._handle_smart_deadband(currentVPD, targetedVPD, deadband, "VPD Target")
                return  # Keine normalen VPD Actions ausführen
            else:
                # Außerhalb Deadband - Reset Deadband State
                self._reset_deadband_state()

            # VPD steuern basierend auf der Toleranz (nur außerhalb Deadband)
            if currentVPD < min_vpd:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is below minimum ({min_vpd}). Increasing VPD."
                )
                await self.event_manager.emit("vpdt_increase_vpd", capabilities)
            elif currentVPD > max_vpd:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is above maximum ({max_vpd}). Reducing VPD."
                )
                await self.event_manager.emit("vpdt_reduce_vpd", capabilities)

        except ValueError as e:
            _LOGGER.error(
                f"ModeManager: Fehler beim Konvertieren der VPD-Werte oder Toleranz in Zahlen. {e}"
            )
        except Exception as e:
            _LOGGER.error(
                f"ModeManager: Unerwarteter Fehler in 'handle_targeted_vpd': {e}"
            )

    ## Premium Handle
    async def handle_premium_mode_cycle(self, tent_mode: str):
        """
        Handle premium control mode cycle by triggering DataRelease to API.

        The API is responsible for controller execution (PID/MPC/AI) and returns
        actions asynchronously via websocket. This method only validates access
        and triggers the data send path.
        """
        mainControl = self.data_store.get("mainControl")
        if mainControl != "Premium":
            _LOGGER.debug(
                f"{self.room}: Premium mode '{tent_mode}' selected but mainControl is '{mainControl}' - skipping API controller cycle"
            )
            return

        tent_mode_to_controller = {
            "PID Control": "PID",
            "MPC Control": "MPC",
            "AI Control": "AI",
        }

        controllerType = tent_mode_to_controller.get(tent_mode)
        if not controllerType:
            _LOGGER.warning(f"{self.room}: Unknown premium tent mode '{tent_mode}'")
            return

        # Check feature flags before sending data to API
        subscription_data = self.data_store.get("subscriptionData") or {}
        features = subscription_data.get("features", {})
        controller_feature_map = {
            "PID": "pidControllers",
            "MPC": "mpcControllers",
            "AI": "aiControllers",
        }
        feature_key = controller_feature_map.get(controllerType)
        feature_enabled = features.get(feature_key, False)

        if not feature_enabled:
            _LOGGER.warning(
                f"{self.room}: {controllerType} mode selected but feature '{feature_key}' is not enabled"
            )
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "Warning": f"{controllerType} controller not available in your subscription",
                    "Feature": feature_key,
                    "Enabled": False,
                },
                haEvent=True,
                debug_type="WARNING",
            )
            return

        _LOGGER.info(
            f"{self.room}: Premium controller cycle for {controllerType} - emitting DataRelease to API"
        )
        await self.event_manager.emit("DataRelease", True)

        if controllerType == "AI" and not self._ai_bridge_started:
            await self.start_ai_data_bridge()

    async def handle_premium_modes(self, data):
        """
        Handle premium controller modes (PID, MPC, AI).
        
        Checks feature flags from subscription_data before executing.
        Feature access is determined by:
        1. Kill switch (global disable)
        2. Tenant override (admin dashboard)
        3. Subscription plan features (from API)
        """
        if not isinstance(data, dict):
            return

        controllerTypeRaw = data.get("controllerType")
        if isinstance(controllerTypeRaw, str):
            controllerType = controllerTypeRaw.strip().upper()
        else:
            controllerType = None

        if not controllerType:
            return
            
        # Get subscription data to check feature access
        # subscription_data is stored in datastore after login
        subscription_data = self.data_store.get("subscriptionData") or {}
        features = subscription_data.get("features", {})
        
        # Map controller types to their feature keys (API uses camelCase)
        controller_feature_map = {
            "PID": "pidControllers",
            "MPC": "mpcControllers",
            "AI": "aiControllers",
        }
        
        feature_key = controller_feature_map.get(controllerType)
        if not feature_key:
            _LOGGER.warning(f"{self.room}: Unknown controller type: {controllerType}")
            return
        
        # Check if feature is enabled in subscription
        feature_enabled = features.get(feature_key, False)
        
        if not feature_enabled:
            _LOGGER.warning(
                f"{self.room}: {controllerType} controller requested but feature '{feature_key}' "
                f"is not enabled in subscription (plan features: {list(features.keys())})"
            )
            # Emit event for UI notification
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Warning": f"{controllerType} controller not available in your subscription",
                "Feature": feature_key,
                "Enabled": False
            }, haEvent=True, debug_type="WARNING")
            return
        
        _LOGGER.info(f"{self.room}: Executing {controllerType} controller (feature '{feature_key}' enabled)")
        
        actionData = data.get("actionData")
        if not actionData:
            _LOGGER.debug(f"{self.room}: PremiumCheck received without actionData for {controllerType}")
            return

        if controllerType == "PID":
            await self.event_manager.emit("PIDActions", data)
        elif controllerType == "MPC":
            await self.event_manager.emit("MPCActions", data)
        elif controllerType == "AI":
            await self.event_manager.emit("AIActions", data)

            # Start AI Data Bridge for cropsteering learning when AI control is active
            if not self._ai_bridge_started:
                await self.start_ai_data_bridge()

        return

    async def start_ai_data_bridge(self):
        """Start the AI Data Bridge for cropsteering learning integration"""
        try:
            if not self._ai_bridge_started:
                await self.aiDataBridge.start()
                self._ai_bridge_started = True
                _LOGGER.info(
                    f"{self.room} - AI Data Bridge started for cropsteering learning"
                )
        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to start AI Data Bridge: {e}")

    async def stop_ai_data_bridge(self):
        """Stop the AI Data Bridge"""
        try:
            if self._ai_bridge_started:
                await self.aiDataBridge.stop()
                self._ai_bridge_started = False
                _LOGGER.info(f"{self.room} - AI Data Bridge stopped")
        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to stop AI Data Bridge: {e}")

    ## Drying Mode - Delegated to DryingActions
    async def handle_drying(self):
        """
        Handles 'Drying' mode by delegating to DryingActions.
        Supports ElClassico, 5DayDry, and DewBased algorithms.
        """
        await self.dryingActions.handle_drying()

    ## Script Mode - Custom user-defined automation
    async def handle_script_mode(self):
        """
        Handles 'Script Mode' - fully customizable user scripts.
        Stateless execution like VPD Perfection - called cyclically by ModeManager.
        """
        _LOGGER.debug(f"ModeManager: {self.room} executing Script Mode cycle")

        # Initialize script mode manager if needed
        if self.scriptModeManager is None:
            if hasattr(self, '_ogb_ref') and self._ogb_ref:
                self.scriptModeManager = OGBScriptMode(self._ogb_ref)
            else:
                _LOGGER.warning(f"{self.room}: Script Mode requires OGB reference. Set _ogb_ref first.")
                return

        # Execute script (stateless - like VPD Perfection)
        # Script is loaded from DataStore on each execution
        await self.scriptModeManager.execute()

    def set_ogb_reference(self, ogb):
        """
        Set OGB reference for Script Mode.
        Called by coordinator after initialization.
        """
        self._ogb_ref = ogb
        if self.scriptModeManager is None:
            self.scriptModeManager = OGBScriptMode(ogb)
            _LOGGER.info(f"{self.room}: Script Mode manager initialized")

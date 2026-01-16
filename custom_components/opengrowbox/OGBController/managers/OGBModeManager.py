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

        # AI Data Bridge for cropsteering learning integration
        # Will be started when premium control is active
        self.aiDataBridge = OGBAIDataBridge(hass, self.event_manager, dataStore, room)
        self._ai_bridge_started = False

        self.currentMode = None
        self._hydro_task: asyncio.Task | None = None
        self._retrive_task: asyncio.Task | None = None
        self._crop_steering_task: asyncio.Task | None = None
        self._plant_watering_task: asyncio.Task | None = None

        ## Events
        self.event_manager.on("selectActionMode", self.selectActionMode)

        # Prem
        self.event_manager.on("PremiumCheck", self.handle_premium_modes)

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
            await self.handle_premium_modes(False)
        elif tentMode == "PID Control":
            await self.handle_premium_modes(False)
        elif tentMode == "AI Control":
            await self.handle_premium_modes(False)
        elif tentMode == "Closed Environment":
            await self.handle_closed_environment()
        elif tentMode == "Disabled":
            await self.handle_disabled_mode()

        else:
            _LOGGER.debug(f"{self.name}: Unbekannter Modus {tentMode}")

    async def handle_disabled_mode(self):
        """
        Handhabt den Modus 'Disabled'.
        """
        await self.event_manager.emit(
            "LogForClient", {"Name": self.room, "Mode": "Disabled"}
        )
        return None

    async def handle_closed_environment(self):
        """
        Handhabt den Modus 'Closed Environment' für sealed grow chambers.
        Uses ambient-enhanced control similar to VPD perfection.
        """
        _LOGGER.info(f"ModeManager: {self.room} Modus 'Closed Environment' aktiviert.")

        # Start the closed environment control manager
        await self.closedEnvironmentManager.start_control()

        # Log mode activation
        await self.event_manager.emit(
            "LogForClient", {"Name": self.room, "Mode": "Closed Environment"}
        )

    ## VPD Modes
    async def handle_vpd_perfection(self):
        """
        Handhabt den Modus 'VPD Perfection' und steuert die Geräte basierend auf dem aktuellen VPD-Wert.
        """
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

        # Verfügbare Capabilities abrufen
        capabilities = self.data_store.get("capabilities")

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
                f"{self.room}: Current VPD ({currentVPD}) is within range but not at perfection ({perfectionVPD}). Fine-tuning."
            )
            await self.event_manager.emit("FineTune_vpd", capabilities)
        else:
            _LOGGER.debug(
                f"{self.room}: Current VPD ({currentVPD}) is at perfection ({perfectionVPD}). No action required."
            )

        # Always maintain CO2 within ranges if CO2 control is enabled
        if self.data_store.getDeep("controlOptions.co2Control"):
            await self.event_manager.emit("maintain_co2", capabilities)

    async def handle_targeted_vpd(self):
        """
        Handhabt den Modus 'Targeted VPD' mit Toleranz.
        """
        _LOGGER.info(f"ModeManager: {self.room} Modus 'Targeted VPD' aktiviert.")

        try:
            # Aktuelle VPD-Werte abrufen
            currentVPD_raw = self.data_store.getDeep("vpd.current")
            targetedVPD_raw = self.data_store.getDeep("vpd.targeted")
            tolerance_raw = self.data_store.getDeep("vpd.tolerance")

            # Validierung: Alle Werte müssen gesetzt sein
            if currentVPD_raw is None or targetedVPD_raw is None or tolerance_raw is None:
                _LOGGER.warning(
                    f"{self.room}: VPD values not initialized (current={currentVPD_raw}, targeted={targetedVPD_raw}, tolerance={tolerance_raw}). Skipping VPD control."
                )
                return

            currentVPD = float(currentVPD_raw)
            targetedVPD = float(targetedVPD_raw)
            tolerance_percent = float(tolerance_raw)  # Prozentuale Toleranz (1-25%)

            # Mindest- und Höchstwert basierend auf der Toleranz berechnen
            tolerance_value = targetedVPD * (tolerance_percent / 100)
            min_vpd = targetedVPD - tolerance_value
            max_vpd = targetedVPD + tolerance_value

            # Verfügbare Capabilities abrufen
            capabilities = self.data_store.get("capabilities")

            # VPD steuern basierend auf der Toleranz
            if currentVPD < min_vpd:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is below minimum ({min_vpd}). Increasing VPD."
                )
                await self.event_manager.emit("increase_vpd", capabilities)
            elif currentVPD > max_vpd:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is above maximum ({max_vpd}). Reducing VPD."
                )
                await self.event_manager.emit("reduce_vpd", capabilities)
            elif currentVPD != targetedVPD:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is within range but not at Targeted ({targetedVPD}). Fine-tuning."
                )
                await self.event_manager.emit("FineTune_vpd", capabilities)
            else:
                _LOGGER.debug(
                    f"{self.room}: Current VPD ({currentVPD}) is within tolerance range ({min_vpd} - {max_vpd}). No action required."
                )
                return

        except ValueError as e:
            _LOGGER.error(
                f"ModeManager: Fehler beim Konvertieren der VPD-Werte oder Toleranz in Zahlen. {e}"
            )
        except Exception as e:
            _LOGGER.error(
                f"ModeManager: Unerwarteter Fehler in 'handle_targeted_vpd': {e}"
            )

    ## Premium Handle
    async def handle_premium_modes(self, data):
        """
        Handle premium controller modes (PID, MPC, AI).
        
        Checks feature flags from subscription_data before executing.
        Feature access is determined by:
        1. Kill switch (global disable)
        2. Tenant override (admin dashboard)
        3. Subscription plan features (from API)
        """
        if data == False:
            return
            
        controllerType = data.get("controllerType")
        if not controllerType:
            return
            
        # Get subscription data to check feature access
        # subscription_data is stored in datastore after login
        subscription_data = self.data_store.get("subscriptionData") or {}
        features = subscription_data.get("features", {})
        
        # Map controller types to their feature keys (API uses camelCase)
        controller_feature_map = {
            "PID": "pidControllers",
            "MPC": "mcpControllers",
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
            }, haEvent=True)
            return
        
        _LOGGER.info(f"{self.room}: Executing {controllerType} controller (feature '{feature_key}' enabled)")
        
        if controllerType == "PID":
            await self.event_manager.emit("PIDActions", data)
        elif controllerType == "MPC":
            await self.event_manager.emit("MPCActions", data)
        elif controllerType == "AI":
            # Only emit DataRelease for Premium users
            mainControl = self.data_store.get("mainControl")
            if mainControl == "Premium":
                await self.event_manager.emit("DataRelease", True)
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

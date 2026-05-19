"""CO2 aggregation manager for OpenGrowBox.

Handles CO2 sensor aggregation and writes averaged values to datastore.
Includes CRITICAL SAFETY CHECKS for max CO2 limits.
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class OGBCO2Manager:
    """Manages CO2 sensor data aggregation and SAFETY CHECKS."""

    def __init__(self, hass, data_store, event_manager, room, notificator=None):
        """
        Initialize CO2 Manager.

        Args:
            hass: Home Assistant instance
            data_store: Datastore for storing CO2 values
            event_manager: Event manager for pub/sub
            room: Room name for logging
            notificator: OGBNotificator instance for mobile push notifications
        """
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.notificator = notificator
        self.co2_sensors: List[float] = []
        self.max_sensor_count = 5  # Keep last N readings
        self.last_write_time: Optional[datetime] = None

        # Safety tracking
        self._co2_emergency_active = False
        self._last_co2_value = 0.0
        self._emergency_start_time = None

        # Register for CO2 update events from sensors
        self.event_manager.on("CO2Check", self.handle_co2_update)

        _LOGGER.info(f"{self.room}: OGBCO2Manager initialized with SAFETY CHECKS")

    def handle_co2_update(self, value: float):
        """
        Handle CO2 sensor updates and aggregate values.

        Args:
            value: CO2 reading in ppm
        """
        try:
            # Validate CO2 value
            if not isinstance(value, (int, float)) or value < 0 or value > 5000:
                _LOGGER.warning(
                    f"{self.room}: Invalid CO2 value received: {value}, ignoring"
                )
                return

            # Add to sensor list
            self.co2_sensors.append(float(value))

            # Keep only last N values
            if len(self.co2_sensors) > self.max_sensor_count:
                self.co2_sensors = self.co2_sensors[-self.max_sensor_count:]

            # Calculate average
            avg_co2 = sum(self.co2_sensors) / len(self.co2_sensors)
            self._last_co2_value = avg_co2

            # Write to datastore (same path as hardcoded in Sensor.py)
            self.data_store.setDeep("tentData.co2Level", avg_co2)
            self.data_store.setDeep("tentData.co2", avg_co2)  # Compatibility

            _LOGGER.debug(
                f"{self.room}: CO2 aggregated - count={len(self.co2_sensors)}, "
                f"avg={avg_co2:.1f} ppm"
            )

            # CRITICAL SAFETY CHECKS - Run for EVERY CO2 reading
            asyncio.create_task(self._check_co2_safety(avg_co2))

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error handling CO2 update: {e}")

    async def _check_co2_safety(self, current_co2: float):
        """CRITICAL SAFETY CHECKS for CO2 levels."""
        try:
            co2_control = self.data_store.getDeep("controlOptions.co2Control", False)
            if not co2_control:
                return

            max_co2 = self.data_store.getDeep("controlOptionData.co2ppm.maxPPM", 1300)
            critical_threshold = self.data_store.getDeep("controlOptionData.co2ppm.criticalPPM", 2000)

            max_co2 = float(max_co2) if max_co2 else 1300
            critical_threshold = float(critical_threshold) if critical_threshold else 2000

            # SAFETY CHECK 1: Max CO2 limit (1300ppm) - WARNING
            if current_co2 >= max_co2 and current_co2 <= critical_threshold:
                if not self._co2_emergency_active:
                    _LOGGER.warning(
                        f"{self.room}: CO2 max limit reached: {current_co2:.0f}ppm >= "
                        f"Max {max_co2:.0f}ppm. CO2 pump stopped."
                    )
                    self._co2_emergency_active = True
                    self._emergency_start_time = datetime.now()

                    await self.event_manager.emit(
                        "LogForClient",
                        {
                            "Name": self.room,
                            "message": f"CO2 max limit reached: {current_co2:.0f}ppm. CO2 pump stopped.",
                            "co2Level": "WARNING",
                            "alertType": "CO2_SAFETY",
                            "current_co2": self._last_co2_value,
                            "timestamp": datetime.now().isoformat(),
                        },
                        haEvent=True,
                        debug_type="WARNING",
                    )

                    await self.event_manager.emit(
                        "EmergencyCO2Stop",
                        {"room": self.room, "current_co2": current_co2, "max_co2": max_co2, "reason": "max_limit_exceeded"}
                    )

            # SAFETY CHECK 2: Critical threshold (>2000ppm) - CRITICAL with mobile notification
            elif current_co2 > critical_threshold:
                if not self._co2_emergency_active:
                    _LOGGER.error(
                        f"{self.room}: CO2 CRITICAL EMERGENCY - {current_co2:.0f}ppm "
                        f"exceeds critical threshold ({critical_threshold:.0f}ppm)!"
                    )
                    self._co2_emergency_active = True
                    self._emergency_start_time = datetime.now()

                    await self._emit_co2_alert(
                        "CRITICAL",
                        f"CO2 at {current_co2:.0f}ppm exceeds CRITICAL threshold! "
                        f"CO2 pump OFF! Check system IMMEDIATELY!",
                        send_notification=True
                    )

                    await self.event_manager.emit(
                        "EmergencyCO2Stop",
                        {"room": self.room, "current_co2": current_co2, "critical_threshold": critical_threshold, "reason": "critical_threshold_exceeded"}
                    )

            # SAFETY CHECK 3: CO2 returned to safe levels
            elif self._co2_emergency_active and current_co2 < (max_co2 * 0.9):
                _LOGGER.warning(f"{self.room}: CO2 returned to safe levels: {current_co2:.0f}ppm. Emergency cleared.")
                self._co2_emergency_active = False
                self._emergency_start_time = None

                await self._emit_co2_alert(
                    "INFO",
                    f"CO2 safety alert cleared. Current: {current_co2:.0f}ppm. System returned to normal.",
                    send_notification=False
                )

                await self.event_manager.emit("CO2Safe", {"room": self.room, "current_co2": current_co2, "max_co2": max_co2})

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error in CO2 safety check: {e}")

    async def _emit_co2_alert(self, level: str, message: str, send_notification: bool = False):
        """Emit CO2 alert to UI and optionally mobile."""
        try:
            await self.event_manager.emit(
                "LogForClient",
                {
                    "Name": self.room,
                    "message": message,
                    "co2Level": level,
                    "alertType": "CO2_SAFETY",
                    "current_co2": self._last_co2_value,
                    "timestamp": datetime.now().isoformat(),
                },
                haEvent=True,
                debug_type="ERROR" if level == "CRITICAL" else "WARNING",
            )

            if send_notification and self.notificator:
                await self.notificator.critical(message=message, title=f"OGB {self.room}: CO2 EMERGENCY")

            if level == "CRITICAL":
                _LOGGER.error(f"{self.room}: {message}")
            else:
                _LOGGER.warning(f"{self.room}: {message}")

        except Exception as e:
            _LOGGER.error(f"{self.room}: Error emitting CO2 alert: {e}")

    def reset_sensor_data(self):
        """Reset sensor data buffer."""
        self.co2_sensors = []
        self._co2_emergency_active = False
        self._emergency_start_time = None
        _LOGGER.debug(f"{self.room}: CO2 sensor data and emergency state reset")

    def is_co2_emergency(self) -> bool:
        """Check if CO2 is currently in emergency state."""
        return self._co2_emergency_active

    def get_last_co2(self) -> float:
        """Get last aggregated CO2 value."""
        return self._last_co2_value

    # =================================================================
    # CENTRALIZED CO2 ACTION DECISION
    # =================================================================

    async def decide_co2_action(self, mode: str, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Zentrale CO2-Aktions-Entscheidung.
        
        Alle CO2-Steuerungslogik (VPD Perfection + Closed Environment) 
        wird hier zentral koordiniert.
        
        Args:
            mode: "VPD" oder "CLOSED" 
            capabilities: Alle verfuegbaren Capabilities
            
        Returns:
            Liste von Action-Maps (leere Liste wenn keine Aktion noetig)
        """
        action_map = []
        
        try:
            # 1. Pruefen ob CO2-Control aktiviert
            co2_control_enabled = self.data_store.getDeep("controlOptions.co2Control", False)
            if not co2_control_enabled:
                _LOGGER.debug(f"{self.room}: CO2 control disabled, skipping")
                return []
            
            # 2. Aktuellen CO2-Wert holen
            current_co2 = self._last_co2_value
            if current_co2 is None or current_co2 == 0:
                _LOGGER.debug(f"{self.room}: No CO2 reading available")
                return []
            
            # 3. Targets (min/max ppm) holen
            co2_target_min = self.data_store.getDeep("controlOptionData.co2ppm.minPPM", 800)
            co2_target_max = self.data_store.getDeep("controlOptionData.co2ppm.maxPPM", 1500)
            
            co2_target_min = float(co2_target_min) if co2_target_min else 800
            co2_target_max = float(co2_target_max) if co2_target_max else 1500
            
            # 4. Licht-Status pruefen
            is_light_on = bool(self.data_store.getDeep("isPlantDay.islightON", False))
            
            # 5. Safety-Checks durchfuehren (immer zuerst!)
            if self._co2_emergency_active:
                _LOGGER.warning(f"{self.room}: CO2 emergency active - blocking all CO2 injection")
                return []
            
            # 6. Target-Logik basierend auf Modus
            if mode == "CLOSED":
                # Closed Environment: Target-basierte Steuerung
                action_map = await self._decide_closed_environment_action(
                    current_co2, co2_target_min, co2_target_max, 
                    is_light_on, capabilities
                )
            elif mode == "VPD":
                # VPD Perfection: Einfaches ein/aus basierend auf Licht
                action_map = await self._decide_vpd_mode_action(
                    current_co2, co2_target_min, co2_target_max,
                    is_light_on, capabilities
                )
            else:
                _LOGGER.warning(f"{self.room}: Unknown CO2 mode: {mode}")
                return []
            
            if action_map:
                _LOGGER.info(
                    f"{self.room}: CO2 action decided - mode={mode}, "
                    f"current={current_co2:.0f}ppm, min={co2_target_min:.0f}, max={co2_target_max:.0f}, "
                    f"actions={[a.get('capability') + ':' + a.get('action') for a in action_map]}"
                )
            
            return action_map
            
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error in decide_co2_action: {e}")
            return []
    
    async def _decide_closed_environment_action(
        self, current_co2: float, co2_target_min: float, co2_target_max: float,
        is_light_on: bool, capabilities: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Entscheidet CO2-Action fuer Closed Environment Modus.
        
        Logic:
        - CO2 < min und Licht an -> Injizieren
        - CO2 > max -> Reduzieren (auch bei Licht aus - Safety)
        - CO2 im Target-Bereich -> Nichts tun
        """
        action_map = []
        action_message = "Closed Environment CO2 Maintenance"
        
        # Emergency high CO2 overrides normal reduction
        co2_emergency_high = self.data_store.getDeep("controlOptionData.co2ppm.criticalPPM", 2000)
        if current_co2 > float(co2_emergency_high):
            _LOGGER.warning(f"CO2 emergency high: {current_co2} > {co2_emergency_high}, forcing ventilation")
            # Use exhaust first to actually remove CO2-laden air
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Increase", action_message))
            elif capabilities.get("canVentilate", {}).get("state", False):
                action_map.append(self._create_action("canVentilate", "Increase", action_message))
            return action_map
        
        # Only inject CO2 when lights are ON (photosynthesis active)
        if current_co2 < co2_target_min:
            if is_light_on:
                _LOGGER.debug("CO2 below min, lights ON - injecting CO2")
                if capabilities.get("canCO2", {}).get("state", False):
                    action_map.append(self._create_action("canCO2", "Increase", action_message))
            else:
                _LOGGER.debug("CO2 below min but lights OFF - skipping CO2 injection")
        
        # Always reduce CO2 if too high, even at night (safety first)
        elif current_co2 > co2_target_max:
            _LOGGER.debug("CO2 above max - reducing")
            if capabilities.get("canExhaust", {}).get("state", False):
                action_map.append(self._create_action("canExhaust", "Increase", action_message))
            elif capabilities.get("canVentilate", {}).get("state", False):
                action_map.append(self._create_action("canVentilate", "Increase", action_message))
        
        return action_map
    
    async def _decide_vpd_mode_action(
        self, current_co2: float, co2_target_min: float, co2_target_max: float,
        is_light_on: bool, capabilities: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Entscheidet CO2-Action fuer VPD Perfection Modus.
        
        Logic:
        - Licht an und CO2 unter max -> Injizieren (fuer Photosynthese)
        - Licht aus oder CO2 ueber max -> Reduzieren/Ausschalten
        """
        action_map = []
        action_message = "VPD Perfection CO2"
        
        if is_light_on:
            if current_co2 < co2_target_max:
                # Licht an und CO2 unter Max -> Injizieren
                if capabilities.get("canCO2", {}).get("state", False):
                    action_map.append(self._create_action("canCO2", "Increase", action_message))
            else:
                # Licht an aber CO2 ueber Max -> Reduzieren
                _LOGGER.debug(f"CO2 above max ({current_co2} > {co2_target_max}) despite light on - reducing")
                if capabilities.get("canCO2", {}).get("state", False):
                    action_map.append(self._create_action("canCO2", "Reduce", action_message))
        else:
            # Licht aus -> CO2 ausschalten
            if capabilities.get("canCO2", {}).get("state", False):
                action_map.append(self._create_action("canCO2", "Reduce", action_message))
                _LOGGER.debug(f"{self.room}: Night mode - CO2 off")
        
        return action_map
    
    def _create_action(self, capability: str, action: str, message: str) -> Dict[str, Any]:
        """Erstellt eine Action-Map."""
        return {
            "capability": capability,
            "action": action,
            "message": message,
            "room": self.room,
        }

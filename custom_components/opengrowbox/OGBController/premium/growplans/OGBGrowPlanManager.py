"""
OGB Grow Plan Manager is Part of the Premium Code it needs a working subscription.
"""
import os
import logging
import asyncio
import aiohttp
import uuid
import json
import base64
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from ...utils.Premium.ogb_state import _save_state_securely, _remove_state_file, _load_state_securely
from ...utils.sensorUpdater import update_entity, _update_specific_select

_LOGGER = logging.getLogger(__name__)


# Mapping of (entity_id, data_store_key) that the grow plan writes to on
# activation. Used for both pre-plan snapshot (capture) and restore on
# pause/stop. Keep in sync with _update_entities_from_week_data().
_GROW_PLAN_ENTITY_SNAPSHOT_KEYS: list[tuple[str, str]] = [
    ("select.ogb_tentmode", "tentMode"),
    ("select.ogb_plantstage", "plantStage"),
    ("number.ogb_maxtemp", "tentData.maxTemp"),
    ("number.ogb_mintemp", "tentData.minTemp"),
    ("number.ogb_maxhum", "tentData.maxHumidity"),
    ("number.ogb_minhum", "tentData.minHumidity"),
    ("number.ogb_vpdtarget", "tentData.targetVPD"),
    ("number.ogb_co2targetvalue", "tentData.targetCO2"),
    ("number.ogb_co2maxvalue", "tentData.maxCO2"),
    ("number.ogb_co2minvalue", "tentData.minCO2"),
    ("select.ogb_co2_control", "tentControls.co2Control"),
    ("time.ogb_lightontime", "isPlantDay.lightOnTime"),
    ("time.ogb_lightofftime", "isPlantDay.lightOffTime"),
    ("time.ogb_sunrisetime", "isPlantDay.sunRiseTime"),
    ("time.ogb_sunsettime", "isPlantDay.sunSetTime"),
    ("number.ogb_light_volt_min", "DeviceMinMax.Light.minVoltage"),
    ("number.ogb_light_volt_max", "DeviceMinMax.Light.maxVoltage"),
    ("select.ogb_holdvpdnight", "tentControls.nightVpdHold"),
    ("select.ogb_vpd_devicedampening", "tentControls.deviceDampening"),
    ("select.ogb_vpd_determination", "tentControls.vpdDetermination"),
]

class OGBGrowPlanManager:
    def __init__(self, hass, dataStore, eventManager, room, ws_client=None):
        self.name = "OGB Grow Plan Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager
        self.ws_client = ws_client  # WebSocket client for API requests

        self.managerActive = None

        # Aktuelles Datum
        self.currentDate = datetime.now(timezone.utc)

        self.is_premium = False

        # GrowPlans
        self.active_grow_plan = None
        self.active_grow_plan_id = None
        self.plan_start_date = None
        self.total_weeks = None
        self.current_week = None
        
        
        self.current_week_data = None
        
        # System initialization tracking
        self._is_system_ready = False
        self._pending_updates = []


        # Timer für tägliche Aktualisierungen
        self._daily_update_task = None
        self._init_task = None
        self._ha_unsubscribers = []
        self._event_bindings = []

        self._register_event_listener("SystemReady", self._on_system_ready)

        # Register WebappGrowPlanStatusChange SYNCHRONOUSLY so the listener
        # is in place before any WS event can fire (init() runs in a task and
        # could miss early events). The other (less critical) listeners get
        # added in init() via _setup_event_listeners().
        self._register_event_listener("WebappGrowPlanStatusChange", self._on_grow_plan_status_change)
        self._register_event_listener("ogb_growplan_command", self._on_growplan_command)

        self._init_task = asyncio.create_task(self.init())
            
    async def init(self):
        """Initialize Grow Plan Manager"""
        self._setup_event_listeners()
        # Register SystemReady listener to know when initialization is complete

        _LOGGER.warning(f"🌱 {self.room} SystemReady listener registered")
        await self._start_daily_update_timer()
        _LOGGER.debug(f"OGB Grow Plan Manager initialized for room: {self.room}")

    def _setup_event_listeners(self):
        """Hook for future listener registration.

        The critical listeners (WebappGrowPlanStatusChange and
        ogb_growplan_command) are registered synchronously in __init__ to
        avoid a race with early WS events. This method is kept so the
        init() flow's call site still works without raising.
        """
        _LOGGER.debug(f"🌱 {self.room} _setup_event_listeners called (no-op)")

    async def _on_system_ready(self, data):
        """Handle SystemReady event - system initialization is complete."""
        event_room = data.get("room") if isinstance(data, dict) else None
        if event_room and event_room.lower() != self.room.lower():
            return

        self._is_system_ready = True

        # If the grow plan is not active (e.g. paused while HA was starting or
        # paused before the restart), discard any pending updates that were
        # queued before the system was ready. Applying them would overwrite
        # user setpoints with stale grow plan data.
        is_active = self.managerActive and self.data_store.get("growManagerActive")
        if not is_active:
            if self._pending_updates:
                _LOGGER.debug(
                    f"🌱 {self.room} SystemReady received but grow plan is not active - "
                    f"clearing {len(self._pending_updates)} pending updates"
                )
                self._pending_updates.clear()
            else:
                _LOGGER.debug(f"🌱 {self.room} SystemReady received - no pending updates")
            return

        _LOGGER.debug(
            f"🌱 {self.room} SystemReady received - processing {len(self._pending_updates)} pending updates"
        )

        # Process any pending updates
        while self._pending_updates:
            update_type = self._pending_updates.pop(0)
            if update_type == "_update_entities_from_week_data":
                await self._update_entities_from_week_data()
            elif update_type == "_update_current_week":
                await self._update_current_week()

    def _is_grow_plan_active(self) -> bool:
        """Return True only when both the manager flag and the data store agree
        that a grow plan is currently active.
        """
        return bool(self.managerActive) and bool(self.data_store.get("growManagerActive"))
  
    def _register_bus_listener(self, event_name, callback):
        unsub = self.hass.bus.async_listen(event_name, callback)
        self._ha_unsubscribers.append(unsub)
    
    def _register_event_listener(self, event_name, callback):
        self.event_manager.on(event_name, callback)
        self._event_bindings.append((event_name, callback))        
            
    async def _capture_pre_plan_snapshot(self):
        """Capture current entity/state values before a grow plan is activated.

        Stores the snapshot under growPlan.snapshot so it can be restored when
        the plan is paused or stopped.
        """
        try:
            snapshot = {}
            room_normalized = self.room.lower().replace(" ", "_")
            for entity_base, data_store_key in _GROW_PLAN_ENTITY_SNAPSHOT_KEYS:
                try:
                    domain = entity_base.split(".")[0]
                    base = entity_base.split(".", 1)[1]
                    full_entity_id = f"{domain}.{base}_{room_normalized}"
                    state_obj = self.hass.states.get(full_entity_id)
                    if state_obj is not None and state_obj.state not in (None, "unavailable", "unknown"):
                        value = state_obj.state
                        # Convert numbers/selects to proper types
                        if domain in ("number", "input_number"):
                            try:
                                value = float(value)
                            except (ValueError, TypeError):
                                pass
                        snapshot[data_store_key] = value
                except Exception as e:
                    _LOGGER.debug(f"🌱 {self.room} Snapshot skip {data_store_key}: {e}")

            # Also capture data_store values as fallback
            for _, data_store_key in _GROW_PLAN_ENTITY_SNAPSHOT_KEYS:
                if data_store_key not in snapshot:
                    existing = self.data_store.getDeep(data_store_key)
                    if existing is not None:
                        snapshot[data_store_key] = existing

            self.data_store.setDeep("growPlan.snapshot", snapshot)
            _LOGGER.info(f"🌱 {self.room} Pre-plan snapshot captured: {snapshot}")
        except Exception as e:
            _LOGGER.error(f"🌱 {self.room} Failed to capture pre-plan snapshot: {e}")

    async def _restore_pre_plan_snapshot(self):
        """Restore entity/state values captured before the grow plan was activated.

        Called when the plan is paused or stopped so the tent falls back to the
        previous user-defined setpoints instead of keeping the plan values.
        """
        try:
            snapshot = self.data_store.getDeep("growPlan.snapshot") or {}
            if not snapshot:
                _LOGGER.info(f"🌱 {self.room} No pre-plan snapshot to restore")
                return False

            _LOGGER.info(f"🌱 {self.room} Restoring pre-plan snapshot: {snapshot}")
            restored_any = False

            for entity_base, data_store_key in _GROW_PLAN_ENTITY_SNAPSHOT_KEYS:
                value = snapshot.get(data_store_key)
                if value is None:
                    continue

                # Update data_store first
                self.data_store.setDeep(data_store_key, value)

                # Update HA entity
                try:
                    result = await update_entity(entity_base, value, self.room, self.hass)
                    if result:
                        restored_any = True
                except Exception as e:
                    _LOGGER.debug(f"🌱 {self.room} Restore entity {entity_base} failed: {e}")

            # Clear snapshot after successful restore so a future activation
            # captures fresh values.
            self.data_store.setDeep("growPlan.snapshot", None)
            return restored_any
        except Exception as e:
            _LOGGER.error(f"🌱 {self.room} Failed to restore pre-plan snapshot: {e}")
            return False

    async def _start_daily_update_timer(self):
        """Startet Timer für tägliche Aktualisierungen (00:00 und 12:00)"""
        if self._daily_update_task:
            self._daily_update_task.cancel()
        
        self._daily_update_task = asyncio.create_task(self._daily_update_loop())

    async def _daily_update_loop(self):
        """Tägliche Aktualisierung der aktuellen Woche um 00:00 und 12:00"""

        if sel.room.lower == "ambient":
            return


        while True:
            try:
                now = datetime.now(timezone.utc)
                
                # Berechne nächsten Zeitpunkt (00:00 oder 12:00)
                if now.hour < 12:
                    # Nächster Zeitpunkt: 12:00 heute
                    next_update = now.replace(hour=12, minute=0, second=0, microsecond=0)
                else:
                    # Nächster Zeitpunkt: 00:00 morgen
                    next_update = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                
                # Berechne Sekunden bis zum nächsten Update
                sleep_seconds = (next_update - now).total_seconds()
                _LOGGER.warning(f"🌱 {self.room} Nächstes API-Update um {next_update.strftime('%H:%M')} Uhr (in {sleep_seconds/3600:.1f} Stunden)")
                
                await asyncio.sleep(sleep_seconds)
                
                # Aktualisiere aktuelles Datum und Woche
                self.currentDate = datetime.now(timezone.utc)
                
                # Nur aktualisieren wenn ein GrowPlan aktiv ist
                if self.managerActive and self.active_grow_plan:
                    _LOGGER.warning(f"🌱 {self.room} Geplantes API-Update um {self.currentDate.strftime('%H:%M')} (Plan aktiv)")
                else:
                    _LOGGER.warning(f"🌱 {self.room} Geplantes Update übersprungen - kein aktiver Grow Plan")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Fehler im täglichen Update-Loop: {e}")
                await asyncio.sleep(3600)  # Warte 1 Stunde bei Fehlern

    ## Workers
    async def _eval_plan_settings(self, plan_data: Dict[str, Any]):
        """
        Prüft den aktiven Plan und triggert Events für relevante Parameter.
        Wird beim Aktivieren eines Plans oder täglichen Update ausgeführt.
        """
        try:
            # Wähle aktuelle Woche aus
            week = self.current_week or 1
            week_settings = self.get_week_data_by_number(week)

            if not week_settings:
                _LOGGER.warning(f"{self.room}: Keine WeekData für Woche {week} gefunden")
                return

            _LOGGER.warning(f"{self.room}: Evaluating Grow Plan Settings for Week {week}: {week_settings}")

            # Jetzt einzelne Prüfungen durchführen
            await self._emit_if_changed("light", week_settings, ["lightStart", "lightEnd", "lightIntensity"])
            await self._emit_if_changed("climate", week_settings, ["temperature", "humidity", "vpd", "co2"])
            await self._emit_if_changed("feed", week_settings, ["A", "B", "C", "EC", "PH"])
            await self._emit_if_changed("mode", week_settings, ["FullAutomatic", "feedControl", "co2Control"])

        except Exception as e:
            _LOGGER.error(f"{self.room}: Fehler bei _eval_plan_settings: {e}")

    async def _emit_if_changed(self, category: str, week_settings: Dict[str, Any], keys: list[str]):
        """
        Prüft bestimmte Werte und sendet Event/DataStore-Update nur, wenn sich etwas geändert hat.
        """
        try:
            changes = {}
            for key in keys:
                new_value = week_settings.get(key)
                old_value = self.data_store.get(f"{category}_{key}")

                # Nur aktualisieren, wenn es sich geändert hat oder noch nicht gesetzt ist
                if new_value is not None and new_value != old_value:
                    self.data_store.set(f"{category}_{key}", new_value)
                    changes[key] = new_value

            # Nur senden, wenn sich Werte wirklich geändert haben
            if changes:
                event_name = f"growplan_update_{category}"
                _LOGGER.warning(f"{self.room}: Änderungen erkannt in {category} → {changes}")
                await self.event_manager.emit(event_name, {"room": self.room, "changes": changes})

        except Exception as e:
            _LOGGER.error(f"{self.room}: Fehler bei _emit_if_changed ({category}): {e}")

    async def activate_grow_plan_by_id(self, plan_id: str, plan_data: dict = None):
        """Aktiviert einen Grow Plan"""
        try:
            # Validate plan_id - handle case where plan_id is a dict
            if isinstance(plan_id, dict):
                _LOGGER.warning(f"{self.room}: plan_id is a dict, extracting id field")
                plan_data = plan_id
                plan_id = plan_id.get("id")

            if not plan_id:
                _LOGGER.warning(f"{self.room}: No plan_id provided for activation")
                return False

            # Idempotent: if the same plan is already active, just re-evaluate week
            if self.managerActive and self.active_grow_plan_id == plan_id:
                _LOGGER.debug(f"🌱 {self.room} Plan {plan_id} already active – re-evaluating week")
                await self._update_current_week()
                return True

            # Aktiviere den Plan
            self.active_grow_plan_id = plan_id
            self.active_grow_plan = plan_data

            # Capture current tent values BEFORE overwriting them with plan values.
            await self._capture_pre_plan_snapshot()

            # Setze Startdatum aus Plan-Daten (nicht datetime.now!)
            start_date_str = None
            if plan_data:
                start_date_str = plan_data.get("startDate") or plan_data.get("start_date")
            if start_date_str:
                try:
                    # Handle ISO format with Z suffix
                    if isinstance(start_date_str, str):
                        start_date_str = start_date_str.replace('Z', '+00:00')
                        parsed_date = datetime.fromisoformat(start_date_str)
                        # WICHTIG: Sicherstellen dass das Datum timezone-aware ist
                        if parsed_date.tzinfo is None:
                            parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                        self.plan_start_date = parsed_date
                    else:
                        self.plan_start_date = datetime.now(timezone.utc)
                except (ValueError, TypeError) as e:
                    _LOGGER.error(f"🌱 {self.room} Could not parse startDate: {start_date_str}, using current time. Error: {e}")
                    self.plan_start_date = datetime.now(timezone.utc)
            else:
                _LOGGER.error(f"🌱 {self.room} No startDate in plan, using current time")
                self.plan_start_date = datetime.now(timezone.utc)
            
            # Speichere Zustand
            await self.event_manager.emit("SaveRequest",self.room) 
            
            # Aktualisiere aktuelle Woche (triggert API-Anfrage wenn keine Daten)
            await self._update_current_week()
            
            _LOGGER.info(f"🌱 {self.room} Grow Plan aktiviert: {plan_id}, Start: {self.plan_start_date}, Woche: {self.current_week}")
            
            self.managerActive = True
            self.data_store.set("growManagerActive",True)
            
            # Event senden
            self.hass.bus.async_fire("grow_plan_activated", {
                "plan_id": plan_id,
                "start_date": self.plan_start_date.isoformat(),
                "current_week": self.current_week
            })
            
            return True

        except Exception as e:
            _LOGGER.error(f"Fehler bei der Aktivierung des Grow Plans: {e}")
            return False

    async def deactivate_grow_plan(self, clear_plan_data: bool = True) -> bool:
        """Deactivate the currently active grow plan.

        Flips the `growManagerActive` flag off so OGBDatastore.get_active_value
        and OGBConfigurationManager._plant_stage_to_vpd naturally fall back to
        the regular tentData values (they were never overwritten while the
        plan was active, thanks to the grow_plan_active gate in the
        configuration manager). No entity snapshot/restore is needed.

        Args:
            clear_plan_data: if True (default), also clear active_grow_plan,
                plan_start_date, current_week and the data store keys
                `growPlan.currentWeekData` / `growPlan.currentWeek`. Pass
                False to keep the plan data around (e.g. for a pause where
                the user might resume).
        """
        try:
            if not self.managerActive and not self.active_grow_plan_id:
                _LOGGER.debug(f"🌱 {self.room} deactivate_grow_plan: no active plan")
                return True

            was_active = self.managerActive
            plan_id = self.active_grow_plan_id
            self.managerActive = False
            self.data_store.set("growManagerActive", False)

            # Restore the pre-plan snapshot so the tent falls back to the
            # previous user-defined setpoints.
            restored = await self._restore_pre_plan_snapshot()

            # Trigger a configuration rebuild so all managers pick up the
            # restored tentData values instead of the grow plan values.
            current_plant_stage = self.data_store.get("plantStage") or "EarlyVeg"
            await self.event_manager.emit("PlantStageChange", current_plant_stage)
            _LOGGER.info(
                f"🌱 {self.room} Emitted PlantStageChange after restore to refresh setpoints"
            )

            if clear_plan_data:
                self.active_grow_plan = None
                self.active_grow_plan_id = None
                self.plan_start_date = None
                self.total_weeks = None
                self.current_week = None
                self.current_week_data = None
                self.data_store.setDeep("growPlan.currentWeekData", None)
                self.data_store.setDeep("growPlan.currentWeek", None)

            # Pause the daily update task if no plan remains
            if clear_plan_data and self._daily_update_task and not self._daily_update_task.done():
                self._daily_update_task.cancel()
                self._daily_update_task = None
                self._start_daily_update_timer()  # restart as idle

            await self.event_manager.emit("SaveRequest", self.room)
            self.hass.bus.async_fire(
                "grow_plan_deactivated",
                {"room": self.room, "plan_id": plan_id, "was_active": was_active},
            )
            _LOGGER.info(
                f"🌱 {self.room} Grow plan deactivated (plan_id={plan_id}, "
                f"clear_plan_data={clear_plan_data})"
            )
            return True
        except Exception as e:
            _LOGGER.error(f"🌱 {self.room} deactivate_grow_plan failed: {e}", exc_info=True)
            return False

    async def _on_grow_plan_status_change(self, data):
        """Handle WebappGrowPlanStatusChange event from prem-api WebSocket.

        Emitted by the SecureWebSocketClient whenever the webapp changes
        a plan's status. Maps webapp actions to manager state transitions.
        """
        try:
            if not isinstance(data, dict):
                return
            event_room = data.get("room")
            if event_room and event_room.lower() != self.room.lower():
                return
            _LOGGER.info(
                f"🌱 {self.room} _on_grow_plan_status_change FIRED: {data}"
            )
        except Exception as e:
            _LOGGER.error(
                f"🌱 {self.room} _on_grow_plan_status_change outer try failed: {e}",
                exc_info=True,
            )
            return

        try:
            return await self._handle_status_change(data)
        except Exception as e:
            _LOGGER.error(
                f"🌱 {self.room} _on_grow_plan_status_change handler failed: {e}",
                exc_info=True,
            )

    async def _handle_status_change(self, data):
        action = data.get("action")
        plan_id = data.get("plan_id")
        plan_name = data.get("plan_name")
        status = data.get("status")
        _LOGGER.info(
            f"🌱 {self.room} Grow plan status change: action={action} plan_id={plan_id} "
            f"status={status} name={plan_name}"
        )

        if action in ("activated", "activate_plan", "resumed", "resume_plan"):
            is_resume = action in ("resumed", "resume_plan")
            if self.active_grow_plan_id == plan_id and self.managerActive and not is_resume:
                # Already active and not a resume – nothing to do.
                _LOGGER.info(f"🌱 {self.room} Grow plan already active (plan_id={plan_id})")
                return
            if self.active_grow_plan_id == plan_id and self.managerActive and is_resume:
                # Plan is already active; treat resume as a state refresh.
                self.managerActive = True
                self.data_store.set("growManagerActive", True)
                self.hass.bus.async_fire(
                    "grow_plan_resumed", {"room": self.room, "plan_id": plan_id}
                )
                _LOGGER.info(f"🌱 {self.room} Grow plan resumed (plan_id={plan_id})")
                return
            plans = self.data_store.getDeep("growPlans") or []
            plan_data = next((p for p in plans if p.get("id") == plan_id), None)

            # Webapp resume events often only carry plan_id/name. Try to enrich
            # from the last known activePlan snapshot stored by new_grow_plans.
            if not plan_data:
                stored_active = self.data_store.getDeep("growPlan.activePlan")
                if isinstance(stored_active, dict) and stored_active.get("id") == plan_id:
                    plan_data = stored_active
                    _LOGGER.debug(
                        f"🌱 {self.room} Using stored activePlan snapshot for {plan_id}"
                    )

            if not plan_data and data:
                # Fall back to building a minimal plan_data from the event
                plan_data = {
                    "id": plan_id,
                    "name": plan_name,
                    "startDate": data.get("startDate"),
                    "weeks": [],
                }
            if plan_data:
                await self.activate_grow_plan_by_id(plan_id, plan_data)
                if is_resume:
                    self.hass.bus.async_fire(
                        "grow_plan_resumed", {"room": self.room, "plan_id": plan_id}
                    )
                    _LOGGER.info(f"🌱 {self.room} Grow plan resumed after activation (plan_id={plan_id})")
                else:
                    self.hass.bus.async_fire(
                        "grow_plan_activated", {"room": self.room, "plan_id": plan_id}
                    )
                    _LOGGER.info(f"🌱 {self.room} Grow plan activated (plan_id={plan_id})")
            else:
                _LOGGER.warning(
                    f"🌱 {self.room} Cannot activate {plan_id} – not in local growPlans"
                )

        elif action in ("paused", "pause_plan"):
            # Freeze: keep plan data, just disable the active flag so
            # OGB falls back to tentData targets. Restore previous values.
            self.managerActive = False
            self.data_store.set("growManagerActive", False)

            # Discard any pending entity updates that may have been queued
            # while the system was still initializing. They must not be applied
            # once the plan is paused.
            if self._pending_updates:
                _LOGGER.debug(
                    f"🌱 {self.room} Clearing {len(self._pending_updates)} pending updates on pause"
                )
                self._pending_updates.clear()

            await self._restore_pre_plan_snapshot()

            # Trigger a configuration rebuild so all managers pick up the
            # restored tentData values instead of the grow plan values.
            current_plant_stage = self.data_store.get("plantStage") or "EarlyVeg"
            await self.event_manager.emit("PlantStageChange", current_plant_stage)
            _LOGGER.info(
                f"🌱 {self.room} Emitted PlantStageChange after pause to refresh setpoints"
            )

            self.hass.bus.async_fire(
                "grow_plan_paused", {"room": self.room, "plan_id": plan_id}
            )
            _LOGGER.info(f"🌱 {self.room} Grow plan paused (plan_id={plan_id})")

        elif action in ("stopped", "stop_plan"):
            await self.deactivate_grow_plan(clear_plan_data=True)
            self.hass.bus.async_fire(
                "grow_plan_stopped", {"room": self.room, "plan_id": plan_id}
            )
            _LOGGER.info(f"🌱 {self.room} Grow plan stopped (plan_id={plan_id})")

        elif action == "switched":
            # Treat as stop + activate with the new plan
            new_plan_id = data.get("new_plan_id") or data.get("plan_id")
            await self.deactivate_grow_plan(clear_plan_data=True)
            if new_plan_id:
                plans = self.data_store.getDeep("growPlans") or []
                plan_data = next((p for p in plans if p.get("id") == new_plan_id), None)
                if plan_data:
                    await self.activate_grow_plan_by_id(new_plan_id, plan_data)

    async def _on_growplan_command(self, data):
        """Handle ogb_growplan_command – local command bus for pause/stop/resume.

        Expected data shape:
            {"action": "pause" | "resume" | "stop" | "activate", "plan_id": str}
        """
        if not isinstance(data, dict):
            return
        event_room = data.get("room")
        if event_room and event_room.lower() != self.room.lower():
            return

        action = data.get("action")
        plan_id = data.get("plan_id") or self.active_grow_plan_id
        if action == "pause":
            self.managerActive = False
            self.data_store.set("growManagerActive", False)
            # Clear pending updates so a half-initialized system does not apply
            # grow plan data after the pause command.
            if self._pending_updates:
                _LOGGER.debug(
                    f"🌱 {self.room} Clearing {len(self._pending_updates)} pending updates on pause command"
                )
                self._pending_updates.clear()
            await self._restore_pre_plan_snapshot()
        elif action == "resume":
            if plan_id:
                plans = self.data_store.getDeep("growPlans") or []
                plan_data = next((p for p in plans if p.get("id") == plan_id), None)
                if not plan_data:
                    stored_active = self.data_store.getDeep("growPlan.activePlan")
                    if isinstance(stored_active, dict) and stored_active.get("id") == plan_id:
                        plan_data = stored_active
                        _LOGGER.debug(
                            f"🌱 {self.room} Using stored activePlan snapshot for resume"
                        )
                if plan_data:
                    await self.activate_grow_plan_by_id(plan_id, plan_data)
                else:
                    _LOGGER.warning(
                        f"🌱 {self.room} Cannot resume {plan_id} – no plan data available"
                    )
        elif action == "stop":
            await self.deactivate_grow_plan(clear_plan_data=True)
        elif action == "activate":
            if plan_id:
                plans = self.data_store.getDeep("growPlans") or []
                plan_data = next((p for p in plans if p.get("id") == plan_id), None)
                if not plan_data:
                    stored_active = self.data_store.getDeep("growPlan.activePlan")
                    if isinstance(stored_active, dict) and stored_active.get("id") == plan_id:
                        plan_data = stored_active
                        _LOGGER.debug(
                            f"🌱 {self.room} Using stored activePlan snapshot for activate"
                        )
                if plan_data:
                    await self.activate_grow_plan_by_id(plan_id, plan_data)
                else:
                    _LOGGER.warning(
                        f"🌱 {self.room} Cannot activate {plan_id} – no plan data available"
                    )

    async def _update_current_week(self):
        """Aktualisiert die aktuelle Woche basierend auf dem Startdatum.

        First checks for API-provided currentWeekData, then falls back to local plan data.
        Also triggers API fetch if week data is missing.
        """
        # Wait for system initialization if not ready
        if not self._is_system_ready:
            _LOGGER.debug(f"{self.room} - System not ready yet, queuing week update")
            if "_update_current_week" not in self._pending_updates:
                self._pending_updates.append("_update_current_week")
            return

        # Safety: never calculate or apply week data when the plan is paused/stopped.
        if not self._is_grow_plan_active():
            _LOGGER.debug(
                f"🌱 {self.room} Grow plan not active - skipping week update"
            )
            return

        if not self.plan_start_date or not self.active_grow_plan:
            self.current_week = None
            self.current_week_data = None
            return

        # Prüfe ob Startdatum in der Zukunft liegt (aber nur wenn mehr als 1 Stunde in der Zukunft)
        try:
            time_diff = self.plan_start_date - self.currentDate
        except TypeError as e:
            _LOGGER.error(f"🌱 {self.room} Date comparison failed: plan_start_date={self.plan_start_date} (tzinfo={self.plan_start_date.tzinfo}), currentDate={self.currentDate} (tzinfo={self.currentDate.tzinfo}). Error: {e}")
            # Fallback: assume week 1
            self.current_week = 1
            return
            
        if time_diff.total_seconds() > 3600:  # Nur wenn mehr als 1 Stunde in der Zukunft
            self.current_week = 0
            self.current_week_data = None
            plan_name = self.active_grow_plan.get('plan_name') or self.active_grow_plan.get('name') or 'Unknown'
            _LOGGER.warning(f"Grow Plan '{plan_name}' ist vorgeplant. Start am {self.plan_start_date.isoformat()}")
            self.hass.bus.async_fire("grow_plan_week_update", {
                "week": 0,
                "week_data": None,
                "plan_id": self.active_grow_plan_id,
                "days_until_start": time_diff.days
            })
            return

        # NEW: First check if API provided a current week
        api_week = self.data_store.getDeep("growPlan.currentWeek")
        if api_week:
            week_number = api_week
        else:
            # Fallback: Calculate week from start date
            try:
                # Ensure both datetimes have timezone info
                if self.plan_start_date.tzinfo is None:
                    self.plan_start_date = self.plan_start_date.replace(tzinfo=timezone.utc)
                if self.currentDate.tzinfo is None:
                    self.currentDate = self.currentDate.replace(tzinfo=timezone.utc)
                
                days_since_start = (self.currentDate - self.plan_start_date).days
                week_number = max(1, (days_since_start // 7) + 1)
                _LOGGER.warning(f"🌱 {self.room} Calculated week from start date: {week_number} (days_since_start={days_since_start}, start_date={self.plan_start_date})")
            except (TypeError, ValueError) as e:
                _LOGGER.error(f"🌱 {self.room} Error calculating week: {e}. Using week 1 as fallback.")
                week_number = 1
        
        # NEW: First try to get week data from API-provided currentWeekData
        api_week_data = self.data_store.getDeep("growPlan.currentWeekData")
        if api_week_data and api_week_data.get("week") == week_number:
            current_week_data = api_week_data
            _LOGGER.debug(f"{self.room} Using API-provided week data for week {week_number}")
        else:
            # Fallback: Find week data in local plan
            plan_data_raw = self.active_grow_plan.get("weeks", [])
            
            # Handle JSON string format
            plan_data = plan_data_raw
            if isinstance(plan_data_raw, str):
                try:
                    parsed = json.loads(plan_data_raw)
                    if isinstance(parsed, dict) and "weeks" in parsed:
                        plan_data = parsed["weeks"]
                    elif isinstance(parsed, list):
                        plan_data = parsed
                    else:
                        plan_data = []
                    _LOGGER.warning(f"🌱 {self.room} Parsed weeks JSON string: {len(plan_data)} weeks")
                except json.JSONDecodeError as e:
                    _LOGGER.error(f"❌ {self.room} Failed to parse weeks JSON: {e}")
                    plan_data = []
            elif isinstance(plan_data_raw, dict):
                plan_data = plan_data_raw.get("weeks", [])
            
            current_week_data = None
            
            for week_data in plan_data:
                try:
                    week_idx = int(week_data.get("week", 0))
                    if week_idx == week_number:
                        current_week_data = week_data
                        break
                except Exception:
                    continue
            
            # Falls keine spezifische Woche gefunden, nimm die letzte verfügbare
            if not current_week_data and plan_data:
                max_week = max(int(w.get("week", 0)) for w in plan_data)
                if week_number > max_week:
                    for week_data in plan_data:
                        if int(week_data.get("week", 0)) == max_week:
                            current_week_data = week_data
                            break

        self.current_week = week_number
        self.current_week_data = current_week_data

        if current_week_data:
            _LOGGER.warning(f"Aktuelle Woche: {week_number}, Daten: {current_week_data}")
            # days_since_start might not be defined if we used api_week
            try:
                days_val = days_since_start
            except NameError:
                days_val = (week_number - 1) * 7
            self.hass.bus.async_fire("grow_plan_week_update", {
                "week": week_number,
                "week_data": current_week_data,
                "plan_id": self.active_grow_plan_id,
                "days_since_start": days_val
            })
            
            # Update entities with the week data
            await self._update_entities_from_week_data()
        else:
            # Keine Wochendaten gefunden - frage API an (nur wenn verbunden)
            _LOGGER.debug(f"🌱 {self.room} Keine Wochendaten für Woche {week_number} gefunden")
            if self.ws_client:
                # Prüfe ob WebSocket verbunden ist
                is_connected = getattr(self.ws_client, 'ws_connected', False) or getattr(self.ws_client.sio, 'connected', False)
                if is_connected:
                    _LOGGER.debug(f"🌱 {self.room} Frage Wochendaten vom API an...")
                    try:
                        await self.ws_client.request_grow_plans_week()
                    except Exception as e:
                        _LOGGER.error(f"🌱 {self.room} Konnte Wochendaten nicht anfragen: {e}")
                else:
                    _LOGGER.debug(f"🌱 {self.room} WebSocket nicht verbunden - überspringe API-Anfrage")

    def is_plan_active(self) -> bool:
        """Prüft ob ein Plan aktiv ist"""
        return self.active_grow_plan is not None and self.plan_start_date is not None

    def _is_grow_plan_active(self) -> bool:
        """Return True only when both the manager flag and the data store agree
        that a grow plan is currently active.
        """
        return bool(self.managerActive) and bool(self.data_store.get("growManagerActive"))

    def get_current_week_data(self) -> Optional[Dict[str, Any]]:
        """Gibt die Daten der aktuellen Woche zurück.
        
        First checks local cache, then falls back to data_store.
        """
        if self.current_week_data:
            return self.current_week_data
        
        # Fallback to data_store (set by API sync)
        stored_data = self.data_store.getDeep("growPlan.currentWeekData")
        if stored_data:
            self.current_week_data = stored_data
            return stored_data
        
        return None

    def get_week_data_by_number(self, week_number: int) -> Optional[Dict[str, Any]]:
        """Gibt Daten für eine spezifische Woche zurück"""
        if not self.active_grow_plan:
            return None
        
        plan_data = self.active_grow_plan.get("weeks", [])
        for week_data in plan_data:
            if week_data.get("week") == week_number:
                return week_data
        
        return None

    async def force_week_update(self):
        """Forces an update of the current week"""
        self.currentDate = datetime.now(timezone.utc)
        await self._update_current_week()

    async def _update_entities_from_week_data(self):
        """Update Home Assistant entities with current week data.

        This ensures all sensors and selects show the GrowPlan values.
        Called after week data is fetched or changed.
        """
        try:
            # Wait for system initialization if not ready
            if not self._is_system_ready:
                _LOGGER.debug(f"{self.room} - System not ready yet, queuing entity update")
                if "_update_entities_from_week_data" not in self._pending_updates:
                    self._pending_updates.append("_update_entities_from_week_data")
                return

            # Safety: never apply grow plan week data when the plan is not active.
            # This prevents paused plans from overwriting user setpoints after a
            # restart or during race conditions.
            if not self._is_grow_plan_active():
                _LOGGER.debug(
                    f"🌱 {self.room} Grow plan not active - skipping entity update from week data"
                )
                return

            week_data = self.data_store.getDeep("growPlan.currentWeekData")
            if not week_data:
                _LOGGER.debug(f"{self.room} - No week data available for entity update")
                return
            
            # Log the complete week data for debugging
            env = week_data.get("environment", {})
            light_cycle = env.get("lightCycle", {})
            light_intensity = env.get("lightIntensity", {})
            tent_mode = week_data.get("tentMode")
            plant_stage = week_data.get("stage")

            _LOGGER.warning(f"🌱 {self.room} Updating entities from week data: week={week_data.get('week')}, stage={week_data.get('stage')}, tentMode={tent_mode}")
            
            # Update tentMode
            if tent_mode:
                _LOGGER.warning(f"🌱 {self.room} Setting tentMode to: {tent_mode}")
                await update_entity("select.ogb_tentmode", tent_mode , self.room, self.hass)

            if plant_stage:
                _LOGGER.warning(f"🌱 {self.room} Setting PlantStage to: {plant_stage}")
                await update_entity("select.ogb_plantstage", plant_stage , self.room, self.hass)

            # Update temperature targets (handle both old and new format)
            temp = env.get("temperature", {})
            if isinstance(temp, dict):
                day_temp = temp.get("day")
                night_temp = temp.get("night")
                
                # Handle new format with min/max/optimal
                if isinstance(day_temp, dict):
                    if day_temp.get("max") is not None:
                        await update_entity("number.ogb_maxtemp", day_temp["max"], self.room, self.hass )
                    if day_temp.get("min") is not None:
                        await update_entity("number.ogb_mintemp", day_temp["min"], self.room, self.hass )
                #elif day_temp is not None:
                #    await update_entity("ogb_current_temp_target", day_temp)
                    
                #if isinstance(night_temp, dict):
                #    if night_temp.get("min") is not None:
                #        await update_entity("ogb_current_temp_target_min", night_temp["min"])
                #elif night_temp is not None:
                #    await update_entity("ogb_current_temp_target_min", night_temp)
            
            # Update humidity targets (supports both old and new format)
            humidity = env.get("humidity", {})
            if isinstance(humidity, dict):
                day_hum = humidity.get("day")
                if day_hum is not None:
                    if isinstance(day_hum, dict):
                        # New format: {min, max, optimal}
                        if day_hum.get("max") is not None:
                            await update_entity("number.ogb_maxhum", day_hum["max"], self.room, self.hass)
                    else:
                        # Old format: direct number
                        await update_entity("number.ogb_maxhum", day_hum, self.room, self.hass)
                #if humidity.get("night") is not None:
                #    await update_entity("number.ogb_minhum", humidity["night"])
            
            # Update VPD target (supports both old and new format)
            vpd = env.get("vpd", {})
            if isinstance(vpd, dict):
                if vpd.get("target") is not None:
                    # Old format
                    await update_entity("number.ogb_vpdtarget", vpd["target"], self.room, self.hass)
                elif vpd.get("optimal") is not None:
                    # New format: {min, max, optimal}
                    await update_entity("number.ogb_vpdtarget", vpd["optimal"], self.room, self.hass)
            
            # Update CO2 targets
            co2 = env.get("co2", {})
            if isinstance(co2, dict):
                if co2.get("optimal") is not None:
                    await update_entity("number.ogb_co2targetvalue", co2["optimal"], self.room, self.hass)
                if co2.get("max") is not None:
                    await update_entity("number.ogb_co2maxvalue", co2["max"], self.room, self.hass)
                if co2.get("min") is not None:
                    await update_entity("number.ogb_co2minvalue", co2["min"], self.room, self.hass)
                if co2.get("enabled") is not None:
                    await update_entity("select.ogb_co2_control", co2["enabled"], self.room, self.hass)
            
            # Update light times
            if light_cycle:
                start_hour = light_cycle.get("startTime")
                on_hours = light_cycle.get("on")
                if start_hour is not None:
                    light_on = f"{int(start_hour):02d}:00:00"
                    await update_entity("time.ogb_lightontime", light_on, self.room, self.hass)
                if start_hour is not None and on_hours is not None:
                    end_hour = (start_hour + on_hours) % 24
                    light_off = f"{int(end_hour):02d}:00:00"
                    await update_entity("time.ogb_lightofftime", light_off, self.room, self.hass)
                
                # Update sunrise/sunset durations (already "HH:MM:00" strings from API)
                sunrise_val = light_cycle.get("sunrise", "")
                sunset_val = light_cycle.get("sunset", "")
                if sunrise_val:
                    await update_entity("time.ogb_sunrisetime", sunrise_val, self.room, self.hass)
                if sunset_val:
                    await update_entity("time.ogb_sunsettime", sunset_val, self.room, self.hass)
            
            # Update light intensity
            if isinstance(light_intensity, dict):
                if light_intensity.get("min") is not None:
                    await update_entity("number.ogb_light_volt_min", light_intensity["min"], self.room, self.hass)
                if light_intensity.get("max") is not None:
                    await update_entity("number.ogb_light_volt_max", light_intensity["max"], self.room, self.hass)
            elif isinstance(light_intensity, (int, float)):
                await update_entity("number.ogb_light_volt_max", light_intensity, self.room, self.hass)
            
            # Update tent controls
            tent_controls = week_data.get("tentControls", {})
            if tent_controls:
                night_vpd = tent_controls.get("nightVpdHold", {})
                if night_vpd.get("enabled") is not None:
                    await update_entity("select.ogb_holdvpdnight", night_vpd["enabled"], self.room, self.hass)
                
                device_damp = tent_controls.get("deviceDampening", {})
                if device_damp.get("enabled") is not None:
                    await update_entity("select.ogb_vpd_devicedampening", device_damp["enabled"], self.room, self.hass)
                
                vpd_det = tent_controls.get("vpdDetermination", {})
                if vpd_det.get("mode") is not None:
                    await update_entity("select.ogb_vpd_determination", vpd_det["mode"], self.room, self.hass)
                
            
            _LOGGER.warning(f"🌱 {self.room} Entity update completed")
            
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error updating entities from week data: {e}")
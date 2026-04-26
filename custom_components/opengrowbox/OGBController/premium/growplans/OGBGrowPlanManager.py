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
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from ...utils.Premium.ogb_state import _save_state_securely, _remove_state_file, _load_state_securely

_LOGGER = logging.getLogger(__name__)

class OGBGrowPlanManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Grow Plan Manager"
        self.hass = hass
        self.room = room
        self.data_store = dataStore
        self.event_manager = eventManager

        self.managerActive = None

        # Aktuelles Datum
        self.currentDate = datetime.now(timezone.utc)

        self.is_premium = False
        
        # GrowPlans
        self.grow_plans = []  
        self.grow_plans_private  = []
        self.grow_plans_public  = []
          
        self.active_grow_plan = None
        self.active_grow_plan_id = None
        self.plan_start_date = None
        self.current_week = None
        self.current_week_data = None
        
        # Timer für tägliche Aktualisierungen
        self._daily_update_task = None
        self._init_task = None
        self._ha_unsubscribers = []
        self._event_bindings = []
        
        self._init_task = asyncio.create_task(self.init())
            
    async def init(self):
        """Initialize Grow Plan Manager"""
        self._setup_event_listeners()
        #await self._load_saved_state()
        await self._start_daily_update_timer()
        _LOGGER.debug(f"OGB Grow Plan Manager initialized for room: {self.room}")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self._register_event_listener("new_grow_plans", self._on_new_grow_plans)
        self._register_event_listener("plan_activation", self._on_plan_activation)
    
    def _register_bus_listener(self, event_name, callback):
        unsub = self.hass.bus.async_listen(event_name, callback)
        self._ha_unsubscribers.append(unsub)
    
    def _register_event_listener(self, event_name, callback):
        self.event_manager.on(event_name, callback)
        self._event_bindings.append((event_name, callback))        
            
    async def _load_saved_state(self):
        """Lade gespeicherten Zustand wenn vorhanden"""
        try:
            state = await _load_state_securely(f"grow_plan_state_{self.room}")
            if state:
                self.active_grow_plan_id = state.get("active_grow_plan_id")
                self.plan_start_date = state.get("plan_start_date")
                if self.plan_start_date:
                    self.plan_start_date = datetime.fromisoformat(self.plan_start_date)
                
                # Lade aktiven Plan
                if self.active_grow_plan_id:
                    await self._load_active_plan()
                    
        except Exception as e:
            _LOGGER.error(f"Fehler beim Laden des gespeicherten Zustands: {e}")

    async def _save_current_state(self):
        """Speichere aktuellen Zustand"""
        try:
            state = {
                "active_grow_plan_id": self.active_grow_plan_id,
                "plan_start_date": self.plan_start_date.isoformat() if self.plan_start_date else None
            }
            await _save_state_securely(f"grow_plan_state_{self.room}", state)
        except Exception as e:
            _LOGGER.error(f"Fehler beim Speichern des Zustands: {e}")

    async def _start_daily_update_timer(self):
        """Startet Timer für tägliche Aktualisierungen (00:00 und 12:00)"""
        if self._daily_update_task:
            self._daily_update_task.cancel()
        
        self._daily_update_task = asyncio.create_task(self._daily_update_loop())

    async def _daily_update_loop(self):
        """Tägliche Aktualisierung der aktuellen Woche um 00:00 und 12:00"""
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
                _LOGGER.info(f"🌱 {self.room} Nächstes API-Update um {next_update.strftime('%H:%M')} Uhr (in {sleep_seconds/3600:.1f} Stunden)")
                
                await asyncio.sleep(sleep_seconds)
                
                # Aktualisiere aktuelles Datum und Woche
                self.currentDate = datetime.now(timezone.utc)
                
                # Nur aktualisieren wenn ein GrowPlan aktiv ist
                if self.managerActive and self.active_grow_plan:
                    _LOGGER.info(f"🌱 {self.room} Geplantes API-Update um {self.currentDate.strftime('%H:%M')} (Plan aktiv)")
                    
                    # Hole frische Daten vom Server
                    if self.ws_client:
                        await self.sync_with_api()
                    
                    # Aktualisiere lokale Woche
                    await self._update_current_week()
                else:
                    _LOGGER.debug(f"🌱 {self.room} Geplantes Update übersprungen - kein aktiver Grow Plan")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Fehler im täglichen Update-Loop: {e}")
                await asyncio.sleep(3600)  # Warte 1 Stunde bei Fehlern

    def _on_new_grow_plans(self, data):
        """Handle neue Grow Plans from API or events.
        
        Processes the new API response format which includes:
        - plans: List of all plans
        - activePlan: Currently active plan
        - currentWeekData: Data for the current week
        """
        try:
            _LOGGER.info(f"🌱 {self.room} Received new_grow_plans event with keys: {list(data.keys()) if isinstance(data, dict) else 'not dict'}")
            
            # NEW: Handle new API response format with currentWeekData
            if "currentWeekData" in data:
                current_week_data = data["currentWeekData"]
                self.current_week_data = current_week_data
                self.data_store.setDeep("growPlan.currentWeekData", current_week_data)
                _LOGGER.info(f"🌱 {self.room} Received currentWeekData via event: week={current_week_data.get('week')}, stage={current_week_data.get('stage')}")
                # Update entities when week data arrives via event
                asyncio.create_task(self._update_entities_from_week_data())
            else:
                _LOGGER.warning(f"🌱 {self.room} No currentWeekData in event. Available keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
            
            # NEW: Handle activePlan from API response
            if "activePlan" in data:
                active_plan = data["activePlan"]
                self.active_grow_plan = active_plan
                if active_plan:
                    self.active_grow_plan_id = active_plan.get("id")
                    _LOGGER.info(f"🌱 {self.room} Received activePlan via event: {active_plan.get('name')} (ID: {self.active_grow_plan_id})")
            
            # Handle plans list
            if "plans" in data:
                plans = data["plans"]
                self.grow_plans = plans
                _LOGGER.info(f"🌱 {self.room} Received {len(plans)} plans via event")
            
            # Legacy format support
            elif "grow_plans" in data:
                grow_plans = data["grow_plans"]
                self.grow_plans = grow_plans
                self.grow_plans_private = grow_plans.get("private_plans", [])
                self.grow_plans_public = grow_plans.get("public_plans", [])
                self.active_grow_plan = grow_plans.get("active_plan", {})
            else:
                # Fallback: assume data is the complete structure
                self.grow_plans = data

        except Exception as e:
            _LOGGER.exception(f"Fehler beim Verarbeiten neuer Grow Plans: {e}")

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

            _LOGGER.info(f"{self.room}: Evaluating Grow Plan Settings for Week {week}: {week_settings}")

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
                _LOGGER.info(f"{self.room}: Änderungen erkannt in {category} → {changes}")
                await self.event_manager.emit(event_name, {"room": self.room, "changes": changes})

        except Exception as e:
            _LOGGER.error(f"{self.room}: Fehler bei _emit_if_changed ({category}): {e}")
 
    ## Helpers
    def get_grow_plan_name_by_id(self, plan_id: str) -> Optional[str]:
        """Gibt den Namen eines Grow Plans anhand seiner ID zurück."""
        try:
            if not plan_id:
                _LOGGER.warning("get_grow_plan_name_by_id wurde ohne plan_id aufgerufen.")
                return None

            # Beide Listen zusammenführen (private + public)
            all_plans = (self.grow_plans or []) + (self.grow_plans_public or [])
            logging.warning(f"PLANOUTPUT-: {all_plans}. - {self.grow_plans}")
            # Passenden Plan suchen
            for plan in all_plans:
                if str(plan.get("id")) == str(plan_id):
                    logging.warning(f"PLANOUTPUT-: {plan}")
                    return plan.get("plan_name")

            _LOGGER.debug(f"Kein Grow Plan mit ID {plan_id} gefunden.")
            return None

        except Exception as e:
            _LOGGER.error(f"Fehler in get_grow_plan_name_by_id: {e}")
            return None

    def _on_plan_activation(self, growPlan):
        """Handle Plan Aktivierung"""
        try:
            # Validate growPlan data
            if not growPlan:
                _LOGGER.warning(f"{self.room}: Received empty growPlan data")
                return
            
            # Handle both dict and object formats
            if isinstance(growPlan, dict):
                plan_id = growPlan.get("id")
                plan_name = growPlan.get("name", "Unknown")
            else:
                plan_id = getattr(growPlan, "id", None)
                plan_name = getattr(growPlan, "name", "Unknown")
            
            # Ensure plan_id is a string, not a dict
            if isinstance(plan_id, dict):
                _LOGGER.warning(f"{self.room}: plan_id is a dict, extracting id field")
                plan_id = plan_id.get("id")
            
            _LOGGER.info(f"🌱 {self.room} Plan activation requested: {plan_name} (ID: {plan_id})")
            
            if not plan_id:
                _LOGGER.warning(f"{self.room}: No plan_id in growPlan data, skipping activation")
                return

            # Handle both list and dict formats
            if isinstance(self.grow_plans, list):
                all_plans = self.grow_plans
            else:
                all_plans = self.grow_plans.get('all_plans', [])
            
            _LOGGER.debug(f"ALL PLANS:{all_plans}")

            # Wenn der Plan schon in grow_plans steckt, nicht doppelt anhängen
            if not any(p.get("id") == plan_id for p in all_plans if isinstance(p, dict)):
                if isinstance(self.grow_plans, list):
                    self.grow_plans.append(growPlan)
                else:
                    self.grow_plans.setdefault("all_plans", []).append(growPlan)

            asyncio.create_task(self.activate_grow_plan(plan_id))

        except Exception as e:
            _LOGGER.error(f"Fehler bei Plan-Aktivierung: {e}")

    async def activate_grow_plan(self, plan_id: str):
        """Aktiviert einen Grow Plan"""
        try:
            # Validate plan_id - handle case where plan_id is a dict
            if isinstance(plan_id, dict):
                _LOGGER.warning(f"{self.room}: plan_id is a dict, extracting id field")
                plan_id = plan_id.get("id")
            
            if not plan_id:
                _LOGGER.warning(f"{self.room}: No plan_id provided for activation")
                return False
            
            # Finde den Plan
            plan = None
            # Handle both list and dict formats
            if isinstance(self.grow_plans, list):
                all_plans = self.grow_plans
            else:
                all_plans = self.grow_plans.get("all_plans", [])
            
            for p in all_plans:
                if isinstance(p, dict) and p.get("id") == plan_id:
                    plan = p
                    break
            
            if not plan:
                _LOGGER.warning(f"{self.room}: Grow Plan mit ID {plan_id} nicht gefunden in {len(all_plans)} Plänen")
                return False
            
            
            isValid = await self._eval_plan_settings(plan)
                        
            
            # Aktiviere den Plan
            self.active_grow_plan = plan
            self.active_grow_plan_id = plan_id
            self.plan_start_date = datetime.now(timezone.utc)
            
            # Speichere Zustand
            await self.event_manager.emit("SaveRequest",self.room) 
            
            # Aktualisiere aktuelle Woche
            await self._update_current_week()
            
            _LOGGER.warning(f"Grow Plan aktiviert: {plan_id}, Start: {self.plan_start_date}")
            
            _LOGGER.warning(f"Grow Plan Settings Adjustment Started for {plan_id},")
            
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

    async def _load_active_plan(self):
        """Lädt den aktiven Plan basierend auf der gespeicherten ID"""
        if not self.active_grow_plan_id:
            return
        
        for plan in self.grow_plans:
            if plan.get("id") == self.active_grow_plan_id:
                self.active_grow_plan = plan
                await self._update_current_week()
                break

    async def _update_current_week(self):
        """Aktualisiert die aktuelle Woche basierend auf dem Startdatum.
        
        First checks for API-provided currentWeekData, then falls back to local plan data.
        Also triggers API fetch if week data is missing.
        """
        if not self.plan_start_date or not self.active_grow_plan:
            self.current_week = None
            self.current_week_data = None
            return

        # Prüfe ob Startdatum in der Zukunft liegt (aber nur wenn mehr als 1 Stunde in der Zukunft)
        time_diff = self.plan_start_date - self.currentDate
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

        # Berechne aktuelle Woche
        days_since_start = (self.currentDate - self.plan_start_date).days
        week_number = max(1, (days_since_start // 7) + 1)
        
        # NEW: First try to get week data from API-provided currentWeekData
        api_week_data = self.data_store.getDeep("growPlan.currentWeekData")
        if api_week_data and api_week_data.get("week") == week_number:
            current_week_data = api_week_data
            _LOGGER.info(f"🌱 {self.room} Using API-provided week data for week {week_number}")
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
                    _LOGGER.info(f"🌱 {self.room} Parsed weeks JSON string: {len(plan_data)} weeks")
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
            self.hass.bus.async_fire("grow_plan_week_update", {
                "week": week_number,
                "week_data": current_week_data,
                "plan_id": self.active_grow_plan_id,
                "days_since_start": days_since_start
            })

    def is_plan_active(self) -> bool:
        """Prüft ob ein Plan aktiv ist"""
        return self.active_grow_plan is not None and self.plan_start_date is not None

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

    async def deactivate_current_plan(self):
        """Deaktiviert den aktuellen Plan"""
        try:
            if self.active_grow_plan_id:
                _LOGGER.info(f"Deaktiviere Grow Plan: {self.active_grow_plan_id}")
                
                # Event senden
                self.hass.bus.async_fire("grow_plan_deactivated", {
                    "plan_id": self.active_grow_plan_id
                })
            
            # Reset alle Werte
            self.active_grow_plan = None
            self.active_grow_plan_id = None
            self.plan_start_date = None
            self.current_week = None
            self.current_week_data = None
            
            # Lösche gespeicherten Zustand
            await _remove_state_file(f"grow_plan_state_{self.room}")
            
            return True
            
        except Exception as e:
            _LOGGER.error(f"Fehler beim Deaktivieren des Plans: {e}")
            return False

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
            week_data = self.data_store.getDeep("growPlan.currentWeekData")
            if not week_data:
                _LOGGER.debug(f"{self.room} - No week data available for entity update")
                return
            
            env = week_data.get("environment", {})
            light_cycle = env.get("lightCycle", {})
            light_intensity = env.get("lightIntensity", {})
            tent_mode = week_data.get("tentMode")
            
            _LOGGER.info(f"🌱 {self.room} Updating entities from week data...")
            
            # Update tentMode
            if tent_mode:
                await self._update_entity("ogb_tentmode", tent_mode)
            
            # Update temperature targets (handle both old and new format)
            temp = env.get("temperature", {})
            if isinstance(temp, dict):
                day_temp = temp.get("day")
                night_temp = temp.get("night")
                
                # Handle new format with min/max/optimal
                if isinstance(day_temp, dict):
                    if day_temp.get("max") is not None:
                        await self._update_entity("ogb_current_temp_target", day_temp["max"])
                    if day_temp.get("min") is not None:
                        await self._update_entity("ogb_current_temp_target_min", day_temp["min"])
                elif day_temp is not None:
                    await self._update_entity("ogb_current_temp_target", day_temp)
                    
                if isinstance(night_temp, dict):
                    if night_temp.get("min") is not None:
                        await self._update_entity("ogb_current_temp_target_min", night_temp["min"])
                elif night_temp is not None:
                    await self._update_entity("ogb_current_temp_target_min", night_temp)
            
            # Update humidity targets
            humidity = env.get("humidity", {})
            if isinstance(humidity, dict):
                if humidity.get("day") is not None:
                    await self._update_entity("ogb_current_humidity_target", humidity["day"])
                if humidity.get("night") is not None:
                    await self._update_entity("ogb_current_humidity_target_min", humidity["night"])
            
            # Update VPD target
            vpd = env.get("vpd", {})
            if isinstance(vpd, dict) and vpd.get("target") is not None:
                await self._update_entity("ogb_current_vpd_target", vpd["target"])
            
            # Update CO2 targets
            co2 = env.get("co2", {})
            if isinstance(co2, dict):
                if co2.get("optimal") is not None:
                    await self._update_entity("ogb_current_co2_target", co2["optimal"])
                if co2.get("max") is not None:
                    await self._update_entity("ogb_current_co2_target_max", co2["max"])
                if co2.get("min") is not None:
                    await self._update_entity("ogb_current_co2_target_min", co2["min"])
                if co2.get("enabled") is not None:
                    await self._update_entity("ogb_co2_control", co2["enabled"])
            
            # Update light times
            if light_cycle:
                start_hour = light_cycle.get("startTime")
                on_hours = light_cycle.get("on")
                if start_hour is not None:
                    light_on = f"{int(start_hour):02d}:00:00"
                    await self._update_entity("ogb_current_light_on", light_on)
                if start_hour is not None and on_hours is not None:
                    end_hour = (start_hour + on_hours) % 24
                    light_off = f"{int(end_hour):02d}:00:00"
                    await self._update_entity("ogb_current_light_off", light_off)
                
                # Update sunrise/sunset durations
                sunrise_min = light_cycle.get("sunrise", 0)
                sunset_min = light_cycle.get("sunset", 0)
                if sunrise_min:
                    await self._update_entity("ogb_current_sunrise_duration", sunrise_min)
                if sunset_min:
                    await self._update_entity("ogb_current_sunset_duration", sunset_min)
            
            # Update light intensity
            if isinstance(light_intensity, dict):
                if light_intensity.get("min") is not None:
                    await self._update_entity("ogb_current_light_intensity_min", light_intensity["min"])
                if light_intensity.get("max") is not None:
                    await self._update_entity("ogb_current_light_intensity_max", light_intensity["max"])
            elif isinstance(light_intensity, (int, float)):
                await self._update_entity("ogb_current_light_intensity_max", light_intensity)
            
            # Update tent controls
            tent_controls = week_data.get("tentControls", {})
            if tent_controls:
                night_vpd = tent_controls.get("nightVpdHold", {})
                if night_vpd.get("enabled") is not None:
                    await self._update_entity("ogb_night_vpd_hold", night_vpd["enabled"])
                
                device_damp = tent_controls.get("deviceDampening", {})
                if device_damp.get("enabled") is not None:
                    await self._update_entity("ogb_device_dampening", device_damp["enabled"])
                
                vpd_det = tent_controls.get("vpdDetermination", {})
                if vpd_det.get("mode") is not None:
                    await self._update_entity("ogb_vpd_determination_mode", vpd_det["mode"])
                if vpd_det.get("enabled") is not None:
                    await self._update_entity("ogb_vpd_determination", vpd_det["enabled"])
                
                drying = tent_controls.get("drying", {})
                if drying.get("mode") is not None:
                    await self._update_entity("ogb_drying_mode", drying["mode"])
                if drying.get("enabled") is not None:
                    await self._update_entity("ogb_drying", drying["enabled"])
            
            _LOGGER.info(f"🌱 {self.room} Entity update completed")
            
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error updating entities from week data: {e}")
    
    async def _update_entity(self, entity_prefix, value):
        """Helper to update a specific entity."""
        try:
            from ...utils.sensorUpdater import _update_specific_sensor, _update_specific_select
            
            # Try sensor update first
            if await _update_specific_sensor(entity_prefix, self.room, value, self.hass):
                return
            
            # Try select update if sensor fails
            if await _update_specific_select(entity_prefix, self.room, value, self.hass):
                return
                
        except Exception as e:
            _LOGGER.debug(f"{self.room} - Could not update entity {entity_prefix}: {e}")
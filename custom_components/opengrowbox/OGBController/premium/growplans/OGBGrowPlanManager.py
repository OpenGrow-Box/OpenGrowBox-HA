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
        
        asyncio.create_task(self.init())
            
    async def init(self):
        """Initialize Grow Plan Manager"""
        self._setup_event_listeners()
        #await self._load_saved_state()
        await self._start_daily_update_timer()
        _LOGGER.debug(f"OGB Grow Plan Manager initialized for room: {self.room}")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self.event_manager.on("new_grow_plans", self._on_new_grow_plans)
        self.event_manager.on("plan_activation",self._on_plan_activation)
        self.hass.bus.async_listen("ogb_premium_growplan_pause", self._pause_manager)   
        self.hass.bus.async_listen("ogb_premium_growplan_resume", self._resume_manager)   

    async def _pause_manager(self,data):
        if self.managerActive == True:
            self.managerActive == False
            self.data_store.set("growManagerActive",False)
            await self.event_manager.emit("SaveRequest",self.room)   
        logging.warning(f"{self.room} Grow-Manager State:{self.managerActive} got Resumed with {self.activate_grow_plan.plan_name} - {data}")            

    async def _resume_manager(self,data):

        if self.managerActive == False:
            self.managerActive == True
            self.data_store.set("growManagerActive",True)
            await self.event_manager.emit("SaveRequest",self.room)         
        logging.warning(f"{self.room} Grow-Manager State:{self.managerActive} got Resumed with {self.activate_grow_plan.plan_name} - {data}")        
            
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
        """Startet Timer für tägliche Aktualisierungen"""
        if self._daily_update_task:
            self._daily_update_task.cancel()
        
        self._daily_update_task = asyncio.create_task(self._daily_update_loop())

    async def _daily_update_loop(self):
        """Tägliche Aktualisierung der aktuellen Woche"""
        while True:
            try:
                # Warte bis zum nächsten Tag um Mitternacht
                now = datetime.now(timezone.utc)
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                sleep_seconds = (tomorrow - now).total_seconds()
                
                await asyncio.sleep(sleep_seconds)
                
                # Aktualisiere aktuelles Datum und Woche
                self.currentDate = datetime.now(timezone.utc)
                await self._update_current_week()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"Fehler im täglichen Update-Loop: {e}")
                await asyncio.sleep(3600)  # Warte 1 Stunde bei Fehlern

    def _on_new_grow_plans(self, data):
        """Handle neue Grow Plans."""
        try:
            # Prüfen, ob die Datenstruktur bereits die Pläne direkt enthält
            if "grow_plans" in data:
                grow_plans = data["grow_plans"]
            else:
                grow_plans = data  # komplette Struktur ist bereits der GrowPlan-Container

            self.grow_plans = grow_plans
            self.grow_plans_private = grow_plans.get("private_plans", [])
            self.grow_plans_public = grow_plans.get("public_plans", [])
            self.active_grow_plan = grow_plans.get("active_plan", {})

        except Exception as e:
            _LOGGER.exception(f"Fehler beim Verarbeiten neuer Grow Plans: {e}")

    ## Workers
    async def _eval_plan_settings(self, plan_data: Dict[str, Any]):
        """
        Prüft den aktiven Plan und triggert Events für relevante Parameter.
        Wird beim Aktivieren eines Plans oder täglichen Update ausgeführt.
        """
        try:
            current_week_data = plan_data.get("weeks", [])
            if not current_week_data:
                _LOGGER.warning(f"{self.room}: Kein Wochenplan in {plan_data.get('plan_name')}")
                return

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
            all_plans = self.grow_plans.get('all_plans', [])
            _LOGGER.error(f"ALL PLANS:{all_plans}")

            plan_id = growPlan.get("id")

            # Wenn der Plan schon in grow_plans steckt, nicht doppelt anhängen
            if plan_id and not any(p.get("id") == plan_id for p in self.grow_plans.get("all_plans", [])):
                self.grow_plans.setdefault("all_plans", []).append(growPlan)

            if plan_id:
                asyncio.create_task(self.activate_grow_plan(plan_id))

        except Exception as e:
            _LOGGER.error(f"Fehler bei Plan-Aktivierung: {e}")

    async def activate_grow_plan(self, plan_id: str):
        """Aktiviert einen Grow Plan"""
        try:
            # Finde den Plan
            plan = None
            for p in self.grow_plans.get("all_plans"):
                if p.get("id") == plan_id:
                    plan = p
                    break
            
            if not plan:
                _LOGGER.error(f"Grow Plan mit ID {plan_id} nicht gefunden")
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
        """Aktualisiert die aktuelle Woche basierend auf dem Startdatum"""
        if not self.plan_start_date or not self.active_grow_plan:
            self.current_week = None
            self.current_week_data = None
            return
        
        # Prüfe ob Startdatum in der Zukunft liegt
        if self.plan_start_date > self.currentDate:
            self.current_week = 0
            self.current_week_data = None
            _LOGGER.warning(f"Grow Plan '{self.active_grow_plan.get('plan_name')}' ist vorgeplant. Start am {self.plan_start_date.isoformat()}")
            self.hass.bus.async_fire("grow_plan_week_update", {
                "week": 0,
                "week_data": None,
                "plan_id": self.active_grow_plan_id,
                "days_until_start": (self.plan_start_date - self.currentDate).days
            })
            return
        
        # Berechne aktuelle Woche
        days_since_start = (self.currentDate - self.plan_start_date).days
        week_number = max(1, (days_since_start // 7) + 1)
        
        # Finde Wochendaten im Plan
        plan_data = self.active_grow_plan.get("weeks", [])
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
        else:
            _LOGGER.warning(f"Keine Daten für Woche {week_number} gefunden")

    def is_plan_active(self) -> bool:
        """Prüft ob ein Plan aktiv ist"""
        return self.active_grow_plan is not None and self.plan_start_date is not None

    def get_current_week_number(self) -> Optional[int]:
        """Gibt die aktuelle Wochennummer zurück"""
        return self.current_week

    def get_current_week_data(self) -> Optional[Dict[str, Any]]:
        """Gibt die Daten der aktuellen Woche zurück"""
        return self.current_week_data

    def get_plan_status(self) -> Dict[str, Any]:
        """Gibt den aktuellen Status des Plans zurück"""
        return {
            "is_active": self.is_plan_active(),
            "plan_id": self.active_grow_plan_id,
            "start_date": self.plan_start_date.isoformat() if self.plan_start_date else None,
            "current_week": self.current_week,
            "current_week_data": self.current_week_data,
            "days_since_start": (self.currentDate - self.plan_start_date).days if self.plan_start_date else None
        }

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

    def get_all_weeks_data(self) -> List[Dict[str, Any]]:
        """Gibt alle Wochendaten des aktiven Plans zurück"""
        if not self.active_grow_plan:
            return []
        
        return self.active_grow_plan.get("weeks", [])

    async def force_week_update(self):
        """Forces an update of the current week"""
        self.currentDate = datetime.now(timezone.utc)
        await self._update_current_week()

    def set_ws_client(self, ws_client):
        """Set the WebSocket client for API communication"""
        self.ws_client = ws_client
        _LOGGER.debug(f"{self.room} - WebSocket client set for grow plan manager")

    async def sync_with_api(self):
        """Sync grow plans with the API"""
        if not self.ws_client:
            _LOGGER.warning(f"{self.room} - No WebSocket client available for grow plan sync")
            return False

        try:
            # Request grow plans from API
            result = await self.ws_client.prem_event("get_grow_plans", {
                "event_id": "sync_request",
                "room": self.room
            })

            if result:
                _LOGGER.info(f"{self.room} - Successfully synced grow plans with API")
                return True
            else:
                _LOGGER.warning(f"{self.room} - Failed to sync grow plans with API")
                return False

        except Exception as e:
            _LOGGER.error(f"{self.room} - Error syncing grow plans with API: {e}")
            return False

    def __del__(self):
        """Cleanup when destroying the instance"""
        if self._daily_update_task and not self._daily_update_task.done():
            self._daily_update_task.cancel()
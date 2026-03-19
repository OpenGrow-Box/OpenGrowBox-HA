import asyncio
import inspect
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Optional, Literal

_LOGGER = logging.getLogger(__name__)

DebugType = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

GET_LOGS_EVENT = "getOGBClientLogs"
GET_LOGS_RESPONSE_EVENT = "ogbClientLogsResponse"


class OGBEventManager:
    def __init__(self, hass, ogb_model):
        self.name = "OGB Event Manager"
        self.hass = hass
        self.ogb_model = ogb_model
        self.listeners = {}
        self.notifications_enabled = False
        # MEMORY FIX: Track background tasks to prevent orphaned tasks
        self._background_tasks: set = set()
        self._shutdown = False
        # Lock for file log writes to prevent race conditions
        self._log_file_lock = asyncio.Lock()

    def __repr__(self):
        return f"Current Listeners: {self.listeners}"

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """Create and track a background task for proper cleanup."""
        if self._shutdown:
            _LOGGER.debug("EventManager shutdown, not creating new tasks")
            return None
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def on(self, event_name, callback):
        """Registriere einen Listener (synchron oder asynchron) für ein spezifisches Event."""
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        # MEMORY FIX: Prevent duplicate listeners
        if callback not in self.listeners[event_name]:
            self.listeners[event_name].append(callback)

    def remove(self, event_name, callback):
        """Entferne einen spezifischen Listener."""
        if event_name in self.listeners and callback in self.listeners[event_name]:
            self.listeners[event_name].remove(callback)

    def remove_all(self, event_name=None):
        """Entferne alle Listener für ein Event oder alle Events."""
        if event_name:
            self.listeners.pop(event_name, None)
        else:
            self.listeners.clear()

    async def _call_listener(self, callback, data):
        """Rufe einen Listener auf, synchron oder asynchron."""
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            _LOGGER.error(f"Fehler beim Aufruf des Listeners für '{callback}': {e}")

    async def emit(self, event_name, data, haEvent=False, debug_type: Optional[DebugType] = None):
        """Event auslösen, inkl. optionalem HA-Event und Notification.
        
        Args:
            event_name: Name des Events
            data: Event-Daten
            haEvent: Wenn True, Event an Home Assistant senden
            debug_type: Optionaler Typ für LogForClient (DEBUG, INFO, WARNING, ERROR)
        """
        
        # Don't emit events during shutdown
        if self._shutdown:
            return
        
        # Debug log for medium-related events
        if "Medium" in event_name or "Plant" in event_name:
            _LOGGER.debug(f"📢 EMIT: {event_name} - listeners: {len(self.listeners.get(event_name, []))}, haEvent: {haEvent}")

        # LogForClient: Speichere in ogb_data mit debug_type (async)
        if event_name == "LogForClient":
            # Bestimme debug_type mit intelligenter Fallback-Logik
            effective_debug_type = debug_type
            
            # Wenn kein expliziter debug_type, versuche aus Payload zu extrahieren
            if not effective_debug_type:
                if isinstance(data, dict):
                    # Prüfe verschiedene Felder im Payload
                    if data.get("Type") in ("DEBUG", "INFO", "WARNING", "ERROR"):
                        effective_debug_type = data.get("Type")
                    elif data.get("Warning"):
                        effective_debug_type = "WARNING"
                    elif data.get("Error") or data.get("error"):
                        effective_debug_type = "ERROR"
                elif isinstance(data, str):
                    # Prüfe String-Inhalte auf Warnungen/Fehler
                    lower_data = data.lower()
                    if "error" in lower_data or "failed" in lower_data or "exception" in lower_data:
                        effective_debug_type = "ERROR"
                    elif "warning" in lower_data or "blocked" in lower_data or "attention" in lower_data:
                        effective_debug_type = "WARNING"
                    elif "debug" in lower_data:
                        effective_debug_type = "DEBUG"
                
                # Fallback auf INFO wenn nichts gefunden
                effective_debug_type = effective_debug_type or "INFO"
            
            self._create_tracked_task(self._save_log_to_file(data, effective_debug_type))

        if haEvent:
            # MEMORY FIX: Track the task
            self._create_tracked_task(self.emit_to_home_assistant(event_name, data, debug_type))
            if self.notifications_enabled:
                # Bestimme effective_debug_type für Notification-Filter
                effective_type = debug_type
                if event_name == "LogForClient" and not effective_type:
                    effective_type = self._extract_debug_type_from_data(data)
                await self.send_notification(event_name, data, effective_type)

        if event_name in self.listeners:
            listener_count = len(self.listeners[event_name])
            if "Medium" in event_name or "Plant" in event_name:
                _LOGGER.debug(f"📢 Calling {listener_count} listeners for {event_name}")
            for callback in self.listeners[event_name]:
                if inspect.iscoroutinefunction(callback):
                    # MEMORY FIX: Track the task
                    self._create_tracked_task(callback(data))
                else:
                    try:
                        callback(data)
                    except Exception as e:
                        _LOGGER.error(f"Fehler beim synchronen Listener: {e}")
        elif "Medium" in event_name or "Plant" in event_name:
            _LOGGER.debug(f"ℹ️ No listeners registered for {event_name}")

    def emit_sync(self, event_name, data, haEvent=False, debug_type: Optional[DebugType] = None):
        """Synchrones Event auslösen (für synchrone Kontexte).
        Wenn haEvent=True, wird das Event auch an Home Assistant gesendet.
        
        Args:
            event_name: Name des Events
            data: Event-Daten
            haEvent: Wenn True, Event an Home Assistant senden
            debug_type: Optionaler Typ für LogForClient (DEBUG, INFO, WARNING, ERROR)
        """
        asyncio.create_task(self.emit(event_name, data, haEvent, debug_type))

    async def emit_to_home_assistant(self, event_name, event_data, debug_type: Optional[DebugType] = None):
        """Sende ein Event an Home Assistant über den Event-Bus.
        
        Args:
            event_name: Name des Events
            event_data: Event-Daten
            debug_type: Optionaler Typ für LogForClient (wird in Event-Daten eingefügt)
        """
        try:
            # Wenn event_data ein Dataclass-Objekt ist, in ein Dictionary umwandeln
            if is_dataclass(event_data):
                event_data = asdict(event_data)
            
            # DebugType in Event-Daten einfügen falls vorhanden
            if debug_type and isinstance(event_data, dict):
                event_data["DebugType"] = debug_type
            elif debug_type and isinstance(event_data, str):
                event_data = {"Message": event_data, "DebugType": debug_type}

            if hasattr(self.hass, "bus"):
                self.hass.bus.fire(event_name, event_data)
                _LOGGER.info(f"Event-Bus Event '{event_name}' erfolgreich gesendet.")
            else:
                _LOGGER.error(
                    f"Kein gültiger Event-Kanal für '{event_name}' verfügbar!"
                )
        except Exception as e:
            _LOGGER.error(f"Fehler beim Senden des Events '{event_name}': {e}")

    def make_json_serializable(self, obj):
        """
        Recursively traverse the object and convert non-serializable types like datetime.
        """
        if isinstance(obj, dict):
            return {k: self.make_json_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.make_json_serializable(i) for i in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return obj

    async def send_notification(self, title: str, data, debug_type: str = None):
        """
        Sende eine Push-Notification via notify.notify an alle konfigurierten Notifier.
        Filtert nach logType aus DataStore.
        """
        try:
            # Bestimme den Log-Typ aus den Daten
            effective_type = debug_type
            
            # Versuche aus den Daten zu extrahieren
            if not effective_type:
                if isinstance(data, dict):
                    if data.get("DebugType") in ("DEBUG", "INFO", "WARNING", "ERROR"):
                        effective_type = data.get("DebugType")
                    elif data.get("Type") in ("DEBUG", "INFO", "WARNING", "ERROR"):
                        effective_type = data.get("Type")
                    elif data.get("Warning"):
                        effective_type = "WARNING"
                    elif data.get("Error") or data.get("error"):
                        effective_type = "ERROR"
                elif isinstance(data, str):
                    lower_data = data.lower()
                    if "error" in lower_data or "failed" in lower_data:
                        effective_type = "ERROR"
                    elif "warning" in lower_data or "blocked" in lower_data:
                        effective_type = "WARNING"
                    elif "debug" in lower_data:
                        effective_type = "DEBUG"
            
            effective_type = effective_type or "INFO"
            
            # Prüfe logType Filter aus DataStore
            allowed_types = self._get_allowed_notification_types()
            
            # DEBUG nur senden wenn explizit erlaubt
            if effective_type == "DEBUG" and "DEBUG" not in allowed_types:
                _LOGGER.debug(f"DEBUG Notification unterdrückt (nicht in logType): {title}")
                return
            
            # INFO nur senden wenn erlaubt
            if effective_type == "INFO" and "INFO" not in allowed_types:
                _LOGGER.debug(f"INFO Notification unterdrückt (nicht in logType): {title}")
                return
            
            # WARNING nur senden wenn erlaubt
            if effective_type == "WARNING" and "WARNING" not in allowed_types:
                _LOGGER.debug(f"WARNING Notification unterdrückt (nicht in logType): {title}")
                return
            
            # ERROR immer senden (wichtig!)
            if effective_type == "ERROR":
                pass  # Keine Filterung für Fehler
            
            serializable_data = self.make_json_serializable(data)
            message = (
                json.dumps(serializable_data, indent=2)
                if isinstance(serializable_data, dict)
                else str(serializable_data)
            )

            await self.hass.services.async_call(
                domain="notify",
                service="notify",
                service_data={
                    "title": title,
                    "message": message,
                },
                blocking=False,
            )
            _LOGGER.info(f"Push-Notification für '{title}' gesendet (Typ: {effective_type}).")
        except Exception as e:
            _LOGGER.error(f"Fehler beim Senden der Push-Notification: {e}")
    
    def _extract_debug_type_from_data(self, data) -> str:
        """Extrahiert debug_type aus den Daten (für interne Nutzung)."""
        if isinstance(data, dict):
            if data.get("DebugType") in ("DEBUG", "INFO", "WARNING", "ERROR"):
                return data.get("DebugType")
            elif data.get("Type") in ("DEBUG", "INFO", "WARNING", "ERROR"):
                return data.get("Type")
            elif data.get("Warning"):
                return "WARNING"
            elif data.get("Error") or data.get("error"):
                return "ERROR"
        elif isinstance(data, str):
            lower_data = data.lower()
            if "error" in lower_data or "failed" in lower_data:
                return "ERROR"
            elif "warning" in lower_data or "blocked" in lower_data:
                return "WARNING"
            elif "debug" in lower_data:
                return "DEBUG"
        return "INFO"
    
    def _get_allowed_notification_types(self) -> list:
        """
        Liest logType aus dem DataStore und gibt Liste erlaubter Typen zurück.
        """
        try:
            # Versuche über ogb_model auf data_store zuzugreifen
            if hasattr(self, 'ogb_model') and self.ogb_model:
                data_store = getattr(self.ogb_model, 'data_store', None)
                if data_store:
                    log_type = data_store.get("logType")
                    if log_type:
                        # Parse CSV-String wie "INFO,WARNING,ERROR"
                        return [t.strip().upper() for t in log_type.split(",") if t.strip()]
            
            # Fallback: Default erlaubt WARNING und ERROR
            return ["WARNING", "ERROR"]
        except Exception as e:
            _LOGGER.debug(f"Konnte logType nicht lesen: {e}")
            return ["WARNING", "ERROR"]

    def change_notify_set(self, state):
        self.notifications_enabled = state
        _LOGGER.info(f"Notify State jetzt: {self.notifications_enabled}")

    async def async_shutdown(self):
        """Shutdown event manager and cleanup all resources."""
        _LOGGER.info("🛑 Shutting down EventManager")
        self._shutdown = True
        
        # Cancel all background tasks
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete with timeout
        if self._background_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._background_tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                _LOGGER.warning("⚠️ Some EventManager tasks did not complete within timeout")
        
        self._background_tasks.clear()
        
        # Clear all listeners to prevent memory leaks
        listener_count = sum(len(v) for v in self.listeners.values())
        self.listeners.clear()
        _LOGGER.info(f"✅ EventManager shutdown complete, cleared {listener_count} listeners")

    def _sanitize_data_for_json(self, data):
        """Reinigt Daten für JSON-Speicherung, um kaputte Strings zu vermeiden."""
        if data is None:
            return None
        elif isinstance(data, str):
            # String-Reinigung: sicherstellen, dass der String gültig ist
            try:
                # Prüfe ob der String selbst gültiges JSON wäre
                json.dumps(data)
                return data
            except (TypeError, ValueError):
                # Wenn nicht, versuchen wir, den String zu bereinigen
                try:
                    # Ersetze nicht-escaped quotes in Strings
                    cleaned = str(data)
                    # Ersetze carriage returns und andere problematische Zeichen
                    cleaned = cleaned.replace('\r', '\\r').replace('\n', '\\n')
                    # Prüfe erneut
                    json.dumps(cleaned)
                    return cleaned
                except:
                    # Als letztes Mittel: repr() verwenden, das sicher immer geht
                    return repr(str(data))
        elif isinstance(data, (list, tuple)):
            return [self._sanitize_data_for_json(item) for item in data]
        elif isinstance(data, dict):
            return {k: self._sanitize_data_for_json(v) for k, v in data.items()}
        elif isinstance(data, (bool, int, float)):
            return data
        else:
            # Für andere Typen: zu String konvertieren und validieren
            try:
                s = str(data)
                json.dumps(s)
                return s
            except:
                return repr(data)

    async def _save_log_to_file(self, data, debug_type: DebugType):
        """Speichert LogForClient Events in ogb_data JSON-Datei.
        
        Args:
            data: Die Log-Daten
            debug_type: Der Typ (DEBUG, INFO, WARNING, ERROR)
        """
        async with self._log_file_lock:
            try:
                # ogb_data Verzeichnis ermitteln
                if hasattr(self.hass, 'config'):
                    ogb_data_dir = self.hass.config.path("ogb_data")
                else:
                    ogb_data_dir = "/config/ogb_data"
                
                os.makedirs(ogb_data_dir, exist_ok=True)
                
                log_file = os.path.join(ogb_data_dir, "client_logs.json")
                
                # Bestehende Logs laden oder neue Liste erstellen (async)
                logs = []
                if os.path.exists(log_file):
                    try:
                        content = await asyncio.to_thread(self._read_file, log_file)
                        if content:
                            logs = json.loads(content)
                    except (json.JSONDecodeError, Exception):
                        # Backup old file before reset
                        backup_file = log_file + ".backup"
                        try:
                            content = await asyncio.to_thread(self._read_file, log_file)
                            await asyncio.to_thread(self._write_file, backup_file, content)
                        except:
                            pass
                        _LOGGER.warning("client_logs.json war korrupt, starte neu")
                        logs = []
                
                # Dataclass in dict umwandeln falls nötig (mit Fallback)
                serializable_data = data
                try:
                    if is_dataclass(data) and not isinstance(data, type):
                        try:
                            serializable_data = asdict(data)
                        except Exception:
                            # Fallback: manuell alle Felder extrahieren
                            serializable_data = {}
                            for field in getattr(data, '__dataclass_fields__', {}).keys():
                                try:
                                    value = getattr(data, field)
                                    serializable_data[field] = value
                                except Exception:
                                    pass
                    elif hasattr(data, "to_dict"):
                        serializable_data = data.to_dict()
                    elif hasattr(data, "__dict__") and not isinstance(data, (list, tuple, dict)):
                        serializable_data = vars(data)
                    else:
                        serializable_data = str(data)
                except Exception as e:
                    _LOGGER.debug(f"Konnte Dataclass nicht konvertieren: {e}")
                    serializable_data = str(data)
                
                # Daten sanitizen, um korrupte JSON zu vermeiden
                serializable_data = self._sanitize_data_for_json(serializable_data)
                
                # Room aus Daten extrahieren - mehrere Quellen prüfen
                room = "unknown"
                
                # 1. Erst vom Original-Data-Objekt (bevor es zum String wird)
                if hasattr(data, "room") and data.room:
                    room = str(data.room)
                elif hasattr(data, "Name") and data.Name:
                    room_name = str(data.Name)
                    # "VeggiTent - Medium: SOIL_1 Info" -> "VeggiTent"
                    if " - " in room_name:
                        room = room_name.split(" - ")[0]
                    else:
                        room = room_name
                
                # 2. Falls immer noch unknown, versuche es mit serializable_data
                if room == "unknown" and isinstance(serializable_data, dict):
                    room = serializable_data.get("room") or serializable_data.get("Room") or serializable_data.get("Name") or "unknown"
                    # Extrahiere Room aus "Name" falls es ein String-Dict ist
                    if isinstance(room, str) and " - " in room:
                        room = room.split(" - ")[0]
                
                # 3. Falls immer noch "unknown", versuche den Room aus einem String-Pattern zu extrahieren
                if room == "unknown" and isinstance(serializable_data, str):
                    import re
                    # Suche nach "room': 'VeggiTent'" oder 'room': "VeggiTent"
                    match = re.search(r"['\"]room['\"]:\s*['\"](\w+)['\"]", serializable_data)
                    if match:
                        room = match.group(1)
                    else:
                        # Suche nach "Name': 'VeggiTent"
                        match = re.search(r"['\"]Name['\"]:\s*['\"](\w+)", serializable_data)
                        if match:
                            room = match.group(1)
                
                log_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "room": room,
                    "type": debug_type,
                    "data": serializable_data
                }
                
                logs.append(log_entry)
                
                # Maximal 1000 Einträge behalten (älteste löschen)
                if len(logs) > 1000:
                    logs = logs[-1000:]
                
                # Speichern (async)
                json_string = json.dumps(logs, indent=2, ensure_ascii=False)
                await asyncio.to_thread(self._write_file, log_file, json_string)
            except Exception as e:
                _LOGGER.error(f"Fehler beim Speichern des LogForClient: {e}")

    async def get_client_logs(self, room_filter: str = None, limit: int = 200):
        """Liest gespeicherte LogForClient Events aus der JSON-Datei.
        
        Args:
            room_filter: Optionaler Room-Filter
            limit: Maximale Anzahl Einträge (default 200)
            
        Returns:
            Liste von Log-Einträgen
        """
        try:
            if hasattr(self.hass, 'config'):
                ogb_data_dir = self.hass.config.path("ogb_data")
            else:
                ogb_data_dir = "/config/ogb_data"
            
            log_file = os.path.join(ogb_data_dir, "client_logs.json")
            
            if not os.path.exists(log_file):
                return []
            
            content = await asyncio.to_thread(self._read_file, log_file)
            if not content:
                return []
            
            logs = json.loads(content)
            
            # Room-Filter temporär deaktiviert - zeige alle Logs
            # if room_filter:
            #     room_lower = room_filter.lower()
            #     logs = [l for l in logs if str(l.get("room", "")).lower() == room_lower]
            
            # Nur die neuesten limit Einträge
            return logs[-limit:] if len(logs) > limit else logs
            
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Client-Logs Datei ist beschädigt (JSON-Fehler): {e}")
            # Versuch, Backup wiederherzustellen
            try:
                if hasattr(self.hass, 'config'):
                    ogb_data_dir = self.hass.config.path("ogb_data")
                else:
                    ogb_data_dir = "/config/ogb_data"
                backup_file = os.path.join(ogb_data_dir, "client_logs.json.backup")
                if os.path.exists(backup_file):
                    _LOGGER.info("Versuche Backup wiederherzustellen...")
                    backup_content = await asyncio.to_thread(self._read_file, backup_file)
                    if backup_content:
                        logs = json.loads(backup_content)
                        # Restore backup as main file
                        import tempfile
                        import shutil
                        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', 
                                                         dir=ogb_data_dir or ".", 
                                                         prefix='client_logs.json.tmp',
                                                         delete=False) as temp_file:
                            temp_file.write(backup_content)
                            temp_file.flush()
                            os.fsync(temp_file.fileno())
                            temp_path = temp_file.name
                        os.replace(temp_path, os.path.join(ogb_data_dir, "client_logs.json"))
                        _LOGGER.info("Backup erfolgreich wiederhergestellt")
                        return logs[-limit:] if len(logs) > limit else logs
            except Exception as backup_error:
                _LOGGER.error(f"Konnte Backup nicht wiederherstellen: {backup_error}")
            return []
        except Exception as e:
            _LOGGER.error(f"Fehler beim Lesen der Client-Logs: {e}")
            return []

    async def handle_get_logs(self, event):
        """Event-Handler für getOGBClientLogs."""
        try:
            _LOGGER.info(f"handle_get_logs called with event: {event}")
            event_data = getattr(event, "data", {}) or {}
            request_id = event_data.get("requestId") or event_data.get("request_id")
            room_filter = event_data.get("room")
            limit = event_data.get("limit", 200)
            
            _LOGGER.info(f"Request: requestId={request_id}, room={room_filter}, limit={limit}")
            
            logs = await self.get_client_logs(room_filter=room_filter, limit=limit)
            
            _LOGGER.info(f"Found {len(logs)} logs, firing response event")
            
            if hasattr(self.hass, "bus"):
                self.hass.bus.fire(GET_LOGS_RESPONSE_EVENT, {
                    "requestId": request_id,
                    "success": True,
                    "logs": logs,
                    "count": len(logs)
                })
                _LOGGER.info(f"Gesendet {len(logs)} Client-Logs für Raum: {room_filter or 'alle'}")
            else:
                _LOGGER.error("No hass.bus available!")
                
        except Exception as e:
            _LOGGER.error(f"Fehler bei handle_get_logs: {e}", exc_info=True)
            if hasattr(self.hass, "bus"):
                self.hass.bus.fire(GET_LOGS_RESPONSE_EVENT, {
                    "success": False,
                    "error": str(e)
                })
    
    def _read_file(self, filepath: str) -> str:
        """Synchroner File-Read für asyncio.to_thread"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    
    def _write_file(self, filepath: str, content: str):
        """Synchroner File-Write für asyncio.to_thread mit atomic write support"""
        import tempfile
        import shutil
        
        # Temporäre Datei im selben Verzeichnis erstellen
        dir_name = os.path.dirname(filepath) or "."
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', 
                                         dir=dir_name, 
                                         prefix=os.path.basename(filepath) + '.tmp',
                                         delete=False) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = temp_file.name
        
        # Atomic rename
        try:
            os.replace(temp_path, filepath)
        except Exception:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise

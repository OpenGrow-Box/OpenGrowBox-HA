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
        """Register a listener (synchronous or asynchronous) for a specific event."""
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        # MEMORY FIX: Prevent duplicate listeners
        if callback not in self.listeners[event_name]:
            self.listeners[event_name].append(callback)

    def remove(self, event_name, callback):
        """Remove a specific listener."""
        if event_name in self.listeners and callback in self.listeners[event_name]:
            self.listeners[event_name].remove(callback)

    def remove_all(self, event_name=None):
        """Remove all listeners for an event or all events."""
        if event_name:
            self.listeners.pop(event_name, None)
        else:
            self.listeners.clear()

    async def _call_listener(self, callback, data):
        """Call a listener, synchronous or asynchronous."""
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            _LOGGER.error(f"Error calling listener for '{callback}': {e}")

    async def emit(self, event_name, data, haEvent=False, debug_type: Optional[DebugType] = None):
        """Emit an event, incl. optional HA event and notification.
        
        Args:
            event_name: Name of the event
            data: Event data
            haEvent: If True, send event to Home Assistant
            debug_type: Optional type for LogForClient (DEBUG, INFO, WARNING, ERROR)
        """
        
        # Don't emit events during shutdown
        if self._shutdown:
            return
        
        # Debug log for medium-related events
        if "Medium" in event_name or "Plant" in event_name:
            _LOGGER.debug(f"📢 EMIT: {event_name} - listeners: {len(self.listeners.get(event_name, []))}, haEvent: {haEvent}")

        # LogForClient: save to ogb_data with debug_type (async)
        if event_name == "LogForClient":
            # Determine debug_type with intelligent fallback logic
            effective_debug_type = debug_type
            
            # If no explicit debug_type, try to extract from payload
            if not effective_debug_type:
                if isinstance(data, dict):
                    # Check various fields in the payload
                    if data.get("Type") in ("DEBUG", "INFO", "WARNING", "ERROR"):
                        effective_debug_type = data.get("Type")
                    elif data.get("Warning"):
                        effective_debug_type = "WARNING"
                    elif data.get("Error") or data.get("error"):
                        effective_debug_type = "ERROR"
                elif isinstance(data, str):
                    # Check string contents for warnings/errors
                    lower_data = data.lower()
                    if "error" in lower_data or "failed" in lower_data or "exception" in lower_data:
                        effective_debug_type = "ERROR"
                    elif "warning" in lower_data or "blocked" in lower_data or "attention" in lower_data:
                        effective_debug_type = "WARNING"
                    elif "debug" in lower_data:
                        effective_debug_type = "DEBUG"
                
                # Fallback to INFO if nothing found
                effective_debug_type = effective_debug_type or "INFO"
            
            self._create_tracked_task(self._save_log_to_file(data, effective_debug_type))

        if haEvent:
            # MEMORY FIX: Track the task
            self._create_tracked_task(self.emit_to_home_assistant(event_name, data, debug_type))
            if self.notifications_enabled:
                # Determine effective_debug_type for notification filter
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
                        _LOGGER.error(f"Error in synchronous listener: {e}")
        elif "Medium" in event_name or "Plant" in event_name:
            _LOGGER.debug(f"ℹ️ No listeners registered for {event_name}")

    def emit_sync(self, event_name, data, haEvent=False, debug_type: Optional[DebugType] = None):
        """Emit an event synchronously (for synchronous contexts).
        If haEvent=True, the event is also sent to Home Assistant.
        
        Args:
            event_name: Name of the event
            data: Event data
            haEvent: If True, send event to Home Assistant
            debug_type: Optional type for LogForClient (DEBUG, INFO, WARNING, ERROR)
        """
        asyncio.create_task(self.emit(event_name, data, haEvent, debug_type))

    async def emit_to_home_assistant(self, event_name, event_data, debug_type: Optional[DebugType] = None):
        """Send an event to Home Assistant via the event bus.
        
        Args:
            event_name: Name of the event
            event_data: Event data
            debug_type: Optional type for LogForClient (inserted into event data)
        """
        try:
            # If event_data is a dataclass object, convert to a dictionary
            if is_dataclass(event_data):
                event_data = asdict(event_data)
            
            # Insert DebugType into event data if available
            if debug_type and isinstance(event_data, dict):
                event_data["DebugType"] = debug_type
            elif debug_type and isinstance(event_data, str):
                event_data = {"Message": event_data, "DebugType": debug_type}

            if hasattr(self.hass, "bus"):
                self.hass.bus.fire(event_name, event_data)
                _LOGGER.debug(f"Event-bus event '{event_name}' sent successfully.")
            else:
                _LOGGER.error(
                    f"No valid event channel available for '{event_name}'!"
                )
        except Exception as e:
            _LOGGER.error(f"Error sending event '{event_name}': {e}")

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
        Send a push notification via notify.notify to all configured notifiers.
        Filters by logType from DataStore.
        """
        try:
            # Determine the log type from the data
            effective_type = debug_type
            
            # Try to extract from the data
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
            
            # Check logType filter from DataStore
            allowed_types = self._get_allowed_notification_types()
            
            # Only send DEBUG if explicitly allowed
            if effective_type == "DEBUG" and "DEBUG" not in allowed_types:
                _LOGGER.debug(f"DEBUG notification suppressed (not in logType): {title}")
                return
            
            # Only send INFO if allowed
            if effective_type == "INFO" and "INFO" not in allowed_types:
                _LOGGER.debug(f"INFO notification suppressed (not in logType): {title}")
                return
            
            # Only send WARNING if allowed
            if effective_type == "WARNING" and "WARNING" not in allowed_types:
                _LOGGER.debug(f"WARNING notification suppressed (not in logType): {title}")
                return
            
            # Always send ERROR (important!)
            if effective_type == "ERROR":
                pass  # No filtering for errors
            
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
            _LOGGER.debug(f"Push notification for '{title}' sent (type: {effective_type}).")
        except Exception as e:
            _LOGGER.error(f"Error sending push notification: {e}")
    
    def _extract_debug_type_from_data(self, data) -> str:
        """Extract debug_type from data (for internal use)."""
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
        Read logType from DataStore and return list of allowed types.
        """
        try:
            # Try to access data_store via ogb_model
            if hasattr(self, 'ogb_model') and self.ogb_model:
                data_store = getattr(self.ogb_model, 'data_store', None)
                if data_store:
                    log_type = data_store.get("logType")
                    if log_type:
                        # Parse CSV string like "INFO,WARNING,ERROR"
                        return [t.strip().upper() for t in log_type.split(",") if t.strip()]
            
            # Fallback: default allows WARNING and ERROR
            return ["WARNING", "ERROR"]
        except Exception as e:
            _LOGGER.debug(f"Konnte logType nicht lesen: {e}")
            return ["WARNING", "ERROR"]

    def change_notify_set(self, state):
        self.notifications_enabled = state
        _LOGGER.debug(f"Notify State jetzt: {self.notifications_enabled}")

    async def async_shutdown(self):
        """Shutdown event manager and cleanup all resources."""
        _LOGGER.debug("🛑 Shutting down EventManager")
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
        _LOGGER.debug(f"✅ EventManager shutdown complete, cleared {listener_count} listeners")

    def _sanitize_data_for_json(self, data):
        """Clean data for JSON storage to avoid broken strings."""
        if data is None:
            return None
        elif isinstance(data, str):
            # String cleanup: ensure the string is valid
            try:
                # Check if the string itself would be valid JSON
                json.dumps(data)
                return data
            except (TypeError, ValueError):
                # If not, try to clean the string
                try:
                    # Replace unescaped quotes in strings
                    cleaned = str(data)
                    # Replace carriage returns and other problematic characters
                    cleaned = cleaned.replace('\r', '\\r').replace('\n', '\\n')
                    # Check again
                    json.dumps(cleaned)
                    return cleaned
                except:
                    # As a last resort: use repr(), which always works
                    return repr(str(data))
        elif isinstance(data, (list, tuple)):
            return [self._sanitize_data_for_json(item) for item in data]
        elif isinstance(data, dict):
            return {k: self._sanitize_data_for_json(v) for k, v in data.items()}
        elif isinstance(data, (bool, int, float)):
            return data
        else:
            # For other types: convert to string and validate
            try:
                s = str(data)
                json.dumps(s)
                return s
            except:
                return repr(data)

    async def _save_log_to_file(self, data, debug_type: DebugType):
        """Save LogForClient events to the ogb_data JSON file.
        
        Args:
            data: The log data
            debug_type: The type (DEBUG, INFO, WARNING, ERROR)
        """
        async with self._log_file_lock:
            try:
                # Determine ogb_data directory
                if hasattr(self.hass, 'config'):
                    ogb_data_dir = self.hass.config.path("ogb_data")
                else:
                    ogb_data_dir = "/config/ogb_data"
                
                os.makedirs(ogb_data_dir, exist_ok=True)
                
                log_file = os.path.join(ogb_data_dir, "client_logs.json")
                
                # Load existing logs or create new list (async)
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
                
                # Convert dataclass to dict if necessary (with fallback)
                serializable_data = data
                try:
                    if is_dataclass(data) and not isinstance(data, type):
                        try:
                            serializable_data = asdict(data)
                        except Exception:
                            # Fallback: manually extract all fields
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
                
                # Sanitize data to avoid corrupt JSON
                serializable_data = self._sanitize_data_for_json(serializable_data)
                
                # Extract room from data - check multiple sources
                room = "unknown"
                
                # 1. First from the original data object (before it becomes a string)
                if hasattr(data, "room") and data.room:
                    room = str(data.room)
                elif hasattr(data, "Name") and data.Name:
                    room_name = str(data.Name)
                    # "VeggiTent - Medium: SOIL_1 Info" -> "VeggiTent"
                    if " - " in room_name:
                        room = room_name.split(" - ")[0]
                    else:
                        room = room_name
                
                # 2. If still unknown, try with serializable_data
                if room == "unknown" and isinstance(serializable_data, dict):
                    room = serializable_data.get("room") or serializable_data.get("Room") or serializable_data.get("Name") or "unknown"
                    # Extract room from "Name" if it's a string dict
                    if isinstance(room, str) and " - " in room:
                        room = room.split(" - ")[0]
                
                # 3. If still "unknown", try to extract room from a string pattern
                if room == "unknown" and isinstance(serializable_data, str):
                    import re
                    # Search for "room': 'VeggiTent'" or 'room': "VeggiTent"
                    match = re.search(r"['\"]room['\"]:\s*['\"](\w+)['\"]", serializable_data)
                    if match:
                        room = match.group(1)
                    else:
                        # Search for "Name': 'VeggiTent"
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
                
                # Keep max 1000 entries (delete oldest)
                if len(logs) > 1000:
                    logs = logs[-1000:]
                
                # Save (async)
                json_string = json.dumps(logs, indent=2, ensure_ascii=False)
                await asyncio.to_thread(self._write_file, log_file, json_string)
            except Exception as e:
                _LOGGER.error(f"Error saving LogForClient: {e}")

    async def get_client_logs(self, room_filter: str = None, limit: int = 200):
        """Read stored LogForClient events from the JSON file.
        
        Args:
            room_filter: Optional room filter
            limit: Maximum number of entries (default 200)
            
        Returns:
            List of log entries
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
            
            # Room filter temporarily disabled - show all logs
            # if room_filter:
            #     room_lower = room_filter.lower()
            #     logs = [l for l in logs if str(l.get("room", "")).lower() == room_lower]
            
            # Only the latest limit entries
            return logs[-limit:] if len(logs) > limit else logs
            
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Client logs file is corrupted (JSON error): {e}")
            # Attempt to restore backup
            try:
                if hasattr(self.hass, 'config'):
                    ogb_data_dir = self.hass.config.path("ogb_data")
                else:
                    ogb_data_dir = "/config/ogb_data"
                backup_file = os.path.join(ogb_data_dir, "client_logs.json.backup")
                if os.path.exists(backup_file):
                    _LOGGER.debug("Versuche Backup wiederherzustellen...")
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
                        _LOGGER.debug("Backup restored successfully")
                        return logs[-limit:] if len(logs) > limit else logs
            except Exception as backup_error:
                _LOGGER.error(f"Konnte Backup nicht wiederherstellen: {backup_error}")
            return []
        except Exception as e:
            _LOGGER.error(f"Error reading client logs: {e}")
            return []

    async def handle_get_logs(self, event):
        """Event handler for getOGBClientLogs."""
        try:
            _LOGGER.debug(f"handle_get_logs called with event: {event}")
            event_data = getattr(event, "data", {}) or {}
            request_id = event_data.get("requestId") or event_data.get("request_id")
            room_filter = event_data.get("room")
            limit = event_data.get("limit", 200)
            
            _LOGGER.debug(f"Request: requestId={request_id}, room={room_filter}, limit={limit}")
            
            logs = await self.get_client_logs(room_filter=room_filter, limit=limit)
            
            _LOGGER.debug(f"Found {len(logs)} logs, firing response event")
            
            if hasattr(self.hass, "bus"):
                self.hass.bus.fire(GET_LOGS_RESPONSE_EVENT, {
                    "requestId": request_id,
                    "success": True,
                    "logs": logs,
                    "count": len(logs)
                })
                _LOGGER.debug(f"Sent {len(logs)} client logs for room: {room_filter or 'all'}")
            else:
                _LOGGER.error("No hass.bus available!")
                
        except Exception as e:
            _LOGGER.error(f"Error in handle_get_logs: {e}", exc_info=True)
            if hasattr(self.hass, "bus"):
                self.hass.bus.fire(GET_LOGS_RESPONSE_EVENT, {
                    "success": False,
                    "error": str(e)
                })
    
    def _read_file(self, filepath: str) -> str:
        """Synchronous file read for asyncio.to_thread."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    
    def _write_file(self, filepath: str, content: str):
        """Synchronous file write for asyncio.to_thread with atomic write support."""
        import tempfile
        import shutil
        
        # Create temporary file in the same directory
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

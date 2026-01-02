"""
OpenGrowBox Premium WebSocket Session Module

Handles session management including:
- Session rotation
- Session restore
- Session key management
- Keep-alive system

This module provides a mixin class that can be used with the main WebSocket client.
"""

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

import aiohttp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from ...utils.Premium.SecureWebSocketClient import OGBWebSocketConManager

_LOGGER = logging.getLogger(__name__)


class OGBPremWebSocketSessionMixin:
    """
    Mixin class providing session management functionality for WebSocket clients.

    This mixin expects the following attributes on the parent class:
    - _session_key: bytes
    - _session_id: str
    - _aes_gcm: AESGCM
    - _user_id: str
    - _access_token: str
    - _rotation_lock: asyncio.Lock
    - _rotation_in_progress: bool
    - _rotation_start_time: float
    - _rotation_task: asyncio.Task
    - _keepalive_task: asyncio.Task
    - _keepalive_interval: int
    - _pong_timeout: int
    - _pong_received: bool
    - _pong_event: asyncio.Event
    - _last_pong_time: float
    - ws_room: str
    - room_id: str
    - api_url: str
    - headers: dict
    - client_id: str
    - sio: socketio.AsyncClient
    - ogbevents: EventManager
    - is_logged_in: bool
    - is_premium: bool
    - subscription_data: dict
    - authenticated: bool
    - ws_connected: bool

    And the following methods:
    - _safe_b64_decode()
    - _connect_websocket()
    - is_connected()
    """

    # =================================================================
    # Session Restore
    # =================================================================

    async def session_restore(
        self, stored_session_data: dict = None, event_id: str = None
    ) -> bool:
        """
        Restore a session from stored data or refresh current session.

        Args:
            stored_session_data: Previously saved session data
            event_id: Event ID for response tracking

        Returns:
            True if session was restored successfully
        """
        try:
            _LOGGER.debug(
                f"Attempting session restore for {self.ws_room} with {stored_session_data}"
            )

            # Option 1: Restore from provided data
            if stored_session_data:
                return await self._restore_from_stored_data(
                    stored_session_data, event_id
                )

            # Option 2: Try to refresh current session if we have access token
            if self._access_token and not stored_session_data:
                _LOGGER.warning(
                    f"Attempting session refresh with existing access token for {self.ws_room}"
                )
                session_data = await self._request_session_key(event_id)
                if session_data and await self._connect_websocket():
                    await self._start_keepalive()
                    return True

            # Nothing to restore
            _LOGGER.warning(f"No valid session data to restore for {self.ws_room}")
            if event_id:
                await self._send_auth_response(
                    event_id, "error", "No session data to restore - please login again"
                )

            return False

        except Exception as e:
            _LOGGER.error(f"Session restore error for {self.ws_room}: {e}")
            return False

    async def _restore_from_stored_data(
        self, stored_session_data: dict, event_id: str = None
    ) -> bool:
        """
        Restore session from stored data dictionary.

        Args:
            stored_session_data: Dictionary with session data
            event_id: Event ID for response tracking

        Returns:
            True if restored successfully
        """
        user_id = stored_session_data.get("user_id")
        access_token = stored_session_data.get("access_token")
        is_logged_in = stored_session_data.get("is_logged_in", False)
        is_premium = stored_session_data.get("is_premium", False)
        subscription_data = stored_session_data.get("subscription_data", {})

        if not (user_id and access_token):
            _LOGGER.warning(
                f"Missing user_id or access_token in stored data for {self.ws_room}"
            )
            return False

        try:
            # Restore basic auth state
            self._user_id = user_id
            self._access_token = access_token
            self.is_logged_in = is_logged_in
            self.is_premium = is_premium
            self.subscription_data = subscription_data

            # Handle session key if available
            session_id = stored_session_data.get("session_id")
            session_key = stored_session_data.get("session_key")

            if session_id and session_key:
                if not await self._restore_session_key(session_id, session_key):
                    # Session key restore failed, will request new one below
                    pass

            # If no valid session key, request new one
            if not self._session_key:
                session_data = await self._request_session_key(event_id)
                if not session_data:
                    _LOGGER.error(
                        f"Failed to get new session key during restore for {self.ws_room}"
                    )
                    if event_id:
                        await self._send_auth_response(
                            event_id, "error", "Failed to establish session"
                        )
                    return False

            # Try to connect
            if await self._connect_websocket():
                if event_id:
                    self.authenticated = True
                    self.ws_connected = True
                    await self._send_auth_response(
                        event_id,
                        "success",
                        "Session restored successfully",
                        {"session_id": self._session_id, "user_id": self._user_id},
                    )
                return True
            else:
                _LOGGER.error(
                    f"WebSocket connection failed during restore for {self.ws_room}"
                )
                return False

        except Exception as e:
            _LOGGER.error(f"Session restore from data failed for {self.ws_room}: {e}")
            return False

    async def _restore_session_key(self, session_id: str, session_key) -> bool:
        """
        Restore a session key from stored data.

        Args:
            session_id: The session ID
            session_key: The session key (string or bytes)

        Returns:
            True if restored successfully
        """
        try:
            self._session_id = session_id
            if isinstance(session_key, str):
                self._session_key = self._safe_b64_decode(session_key)
            else:
                self._session_key = session_key

            if len(self._session_key) == 32:
                self._aes_gcm = AESGCM(self._session_key)
                _LOGGER.warning(
                    f"Session key restored from stored data for {self.ws_room}"
                )
                return True
            else:
                _LOGGER.warning(
                    f"Invalid stored session key for {self.ws_room}, will request new one"
                )
                self._clear_session_key()
                return False

        except Exception as e:
            _LOGGER.warning(
                f"Error restoring session key for {self.ws_room}: {e}, will request new one"
            )
            self._clear_session_key()
            return False

    def _clear_session_key(self):
        """Clear session key and related state."""
        self._session_key = None
        self._session_id = None
        self._aes_gcm = None

    # =================================================================
    # Session Key Request
    # =================================================================

    async def _request_session_key(
        self, event_id: str = None, room_id: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        Request a new session key from the server.

        Args:
            event_id: Event ID for tracking
            room_id: Optional room ID override

        Returns:
            Session data dict or None on failure
        """
        try:
            if not self._user_id or not self._access_token:
                _LOGGER.error(
                    f"Cannot request session - missing auth data for {self.ws_room}"
                )
                return None

            url = f"{self.api_url}/api/auth/create-session-for-device"

            request_data = {
                "user_id": self._user_id,
                "access_token": self._access_token,
                "client_id": self.client_id,
                "room_id": room_id or self.room_id,
                "room_name": self.ws_room,
            }

            _LOGGER.warning(f"Requesting new session key for {self.ws_room}")

            timeout_config = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(
                    url, json=request_data, headers=self.headers
                ) as response:

                    if response.status != 200:
                        _LOGGER.error(
                            f"Session request failed for {self.ws_room}: HTTP {response.status}"
                        )
                        return None

                    try:
                        result = json.loads(await response.text())
                    except json.JSONDecodeError as e:
                        _LOGGER.error(
                            f"Invalid JSON in session response for {self.ws_room}: {e}"
                        )
                        return None

                    if result.get("status") != "success":
                        _LOGGER.error(
                            f"Session request failed for {self.ws_room}: {result.get('message', 'Unknown error')}"
                        )
                        return None

                    session_data = {
                        "session_id": result.get("session_id"),
                        "session_key": result.get("session_key"),
                        "room_id": result.get("room_id"),
                        "client_id": result.get("client_id"),
                        "plan": result.get("plan"),
                        "timestamp": result.get("timestamp"),
                    }

                    # Update local session data
                    self._session_id = session_data["session_id"]

                    # Decode and store session key
                    session_key_b64 = session_data["session_key"]
                    try:
                        self._session_key = self._safe_b64_decode(session_key_b64)
                        if len(self._session_key) == 32:
                            self._aes_gcm = AESGCM(self._session_key)
                            _LOGGER.debug(
                                f"New session key established for {self.ws_room}: {self._session_id}"
                            )
                        else:
                            _LOGGER.error(
                                f"Invalid session key length received for {self.ws_room}"
                            )
                            return None
                    except Exception as e:
                        _LOGGER.error(
                            f"Session key processing error for {self.ws_room}: {e}"
                        )
                        return None

                    return session_data

        except Exception as e:
            _LOGGER.error(f"Session key request error for {self.ws_room}: {e}")
            return None

    # =================================================================
    # Session Rotation
    # =================================================================

    async def _handle_session_rotation(self, rotation_data: dict):
        """
        Handle a session rotation request from the server.

        Args:
            rotation_data: Dictionary containing old_session_id, new_session_id, new_session_key
        """
        if self._rotation_in_progress:
            _LOGGER.debug(f"Rotation already in progress for {self.ws_room}")
            return

        self._rotation_in_progress = True
        self._rotation_start_time = time.time()

        old_session_id = rotation_data.get("old_session_id")
        new_session_id = rotation_data.get("new_session_id")
        new_session_key_b64 = rotation_data.get("new_session_key")

        try:
            _LOGGER.debug(
                f"Starting session rotation for {self.ws_room}: {old_session_id} -> {new_session_id}"
            )

            # Validate rotation applies to our current session
            if old_session_id != self._session_id:
                _LOGGER.debug(
                    f"Rotation not for our session ({self._session_id}), ignoring"
                )
                self._rotation_in_progress = False
                return

            # Decode and validate new session key
            try:
                new_session_key = self._safe_b64_decode(new_session_key_b64)
                if len(new_session_key) != 32:
                    raise ValueError(
                        f"Invalid session key length: {len(new_session_key)}"
                    )
                new_aes_gcm = AESGCM(new_session_key)
            except Exception as e:
                _LOGGER.error(
                    f"Failed to decode new session key for {self.ws_room}: {e}"
                )
                await self._rotation_failed(old_session_id, "key_decode_error")
                return

            # Store old values for rollback
            old_session_key = self._session_key
            old_aes_gcm = self._aes_gcm

            # Apply new session
            self._session_id = new_session_id
            self._session_key = new_session_key
            self._aes_gcm = new_aes_gcm

            # Test new session
            test_success = await self._test_new_session_with_timeout()
            if not test_success:
                _LOGGER.debug(
                    f"New session test failed for {self.ws_room}, rolling back"
                )
                # Rollback
                self._session_id = old_session_id
                self._session_key = old_session_key
                self._aes_gcm = old_aes_gcm
                await self._rotation_failed(old_session_id, "session_test_failed")
                return

            # Acknowledge rotation to server
            try:
                await self.sio.emit(
                    "session_rotation_acknowledged",
                    {
                        "old_session_id": old_session_id,
                        "new_session_id": new_session_id,
                        "immediate_cleanup_requested": True,
                        "cleanup_confirmed": True,
                        "rotation_duration": time.time() - self._rotation_start_time,
                        "timestamp": time.time(),
                    },
                )

                _LOGGER.debug(f"Sent rotation acknowledgment for {self.ws_room}")
            except Exception as e:
                _LOGGER.warning(f"Socket acknowledgment failed for {self.ws_room}: {e}")

            # Final verification
            await asyncio.sleep(1)
            final_test = await self._test_new_session_with_timeout(timeout=5)

            if final_test:
                _LOGGER.warning(
                    f"Session rotation completed successfully for {self.ws_room}"
                )
                await self.ogbevents.emit(
                    "LogForClient",
                    {
                        "Name": f"{self.ws_room} - Session rotation completed successfully",
                        "rotation_success": True,
                    },
                    haEvent=True,
                )
                await self.ogbevents.emit("SaveRequest", True)
            else:
                _LOGGER.warning(
                    f"Final session test failed for {self.ws_room}, but rotation complete"
                )

        except Exception as e:
            _LOGGER.error(f"Session rotation process error for {self.ws_room}: {e}")
            await self._rotation_failed(old_session_id, f"process_error: {str(e)}")

        finally:
            self._rotation_in_progress = False
            self._rotation_start_time = None

    async def _test_new_session_with_timeout(self, timeout: int = 10) -> bool:
        """
        Test a new session with a configurable timeout.

        Args:
            timeout: Timeout in seconds

        Returns:
            True if session test succeeded
        """
        try:
            if not self.is_connected():
                _LOGGER.warning(f"Not connected during session test for {self.ws_room}")
                return False

            # Send test ping
            test_data = {
                "timestamp": time.time(),
                "room": self.ws_room,
                "test_type": "session_rotation_validation",
                "session_id": self._session_id,
            }

            # Send test ping with event signaling
            self._pong_received = False
            self._pong_event.clear()
            ping_time = time.time()
            await self.sio.emit("ses_test", test_data)

            # Wait for pong with proper event signaling
            try:
                await asyncio.wait_for(self._pong_event.wait(), timeout=timeout)
                if self._pong_received:
                    duration = time.time() - ping_time
                    _LOGGER.warning(
                        f"Session test successful for {self.ws_room} in {duration:.2f}s"
                    )
                    return True
            except asyncio.TimeoutError:
                pass

            _LOGGER.warning(f"Session test timeout after {timeout}s for {self.ws_room}")
            return False

        except Exception as e:
            _LOGGER.error(f"Session test error for {self.ws_room}: {e}")
            return False

    async def _acknowledge_session_rotation(
        self, old_session_id: str, new_session_id: str
    ) -> bool:
        """
        Send HTTP acknowledgment for session rotation with retry logic.

        Args:
            old_session_id: The old session ID
            new_session_id: The new session ID

        Returns:
            True if acknowledgment was successful
        """
        max_retries = 3

        for attempt in range(max_retries):
            try:
                url = f"{self.api_url}/api/auth/acknowledge-rotation"

                request_data = {
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id,
                    "user_id": self._user_id,
                    "access_token": self._access_token,
                    "immediate_cleanup": True,
                    "client_confirmed": True,
                    "rotation_attempt": attempt + 1,
                }

                timeout_config = aiohttp.ClientTimeout(total=8)

                async with aiohttp.ClientSession(timeout=timeout_config) as session:
                    async with session.post(
                        url, json=request_data, headers=self.headers
                    ) as response:

                        if response.status == 200:
                            try:
                                result = await response.json()
                                if result.get("status") == "success":
                                    _LOGGER.warning(
                                        f"HTTP acknowledgment successful for {self.ws_room} (attempt {attempt + 1})"
                                    )
                                    await self.ogbevents.emit("SaveRequest", True)
                                    return True
                                else:
                                    _LOGGER.error(
                                        f"HTTP acknowledgment failed for {self.ws_room}: {result.get('message')}"
                                    )

                            except json.JSONDecodeError:
                                _LOGGER.error(
                                    f"Invalid HTTP acknowledgment response for {self.ws_room}"
                                )
                        else:
                            _LOGGER.error(
                                f"HTTP acknowledgment HTTP error for {self.ws_room}: {response.status}"
                            )

                        # If not last attempt, wait before retry
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1)
                            continue

                        return False

            except Exception as e:
                _LOGGER.error(
                    f"HTTP acknowledgment attempt {attempt + 1} failed for {self.ws_room}: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                continue

        return False

    async def _rotation_failed(self, old_session_id: str, reason: str):
        """
        Handle rotation failure with proper cleanup.

        Args:
            old_session_id: The old session ID
            reason: Reason for the failure
        """
        _LOGGER.error(f"Session rotation failed for {self.ws_room}: {reason}")

        try:
            # Notify server of failure
            await self.sio.emit(
                "session_rotation_error",
                {
                    "old_session_id": old_session_id,
                    "failure_reason": reason,
                    "timestamp": time.time(),
                    "request_rollback": True,
                },
            )
        except Exception as e:
            _LOGGER.error(
                f"Failed to send rotation error notification for {self.ws_room}: {e}"
            )

        # Reset rotation state
        self._rotation_in_progress = False
        self._rotation_start_time = None

        # Notify application
        await self.ogbevents.emit(
            "LogForClient",
            f"Session rotation failed for {self.ws_room}: {reason}",
            haEvent=True,
        )

    # =================================================================
    # Keep-Alive System
    # =================================================================

    async def _start_keepalive(self):
        """Start the unified keep-alive system."""
        await self._stop_keepalive()
        if self._keepalive_task and not self._keepalive_task.done():
            return

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        _LOGGER.debug(f"Keep-alive started for {self.ws_room}")

    async def _stop_keepalive(self):
        """Stop the keep-alive system."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        _LOGGER.debug(f"Keep-alive stopped for {self.ws_room}")

    async def _keepalive_loop(self):
        """
        Unified keep-alive and health monitoring loop.

        Sends periodic ping messages and monitors pong responses.
        Triggers reconnection after consecutive failures.
        """
        consecutive_failures = 0
        max_consecutive_failures = 3

        try:
            while self.sio and self.sio.connected and self.authenticated:
                await asyncio.sleep(self._keepalive_interval)

                if not self.sio.connected or not self.authenticated:
                    break

                try:
                    # Send ping
                    ping_data = {
                        "timestamp": time.time(),
                        "room": self.ws_room,
                        "health_check": True,
                    }

                    await self.sio.emit("ping", ping_data)
                    _LOGGER.debug(f"Sent health ping for {self.ws_room}")

                    # Wait for pong with timeout
                    pong_received = await self._wait_for_pong(self._pong_timeout)

                    if not pong_received:
                        consecutive_failures += 1
                        _LOGGER.warning(
                            f"Health check failed {consecutive_failures}/{max_consecutive_failures} "
                            f"for {self.ws_room} - no pong received"
                        )

                        if consecutive_failures >= max_consecutive_failures:
                            _LOGGER.error(
                                f"Health check permanently failed for {self.ws_room} - triggering reconnection"
                            )
                            await self._handle_connection_loss(
                                "health_check_permanent_failure"
                            )
                            break
                    else:
                        # Reset consecutive failures on successful pong
                        if consecutive_failures > 0:
                            _LOGGER.info(
                                f"Health check recovered for {self.ws_room} after {consecutive_failures} failures"
                            )
                        consecutive_failures = 0
                        _LOGGER.debug(f"Health check OK for {self.ws_room}")

                except Exception as e:
                    consecutive_failures += 1
                    _LOGGER.error(
                        f"Keep-alive error {consecutive_failures}/{max_consecutive_failures} "
                        f"for {self.ws_room}: {e}"
                    )

                    if consecutive_failures >= max_consecutive_failures:
                        _LOGGER.error(
                            f"Keep-alive permanently failed for {self.ws_room} - triggering reconnection"
                        )
                        await self._handle_connection_loss("keepalive_permanent_error")
                        break

        except asyncio.CancelledError:
            _LOGGER.debug(f"Keep-alive cancelled for {self.ws_room}")
        except Exception as e:
            _LOGGER.error(f"Keep-alive loop error for {self.ws_room}: {e}")

    async def _wait_for_pong(self, timeout: float) -> bool:
        """
        Wait for pong response with proper event signaling.

        Args:
            timeout: Timeout in seconds

        Returns:
            True if pong was received
        """
        self._pong_received = False
        self._pong_event.clear()

        try:
            await asyncio.wait_for(self._pong_event.wait(), timeout=timeout)
            return self._pong_received
        except asyncio.TimeoutError:
            return False

    # =================================================================
    # Session Status & Backup
    # =================================================================

    def get_session_status(self) -> dict:
        """
        Get current session status with rotation information.

        Returns:
            Dictionary with session status
        """
        return {
            "session_id": self._session_id,
            "has_session_key": bool(self._session_key),
            "rotation_in_progress": self._rotation_in_progress,
            "rotation_start_time": getattr(self, "_rotation_start_time", None),
            "rotation_duration": (
                (time.time() - self._rotation_start_time)
                if getattr(self, "_rotation_start_time", None)
                else None
            ),
            "connected": self.is_connected(),
            "authenticated": self.authenticated,
            "room": self.ws_room,
            "user_id": self._user_id,
            "last_pong_time": self._last_pong_time,
            "keepalive_running": bool(
                self._keepalive_task and not self._keepalive_task.done()
            ),
        }

    def get_session_backup_data(self) -> dict:
        """
        Get session backup data for persistence.

        Returns:
            Dictionary with all data needed to restore session
        """
        if not self.is_logged_in:
            return {}

        return {
            "user_id": self._user_id,
            "session_id": self._session_id,
            "session_key": (
                base64.urlsafe_b64encode(self._session_key).decode()
                if self._session_key
                else None
            ),
            "access_token": self._access_token,
            "token_expires_at": getattr(self, "token_expires_at", None),
            "access_token_hash": (
                hashlib.sha256(self._access_token.encode()).hexdigest()
                if self._access_token
                else None
            ),
            "ogb_sessions": getattr(self, "ogb_sessions", 0),
            "ogb_max_sessions": getattr(self, "ogb_max_sessions", None),
            "is_premium": self.is_premium,
            "is_logged_in": self.is_logged_in,
            "authenticated": self.authenticated,
            "subscription_data": self.subscription_data,
            "plan": self.subscription_data.get("plan_name", "free"),
            "created_at": time.time(),
            "client_id": self.client_id,
            "backup_version": "2.0",
            "rotation_capable": True,
        }

    async def health_check(self) -> dict:
        """
        Perform a comprehensive health check.

        Returns:
            Dictionary with health status
        """
        return {
            "room": self.ws_room,
            "connected": self.is_connected(),
            "ready": self.is_ready() if hasattr(self, "is_ready") else False,
            "authenticated": self.authenticated,
            "is_premium": self.is_premium,
            "session_valid": bool(self._session_key and self._session_id),
            "reconnect_attempts": getattr(self, "reconnect_attempts", 0),
            "reconnection_in_progress": getattr(
                self, "_reconnection_in_progress", False
            ),
            "rotation_in_progress": self._rotation_in_progress,
            "keepalive_running": bool(
                self._keepalive_task and not self._keepalive_task.done()
            ),
            "user_id": self._user_id,
            "last_pong": self._last_pong_time,
            "timestamp": time.time(),
        }

"""
OpenGrowBox Premium WebSocket Authentication Module

Handles authentication including:
- Login flow (email + OGBToken)
- Dev login flow
- Session establishment from auth data
- Cleanup and logout

This module provides a mixin class that can be used with the main WebSocket client.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

import aiohttp
import socketio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from ...utils.Premium.SecureWebSocketClient import OGBWebSocketConManager

_LOGGER = logging.getLogger(__name__)


class OGBPremWebSocketAuthMixin:
    """
    Mixin class providing authentication functionality for WebSocket clients.

    This mixin expects the following attributes on the parent class:
    - _connection_lock: asyncio.Lock
    - _user_id: str
    - _session_id: str
    - _access_token: str
    - _session_key: bytes
    - _aes_gcm: AESGCM
    - token_expires_at: str
    - tenant_id: str
    - is_premium: bool
    - is_logged_in: bool
    - subscription_data: dict
    - ogb_sessions: int
    - ogb_max_sessions: int
    - authenticated: bool
    - ws_connected: bool
    - ws_room: str
    - room_id: str
    - client_id: str
    - api_url: str
    - login_url: str
    - dev_login_url: str
    - headers: dict
    - sio: socketio.AsyncClient
    - ogbevents: EventManager
    - reconnect_task: asyncio.Task
    - _rotation_task: asyncio.Task
    - _reconnection_in_progress: bool
    - _rotation_in_progress: bool
    - _should_reconnect: bool
    - _reconnect_delay: int
    - _last_pong_time: float
    - _ping_task: asyncio.Task
    - user_data: dict

    And the following methods:
    - _safe_b64_decode()
    - _connect_websocket()
    - _start_keepalive()
    - _stop_keepalive()
    - _request_session_key()
    - _send_auth_response()
    - _setup_event_handlers()
    """

    # =================================================================
    # Login Flow
    # =================================================================

    async def login_and_connect(
        self,
        email: str,
        OGBToken: str,
        room_id: str,
        room_name: str,
        event_id: str = None,
        auth_callback: Optional[Callable] = None,
    ) -> bool:
        """
        Login and establish a secure WebSocket connection in one step.

        Args:
            email: User's email address
            OGBToken: User's OGB token
            room_id: Room ID to connect to
            room_name: Room name for display
            event_id: Event ID for response tracking
            auth_callback: Optional callback for auth responses

        Returns:
            True if login and connection were successful
        """
        try:
            async with self._connection_lock:
                if not email or not OGBToken:
                    await self._send_auth_response(
                        event_id, "error", "Email and OGB-token required"
                    )
                    return False

                # Step 1: Login
                if not await self._perform_login(
                    email, OGBToken, room_id, room_name, event_id
                ):
                    if auth_callback:
                        await auth_callback(event_id, "error", "Login failed")
                    return False

                # Step 2: Log user plan (allow all plans including free)
                # Free plan users have limited features via OGBFeatureManager
                plan_name = self.subscription_data.get("plan_name", "unknown")
                _LOGGER.info(
                    f"🔐 User plan for {self.ws_room}: {plan_name} "
                    f"(is_premium: {self.is_premium})"
                )
                
                # Allow connection for all plans (feature restrictions handled by OGBFeatureManager)
                # Free plan: basic_monitoring, ai_controllers, mobile_app
                # Basic+: advanced_analytics, notifications, data_export
                # Professional+: compliance, research_data, api_access, webhooks
                # Enterprise: multi_tenant, priority_support, custom_integrations

                # Step 3: Connect WebSocket
                if not await self._connect_websocket():
                    if auth_callback:
                        await auth_callback(
                            event_id, "error", "WebSocket connection failed"
                        )
                    return False

                # Step 4: Start keep-alive
                await self._start_keepalive()

                # Success
                if auth_callback:
                    await auth_callback(
                        event_id, "success", "Login and connection successful"
                    )

                return True

        except Exception as e:
            _LOGGER.error(f"Login and connect error for {self.ws_room}: {e}")
            if auth_callback:
                await auth_callback(event_id, "error", f"Connection error: {str(e)}")
            return False

    async def _perform_login(
        self, email: str, OGBToken: str, room_id: str, room_name: str, event_id: str
    ) -> bool:
        """
        Perform the login API call.

        Args:
            email: User's email
            OGBToken: User's OGB token
            room_id: Room ID
            room_name: Room name
            event_id: Event ID for responses

        Returns:
            True if login was successful
        """
        try:
            login_data = {
                "email": email,
                "OGBToken": OGBToken,
                "room_id": room_id,
                "room_name": room_name,
                "event_id": event_id,
                "client_id": self.client_id,
            }

            timeout_config = aiohttp.ClientTimeout(total=15)

            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(
                    self.login_url, json=login_data, headers=self.headers
                ) as response:

                    response_text = await response.text()
                    _LOGGER.debug(
                        f"Login response for {self.ws_room}: {response.status}"
                    )

                    if response.status != 200:
                        _LOGGER.error(
                            f"Login HTTP error for {self.ws_room}: {response.status}"
                        )
                        await self._send_auth_response(
                            event_id, "error", f"Login failed (HTTP {response.status})"
                        )
                        return False

                    try:
                        result = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        _LOGGER.error(f"Invalid JSON response for {self.ws_room}: {e}")
                        await self._send_auth_response(
                            event_id, "error", "Server returned invalid response"
                        )
                        return False

                    if result.get("status") != "success":
                        _LOGGER.error(
                            f"Login failed for {self.ws_room}: {result.get('message', 'Unknown error')}"
                        )
                        await self._send_auth_response(
                            event_id, "error", result.get("message", "Login failed")
                        )
                        return False

                    # Store login data
                    return await self._process_login_response(result, event_id)

        except Exception as e:
            _LOGGER.error(f"Login error for {self.ws_room}: {e}")
            await self._send_auth_response(
                event_id, "error", "Unexpected server error during login"
            )
            return False

    async def _process_login_response(self, result: dict, event_id: str) -> bool:
        """
        Process a successful login response.

        Args:
            result: The login response data
            event_id: Event ID for responses

        Returns:
            True if processing was successful
        """
        # Store login data
        self._user_id = result.get("user_id")
        self._session_id = result.get("session_id")
        self._access_token = result.get("access_token")
        self.token_expires_at = result.get("token_expires_at")
        self.tenant_id = result.get("tenant_id")  # Store tenant ID for feature flags

        self.is_premium = result.get("is_premium", False)
        self.subscription_data = result.get("subscription_data", {})

        self.ogb_max_sessions = result.get("obg_max_sessions")
        self.ogb_sessions = result.get("ogb_sessions")
        self.is_logged_in = True

        # Decode session key
        session_key_b64 = result.get("session_key")
        if not session_key_b64:
            _LOGGER.error(f"No session key received for {self.ws_room}")
            await self._send_auth_response(
                event_id, "error", "No session key received from server"
            )
            return False

        try:
            self._session_key = self._safe_b64_decode(session_key_b64)
            if len(self._session_key) != 32:
                _LOGGER.error(
                    f"Invalid session key length for {self.ws_room}: {len(self._session_key)}"
                )
                await self._send_auth_response(
                    event_id, "error", "Invalid session key length"
                )
                return False

            self._aes_gcm = AESGCM(self._session_key)
            _LOGGER.debug(f"AES-GCM cipher initialized for {self.ws_room}")

        except Exception as e:
            _LOGGER.error(f"Session key decode error for {self.ws_room}: {e}")
            await self._send_auth_response(
                event_id, "error", "Session key decoding failed"
            )
            return False

        if not all([self._user_id, self._session_id, self._session_key]):
            _LOGGER.error(f"Missing required login data for {self.ws_room}")
            await self._send_auth_response(
                event_id, "error", "Missing required login data"
            )
            return False

        await self._send_auth_response(
            event_id,
            "success",
            "LoginSuccess",
            {
                "currentPlan": self.subscription_data.get("plan_name"),
                "is_premium": self.is_premium,
                "subscription_data": self.subscription_data,
                "ogb_sessions": self.ogb_sessions,
                "ogb_max_sessions": self.ogb_max_sessions,
            },
        )

        await self.ogbevents.emit(
            "LogForClient",
            f"Successfully logged in. Welcome to OGB Premium!",
            haEvent=True,
        )

        _LOGGER.warning(f"Login successful for {self.ws_room} - User: {self._user_id}")
        return True

    async def _perform_dev_login(
        self,
        email: str,
        ogbAccessToken: str,
        ogbBetaToken: str,
        room_id: str,
        room_name: str,
        event_id: str,
        auth_callback: Optional[Callable] = None,
    ) -> bool:
        """
        Perform developer login for beta testing.

        Args:
            email: User's email
            ogbAccessToken: Access token
            ogbBetaToken: Beta test token
            room_id: Room ID
            room_name: Room name
            event_id: Event ID
            auth_callback: Optional callback

        Returns:
            True if dev login was successful
        """
        try:
            login_data = {
                "email": email,
                "ogbAccessToken": ogbAccessToken,
                "ogbBetaToken": ogbBetaToken,
            }

            timeout_config = aiohttp.ClientTimeout(total=15)

            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(
                    self.dev_login_url, json=login_data, headers=self.headers
                ) as response:

                    response_text = await response.text()
                    _LOGGER.debug(
                        f"Dev login response for {self.ws_room}: {response.status}"
                    )

                    # Handle HTTP errors
                    if response.status == 400:
                        _LOGGER.error(
                            f"Dev login failed for {self.ws_room}: Missing required fields"
                        )
                        await self._send_auth_response(
                            event_id, "error", "Missing required fields"
                        )
                        return False

                    if response.status == 403:
                        try:
                            result = json.loads(response_text)
                            message = result.get("message", "Access denied")
                            await self._send_auth_response(
                                event_id,
                                "error",
                                message,
                                {
                                    "access": False,
                                    "releaseDate": result.get("releaseDate"),
                                    "version": result.get("version"),
                                },
                            )
                        except json.JSONDecodeError:
                            await self._send_auth_response(
                                event_id, "error", "Access denied"
                            )
                        return False

                    if response.status != 200:
                        _LOGGER.error(
                            f"Dev login HTTP error for {self.ws_room}: {response.status}"
                        )
                        await self._send_auth_response(
                            event_id, "error", f"Login failed (HTTP {response.status})"
                        )
                        return False

                    # Parse successful response
                    try:
                        result = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        _LOGGER.error(f"Invalid JSON response for {self.ws_room}: {e}")
                        await self._send_auth_response(
                            event_id, "error", "Server returned invalid response"
                        )
                        return False

                    if not result.get("access"):
                        _LOGGER.error(
                            f"Access denied for {self.ws_room}: {result.get('message')}"
                        )
                        await self._send_auth_response(
                            event_id, "error", result.get("message", "Access denied")
                        )
                        return False

                    # Success
                    if auth_callback:
                        await auth_callback(
                            event_id,
                            "success",
                            "DevLoginSuccess",
                            {
                                "access": result.get("access"),
                                "message": result.get("message"),
                                "version": result.get("version"),
                            },
                        )

                    await self.ogbevents.emit(
                        "LogForClient",
                        f"Dev Tester Welcome - you got Validated.",
                        haEvent=True,
                    )
                    return True

        except aiohttp.ClientError as e:
            _LOGGER.error(f"Network error during dev login for {self.ws_room}: {e}")
            await self._send_auth_response(
                event_id, "error", "Network error during login"
            )
            return False
        except Exception as e:
            _LOGGER.error(f"Dev login error for {self.ws_room}: {e}")
            await self._send_auth_response(
                event_id, "error", "Unexpected server error during login"
            )
            return False

    # =================================================================
    # Session Establishment from Auth Data
    # =================================================================

    async def establish_session_from_auth_data(
        self, auth_data: dict, event_id: str = None
    ) -> bool:
        """
        Establish a session using existing auth data (for other rooms).

        Args:
            auth_data: Dictionary with user_id, access_token, is_premium, etc.
            event_id: Event ID for tracking

        Returns:
            True if session was established
        """
        try:
            async with self._connection_lock:
                _LOGGER.warning(
                    f"Establishing session from auth data for {self.ws_room}: {auth_data}"
                )

                # Extract data
                self._user_id = auth_data.get("user_id")
                self._access_token = auth_data.get("access_token")
                self.is_premium = auth_data.get("is_premium", False)
                self.is_logged_in = auth_data.get("is_logged_in", False)
                self.subscription_data = auth_data.get("subscription_data", {})

                # Get room-specific session key
                session_data = await self._request_session_key(event_id, self.room_id)
                if not session_data:
                    _LOGGER.error(f"Failed to get session key for {self.ws_room}")
                    return False

                # Extract session info
                self._session_id = session_data.get("session_id")
                session_key_b64 = session_data.get("session_key")

                # Decode session key
                try:
                    self._session_key = self._safe_b64_decode(session_key_b64)
                    if len(self._session_key) != 32:
                        _LOGGER.error(f"Invalid session key length for {self.ws_room}")
                        return False
                    self._aes_gcm = AESGCM(self._session_key)
                except Exception as e:
                    _LOGGER.error(f"Session key decode error for {self.ws_room}: {e}")
                    return False

                # Connect WebSocket
                if not await self._connect_websocket():
                    _LOGGER.error(f"WebSocket connection failed for {self.ws_room}")
                    return False

                # Start keep-alive
                await self._start_keepalive()

                _LOGGER.warning(
                    f"Session established from auth data for {self.ws_room}"
                )
                return True

        except Exception as e:
            _LOGGER.error(f"Session establishment error for {self.ws_room}: {e}")
            return False

    # =================================================================
    # Disconnect & Cleanup
    # =================================================================

    async def disconnect(self):
        """Disconnect the WebSocket (keeps auth data)."""
        _LOGGER.warning(f"Disconnecting WebSocket for {self.ws_room}")

        # Stop reconnection
        self._should_reconnect = False
        self._reconnection_in_progress = False

        # Stop keep-alive
        await self._stop_keepalive()

        # Cancel reconnect task
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass

        # Disconnect socket
        if hasattr(self, "sio") and self.ws_connected:
            try:
                await self.sio.disconnect()
            except Exception as e:
                _LOGGER.warning(f"Error during disconnect for {self.ws_room}: {e}")

        # Reset connection states only
        self.ws_connected = False

        await self.ogbevents.emit("ogb_client_disconnect", self.ogb_sessions)
        await self.room_removed()

        await self._send_auth_response(
            self.create_event_id(),
            "success",
            "Disconnect Success",
            {
                "ogb_sessions": self.ogb_sessions,
                "ogb_max_sessions": self.ogb_max_sessions,
            },
        )

        _LOGGER.warning(f"WebSocket disconnected for {self.ws_room}")

    async def cleanup_prem(self, event_id: str = None):
        """
        Full cleanup of premium data including all auth state.

        Args:
            event_id: Event ID for response tracking
        """
        try:
            _LOGGER.warning(f"Cleaning up premium data for {self.ws_room}")

            # Cancel rotation task if running
            if (
                hasattr(self, "_rotation_task")
                and self._rotation_task
                and not self._rotation_task.done()
            ):
                self._rotation_task.cancel()
                try:
                    await self._rotation_task
                except asyncio.CancelledError:
                    pass

            # Cancel reconnect task
            if self.reconnect_task and not self.reconnect_task.done():
                self.reconnect_task.cancel()
                try:
                    await self.reconnect_task
                except asyncio.CancelledError:
                    pass

            # Stop keep-alive
            await self._stop_keepalive()

            # Disconnect existing socket
            if hasattr(self, "sio") and self.sio.connected:
                try:
                    await self.sio.disconnect()
                except Exception:
                    pass

            # Reset all state variables
            self._session_key = None
            self._session_id = None
            self._user_id = None
            self._access_token = None
            self.token_expires_at = None
            self.authenticated = False
            self.ws_connected = False
            self.ws_reconnect_attempts = 0
            self.is_logged_in = False
            self.is_premium = False
            self.ogb_sessions = 0
            self.ogb_max_sessions = None
            self.active_grow_plan = None

            # Reset connection and rotation state
            self._reconnection_in_progress = False
            self._rotation_in_progress = False
            self._rotation_task = None
            self._should_reconnect = True
            self._reconnect_delay = 5

            # Clear data
            self.user_data = {}
            self.subscription_data = {}
            self._aes_gcm = None
            self._last_pong_time = time.time()
            self._ping_task = None

            # Create fresh socket.io client
            self.sio = socketio.AsyncClient(
                reconnection=True, logger=False, engineio_logger=False, ssl_verify=True
            )

            # Re-setup event handlers
            self._setup_event_handlers()

            _LOGGER.warning(f"Premium data cleanup completed for {self.ws_room}")

            if event_id:
                await self._send_auth_response(
                    event_id,
                    "success",
                    "Logout successful",
                    {"logged_out_at": time.time()},
                )

            await self.ogbevents.emit(
                "LogForClient",
                f"Successfully logged out from {self.ws_room}",
                haEvent=True,
            )
            return True

        except Exception as e:
            _LOGGER.error(f"Cleanup error for {self.ws_room}: {e}")
            return False

    # =================================================================
    # User/Connection Info
    # =================================================================

    def get_connection_info(self) -> dict:
        """Get current connection information."""
        return {
            "connected": self.sio.connected if hasattr(self, "sio") else False,
            "authenticated": self.authenticated,
            "is_logged_in": self.is_logged_in,
            "is_premium": self.is_premium,
            "user_id": self._user_id,
            "room_id": self.room_id,
            "room_name": self.ws_room,
            "session_id": self._session_id,
            "reconnect_attempts": getattr(self, "ws_reconnect_attempts", 0),
            "reconnection_in_progress": self._reconnection_in_progress,
            "rotation_in_progress": getattr(self, "_rotation_in_progress", False),
        }

    def get_user_info(self) -> dict:
        """Get user information."""
        return {
            "user_id": self._user_id,
            "is_logged_in": self.is_logged_in,
            "is_premium": self.is_premium,
            "subscription_data": self.subscription_data,
            "room_id": self.room_id,
            "room_name": self.ws_room,
        }

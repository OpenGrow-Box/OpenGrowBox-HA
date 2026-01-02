import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import aiohttp
import socketio
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ....const import VERSION


class OGBWebSocketConManager:
    # Standardized timeout constants
    CONNECTION_TIMEOUT = 15.0
    AUTH_TIMEOUT = 30.0
    PONG_TIMEOUT = 10.0
    HTTP_TIMEOUT = 12.0
    RECONNECT_BASE_DELAY = 2.0

    def __init__(
        self,
        base_url: str,
        eventManager={},
        ws_room="",
        room_id="",
        timeout: float = CONNECTION_TIMEOUT,
    ):
        # Connect to the main API URL - Socket.IO will use default /socket.io path
        self.base_url = self._validate_url(base_url)
        self.api_url = base_url.replace("ws://", "http://").replace(
            "wss://", "https://"
        )
        # V1 API endpoints
        self.login_url = f"{self.api_url}/api/v1/auth/login"
        self.dev_login_url = f"{self.api_url}/api/v1/auth/devlogin"
        self.profile_url = f"{self.api_url}/api/v1/auth/profile"
        self.timeout = timeout
        self.ogbevents = eventManager
        self.ws_room = ws_room
        self.room_id = room_id
        self.client_id = f"ogb-client-{self.ws_room}-{secrets.token_hex(8)}"

        # User data
        self.user_data = {}
        self.subscription_data = {}
        self.tenant_id = None  # Tenant ID for feature flag control

        self.active_grow_plan = None

        self.ogb_sessions = 0
        self.ogb_max_sessions = 0

        # Security variables
        self._session_key = None
        self._session_id = None
        self._user_id = None
        self._access_token = None
        self.token_expires_at = None

        # Connection state
        self.authenticated = False
        self.ws_connected = False
        self.is_logged_in = False
        self.is_premium = False

        # SIMPLIFIED: Single reconnection system
        self._reconnection_in_progress = False
        self._should_reconnect = True
        self._reconnect_delay = 5
        self.reconnect_task = None
        self._reconnect_task = None
        self._connection_lock = asyncio.Lock()
        self._reconnection_lock = (
            asyncio.Lock()
        )  # Dedicated lock for reconnection operations
        self._rotation_lock = (
            asyncio.Lock()
        )  # Dedicated lock for session rotation operations
        self.max_reconnect_attempts = 15
        self.reconnect_attempts = 0
        self.ws_reconnect_attempts = 0

        # AES-GCM for encryption
        self._aes_gcm = None

        # UNIFIED: Single keep-alive system (replaces separate ping/pong and health monitoring)
        # CRITICAL: Keep-alive interval MUST be shorter than server's pingTimeout
        # Server typically: pingInterval=25s, pingTimeout=20s (total 45s)
        # We use 20s interval to stay well ahead of server timeout
        self._last_pong_time = time.time()
        self._keepalive_task = None
        self._keepalive_interval = 20  # Reduced from 30s to prevent transport close
        self._pong_timeout = 10

        # Pong detection with proper event signaling
        self._pong_received = False
        self._pong_event = asyncio.Event()

        # V1 Authentication event signaling
        self._auth_confirmed = asyncio.Event()
        self._auth_success = None

        # Connection health tracking
        self._connection_closing = False
        self._send_queue = asyncio.Queue()
        self._health_monitor_task = None
        self._connection_monitoring_paused = False
        self._connection_start_time = None

        # Stored auth callback for V1 authentication
        self._pending_auth_callback = None
        self._pending_event_id = None

        # Session rotation state
        self._rotation_in_progress = False
        self._rotation_task = None
        self._rotation_start_time = None

        # V1 namespace for Socket.IO
        self._v1_namespace = '/v1/websocket'
        
        # V1 authentication state tracking
        self._plan = None
        self._room_name = None
        self._auth_callback_called = False
        
        # Track if event handlers are already registered (prevent memory leak)
        self._handlers_registered = False

        # Message handlers
        self.message_handlers: Dict[str, Callable] = {}

        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "OGB-Python-Client/1.0",
            "Accept": "application/json",
            "origin": "https://opengrowbox.net",
            "ogb-client": "ogb-ws-ha-connector 1.0",
            "ogb-client-id": self.client_id,
            "ogb-client-version": VERSION,
        }

        # Setup socket.io ASYNC client
        # NOTE: engineio ping/pong is automatic but we set explicit timeouts
        # to match server configuration and prevent transport close
        self.sio = socketio.AsyncClient(
            reconnection=False,  # Handle reconnection ourselves
            logger=False,
            engineio_logger=False,
            ssl_verify=True,
            # Match server's pingInterval and pingTimeout to prevent disconnects
            # Default server: pingInterval=25000ms, pingTimeout=20000ms
            # We use slightly shorter intervals to stay ahead of server timeout
        )

        self._setup_event_listeners()
        self._setup_event_handlers()

    # =================================================================
    # Connection
    # =================================================================

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self.ogbevents.on("ogb_client_disconnect", self.room_disconnect)

    async def login_and_connect(
        self,
        email: str,
        OGBToken: str,
        room_id: str,
        room_name: str,
        event_id: str = None,
        auth_callback: Optional[Callable] = None,
    ) -> bool:
        """Login und sichere Verbindung in einem Schritt"""
        try:
            async with self._connection_lock:
                if not email or not OGBToken:
                    await self._send_auth_response(
                        event_id, "error", "Email and OGB-token required"
                    )
                    return False

                # Store auth callback for V1 authentication success
                self._pending_auth_callback = auth_callback
                self._pending_event_id = event_id

                # Step 1: Login
                if not await self._perform_login(
                    email, OGBToken, room_id, room_name, event_id
                ):
                    # Call auth callback with login failure
                    if auth_callback:
                        await auth_callback(event_id, "error", "Login failed")
                    # Clear stored callback on failure
                    self._pending_auth_callback = None
                    self._pending_event_id = None
                    return False

                # Step 2: Log user plan (allow all plans including free)
                # Free plan users have limited features via OGBFeatureManager
                plan_name = self.subscription_data.get("plan_name", "unknown") if hasattr(self, 'subscription_data') else "unknown"
                logging.info(
                    f"🔐 User plan for {self.ws_room}: {plan_name} "
                    f"(is_premium: {self.is_premium})"
                )

                # Allow connection for all plans (feature restrictions handled by OGBFeatureManager)
                # Free plan: basic_monitoring, ai_controllers, mobile_app
                # Basic+: advanced_analytics, notifications, data_export
                # Professional+: compliance, research_data, api_access, webhooks
                # Enterprise: multi_tenant, priority_support, custom_integrations

                # Step 3: Connect WebSocket (but don't call auth callback yet)
                if not await self._connect_websocket():
                    # Call auth callback with connection failure
                    if auth_callback:
                        await auth_callback(
                            event_id, "error", "WebSocket connection failed"
                        )
                    # Clear stored callback on failure
                    self._pending_auth_callback = None
                    self._pending_event_id = None
                    return False

                # Step 4: Start keep-alive
                await self._start_keepalive()

                # DON'T call auth callback here - wait for V1 authentication success
                # The V1 auth success handlers will call the stored callback

                logging.info(f"🔄 {self.ws_room} WebSocket connected, waiting for V1 authentication...")
                return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Login and connect error: {e}")
            if auth_callback:
                await auth_callback(
                    event_id, "error", f" {self.ws_room} Connection error: {str(e)}"
                )
            # Clear stored callback on error
            self._pending_auth_callback = None
            self._pending_event_id = None
            return False

    async def _perform_login(
        self, email: str, OGBToken: str, room_id: str, room_name: str, event_id: str
    ) -> bool:
        """Perform login and send user-facing error messages if something goes wrong."""
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
                    logging.debug(
                        f"📥 {self.ws_room} Login response: {response.status}"
                    )

                    if response.status != 200:
                        logging.error(
                            f"❌ {self.ws_room} Login HTTP error: {response.status}"
                        )
                        await self._send_auth_response(
                            event_id, "error", f"Login failed (HTTP {response.status})"
                        )
                        return False

                    try:
                        result = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        logging.error(f"❌ {self.ws_room} Invalid JSON response: {e}")
                        await self._send_auth_response(
                            event_id, "error", "Server returned invalid response"
                        )
                        return False

                    if result.get("status") != "success":
                        logging.error(
                            f"❌ {self.ws_room} Login failed: {result.get('message', 'Unknown error')}"
                        )
                        await self._send_auth_response(
                            event_id, "error", result.get("message", "Login failed")
                        )
                        return False

                    # Store login data
                    self._user_id = result.get("user_id")
                    self._session_id = result.get("session_id")
                    self._access_token = result.get("access_token")
                    self.token_expires_at = result.get("token_expires_at")
                    self.tenant_id = result.get(
                        "tenant_id"
                    )  # Store tenant ID for feature flags

                    self.is_premium = result.get("is_premium", False)
                    self.subscription_data = result.get("subscription_data", {})

                    # Session counts - server may use different field names
                    self.ogb_max_sessions = result.get("ogb_max_sessions") or result.get("maxSessions") or result.get("max_sessions") or 2
                    self.ogb_sessions = result.get("ogb_sessions") or result.get("sessionCount") or result.get("session_count") or 1
                    
                    logging.info(f"📊 {self.ws_room} Login result - sessions: {self.ogb_sessions}/{self.ogb_max_sessions}")
                    self.is_logged_in = True
                    
                    # Store email for auto-relogin functionality
                    self._stored_email = email

                    # Decode session key
                    session_key_b64 = result.get("session_key")
                    if not session_key_b64:
                        logging.error(f"❌ {self.ws_room} No session key received")
                        await self._send_auth_response(
                            event_id, "error", "No session key received from server"
                        )
                        return False

                    try:
                        self._session_key = self._safe_b64_decode(session_key_b64)
                        if len(self._session_key) != 32:
                            logging.error(
                                f"❌ {self.ws_room} Invalid session key length: {len(self._session_key)}"
                            )
                            await self._send_auth_response(
                                event_id, "error", "Invalid session key length"
                            )
                            return False

                        # Log first 8 bytes of key for debugging
                        key_hex = self._session_key[:8].hex()
                        logging.warning(
                            f"🔐 {self.ws_room} Session key received - first 8 bytes: {key_hex}, length: {len(self._session_key)}"
                        )

                        self._aes_gcm = AESGCM(self._session_key)
                        logging.debug(
                            f"🔐 {self.ws_room} AES-GCM cipher initialized successfully"
                        )

                    except Exception as e:
                        logging.error(
                            f"❌ {self.ws_room} Session key decode error: {e}"
                        )
                        await self._send_auth_response(
                            event_id, "error", "Session key decoding failed"
                        )
                        return False

                    if not all([self._user_id, self._session_id, self._session_key]):
                        logging.error(f"❌ {self.ws_room} Missing required login data")
                        await self._send_auth_response(
                            event_id, "error", "Missing required login data"
                        )
                        return False

                    await self._send_auth_response(
                        event_id,
                        "success",
                        "LoginSuccess",
                        {
                            "access_token": self._access_token,
                            "refresh_token": None,  # Not used in current implementation
                            "user": {
                                "id": self._user_id,
                                "email": email,
                            },
                            "expires_at": self.token_expires_at,
                            "currentPlan": self.subscription_data.get("plan_name"),
                            "is_premium": self.is_premium,
                            "subscription_data": self.subscription_data,
                            "ogb_sessions": self.ogb_sessions,
                            "ogb_max_sessions": self.ogb_max_sessions,
                        },
                    )

                    await self._safe_emit(
                        "LogForClient",
                        f"Successfully logged in. Welcome to OGB Premium!",
                        haEvent=True,
                    )

                    logging.warning(
                        f"✅ {self.ws_room} Login successful - User: {self._user_id}"
                    )
                    return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Login error: {e}")
            await self._send_auth_response(
                event_id, "error", "Unexpected server error during login"
            )
            return False

    async def _connect_websocket(self) -> bool:
        """Connect WebSocket with session authentication."""
        # NOTE: This method is called from login_and_connect which already holds the lock
        # So we don't acquire the lock here to avoid deadlock
        return await self._connect_websocket_internal()
    
    async def _connect_websocket_internal(self) -> bool:
        """Internal WebSocket connection - called with lock already held or standalone."""
        try:
            # Validate authentication data
            if not self.is_logged_in and not (self._user_id and self._access_token):
                logging.error(
                    f"❌ {self.ws_room} Must have valid auth data to connect"
                )
                return False

            # Check if already connected
            if self.sio.connected:
                logging.warning(f"ℹ️ {self.ws_room} WebSocket already connected")
                return True

            # Get actual plan from subscription data
            actual_plan = self.subscription_data.get("plan_name", "free") if self.subscription_data else "free"
            
            # FIRST: Validate and auto-request session data before building headers
            # This MUST happen before header construction to avoid "None" values
            if not self._session_id or not self._session_key:
                logging.warning(f"⚠️ {self.ws_room} No session data, requesting new session key...")
                session_data = await self._request_session_key()
                if not session_data:
                    logging.error(f"❌ {self.ws_room} Failed to get session key for V1 connection")
                    return False
                logging.info(f"✅ {self.ws_room} New session key obtained: {self._session_id[:8] if self._session_id else 'None'}...")
            
            if not self._user_id:
                logging.error(f"❌ {self.ws_room} No user ID available for V1 auth")
                return False

            # NOW build authentication headers (after session data is ensured)
            auth_headers = {
                "origin": "https://opengrowbox.net",
                "user-agent": "OGB-HA-Integration/1.0",
                "ogb-client": "ogb-ws-ha-connector 1.0",  # Match API expectation
                "ogb-client-version": VERSION,
                "ogb-room-id": str(self.room_id),        # API expects ogb-room-id
                "ogb-room-name": str(self.ws_room),      # API expects ogb-room-name
                "ogb-session-id": str(self._session_id),
                "ogb-token": str(self._access_token),  # API expects ogb-token, not ogb-access-token
                "ogb-user-id": str(self._user_id),
                "ogb-plan": actual_plan,
            }

            # Check for missing/invalid headers
            missing_headers = [
                k
                for k, v in auth_headers.items()
                if not v or str(v).strip() == "" or v == "None"
            ]
            if missing_headers:
                logging.error(
                    f"❌ {self.ws_room} Missing error headers: {missing_headers}"
                )
                return False

            # Construct connection URL - Socket.IO handles WS upgrade internally
            # Connect to BASE URL with namespace parameter (NOT appended to URL!)
            base_url = self.api_url  # HTTP URL - Socket.IO upgrades to WS
            self._v1_namespace = '/v1/websocket'  # Store for later emits

            # V1 Debug: Log connection details
            logging.warning(f"🔗 {self.ws_room} Connecting to V1 WebSocket: {base_url} namespace: {self._v1_namespace} with plan: {actual_plan}")
            logging.warning(f"🔐 {self.ws_room} V1 AUTH HEADERS: {auth_headers}")
            logging.warning(f"🎯 {self.ws_room} Session ID: {auth_headers.get('ogb-session-id', 'MISSING')}")
            logging.warning(f"🏷️ {self.ws_room} Client ID: {auth_headers.get('ogb-client', 'MISSING')}")
            logging.warning(f"📊 {self.ws_room} User ID: {auth_headers.get('ogb-user-id', 'MISSING')}")

            logging.warning(f"✅ {self.ws_room} Session ready for V1 auth: {self._session_id[:8]}...")

            # Transport configuration - websocket first, fallback to polling
            transports = ["websocket", "polling"]

            # CRITICAL FIX: Reset auth state BEFORE connecting
            # The server emits v1:session:confirmed immediately during the connection handshake.
            # If we clear _auth_confirmed AFTER connecting, we clear the flag that was just set!
            logging.info(f"🔄 {self.ws_room} Resetting auth state before connection...")
            self._auth_confirmed.clear()
            self._auth_success = None
            self._auth_callback_called = False

            # Connect with timeout
            # Socket.IO namespace connection: connect to BASE URL with namespaces parameter
            logging.warning(f"🔗 {self.ws_room} Starting V1 WebSocket connect to namespace {self._v1_namespace}...")
            await asyncio.wait_for(
                self.sio.connect(
                    base_url,  # HTTP URL - Socket.IO handles WS upgrade
                    transports=transports,
                    headers=auth_headers,
                    wait_timeout=int(self.timeout),
                    namespaces=[self._v1_namespace],  # Connect to V1 namespace
                ),
                timeout=self.timeout * 2,
            )
            logging.warning(f"🔗 {self.ws_room} WebSocket connect call completed, connected={self.sio.connected}")

            # Wait for connection stabilization
            await asyncio.sleep(0.5)

            if not self.sio.connected:
                logging.error(f"❌ {self.ws_room} WebSocket connection failed")
                return False

            # Connection established - check if already authenticated
            self.ws_connected = True
            
            # FIX: Check if authentication already succeeded during connection
            # (the v1:session:confirmed event is emitted immediately by the server)
            if self._auth_success:
                logging.error(f"✅ {self.ws_room} V1 authentication already confirmed (received during connect)")
            else:
                # Wait for V1 authentication confirmation
                logging.info(f"🔄 {self.ws_room} Waiting for V1 authentication confirmation...")
                try:
                    # Wait for server to confirm authentication (V1 API requirement)
                    # Extended timeout to 30s to give server more time
                    await asyncio.wait_for(self._auth_confirmed.wait(), timeout=30.0)

                    if not self._auth_success:
                        logging.error(f"❌ {self.ws_room} V1 authentication failed - server rejected connection")
                        return False

                    logging.error(f"✅ {self.ws_room} V1 authentication confirmed by server")

                except asyncio.TimeoutError:
                    logging.error(f"❌ {self.ws_room} V1 authentication timeout - no server confirmation within 30s")
                    logging.error(f"🔍 {self.ws_room} Debug: _auth_success={self._auth_success}, _auth_confirmed.is_set()={self._auth_confirmed.is_set()}")
                    return False
            
            # Reset reconnection state after successful authentication
            self.ws_reconnect_attempts = 0
            self._reconnection_in_progress = False
            self._reconnect_delay = 5
            self._should_reconnect = True
            # NOTE: Session count now managed by API, updated via session_count_updated event

            # Send success response
            await self._send_auth_response(
                self.create_event_id(),
                "success",
                "Connect Success",
                {
                    "currentPlan": self.subscription_data.get("plan_name"),
                    "is_premium": self.is_premium,
                    "subscription_data": self.subscription_data,
                    "ogb_sessions": self.ogb_sessions,
                    "ogb_max_sessions": self.ogb_max_sessions,
                },
            )

            # Start keep-alive
            try:
                await self._start_keepalive()
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Failed to start keep-alive: {e}")
                # Don't fail the connection for keep-alive issues

            return True

        except asyncio.TimeoutError:
            logging.error(f"❌ {self.ws_room} WebSocket connection timeout")
            return False
        except Exception as e:
            logging.error(f"❌ {self.ws_room} WebSocket connection error: {e}")
            return False

    async def _request_session_key(self, event_id: str = None, room_id: str = None):
        """Request new session key from server"""
        try:
            if not self._user_id or not self._access_token:
                logging.error(
                    f"❌ {self.ws_room} Cannot request session - missing auth data"
                )
                return None

            url = f"{self.api_url}/api/v1/auth/create-session-for-device"

            request_data = {
                "user_id": self._user_id,
                "access_token": self._access_token,
                "client_id": self.client_id,
                "room_id": self.room_id,
                "room_name": self.ws_room,
            }

            logging.warning(f"🔑 {self.ws_room} Requesting new session key")

            timeout_config = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(
                    url, json=request_data, headers=self.headers
                ) as response:

                    if response.status != 200:
                        logging.error(
                            f"❌ {self.ws_room} Session request failed: HTTP {response.status}"
                        )
                        return None

                    try:
                        result = json.loads(await response.text())
                    except json.JSONDecodeError as e:
                        logging.error(
                            f"❌ {self.ws_room} Invalid JSON in session response: {e}"
                        )
                        return None

                    if result.get("status") != "success":
                        logging.error(
                            f"❌ {self.ws_room} Session request failed: {result.get('message', 'Unknown error')}"
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
                            logging.debug(
                                f"✅ {self.ws_room} New session key established: {self._session_id}"
                            )
                        else:
                            logging.error(
                                f"❌ {self.ws_room} Invalid session key length received"
                            )
                            return None
                    except Exception as e:
                        logging.error(
                            f"❌ {self.ws_room} Session key processing error: {e}"
                        )
                        return None

                    return session_data

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Session key request error: {e}")
            return None

    def _check_session_limits(self) -> bool:
        """Check if new session is allowed based on plan limits from API"""
        if not self.subscription_data:
            return True  # Allow during auth process

        # Extract limits from API response (subscription_data.limits)
        limits = self.subscription_data.get("limits", {})
        max_sessions = limits.get("max_sessions") or limits.get("max_concurrent_connections")

        # If API doesn't provide session limits, use plan-based defaults
        if max_sessions is None:
            plan_name = self.subscription_data.get("plan_name", "free")
            plan_defaults = {
                "free": 1,        # FREE: 1 connection
                "basic": 25,      # BASIC: 25 connections
                "professional": 100, # PROFESSIONAL: 100 connections
                "enterprise": 500, # ENTERPRISE: 500 connections
            }
            max_sessions = plan_defaults.get(plan_name, 1)

        # With independent WebSocket clients per room, each client represents one session
        # We allow multiple concurrent connections up to the plan limit
        active_sessions = 1 if self.ws_connected else 0

        return active_sessions < max_sessions

    async def establish_session_from_auth_data(
        self, auth_data: dict, event_id: str = None
    ) -> bool:
        """Establish session from authenticated data (for other rooms)"""
        try:
            async with self._connection_lock:
                logging.warning(
                    f"🔐 {self.ws_room} Establishing session from auth data  {auth_data}"
                )

                # Extract data
                self._user_id = auth_data.get("user_id")
                self._access_token = auth_data.get("access_token")
                self.is_premium = auth_data.get("is_premium", False)
                self.is_logged_in = auth_data.get("is_logged_in", False)
                self.subscription_data = auth_data.get("subscription_data", {})

                # Check session limits before proceeding
                if not self._check_session_limits():
                    plan_name = self.subscription_data.get("plan_name", "free")
                    logging.error(f"❌ {self.ws_room} Session limit exceeded for {plan_name} plan")
                    return False

                # Get room-specific session key
                session_data = await self._request_session_key(event_id, self.room_id)
                if not session_data:
                    logging.error(f"❌ {self.ws_room} Failed to get session key")
                    return False

                # Extract session info
                self._session_id = session_data.get("session_id")
                session_key_b64 = session_data.get("session_key")

                # Decode session key
                try:
                    self._session_key = self._safe_b64_decode(session_key_b64)
                    if len(self._session_key) != 32:
                        logging.error(f"❌ {self.ws_room} Invalid session key length")
                        return False
                    self._aes_gcm = AESGCM(self._session_key)
                except Exception as e:
                    logging.error(f"❌ {self.ws_room} Session key decode error: {e}")
                    return False

                # Connect WebSocket
                logging.warning(f"🔗 {self.ws_room} Connecting WebSocket with session_id={self._session_id}")
                if not await self._connect_websocket():
                    logging.error(f"❌ {self.ws_room} WebSocket connection failed")
                    return False

                # Start keep-alive
                await self._start_keepalive()

                logging.warning(f"✅ {self.ws_room} Session established from auth data - authenticated={self.authenticated}, ws_connected={self.ws_connected}")
                return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Session establishment error: {e}")
            return False

    # =================================================================
    # Keep-Alive System
    # =================================================================

    async def _start_keepalive(self):
        """Start the unified keep-alive system."""
        await self._stop_keepalive()
        if self._keepalive_task and not self._keepalive_task.done():
            return

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logging.debug(f"🔄 Keep-alive started for {self.ws_room}")

    async def _stop_keepalive(self):
        """Stop the keep-alive system."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        logging.debug(f"🛑 Keep-alive stopped for {self.ws_room}")

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
                    # Send V1 monitoring ping
                    ping_data = {
                        "timestamp": time.time(),
                        "room": self.ws_room,
                        "client_time": time.time(),
                        "event_id": f"ping-{int(time.time())}",
                    }

                    await self.sio.emit("v1:monitoring:ping", ping_data, namespace=self._v1_namespace)
                    logging.debug(f"🏓 Sent V1 monitoring ping for {self.ws_room}")

                    # Wait for pong with timeout
                    pong_received = await self._wait_for_pong(self._pong_timeout)

                    if not pong_received:
                        consecutive_failures += 1
                        logging.warning(
                            f"🏓 Health check failed {consecutive_failures}/{max_consecutive_failures} "
                            f"for {self.ws_room} - no pong received"
                        )

                        if consecutive_failures >= max_consecutive_failures:
                            logging.error(
                                f"❌ Health check permanently failed for {self.ws_room} - triggering reconnection"
                            )
                            await self._attempt_reconnect()
                            break
                    else:
                        # Reset consecutive failures on successful pong
                        if consecutive_failures > 0:
                            logging.info(
                                f"✅ Health check recovered for {self.ws_room} after {consecutive_failures} failures"
                            )
                        consecutive_failures = 0
                        logging.debug(f"🏓 Health check OK for {self.ws_room}")

                except Exception as e:
                    consecutive_failures += 1
                    logging.error(
                        f"❌ Keep-alive error {consecutive_failures}/{max_consecutive_failures} "
                        f"for {self.ws_room}: {e}"
                    )

                    if consecutive_failures >= max_consecutive_failures:
                        logging.error(
                            f"❌ Keep-alive permanently failed for {self.ws_room} - triggering reconnection"
                        )
                        await self._attempt_reconnect()
                        break

        except asyncio.CancelledError:
            logging.debug(f"🛑 Keep-alive cancelled for {self.ws_room}")
        except Exception as e:
            logging.error(f"❌ Keep-alive loop error for {self.ws_room}: {e}")

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

    def _setup_event_handlers(self):
        """Setup Socket.IO event handlers for V1 namespace.
        
        IMPORTANT: This should only be called ONCE per socket instance.
        Calling it multiple times will register duplicate handlers causing:
        - Memory leaks
        - Multiple event callbacks
        - OOM crashes
        """
        # Prevent duplicate handler registration (memory leak prevention)
        if self._handlers_registered:
            logging.debug(f"⚠️ {self.ws_room} Event handlers already registered, skipping")
            return
        
        ns = self._v1_namespace  # /v1/websocket
        
        @self.sio.on('connect', namespace=ns)
        async def on_connect():
            logging.info(
                f"🔗 {self.ws_room} WebSocket connected to V1 namespace, waiting for authentication..."
            )
            self.ws_connected = True

        @self.sio.on('disconnect', namespace=ns)
        async def on_disconnect():
            logging.warning(f"❌ {self.ws_room} WebSocket disconnected from V1 namespace")
            self.ws_connected = False
            self.authenticated = False

        @self.sio.on('auth_success', namespace=ns)
        async def on_auth_success(data):
            logging.error(f"🎉 AUTH SUCCESS RECEIVED: {data}")

            # Update session data from auth response
            # Server sends: sessionCount, maxSessions (or ogb_sessions, ogb_max_sessions)
            self.ogb_max_sessions = data.get("maxSessions") or data.get("max_sessions") or data.get("ogb_max_sessions") or self.ogb_max_sessions
            self.ogb_sessions = data.get("sessionCount") or data.get("session_count") or data.get("ogb_sessions") or self.ogb_sessions
            
            logging.info(f"📊 {self.ws_room} Auth success - sessions: {self.ogb_sessions}/{self.ogb_max_sessions}")

            # Update user info from auth data
            if data.get('user_id'):
                self._user_id = data.get('user_id')
            if data.get('plan'):
                self._plan = data.get('plan')
            if data.get('room_name'):
                self._room_name = data.get('room_name')

            # DO NOT set auth flags here - wait for v1:session:confirmed
            # DO NOT call auth callback here - wait for final confirmation

        @self.sio.on("v1:session:confirmed", namespace=ns)
        async def on_v1_session_confirmed(data):
            """Handle V1 session confirmation (session key already exists from login)"""
            try:
                session_id = data.get("session_id")
                user_id = data.get("user_id")
                room_id = data.get("room_id")
                room_name = data.get("room_name")

                logging.error(f"🎉 {self.ws_room} V1 SESSION CONFIRMED RECEIVED: {data}")
                logging.error(f"🔍 {self.ws_room} Session ID check: server={session_id}, local={self._session_id}, match={session_id == self._session_id}")

                # Verify session ID matches what we expect
                if session_id and session_id == self._session_id:
                    self.authenticated = True
                    self.ws_connected = True
                    self._auth_success = True
                    self._auth_confirmed.set()
                    
                    # Update session counts if provided in confirmation
                    if data.get("sessionCount") or data.get("session_count"):
                        self.ogb_sessions = data.get("sessionCount") or data.get("session_count") or self.ogb_sessions
                    if data.get("maxSessions") or data.get("max_sessions"):
                        self.ogb_max_sessions = data.get("maxSessions") or data.get("max_sessions") or self.ogb_max_sessions
                    
                    logging.error(f"✅ {self.ws_room} V1 session confirmed - encryption ready (AES-256-GCM) - sessions: {self.ogb_sessions}/{self.ogb_max_sessions}")

                    # Call auth callback if not already called by auth_success
                    if self._pending_auth_callback and not self._auth_callback_called:
                        logging.error(f"🔄 {self.ws_room} Calling stored auth callback after V1 session confirmed")
                        try:
                            await self._pending_auth_callback(
                                self._pending_event_id,
                                "success",
                                "V1 session confirmed",
                                data
                            )
                            logging.error(f"✅ {self.ws_room} Auth callback called successfully")
                            self._auth_callback_called = True
                        except Exception as e:
                            logging.error(f"❌ {self.ws_room} Error calling auth callback: {e}")
                        finally:
                            # Clear callback after calling
                            self._pending_auth_callback = None
                            self._pending_event_id = None
                else:
                    logging.error(f"❌ {self.ws_room} V1 session ID mismatch: expected {self._session_id}, got {session_id}")
                    self.authenticated = False
                    self._auth_success = False
                    self._auth_confirmed.set()

                    # Call auth callback with failure
                    if self._pending_auth_callback:
                        try:
                            await self._pending_auth_callback(
                                self._pending_event_id,
                                "error",
                                f"V1 session mismatch: expected {self._session_id}, got {session_id}",
                                data
                            )
                        except Exception as e:
                            logging.error(f"❌ {self.ws_room} Error calling auth callback on failure: {e}")
                        finally:
                            self._pending_auth_callback = None
                            self._pending_event_id = None

                # Log session details for debugging
                if self._session_key:
                    key_hex = self._session_key[:8].hex()
                    logging.debug(f"🔐 {self.ws_room} Session key: first 8 bytes {key_hex}, length {len(self._session_key)}")

            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling V1 session confirmation: {e}")
                self.authenticated = False
                self._auth_success = False
                self._auth_confirmed.set()

        @self.sio.on("v1:connection:established", namespace=ns)
        async def v1_connection_established(data):
            """Handle V1 connection confirmation"""
            logging.info(f"🔗 {self.ws_room} V1 connection established: {data}")
            self.ws_connected = True

        @self.sio.on("v1:error", namespace=ns)
        async def v1_error(data):
            """Handle V1 errors - indicates authentication failure"""
            logging.error(f"❌ {self.ws_room} V1 ERROR RECEIVED: {data}")
            self.authenticated = False
            self._auth_success = False
            self._auth_confirmed.set()

            # Call auth callback with V1 error
            if self._pending_auth_callback:
                try:
                    await self._pending_auth_callback(
                        self._pending_event_id,
                        "error",
                        f"V1 authentication failed: {data.get('message', 'Unknown error')}",
                        data
                    )
                except Exception as e:
                    logging.error(f"❌ {self.ws_room} Error calling auth callback on V1 error: {e}")
                finally:
                    self._pending_auth_callback = None
                    self._pending_event_id = None

        @self.sio.on("grow-data_acknowledged", namespace=ns)
        async def grow_data_acknowledged(data):
            """Handle grow data acknowledgement from V1 API"""
            logging.info(f"✅ {self.ws_room} Grow data acknowledged: {data}")

        @self.sio.on("message_acknowledged", namespace=ns)
        async def message_acknowledged(data):
            """Handle generic message acknowledgement"""
            logging.debug(f"✅ {self.ws_room} Message acknowledged: {data}")

        @self.sio.on("message_error", namespace=ns)
        async def message_error(data):
            """Handle message errors from server"""
            logging.error(f"❌ {self.ws_room} Message error: {data}")

            # Empty error object indicates authentication failure
            if data == {}:
                logging.error(f"❌ {self.ws_room} Authentication rejected by server (empty message_error)")
                self.authenticated = False
                self._auth_success = False
                self._auth_confirmed.set()  # Wake waiting authentication code with failure

        @self.sio.on("pong", namespace=ns)
        async def pong(data):
            """Handle pong response from server for keep-alive"""
            self._pong_received = True
            self._pong_event.set()
            self._last_pong_time = time.time()
            logging.debug(f"🏓 {self.ws_room} Pong received: {data}")

        @self.sio.on("session_count_updated", namespace=ns)
        async def session_count_updated(data):
            """Handle session count update from server"""
            logging.info(f"📊 {self.ws_room} Session count updated: {data}")
            # Server may send either 'active_sessions' or 'roomsUsed' or 'session_count'
            self.ogb_sessions = data.get("active_sessions") or data.get("roomsUsed") or data.get("session_count") or self.ogb_sessions
            self.ogb_max_sessions = data.get("max_sessions") or data.get("maxRooms") or self.ogb_max_sessions
            # Emit to HA frontend for UI update
            await self._safe_emit(
                "session_update",
                {
                    "active_sessions": self.ogb_sessions,
                    "max_sessions": self.ogb_max_sessions,
                    "roomsUsed": self.ogb_sessions,  # Also include roomsUsed for frontend compatibility
                    "plan": data.get("plan", self._plan),
                    "active_rooms": data.get("active_rooms") or data.get("activeRooms", []),
                    "timestamp": data.get("timestamp", time.time()),
                },
                haEvent=True,
            )

        @self.sio.on("v1:monitoring:pong", namespace=ns)
        async def v1_pong(data):
            """Handle V1 pong response for keep-alive"""
            self._pong_received = True
            self._pong_event.set()
            self._last_pong_time = time.time()
            logging.debug(f"🏓 {self.ws_room} V1 Pong received: {data}")

        # =================================================================
        # INCOMING ENCRYPTED MESSAGE HANDLERS
        # Server sends encrypted messages for: api_usage_update, grow_plans, etc.
        # =================================================================
        
        @self.sio.on("v1:messaging:encrypted", namespace=ns)
        async def on_v1_encrypted_message(data):
            """Handle incoming V1 encrypted messages from server"""
            try:
                logging.debug(f"📨 {self.ws_room} Received V1 encrypted message")
                decrypted = self._decrypt_message(data)
                if decrypted:
                    msg_type = decrypted.get("type") or decrypted.get("message_type")
                    msg_data = decrypted.get("data", decrypted)
                    
                    logging.info(f"📨 {self.ws_room} Decrypted V1 message type: {msg_type}")
                    await self._route_incoming_message(msg_type, msg_data)
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling V1 encrypted message: {e}")

        @self.sio.on("encrypted_message", namespace=ns)
        async def on_legacy_encrypted_message(data):
            """Handle incoming legacy encrypted messages from server"""
            try:
                logging.debug(f"📨 {self.ws_room} Received legacy encrypted message")
                decrypted = self._decrypt_message(data)
                if decrypted:
                    msg_type = decrypted.get("type") or decrypted.get("message_type")
                    msg_data = decrypted.get("data", decrypted)
                    
                    logging.info(f"📨 {self.ws_room} Decrypted legacy message type: {msg_type}")
                    await self._route_incoming_message(msg_type, msg_data)
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling legacy encrypted message: {e}")

        # Direct (unencrypted) server push events
        @self.sio.on("api_usage_update", namespace=ns)
        async def on_api_usage_update(data):
            """Handle direct api_usage_update event from server"""
            logging.info(f"📊 {self.ws_room} Received api_usage_update: {data}")
            await self._handle_api_usage_update(data)

        @self.sio.on("new_grow_plans", namespace=ns)
        async def on_new_grow_plans(data):
            """Handle new grow plans from server"""
            logging.info(f"🌱 {self.ws_room} Received new_grow_plans: {data}")
            await self._handle_grow_plans(data)

        @self.sio.on("grow_plans", namespace=ns)
        async def on_grow_plans(data):
            """Handle grow plans response from server"""
            logging.info(f"🌱 {self.ws_room} Received grow_plans: {data}")
            await self._handle_grow_plans(data)

        # V1 Debug: Catch ALL events from server (namespace-specific)
        @self.sio.on('*', namespace=ns)
        async def v1_debug_all_events(event, *args):
            """Debug ALL events received from V1 server"""
            logging.warning(f"📨 {self.ws_room} V1 EVENT RECEIVED: {event} -> {args}")
            if 'auth' in event.lower() or 'session' in event.lower() or 'v1' in event.lower() or 'error' in event.lower():
                logging.error(f"🚨 {self.ws_room} AUTH/ERROR EVENT: {event} -> {args}")  # Make error events visible

        # Mark handlers as registered (prevent duplicate registration)
        self._handlers_registered = True
        logging.debug(f"✅ {self.ws_room} Event handlers registered successfully")

    async def health_check(self) -> dict:
        """Single comprehensive health check"""
        return {
            "room": self.ws_room,
            "connected": self.is_connected(),
            "ready": self.is_ready(),
            "authenticated": self.authenticated,
            "is_premium": self.is_premium,
            "session_valid": bool(self._session_key and self._session_id),
            "reconnect_attempts": self.reconnect_attempts,
            "reconnection_in_progress": self._reconnection_in_progress,
            "rotation_in_progress": self._rotation_in_progress,
            "keepalive_running": bool(
                self._keepalive_task and not self._keepalive_task.done()
            ),
            "user_id": self._user_id,
            "last_pong": self._last_pong_time,
            "timestamp": time.time(),
        }

    # =================================================================
    # Incoming Message Handlers
    # =================================================================

    async def _route_incoming_message(self, msg_type: str, msg_data: dict):
        """Route incoming decrypted messages to appropriate handlers"""
        if not msg_type:
            logging.warning(f"⚠️ {self.ws_room} Received message without type")
            return
        
        msg_type_lower = msg_type.lower()
        
        # API Usage updates
        if "usage" in msg_type_lower or msg_type == "api_usage_update":
            await self._handle_api_usage_update(msg_data)
        
        # Grow plans
        elif "grow_plan" in msg_type_lower or "grow-plan" in msg_type_lower:
            await self._handle_grow_plans(msg_data)
        
        # Session updates
        elif "session" in msg_type_lower:
            await self._handle_session_update(msg_data)
        
        # Feature flags
        elif "feature" in msg_type_lower:
            await self._handle_feature_update(msg_data)
        
        # Generic acknowledgments
        elif "ack" in msg_type_lower or "acknowledged" in msg_type_lower:
            logging.debug(f"✅ {self.ws_room} Received acknowledgment: {msg_type}")
        
        # Unknown message type - log for debugging
        else:
            logging.info(f"📨 {self.ws_room} Unhandled message type '{msg_type}': {msg_data}")

    async def _handle_api_usage_update(self, data: dict):
        """Handle API usage update from server"""
        try:
            logging.info(f"📊 {self.ws_room} Processing api_usage_update: {data}")
            
            # Server sends data with nested 'usage' object:
            # {'usage': {'roomsUsed': 1, 'growPlansUsed': 0, ...}, 'timestamp': ..., 'source': ...}
            # Extract the usage object, or use data directly if it's already flat
            usage = data.get("usage", data)
            
            # Extract rooms used - server sends 'roomsUsed'
            # activeConnections = roomsUsed (connected rooms = active sessions)
            rooms_used = usage.get("roomsUsed", 0)
            
            # Update local session count from usage data
            if rooms_used > 0:
                self.ogb_sessions = rooms_used
            
            # CRITICAL FIX: Update subscription_data.usage so it persists for browser refresh
            # When frontend reconnects, it gets subscription_data which must have current values
            if self.subscription_data:
                if "usage" not in self.subscription_data:
                    self.subscription_data["usage"] = {}
                self.subscription_data["usage"]["roomsUsed"] = rooms_used
                self.subscription_data["usage"]["growPlansUsed"] = usage.get("growPlansUsed", 0)
                self.subscription_data["usage"]["apiCallsThisMonth"] = usage.get("apiCallsThisMonth", 0)
                self.subscription_data["usage"]["storageUsedGB"] = usage.get("storageUsedGB", 0)
                self.subscription_data["usage"]["activeConnections"] = rooms_used
                self.subscription_data["usage"]["activeRooms"] = usage.get("activeRooms", [])
                logging.debug(f"📊 {self.ws_room} Updated subscription_data.usage with current values")
            
            # Wrap in 'usage' object for frontend compatibility 
            # Frontend expects: { usage: {...}, timestamp: ..., lastEndpoint: ..., lastMethod: ... }
            # activeConnections should always equal roomsUsed (connected rooms = active sessions)
            emit_data = {
                "usage": {
                    "roomsUsed": rooms_used,
                    "growPlansUsed": usage.get("growPlansUsed", 0),
                    "apiCallsThisMonth": usage.get("apiCallsThisMonth", 0),
                    "storageUsedGB": usage.get("storageUsedGB", 0),
                    "activeConnections": rooms_used,  # Same as roomsUsed - connected rooms = active sessions
                    "activeRooms": usage.get("activeRooms", []),
                },
                "timestamp": data.get("timestamp", time.time()),
                "lastEndpoint": data.get("lastEndpoint"),
                "lastMethod": data.get("lastMethod"),
            }
            
            # Emit to Home Assistant frontend
            await self._safe_emit("api_usage_update", emit_data, haEvent=True)
            logging.info(f"📊 {self.ws_room} Emitted api_usage_update to HA: rooms={rooms_used}, activeConnections={rooms_used}, apiCalls={emit_data['usage']['apiCallsThisMonth']}")
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling api_usage_update: {e}")
            import traceback
            logging.error(traceback.format_exc())

    async def _handle_grow_plans(self, data: dict):
        """Handle grow plans from server"""
        try:
            logging.info(f"🌱 {self.ws_room} Processing grow_plans: {data}")
            
            # Emit to HA for GrowPlanManager to handle
            await self._safe_emit("new_grow_plans", data, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling grow_plans: {e}")

    async def _handle_session_update(self, data: dict):
        """Handle session updates from server"""
        try:
            logging.info(f"🔐 {self.ws_room} Processing session_update: {data}")
            
            # Update local session info if provided
            if "session_count" in data:
                self.ogb_sessions = data.get("session_count", self.ogb_sessions)
            if "max_sessions" in data:
                self.ogb_max_sessions = data.get("max_sessions", self.ogb_max_sessions)
            
            # Emit to HA
            await self._safe_emit("session_update", {
                "room": self.ws_room,
                "active_sessions": self.ogb_sessions,
                "max_sessions": self.ogb_max_sessions,
                **data
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling session_update: {e}")

    async def _handle_feature_update(self, data: dict):
        """Handle feature flag updates from server"""
        try:
            logging.info(f"🚩 {self.ws_room} Processing feature_update: {data}")
            
            # Emit to HA for FeatureManager to handle
            await self._safe_emit("feature_flags_updated", data, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling feature_update: {e}")

    # =================================================================
    # Session Rotation
    # =================================================================

    async def _handle_session_rotation(self, rotation_data):
        """Enhanced session rotation with immediate cleanup confirmation"""
        if self._rotation_in_progress:
            logging.debug(f"Rotation already in progress for {self.ws_room}")
            return

        self._rotation_in_progress = True
        self._rotation_start_time = time.time()

        try:
            old_session_id = rotation_data.get("old_session_id")
            new_session_id = rotation_data.get("new_session_id")
            new_session_key_b64 = rotation_data.get("new_session_key")

            logging.debug(
                f"Starting enhanced session rotation for {self.ws_room}: {old_session_id} -> {new_session_id}"
            )

            # Validate rotation applies to our current session
            if old_session_id != self._session_id:
                logging.debug(
                    f"Rotation not for our session ({self._session_id}), ignoring"
                )
                self._rotation_in_progress = False
                return

            # Step 1: Decode and validate new session key
            try:
                new_session_key = self._safe_b64_decode(new_session_key_b64)
                if len(new_session_key) != 32:
                    raise ValueError(
                        f"Invalid session key length: {len(new_session_key)}"
                    )
                new_aes_gcm = AESGCM(new_session_key)
            except Exception as e:
                logging.error(
                    f"Failed to decode new session key for {self.ws_room}: {e}"
                )
                await self._rotation_failed(old_session_id, "key_decode_error")
                return

            old_session_key = self._session_key
            old_aes_gcm = self._aes_gcm

            self._session_id = new_session_id
            self._session_key = new_session_key
            self._aes_gcm = new_aes_gcm

            test_success = await self._test_new_session_with_timeout()
            if not test_success:
                logging.debug(
                    f"New session test failed for {self.ws_room}, rolling back"
                )
                # Rollback
                self._session_id = old_session_id
                self._session_key = old_session_key
                self._aes_gcm = old_aes_gcm
                await self._rotation_failed(old_session_id, "session_test_failed")
                return

            try:
                await self.sio.emit(
                    "session_rotation_acknowledged",
                    {
                        "old_session_id": old_session_id,
                        "new_session_id": new_session_id,
                        "immediate_cleanup_requested": True,  # Signal server to clean up NOW
                        "cleanup_confirmed": True,
                        "rotation_duration": time.time() - self._rotation_start_time,
                        "timestamp": time.time(),
                    },
                    namespace=self._v1_namespace,
                )

                logging.debug(
                    f"Sent immediate cleanup acknowledgment for {self.ws_room}"
                )
            except Exception as e:
                logging.warning(f"Socket acknowledgment failed for {self.ws_room}: {e}")

            await asyncio.sleep(1)  # Brief pause
            final_test = await self._test_new_session_with_timeout(timeout=5)

            if final_test:
                logging.warning(
                    f"Session rotation completed successfully for {self.ws_room}"
                )
                await self._safe_emit(
                    "LogForClient",
                    {
                        "Name": f"{self.ws_room} - Session rotation completed successfully",
                        "rotation_success": True,
                    },
                    haEvent=True,
                )
                await self._safe_emit("SaveRequest", True)
            else:
                logging.warning(
                    f"Final session test failed for {self.ws_room}, but rotation complete"
                )

        except Exception as e:
            logging.error(f"Session rotation process error for {self.ws_room}: {e}")
            await self._rotation_failed(old_session_id, f"process_error: {str(e)}")

        finally:
            self._rotation_in_progress = False
            self._rotation_start_time = None

    async def _test_new_session_with_timeout(self, timeout: int = 10) -> bool:
        """Test new session with configurable timeout"""
        try:
            if not self.is_connected():
                logging.warning(f"Not connected during session test for {self.ws_room}")
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
            await self.sio.emit("ses_test", test_data, namespace=self._v1_namespace)

            # Wait for pong with proper event signaling
            try:
                await asyncio.wait_for(self._pong_event.wait(), timeout=timeout)
                if self._pong_received:
                    duration = time.time() - ping_time
                    logging.warning(
                        f"Session test successful for {self.ws_room} in {duration:.2f}s"
                    )
                    return True
            except asyncio.TimeoutError:
                pass

            logging.warning(f"Session test timeout after {timeout}s for {self.ws_room}")
            return False

        except Exception as e:
            logging.error(f"Session test error for {self.ws_room}: {e}")
            return False

    async def _acknowledge_session_rotation(
        self, old_session_id: str, new_session_id: str
    ) -> bool:
        """Enhanced HTTP acknowledgment with retry logic"""
        max_retries = 3

        for attempt in range(max_retries):
            try:
                url = f"{self.api_url}/api/v1/auth/acknowledge-rotation"

                request_data = {
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id,
                    "user_id": self._user_id,
                    "access_token": self._access_token,
                    "immediate_cleanup": True,  # Request immediate cleanup
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
                                    logging.warning(
                                        f"HTTP acknowledgment successful for {self.ws_room} (attempt {attempt + 1})"
                                    )
                                    await self._safe_emit("SaveRequest", True)
                                    return True
                                else:
                                    logging.error(
                                        f"HTTP acknowledgment failed for {self.ws_room}: {result.get('message')}"
                                    )

                            except json.JSONDecodeError:
                                logging.error(
                                    f"Invalid HTTP acknowledgment response for {self.ws_room}"
                                )
                        else:
                            logging.error(
                                f"HTTP acknowledgment HTTP error for {self.ws_room}: {response.status}"
                            )

                        # If not last attempt, wait before retry
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1)
                            continue

                        return False

            except Exception as e:
                logging.error(
                    f"HTTP acknowledgment attempt {attempt + 1} failed for {self.ws_room}: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                continue

        return False

    async def _rotation_failed(self, old_session_id: str, reason: str):
        """Handle rotation failure with proper cleanup"""
        logging.error(f"Session rotation failed for {self.ws_room}: {reason}")

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
                namespace=self._v1_namespace,
            )
        except Exception as e:
            logging.error(
                f"Failed to send rotation error notification for {self.ws_room}: {e}"
            )

        # Reset rotation state
        self._rotation_in_progress = False
        self._rotation_start_time = None

        # Notify application
        await self._safe_emit(
            "LogForClient",
            f"Session rotation failed for {self.ws_room}: {reason}",
            haEvent=True,
        )

    # =================================================================
    # Cleanup Methods
    # =================================================================

    async def disconnect(self):
        """Clean disconnect of WebSocket only (shortened version)"""
        logging.warning(f"🔄 {self.ws_room} Disconnecting WebSocket")

        # Stop reconnection
        self._should_reconnect = False
        self._reconnection_in_progress = False

        # Stop keep-alive
        await self._stop_keepalive()

        # Cancel reconnect tasks
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass

        if hasattr(self, '_reconnect_task') and self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # Disconnect socket
        if hasattr(self, "sio") and self.ws_connected:
            try:
                await self.sio.disconnect()
            except Exception as e:
                logging.warning(f"Error during disconnect: {e}")

        # Reset connection states only
        self.ws_connected = False

        await self._safe_emit("ogb_client_disconnect", self.ogb_sessions)
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

        logging.warning(f"✅ {self.ws_room} WebSocket disconnected")

    async def cleanup_prem(self, event_id):
        """Enhanced cleanup with rotation task cancellation"""
        try:
            logging.warning(f"🧹 {self.ws_room} Cleaning up premium data")
            
            # CRITICAL: Set connection closing flag to stop all loops
            self._connection_closing = True
            self._should_reconnect = False

            # Cancel health monitor task
            if self._health_monitor_task and not self._health_monitor_task.done():
                self._health_monitor_task.cancel()
                try:
                    await self._health_monitor_task
                except asyncio.CancelledError:
                    pass

            # Cancel rotation task if running
            if self._rotation_task and not self._rotation_task.done():
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
                except:
                    pass

            # Reset all state variables including rotation state

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
            self._rotation_start_time = None
            self._should_reconnect = True
            self._reconnect_delay = 5
            self._auth_callback_called = False
            self._plan = None
            self._room_name = None

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

            # Reset handler registration flag since we have a NEW sio instance
            self._handlers_registered = False
            
            # Re-setup event handlers for the new socket instance
            self._setup_event_handlers()

            logging.warning(f"✅ {self.ws_room} Premium data cleanup completed")

            if event_id:
                await self._send_auth_response(
                    event_id,
                    "success",
                    "Logout successful",
                    {"logged_out_at": time.time()},
                )

            await self._safe_emit(
                "LogForClient",
                f"Successfully logged out from {self.ws_room}",
                haEvent=True,
            )
            return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Cleanup error: {e}")
            return False

    # =================================================================
    # Crypto
    # =================================================================

    def _safe_b64_decode(self, encoded_data: str) -> bytes:
        """Sicheres Base64 Dekodieren mit Padding-Korrektur"""
        try:
            encoded_data = encoded_data.strip()
            try:
                return base64.urlsafe_b64decode(encoded_data)
            except Exception:
                pass

            missing_padding = len(encoded_data) % 4
            if missing_padding:
                encoded_data += "=" * (4 - missing_padding)

            try:
                return base64.urlsafe_b64decode(encoded_data)
            except Exception:
                pass

            try:
                return base64.b64decode(encoded_data)
            except Exception:
                pass

            missing_padding = len(encoded_data) % 4
            if missing_padding:
                encoded_data += "=" * (4 - missing_padding)
            return base64.b64decode(encoded_data)

        except Exception as e:
            logging.error(f"❌ Base64 decode error: {e}")
            raise ValueError(f"Failed to decode base64 data: {e}")

    def _encrypt_message(self, data: dict) -> dict:
        """Verschlüssele Nachricht mit AES-GCM"""
        try:
            if not self._aes_gcm:
                raise ValueError("No encryption key available")

            message = json.dumps(data).encode("utf-8")
            nonce = secrets.token_bytes(12)
            ciphertext = self._aes_gcm.encrypt(nonce, message, None)

            return {
                "iv": base64.urlsafe_b64encode(nonce[:12]).decode(),
                "tag": base64.urlsafe_b64encode(ciphertext[-16:]).decode(),
                "data": base64.urlsafe_b64encode(ciphertext[:-16]).decode(),
                "timestamp": int(time.time()),
            }

        except Exception as e:
            logging.error(f"❌ Encryption error: {e}")
            raise

    def _decrypt_message(self, encrypted_data: dict) -> dict:
        """Entschlüssele Nachricht mit AES-GCM"""
        try:
            if not self._aes_gcm:
                raise ValueError("No decryption key available")

            nonce = base64.urlsafe_b64decode(encrypted_data["iv"])
            tag = base64.urlsafe_b64decode(encrypted_data["tag"])
            ciphertext_only = base64.urlsafe_b64decode(encrypted_data["data"])
            full_ciphertext = ciphertext_only + tag

            plaintext = self._aes_gcm.decrypt(nonce, full_ciphertext, None)
            return json.loads(plaintext.decode("utf-8"))

        except Exception as e:
            logging.error(f"❌ Decryption error: {e}")
            raise

    # =================================================================
    # User Data
    # =================================================================

    def get_connection_info(self) -> dict:
        """Get current connection information"""
        base_info = {
            "connected": self.sio.connected if hasattr(self, "sio") else False,
            "authenticated": self.authenticated,
            "is_logged_in": self.is_logged_in,
            "is_premium": self.is_premium,
            "user_id": self._user_id,
            "room_id": self.room_id,
            "room_name": self.ws_room,
            "session_id": self._session_id,
            "reconnect_attempts": self.ws_reconnect_attempts,
            "reconnection_in_progress": self._reconnection_in_progress,
            "rotation_in_progress": self._rotation_in_progress,
        }

        return base_info

    def get_user_info(self) -> dict:
        """Get user information"""
        return {
            "user_id": self._user_id,
            "is_logged_in": self.is_logged_in,
            "is_premium": self.is_premium,
            "subscription_data": self.subscription_data,
            "room_id": self.room_id,
            "room_name": self.ws_room,
        }

    def get_session_status(self) -> dict:
        """Enhanced session status with rotation information"""
        return {
            "session_id": self._session_id,
            "has_session_key": bool(self._session_key),
            "rotation_in_progress": self._rotation_in_progress,
            "rotation_start_time": self._rotation_start_time,
            "rotation_duration": (
                (time.time() - self._rotation_start_time)
                if self._rotation_start_time
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
        """Enhanced session backup with additional metadata"""
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
            "token_expires_at": self.token_expires_at,
            "access_token_hash": (
                hashlib.sha256(self._access_token.encode()).hexdigest()
                if self._access_token
                else None
            ),
            "ogb_sessions": self.ogb_sessions,
            "ogb_max_sessions": self.ogb_max_sessions,
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

    # =================================================================
    # Event Handling
    # =================================================================

    async def send_encrypted_message(self, message_type: str, data: dict) -> bool:
        """Send encrypted message"""
        try:
            # Check connection state first
            if not self.ws_connected:
                logging.warning(f"⚠️ {self.ws_room} Cannot send - WebSocket not connected")
                return False

            if not self.authenticated or not self._aes_gcm:
                logging.error(
                    f"❌ {self.ws_room} Cannot send - not authenticated or no encryption key"
                )
                return False

            message_data = {
                "type": message_type,
                "data": data,
                "timestamp": int(time.time()),
                "from": self._user_id,
                "client_id": self.client_id,
            }

            encrypted_data = self._encrypt_message(message_data)
            await self.sio.emit("encrypted_message", encrypted_data, namespace=self._v1_namespace)
            logging.debug(f"📤 {self.ws_room} Sent encrypted message: {message_type}")
            return True

        except aiohttp.ClientConnectionResetError as e:
            logging.warning(f"⚠️ {self.ws_room} Connection reset while sending: {e}")
            # Mark connection as closed and trigger cleanup
            self._handle_connection_lost("connection_reset")
            return False
        except aiohttp.ClientError as e:
            logging.warning(f"⚠️ {self.ws_room} Network error while sending: {e}")
            # Mark connection as potentially unstable
            self._handle_connection_lost("network_error")
            return False
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Unexpected send error: {type(e).__name__}: {e}")
            return False

    def _handle_connection_lost(self, reason: str):
        """Handle connection loss and cleanup state"""
        logging.warning(f"🔌 {self.ws_room} Connection lost: {reason}")

        # Mark connection as closed
        self.ws_connected = False
        self.authenticated = False

        # Clear encryption state
        self._aes_gcm = None
        self._session_key = None

        # Clear user state
        self._user_id = None
        self._session_id = None

        # Schedule reconnection attempt after a delay
        if hasattr(self, '_reconnect_task') and not self._reconnect_task.done():
            self._reconnect_task.cancel()

        self._reconnect_task = asyncio.create_task(self._schedule_reconnection(reason))

    async def _schedule_reconnection(self, reason: str):
        """Schedule reconnection with exponential backoff"""
        import random

        base_delay = 5  # Start with 5 seconds
        max_delay = 300  # Max 5 minutes
        max_attempts = 10

        for attempt in range(max_attempts):
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
            logging.info(f"🔄 {self.ws_room} Scheduling reconnection in {delay:.1f}s (attempt {attempt + 1}/{max_attempts}, reason: {reason})")

            await asyncio.sleep(delay)

            try:
                logging.info(f"🔄 {self.ws_room} Attempting reconnection...")
                success = await self.login_and_connect()
                if success:
                    logging.info(f"✅ {self.ws_room} Reconnection successful")
                    return
                else:
                    logging.warning(f"⚠️ {self.ws_room} Reconnection failed, will retry")
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Reconnection error: {e}")

        logging.error(f"❌ {self.ws_room} Failed to reconnect after {max_attempts} attempts")

    async def prem_event(self, message_type: str, data: dict) -> bool:
        """Send encrypted message via WebSocket - all logged-in users (free + premium)

        Uses V1 encrypted messaging for enhanced security and features.
        """
        try:
            # Diagnostic: Log prem_event call details with full state
            logging.warning(
                f"📨 {self.ws_room} PREM_EVENT called: type={message_type}, "
                f"ws_connected={self.ws_connected}, authenticated={self.authenticated}, "
                f"has_aes_gcm={self._aes_gcm is not None}, "
                f"sio_connected={self.sio.connected if self.sio else False}, "
                f"session_id={self._session_id[:16] if self._session_id else 'None'}..."
            )
            
            if not self.ws_connected:
                logging.warning(f"⚠️ {self.ws_room} Prem event skipped - WebSocket not connected (sio.connected={self.sio.connected if self.sio else False})")
                return False
            if not self.authenticated:
                logging.warning(f"⚠️ {self.ws_room} Prem event skipped - not authenticated (ws_connected={self.ws_connected}, _auth_success={self._auth_success})")
                return False

            # Use V1 encrypted messaging for all communication
            v1_message_type = f"v1:{message_type}"
            logging.debug(f"🔄 {self.ws_room} Calling send_v1_encrypted_message with type: {v1_message_type}")
            success = await self.send_v1_encrypted_message(v1_message_type, data)
            if success:
                logging.info(f"✅ {self.ws_room} Prem event sent successfully: {message_type}")
            else:
                logging.warning(f"⚠️ {self.ws_room} Prem event send returned False: {message_type}")
            return success

        except Exception as e:
            logging.error(f"❌ {self.ws_room} V1 encrypted send failed: {e}", exc_info=True)
            # Fallback to legacy encrypted messaging if V1 fails
            try:
                if not self._aes_gcm:
                    logging.warning(f"❌ {self.ws_room} Cannot send - no encryption key")
                    return False
                logging.warning(f"⚠️ {self.ws_room} Falling back to legacy encrypted messaging")
                return await self.send_encrypted_message(message_type, data)
            except Exception as fallback_error:
                logging.error(f"❌ {self.ws_room} Fallback also failed: {fallback_error}")
                return False

    async def submit_analytics(self, analytics_data: dict) -> bool:
        """
        Submit analytics data to Premium API via WebSocket.
        
        Args:
            analytics_data: Dictionary containing analytics data
                Required keys:
                - type: str - Analytics type (vpd, device, energy, etc.)
                - timestamp: str - ISO format timestamp
                Optional keys depend on analytics type
        
        Returns:
            bool: True if submission succeeded, False otherwise
        """
        try:
            if not self.ws_connected or not self.authenticated:
                logging.debug(f"⚠️ {self.ws_room} Analytics skipped - not connected")
                return False
            
            analytics_type = analytics_data.get("type", "general")
            event_name = f"analytics_{analytics_type}"
            
            # Add room context
            payload = {
                "room_id": self.room_id,
                "room_name": self.ws_room,
                **analytics_data
            }
            
            # Send via WebSocket
            await self.sio.emit(event_name, payload, namespace=self._v1_namespace)
            logging.debug(f"📊 {self.ws_room} Analytics submitted: {analytics_type}")
            return True
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Analytics submission failed: {e}")
            return False

    def encrypt_v1_message(self, message_type: str, data: dict) -> dict:
        """
        Encrypt a message using V1 format with AES-GCM.

        Args:
            message_type: Type of message (e.g., "v1:grow-data")
            data: Message data to encrypt

        Returns:
            dict: Encrypted message with iv, tag, data fields
        """
        if not self._session_key or not self._aes_gcm:
            raise ValueError("No encryption session available")

        import json
        import base64
        import os

        message_data = {
            "type": message_type,
            "data": data,
            "timestamp": int(time.time()),
            "v1_format": True
        }

        message_json = json.dumps(message_data).encode('utf-8')
        nonce = os.urandom(12)  # GCM nonce (96 bits)
        ciphertext = self._aes_gcm.encrypt(nonce, message_json, None)

        # Split ciphertext and tag (GCM format: ciphertext + 16-byte tag)
        encrypted_data = ciphertext[:-16]  # Everything except last 16 bytes
        tag = ciphertext[-16:]  # Last 16 bytes are the authentication tag

        return {
            "iv": base64.urlsafe_b64encode(nonce).decode(),
            "tag": base64.urlsafe_b64encode(tag).decode(),
            "data": base64.urlsafe_b64encode(encrypted_data).decode(),
            "event_id": data.get("event_id", f"v1-{int(time.time())}")
        }

    async def send_v1_encrypted_message(self, message_type: str, data: dict) -> bool:
        """
        Send V1 encrypted message via WebSocket.

        Args:
            message_type: Type of message
            data: Message data

        Returns:
            bool: Success status
        """
        try:
            # Diagnostic: Check all prerequisites
            if not self.ws_connected:
                logging.error(f"❌ {self.ws_room} Cannot send V1 encrypted - WebSocket not connected")
                return False
            if not self.authenticated:
                logging.error(f"❌ {self.ws_room} Cannot send V1 encrypted - not authenticated")
                return False
            if not self._aes_gcm:
                logging.error(f"❌ {self.ws_room} Cannot send V1 encrypted - no AES-GCM cipher (session key missing)")
                return False
            if not self.sio or not self.sio.connected:
                logging.error(f"❌ {self.ws_room} Cannot send V1 encrypted - Socket.IO not connected")
                return False

            # Diagnostic: Log encryption attempt
            key_hex = self._session_key[:8].hex() if self._session_key else "NO_KEY"
            logging.info(
                f"🔐 {self.ws_room} V1 ENCRYPT: type={message_type}, "
                f"session_key_first8={key_hex}, data_keys={list(data.keys()) if data else 'None'}"
            )

            encrypted_data = self.encrypt_v1_message(message_type, data)
            
            # Diagnostic: Log what we're sending
            logging.info(
                f"📤 {self.ws_room} V1 EMIT: event='v1:messaging:encrypted', "
                f"iv_len={len(encrypted_data.get('iv', ''))}, "
                f"tag_len={len(encrypted_data.get('tag', ''))}, "
                f"data_len={len(encrypted_data.get('data', ''))}"
            )
            
            await self.sio.emit("v1:messaging:encrypted", encrypted_data, namespace=self._v1_namespace)

            logging.debug(f"✅ {self.ws_room} Sent V1 encrypted message: {message_type}")
            return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} V1 encryption send failed: {e}", exc_info=True)
            return False

    async def send_v1_grow_data(self, grow_data: dict) -> bool:
        """
        Send grow data using V1 encrypted messaging.

        Args:
            grow_data: Grow data payload

        Returns:
            bool: Success status
        """
        return await self.send_v1_encrypted_message("v1:grow-data", grow_data)

    async def send_v1_ai_query(self, query_data: dict) -> bool:
        """
        Send AI query using V1 encrypted messaging.

        Args:
            query_data: AI query payload

        Returns:
            bool: Success status
        """
        return await self.send_v1_encrypted_message("v1:ai-query", query_data)

    async def _safe_send(self, event: str, data: dict) -> bool:
        """Safely send WebSocket data with connection state checks."""
        if self._connection_closing or not self.sio.connected:
            logging.debug(f"⚠️ {self.ws_room} Skipping send - connection closing or not connected")
            return False

        try:
            await self.sio.emit(event, data, namespace=self._v1_namespace)
            return True
        except aiohttp.ClientConnectionResetError as e:
            logging.warning(f"⚠️ {self.ws_room} Connection reset during send: {e}")
            self._connection_closing = True
            # Don't trigger reconnection here - let disconnect handler manage it
            return False
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Send error: {e}")
            return False

    async def _monitor_connection_health(self):
        """Monitor WebSocket connection health and recover from issues."""
        try:
            while not self._connection_closing:
                await asyncio.sleep(15)  # Check every 15 seconds

                # Exit if connection is closing
                if self._connection_closing:
                    logging.debug(f"🏓 {self.ws_room} Connection closing, stopping health monitor")
                    break

                # Skip monitoring if paused (during HA operations)
                if self._connection_monitoring_paused:
                    continue

                if self.sio and self.sio.connected:
                    # Test connection with ping
                    try:
                        await asyncio.wait_for(
                            self.sio.emit('ping', {'timestamp': time.time()}, namespace=self._v1_namespace),
                            timeout=5.0
                        )
                        logging.debug(f"🏓 {self.ws_room} Connection healthy")
                    except (asyncio.TimeoutError, Exception) as e:
                        if not self._connection_closing:
                            logging.warning(f"🏓 {self.ws_room} Ping failed: {e}")
                            await self._attempt_reconnect()
                elif not self._connection_closing:
                    logging.warning(f"🏓 {self.ws_room} Connection lost")
                    await self._attempt_reconnect()
                    
            logging.debug(f"🏓 {self.ws_room} Health monitoring loop exited cleanly")
        except asyncio.CancelledError:
            logging.debug(f"🏓 {self.ws_room} Connection health monitoring stopped")
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Connection health monitoring error: {e}")

    async def _attempt_reconnect(self):
        """Attempt to reconnect after connection issues.
        
        This method will:
        1. Clear any pending auth callbacks
        2. Request a fresh session key if needed
        3. Attempt WebSocket connection with new session
        """
        logging.info(f"🔄 {self.ws_room} Attempting reconnection...")

        try:
            # Check if reconnection should be attempted
            if not self._should_reconnect:
                logging.info(f"⏭️ {self.ws_room} Reconnection disabled, skipping")
                return False
            
            # Check max reconnection attempts
            self.ws_reconnect_attempts += 1
            if self.ws_reconnect_attempts > self.max_reconnect_attempts:
                logging.error(f"❌ {self.ws_room} Max reconnection attempts ({self.max_reconnect_attempts}) reached")
                self._should_reconnect = False
                return False
            
            # Brief delay before reconnect with exponential backoff
            delay = min(self._reconnect_delay * (1.5 ** (self.ws_reconnect_attempts - 1)), 60)
            logging.info(f"⏳ {self.ws_room} Waiting {delay:.1f}s before reconnection attempt {self.ws_reconnect_attempts}/{self.max_reconnect_attempts}")
            await asyncio.sleep(delay)

            # If we have pending auth callback, clear it
            if self._pending_auth_callback:
                logging.warning(f"⚠️ {self.ws_room} Clearing pending auth callback for reconnection")
                self._pending_auth_callback = None
                self._pending_event_id = None

            # Reset authentication state for fresh connection
            self.authenticated = False
            self._auth_confirmed.clear()
            self._auth_success = None
            
            # Clear stale session data to force fresh session key request
            # The _connect_websocket_internal() will auto-request a new one
            if self.ws_reconnect_attempts > 1:
                logging.info(f"🔑 {self.ws_room} Clearing stale session data for fresh reconnection")
                self._session_id = None
                self._session_key = None

            # Attempt reconnection - will auto-request session key if missing
            success = await self._connect_websocket()

            if success:
                logging.info(f"✅ {self.ws_room} Reconnection successful on attempt {self.ws_reconnect_attempts}")
                self.ws_reconnect_attempts = 0  # Reset on success
                self._reconnect_delay = 5  # Reset delay
                return True
            else:
                logging.error(f"❌ {self.ws_room} Reconnection failed on attempt {self.ws_reconnect_attempts}")
                
                # AUTO-RELOGIN FALLBACK: If we have stored credentials, try fresh login
                if self.ws_reconnect_attempts >= 3 and self._user_id and self._access_token:
                    logging.warning(f"🔄 {self.ws_room} Attempting auto-relogin with stored credentials after {self.ws_reconnect_attempts} failed reconnection attempts")
                    
                    try:
                        # Perform fresh login with stored credentials
                        # This will get a new session from the server
                        login_success = await self._perform_login(
                            email=getattr(self, '_stored_email', None) or "auto_relogin@restore",
                            OGBToken=self._access_token,
                            room_id=self.room_id,
                            room_name=self.ws_room,
                            event_id=self.create_event_id()
                        )
                        
                        if login_success:
                            logging.info(f"✅ {self.ws_room} Auto-relogin successful! Attempting reconnection...")
                            # Now try connecting with new session
                            connect_success = await self._connect_websocket()
                            if connect_success:
                                logging.info(f"✅ {self.ws_room} Connection successful after auto-relogin")
                                self.ws_reconnect_attempts = 0  # Reset on success
                                self._reconnect_delay = 5
                                return True
                            else:
                                logging.error(f"❌ {self.ws_room} Connection failed even after successful auto-relogin")
                        else:
                            logging.error(f"❌ {self.ws_room} Auto-relogin failed - credentials may be invalid")
                    except Exception as relogin_error:
                        logging.error(f"❌ {self.ws_room} Auto-relogin exception: {relogin_error}")
                
                return False

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Reconnection attempt failed: {e}")
            return False

    # =================================================================
    # Helper Methods
    # =================================================================

    async def _safe_emit(self, event: str, data, haEvent: bool = False):
        """Safely emit an event, checking if ogbevents is valid first.
        
        This prevents crashes during startup/shutdown when ogbevents 
        may not be initialized or may be an empty dict.
        """
        if self.ogbevents and hasattr(self.ogbevents, 'emit'):
            try:
                await self.ogbevents.emit(event, data, haEvent=haEvent)
            except Exception as e:
                logging.warning(f"⚠️ {self.ws_room} Failed to emit {event}: {e}")
        else:
            logging.debug(f"⚠️ {self.ws_room} Cannot emit {event} - ogbevents not available")

    def create_event_id(self):
        return str(uuid.uuid4())

    async def _handle_premium_actions(self, data):
        if self.room_id != data.get("room_id"):
            return

        await self._safe_emit("PremiumCheck", data)

    async def _send_auth_response(
        self, event_id: str, status: str, message: str, data: dict = None
    ):
        """Send authentication response"""
        response_data = {
            "event_id": event_id,
            "status": status,
            "message": message,
            "room": self.ws_room,
            "timestamp": datetime.now().isoformat(),
        }

        if data:
            response_data["data"] = data

        await self._safe_emit(
            "ogb_premium_auth_response", response_data, haEvent=True
        )

    def _validate_url(self, url: str) -> str:
        """Validate URL"""
        if not url or not isinstance(url, str):
            raise ValueError("Invalid URL provided")

        if not (
            url.startswith("ws://")
            or url.startswith("wss://")
            or url.startswith("http://")
            or url.startswith("https://")
        ):
            raise ValueError("Only ws://, wss://, http://, https:// protocols allowed")

        try:
            parsed = urlparse(url)
            if not parsed.hostname:
                raise ValueError("Invalid hostname in URL")
            return url.strip()
        except Exception as e:
            raise ValueError(f"URL validation failed: {e}")

    def is_connected(self) -> bool:
        """Check if WebSocket is connected and authenticated"""
        return (
            hasattr(self, "sio")
            and self.sio.connected
            and self.authenticated
            and self.ws_connected
        )

    def is_ready(self) -> bool:
        """Check if client is ready to send messages - all logged-in users"""
        return self.is_connected() and self.is_logged_in and self._session_key is not None

    async def health_check(self) -> dict:
        """Perform health check"""
        return {
            "room": self.ws_room,
            "connected": self.is_connected(),
            "ready": self.is_ready(),
            "authenticated": self.authenticated,
            "is_premium": self.is_premium,
            "session_valid": bool(self._session_key and self._session_id),
            "reconnect_attempts": self.ws_reconnect_attempts,
            "reconnection_in_progress": self._reconnection_in_progress,
            "user_id": self._user_id,
            "timestamp": time.time(),
        }

    async def room_removed(self):
        # NOTE: Session count now managed by API, updated via session_count_updated event
        logging.warning(
            f"{self.ws_room} - Disconnected - Sessions managed by API (current: {self.ogb_sessions}/{self.ogb_max_sessions})"
        )
        # Jetzt den aktuellen Wert senden
        await self._safe_emit("ogb_client_disconnect", self.ws_room, haEvent=True)

    async def room_disconnect(self, data):
        if self.ws_room == data:
            return
        # NOTE: Session count now managed by API, updated via session_count_updated event
        logging.warning(
            f"{self.ws_room} - Room {data} disconnected - Sessions managed by API (current: {self.ogb_sessions}/{self.ogb_max_sessions})"
        )

    # =================================================================
    # Session Monitoring for Frontend Updates
    # =================================================================

    async def start_session_monitoring(self):
        """Start monitoring session changes for frontend updates"""
        if (
            hasattr(self, "_session_monitoring_active")
            and self._session_monitoring_active
        ):
            return

        self._session_monitoring_active = True
        self._last_session_data = None

        logging.info(f"Starting session monitoring for {self.ws_room}")

        while self._session_monitoring_active and self.is_connected():
            try:
                # Query current session status from API
                session_data = await self.get_session_status()

                # Broadcast to HA frontend if changed
                if self._last_session_data != session_data:
                    await self._broadcast_session_update_to_frontend(session_data)
                    self._last_session_data = session_data

                await asyncio.sleep(30)  # Update every 30 seconds

            except Exception as e:
                logging.error(f"Session monitoring error for {self.ws_room}: {e}")
                await asyncio.sleep(60)  # Retry after error

        self._session_monitoring_active = False
        logging.info(f"Session monitoring stopped for {self.ws_room}")

    async def stop_session_monitoring(self):
        """Stop session monitoring"""
        self._session_monitoring_active = False
        logging.info(f"Session monitoring stop requested for {self.ws_room}")

    async def get_session_status(self) -> dict:
        """Get current session status from API"""
        try:
            if not self.authenticated:
                return {
                    "active_sessions": 0,
                    "max_sessions": 1,
                    "current_plan": "free",
                    "active_rooms": [],
                    "usage_percent": 0,
                }

            # Send request to API for session data
            response = await self.send_encrypted_message("get_session_status", {})

            if response and "session_data" in response:
                return response["session_data"]

            return {
                "active_sessions": 0,
                "max_sessions": 1,
                "current_plan": "free",
                "active_rooms": [],
                "usage_percent": 0,
            }
        except Exception as e:
            logging.error(f"Failed to get session status for {self.ws_room}: {e}")
            return {
                "active_sessions": 0,
                "max_sessions": 1,
                "current_plan": "free",
                "active_rooms": [],
                "usage_percent": 0,
                "error": str(e),
            }

    async def _broadcast_session_update_to_frontend(self, session_data):
        """Send session update to HA frontend"""
        try:
            await self._safe_emit("session_update", session_data, haEvent=True)
            logging.debug(f"Broadcasted session update to frontend: {session_data}")
        except Exception as e:
            logging.error(f"Failed to broadcast session update to frontend: {e}")



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
        notify_manager=None,
    ):
        # Connect to the main API URL - Socket.IO will use default /socket.io path
        self.base_url = self._validate_url(base_url)
        self.api_url = self._derive_api_root(base_url)
        # V1 API endpoints
        self.login_url = f"{self.api_url}/api/v1/auth/login"
        self.profile_url = f"{self.api_url}/api/v1/auth/profile"
        self.subscription_current_url = f"{self.api_url}/api/v1/subscriptions/current"
        self.timeout = timeout
        self.ogbevents = eventManager
        self.ws_room = ws_room
        self.room_id = room_id
        self.client_id = f"ogb-client-{self.ws_room}-{secrets.token_hex(8)}"
        self.notify_manager = notify_manager  # Store notification manager

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
        self._stored_email = None  # For auto-relogin after API restart
        self._stored_ogb_token = None  # For auto-relogin - the OGBToken (API key), NOT the JWT
        self._session_error_relogin_triggered = False  # Prevents disconnect handler from interfering
        self._credential_provider = None  # Callback to get credentials from OGBPremiumIntegration
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
        self._recovery_task = None
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
        # Server: pingInterval=30s, pingTimeout=60s
        # We use 30s interval to match server pingInterval
        self._last_pong_time = time.time()
        self._keepalive_task = None
        self._keepalive_interval = 30  # Match server's pingInterval (30s)
        self._pong_timeout = 30  # CRITICAL: Match server's pingInterval (30s)

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
        self._last_plan_fallback_check = 0.0
        # Deduplicate controller completion events (API can emit legacy + v1 ack for same event_id)
        self._processed_controller_events = {}

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
            # Default server: pingInterval=30000ms, pingTimeout=60000ms
            # We use slightly shorter intervals to stay ahead of server timeout
        )

        self._setup_event_listeners()
        self._setup_event_handlers()

    def _get_api_limit_from_subscription(self) -> Optional[int]:
        """Return the best known API call limit from subscription data."""
        if not isinstance(self.subscription_data, dict):
            return None

        limits = self.subscription_data.get("limits", {})
        if not isinstance(limits, dict):
            return None

        for key in (
            "apiCallsPerMonth",
            "api_calls_per_month",
            "apiCallLimit",
            "api_call_limit",
            "maxApiCalls",
            "max_api_calls",
        ):
            value = limits.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)

        return None

    def _normalize_api_limit_payload(
        self, used: Any, limit: Any, percent: Optional[Any] = None
    ) -> Optional[Dict[str, int]]:
        """Sanitize API limit payloads and reject invalid 0/0 server events."""
        try:
            used_value = int(float(used or 0))
        except (TypeError, ValueError):
            used_value = 0

        try:
            limit_value = int(float(limit or 0))
        except (TypeError, ValueError):
            limit_value = 0

        if limit_value <= 0:
            fallback_limit = self._get_api_limit_from_subscription()
            if fallback_limit and used_value <= fallback_limit:
                limit_value = fallback_limit
            else:
                logging.error(
                    f"❌ {self.ws_room} Ignoring invalid API limit payload: used={used}, limit={limit}, percent={percent}"
                )
                return None

        try:
            percent_value = int(round(float(percent))) if percent is not None else None
        except (TypeError, ValueError):
            percent_value = None

        if percent_value is None:
            percent_value = round((used_value / limit_value) * 100)

        return {
            "used": used_value,
            "limit": limit_value,
            "percent": percent_value,
        }

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

                # CRITICAL: Store credentials immediately for auto-relogin after API restart
                # This ensures we have them even if login fails later
                if email:
                    self._stored_email = email
                    logging.debug(f"🔐 {self.ws_room} Stored email for auto-relogin: {email[:3]}***")
                if OGBToken:
                    self._stored_ogb_token = OGBToken
                    logging.debug(f"🔐 {self.ws_room} Stored OGBToken for auto-relogin")

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
                
                # Step 5: Start health monitor (5-minute fallback for reconnection)
                await self._start_health_monitor()

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

                    # Check for PLAN_LIMIT_EXCEEDED error - block reconnection to prevent memory leak
                    if result.get("code") == "PLAN_LIMIT_EXCEEDED":
                        usage = result.get("usage", {})
                        normalized_limit = self._normalize_api_limit_payload(
                            usage.get("api_calls", 0),
                            usage.get("limit", 0),
                            usage.get("percentage"),
                        )
                        if not normalized_limit:
                            logging.error(
                                f"❌ {self.ws_room} Received PLAN_LIMIT_EXCEEDED with invalid payload, skipping hard block: {result}"
                            )
                            await self._send_auth_response(
                                event_id,
                                "error",
                                "API returned an invalid limit state. Please retry in a moment.",
                            )
                            return False

                        api_calls = normalized_limit["used"]
                        limit = normalized_limit["limit"]
                        percentage = normalized_limit["percent"]
                        plan = result.get("plan", "unknown")
                        
                        logging.error(
                            f"🛑 {self.ws_room} Login BLOCKED - API limit exceeded: "
                            f"{api_calls}/{limit} calls ({percentage}%) on {plan} plan"
                        )
                        
                        # Disable reconnection to prevent memory leak from retry loop
                        self._should_reconnect = False
                        logging.warning(f"🔴 {self.ws_room} Auto-reconnection DISABLED due to API limit exceeded")
                        
                        # Send error response to client
                        await self._send_auth_response(
                            event_id, "error", 
                            f"API limit exceeded ({api_calls}/{limit} calls). Please upgrade your plan."
                        )
                        
                        # Trigger cleanup to clean up
                        asyncio.create_task(self.cleanup_prem(None))
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
                    # CRITICAL: Use explicit None check to allow 0 sessions
                    self.ogb_max_sessions = result.get("ogb_max_sessions") or result.get("maxSessions") or result.get("max_sessions") or 2
                    
                    # Explicit check - don't default to 1 if API returns 0
                    ogb_sessions = result.get("ogb_sessions")
                    if ogb_sessions is None:
                        ogb_sessions = result.get("sessionCount") or result.get("session_count") or 1
                    self.ogb_sessions = ogb_sessions
                    
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
                logging.debug(f"⚠️ {self.ws_room} No session data, requesting new session key...")
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
            logging.debug(f"🔗 {self.ws_room} Connecting to V1 WebSocket: {base_url} namespace: {self._v1_namespace} with plan: {actual_plan}")
            logging.debug(f"🔐 {self.ws_room} V1 AUTH HEADERS: {auth_headers}")
            logging.debug(f"🎯 {self.ws_room} Session ID: {auth_headers.get('ogb-session-id', 'MISSING')}")
            logging.debug(f"🏷️ {self.ws_room} Client ID: {auth_headers.get('ogb-client', 'MISSING')}")
            logging.debug(f"📊 {self.ws_room} User ID: {auth_headers.get('ogb-user-id', 'MISSING')}")

            logging.debug(f"✅ {self.ws_room} Session ready for V1 auth: {self._session_id[:8]}...")

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
            logging.debug(f"🔗 {self.ws_room} Starting V1 WebSocket connect to namespace {self._v1_namespace}...")
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
            logging.debug(f"🔗 {self.ws_room} WebSocket connect call completed, connected={self.sio.connected}")

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
                logging.debug(f"✅ {self.ws_room} V1 authentication already confirmed (received during connect)")
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

    async def request_grow_plans(self):
        """Request grow plans from server via V1 WebSocket"""
        try:
            if not self.sio.connected or not self.authenticated:
                logging.warning(f"⚠️ {self.ws_room} Cannot request grow plans - not connected")
                return False
            
            event_id = f"get-plans-{int(time.time())}"
            request_data = {
                "event_id": event_id,
                "timestamp": time.time()
            }
            
            logging.info(f"📤 {self.ws_room} Requesting grow plans from server")
            await self.sio.emit("v1:management:get-plans", request_data, namespace=self._v1_namespace)
            return True
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error requesting grow plans: {e}")
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

            logging.debug(f"🔑 {self.ws_room} Requesting new session key")

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
                logging.debug(
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
                logging.debug(f"🔗 {self.ws_room} Connecting WebSocket with session_id={self._session_id}")
                if not await self._connect_websocket():
                    logging.error(f"❌ {self.ws_room} WebSocket connection failed")
                    return False

                # Start keep-alive
                await self._start_keepalive()

                logging.debug(f"✅ {self.ws_room} Session established from auth data - authenticated={self.authenticated}, ws_connected={self.ws_connected}")
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
                            self._schedule_recovery_task("reconnect", "keepalive_failure")
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
                        self._schedule_recovery_task("reconnect", "keepalive_exception")
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
        async def on_disconnect(reason=None):
            logging.warning(f"❌ {self.ws_room} WebSocket disconnected from V1 namespace (reason: {reason})")
            self.ws_connected = False
            self.authenticated = False
            
            # Check if session error handler already triggered re-login
            if self._session_error_relogin_triggered:
                logging.info(f"⏭️ {self.ws_room} Session error already triggered re-login - skipping disconnect handler")
                return
            
            # Trigger automatic reconnection if enabled and not already in progress
            if self._should_reconnect and not self._reconnection_in_progress and not self._connection_closing:
                # CRITICAL: If we have stored credentials, do immediate re-login instead of slow reconnect
                # This handles API restarts where all sessions are invalidated
                # Check local credentials first, then try credential provider
                has_credentials = self._stored_email and self._stored_ogb_token
                if not has_credentials and self._credential_provider:
                    try:
                        creds = self._credential_provider()
                        has_credentials = creds and creds.get("email") and creds.get("token")
                    except:
                        pass
                
                if has_credentials:
                    logging.info(f"🔐 {self.ws_room} Have credentials - triggering immediate re-login after disconnect...")
                    self._schedule_recovery_task("relogin", "disconnect_with_credentials")
                else:
                    logging.info(f"🔄 {self.ws_room} No stored credentials - scheduling reconnection after disconnect...")
                    self._schedule_recovery_task("reconnect", "disconnect_event")

        @self.sio.on('auth_success', namespace=ns)
        async def on_auth_success(data):
            logging.debug(f"🎉 AUTH SUCCESS RECEIVED: {data}")

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

                logging.debug(f"🎉 {self.ws_room} V1 SESSION CONFIRMED RECEIVED: {data}")
                logging.debug(f"🔍 {self.ws_room} Session ID check: server={session_id}, local={self._session_id}, match={session_id == self._session_id}")

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
                    
                    logging.debug(f"✅ {self.ws_room} V1 session confirmed - encryption ready (AES-256-GCM) - sessions: {self.ogb_sessions}/{self.ogb_max_sessions}")

                    # Call auth callback if not already called by auth_success
                    if self._pending_auth_callback and not self._auth_callback_called:
                        logging.debug(f"🔄 {self.ws_room} Calling stored auth callback after V1 session confirmed")
                        try:
                            await self._pending_auth_callback(
                                self._pending_event_id,
                                "success",
                                "V1 session confirmed",
                                data
                            )
                            logging.debug(f"✅ {self.ws_room} Auth callback called successfully")
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
        async def on_v1_error(data):
            """Handle V1 errors - indicates authentication failure (e.g., session not found after API restart)"""
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
            
            # Check if this is a session-not-found error (API was restarted)
            # In this case, trigger immediate re-login instead of waiting for reconnect attempts
            error_msg = str(data.get('message', '')).lower()
            error_code = str(data.get('code', '')).upper()
            
            is_session_error = (
                'session' in error_msg or 
                'not found' in error_msg or 
                'invalid' in error_msg or 
                error_code == 'INVALID_SESSION' or
                error_code == 'SESSION_NOT_FOUND' or
                error_code == 'AUTH_FAILED' or
                data == {}
            )
            
            if is_session_error:
                logging.warning(f"🔐 {self.ws_room} Session error detected (code={error_code}, msg={error_msg}) - triggering immediate re-login...")
                # IMPORTANT: Don't block here with sleep - create task immediately
                # The re-login method has its own locking to prevent duplicates
                # Check local credentials first, then try credential provider
                has_credentials = self._stored_email and self._stored_ogb_token
                logging.info(f"🔐 {self.ws_room} Local credentials: email={bool(self._stored_email)}, token={bool(self._stored_ogb_token)}, provider={bool(self._credential_provider)}")
                if not has_credentials and self._credential_provider:
                    try:
                        creds = self._credential_provider()
                        logging.info(f"🔐 {self.ws_room} Provider returned: email={bool(creds.get('email') if creds else None)}, token={bool(creds.get('token') if creds else None)}")
                        has_credentials = creds and creds.get("email") and creds.get("token")
                    except Exception as e:
                        logging.error(f"❌ {self.ws_room} Provider error: {e}")
                
                if has_credentials:
                    # Set flag to prevent disconnect handler from interfering
                    self._session_error_relogin_triggered = True
                    logging.info(f"🔐 {self.ws_room} Have credentials - scheduling re-login task")
                    self._schedule_recovery_task("relogin", "v1_error_session_invalid")
                else:
                    logging.error(f"❌ {self.ws_room} Cannot re-login: no credentials available")

        @self.sio.on("grow-data_acknowledged", namespace=ns)
        async def grow_data_acknowledged(data):
            """Handle grow data acknowledgement from V1 API"""
            logging.info(f"✅ {self.ws_room} Grow data acknowledged: {data}")
            # Legacy acknowledgment (kept for compatibility). We route controller
            # actions from v1:control:grow-data:completed only to avoid double execution.

        @self.sio.on("v1:control:grow-data:completed", namespace=ns)
        async def v1_grow_data_completed(data):
            """Handle V1 grow-data completion with controller actions."""
            logging.info(f"✅ {self.ws_room} V1 grow data completed: {data}")
            await self._handle_controller_completed(data, "v1:control:grow-data:completed")

        @self.sio.on("grow-completed_acknowledged", namespace=ns)
        async def grow_completed_acknowledged(data):
            """Handle grow completion ack from API."""
            logging.info(f"🏁 {self.ws_room} Grow completed acknowledged: {data}")

        @self.sio.on("v1:grow:completed:ack", namespace=ns)
        async def v1_grow_completed_ack(data):
            """Handle V1 grow completion ack from API."""
            logging.info(f"🏁 {self.ws_room} V1 grow completed ack: {data}")

        @self.sio.on("message_acknowledged", namespace=ns)
        async def message_acknowledged(data):
            """Handle generic message acknowledgement"""
            logging.debug(f"✅ {self.ws_room} Message acknowledged: {data}")

        @self.sio.on("message_error", namespace=ns)
        async def message_error(data):
            """Handle message errors from server"""
            logging.error(f"❌ {self.ws_room} Message error: {data}")

            # Empty error object indicates authentication failure (session not found)
            if data == {}:
                logging.error(f"❌ {self.ws_room} Authentication rejected by server (empty message_error) - triggering re-login")
                self.authenticated = False
                self._auth_success = False
                self._auth_confirmed.set()  # Wake waiting authentication code with failure
                
                # Trigger re-login for session recovery
                self._schedule_recovery_task("relogin", "message_error_empty")

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

        @self.sio.on("limit_warning", namespace=ns)
        async def on_limit_warning(data):
            """Handle limit warning from server (80% or 90% of limit reached)"""
            logging.warning(f"⚠️ {self.ws_room} Limit warning: {data}")
            
            resource = data.get("resource", "unknown")
            current = data.get("current", 0)
            limit = data.get("limit", 0)
            percentage = data.get("percentage", 0)
            warning_level = data.get("warningLevel", "warning")
            message = data.get("message", "")

            if resource == "apiCalls" or resource == "api_calls":
                api_limit_data = self._normalize_api_limit_payload(
                    current, limit, percentage
                )
                if not api_limit_data:
                    logging.warning(
                        f"⚠️ {self.ws_room} Ignoring invalid API limit warning event: {data}"
                    )
                    return
                current = api_limit_data["used"]
                limit = api_limit_data["limit"]
                percentage = api_limit_data["percent"]
            
            # Emit to HA frontend for notification
            await self._safe_emit(
                "limit_warning",
                {
                    "resource": resource,
                    "current": current,
                    "limit": limit,
                    "percentage": percentage,
                    "message": message,
                    "warning_level": warning_level,
                    "blocking": False,
                    "room": self.ws_room,
                    "timestamp": time.time(),
                },
                haEvent=True,
            )
            
            # Send notification via OGBNotifyManager if available
            if self.notify_manager:
                if resource == "apiCalls" or resource == "api_calls":
                    await self.notify_manager.notify_api_warning(
                        used=current,
                        limit=limit,
                        percent=percentage
                    )
                elif resource == "storage":
                    await self.notify_manager.notify_storage_warning(
                        used_gb=current,
                        limit_gb=limit,
                        percent=percentage
                    )

        @self.sio.on("limit_exceeded", namespace=ns)
        async def on_limit_exceeded(data):
            """Handle limit exceeded from server (operation blocked)"""
            logging.error(f"🛑 {self.ws_room} Limit EXCEEDED (blocking): {data}")
            
            resource = data.get("resource", "unknown")
            current = data.get("current", 0)
            limit = data.get("limit", 0)
            message = data.get("message", "Limit exceeded")
            blocking = data.get("blocking", True)

            api_limit_data = None
            if resource == "apiCalls" or resource == "api_calls":
                api_limit_data = self._normalize_api_limit_payload(current, limit)
                if not api_limit_data:
                    logging.warning(
                        f"⚠️ {self.ws_room} Ignoring invalid blocking API limit event: {data}"
                    )
                    return
                current = api_limit_data["used"]
                limit = api_limit_data["limit"]
            
            # Emit to HA frontend for notification
            await self._safe_emit(
                "limit_exceeded",
                {
                    "resource": resource,
                    "current": current,
                    "limit": limit,
                    "message": message,
                    "blocking": blocking,
                    "room": self.ws_room,
                    "timestamp": time.time(),
                },
                haEvent=True,
            )
            
            # Send critical notification via OGBNotifyManager if available
            if self.notify_manager:
                if resource == "apiCalls" or resource == "api_calls":
                    await self.notify_manager.notify_api_limit_reached(
                        used=current,
                        limit=limit,
                        percent=api_limit_data["percent"] if api_limit_data else round((current / limit) * 100)
                    )
                elif resource == "storage":
                    await self.notify_manager.notify_storage_limit_reached(
                        used_gb=current,
                        limit_gb=limit,
                        percent=round((current / limit) * 100) if limit > 0 else 100
                    )
            
            # CRITICAL: Disable reconnection and cleanup to prevent memory leak from retry loop
            # When API limit is exceeded, the client should stop trying to reconnect
            if resource == "apiCalls" or resource == "api_calls":
                logging.error(f"🛑 {self.ws_room} API limit exceeded - disabling auto-reconnection to prevent memory leak")
                self._should_reconnect = False
                
                # Trigger cleanup to disconnect and clean up session on server
                logging.info(f"🔴 {self.ws_room} Initiating cleanup due to API limit exceeded")
                try:
                    await self.cleanup_prem(None)
                except Exception as e:
                    logging.error(f"❌ {self.ws_room} Cleanup failed after limit exceeded: {e}")

        @self.sio.on("subscription_expiring_soon", namespace=ns)
        async def on_subscription_expiring_soon(data):
            """Handle subscription expiring warning"""
            logging.warning(f"⚠️ {self.ws_room} Subscription expiring soon: {data}")
            await self._handle_subscription_expiring(data)

        @self.sio.on("subscription_expired", namespace=ns)
        async def on_subscription_expired(data):
            """Handle subscription expired event"""
            logging.error(f"🚨 {self.ws_room} Subscription EXPIRED: {data}")
            await self._handle_subscription_expired(data)

        # === OGB Token Expiration Events ===
        @self.sio.on("v1:token:expiring:warning", namespace=ns)
        async def on_token_expiring_warning(data):
            """Handle OGB token expiring warning (7 days before)"""
            logging.warning(f"⚠️ {self.ws_room} OGB Token expiring soon: {data}")
            await self._handle_token_expiring_warning(data)

        @self.sio.on("v1:token:expiring:critical", namespace=ns)
        async def on_token_expiring_critical(data):
            """Handle OGB token expiring critical (3 days before)"""
            logging.error(f"🔴 {self.ws_room} OGB Token EXPIRING CRITICAL: {data}")
            await self._handle_token_expiring_critical(data)

        @self.sio.on("v1:token:expired", namespace=ns)
        async def on_token_expired(data):
            """Handle OGB token expired event"""
            logging.error(f"🚨 {self.ws_room} OGB Token EXPIRED: {data}")
            await self._handle_token_expired(data)

        # === Control Sync Events (from Webapp) ===
        @self.sio.on("ctrl_change", namespace=ns)
        async def on_ctrl_change(data):
            """Handle control mode changes from webapp"""
            logging.info(f"🎛️ {self.ws_room} Control change from webapp: {data}")
            await self._handle_ctrl_change(data)

        @self.sio.on("ctrl_values_change", namespace=ns)
        async def on_ctrl_values_change(data):
            """Handle control values changes from webapp"""
            logging.info(f"🎛️ {self.ws_room} Control values change from webapp: {data}")
            await self._handle_ctrl_values_change(data)

        @self.sio.on("ctrl_value_update", namespace=ns)
        async def on_ctrl_value_update(data):
            """Handle individual control value update from webapp"""
            logging.debug(f"🎛️ {self.ws_room} Control value update: {data}")
            await self._handle_ctrl_value_update(data)

        @self.sio.on("plant_stage_change", namespace=ns)
        async def on_plant_stage_change(data):
            """Handle plant stage change from webapp"""
            logging.info(f"🌱 {self.ws_room} Plant stage change from webapp: {data}")
            await self._handle_plant_stage_change(data)

        @self.sio.on("plant_view_need", namespace=ns)
        async def plant_view_need(data):
            """Handle plant view need from webapp"""
            logging.info(f"🌱 {self.ws_room} USER Plant VIEW REQUEST: {data}")
            await self._handle_plant_view_need(data)

        @self.sio.on("prem_actions", namespace=ns)
        async def on_prem_actions(data):
            """Handle premium actions from webapp"""
            logging.info(f"⚡ {self.ws_room} Premium actions from webapp: {data}")
            await self._handle_prem_actions(data)
        
        # === Control Sync Events (from Webapp) ===
        @self.sio.on("connection_status", namespace=ns)
        async def on_connection_status(data):
            """Handle connection_status event from server - updates plan and session data"""
            logging.info(f"📊 {self.ws_room} Connection status update: {data}")
            
            try:
                # New consistent API structure: { plan, features, limits, usage, ogb_sessions, timestamp }
                server_plan = data.get("plan")
                features = data.get("features", {})
                limits = data.get("limits", {})
                usage = data.get("usage", {})
                ogb_sessions = data.get("ogb_sessions", {})
                
                logging.info(f"📊 {self.ws_room} Connection status: plan={server_plan}")
                
                # Extract session info
                if isinstance(ogb_sessions, dict):
                    active_sessions = ogb_sessions.get("active", 0)
                    max_sessions = ogb_sessions.get("max_sessions") or ogb_sessions.get("maxRooms")
                else:
                    active_sessions = ogb_sessions.get("active", 0) if isinstance(ogb_sessions, dict) else ogb_sessions or 0
                    max_sessions = None
                
                # Update local session counts
                self.ogb_sessions = active_sessions
                if max_sessions:
                    self.ogb_max_sessions = max_sessions
                
                # CRITICAL FIX: Update subscription_data with FULL structure
                if not self.subscription_data:
                    self.subscription_data = {}
                
                # Update plan from connection_status (source of truth!)
                if server_plan:
                    self.subscription_data["plan_name"] = server_plan
                    self._plan = server_plan
                    logging.info(f"📊 {self.ws_room} Updated plan from connection_status: {server_plan}")
                
                # Update features and limits
                if features:
                    self.subscription_data["features"] = features
                    logging.info(f"📊 {self.ws_room} Updated features: {len(features)}")
                
                if limits:
                    self.subscription_data["limits"] = limits
                    logging.info(f"📊 {self.ws_room} Updated limits: {len(limits)}")
                
                # Update usage from connection_status
                if "usage" not in self.subscription_data:
                    self.subscription_data["usage"] = {}
                
                # Merge usage data - connection_status may have partial data
                for key, value in usage.items():
                    self.subscription_data["usage"][key] = value
                
                # Override room-specific fields with validated values
                normalized_current_room = str(self.ws_room).lower()
                if active_sessions > 0:
                    self.subscription_data["usage"]["activeConnections"] = active_sessions
                    self.subscription_data["usage"]["activeRooms"] = [normalized_current_room]
                    self.subscription_data["usage"]["roomsUsed"] = 1
                else:
                    self.subscription_data["usage"]["activeConnections"] = 0
                    self.subscription_data["usage"]["activeRooms"] = []
                    self.subscription_data["usage"]["roomsUsed"] = 0
                
                if max_sessions:
                    self.subscription_data["usage"]["maxSessions"] = max_sessions
                
                # Emit to Premium Integration with FULL structure
                emit_data = {
                    "plan": server_plan,
                    "features": features,
                    "limits": limits,
                    "usage": self.subscription_data.get("usage", {}),
                    "timestamp": data.get("timestamp", time.time())
                }
                
                await self._safe_emit("api_usage_update", emit_data, haEvent=True)
                
                logging.info(
                    f"📊 {self.ws_room} Updated from connection_status: "
                    f"plan={server_plan}, sessions={active_sessions}/{max_sessions or self.ogb_max_sessions}"
                )
                
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling connection_status: {e}")

        async def _handle_grow_plans_changed_event(data, event_name="plan_changed"):
            """Handle plan change events from server and forward to PremiumIntegration."""
            try:
                plan_id = data.get("plan_id")
                plan_name = data.get("plan_name")
                action = data.get("action", "sync")
                source = data.get("source", "unknown")
                scheduled = data.get("scheduled", False)

                logging.info(
                    f"📊 {self.ws_room} {event_name}: plan_id={plan_id}, plan_name={plan_name}, "
                    f"action={action}, source={source}, scheduled={scheduled}"
                )

                # connection_status will usually follow with full plan/features/limits snapshot.
                # Forward explicit event immediately so PremiumIntegration can trigger notifications.
                emit_data = {
                    "plan_id": plan_id,
                    "plan_name": plan_name,
                    "action": action,
                    "source": source,
                    "scheduled": scheduled,
                    "timestamp": data.get("timestamp", time.time()),
                }

                logging.info(f"📤 {self.ws_room} Emitting plan_changed event to event_manager: {emit_data}")
                await self._safe_emit("plan_changed", emit_data, haEvent=True)
                logging.info(f"✅ {self.ws_room} plan_changed event emitted successfully")

            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling {event_name}: {e}")

        async def _handle_plan_changed_event(data, event_name="plan_changed"):
            """Handle plan change events from server and forward to PremiumIntegration."""
            try:
                plan_id = data.get("plan_id")
                plan_name = data.get("plan_name")
                action = data.get("action", "sync")
                source = data.get("source", "unknown")
                scheduled = data.get("scheduled", False)

                logging.info(
                    f"📊 {self.ws_room} {event_name}: plan_id={plan_id}, plan_name={plan_name}, "
                    f"action={action}, source={source}, scheduled={scheduled}"
                )

                # connection_status will usually follow with full plan/features/limits snapshot.
                # Forward explicit event immediately so PremiumIntegration can trigger notifications.
                emit_data = {
                    "plan_id": plan_id,
                    "plan_name": plan_name,
                    "action": action,
                    "source": source,
                    "scheduled": scheduled,
                    "timestamp": data.get("timestamp", time.time()),
                }

                logging.info(f"📤 {self.ws_room} Emitting plan_changed event to event_manager: {emit_data}")
                await self._safe_emit("plan_changed", emit_data, haEvent=True)
                logging.info(f"✅ {self.ws_room} plan_changed event emitted successfully")

            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling {event_name}: {e}")

        @self.sio.on("plan_changed", namespace=ns)
        async def on_plan_changed(data):
            await _handle_plan_changed_event(data, "plan_changed")

        @self.sio.on("v1:management:plan-changed", namespace=ns)
        async def on_v1_plan_changed(data):
            await _handle_plan_changed_event(data, "v1:management:plan-changed")

        @self.sio.on("v1:plans:get:response", namespace=ns)
        async def on_v1_get_plans_response(data):
            """Handle grow plans data response from server"""
            try:
                active_plan = data.get("activePlan")
                current_week = data.get("currentWeek", 1)
                week_data = data.get("weekData")
                
                if active_plan:
                    logging.info(
                        f"📊 {self.ws_room} Received grow plan data: "
                        f"plan={active_plan.get('name')}, week={current_week}/{active_plan.get('totalWeeks')}"
                    )
                    
                    # Store active grow plan
                    self.active_grow_plan = {
                        "id": active_plan.get("id"),
                        "name": active_plan.get("name"),
                        "strain": active_plan.get("strain"),
                        "status": active_plan.get("status"),
                        "current_week": current_week,
                        "total_weeks": active_plan.get("totalWeeks"),
                        "start_date": active_plan.get("startDate"),
                        "week_data": week_data
                    }
                    
                    # Emit to event manager for PremiumIntegration
                    emit_data = {
                        "type": "grow_plan_data",
                        "plan": self.active_grow_plan,
                        "timestamp": data.get("timestamp", time.time())
                    }
                    
                    await self._safe_emit("grow_plan_data", emit_data, haEvent=True)
                    logging.info(f"✅ {self.ws_room} Grow plan data emitted successfully")
                else:
                    logging.warning(f"⚠️ {self.ws_room} No active grow plan received")
                    
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling grow plan data: {e}")

        @self.sio.on("storage_limit_reached", namespace=ns)
        async def on_storage_limit_reached(data):
            """Handle storage_limit_reached event from server"""
            try:
                used = data.get("used", 0)
                limit = data.get("limit", 1)
                percent = data.get("percent", 100)
                upgrade_url = data.get("upgradeUrl", "/settings/upgrade")
                plan = data.get("plan", "free")
                
                logging.warning(f"🚫 {self.ws_room} Storage limit REACHED: {used}/{limit}GB ({percent:.0f}%)")
                
                # Emit to Home Assistant frontend
                emit_data = {
                    "type": "storage_limit_reached",
                    "used": used,
                    "limit": limit,
                    "percent": percent,
                    "upgrade_url": upgrade_url,
                    "plan": plan,
                    "message": f"Storage full ({used}/{limit}GB). Upgrade to continue storing data.",
                    "timestamp": data.get("timestamp", time.time())
                }
                
                await self._safe_emit("storage_alert", emit_data, haEvent=True)
                
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling storage_limit_reached: {e}")
        
        @self.sio.on("api_limit_reached", namespace=ns)
        async def on_api_limit_reached(data):
            """Handle api_limit_reached event from server"""
            try:
                used = data.get("used", 0)
                limit = data.get("limit", 1000)
                percent = data.get("percent")
                upgrade_url = data.get("upgradeUrl", "/settings/upgrade")
                plan = data.get("plan", "free")

                normalized_limit = self._normalize_api_limit_payload(used, limit, percent)
                if not normalized_limit:
                    logging.warning(
                        f"⚠️ {self.ws_room} Ignoring invalid api_limit_reached event: {data}"
                    )
                    return

                used = normalized_limit["used"]
                limit = normalized_limit["limit"]
                percent = normalized_limit["percent"]
                
                logging.warning(f"🚫 {self.ws_room} API limit REACHED: {used}/{limit} calls ({percent:.0f}%)")
                
                # Emit to Home Assistant frontend
                emit_data = {
                    "type": "api_limit_reached",
                    "used": used,
                    "limit": limit,
                    "percent": percent,
                    "upgrade_url": upgrade_url,
                    "plan": plan,
                    "message": f"API limit reached ({used}/{limit} calls). Upgrade to continue.",
                    "timestamp": data.get("timestamp", time.time())
                }
                
                await self._safe_emit("api_alert", emit_data, haEvent=True)
                
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling api_limit_reached: {e}")
        
        @self.sio.on("maintenance_alert", namespace=ns)
        async def on_maintenance_alert(data):
            """Handle maintenance_alert event from server"""
            try:
                title = data.get("title", "Maintenance")
                message = data.get("message", "System maintenance in progress")
                level = data.get("level", "info")
                start_time = data.get("startTime", time.time())
                end_time = data.get("endTime")
                requires_action = data.get("requiresAction", False)
                
                level_prefix = {
                    "info": "ℹ️",
                    "warning": "⚠️",
                    "critical": "🚨"
                }.get(level, "ℹ️")
                
                logging.warning(f"{level_prefix} {self.ws_room} Maintenance alert: {title} - {message}")
                
                # Emit to Home Assistant frontend for notification
                emit_data = {
                    "type": "maintenance_alert",
                    "title": title,
                    "message": message,
                    "level": level,
                    "start_time": start_time,
                    "end_time": end_time,
                    "requires_action": requires_action,
                    "timestamp": data.get("timestamp", time.time())
                }
                
                await self._safe_emit("maintenance_alert", emit_data, haEvent=True)
                
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Error handling maintenance_alert: {e}")
        
        # V1 Debug: Catch ALL events from server (namespace-specific)
        @self.sio.on('*', namespace=ns)
        async def v1_debug_all_events(event, *args):
            """Debug ALL events received from V1 server"""
            logging.info(f"📨 {self.ws_room} V1 EVENT RECEIVED: {event} -> {args}")
            if 'auth' in event.lower() or 'session' in event.lower() or 'v1' in event.lower() or 'error' in event.lower():
                logging.debug(f"🚨 {self.ws_room} AUTH/ERROR EVENT: {event} -> {args}")  # Make error events visible

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
        """Handle API usage update from server with new consistent structure"""
        try:
            logging.info(f"📊 {self.ws_room} Processing api_usage_update: {data}")
            
            # New consistent API structure: { plan, features, limits, usage, activeGrowPlan, timestamp, ... }
            # API may send "plan" (snake_case) or "planName" (from API response)
            server_plan = data.get("plan") or data.get("plan_name")
            features = data.get("features", {})
            limits = data.get("limits", {})
            usage = data.get("usage", {})
            active_grow_plan = data.get("activeGrowPlan")
            
            # Extract usage fields
            active_connections = usage.get("activeConnections", 0)
            server_active_rooms = usage.get("activeRooms", [])
            rooms_used = usage.get("roomsUsed", 0)
            
            # CRITICAL FIX: Always use current room if we have active connections
            normalized_current_room = str(self.ws_room).lower()
            
            if active_connections > 0:
                # We have an active connection - we ARE a room!
                active_rooms = [normalized_current_room]
                logging.debug(f"📊 {self.ws_room} API update: active connections={active_connections}, using current room")
            else:
                active_rooms = []
            
            # Update local session count
            self.ogb_sessions = active_connections
            
            # CRITICAL FIX: Update subscription_data with FULL structure
            if not self.subscription_data:
                self.subscription_data = {}
            
            # Update plan from api_usage_update
            if server_plan:
                self.subscription_data["plan_name"] = server_plan
                self._plan = server_plan
                logging.info(f"📊 {self.ws_room} Updated plan from api_usage_update: {server_plan}")
            
            # Update features and limits
            if features:
                self.subscription_data["features"] = features
                logging.info(f"📊 {self.ws_room} Updated features: {len(features)}")
            
            if limits:
                self.subscription_data["limits"] = limits
                logging.info(f"📊 {self.ws_room} Updated limits: {len(limits)}")
            
            # Update usage
            if "usage" not in self.subscription_data:
                self.subscription_data["usage"] = {}
            
            # Merge ALL usage fields from server
            usage_fields = [
                "roomsUsed", "activeConnections", "activeRooms", "maxSessions",
                "growPlansUsed", "apiCallsThisMonth", "storageUsedGB",
                "billingPeriodStart", "billingPeriodEnd", "billingInterval",
                "isYearlyPlan", "dataRetention"
            ]
            
            for field in usage_fields:
                value = usage.get(field)
                if value is not None:
                    self.subscription_data["usage"][field] = value
            
            # Override room-specific fields with validated values
            self.subscription_data["usage"]["activeConnections"] = active_connections
            self.subscription_data["usage"]["activeRooms"] = active_rooms
            self.subscription_data["usage"]["roomsUsed"] = len(active_rooms)
            
            # Store active grow plan if present
            if active_grow_plan:
                logging.info(
                    f"🌱 {self.ws_room} Active grow plan: "
                    f"name={active_grow_plan.get('name')}, "
                    f"strain={active_grow_plan.get('strainName')}, "
                    f"week={active_grow_plan.get('elapsedWeeks')}/{active_grow_plan.get('maxWeeks')}"
                )
                self.subscription_data["active_grow_plan"] = active_grow_plan

            # Emit to Premium Integration with FULL structure
            emit_data = {
                "plan": server_plan,
                "features": features,
                "limits": limits,
                "usage": self.subscription_data.get("usage", {}),
                "activeGrowPlan": active_grow_plan,
                "timestamp": data.get("timestamp", time.time()),
                "source": data.get("source", "WebSocket")
            }

            await self._safe_emit("api_usage_update", emit_data, haEvent=True)

            logging.info(
                f"📊 {self.ws_room} Processed api_usage_update: "
                f"plan={server_plan}, rooms={len(active_rooms)}, connections={active_connections}"
            )
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling api_usage_update: {e}")
            import traceback
            logging.error(traceback.format_exc())
            # When frontend reconnects, it gets subscription_data which must have current values
            if self.subscription_data:
                if "usage" not in self.subscription_data:
                    self.subscription_data["usage"] = {}
                self.subscription_data["usage"]["roomsUsed"] = len(active_rooms)
                self.subscription_data["usage"]["growPlansUsed"] = usage.get("growPlansUsed", 0)
                self.subscription_data["usage"]["apiCallsThisMonth"] = usage.get("apiCallsThisMonth", 0)
                self.subscription_data["usage"]["storageUsedGB"] = usage.get("storageUsedGB", 0)
                self.subscription_data["usage"]["activeConnections"] = active_connections
                self.subscription_data["usage"]["activeRooms"] = active_rooms
                logging.debug(f"📊 {self.ws_room} Updated subscription_data.usage with current values: rooms={len(active_rooms)}, connections={active_connections}")
                
                # CRITICAL: Update plan name in subscription_data when it changes
                if server_plan and server_plan != "free":
                    self.subscription_data["plan_name"] = server_plan
                    logging.info(f"📊 {self.ws_room} Updated plan_name in subscription_data: {server_plan}")
            
            # Wrap in 'usage' object for frontend compatibility
            # Frontend expects: { usage: {...}, timestamp: ..., lastEndpoint: ..., lastMethod: ... }
            emit_data = {
                "usage": {
                    "roomsUsed": rooms_used,
                    "growPlansUsed": usage.get("growPlansUsed", 0),
                    "apiCallsThisMonth": usage.get("apiCallsThisMonth", 0),
                    "storageUsedGB": usage.get("storageUsedGB", 0),
                    "activeConnections": active_connections,
                    "activeRooms": active_rooms if isinstance(active_rooms, list) else [],
                    "plan_name": server_plan
                },
                "activeGrowPlan": active_grow_plan,
                "timestamp": data.get("timestamp", time.time()),
                "lastEndpoint": data.get("lastEndpoint"),
                "lastMethod": data.get("lastMethod"),
            }

            # Emit to Home Assistant frontend
            await self._safe_emit("api_usage_update", emit_data, haEvent=True)
            logging.info(f"📊 {self.ws_room} Emitted api_usage_update to HA: plan={server_plan}, rooms={rooms_used}, activeConnections={active_connections}, apiCalls={emit_data['usage']['apiCallsThisMonth']}, storageGB={emit_data['usage']['storageUsedGB']}")
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling api_usage_update: {e}")
            import traceback
            logging.error(traceback.format_exc())

    # =================================================================
    # Subscription Lifecycle Handlers
    # =================================================================

    async def _handle_subscription_expiring(self, data: dict):
        """Handle subscription expiring soon warning with new API structure"""
        try:
            plan_name = data.get("plan_name", "unknown")
            features = data.get("features", {})
            limits = data.get("limits", {})
            expires_in = data.get("expires_in_seconds", 0)
            current_period_end = data.get("current_period_end")
            
            logging.warning(
                f"⚠️ {self.ws_room} Subscription '{plan_name}' expiring in {expires_in}s!"
            )
            
            # Update subscription_data with plan info
            if not self.subscription_data:
                self.subscription_data = {}
            
            self.subscription_data["plan_name"] = plan_name
            self.subscription_data["features"] = features
            self.subscription_data["limits"] = limits
            
            # Emit to HA for Premium Integration to handle
            await self._safe_emit("SubscriptionExpiringSoon", {
                "room": self.ws_room,
                "plan_name": plan_name,
                "features": features,
                "limits": limits,
                "expires_in_seconds": expires_in,
                "current_period_end": current_period_end,
                "timestamp": data.get("timestamp", time.time())
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling subscription_expiring: {e}")

    async def _handle_subscription_expired(self, data: dict):
        """Handle subscription expired event with new API structure"""
        try:
            previous_plan = data.get("previous_plan", "unknown")
            new_plan = data.get("new_plan", "free")
            features = data.get("features", {})
            limits = data.get("limits", {})
            
            logging.error(
                f"🚨 {self.ws_room} Subscription EXPIRED! {previous_plan} -> {new_plan}"
            )
            
            # Update local state
            self.is_premium = False
            
            # Update subscription_data to free plan with FULL structure
            if not self.subscription_data:
                self.subscription_data = {}
            
            self.subscription_data["plan_name"] = new_plan
            self.subscription_data["features"] = features or {}
            self.subscription_data["limits"] = limits or {}
            
            # Emit to HA for Premium Integration to handle
            await self._safe_emit("SubscriptionExpired", {
                "room": self.ws_room,
                "previous_plan": previous_plan,
                "new_plan": new_plan,
                "features": features or {},
                "limits": limits or {},
                "expired_at": data.get("expired_at"),
                "timestamp": data.get("timestamp", time.time())
            }, haEvent=True)
            
            # Also emit SubscriptionChanged for feature manager update
            await self._safe_emit("SubscriptionChanged", {
                "room": self.ws_room,
                "plan_name": new_plan,
                "features": features or {},
                "limits": limits or {},
                "timestamp": data.get("timestamp", time.time())
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling subscription_expired: {e}")

    # =================================================================
    # OGB Token Expiration Handlers
    # =================================================================

    async def _handle_token_expiring_warning(self, data: dict):
        """Handle OGB token expiring warning (7 days before expiration)"""
        try:
            token_id = data.get("token_id", "unknown")
            token_name = data.get("token_name", "OGB Token")
            expires_at = data.get("expires_at")
            days_remaining = data.get("days_remaining", 0)
            hours_remaining = data.get("hours_remaining", 0)
            action_required = data.get("action_required", "")

            logging.warning(
                f"⚠️ {self.ws_room} OGB Token '{token_name}' expiring in {days_remaining} days!"
            )

            # Send notification via notify_manager
            if self.notify_manager:
                await self.notify_manager.warning(
                    message=f"OGB Token '{token_name}' expires in {days_remaining} day(s). {action_required}",
                    title=f"OGB Token Expiring - {self.ws_room}"
                )

            # Emit to HA for Premium Integration to handle
            await self._safe_emit("TokenExpiringWarning", {
                "room": self.ws_room,
                "token_id": token_id,
                "token_name": token_name,
                "expires_at": expires_at,
                "days_remaining": days_remaining,
                "hours_remaining": hours_remaining,
                "action_required": action_required,
                "timestamp": data.get("timestamp", time.time())
            }, haEvent=True)

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling token_expiring_warning: {e}")

    async def _handle_token_expiring_critical(self, data: dict):
        """Handle OGB token expiring critical (3 days before expiration)"""
        try:
            token_id = data.get("token_id", "unknown")
            token_name = data.get("token_name", "OGB Token")
            expires_at = data.get("expires_at")
            days_remaining = data.get("days_remaining", 0)
            hours_remaining = data.get("hours_remaining", 0)
            action_required = data.get("action_required", "")

            logging.error(
                f"🔴 {self.ws_room} OGB Token '{token_name}' EXPIRING CRITICAL in {days_remaining} days ({hours_remaining}h)!"
            )

            # Send notification via notify_manager
            if self.notify_manager:
                await self.notify_manager.critical(
                    message=f"URGENT: OGB Token '{token_name}' expires in {hours_remaining} hours! {action_required}",
                    title=f"OGB Token Expiring CRITICAL - {self.ws_room}"
                )

            # Emit to HA for Premium Integration to handle
            await self._safe_emit("TokenExpiringCritical", {
                "room": self.ws_room,
                "token_id": token_id,
                "token_name": token_name,
                "expires_at": expires_at,
                "days_remaining": days_remaining,
                "hours_remaining": hours_remaining,
                "action_required": action_required,
                "timestamp": data.get("timestamp", time.time())
            }, haEvent=True)

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling token_expiring_critical: {e}")

    async def _handle_token_expired(self, data: dict):
        """Handle OGB token expired event"""
        try:
            token_id = data.get("token_id", "unknown")
            token_name = data.get("token_name", "OGB Token")
            message = data.get("message", "Token has expired")
            grace_period_seconds = data.get("grace_period_seconds", 300)
            action_required = data.get("action_required", "")

            logging.error(
                f"🚨 {self.ws_room} OGB Token '{token_name}' EXPIRED! {message}"
            )

            # Send notification via notify_manager
            if self.notify_manager:
                await self.notify_manager.critical(
                    message=f"OGB Token '{token_name}' has EXPIRED! {message} Reconnect required within {grace_period_seconds}s. {action_required}",
                    title=f"OGB Token EXPIRED - {self.ws_room}"
                )

            # Emit to HA for Premium Integration to handle
            await self._safe_emit("TokenExpired", {
                "room": self.ws_room,
                "token_id": token_id,
                "token_name": token_name,
                "message": message,
                "grace_period_seconds": grace_period_seconds,
                "action_required": action_required,
                "timestamp": data.get("timestamp", time.time())
            }, haEvent=True)

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling token_expired: {e}")

    # =================================================================
    # Control Sync Handlers (from Webapp)
    # =================================================================

    async def _handle_ctrl_change(self, data: dict):
        """Handle control mode changes from webapp"""
        try:
            logging.info(f"🎛️ {self.ws_room} Processing ctrl_change: {data}")
            
            # Emit to HA using the existing event name that OGBPremiumIntegration listens for
            await self._safe_emit("PremUICTRLChange", data, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling ctrl_change: {e}")

    async def _handle_ctrl_values_change(self, data: dict):
        """Handle control values changes from webapp"""
        try:
            logging.info(f"🎛️ {self.ws_room} Processing ctrl_values_change: {data}")
            
            # Emit to HA for data store update
            # Note: plantStage is handled via ctrl_change -> _handle_ctrl_change path only
            await self._safe_emit("WebappControlValuesChange", {
                "room": self.ws_room,
                "controlOptionData": data.get("controlOptionData"),
                "isLightON": data.get("isLightON"),
                "vpd": data.get("vpd"),
                "source": "webapp"
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling ctrl_values_change: {e}")

    async def _handle_ctrl_value_update(self, data: dict):
        """Handle individual control value update from webapp"""
        try:
            key = data.get("key")
            value = data.get("value")
            
            logging.debug(f"🎛️ {self.ws_room} Control value update: {key}={value}")
            
            # Emit to HA for data store update
            # Note: plantStage is handled via ctrl_change -> _handle_ctrl_change path only
            await self._safe_emit("WebappControlValueUpdate", {
                "room": self.ws_room,
                "key": key,
                "value": value,
                "source": "webapp"
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling ctrl_value_update: {e}")

    async def _handle_plant_view_need(self, data: dict):
        """Handle plant view need from webapp"""
        try:
            room_id = data.get("room_id")
            request_socket_id = data.get("request_socket_id")
            device_name = data.get("device_name")
            force_new = data.get("force_new", False)
            logging.info(f"📷 {self.ws_room} plant_view_need received, room_id: {room_id}")
            
            # Emit to HA for camera to handle - pass room_id in event data
            await self._safe_emit("NeedViewPlant", {
                "room_id": room_id,
                "room": self.ws_room,
                "request_socket_id": request_socket_id,
                "device_name": device_name,
                "force_new": bool(force_new),
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling plant_view_need: {e}")

    async def _handle_plant_stage_change(self, data: dict):
        """Handle plant stage change from webapp"""
        try:
            plant_stage = data.get("plantStage")
            
            logging.info(f"🌱 {self.ws_room} Plant stage change: {plant_stage}")
            
            # Emit to HA for mode manager to handle
            await self._safe_emit("WebappPlantStageChange", {
                "room": self.ws_room,
                "plantStage": plant_stage,
                "source": "webapp"
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling plant_stage_change: {e}")

    async def _handle_prem_actions(self, data: dict):
        """Handle premium actions from webapp"""
        try:
            actions = data.get("actions", [])
            
            logging.info(f"⚡ {self.ws_room} Premium actions received: {len(actions)} actions")
            
            # Emit to HA for action manager to execute
            await self._safe_emit("WebappPremiumActions", {
                "room": self.ws_room,
                "actions": actions,
                "source": data.get("source", "webapp"),
                "timestamp": data.get("timestamp")
            }, haEvent=True)
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error handling prem_actions: {e}")

    async def _handle_controller_completed(self, data: dict, source_event: str):
        """Route API controller completion payloads to HA premium action pipeline."""
        try:
            if not isinstance(data, dict):
                return

            status = data.get("status")
            if status != "success":
                reason = data.get("message") or "no message"
                logging.debug(
                    f"📦 {self.ws_room} {source_event} status={status} - no controller actions to apply (reason: {reason})"
                )
                return

            action_data = data.get("actionData") or {}
            if not isinstance(action_data, dict):
                logging.warning(f"⚠️ {self.ws_room} {source_event} invalid actionData type: {type(action_data)}")
                return

            control_commands = action_data.get("controlCommands") or []
            if not isinstance(control_commands, list):
                logging.warning(f"⚠️ {self.ws_room} {source_event} controlCommands is not a list")
                return

            controller_type = (
                data.get("controllerType")
                or action_data.get("controllerType")
                or "PID"
            )
            if isinstance(controller_type, str):
                controller_type = controller_type.strip().upper()
            else:
                controller_type = "PID"

            payload = {
                "controllerType": controller_type,
                "actionData": action_data,
                "room_id": data.get("room_id", self.room_id),
                "room_name": data.get("room_name", self.ws_room),
                "event_id": data.get("event_id"),
                "source": source_event,
                "timestamp": data.get("timestamp", time.time()),
            }

            event_id = payload.get("event_id")
            dedupe_key = f"{event_id}:{controller_type}:{status}" if event_id else None
            now = time.time()
            # prune old dedupe entries (>120s)
            self._processed_controller_events = {
                k: ts for k, ts in self._processed_controller_events.items() if now - ts < 120
            }
            if dedupe_key and dedupe_key in self._processed_controller_events:
                logging.debug(
                    f"🔁 {self.ws_room} Duplicate controller completion ignored: {dedupe_key}"
                )
                return
            if dedupe_key:
                self._processed_controller_events[dedupe_key] = now

            logging.info(
                f"⚡ {self.ws_room} Controller result routed: controller={controller_type}, commands={len(control_commands)}, source={source_event}"
            )
            await self._safe_emit("PremiumCheck", payload)

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Error routing controller completion ({source_event}): {e}")

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
        
        # Stop health monitor
        await self._stop_health_monitor()

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

        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
        self._recovery_task = None

        # Disconnect socket
        if hasattr(self, "sio") and self.ws_connected:
            try:
                await self.sio.disconnect()
            except Exception as e:
                logging.warning(f"Error during disconnect: {e}")

        # Normalize session counts immediately so frontend does not keep stale values
        updated_sessions = self.ogb_sessions
        if isinstance(updated_sessions, dict):
            current_active = updated_sessions.get("active", 0) or 0
            updated_sessions = {
                **updated_sessions,
                "active": max(0, current_active - 1),
            }
        else:
            updated_sessions = max(0, (updated_sessions or 0) - 1)

        self.ogb_sessions = updated_sessions

        if not self.subscription_data:
            self.subscription_data = {}
        if "usage" not in self.subscription_data:
            self.subscription_data["usage"] = {}

        usage = self.subscription_data["usage"]
        active_connections = updated_sessions.get("active", 0) if isinstance(updated_sessions, dict) else updated_sessions
        usage["activeConnections"] = active_connections

        active_rooms = usage.get("activeRooms") or []
        if isinstance(active_rooms, list):
            usage["activeRooms"] = [room for room in active_rooms if str(room).lower() != str(self.ws_room).lower()]
            usage["roomsUsed"] = len(usage["activeRooms"])

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
                "active_rooms": usage.get("activeRooms", []),
                "active_connections": active_connections,
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

            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()
                try:
                    await self._reconnect_task
                except asyncio.CancelledError:
                    pass

            if self._recovery_task and not self._recovery_task.done():
                self._recovery_task.cancel()
                try:
                    await self._recovery_task
                except asyncio.CancelledError:
                    pass
            self._recovery_task = None

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
        """Send encrypted message (backward-compatible wrapper).

        Uses V1 encrypted transport by default.
        """
        try:
            logging.debug(
                f"⚠️ {self.ws_room} send_encrypted_message() is deprecated; "
                f"using V1 transport for type={message_type}"
            )
            return await self.send_v1_encrypted_message(message_type, data)

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

        if self._connection_closing or not self._should_reconnect:
            logging.debug(
                f"⏭️ {self.ws_room} Ignoring connection_lost while closing/reconnect disabled ({reason})"
            )
            return

        # Mark connection as closed
        self.ws_connected = False
        self.authenticated = False

        # Clear encryption state
        self._aes_gcm = None
        self._session_key = None

        # Clear user state
        self._user_id = None
        self._session_id = None

        # Schedule reconnection attempt after a delay (deduplicated)
        if self._reconnect_task and not self._reconnect_task.done():
            logging.debug(f"⏭️ {self.ws_room} Reconnect scheduler already running ({reason})")
            return

        self._reconnect_task = asyncio.create_task(self._schedule_reconnection(reason))

    async def _schedule_reconnection(self, reason: str):
        """Schedule reconnection with exponential backoff"""
        import random

        base_delay = 5  # Start with 5 seconds
        max_delay = 300  # Max 5 minutes
        max_attempts = 10

        for attempt in range(max_attempts):
            if self._connection_closing or not self._should_reconnect:
                logging.info(f"⏭️ {self.ws_room} Reconnection scheduler stopped ({reason})")
                return

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

    def _schedule_recovery_task(self, action: str, reason: str) -> None:
        """Schedule one recovery task at a time to avoid reconnect storms."""
        if self._connection_closing or not self._should_reconnect:
            logging.debug(
                f"⏭️ {self.ws_room} Recovery skipped while closing/reconnect disabled ({action}:{reason})"
            )
            return

        if self._recovery_task and not self._recovery_task.done():
            logging.debug(
                f"⏭️ {self.ws_room} Recovery task already running, skipping duplicate ({action}:{reason})"
            )
            return

        if action == "relogin":
            coro = self._trigger_relogin(reason)
        else:
            coro = self._trigger_reconnect_with_lock(reason)

        self._recovery_task = asyncio.create_task(coro)
        self._recovery_task.add_done_callback(self._on_recovery_task_done)

    def _on_recovery_task_done(self, task: asyncio.Task) -> None:
        """Clear recovery task reference and surface unexpected failures."""
        try:
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                logging.error(f"❌ {self.ws_room} Recovery task failed: {exc}")
        except Exception as err:
            logging.error(f"❌ {self.ws_room} Recovery task completion error: {err}")
        finally:
            if self._recovery_task is task:
                self._recovery_task = None

    async def prem_event(self, message_type: str, data: dict) -> bool:
        """Send encrypted message via WebSocket - all logged-in users (free + premium)

        Uses V1 encrypted messaging for enhanced security and features.
        """
        try:
            # Diagnostic: Log prem_event call details with full state
            logging.debug(
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
                # CRITICAL: Use _stored_ogb_token (API key), NOT _access_token (JWT)
                ogb_token = getattr(self, '_stored_ogb_token', None)
                if self.ws_reconnect_attempts >= 3 and self._user_id and ogb_token:
                    logging.warning(f"🔄 {self.ws_room} Attempting auto-relogin with stored credentials after {self.ws_reconnect_attempts} failed reconnection attempts")
                    
                    try:
                        # Perform fresh login with stored credentials
                        # This will get a new session from the server
                        login_success = await self._perform_login(
                            email=getattr(self, '_stored_email', None) or "auto_relogin@restore",
                            OGBToken=ogb_token,
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

    async def _trigger_relogin(self, reason: str):
        """
        Trigger a full re-login when session is invalid (e.g., after API restart).
        
        Unlike reconnect, this performs a fresh login to get a new session ID.
        This is needed when:
        - API was restarted and old sessions are invalidated
        - Session expired on server side
        - v1:error indicates session not found
        
        Args:
            reason: Why re-login is being triggered (for logging)
        """
        # Use lock to prevent multiple simultaneous re-login attempts
        async with self._reconnection_lock:
            if self._reconnection_in_progress:
                logging.debug(f"⏭️ {self.ws_room} Re-login skipped - reconnection already in progress ({reason})")
                return
            
            if not self._should_reconnect:
                logging.debug(f"⏭️ {self.ws_room} Re-login disabled ({reason})")
                return
            
            if self._connection_closing:
                logging.debug(f"⏭️ {self.ws_room} Connection closing, skipping re-login ({reason})")
                return
            
            self._reconnection_in_progress = True
            logging.warning(f"🔐 {self.ws_room} Starting re-login sequence (reason: {reason})")
        
        try:
            # Check if we have stored credentials for re-login
            # CRITICAL: We need _stored_ogb_token (API key), NOT _access_token (JWT)
            # The _access_token is invalid after API restart, but _stored_ogb_token is permanent
            logging.warning(f"🔐 {self.ws_room} Re-login credentials check: email={bool(self._stored_email)}, ogb_token={bool(self._stored_ogb_token)}")
            
            # Clear old session data to force fresh session
            logging.warning(f"🔑 {self.ws_room} Clearing stale session data: session_id={self._session_id}, has_key={bool(self._session_key)}")
            self._session_id = None
            self._session_key = None
            self._aes_gcm = None
            self.authenticated = False
            self._auth_success = None
            self._auth_confirmed.clear()
            
            # Disconnect existing socket cleanly
            if hasattr(self, "sio") and self.sio.connected:
                try:
                    await self.sio.disconnect()
                except Exception:
                    pass
            
            # Brief delay before re-login
            await asyncio.sleep(2)
            
            # Perform fresh login with stored credentials
            # CRITICAL: Use _stored_ogb_token (API key), NOT _access_token (JWT)
            email_to_use = getattr(self, '_stored_email', None)
            ogb_token_to_use = getattr(self, '_stored_ogb_token', None)
            
            # If not stored locally, try getting from credential provider (OGBPremiumIntegration)
            if (not email_to_use or not ogb_token_to_use) and self._credential_provider:
                try:
                    creds = self._credential_provider()
                    if creds:
                        email_to_use = email_to_use or creds.get("email")
                        ogb_token_to_use = ogb_token_to_use or creds.get("token")
                        logging.warning(f"🔐 {self.ws_room} Got credentials from provider: email={bool(email_to_use)}, token={bool(ogb_token_to_use)}")
                except Exception as e:
                    logging.error(f"❌ {self.ws_room} Error getting credentials from provider: {e}")
            
            if not email_to_use or not ogb_token_to_use:
                logging.error(f"❌ {self.ws_room} Cannot re-login - missing credentials: email={bool(email_to_use)}, token={bool(ogb_token_to_use)}")
                return
            
            logging.warning(f"🔐 {self.ws_room} Calling _perform_login with email={email_to_use[:3] if email_to_use else 'None'}***, room_id={self.room_id}")
            login_success = await self._perform_login(
                email=email_to_use,
                OGBToken=ogb_token_to_use,
                room_id=self.room_id,
                room_name=self.ws_room,
                event_id=self.create_event_id()
            )
            logging.warning(f"🔐 {self.ws_room} _perform_login returned: {login_success}")
            
            if login_success:
                logging.warning(f"✅ {self.ws_room} Re-login successful! New session obtained.")
                
                # Connect WebSocket with new session
                connect_success = await self._connect_websocket()
                if connect_success:
                    logging.warning(f"✅ {self.ws_room} Connection successful after re-login")
                    self.ws_reconnect_attempts = 0
                    self._reconnect_delay = 5
                    
                    # Restart keep-alive and health monitor
                    await self._start_keepalive()
                    await self._start_health_monitor()
                else:
                    logging.error(f"❌ {self.ws_room} Connection failed even after successful re-login")
            else:
                logging.error(f"❌ {self.ws_room} Re-login failed - credentials may be invalid or API unreachable")
                
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Re-login error ({reason}): {e}")
        finally:
            self._reconnection_in_progress = False
            self._session_error_relogin_triggered = False  # Reset flag after re-login attempt

    async def _trigger_reconnect_with_lock(self, reason: str):
        """
        Trigger reconnection with proper locking to prevent duplicate attempts.
        
        This is the main entry point for automatic reconnection from:
        - Disconnect events
        - Keep-alive failures
        - Health monitor detection
        
        Args:
            reason: Why reconnection is being triggered (for logging)
        """
        # Use lock to prevent multiple simultaneous reconnection attempts
        async with self._reconnection_lock:
            if self._reconnection_in_progress:
                logging.debug(f"⏭️ {self.ws_room} Reconnection already in progress, skipping ({reason})")
                return
            
            if not self._should_reconnect:
                logging.debug(f"⏭️ {self.ws_room} Reconnection disabled, skipping ({reason})")
                return
            
            if self._connection_closing:
                logging.debug(f"⏭️ {self.ws_room} Connection closing, skipping reconnection ({reason})")
                return
            
            self._reconnection_in_progress = True
            logging.info(f"🔄 {self.ws_room} Starting reconnection sequence (reason: {reason})")
        
        try:
            success = await self._attempt_reconnect()
            
            if success:
                logging.info(f"✅ {self.ws_room} Reconnection successful after {reason}")
                # Restart keep-alive after successful reconnection
                await self._start_keepalive()
                # Restart health monitor
                await self._start_health_monitor()
            else:
                logging.warning(f"⚠️ {self.ws_room} Reconnection failed after {reason}")
                
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Reconnection error ({reason}): {e}")
        finally:
            self._reconnection_in_progress = False

    # =================================================================
    # Connection Health Monitor (5-minute fallback)
    # =================================================================

    async def _start_health_monitor(self):
        """Start the 5-minute connection health monitor as a fallback safety net."""
        # Stop any existing monitor first
        await self._stop_health_monitor()
        
        if self._connection_closing:
            return
        
        self._health_monitor_task = asyncio.create_task(self._health_monitor_loop())
        logging.info(f"🏥 {self.ws_room} Health monitor started (5-minute interval)")

    async def _stop_health_monitor(self):
        """Stop the health monitor."""
        if self._health_monitor_task and not self._health_monitor_task.done():
            self._health_monitor_task.cancel()
            try:
                await self._health_monitor_task
            except asyncio.CancelledError:
                pass
        self._health_monitor_task = None
        logging.debug(f"🏥 {self.ws_room} Health monitor stopped")

    async def _health_monitor_loop(self):
        """
        5-minute interval health monitor as a fallback safety net.
        
        This runs independently from the keep-alive system and ensures
        reconnection even if disconnect events are missed or keep-alive fails silently.
        
        Checks every 5 minutes:
        1. If WebSocket should be connected but isn't
        2. If authenticated but no pong received for extended period
        3. Triggers reconnection if issues detected
        """
        HEALTH_CHECK_INTERVAL = 300  # 5 minutes
        MAX_PONG_AGE = 180  # 3 minutes - if no pong for this long, connection is dead
        
        try:
            while not self._connection_closing:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                
                # Skip if connection is intentionally closing
                if self._connection_closing:
                    break
                
                # Skip if reconnection is already in progress
                if self._reconnection_in_progress:
                    logging.debug(f"🏥 {self.ws_room} Health check: reconnection already in progress")
                    continue
                
                # Skip if reconnection is disabled
                if not self._should_reconnect:
                    logging.debug(f"🏥 {self.ws_room} Health check: reconnection disabled")
                    continue
                
                # Helper to trigger reconnect or relogin based on available credentials
                async def trigger_recovery(reason: str):
                    if self._access_token and self._stored_email:
                        logging.info(f"🔐 {self.ws_room} Health monitor: triggering re-login ({reason})")
                        await self._trigger_relogin(f"health_monitor_{reason}")
                    else:
                        logging.info(f"🔄 {self.ws_room} Health monitor: triggering reconnect ({reason})")
                        await self._trigger_reconnect_with_lock(f"health_monitor_{reason}")
                
                # Check 1: Should be connected but isn't
                if self.is_logged_in and not self.ws_connected:
                    logging.warning(f"🏥 {self.ws_room} Health check FAILED: logged in but WebSocket not connected")
                    await trigger_recovery("disconnected")
                    continue
                
                # Check 2: Connected but not authenticated (stuck state)
                if self.ws_connected and not self.authenticated and self.is_logged_in:
                    logging.warning(f"🏥 {self.ws_room} Health check FAILED: connected but not authenticated")
                    await trigger_recovery("auth_stuck")
                    continue
                
                # Check 3: No pong received for too long (connection is dead but not detected)
                if self.authenticated and self._last_pong_time:
                    pong_age = time.time() - self._last_pong_time
                    if pong_age > MAX_PONG_AGE:
                        logging.warning(
                            f"🏥 {self.ws_room} Health check FAILED: no pong for {pong_age:.0f}s "
                            f"(max: {MAX_PONG_AGE}s)"
                        )
                        await trigger_recovery("stale_pong")
                        continue

                # Fallback safety: refresh subscription plan from cached API endpoint.
                # This prevents long-lived stale plan state when real-time events are missed.
                await self._refresh_plan_from_cache_fallback()
                
                # All checks passed
                logging.debug(
                    f"🏥 {self.ws_room} Health check OK: "
                    f"ws_connected={self.ws_connected}, "
                    f"authenticated={self.authenticated}, "
                    f"last_pong_age={time.time() - self._last_pong_time:.0f}s"
                )
                
        except asyncio.CancelledError:
            logging.debug(f"🏥 {self.ws_room} Health monitor cancelled")
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Health monitor error: {e}")

    async def _refresh_plan_from_cache_fallback(self):
        """Refresh plan every 10 minutes via cached API endpoint (fallback only)."""
        if not self.authenticated or not self._access_token:
            return

        now = time.time()
        if now - self._last_plan_fallback_check < 600:
            return

        self._last_plan_fallback_check = now

        try:
            url = self.subscription_current_url
            headers = dict(self.headers)
            headers["Authorization"] = f"Bearer {self._access_token}"

            timeout_config = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logging.warning(
                            f"⚠️ {self.ws_room} Plan fallback check failed HTTP {response.status}"
                        )
                        return

                    data = await response.json()
                    server_plan = data.get("plan")
                    if not server_plan:
                        return

                    current_plan = (self.subscription_data or {}).get("plan_name")
                    if current_plan != server_plan:
                        self.subscription_data["plan_name"] = server_plan
                        self._plan = server_plan
                        logging.info(
                            f"🔁 {self.ws_room} Plan fallback sync: {current_plan} -> {server_plan} "
                            f"(source={data.get('source', 'unknown')}, cached={data.get('cached', False)})"
                        )

                        await self._safe_emit(
                            "api_usage_update",
                            {
                                "plan": server_plan,
                                "features": data.get("features", {}),
                                "limits": data.get("limits", {}),
                                "usage": data.get("usage", {}),
                                "source": "fallback_poll",
                                "timestamp": data.get("timestamp"),
                            },
                            haEvent=True,
                        )
                    else:
                        logging.debug(
                            f"📦 {self.ws_room} Plan fallback check OK: {server_plan} "
                            f"(source={data.get('source', 'unknown')}, cached={data.get('cached', False)})"
                        )
        except Exception as e:
            logging.warning(f"⚠️ {self.ws_room} Plan fallback check error: {e}")

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

    def _derive_api_root(self, url: str) -> str:
        """Normalize incoming URL to API root (scheme://host[:port])."""
        parsed = urlparse(url.strip())

        # Convert websocket schemes to HTTP(S) for REST endpoints
        if parsed.scheme == "ws":
            scheme = "http"
        elif parsed.scheme == "wss":
            scheme = "https"
        else:
            scheme = parsed.scheme

        netloc = parsed.netloc
        if not netloc:
            raise ValueError("Invalid URL: missing host")

        api_root = f"{scheme}://{netloc}"

        # Helpful warning if caller passed path-based websocket URL (e.g. /ws)
        if parsed.path and parsed.path not in ("", "/"):
            logging.debug(
                f"🔧 {self.ws_room} Normalized base URL from '{url}' to API root '{api_root}'"
            )

        return api_root

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
        """Get current session status from local runtime state."""
        try:
            if not self.authenticated:
                return {
                    "active_sessions": 0,
                    "max_sessions": self.ogb_max_sessions or 1,
                    "current_plan": self.subscription_data.get("plan_name", "free") if self.subscription_data else "free",
                    "active_rooms": [],
                    "usage_percent": 0,
                }

            ogb_sessions_data = self.ogb_sessions
            if isinstance(ogb_sessions_data, dict):
                active_sessions = ogb_sessions_data.get("active", 0) or 0
                total_sessions = ogb_sessions_data.get("total", active_sessions) or active_sessions
            else:
                active_sessions = ogb_sessions_data or 0
                total_sessions = active_sessions

            max_sessions = self.ogb_max_sessions or 1
            active_rooms = []

            usage = self.subscription_data.get("usage", {}) if isinstance(self.subscription_data, dict) else {}
            if isinstance(usage.get("activeRooms"), list):
                active_rooms = usage.get("activeRooms", [])

            if not active_rooms and active_sessions > 0 and self.ws_room:
                active_rooms = [self.ws_room]

            return {
                "active_sessions": active_sessions,
                "max_sessions": max_sessions,
                "current_plan": self.subscription_data.get("plan_name", "free") if self.subscription_data else "free",
                "active_rooms": active_rooms,
                "usage_percent": (active_sessions / max_sessions * 100) if max_sessions else 0,
                "total_sessions": total_sessions,
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

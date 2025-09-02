import socketio
import json
import logging
import hashlib
import time
import base64
import secrets
import asyncio
import aiohttp
from typing import Dict, Any, Optional, Callable
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from urllib.parse import urlparse
from datetime import datetime, timezone,timedelta
import uuid

class OGBWebSocketConManager:
    def __init__(self, base_url: str, eventManager={},ws_room="",room_id="", timeout: float = 10.0):
        self.base_url = f"{self._validate_url(base_url)}/ws"
        self.api_url = base_url.replace('ws://', 'http://').replace('wss://', 'https://')
        self.login_url = f"{self.api_url}/api/auth/login"
        self.timeout = timeout
        self.ogbevents = eventManager
        self.ws_room = ws_room
        self.room_id = room_id   
        self.client_id = f"ogb-client-{self.ws_room}-{secrets.token_hex(8)}"
        
        # User data
        self.user_data = {}
        self.subscription_data = {}

        self.active_grow_plan = None

        self.ogb_sessions = 0
        self.ogb_max_sessions = 0

        # Security variables
        self._session_key = None
        self._session_id = None
        self._user_id = None
        self._access_token = None
        self.token_expires_at = None
        self._refresh_token = None
        
        
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
        self._connection_lock = asyncio.Lock()
        self.max_reconnect_attempts = 15
        self.reconnect_attempts = 0
        self.ws_reconnect_attempts = 0
        
        # AES-GCM for encryption
        self._aes_gcm = None
        
        # UNIFIED: Single keep-alive system (replaces separate ping/pong and health monitoring)
        self._last_pong_time = time.time()
        self._keepalive_task = None
        self._keepalive_interval = 30
        self._pong_timeout = 10
                      
        # Session rotation state
        self._rotation_in_progress = False
        self._rotation_task = None
     
        # Message handlers
        self.message_handlers: Dict[str, Callable] = {}

        # Setup socket.io ASYNC client
        self.sio = socketio.AsyncClient(
            reconnection=False,  # Handle reconnection ourselves
            logger=False,
            engineio_logger=False,
            ssl_verify=True
        )


        self._setup_event_listeners()
        self._setup_event_handlers()
        
    # =================================================================
    # Connection
    # =================================================================

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self.ogbevents.on("ogb_client_disconnect",self.room_disconnect)
        
    async def login_and_connect(
        self,
        email: str,
        password: str,
        room_id: str,
        room_name: str,
        event_id: str = None,
        auth_callback: Optional[Callable] = None
    ) -> bool:
        """Login und sichere Verbindung in einem Schritt"""
        try:
            async with self._connection_lock:
                if not email or not password:
                    await self._send_auth_response(event_id, "error", "Email and password required")
                    return False

                # Step 1: Login
                if not await self._perform_login(email, password, room_id, room_name, event_id):
                    if auth_callback:
                        await auth_callback(event_id, "error", "Login failed")
                    return False

                # Step 2: Check premium
                if not self.is_premium:
                    logging.warning(f"⚠️ {self.ws_room} User is not premium")
                    if auth_callback:
                        await auth_callback(event_id, "error", "Premium subscription required")
                    return False

                # Step 3: Connect WebSocket
                if not await self._connect_websocket():
                    if auth_callback:
                        await auth_callback(event_id, "error", "WebSocket connection failed")
                    return False

                # Step 4: Start keep-alive
                await self._start_keepalive()

                # Success
                if auth_callback:
                    await auth_callback(event_id, "success", "Login and connection successful")

                logging.warning(f"✅ {self.ws_room} Login and connection successful")
                return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Login and connect error: {e}")
            if auth_callback:
                await auth_callback(event_id, "error", f" {self.ws_room} Connection error: {str(e)}")
            return False

    async def _perform_login(self, email: str, password: str, room_id: str, room_name: str, event_id: str) -> bool:
        """Perform login and send user-facing error messages if something goes wrong."""
        try:
            login_data = {
                "email": email,
                "password": password,
                "room_id": room_id,
                "room_name": room_name,
                "event_id": event_id,
                "client_id": self.client_id,
            }

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "OGB-Python-Client/1.0",
                "Accept": "application/json",
                "origin": "https://opengrowbox.net",
                "ogb-client": "ogb-ws-ha-connector 1.0",
                "ogb-client-id": self.client_id,
            }

            logging.warning(f"🔄 {self.ws_room} Attempting login for: {email}")

            timeout_config = aiohttp.ClientTimeout(total=15)

            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(
                    self.login_url,
                    json=login_data,
                    headers=headers
                ) as response:

                    response_text = await response.text()
                    logging.debug(f"📥 {self.ws_room} Login response: {response.status}")

                    if response.status != 200:
                        logging.error(f"❌ {self.ws_room} Login HTTP error: {response.status}")
                        await self._send_auth_response(event_id, "error", f"Login failed (HTTP {response.status})")
                        return False

                    try:
                        result = json.loads(response_text)
                    except json.JSONDecodeError as e:
                        logging.error(f"❌ {self.ws_room} Invalid JSON response: {e}")
                        await self._send_auth_response(event_id, "error", "Server returned invalid response")
                        return False

                    if result.get("status") != "success":
                        logging.error(f"❌ {self.ws_room} Login failed: {result.get('message', 'Unknown error')}")
                        await self._send_auth_response(event_id, "error", result.get('message', 'Login failed'))
                        return False

                    # Store login data
                    self._user_id = result.get("user_id")
                    self._session_id = result.get("session_id")
                    self._access_token = result.get("access_token")
                    self.token_expires_at = result.get("token_expires_at")
                    self._refresh_token = result.get("refresh_token")
                    
                    self.is_premium = result.get("is_premium", False)
                    self.subscription_data = result.get("subscription_data", {})

                    self.ogb_max_sessions = result.get("obg_max_sessions")
                    self.ogb_sessions = result.get("ogb_sessions")
                    self.is_logged_in = True
                    
                    # Decode session key
                    session_key_b64 = result.get("session_key")
                    if not session_key_b64:
                        logging.error(f"❌ {self.ws_room} No session key received")
                        await self._send_auth_response(event_id, "error", "No session key received from server")
                        return False

                    try:
                        self._session_key = self._safe_b64_decode(session_key_b64)
                        if len(self._session_key) != 32:
                            logging.error(f"❌ {self.ws_room} Invalid session key length: {len(self._session_key)}")
                            await self._send_auth_response(event_id, "error", "Invalid session key length")
                            return False

                        self._aes_gcm = AESGCM(self._session_key)
                        logging.warning(f"🔐 {self.ws_room} AES-GCM cipher initialized successfully")

                    except Exception as e:
                        logging.error(f"❌ {self.ws_room} Session key decode error: {e}")
                        await self._send_auth_response(event_id, "error", "Session key decoding failed")
                        return False

                    if not all([self._user_id, self._session_id, self._session_key]):
                        logging.error(f"❌ {self.ws_room} Missing required login data")
                        await self._send_auth_response(event_id, "error", "Missing required login data")
                        return False

                    
                    await self._send_auth_response(event_id, "success", "LoginSuccess", {
                        "currentPlan": self.subscription_data.get("plan_name"),
                        "is_premium": self.is_premium,
                        "subscription_data": self.subscription_data,
                        "ogb_sessions": self.ogb_sessions,
                        "ogb_max_sessions": self.ogb_max_sessions,
                    })

                    await self.ogbevents.emit(
                        "LogForClient",
                        f"Successfully logged in. Welcome to OGB Premium!",
                        haEvent=True
                    )

                    logging.warning(f"✅ {self.ws_room} Login successful - User: {self._user_id}")
                    return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Login error: {e}")
            await self._send_auth_response(event_id, "error", "Unexpected server error during login")
            return False
     
    async def _connect_websocket(self) -> bool:
        """Verbinde WebSocket mit Session-Authentifizierung"""
        try:
            if not self.is_logged_in and not (self._user_id and self._access_token):
                logging.error(f"❌ {self.ws_room} Must have valid auth data to connect")
                return False

            if self.sio.connected:
                logging.warning(f"ℹ️ {self.ws_room} WebSocket already connected")
                return True

            # Ensure we have session data
            if not self._session_id or not self._session_key:
                logging.warning(f"⚠️ {self.ws_room} No session data, requesting new session key")
                session_data = await self._request_session_key()
                if not session_data:
                    logging.error(f"❌ {self.ws_room} Failed to get session key for connection")
                    return False

            # Prepare headers
            auth_headers = {
                "ogb-user-id": str(self._user_id),
                "ogb-access-token": str(self._access_token),
                "ogb-room-id": str(self.room_id),
                "ogb-room-name": str(self.ws_room),
                "ogb-session-id": str(self._session_id),
                "ogb-client": "ogb-ws-ha-connector 1.0",
                "ogb-client-id": self.client_id,
                "origin": "https://opengrowbox.net",
                "user-agent": "OGB-Python-Client/1.0"
            }

            # Check for missing critical headers
            missing_headers = [k for k, v in auth_headers.items() if not v or str(v).strip() == '' or v == 'None']
            if missing_headers:
                logging.error(f"❌ {self.ws_room} Missing critical headers: {missing_headers}")
                return False

            logging.warning(f"🔗 {self.ws_room} Connecting to WebSocket: {self.base_url}")

            # FIXED: Transport-Reihenfolge anpassen je nach Server-Typ
            # Für Development-Server: websocket first
            # Für Production-Server: polling first
            transports = ["websocket", "polling"]  # Production-kompatibel
                           
            logging.warning(f"🔗 {self.ws_room} Using transports: {transports}")

            # Connect with timeout
            await asyncio.wait_for(
                self.sio.connect(
                    self.base_url,
                    transports=transports,  # Dynamische Transport-Auswahl
                    headers=auth_headers,
                    wait_timeout=self.timeout,
                    socketio_path='/ws'
                ),
                timeout=self.timeout * 2  # Längeres Timeout
            )

            # Wait for authentication
            auth_timeout = 25
            auth_wait = 0
            while not self.authenticated and auth_wait < auth_timeout:
                if not self.sio.connected:
                    logging.error(f"❌ {self.ws_room} WebSocket disconnected before authentication")
                    return False
                await asyncio.sleep(1)
                auth_wait += 1

            if not self.authenticated:
                logging.error(f"❌ {self.ws_room} WebSocket authentication timeout")
                if self.sio.connected:
                    await self.sio.disconnect()
                return False

            self.ws_connected = True
            self.authenticated = True
            self.ws_reconnect_attempts = 0
            self._reconnection_in_progress = False
            self._reconnect_delay = 5  
            self._should_reconnect = True
            self.ogb_sessions += 1
            logging.warning(f"✅ {self.ws_room} WebSocket connection established")
            
            await self._send_auth_response(self.create_event_id(), "success", "Connect Success", {
                "currentPlan": self.subscription_data.get("plan_name"),
                "is_premium": self.is_premium,
                "subscription_data": self.subscription_data,
                "ogb_sessions": self.ogb_sessions,
                "ogb_max_sessions": self.ogb_max_sessions,
            })
            
            await self._start_keepalive()
            return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} WebSocket connection error: {e}")
            # Zusätzliches Debugging
            if hasattr(e, '__dict__'):
                logging.error(f"❌ {self.ws_room} Error details: {e.__dict__}")
            return False
        
    async def _request_session_key(self, event_id: str = None, room_id: str = None):
        """Request new session key from server"""
        try:
            if not self._user_id or not self._access_token:
                logging.error(f"❌ {self.ws_room} Cannot request session - missing auth data")
                return None

            url = f"{self.api_url}/api/auth/create-session-for-device"
            
            request_data = {
                "user_id": self._user_id,
                "access_token": self._access_token,
                "client_id": self.client_id,
                "room_id": self.room_id,
                "room_name": self.ws_room,
            }

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "OGB-Python-Client/1.0",
                "Accept": "application/json",
                "origin": "https://opengrowbox.net",
                "ogb-client": "ogb-ws-ha-connector 1.0",
                "ogb-client-id": self.client_id,
            }

            logging.warning(f"🔑 {self.ws_room} Requesting new session key")

            timeout_config = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(url, json=request_data, headers=headers) as response:
                    
                    if response.status != 200:
                        logging.error(f"❌ {self.ws_room} Session request failed: HTTP {response.status}")
                        return None

                    try:
                        result = json.loads(await response.text())
                    except json.JSONDecodeError as e:
                        logging.error(f"❌ {self.ws_room} Invalid JSON in session response: {e}")
                        return None

                    if result.get("status") != "success":
                        logging.error(f"❌ {self.ws_room} Session request failed: {result.get('message', 'Unknown error')}")
                        return None

                    session_data = {
                        "session_id": result.get("session_id"),
                        "session_key": result.get("session_key"),
                        "room_id": result.get("room_id"),
                        "client_id": result.get("client_id"),
                        "plan": result.get("plan"),
                        "timestamp": result.get("timestamp")
                    }

                    # Update local session data
                    self._session_id = session_data["session_id"]

                    # Decode and store session key
                    session_key_b64 = session_data["session_key"]
                    try:
                        self._session_key = self._safe_b64_decode(session_key_b64)
                        if len(self._session_key) == 32:
                            self._aes_gcm = AESGCM(self._session_key)
                            logging.debug(f"✅ {self.ws_room} New session key established: {self._session_id}")
                        else:
                            logging.error(f"❌ {self.ws_room} Invalid session key length received")
                            return None
                    except Exception as e:
                        logging.error(f"❌ {self.ws_room} Session key processing error: {e}")
                        return None

                    return session_data

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Session key request error: {e}")
            return None
        
    async def establish_session_from_auth_data(self, auth_data: dict, event_id: str = None) -> bool:
        """Establish session from authenticated data (for other rooms)"""
        try:
            async with self._connection_lock:
                logging.warning(f"🔐 {self.ws_room} Establishing session from auth data  {auth_data}")
                
                # Extract data
                self._user_id = auth_data.get("user_id")
                self._access_token = auth_data.get("access_token")
                self.is_premium = auth_data.get("is_premium", False)
                self.is_logged_in = auth_data.get("is_logged_in", False)
                self.subscription_data = auth_data.get("subscription_data", {})
                
                # Get room-specific session key
                session_data = await self._request_session_key(event_id,self.room_id)
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
                if not await self._connect_websocket():
                    logging.error(f"❌ {self.ws_room} WebSocket connection failed")
                    return False
                
                # Start keep-alive
                await self._start_keepalive()
                
                logging.warning(f"✅ {self.ws_room} Session established from auth data")
                return True
                
        except Exception as e:
            logging.error(f"❌ {self.ws_room} Session establishment error: {e}")
            return False

    def _setup_event_handlers(self):     
        @self.sio.event
        async def connect():
            logging.debug(f"🔗 {self.ws_room} WebSocket connected, waiting for authentication...")
            self.ws_connected = True

        @self.sio.event
        async def auth_success(data):
            logging.warning(f"✅ Authentication successful: {data}")
            self.authenticated = True
            self.ogb_max_sessions = data.get("ogb_max_sessions")
            self.ogb_sessions = data.get("ogb_sessions")

        @self.sio.event
        async def disconnect():
            logging.warning(f"💔 {self.ws_room} WebSocket disconnected")
            await self._handle_connection_loss("disconnect_event")

        @self.sio.event
        async def pong(data):
            """Handle pong response"""
            self._last_pong_time = time.time()
            self.ogb_sessions = data.get("ogb_sessions")
            logging.debug(f"🏓 {self.ws_room} Received pong: {data}")

        @self.sio.event
        async def encrypted_message(data):
            """Handle incoming encrypted messages"""
            try:
                decrypted_data = self._decrypt_message(data.get('data', {}))
                message_type = decrypted_data.get('type', 'message')
                
                if message_type in self.message_handlers:
                    if asyncio.iscoroutinefunction(self.message_handlers[message_type]):
                        await self.message_handlers[message_type](decrypted_data)
                    else:
                        self.message_handlers[message_type](decrypted_data)
                else:
                    logging.warning(f"📨 Received message: {message_type}")
                    
            except Exception as e:
                logging.error(f"❌ Error handling encrypted message: {e}")

        ## SES ROTATION
        @self.sio.event
        async def new_session_available(data):
            """Enhanced new session handler"""
            logging.warning(f"New session available for {self.ws_room}: {data}")
            
            try:
                if data.get('session_id') and data.get('session_key'):
                    old_session_id = self._session_id
                    self._session_id = data['session_id']
                    session_key_b64 = data['session_key']
                    
                    self._session_key = self._safe_b64_decode(session_key_b64)
                    if len(self._session_key) == 32:
                        self._aes_gcm = AESGCM(self._session_key)
                        logging.warning(f"Session key updated from distribution for {self.ws_room}")
                        
                        # Test new session
                        if await self._test_new_session_with_timeout(timeout=5):
                            # Acknowledge the distribution
                            await self.sio.emit('session_distribution_acknowledged', {
                                'old_session_id': old_session_id,
                                'new_session_id': self._session_id,
                                'distribution_confirmed': True,
                                'timestamp': time.time()
                            })
                    else:
                        logging.error(f"Invalid distributed session key length for {self.ws_room}")
                    
            except Exception as e:
                logging.error(f"Error processing distributed session for {self.ws_room}: {e}")
        
        @self.sio.event
        async def session_rotation_required(data):
            """Enhanced rotation handler with timeout protection"""
            try:
                if self._rotation_in_progress:
                    logging.warning(f"Rotation already in progress for {self.ws_room}, ignoring new request")
                    return

                logging.debug(f"Session rotation required for {self.ws_room}: {data}")
                
                old_session_id = data.get('old_session_id')
                new_session_id = data.get('new_session_id')
                new_session_key = data.get('new_session_key')

                if not all([old_session_id, new_session_id, new_session_key]):
                    logging.error(f"Invalid rotation data for {self.ws_room}")
                    return
                
                if old_session_id != self._session_id:
                    logging.warning(f"Rotation not for our session {self._session_id} for {self.ws_room}, ignoring")
                    return
                
                # Start rotation with timeout protection
                if not self._rotation_task or self._rotation_task.done():
                    self._rotation_task = asyncio.create_task(
                        self._handle_session_rotation(data)
                    )      
            except asyncio.TimeoutError:
                logging.error(f"Session rotation timeout for {self.ws_room}")
                await self._rotation_failed(old_session_id, "rotation_timeout")
            except Exception as e:
                logging.error(f"Session rotation handler error for {self.ws_room}: {e}")
        
        @self.sio.event
        async def session_rotation_initiated(data):
            """Handle manual rotation confirmation"""
            logging.warning(f"Manual session rotation initiated for {self.ws_room}: {data}")

        ## Token Refresh
        @self.sio.event
        async def token_refresh_required(data):
            await self._refresh_access_token()
        
        @self.sio.event
        async def token_refresh_success(data):
            """Handle successful token refresh from server"""
            try:
                session_id = data.get('session_id')
                new_access_token = data.get('new_access_token')
                new_expires_at = data.get('new_expires_at')
                new_refresh_token = data.get('new_refresh_token')
                
                if not all([session_id, new_access_token, new_expires_at]):
                    logging.error(f"❌ {self.ws_room} Incomplete token refresh data")
                    return
                    
                if session_id != self._session_id:
                    logging.warning(f"⚠️ {self.ws_room} Token refresh for wrong session")
                    return
                    
                # Update stored tokens
                self._access_token = new_access_token
                self.token_expires_at = new_expires_at
                if new_refresh_token:
                    self._refresh_token = new_refresh_token
                    
                # Acknowledge the refresh
                await self.sio.emit('token_refresh_acknowledged', {
                    'session_id': session_id,
                    'new_access_token': new_access_token,
                    'new_expires_at': new_expires_at
                })
                
                # Save to persistent storage
                await self.ogbevents.emit("SaveRequest", True)
                
                logging.warning(f"✅ {self.ws_room} Token refresh successful")
                
            except Exception as e:
                logging.error(f"❌ {self.ws_room} Token refresh success handler error: {e}")

        # ACTIONS
        @self.sio.event
        async def prem_actions(data):
            await self._handle_premium_actions(data)

        # Grow Plans
        @self.sio.event
        async def grow_plans_response(data):
            logging.debug(f"Recieved GrowPlans For {self.ws_room}: {data}")
            grow_plans = data.get("grow_plans", [])
            await self.ogbevents.emit("new_grow_plans",grow_plans)          

        # PREM UI CONTROLS
        @self.sio.event
        async def ctrl_change(data):
            logging.error(f"Recieved CTRL CHANGE FROM PREM UI For {self.ws_room}: {data}")
            await self.ogbevents.emit("PremUICTRLChange",data)
    
        ## OGB ERRORS
        @self.sio.event
        async def to_many_rooms(data):
            logging.error(f"❌ {self.ws_room} - {data}")
            await self.ogbevents.emit("ui_to_many_rooms_message",data,haEvent=True)
    
        @self.sio.event
        async def ip_violation(data):
            logging.error(f"❌ {self.ws_room} - IP VIOLATION- {data}")
                     
        @self.sio.event
        async def free_plan_no_access(data):
            logging.error(f"❌ {self.ws_room} - IP VIOLATION- {data}")
        
        ## ERRRORS
        @self.sio.event
        async def auth_failed(data):
            logging.error(f"❌ Authentication failed: {data}")
            self.authenticated = False

        @self.sio.event
        async def session_rotation_error(data):
            """Enhanced rotation error handler"""
            logging.error(f"Session rotation error from server for {self.ws_room}: {data}")
            self._rotation_in_progress = False
            self._rotation_start_time = None
            
            await self.ogbevents.emit(
                "LogForClient",
                f"Session rotation error for {self.ws_room}: {data.get('error', 'Unknown error')}",
                haEvent=True
            )

        @self.sio.event
        async def token_refresh_error(data):
            """Handle token refresh error"""
            error_msg = data.get('error', 'Unknown token refresh error')
            logging.error(f"❌ {self.ws_room} Token refresh failed: {error_msg}")
            
            await self.ogbevents.emit(
                "LogForClient",
                f"Token refresh failed for {self.ws_room}: {error_msg}. Please re-login.",
                haEvent=True
            )

        @self.sio.event
        async def message_error(data):
            logging.error(f"❌ Message error: {data}")

        @self.sio.event
        async def connect_error(data):
            logging.error(f"❌ Connection error: {data}")
            await self._handle_connection_loss()

        @self.sio.event
        async def error(data):
            logging.error(f"❌ Socket error: {data}")

    # =================================================================
    # Reconnection Logic
    # =================================================================

    async def _refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token."""
        if not self.is_logged_in or not self.is_premium:
            return False
        
        try:
            refresh_data = {
                "refresh_token": self._refresh_token,
            }
            success = None
            
            # Try via WebSocket first
            if self.sio and self.is_connected():
                try:
                    success = await self.prem_event("token-refresh", refresh_data)

                    logging.warning(f"🔄 Token refresh request sent via WebSocket {self.ws_room}")
                    return True  # Response will be handled by _handle_auth_response
                except Exception as e:
                    logging.warning(f"WebSocket token refresh failed: {e}")
            
            # Try via REST
            if success == False:
                logging.error(f"Token refresh error: {success}")
                return False
            else:
                await self.prem_event("token_refresh_acknowledged",{"session_id":self._session_id,"new_access_token":self._access_token,"new_expires_at":self.token_expires_at})
       
        
        except Exception as e:
            logging.error(f"Token refresh error: {e}")
            return False

    async def _handle_connection_loss(self, reason: str = "unknown"):
        """Simplified connection loss handler"""
        if self._reconnection_in_progress:
            logging.warning(f"Reconnection already in progress for {self.ws_room}")
            return

        # Update states
        self.ws_connected = False
        self._reconnection_in_progress = True
        
        # Stop keep-alive
        await self._stop_keepalive()
        
        # Disconnect cleanly if still connected
        try:
            if self.sio.connected:
                await self.sio.disconnect()
        except Exception:
            pass
        
        # Start single reconnection if enabled
        if self._should_reconnect:
            logging.warning(f"Connection lost for {self.ws_room} ({reason}), starting reconnection")
            if not self.reconnect_task or self.reconnect_task.done():
                self.reconnect_task = asyncio.create_task(self._unified_reconnect_loop())
        else:
            logging.warning(f"Connection lost for {self.ws_room} ({reason}), reconnection disabled")
         
    async def force_reconnect(self):
        """Single force reconnect method"""
        try:
            if self._reconnection_in_progress:
                logging.warning(f"Reconnection already in progress for {self.ws_room}")
                return True
            
            if self.is_connected():
                logging.warning(f"Already connected for {self.ws_room}, no need to reconnect")
                return True
                
            logging.warning(f"Forcing reconnection for {self.ws_room}")
            await self._handle_connection_loss("force_reconnect")
            
            # Wait briefly for reconnection to start
            await asyncio.sleep(2)
            return True
            
        except Exception as e:
            logging.error(f"Force reconnect error for {self.ws_room}: {e}")
            return False

    async def _unified_reconnect_loop(self):
        """Single unified reconnection loop with optimized timing"""
        base_delay = 2  # Start with 2 seconds
        max_delay = 60  # 1 minute max instead of 5 minutes
        
        while (self._should_reconnect and 
            self.reconnect_attempts < self.max_reconnect_attempts and
            not self.ws_connected):
            
            self.reconnect_attempts += 1
            
            # More conservative exponential backoff
            if self.reconnect_attempts == 1:
                delay = base_delay  # First attempt: 2s
            elif self.reconnect_attempts <= 3:
                delay = base_delay * (1.2 ** (self.reconnect_attempts - 1))  # Slower growth: 2s, 2.4s, 2.88s
            else:
                delay = min(base_delay * (1.5 ** (self.reconnect_attempts - 3)) * 2.88, max_delay)  # After 3rd: faster growth
            
            # Smaller jitter (5% instead of 10%)
            jitter = delay * 0.05 * (secrets.randbelow(100) / 100)
            total_delay = delay + jitter
            
            logging.warning(f"Reconnect attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} "
                        f"for {self.ws_room} in {total_delay:.1f}s")
            
            await asyncio.sleep(total_delay)
            
            # Check if we should still reconnect
            if not self._should_reconnect or self.ws_connected:
                break
                
            try:
                async with self._connection_lock:
                    # Double check connection state
                    if self.ws_connected:
                        logging.warning(f"Already connected during reconnect attempt for {self.ws_room}")
                        break
                    
                    # Ensure clean state
                    if hasattr(self, 'sio') and self.sio.connected:
                        try:
                            await self.sio.disconnect()
                            await asyncio.sleep(0.3)  # Reduced from 0.5s
                        except:
                            pass
                    
                    # Try to get fresh session if needed
                    if not self._session_key or not self._session_id:
                        logging.warning(f"Requesting new session for {self.ws_room} reconnect")
                        if not await self._request_session_key():
                            logging.error(f"Failed to get session key for {self.ws_room} reconnect")
                            continue
                    
                    # Try to reconnect
                    if await self._connect_websocket():
                        logging.warning(f"Reconnect successful for {self.ws_room}")
                        self._reconnection_in_progress = False
                        self.reconnect_attempts = 0  # Reset on success
                        await self._start_keepalive()
                        return
                    else:
                        logging.warning(f"Reconnection Error Try Session Restore for {self.ws_room}")
                        await self.session_restore()
                    
            except Exception as e:
                logging.error(f"Reconnect attempt {self.reconnect_attempts} failed for {self.ws_room}: {e}")

        # Max attempts reached or should not reconnect
        logging.error(f"Max reconnect attempts reached for {self.ws_room} attempts:{self.reconnect_attempts}")
        self._reconnection_in_progress = False
        
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            await self.ogbevents.emit(
                "LogForClient",
                f"Connection lost and max reconnect attempts reached for {self.ws_room}. Please try logging in again.",
                haEvent=True
            )
    # =================================================================
    # Keep-Alive System
    # =================================================================
    
    async def _start_keepalive(self):
        """Start unified keep-alive system"""
        await self._stop_keepalive()        
        if self._keepalive_task and not self._keepalive_task.done():
            return
            
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logging.debug(f"Keep-alive started for {self.ws_room}")

    async def _stop_keepalive(self):
        """Stop keep-alive system"""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        logging.debug(f"Keep-alive stopped for {self.ws_room}")

    async def _keepalive_loop(self):
        """Unified keep-alive and health monitoring loop"""
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
                        "health_check": True
                    }
                    
                    await self.sio.emit("ping", ping_data)
                    logging.debug(f"Sent health ping for {self.ws_room}")
                    
                    # Wait for pong with timeout
                    pong_received = await self._wait_for_pong(self._pong_timeout)
                    
                    if not pong_received:
                        logging.warning(f"Health check failed for {self.ws_room} - no pong received")
                        # Connection might be unhealthy, trigger reconnection
                        await self._handle_connection_loss("health_check_failed")
                        break
                    else:
                        logging.debug(f"Health check OK for {self.ws_room}")
                        
                except Exception as e:
                    logging.error(f"Keep-alive error for {self.ws_room}: {e}")
                    await self._handle_connection_loss("keepalive_error")
                    break
                    
        except asyncio.CancelledError:
            logging.debug(f"Keep-alive cancelled for {self.ws_room}")
        except Exception as e:
            logging.error(f"Keep-alive loop error for {self.ws_room}: {e}")

    async def _wait_for_pong(self, timeout: float) -> bool:
        """Wait for pong response with timeout"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._last_pong_time > start_time:
                return True
            await asyncio.sleep(0.1)
        return False

    # =================================================================
    # Session Management
    # =================================================================

    async def session_restore(self, stored_session_data: dict = None, event_id: str = None):
        """Single session restore method"""
        try:
            logging.debug(f"Attempting session restore for {self.ws_room} with {stored_session_data}")

            # Option 1: Restore from provided data
            if stored_session_data:
                user_id = stored_session_data.get("user_id")
                access_token = stored_session_data.get("access_token") 
                is_logged_in = stored_session_data.get("is_logged_in", False)
                is_premium = stored_session_data.get("is_premium", False)
                subscription_data = stored_session_data.get("subscription_data", {})

                if user_id and access_token:
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
                            try:
                                self._session_id = session_id
                                if isinstance(session_key, str):
                                    self._session_key = self._safe_b64_decode(session_key)
                                else:
                                    self._session_key = session_key
                                
                                if len(self._session_key) == 32:
                                    self._aes_gcm = AESGCM(self._session_key)
                                    logging.warning(f"Session key restored from stored data for {self.ws_room}")
                                else:
                                    logging.warning(f"Invalid stored session key for {self.ws_room}, will request new one")
                                    self._session_key = None
                                    self._session_id = None
                                    self._aes_gcm = None
                            except Exception as e:
                                logging.warning(f"Error restoring session key for {self.ws_room}: {e}, will request new one")
                                self._session_key = None
                                self._session_id = None
                                self._aes_gcm = None
                        
                        # If no valid session key, request new one
                        if not self._session_key:
                            session_data = await self._request_session_key(event_id)
                            if not session_data:
                                logging.error(f"Failed to get new session key during restore for {self.ws_room}")
                                if event_id:
                                    await self._send_auth_response(event_id, "error", "Failed to establish session")
                                return False
                        
                        # Try to connect
                        if await self._connect_websocket():
                            if event_id:
                                self.authenticated = True
                                self.ws_connected = True
                                await self._send_auth_response(
                                    event_id, "success", "Session restored successfully",
                                    {"session_id": self._session_id, "user_id": self._user_id}
                                )
                            return True
                        else:
                            logging.error(f"WebSocket connection failed during restore for {self.ws_room}")
                            return False
                        
                    except Exception as e:
                        logging.error(f"Session restore from data failed for {self.ws_room}: {e}")

            # Option 2: Try to refresh current session if we have access token
            if self._access_token and not stored_session_data:
                logging.warning(f"Attempting session refresh with existing access token for {self.ws_room}")
                session_data = await self._request_session_key(event_id)
                if session_data and await self._connect_websocket():
                    await self._start_keepalive()
                    return True

            # Nothing to restore
            logging.warning(f"No valid session data to restore for {self.ws_room}")
            if event_id:
                await self._send_auth_response(event_id, "error", "No session data to restore - please login again")
            
            return False

        except Exception as e:
            logging.error(f"Session restore error for {self.ws_room}: {e}")
            return False

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
            "keepalive_running": bool(self._keepalive_task and not self._keepalive_task.done()),
            "user_id": self._user_id,
            "last_pong": self._last_pong_time,
            "timestamp": time.time()
        }
 
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
            old_session_id = rotation_data.get('old_session_id')
            new_session_id = rotation_data.get('new_session_id')
            new_session_key_b64 = rotation_data.get('new_session_key')
            
            logging.debug(f"Starting enhanced session rotation for {self.ws_room}: {old_session_id} -> {new_session_id}")
            
            # Validate rotation applies to our current session
            if old_session_id != self._session_id:
                logging.debug(f"Rotation not for our session ({self._session_id}), ignoring")
                self._rotation_in_progress = False
                return

            # Step 1: Decode and validate new session key
            try:
                new_session_key = self._safe_b64_decode(new_session_key_b64)
                if len(new_session_key) != 32:
                    raise ValueError(f"Invalid session key length: {len(new_session_key)}")
                new_aes_gcm = AESGCM(new_session_key)
            except Exception as e:
                logging.error(f"Failed to decode new session key for {self.ws_room}: {e}")
                await self._rotation_failed(old_session_id, "key_decode_error")
                return


            old_session_key = self._session_key
            old_aes_gcm = self._aes_gcm

            self._session_id = new_session_id
            self._session_key = new_session_key
            self._aes_gcm = new_aes_gcm


            test_success = await self._test_new_session_with_timeout()
            if not test_success:
                logging.debug(f"New session test failed for {self.ws_room}, rolling back")
                # Rollback
                self._session_id = old_session_id
                self._session_key = old_session_key
                self._aes_gcm = old_aes_gcm
                await self._rotation_failed(old_session_id, "session_test_failed")
                return

            try:
                await self.sio.emit('session_rotation_acknowledged', {
                    'old_session_id': old_session_id,
                    'new_session_id': new_session_id,
                    'immediate_cleanup_requested': True,  # Signal server to clean up NOW
                    'cleanup_confirmed': True,
                    'rotation_duration': time.time() - self._rotation_start_time,
                    'timestamp': time.time()
                })
             
                logging.debug(f"Sent immediate cleanup acknowledgment for {self.ws_room}")
            except Exception as e:
                logging.warning(f"Socket acknowledgment failed for {self.ws_room}: {e}")


            await asyncio.sleep(1)  # Brief pause
            final_test = await self._test_new_session_with_timeout(timeout=5)
            
            if final_test:
                logging.warning(f"Session rotation completed successfully for {self.ws_room}")
                await self.ogbevents.emit(
                    "LogForClient",
                    f"Session key rotated successfully for {self.ws_room}",
                    haEvent=True
                )
                await self.ogbevents.emit("SaveRequest",True)
            else:
                logging.warning(f"Final session test failed for {self.ws_room}, but rotation complete")

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
                "session_id": self._session_id
            }
            
            # Record time before ping
            ping_time = time.time()
            await self.sio.emit("ses_test", test_data)
            
            # Wait for pong with timeout
            while time.time() - ping_time < timeout:
                if self._last_pong_time > ping_time:
                    duration = time.time() - ping_time
                    logging.warning(f"Session test successful for {self.ws_room} in {duration:.2f}s")
                    return True
                await asyncio.sleep(0.1)
            
            logging.warning(f"Session test timeout after {timeout}s for {self.ws_room}")
            return False
            
        except Exception as e:
            logging.error(f"Session test error for {self.ws_room}: {e}")
            return False

    async def _acknowledge_session_rotation(self, old_session_id: str, new_session_id: str) -> bool:
        """Enhanced HTTP acknowledgment with retry logic"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                url = f"{self.api_url}/api/auth/acknowledge-rotation"
                
                request_data = {
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id,
                    "user_id": self._user_id,
                    "access_token": self._access_token,
                    "immediate_cleanup": True,  # Request immediate cleanup
                    "client_confirmed": True,
                    "rotation_attempt": attempt + 1
                }

                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "OGB-Python-Client/1.0",
                    "Accept": "application/json",
                    "origin": "https://opengrowbox.net",
                    "ogb-client": "ogb-ws-ha-connector 1.0",
                    "ogb-client-id": self.client_id,
                }

                timeout_config = aiohttp.ClientTimeout(total=8)

                async with aiohttp.ClientSession(timeout=timeout_config) as session:
                    async with session.post(url, json=request_data, headers=headers) as response:
                        
                        if response.status == 200:
                            try:
                                result = await response.json()
                                if result.get("status") == "success":
                                    logging.warning(f"HTTP acknowledgment successful for {self.ws_room} (attempt {attempt + 1})")
                                    await self.ogbevents.emit("SaveRequest",True)
                                    return True
                                else:
                                    logging.error(f"HTTP acknowledgment failed for {self.ws_room}: {result.get('message')}")
                                    
                            except json.JSONDecodeError:
                                logging.error(f"Invalid HTTP acknowledgment response for {self.ws_room}")
                        else:
                            logging.error(f"HTTP acknowledgment HTTP error for {self.ws_room}: {response.status}")
                        
                        # If not last attempt, wait before retry
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1)
                            continue
                        
                        return False

            except Exception as e:
                logging.error(f"HTTP acknowledgment attempt {attempt + 1} failed for {self.ws_room}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                continue
        
        return False

    async def _rotation_failed(self, old_session_id: str, reason: str):
        """Handle rotation failure with proper cleanup"""
        logging.error(f"Session rotation failed for {self.ws_room}: {reason}")
        
        try:
            # Notify server of failure
            await self.sio.emit('session_rotation_error', {
                'old_session_id': old_session_id,
                'failure_reason': reason,
                'timestamp': time.time(),
                'request_rollback': True
            })
        except Exception as e:
            logging.error(f"Failed to send rotation error notification for {self.ws_room}: {e}")
        
        # Reset rotation state
        self._rotation_in_progress = False
        self._rotation_start_time = None
        
        # Notify application
        await self.ogbevents.emit(
            "LogForClient",
            f"Session rotation failed for {self.ws_room}: {reason}",
            haEvent=True
        )

    async def request_manual_rotation(self, event_id: str = None) -> bool:
        """Enhanced manual rotation request"""
        try:
            if not self.is_connected() or self._rotation_in_progress:
                logging.warning(f"Cannot rotate for {self.ws_room} - not connected or rotation in progress")
                return False

            logging.warning(f"Requesting manual session rotation for {self.ws_room}")
            
            await self.sio.emit('request_session_rotation', {
                'event_id': event_id or str(uuid.uuid4()),
                'current_session_id': self._session_id,
                'manual_request': True,
                'immediate_cleanup_preferred': True,
                'timestamp': time.time()
            })

            return True

        except Exception as e:
            logging.error(f"Manual rotation request error for {self.ws_room}: {e}")
            return False

    # =================================================================
    # Session Distribution (for other rooms)
    # =================================================================

    async def distribute_session_to_others(self, target_room_id: str = None, event_id: str = None):
        """Distribute session to other connected devices/clients"""
        try:
            if not self._user_id or not self._access_token or not self._session_id:
                logging.error(f"❌ {self.ws_room} Cannot distribute - not fully authenticated")
                if event_id:
                    await self._send_auth_response(event_id, "error", "Not authenticated")
                return None

            url = f"{self.api_url}/api/auth/distribute-session"
            
            request_data = {
                "client_id": self.client_id,
                "user_id": self._user_id,
                "access_token": self._access_token,
                "requester_session_id": self._session_id,
                "room_id": self.room_id,
                "room_name":self.ws_room,
                "target_room_id": target_room_id
            }

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "OGB-Python-Client/1.0"
            }

            logging.warning(f"📤 {self.ws_room} Distributing session to others")

            timeout_config = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.post(url, json=request_data, headers=headers) as response:
                    
                    if response.status != 200:
                        logging.error(f"❌ {self.ws_room} Session distribution failed: HTTP {response.status}")
                        return None

                    try:
                        result = json.loads(await response.text())
                    except json.JSONDecodeError as e:
                        logging.error(f"❌ {self.ws_room} Invalid JSON in distribution response: {e}")
                        return None

                    if result.get("status") != "success":
                        error_msg = result.get('message', 'Unknown error')
                        logging.error(f"❌ {self.ws_room} Session distribution failed: {error_msg}")
                        return None

                    distribution_info = {
                        "new_session_id": result.get("session_id"),
                        "distributed_to_sockets": result.get("distributed_to_sockets", 0),
                        "target_room_id": result.get("target_room_id"),
                        "plan": result.get("plan"),
                        "timestamp": result.get("timestamp")
                    }

                    logging.warning(f"✅ {self.ws_room} Session distributed to {distribution_info['distributed_to_sockets']} sockets")
                    return distribution_info

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Session distribution error: {e}")
            return None

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
        
        # Cancel reconnect task
        if self.reconnect_task and not self.reconnect_task.done():
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
        
        # Disconnect socket
        if hasattr(self, 'sio') and self.ws_connected:
            try:
                await self.sio.disconnect()
            except Exception as e:
                logging.warning(f"Error during disconnect: {e}")
        
        # Reset connection states only
        self.ws_connected = False
        
        await self.ogbevents.emit("ogb_client_disconnect",self.ogb_sessions)
        await self.room_removed()
        
        await self._send_auth_response(self.create_event_id(), "success", "Disconnect Success", {
            "ogb_sessions": self.ogb_sessions,
            "ogb_max_sessions": self.ogb_max_sessions,
        })

        logging.warning(f"✅ {self.ws_room} WebSocket disconnected")

    async def cleanup_prem(self, event_id):
        """Enhanced cleanup with rotation task cancellation"""
        try:
            logging.warning(f"🧹 {self.ws_room} Cleaning up premium data")
            
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
            if hasattr(self, 'sio') and self.sio.connected:
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
            self._refresh_token = None
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
                reconnection=True,
                logger=False,
                engineio_logger=False,
                ssl_verify=True
            )
            
            # Re-setup event handlers including rotation handlers
            self._setup_event_handlers()
                
            logging.warning(f"✅ {self.ws_room} Premium data cleanup completed")
            
            if event_id:
                await self._send_auth_response(
                    event_id, 
                    "success", 
                    "Logout successful",
                    {"logged_out_at": time.time()}
                )
            
            await self.ogbevents.emit(
                "LogForClient",
                f"Successfully logged out from {self.ws_room}",
                haEvent=True
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
                encoded_data += '=' * (4 - missing_padding)
            
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
                encoded_data += '=' * (4 - missing_padding)
            return base64.b64decode(encoded_data)
            
        except Exception as e:
            logging.error(f"❌ Base64 decode error: {e}")
            raise ValueError(f"Failed to decode base64 data: {e}")
   
    def _encrypt_message(self, data: dict) -> dict:
        """Verschlüssele Nachricht mit AES-GCM"""
        try:
            if not self._aes_gcm:
                raise ValueError("No encryption key available")
            
            message = json.dumps(data).encode('utf-8')
            nonce = secrets.token_bytes(12)
            ciphertext = self._aes_gcm.encrypt(nonce, message, None)
            
            return {
                "iv": base64.urlsafe_b64encode(nonce[:12]).decode(),
                "tag": base64.urlsafe_b64encode(ciphertext[-16:]).decode(),
                "data": base64.urlsafe_b64encode(ciphertext[:-16]).decode(),
                "timestamp": int(time.time())
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
            return json.loads(plaintext.decode('utf-8'))
            
        except Exception as e:
            logging.error(f"❌ Decryption error: {e}")
            raise

    # =================================================================
    # User Data
    # =================================================================
 
    def get_connection_info(self) -> dict:
        """Get current connection information"""
        base_info = {
            "connected": self.sio.connected if hasattr(self, 'sio') else False,
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
            "rotation_duration": (time.time() - self._rotation_start_time) if self._rotation_start_time else None,
            "connected": self.is_connected(),
            "authenticated": self.authenticated,
            "room": self.ws_room,
            "user_id": self._user_id,
            "last_pong_time": self._last_pong_time,
            "keepalive_running": bool(self._keepalive_task and not self._keepalive_task.done())
        }

    def get_session_backup_data(self) -> dict:
        """Enhanced session backup with additional metadata"""
        if not self.is_logged_in:
            return {}
        
        return {
            "user_id": self._user_id,
            "session_id": self._session_id,
            "session_key": base64.urlsafe_b64encode(self._session_key).decode() if self._session_key else None,
            "access_token": self._access_token,
            "token_expires_at":self.token_expires_at,
            "refresh_token":self._refresh_token,
            "access_token_hash": hashlib.sha256(self._access_token.encode()).hexdigest() if self._access_token else None,
            "refresh_token":self._refresh_token,
            "ogb_sessions":self.ogb_sessions,
            "ogb_max_sessions":self.ogb_max_sessions,
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
            if not self.authenticated or not self._aes_gcm:
                logging.error(f"❌ {self.ws_room} Cannot send - not authenticated or no encryption key")
                return False

            message_data = {
                "type": message_type,
                "data": data,
                "timestamp": int(time.time()),
                "from": self._user_id,
                "client_id":self.client_id,
            }

            encrypted_data = self._encrypt_message(message_data)
            await self.sio.emit('encrypted_message', encrypted_data)
            logging.debug(f"📤 {self.ws_room} Sent encrypted message: {message_type}")
            return True

        except Exception as e:
            logging.error(f"❌ {self.ws_room} Send message error: {e}")
            return False

    async def prem_event(self, message_type: str, data: dict) -> bool:
        """Send message via WebSocket"""
        try:
            if not (self.is_premium and self.sio and self.sio.connected and self.authenticated):
                logging.debug(f"❌ {self.ws_room} Cannot send - not ready (premium: {self.is_premium}, connected: {self.sio.connected if self.sio else False}, auth: {self.authenticated})")
                return False
            
            message_data = {
                "type": message_type,
                "event_id": data.get("event_id") or str(uuid.uuid4()),
                "room_name": self.ws_room,
                "room_id": self.room_id,
                "user_id": self._user_id,
                "session_id":self._session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data,
            }
            
            await self.sio.emit(message_type, message_data)
            logging.debug(f"📤 {self.ws_room} Sent via WebSocket: {message_type}")
            return True
            
        except Exception as e:
            logging.error(f"❌ {self.ws_room} WebSocket send failed: {e}")
            return False
     
    # =================================================================
    # Helper Methods
    # =================================================================

    def create_event_id(self):
        return str(uuid.uuid4())

    async def _handle_premium_actions(self, data):
        if self.room_id != data.get("room_id"):
            return
        
        await self.ogbevents.emit("PremiumCheck",data)
   
    async def _send_auth_response(self, event_id: str, status: str, message: str, data: dict = None):
        """Send authentication response"""
        response_data = {
            "event_id": event_id,
            "status": status,
            "message": message,
            "room": self.ws_room,
            "timestamp": datetime.now().isoformat()
        }
        
        if data:
            response_data["data"] = data
        
        await self.ogbevents.emit("ogb_premium_auth_response", response_data, haEvent=True)

    def _validate_url(self, url: str) -> str:
        """Validate URL"""
        if not url or not isinstance(url, str):
            raise ValueError("Invalid URL provided")
            
        if not (url.startswith('ws://') or url.startswith('wss://') or 
                url.startswith('http://') or url.startswith('https://')):
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
        return (hasattr(self, 'sio') and 
                self.sio.connected and 
                self.authenticated and 
                self.ws_connected)

    def is_ready(self) -> bool:
        """Check if client is ready to send messages"""
        return (self.is_connected() and 
                self.is_premium and 
                self._session_key is not None)

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
            "timestamp": time.time()
        }
    
    async def room_removed(self):
        if self.ogb_sessions > 0:
            self.ogb_sessions -= 1
        logging.warning(f"{self.ws_room} - Active Sessions:{self.ogb_sessions} Max Sessions:{self.ogb_max_sessions} From:{self.ws_room}")
        # Jetzt den aktuellen Wert senden
        await self.ogbevents.emit("ogb_client_disconnect", self.ws_room,haEvent=True)
        
    async def room_disconnect(self,data):
        if self.ws_room == data:
            return
        if self.ogb_sessions > 0:
            self.ogb_sessions -= 1
        logging.warning(f"{self.ws_room} - Active Sessions:{self.ogb_sessions} Max Sessions:{self.ogb_max_sessions}")
        

"""
OGB Premium Manager is There to make The API CALLS for the OGB Premium Version
"""
import os
import logging
import asyncio
import aiohttp
import uuid
import json
import base64
from datetime import datetime,  timezone
from ..const import PREM_WS_API
from .utils.Premium.SecureWebSocketClient import OGBWebSocketConManager as OGB_WS
from .utils.Premium.ogb_state import _save_state_securely,_remove_state_file,_load_state_securely
from .OGBGrowPlanManager import OGBGrowPlanManager

_LOGGER = logging.getLogger(__name__)

class OGBPremManager:
    def __init__(self, hass, dataStore, eventManager, room):
        self.name = "OGB Premium Manager"
        self.hass = hass
        self.room = room
        self.dataStore = dataStore
        self.eventManager = eventManager
        self.growPlanManager = OGBGrowPlanManager(self.hass, self.dataStore, self.eventManager,self.room)

        self.room_id = None
        
        # Main Control Status
        self.is_premium_selected = False
        self.is_logged_in = False
        self.is_premium = False
        self.has_control_prem = False
        self.is_main_auth_room = False
        #User Data
        self.user_id = None
        self.subscription_data  = None

        self._setup_event_listeners()

        asyncio.create_task(self.init())
            
    async def init(self):
        """Initialize Premium Manager"""
        await self._get_or_create_room_id()
        self.ogb_ws = OGB_WS(PREM_WS_API,self.eventManager,ws_room=self.room,room_id=self.room_id)
        await asyncio.sleep(0.5)
        await self._load_last_state()
        
        # Connection Configuration
        _LOGGER.info(f"OGBPremiumManager initialized for room: {self.room}")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self.hass.bus.async_listen("ogb_premium_login", self._on_prem_login)
        self.hass.bus.async_listen("ogb_premium_logout", self._on_prem_logout)
        self.hass.bus.async_listen("ogb_premium_get_profile", self._get_user_profile)
        # GrowPlan Handlers

        self.hass.bus.async_listen("ogb_premium_get_growplans", self._ui_get_growplans_request)
        self.hass.bus.async_listen("ogb_premium_add_growplan", self._ui_grow_plan_add_request)   
        
        self.hass.bus.async_listen("ogb_premium_del_growplan", self._ui_grow_plan_del_request)

        self.hass.bus.async_listen("ogb_premium_growplan_activate", self._ui_grow_plan_activation)   
        
        self.eventManager.on("DataRelease", self._send_growdata_to_prem_api)
        self.eventManager.on("PremiumChange", self._handle_premium_change)
        self.eventManager.on("SaveRequest",self._save_request)
        self.eventManager.on("PremUICTRLChange",self._handle_ctrl_change)

        self.hass.bus.async_listen("isAuthenticated", self._handle_authenticated)

    async def _load_last_state(self):
        """Load saved state and restore connection"""
        state_data = await _load_state_securely(self.hass)
        
        if not state_data:
            _LOGGER.debug(f"No saved state found for {self.room}")
            return False
        
        restoring_room = state_data.get("room_name")
        
        if self.room != restoring_room:
            return False
        
        _LOGGER.debug(f"✅ {self.room} Loading saved state {state_data}")

        # Restore Manager State
        self.is_main_auth_room = True
        self.is_logged_in = state_data.get("is_logged_in", False)
        self.is_premium = state_data.get("is_premium", False)
        self.user_id = state_data.get("user_id",None)
        self.subscription_data = state_data.get("subscription_data", {})
        self.growPlanManager.active_grow_plan = state_data.get("active_grow_plan",None)
        
        # Restore WebSocket state if present
        ws_data = state_data.get("ws_data")
        if ws_data and self.is_logged_in:
            try:
                # Create new WebSocket client and restore its state               
                #self.ogb_ws = OGB_WS(PREM_WS_API, self.eventManager, ws_room=self.room,room_id=self.room_id)
                
                logging.debug(f"{self.room} Restoring WS_DATA: {ws_data}")
                
                # Restore WebSocket client data
                self.ogb_ws._user_id = ws_data.get("user_id",None)
                self.ogb_ws.client_id = ws_data.get("client_id",None)
                self.ogb_ws.is_premium = ws_data.get("is_premium", False)
                self.ogb_ws.is_logged_in = ws_data.get("is_logged_in", False)
                self.ogb_ws.authenticated = ws_data.get("authenticated", False)
                self.ogb_ws.subscription_data = ws_data.get("subscription_data", {})
                self.ogb_ws._session_id = ws_data.get("session_id",None)
                self.ogb_ws._session_key = ws_data.get("session_key",None)
                self.ogb_ws.ogb_sessions = ws_data.get("ogb_sessions",0)
                self.ogb_ws.ogb_max_sessions = ws_data.get("ogb_max_sessions",0)
        
                # Restore access token
                access_token_b64 = ws_data.get("access_token")
                if access_token_b64:
                    try:
                        # Clean up the base64 string if it has literal b'' wrapper
                        if isinstance(access_token_b64, str) and access_token_b64.startswith("b'"):
                            access_token_b64 = access_token_b64[2:-1]
                        
                        token_bytes = base64.b64decode(access_token_b64)
                        self.ogb_ws._access_token = token_bytes.decode("utf-8")
                    except Exception as e:
                        _LOGGER.error(f"❌ {self.room} Error restoring access token: {e}")
                        self.ogb_ws._access_token = None
                        self.ogb_ws.authenticated = False
                
                self.ogb_ws.token_expires_at = ws_data.get("token_expires_at")
                self.ogb_ws._refresh_token = ws_data.get("refresh_token")
                
                # Restore session data (will be validated/refreshed during connection)
                session_data = {}
                if ws_data.get("session_id"):
                    session_data["session_id"] = ws_data.get("session_id")
                if ws_data.get("session_key"):
                    session_data["session_key"] = ws_data.get("session_key")
                
                session_data.update({
                    "user_id": self.ogb_ws._user_id,
                    "access_token": self.ogb_ws._access_token,
                    "token_expires_at":self.ogb_ws.token_expires_at,
                    "refresh_token":self.ogb_ws._refresh_token,
                    "is_logged_in": self.ogb_ws.is_logged_in,
                    "is_premium": self.ogb_ws.is_premium,
                    "subscription_data": self.ogb_ws.subscription_data,
                    "room_name": self.ogb_ws.ws_room,
                    "room_id": self.room_id
                    
                })

                # Attempt to restore session
                _LOGGER.warning(f"🔄 {self.room} Attempting to restore WebSocket session {self.ogb_ws._user_id} {self.ogb_ws._access_token}")
                
                #success = await self.ogb_ws.session_restore(session_data)
                #success = await self.ogb_ws.establish_session_from_auth_data(session_data)
                success = await self.ogb_ws._connect_websocket()        

                if success:
                    _LOGGER.warning(f"✅ {self.room} WebSocket session restored successfully")
                    await self._send_auth_to_other_rooms()
                else:
                    _LOGGER.warning(f"⚠️ {self.room} WebSocket session restore failed, will need fresh login")
                
                return success
                
            except Exception as e:
                _LOGGER.error(f"❌ {self.room} Error during state restore: {e}")
                return False
        
        return True

    # =================================================================
    # User Profile
    # =================================================================

    async def _get_user_profile(self, event):
        """Get current premium status"""
        try:
            if self.room != event.data.get("room"):
                return
                
            event_id = event.data.get("event_id")
            
            if not self.is_logged_in or not self.is_premium:
                await self._send_auth_response(event_id, "error", "Not authenticated")
                return
            
            connection_info = self.ogb_ws.get_connection_info()
            health = await self.ogb_ws.health_check()
            
            await self._send_auth_response(event_id, "success", "Profile retrieved", {
                "currentPlan": self.subscription_data.get("plan_name"),
                "is_premium": self.is_premium,
                "subscription_data": self.subscription_data,
                "connection_info": connection_info,
                "health_status": health,
                "ogb_max_sessions": self.ogb_ws.ogb_max_sessions,
                "ogb_sessions":self.ogb_ws.ogb_sessions,
            })

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Get profile error: {str(e)}")
            await self._send_auth_response(event.data.get("event_id"), "error", f"Failed to get profile: {str(e)}")
            try:
                event_id = event.data.get("event_id")
                
                if not self.is_logged_in or not self.is_premium:
                    await self._send_auth_response(event_id, "error", "Not authenticated")
                    return
                    
                await self._send_auth_response(event_id, "success", "Profile retrieved", {
                    "currentPlan": self.subscription_data.get("plan_name"),
                    "is_premium": self.is_premium,
                    "subscription_data": self.subscription_data,
                    #"activeSockets": self.ogb_connections,
                    #"maxSockets":  self.ogb_max_connections,
                })

            except Exception as e:
                _LOGGER.error(f"Get profile error: {str(e)}")
                await self._send_auth_response(event_id, "error", f"Failed to get profile: {str(e)}")

   # =================================================================
    # Authentication
    # =================================================================

    async def _on_prem_login(self, event):
        """Enhanced login handler using integrated client"""
        try:
            if self.room != event.data.get("room"):
                return

            # Set premium selected FIRST
            self.is_main_auth_room = True
            self.is_premium_selected = True
            requestingRoom = event.data.get("room")
            email = event.data.get("email")
            password = event.data.get("password")
            event_id = event.data.get("event_id")

            _LOGGER.debug(f"🔐 {self.room} Premium login attempt for: {email}")

            success = await self.ogb_ws.login_and_connect(
                email=email,
                password=password,
                room_id=self.room_id,
                room_name=self.room,
                event_id=event_id,
                auth_callback=self._send_auth_response
            )

            if success:
                # Store additional data
                user_info = self.ogb_ws.get_user_info()
                self.subscription_data = user_info["subscription_data"]
                self.user_id = user_info["user_id"]
                self.is_premium = user_info["is_premium"]
                self.is_logged_in = user_info["is_logged_in"]
                
                _LOGGER.info(f"✅ {self.room} Premium login and connection successful")
                
                # Update main control
                await self._change_sensor_value("SET", "select.ogb_maincontrol", "Premium")
                
                # Notify other rooms
                await self._send_auth_to_other_rooms()
            
                # Save state
                await self._save_current_state()    
        
            else:
                # Reset premium selected on failure
                self.is_premium_selected = False
                _LOGGER.error(f"❌ {self.room} Premium login failed")

        except Exception as e:
            self.is_premium_selected = False  # Reset on error
            _LOGGER.error(f"❌ {self.room} Premium login error: {e}")

    async def _on_prem_logout(self, event):
        """Handle logout event."""
        try:
            _LOGGER.error(f"Logout event: {event}")

            # Event-Daten holen
            event_id = event.data.get("event_id")
            self.is_main_auth_room = False
            if self.is_logged_in:
                await self.ogb_ws.prem_event("logout", {"user_id": self.user_id})
                
            await self._cleanup_auth(event_id)

        except Exception as e:
            _LOGGER.error(f"Logout error: {e}")
            # Fallback falls event_id nicht gefunden wurde
            event_id = getattr(event, "data", {}).get("event_id", None)
            await self._send_auth_response(event_id, "error", f"Logout failed: {str(e)}")

    async def _send_auth_to_other_rooms(self):
        """Send authentication data to other rooms"""
        try:
            auth_data = {
                "AuthenticatedRoom": self.room,
                "user_id": self.user_id,
                "is_logged_in": self.is_logged_in,
                "is_premium": self.is_premium,
                "subscription_data": self.subscription_data,
                "access_token": self.ogb_ws._access_token,
                "token_expires_at":self.ogb_ws.token_expires_at,
                "refresh_token":self.ogb_ws._refresh_token,
                "ogb_sessions":self.ogb_ws.ogb_sessions,
                "ogb_max_sessions":self.ogb_ws.ogb_max_sessions,
            }

            await self.eventManager.emit("isAuthenticated", auth_data, haEvent=True)
            _LOGGER.debug(f"📤 {self.room} Sent authentication data to other rooms")
            
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error sending auth to other rooms: {e}")

    async def _handle_authenticated(self, event):
        """Handle authentication event from other rooms"""
        try:
            if self.room == "Ambient":
                return
            if self.room == event.data.get("AuthenticatedRoom"):
                return

            authenticated_room = event.data.get("AuthenticatedRoom")               

            
            # Extract data directly (these are already booleans from the event)
            user_id = event.data.get("user_id")
            access_token = event.data.get("access_token") 
            is_logged_in = event.data.get("is_logged_in", False)
            is_premium = event.data.get("is_premium", False)
            subscription_data = event.data.get("subscription_data", {})
            token_expires_at = event.data.get('token_expires_at')
            refresh_token = event.data.get("refresh_token")
            ogb_sessions = event.data.get("ogb_sessions")
            ogb_max_sessions = event.data.get("ogb_max_sessions")
            _LOGGER.warning(f"🔐 {self.room} Received auth from {authenticated_room}: is_premium={event.data.get('is_premium')} Sessions:{ogb_sessions} MaxSessions:{ogb_max_sessions} ")   
            # Update local state
            self.user_id = user_id
            self.is_logged_in = is_logged_in
            self.is_premium = is_premium
            self.subscription_data = subscription_data
            
            self.ogb_ws._access_token = access_token
            self.ogb_ws.token_expires_at = token_expires_at
            self.ogb_ws._refresh_token  = refresh_token
            
            self.ogb_ws._user_id = user_id
            self.ogb_ws.is_logged_in = is_logged_in
            self.ogb_ws.is_premium = is_premium
            self.ogb_ws.ogb_sessions = ogb_sessions
            self.ogb_ws.ogb_max_sessions = ogb_max_sessions

            if self.ogb_ws.is_premium == True and self.ogb_ws.is_logged_in == True:
                self.ogb_ws.authenticated = True
            
            _LOGGER.debug(f"🎯 {self.room} Updated state: is_logged_in={self.is_logged_in}, is_premium={self.is_premium}")
            
            # Create auth data for WebSocket
            auth_data = {
                "user_id": user_id,
                "access_token": access_token,
                "is_premium": is_premium,
                "is_logged_in": is_logged_in,
                "subscription_data": subscription_data,
            }
            
            # Establish session if conditions are met
            if self.is_premium and self.is_logged_in:

                if not self._check_if_premium_selected():
                    return
                
                _LOGGER.debug(f"🔄 {self.room} Load auth data for session")
                success = await self.ogb_ws.establish_session_from_auth_data(auth_data)
                
                if success:
                    _LOGGER.warning(f"✅ {self.room} Session established successfully")
                else:
                    _LOGGER.error(f"❌ {self.room} Failed to establish session")
            else:
                _LOGGER.warning(f"⚠️ {self.room} Conditions not met for session establishment")
                
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Handle authenticated error: {e}")

    # =================================================================
    # PREM API
    # =================================================================
    
    async def _send_auth_response(self, event_id: str, status: str, message: str, data: dict = None):
        """Send authentication response."""
        response_data = {
            "event_id": event_id,
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        
        if data:
            response_data["data"] = data
        
        await self.eventManager.emit("ogb_premium_auth_response", response_data, haEvent=True)

    # =================================================================
    # GROW 
    # =================================================================
   
    async def _send_growdata_to_prem_api(self,event):
        mainControl = self.dataStore.get("mainControl")
        if mainControl != "Premium": return

        # Collect grow data
        grow_data = {
            "vpd": self.dataStore.get("vpd"),
            "tentData": self.dataStore.get("tentData"),
            "isLightON": self.dataStore.get("isPlantDay"),
            "devCaps": self.dataStore.get("capabilities"),
            "plantStage": self.dataStore.get("plantStage"),
            "strainName": self.dataStore.get("strainName"),
            "plantDates": self.dataStore.get("plantDates"),
            "workdata": self.dataStore.get("workData"),
            "hydro": self.dataStore.get("Hydro"),
            "Feed": self.dataStore.get("Feed"),
            "plantStages": self.dataStore.get("plantStages"),
            "tentMode": self.dataStore.get("tentMode"),
            "drying":self.dataStore.get("drying"),
            "previousActions":self.dataStore.get("previousActions"),
            "DeviceProfiles":self.dataStore.get("DeviceProfiles"),
            "DeviceMinMax":self.dataStore.get("DeviceMinMax"),
            #"lightPlantStages":self.dataStore.get("lightPlantStages"),
            "controlOptions": self.dataStore.get("controlOptions"),
            "controlOptionData":self.dataStore.get("controlOptionData"),
        }     
 
        success = await self.ogb_ws.prem_event("grow-data", grow_data)
    
        if success:
            _LOGGER.info("Grow data sent successfully")
        else:
            _LOGGER.debug("Failed to send grow data")
    
        return success       
        
    # =================================================================
    # GROW PLANS
    # =================================================================

    async def _ui_get_growplans_request(self,event):
        """Handle get profile event from frontend."""
        event_id = event.data.get("event_id")
        requestingRoom = event.data.get("requestingRoom")
        if self.room.lower() != requestingRoom.lower():
            return

        if not self.is_premium and not self.is_logged_in:
            await self._send_auth_response(event_id, "error", "Not authenticated")
            return

        await self._get_prem_grow_plans(event)

        try:
            if self.ogb_ws.ws_connected == True:
                # Send via available connection
                await self._send_auth_response(event_id, "success", "GrowPlans retrieved", self.growPlanManager.grow_plans)
        except Exception as e:
            _LOGGER.error(f"GET Grow Plan error: {str(e)}")
            await self._send_auth_response(event_id, "error", f"Failed to GET Grow Plans: {str(e)}")

    async def _get_prem_grow_plans(self,event):
        requestingRoom =  event.data.get("requestingRoom")
        event_id = event.data.get("event_id")
        strain_name = self.dataStore.get("strainName")
        
        if strain_name == None or strain_name == "":
            return
        
        _LOGGER.debug(f"Requesting Grow Plans for Strain: {strain_name} from {requestingRoom}")
        planRequestData = {"event_id":event_id,"strain_name":strain_name}
        
        success = await self.ogb_ws.prem_event("get_grow_plans", planRequestData)
        if success:
            await self._send_auth_response(event_id, "success", "GrowPlan Added", self.growPlanManager.grow_plans)
     
    async def _ui_grow_plan_add_request(self,event):
        raw_plan = event.data.get("growPlan")
        event_id = event.data.get("event_id")
        
        if not raw_plan:
            _LOGGER.warning("No 'growPlan' found in event.")
            return

        try:
            grow_plan = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
        except Exception as e:
            _LOGGER.error(f"Failed to decode grow plan JSON: {e}")
            return

        requestingRoom = grow_plan.get("roomKey")
        if requestingRoom.lower() != self.room.lower():
            _LOGGER.debug(f"GrowPlan roomKey '{requestingRoom}' does not match current room '{self.room}'. Ignoring.")
            return

        _LOGGER.debug(f"✅ Adding GrowPlan {grow_plan} Room:{self.room} ")

        if not self.is_logged_in and not self.is_premium:
            _LOGGER.error("Not authenticated. Cannot apply grow plan.")
            return

        success = await self.ogb_ws.prem_event("add_grow_plan", grow_plan)
        _LOGGER.debug("Grow Plan sent successfully" if success else "Failed to send grow Plan")
        await self._send_auth_response(event_id, "success", "GrowPlan Added", True)
        return success

    async def _ui_grow_plan_del_request(self,event):
        pass
        """Handle get profile event from frontend."""
        _LOGGER.warning(f"Hanlde GrowPlan Delete Request From UI: {event}")
        
        mainControl = self.dataStore.get("mainControl")
        if mainControl != "Premium": return
        if self.access_token == None: return

        event_room = event.data.get("room") or ""
        if self.room.lower() != event_room.lower():
            return


        _LOGGER.warning(f"{self.room} Processing delete Grow Plan request {event}")   

        try:
            event_id = event.data.get("event_id")
            
            if not self.is_logged_in and not self.is_premium:
                _LOGGER.warning("Not authenticated. Cannot apply grow plan.")
                return
        
            _LOGGER.info("Processing delete Grow Plan request")

            delPlan = event.data
        
            # Send via available connection
            await self.ogb_ws.prem_event("del_grow_plan", delPlan)
            
            await self._send_auth_response(event_id, "success", "Delete Grow Plan", {

            })

        except Exception as e:
            _LOGGER.error(f"Delete Grow Plan error: {str(e)}")
            await self._send_auth_response(event_id, "error", f"Failed to Delete Grow Plan: {str(e)}")

    async def _ui_grow_plan_activation(self,event):
        """Handle get profile event from frontend."""
      
        requestingRoom = event.data.get("requestingRoom")     
      
        if not self.is_logged_in or not self.is_premium:
            return
       
        if self.room.lower() != requestingRoom.lower():
            return
        
        growPlan = event.data.get("growPlan")
        
        _LOGGER.warning(f"Hanlde GrowPlan Activation Request From UI: {event}") 
        _LOGGER.warning(f"{self.room} Grow Plan to Activate: {growPlan}")  
        try:
            event_id = event.data.get("event_id")
            if event_id:
                await self.ogb_ws.prem_event("grow_plan_activation", growPlan)
                await self.eventManager.emit("plan_activation",growPlan)
                await self._send_auth_response(event_id, "success", "Grow Plan Activated", {})
                await self._save_current_state()
        except Exception as e:
            _LOGGER.error(f"Activate Grow Plan error: {str(e)}")
            await self._send_auth_response(event_id, "error", f"Failed to Activate Grow Plan: {str(e)}")
    
    # =================================================================
    # PREM UI CONTROL
    # =================================================================
   
    async def _handle_ctrl_change(self, data):
        _LOGGER.debug(f"CTRL Change from API : {self.room} ------ {data}")

        if isinstance(data, str):
            # String = TentMode only
            await self._change_ctrl_values(tentmode=data)
        elif isinstance(data, dict):
            # Dict = TentMode (optional) + Controls
            tentmode = data.get("tentMode")
            controls = {k: v for k, v in data.items() if k != "tentMode"}
            await self._change_ctrl_values(controls=controls)
        else:
            _LOGGER.error(f"Unsupported data format: {data}")

    # =================================================================
    # HELPERS
    # =================================================================
    
    async def _change_ctrl_values(self, tentmode=None, controls=dict()):
        # TentMode
        if tentmode != None:
            tent_control = f"select.ogb_tentmode_{self.room.lower()}"
            self.dataStore.set("tentMode", tentmode)
            await self.hass.services.async_call(
                domain="select",
                service="select_option",
                service_data={
                    "entity_id": tent_control,
                    "option": tentmode
                },
                blocking=True
            )
        else:
            # Boolean Controls Mapping
            mapping = {
                "workMode": f"select.ogb_workmode_{self.room.lower()}",
                "co2Control": f"select.ogb_co2_control_{self.room.lower()}",
                "ownWeights": f"select.ogb_ownweights_{self.room.lower()}",
                "nightVPDHold": f"select.ogb_holdvpdnight_{self.room.lower()}",
                "minMaxControl": f"select.ogb_minmax_control_{self.room.lower()}",
                "ambientControl": f"select.ogb_ambientcontrol_{self.room.lower()}",
                "ownDeviceSetup": f"select.ogb_owndevicesets_{self.room.lower()}",
                "vpdLightControl": f"select.ogb_vpdlightcontrol_{self.room.lower()}",
                "lightbyOGBControl": f"select.ogb_lightcontrol_{self.room.lower()}",
            }

            for key, value in controls.items():
                entity_id = mapping.get(key)
                if not entity_id:
                    continue  # Skip unknown keys

                # Update DataStore
                self.dataStore.setDeep(f"controlOptions.{key}", value)

                # Map Boolean to YES/NO
                option_value = "YES" if value else "NO"

                await self.hass.services.async_call(
                    domain="select",
                    service="select_option",
                    service_data={
                        "entity_id": entity_id,
                        "option": option_value
                    },
                    blocking=True
                )

    async def _change_sensor_value(self,type="SET",entity="",value=None):
        
        if value == None:
            return
        
        entity_id = f"{entity}_{self.room.lower()}"
        
        
        if type == "SET":
            await self.hass.services.async_call(
                domain="select",
                service="select_option",
                service_data={
                    "entity_id": entity_id,
                    "option": value
                },
                blocking=True
            )
        elif type == "ADD":
            await self.hass.services.async_call(
                domain="opengrowbox",
                service="add_select_options",
                service_data={
                    "entity_id": entity_id,
                    "options": value
                },
                blocking=True
            )
        elif type == "DEL":
            await self.hass.services.async_call(
                domain="opengrowbox",
                service="remove_select_options",
                service_data={
                    "entity_id": entity_id,
                    "options": value
                },
                blocking=True
            ) 

    async def health_check(self) -> dict:
        """Simplified health check - delegate to WebSocket client"""
        try:
            ws_health = await self.ogb_ws.health_check() if self.ogb_ws else {}
            
            return {
                "room": self.room,
                "manager_ready": self.is_logged_in and self.is_premium,
                "premium_selected": self.is_premium_selected,
                "websocket_health": ws_health,  # All connection details from WS client
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            _LOGGER.error(f"Health check error: {e}")
            return {"error": str(e)}
              
    async def _save_current_state(self):
        """Save current state securely"""
        if self.is_main_auth_room == False:
            return
        
        try:
            # Get session backup data from WebSocket client
            ws_backup = self.ogb_ws.get_session_backup_data()
            
            state_data = {
                "user_id": self.user_id,
                "is_logged_in": self.is_logged_in,
                "is_premium": self.is_premium,
                "room_id": self.room_id,
                "room_name":self.room,
                "subscription_data": self.subscription_data,
                "active_grow_plan":self.growPlanManager.active_grow_plan,
                "ws_data": {
                    "base_url": self.ogb_ws.base_url,
                    "user_id": ws_backup.get("user_id"),
                    "client_id":ws_backup.get("client_id"),
                    "session_id": ws_backup.get("session_id"),
                    "ogb_sessions":ws_backup.get("ogb_sessions"),
                    "ogb_max_sessions":ws_backup.get("ogb_max_sessions"),
                    "is_premium": ws_backup.get("is_premium"),
                    "is_logged_in": ws_backup.get("is_logged_in"),
                    "subscription_data": ws_backup.get("subscription_data"),
                    "session_key": ws_backup.get("session_key"),
                    "access_token": base64.b64encode(
                        self.ogb_ws._access_token.encode('utf-8') if self.ogb_ws._access_token else b''
                    ).decode('utf-8'),
                    "token_expires_at": ws_backup.get("token_expires_at"),
                    "refresh_token":ws_backup.get("refresh_token")
                },
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            
            await _save_state_securely(self.hass, state_data)
            _LOGGER.warning(f"💾 {self.room} State saved securely")
            
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error saving state: {e}")

    async def _managePremiumControls(self):
        tent_control = f"select.ogb_tentmode_{self.room.lower()}"
        drying_modes = f"select.ogb_dryingmodes_{self.room.lower()}"

        ctrl_options = ["OGB Control", "PID Control", "MCP Control", "AI Control"]
        dry_options = ["OGB DRY"]

        current_tent_mode = self.dataStore.get("tentMode")
        invalid_modes = ["AI Control", "MCP Control", "PID Control", "OGB Control"]

        if not self.is_premium and not self.is_logged_in:
            for entity_id, options in [
                (tent_control, ctrl_options),
                (drying_modes, dry_options)
            ]:
                if current_tent_mode in invalid_modes:
                    await self.hass.services.async_call(
                        domain="select",
                        service="select_option",
                        service_data={
                            "entity_id": tent_control,
                            "option": "VPD Perfection"
                        },
                        blocking=True
                    )
                    await self.hass.services.async_call(
                        domain="opengrowbox",
                        service="remove_select_options",
                        service_data={
                            "entity_id": entity_id,
                            "options": options
                        },
                        blocking=True
                    )
        else:
            for entity_id, options in [
                (tent_control, ctrl_options),
                (drying_modes, dry_options)
            ]:
                await self.hass.services.async_call(
                    domain="opengrowbox",
                    service="add_select_options",
                    service_data={
                        "entity_id": entity_id,
                        "options": options
                    },
                    blocking=True
                )

    # =================================================================
    # UTILITYS
    # =================================================================

    async def _save_request(self,event):
        if self.is_main_auth_room == False:
            return
        logging.warning(f"{self.room} Saving New State after Rotation")
        await self._save_current_state()
   
    async def _get_or_create_room_id(self):
        subdir = self.hass.config.path(".ogb_premium")
        os.makedirs(subdir, exist_ok=True)

        filename = f"ogb_{self.room}_room_id.txt"
        room_id_path = os.path.join(subdir, filename)

        def read_room_id():
            if os.path.exists(room_id_path):
                with open(room_id_path, 'r') as f:
                    return f.read().strip()
            return None

        room_id = await asyncio.to_thread(read_room_id)

        if room_id:
            self.room_id = room_id
            _LOGGER.debug(f"📁 Current Device-ID loaded for Room  {self.room}: {room_id}")
            return room_id

        # Neue ID erzeugen
        room_id = str(uuid.uuid4())

        def write_room_id():
            with open(room_id_path, 'w') as f:
                f.write(room_id)

        await asyncio.to_thread(write_room_id)

        _LOGGER.debug(f"🆕 New Device-ID Created for Room {self.room}: {room_id}")
        self.room_id = room_id
        self.ogb_ws._room_id = room_id
        return room_id

    def _check_if_premium_selected(self):
        """Check if Premium mode is currently enabled."""
        return self.dataStore.get("mainControl") == "Premium" 

    def _check_if_premium_control_active(self):
        """Check if Premium mode is currently enabled."""
        if self.ogb_ws.subscription_data.get("plan_name") in ["free", "basic"]:
            return False
        return True

    def _check_if_can_connect(self):
        return self.ogb_ws.ogb_sessions < self.ogb_ws.ogb_max_sessions

    async def _handle_premium_change(self, data):
        """Handle premium mode changes"""
        current = data.get("currentValue")
        previous = data.get("lastValue")

        if previous != "Premium" and current == "Premium":
            await self._on_premium_selected()
        elif previous == "Premium" and current != "Premium":
            await self._on_premium_deselected()
            
    async def _on_premium_selected(self):
        """Handle Premium mode activation - Simplified"""
        try:
            _LOGGER.debug(f"Premium mode activated for {self.room}")
            self.is_premium_selected = True

            # If already authenticated, let WebSocket client handle reconnection
            if self.is_logged_in and self.is_premium:

                if self.ogb_ws.ws_connected is True:
                    logging.debug(f"{self.room} is already connected over WS")
                    await self._managePremiumControls() 

                    return   
               
                if not self.ogb_ws.is_connected():
                    _LOGGER.warning(f"Triggering WebSocket connection for {self.room}")

                    if not self._check_if_can_connect():
                        logging.warning(f"{self.ws_room} TOO MANY ROOMS - REMOVE ONE FROM PREMIUM AND ADD THE NEW ON ROOMS: {self.ogb_ws.ogb_sessions} MAX: {self.ogb_ws.ogb_max_sessions}")

                        await self._send_auth_response(
                            "error",
                            "to_many_rooms",
                            {
                                "success": "false",
                                "MSG": "Cannot activate this room in Premium. Reason: Too many rooms."
                            }
                        )
                        return

                    success = await self.ogb_ws._connect_websocket()
                    if success:
                        await self._managePremiumControls() 


        except Exception as e:
            _LOGGER.error(f"Premium selection error: {e}")

    async def _on_premium_deselected(self):
        """Handle Premium mode deactivation - Simplified"""
        try:
            _LOGGER.warning(f"Premium mode deactivated for {self.room}")
            self.is_premium_selected = False
            
            # Just disconnect - no complex monitoring cleanup needed
            if self._check_if_premium_control_active():
                await self._managePremiumControls()
                
            await self.ogb_ws.disconnect()

        except Exception as e:
            _LOGGER.error(f"Premium deselection error: {e}")
            
    async def _cleanup_auth(self, event_id):
        """Simplified cleanup - let WebSocket client handle its own cleanup"""
        try:
            _LOGGER.warning(f"Starting authentication cleanup for {self.room}")
            
            # Reset manager state only
            self.is_premium_selected = False
            self.is_logged_in = False
            self.is_premium = False
            self.user_id = None
            self.subscription_data = None
            self.has_control_prem = False
            
            self.growPlanManager.active_grow_plan = None

            # Reset UI if needed
            if self._check_if_premium_selected():
                await self._change_sensor_value("SET", "select.ogb_maincontrol", "HomeAssistant")
            
            # Let WebSocket client handle its own cleanup
            await self.ogb_ws.cleanup_prem(event_id)
            
            # Remove state file
            try:
                await _remove_state_file(self.hass)
            except Exception as e:
                _LOGGER.error(f"Error removing token file: {e}")
                
            _LOGGER.warning(f"Authentication cleanup completed for {self.room}")
            
        except Exception as e:
            _LOGGER.error(f"Cleanup auth error: {e}")
        """Cleanup authentication data."""
        try:
            _LOGGER.warning(f"🧹 {self.room} Starting authentication cleanup")
            
            if self.ogb_ws.ws_connected:
                await self.ogb_ws.disconnect()
            
            # Main Control Status Reset
            self.is_premium_selected = False
            self.is_logged_in = False
            self.is_premium = False
            self.has_control_prem = False
            
            #User Data
            self.user_id = None
            self.subscription_data  = None
            
            # GrowPlans 
            self.growPlanManager.active_grow_plan = None


            if self._check_if_premium_selected():
                await self._change_sensor_value("SET","select.ogb_maincontrol","HomeAssistant")
        

            await self.ogb_ws.cleanup_prem(event_id)
            
            try:
                if self.is_main_auth_room:
                    await _remove_state_file(self.hass)
            except Exception as e:
                _LOGGER.error(f"Error removing token file: {e}")
                
            _LOGGER.warning(f"✅ {self.room} Authentication cleanup completed")
            
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Cleanup auth error: {e}")
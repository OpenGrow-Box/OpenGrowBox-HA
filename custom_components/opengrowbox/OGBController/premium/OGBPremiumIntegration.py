"""
OpenGrowBox Premium Integration Manager

Complete modular replacement for the monolithic OGBPremManager.py.
Handles all premium functionality including:
- Authentication (login/logout)
- WebSocket connection management
- State persistence
- Feature service
- Grow plan management
- Premium controls
"""

import asyncio
import base64
import json
import time
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ...const import DOMAIN, PREM_WS_API
from ..utils.Premium.ogb_state import (
    _load_state_securely,
    _remove_state_file,
    _save_state_securely,
)
from ..utils.Premium.SecureWebSocketClient import OGBWebSocketConManager
from .analytics.OGBPremAnalytics import OGBPremAnalytics
from .analytics.OGBPremCompliance import OGBPremCompliance
from .analytics.OGBPremResearch import OGBPremResearch
from .features.OGBPremFeatureManager import OGBFeatureManager
from .growplans.OGBGrowPlanManager import OGBGrowPlanManager

_LOGGER = logging.getLogger(__name__)


class OGBPremiumIntegration:
    """
    Complete Premium Integration Manager.
    
    Replaces the monolithic OGBPremManager with a modular architecture
    while maintaining full backwards compatibility.
    """

    def __init__(self, hass, data_store, event_manager, room):
        """
        Initialize the premium integration manager.

        Args:
            hass: Home Assistant instance
            data_store: Data store instance
            event_manager: Event manager instance
            room: Room identifier
        """
        self.name = "OGB Premium Manager"
        self.hass = hass
        self.room = room
        self.data_store = data_store
        self.event_manager = event_manager

        # Authentication state
        self.ogb_login_email = None
        self.ogb_login_token = None
        self.access_token = None
        self.room_id = None

        # Status flags
        self.is_premium_selected = False
        self.is_logged_in = False
        self.is_premium = False
        self.has_control_prem = False
        self._login_in_progress = False
        self.is_primary_auth_room = False  # True if this room did the original login

        # User data
        self.user_id = None
        self.tenant_id = None
        self.subscription_data = None
        self._is_initialized = False
        self._init_lock = asyncio.Lock()

        # Data control
        self.lastTentMode = None

        # Premium sensors registry
        self._premium_sensors = {}

        # Feature manager for subscription-based feature access control
        self.feature_manager: Optional[OGBFeatureManager] = None

        # Initialize WebSocket client - use shared client if available
        self.ogb_ws = None
        # No shared WebSocket clients - each room has its own

        # Initialize grow plan manager (will be configured after ws client)
        self.growPlanManager = None

        # Set up event listeners
        self._setup_event_listeners()

        # Start async initialization
        asyncio.create_task(self._safe_init())

    @property
    def is_ready(self):
        """Check if manager is ready."""
        return self._is_initialized and self.ogb_ws is not None

    async def _safe_init(self):
        """Safe initialization with error handling."""
        _LOGGER.info(f"🔄 {self.room} Starting Premium Manager initialization...")
        async with self._init_lock:
            try:
                await self.init()
                self._is_initialized = True
                _LOGGER.info(f"✅ {self.room} Premium Manager initialized successfully. ogb_ws={self.ogb_ws is not None}")
            except Exception as e:
                _LOGGER.error(f"❌ {self.room} Premium Manager init failed: {e}", exc_info=True)
                # Ensure we still set ogb_ws even on partial failure
                if self.ogb_ws is None:
                    _LOGGER.error(f"❌ {self.room} ogb_ws is None after init failure!")

    async def _check_global_premium_limits(self) -> bool:
        """Check if this room can initialize premium services based on global limits."""
        try:
            # Get all coordinators to check active premium connections
            domain_data = self.hass.data.get(DOMAIN, {})
            active_premium_connections = 0
            blocked_rooms = []

            for entry_id, coordinator in domain_data.items():
                if entry_id == "_premium_services_registered":
                    continue  # Skip the service registration flag

                # Check if this coordinator has an active premium integration
                if hasattr(coordinator, 'premium_integration') and coordinator.premium_integration:
                    prem_integration = coordinator.premium_integration
                    room_name = getattr(prem_integration, 'room', 'unknown')

                    # Count active connections
                    if (prem_integration.is_logged_in and
                        prem_integration.ogb_ws and
                        prem_integration.ogb_ws.ws_connected):
                        active_premium_connections += 1
                        _LOGGER.info(f"📊 Active connection: {room_name}")
                    elif hasattr(prem_integration, '_initialization_blocked') and prem_integration._initialization_blocked:
                        blocked_rooms.append(room_name)

            # Get plan limits (default to FREE plan limits)
            plan_name = "free"  # Default assumption
            max_connections = 1  # FREE plan default

            # If we have subscription data, use actual plan limits
            if hasattr(self, 'subscription_data') and self.subscription_data:
                plan_name = self.subscription_data.get("plan_name", "free")
                limits = self.subscription_data.get("limits", {})
                max_connections = limits.get("max_sessions") or limits.get("max_concurrent_connections") or 1

                # FREE plan always limited to 1 connection
                if plan_name == "free":
                    max_connections = 1

            _LOGGER.warning(f"🔍 {self.room} Plan: {plan_name}, Active connections: {active_premium_connections}, Max allowed: {max_connections}")
            if blocked_rooms:
                _LOGGER.warning(f"🚫 Blocked rooms: {', '.join(blocked_rooms)}")

            if active_premium_connections >= max_connections:
                _LOGGER.warning(f"⚠️ {self.room} BLOCKED - {active_premium_connections} connections active, limit is {max_connections}")
                # Mark this integration as blocked
                self._initialization_blocked = True
                return False

            _LOGGER.warning(f"✅ {self.room} ALLOWED - proceeding with initialization")
            return True

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error checking premium limits: {e}")
            import traceback
            _LOGGER.error(f"📋 Traceback: {traceback.format_exc()}")
            # Allow initialization on error to avoid blocking
            return True

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error checking premium limits: {e}")
            # Allow initialization on error to avoid blocking
            return True

    async def init(self):
        """Initialize Premium Manager."""
        # Check global premium connection limits before initializing
        if not await self._check_global_premium_limits():
            _LOGGER.warning(f"⚠️ {self.room} Premium initialization blocked - plan limits exceeded")
            return

        # Get or create room ID first
        await self._get_or_create_room_id()

        # Initialize independent WebSocket client for this room
        # Each room gets its own WebSocket connection (no sharing)
        self.ogb_ws = OGBWebSocketConManager(
            PREM_WS_API,
            self.event_manager,  # eventManager
            self.room,           # ws_room (unique per room)
            self.room_id,        # room_id (unique per room)
        )
        self.is_primary_ws_client = True  # Always true since no sharing
        self._datarelease_counter = 0  # Track DataRelease events
        self._last_datarelease_time = 0  # Debounce DataRelease events
        self._datarelease_debounce_seconds = 5  # Minimum seconds between DataRelease sends
        self._initialization_blocked = False  # Track if initialization was blocked by limits
        _LOGGER.info(f"🔗 {self.room} Created independent WebSocket client")

        # Initialize grow plan manager
        self.growPlanManager = OGBGrowPlanManager(
            self.hass, self.data_store, self.event_manager, self.room
        )
        self.growPlanManager.set_ws_client(self.ogb_ws)

        # Initialize analytics modules
        self.analytics = OGBPremAnalytics(api_proxy=None, cache=None)  # TODO: Add proper api_proxy and cache
        self.compliance = OGBPremCompliance(api_proxy=None, cache=None)  # TODO: Add proper api_proxy and cache
        self.research = OGBPremResearch(api_proxy=None, cache=None)  # TODO: Add proper api_proxy and cache

        # Load saved state
        await self._load_last_state()

        # Wait for WebSocket to be ready (with timeout)
        for _ in range(20):
            if self.ogb_ws.is_connected:
                break
            await asyncio.sleep(0.1)

        _LOGGER.debug(f"OGBPremiumManager initialized for room: {self.room}")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        # Authentication events
        self.hass.bus.async_listen("ogb_premium_devlogin", self._on_prem_dev_login)
        self.hass.bus.async_listen("ogb_premium_login", self._on_prem_login)
        self.hass.bus.async_listen("ogb_premium_logout", self._on_prem_logout)
        self.hass.bus.async_listen("ogb_premium_get_profile", self._get_user_profile)

        # GrowPlan handlers
        self.hass.bus.async_listen("ogb_premium_get_growplans", self._ui_get_growplans_request)
        self.hass.bus.async_listen("ogb_premium_add_growplan", self._ui_grow_plan_add_request)
        self.hass.bus.async_listen("ogb_premium_del_growplan", self._ui_grow_plan_del_request)
        self.hass.bus.async_listen("ogb_premium_growplan_activate", self._ui_grow_plan_activation)

        # Feature Service handlers (for frontend featureService.js)
        self.hass.bus.async_listen("ogb_features_get_subscription", self._handle_get_subscription)
        self.hass.bus.async_listen("ogb_features_get_available", self._handle_get_available_features)
        self.hass.bus.async_listen("ogb_features_check_access", self._handle_check_feature_access)

        # V1 WebSocket event handlers
        self.event_manager.on("AnalyticsUpdate", self._handle_analytics_update)
        self.hass.bus.async_listen("ogb_features_get_definitions", self._handle_get_feature_definitions)

        # Internal event manager events
        self.event_manager.on("DataRelease", self._send_growdata_to_prem_api)
        self.event_manager.on("PremiumChange", self._handle_premium_change)
        self.event_manager.on("SaveRequest", self._save_request)
        self.event_manager.on("PremUICTRLChange", self._handle_ctrl_change)

        # WebSocket events from Premium API
        self.event_manager.on("FeatureFlagUpdated", self._on_feature_flag_updated)
        self.event_manager.on("KillSwitchActivated", self._on_kill_switch_activated)
        self.event_manager.on("SubscriptionChanged", self._on_subscription_changed)

        # Grow Plan events from WebSocket
        self.event_manager.on("new_grow_plans", self._on_new_grow_plans)
        self.event_manager.on("plan_activation", self._on_plan_activated)

        # API Usage updates from WebSocket - CRITICAL for browser refresh to show current values
        self.event_manager.on("api_usage_update", self._on_api_usage_update)

        # Grow completion events - send harvest data to Premium API
        self.event_manager.on("GrowCompleted", self._on_grow_completed)

        # Cross-room authentication
        self.hass.bus.async_listen("isAuthenticated", self._handle_authenticated)

    # =================================================================
    # WebSocket Event Handlers (Premium API Real-time Updates)
    # =================================================================

    async def _on_feature_flag_updated(self, data):
        """Handle feature flag updates from Premium API."""
        try:
            feature_key = data.get("feature_key")
            enabled = data.get("enabled")
            reason = data.get("reason", "unknown")

            _LOGGER.info(f"🎛️ {self.room} Feature '{feature_key}' = {enabled} (reason: {reason})")

            # Update subscription_data for feature checks
            if not self.subscription_data:
                self.subscription_data = {}
            if "features" not in self.subscription_data:
                self.subscription_data["features"] = {}

            self.subscription_data["features"][feature_key] = enabled

            # Update WebSocket client's subscription data
            if self.ogb_ws:
                self.ogb_ws.subscription_data = self.subscription_data

            # Update datastore for feature checks by other managers (ModeManager, etc.)
            self.data_store.set("subscriptionData", self.subscription_data)
            
            # Update feature manager with new feature flag
            if self.feature_manager:
                self.feature_manager.update_override(feature_key, enabled)
            else:
                self._update_feature_manager()
            
            # Refresh premium controls (add/remove modes based on new features)
            await self._managePremiumControls()

            # Fire HA event for UI notification
            self.hass.bus.async_fire("ogb_premium_feature_updated", {
                "room": self.room,
                "feature": feature_key,
                "enabled": enabled,
                "reason": reason
            })

            # Save updated state
            await self._save_request(True)

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Feature flag update error: {e}")

    async def _on_kill_switch_activated(self, data):
        """Handle emergency feature disable from Premium API."""
        try:
            feature_key = data.get("feature_key")
            reason = data.get("reason", "Emergency disable")

            _LOGGER.warning(f"🚨 {self.room} KILL SWITCH: Feature '{feature_key}' DISABLED - {reason}")

            # Disable feature immediately
            if self.subscription_data and "features" in self.subscription_data:
                self.subscription_data["features"][feature_key] = False

            # Update datastore for feature checks by other managers
            self.data_store.set("subscriptionData", self.subscription_data)

            # Update WebSocket client
            if self.ogb_ws:
                self.ogb_ws.subscription_data = self.subscription_data
            
            # Update feature manager with kill switch
            if self.feature_manager:
                self.feature_manager.update_override(feature_key, False)
            else:
                self._update_feature_manager()

            # Fire HA alert event
            self.hass.bus.async_fire("ogb_premium_alert", {
                "type": "kill_switch",
                "severity": "critical",
                "room": self.room,
                "feature": feature_key,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            # If currently using this feature mode, switch to safe mode
            current_mode = self.data_store.get("tentMode")
            if feature_key == "ai_controllers" and current_mode == "AI Control":
                _LOGGER.warning(f"⚠️ {self.room} Switching from AI Control to VPD Perfection (kill switch)")
                await self._change_ctrl_values(tentmode="VPD Perfection")
            elif feature_key == "pid_controllers" and current_mode == "PID Control":
                _LOGGER.warning(f"⚠️ {self.room} Switching from PID Control to VPD Perfection (kill switch)")
                await self._change_ctrl_values(tentmode="VPD Perfection")
            elif feature_key == "mpc_controllers" and current_mode == "MPC Control":
                _LOGGER.warning(f"⚠️ {self.room} Switching from MPC Control to VPD Perfection (kill switch)")
                await self._change_ctrl_values(tentmode="VPD Perfection")
            
            # Refresh premium controls to remove disabled mode from UI
            await self._managePremiumControls()

            # Save state
            await self._save_request(True)

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Kill switch handler error: {e}")

    async def _on_subscription_changed(self, data):
        """Handle subscription plan change from Premium API."""
        try:
            old_plan = data.get("old_plan")
            new_plan = data.get("new_plan")

            _LOGGER.info(f"📊 {self.room} Subscription changed: {old_plan} → {new_plan}")

            # Update subscription data
            if "features" in data:
                if not self.subscription_data:
                    self.subscription_data = {}
                self.subscription_data["features"] = data["features"]

            if "limits" in data:
                if not self.subscription_data:
                    self.subscription_data = {}
                self.subscription_data["limits"] = data["limits"]

            if new_plan:
                if not self.subscription_data:
                    self.subscription_data = {}
                self.subscription_data["plan_name"] = new_plan

            # Update WebSocket client
            if self.ogb_ws:
                self.ogb_ws.subscription_data = self.subscription_data
                self.ogb_ws.is_premium = new_plan not in ["free", "trial"]

            # Update local premium status
            self.is_premium = new_plan not in ["free", "trial"]
            
            # Update feature manager with new subscription data
            self._update_feature_manager()

            # Fire HA event
            self.hass.bus.async_fire("ogb_premium_subscription_changed", {
                "room": self.room,
                "old_plan": old_plan,
                "new_plan": new_plan,
                "features": data.get("features", {}),
                "limits": data.get("limits", {}),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

            # Update UI controls based on new plan features
            await self._managePremiumControls()

            # Save state
            await self._save_request(True)

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Subscription change error: {e}")

    async def _on_api_usage_update(self, data):
        """Handle API usage updates from WebSocket.
        
        CRITICAL: This updates subscription_data.usage so that when the frontend
        refreshes and requests auth status, it gets the CURRENT usage values
        instead of the stale values from the initial login.
        """
        try:
            # Extract usage data - may be nested or flat
            usage = data.get("usage", data)
            
            _LOGGER.debug(f"📊 {self.room} API usage update received: {usage}")
            
            # Update subscription_data with current usage values
            if not self.subscription_data:
                self.subscription_data = {}
            
            if "usage" not in self.subscription_data:
                self.subscription_data["usage"] = {}
            
            # Update all usage fields
            self.subscription_data["usage"]["roomsUsed"] = usage.get("roomsUsed", 0)
            self.subscription_data["usage"]["growPlansUsed"] = usage.get("growPlansUsed", 0)
            self.subscription_data["usage"]["apiCallsThisMonth"] = usage.get("apiCallsThisMonth", 0)
            self.subscription_data["usage"]["storageUsedGB"] = usage.get("storageUsedGB", 0)
            self.subscription_data["usage"]["activeConnections"] = usage.get("activeConnections", 0)
            self.subscription_data["usage"]["activeRooms"] = usage.get("activeRooms", [])
            
            # Sync with WebSocket client's subscription_data
            if self.ogb_ws:
                self.ogb_ws.subscription_data = self.subscription_data
            
            _LOGGER.debug(f"📊 {self.room} subscription_data.usage updated: apiCallsThisMonth={usage.get('apiCallsThisMonth', 0)}")
            
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} API usage update error: {e}")

    async def _on_grow_completed(self, data):
        """
        Handle grow completion events from MediumManager.
        
        Sends harvest/completion data to Premium API for:
        - Harvest history tracking
        - Analytics and insights
        - Compliance records (if enabled)
        
        Args:
            data: Dict containing harvest data:
                - room: Room name
                - medium_index: Index of the medium
                - medium_name: Name of the medium
                - plant_name: Name of the plant
                - breeder_name: Strain/breeder name
                - grow_start_date: ISO date string
                - bloom_switch_date: ISO date string  
                - harvest_date: ISO date string
                - total_days: Total grow days
                - bloom_days: Days in bloom
                - final_readings: Final sensor values
                - notes: Optional harvest notes
        """
        try:
            # Only process for this room
            if data.get("room") != self.room:
                return
            
            _LOGGER.info(f"🏁 {self.room} Grow completed event received")
            
            # Only send to API if logged in and premium
            if not self.is_logged_in or not self.ogb_ws:
                _LOGGER.debug(f"📊 {self.room} Not logged in to Premium, skipping API submission")
                return
            
            # Prepare harvest data for API
            harvest_payload = {
                "event_type": "grow_completed",
                "room_id": self.room_id,
                "room_name": self.room,
                "tenant_id": self.tenant_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                # Grow details
                "medium_index": data.get("medium_index"),
                "medium_name": data.get("medium_name"),
                "medium_type": data.get("medium_type"),
                # Plant details
                "plant_name": data.get("plant_name"),
                "breeder_name": data.get("breeder_name"),
                "plant_type": data.get("plant_type"),
                # Dates
                "grow_start_date": data.get("grow_start_date"),
                "bloom_switch_date": data.get("bloom_switch_date"),
                "harvest_date": data.get("harvest_date"),
                # Duration
                "total_days": data.get("total_days"),
                "bloom_days": data.get("bloom_days"),
                "breeder_bloom_days": data.get("breeder_bloom_days"),
                # Final readings
                "final_readings": data.get("final_readings"),
                # Notes
                "notes": data.get("notes"),
            }
            
            _LOGGER.info(f"📤 {self.room} Sending grow completion to Premium API: {harvest_payload.get('plant_name')}")
            
            # Send to Premium API via WebSocket
            try:
                success = await self.ogb_ws.send_encrypted_message("grow-completed", harvest_payload)
                if success:
                    _LOGGER.info(f"✅ {self.room} Grow completion data sent to Premium API")
                else:
                    _LOGGER.warning(f"⚠️ {self.room} Failed to send grow completion to Premium API")
            except Exception as ws_error:
                _LOGGER.error(f"❌ {self.room} WebSocket error sending grow completion: {ws_error}")
            
            # Fire HA event for UI notification
            self.hass.bus.async_fire("ogb_grow_completed", {
                "room": self.room,
                "plant_name": data.get("plant_name"),
                "total_days": data.get("total_days"),
                "bloom_days": data.get("bloom_days"),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error handling grow completion: {e}", exc_info=True)

    async def _on_new_grow_plans(self, data):
        """Handle new grow plans received from Premium API."""
        try:
            plans = data.get("plans", [])
            _LOGGER.info(f"📅 {self.room} Received {len(plans)} grow plans from API")

            # Forward to grow plan manager
            if self.growPlanManager:
                await self.growPlanManager.update_available_plans(plans)

                # Fire HA event for UI update
                self.hass.bus.async_fire("ogb_premium_growplans_updated", {
                    "room": self.room,
                    "plan_count": len(plans),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            else:
                _LOGGER.warning(f"⚠️ {self.room} GrowPlanManager not initialized, cannot update plans")

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} New grow plans error: {e}")

    async def _on_plan_activated(self, growPlan):
        """Handle grow plan activation from Premium API."""
        try:
            # Handle both dict and object formats
            if isinstance(growPlan, dict):
                plan_id = growPlan.get("plan_id")
                plan_name = growPlan.get("plan_name", "Unknown")
            else:
                plan_id = getattr(growPlan, "plan_id", None)
                plan_name = getattr(growPlan, "plan_name", "Unknown")

            _LOGGER.info(f"✅ {self.room} Grow plan activated: {plan_name} (ID: {plan_id})")

            # Forward to grow plan manager
            if self.growPlanManager:
                await self.growPlanManager.activate_plan(growPlan)

                # Fire HA event
                self.hass.bus.async_fire("ogb_premium_growplan_activated", {
                    "room": self.room,
                    "plan_id": plan_id,
                    "plan_name": plan_name,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            else:
                _LOGGER.warning(f"⚠️ {self.room} GrowPlanManager not initialized, cannot activate plan")

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Plan activation error: {e}")

    # =================================================================
    # State Loading & Restoration
    # =================================================================

    async def _load_last_state(self):
        """Load saved state and restore connection.

        IMPORTANT: Only the room that was originally authenticated (is_primary_auth_room=True)
        should attempt to restore/re-login. Other rooms will receive auth data via events.
        """
        state_file_path = self.hass.config.path(f".ogb_premium/ogb_premium_state_{self.room.lower()}.enc")
        _LOGGER.debug(f"🚀 {self.room} Checking for saved state at: {state_file_path}")
        state_data = await _load_state_securely(self.hass, self.room)

        if not state_data:
            _LOGGER.debug(f"{self.room} No saved state found (first run or after logout)")
            return False

        # Additional safety check - ensure state_data is a dict
        if not isinstance(state_data, dict):
            _LOGGER.error(f"❌ {self.room} Invalid state_data type: {type(state_data)}, expected dict")
            return False

        # Migration: Fix corrupted state files with null subscription_data
        if "subscription_data" in state_data and state_data["subscription_data"] is None:
            _LOGGER.warning(f"⚠️ {self.room} Found null subscription_data in state, fixing...")
            state_data["subscription_data"] = {}
            # Save the fixed state back to prevent future issues
            try:
                await _save_state_securely(self.hass, state_data, self.room)
                _LOGGER.info(f"✅ {self.room} Fixed corrupted state file")
            except Exception as e:
                _LOGGER.warning(f"⚠️ {self.room} Could not save fixed state: {e}")

        restoring_room = state_data.get("room_name")

        if self.room != restoring_room:
            _LOGGER.error(f"❌ {self.room} State file room mismatch: expected {self.room}, got {restoring_room}")
            return False

        # Check if this room is the primary authenticated room
        is_primary_auth_room = state_data.get("is_primary_auth_room", False)

        try:
            user_id = state_data.get("user_id", "unknown")
            subscription_data = state_data.get("subscription_data") or {}
            # subscription_data is now guaranteed to be a dict
            plan_name = subscription_data.get("plan_name", "unknown")
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error loading state data: {e}")
            return False

        _LOGGER.debug(
            f"✅ {self.room} Restoring state: "
            f"user={user_id[:8] if user_id and user_id != 'unknown' else 'none'}, "
            f"plan={plan_name}, "
            f"is_primary={is_primary_auth_room}"
        )

        # Restore Manager State
        self.is_logged_in = state_data.get("is_logged_in", False)
        self.is_premium = state_data.get("is_premium", False)
        self.user_id = state_data.get("user_id", None)
        self.subscription_data = state_data.get("subscription_data") or {}
        self.lastTentMode = state_data.get("lastTentMode", None)
        self.ogb_login_token = state_data.get("ogb_login_token", None)
        self.ogb_login_email = state_data.get("ogb_login_email", None)
        self.is_premium_selected = state_data.get("is_premium_selected", False)
        self.is_primary_auth_room = is_primary_auth_room

        # Initialize feature manager with restored subscription data
        if self.subscription_data:
            self._update_feature_manager()

        # GrowPlan manager
        if self.growPlanManager:
            self.growPlanManager.managerActive = state_data.get("growmanager_state", None)
            self.data_store.set("growManagerActive", self.growPlanManager.managerActive)
            self.growPlanManager.active_grow_plan_id = state_data.get("active_grow_plan_id", None)

        # Restore WebSocket state if present
        ws_data = state_data.get("ws_data")
        if ws_data and self.is_logged_in and self.ogb_ws:
            try:
                _LOGGER.debug(f"{self.room} Restoring WS_DATA")

                # Restore WebSocket client data including session info
                # Session key will be re-requested if stale during connection
                self.ogb_ws._user_id = ws_data.get("user_id", None)
                self.ogb_ws.client_id = ws_data.get("client_id", None)
                self.ogb_ws.is_premium = ws_data.get("is_premium", False)
                self.ogb_ws.is_logged_in = ws_data.get("is_logged_in", False)
                self.ogb_ws.authenticated = ws_data.get("authenticated", False)
                self.ogb_ws.subscription_data = ws_data.get("subscription_data") or {}
                # Restore session_id/session_key - will be refreshed if stale
                self.ogb_ws._session_id = ws_data.get("session_id", None)
                # Note: session_key is stored as base64 in backup
                self.ogb_ws.ogb_sessions = ws_data.get("ogb_sessions", 0)
                self.ogb_ws.ogb_max_sessions = ws_data.get("ogb_max_sessions", 0)
                self.data_store.set("strainName", state_data.get("strain_name", None))

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

                # Prepare session data
                session_data = {}
                if ws_data.get("session_id"):
                    session_data["session_id"] = ws_data.get("session_id")
                if ws_data.get("session_key"):
                    session_data["session_key"] = ws_data.get("session_key")

                session_data.update({
                    "user_id": self.ogb_ws._user_id,
                    "access_token": self.ogb_ws._access_token,
                    "token_expires_at": self.ogb_ws.token_expires_at,
                    "is_logged_in": self.ogb_ws.is_logged_in,
                    "is_premium": self.ogb_ws.is_premium,
                    "subscription_data": self.ogb_ws.subscription_data,
                    "room_name": self.ogb_ws.ws_room,
                    "room_id": self.room_id
                })

                _LOGGER.debug(f"🔄 {self.room} Restoring WebSocket session for user {self.ogb_ws._user_id[:8] if self.ogb_ws._user_id else 'unknown'}")

                success = None

                # Only try to restore if this room had premium selected
                if not self.is_premium_selected:
                    _LOGGER.debug(f"⏭️ {self.room} Premium not selected, skipping WebSocket restore")
                    return True  # Not an error, just not needed

                # Try direct WebSocket reconnection first (will auto-request session key if needed)
                _LOGGER.info(f"🔄 {self.room} Attempting direct WebSocket reconnection...")
                success = await self.ogb_ws._connect_websocket()

                if success:
                    _LOGGER.info(f"✅ {self.room} WebSocket session restored successfully via direct reconnection")
                    
                    # CRITICAL FIX: Wait for authentication flag to be set
                    # The v1:session:confirmed event sets this flag asynchronously
                    await asyncio.sleep(1.0)  # Give time for auth flag update
                    
                    # Verify authentication succeeded
                    if not self.ogb_ws.authenticated:
                        _LOGGER.warning(f"⚠️ {self.room} WebSocket connected but not authenticated, waiting...")
                        # Wait up to 5 seconds for authentication
                        for _ in range(5):
                            await asyncio.sleep(1.0)
                            if self.ogb_ws.authenticated:
                                _LOGGER.info(f"✅ {self.room} Authentication confirmed after wait")
                                break
                        
                        if not self.ogb_ws.authenticated:
                            _LOGGER.error(f"❌ {self.room} WebSocket authentication failed after reconnect - will try fallback login")
                            success = False  # Mark as failed to trigger fallback
                    
                    if success:  # Only proceed if authenticated
                        strain_name = state_data.get("strain_name", None)
                        planRequestData = {"event_id": "starting_event", "strain_name": strain_name}
                        await self.ogb_ws.prem_event("get_grow_plans", planRequestData)
                        await self._send_auth_to_other_rooms()
                        await self._broadcast_restored_state()
                        return True
                
                # Direct reconnection failed - fallback to full login with stored credentials
                _LOGGER.warning(f"⚠️ {self.room} Direct reconnection failed, trying fresh login with stored credentials...")
                
                if not self.ogb_login_email or not self.ogb_login_token:
                    _LOGGER.error(f"❌ {self.room} No stored credentials for fallback login")
                    return False
                
                success = await self.ogb_ws.login_and_connect(
                    email=self.ogb_login_email,
                    OGBToken=self.ogb_login_token,
                    room_id=self.room_id or "",
                    room_name=self.room,
                    event_id="RestoreLogin",
                    auth_callback=self._send_auth_response
                )

                if success:
                    _LOGGER.info(f"✅ {self.room} Premium session restored via fresh login")
                    strain_name = state_data.get("strain_name", None)
                    planRequestData = {"event_id": "starting_event", "strain_name": strain_name}
                    await self.ogb_ws.prem_event("get_grow_plans", planRequestData)
                    await self._send_auth_to_other_rooms()
                    await self._broadcast_restored_state()
                else:
                    _LOGGER.error(f"❌ {self.room} Both reconnection methods failed during restore")

                return success

            except Exception as e:
                _LOGGER.error(f"❌ {self.room} Error during state restore: {e}")
                return False

        return True

    # =================================================================
    # User Profile
    # =================================================================

    async def _get_user_profile(self, event):
        """Get current premium status and login state (handles browser refresh)."""
        try:
            if self.room != event.data.get("room"):
                return

            event_id = event.data.get("event_id")

            # ALWAYS send a response, whether logged in or not (fixes browser refresh issue)
            if not self.is_logged_in:
                # User not logged in - send empty profile
                _LOGGER.debug(f"📭 {self.room} Profile requested but not logged in - sending empty profile")
                await self._send_auth_response(event_id, "success", "Profile retrieved", {
                    "user": None,  # UI checks for this to determine login state
                    "is_premium": False,
                    "is_logged_in": False,
                    "currentPlan": "free",
                })
                return

            # User IS logged in - send full profile with user object
            _LOGGER.debug(f"📋 {self.room} Profile requested - sending logged-in user data")
            
            connection_info = self.ogb_ws.get_connection_info() if self.ogb_ws else {}
            health = await self.ogb_ws.health_check() if self.ogb_ws else {}

            await self._send_auth_response(event_id, "success", "Profile retrieved", {
                # UI expects 'user' object to exist when logged in
                "user": {
                    "user_id": self.user_id,
                    "currentPlan": self.subscription_data.get("plan_name", "free") if self.subscription_data else "free",
                },
                "currentPlan": self.subscription_data.get("plan_name", "free") if self.subscription_data else "free",
                "is_premium": self.is_premium,
                "is_logged_in": self.is_logged_in,
                "subscription_data": self.subscription_data,
                "user_id": self.user_id,
                "connection_info": connection_info,
                "health_status": health,
                "ogb_max_sessions": self.ogb_ws.ogb_max_sessions if self.ogb_ws else 0,
                "ogb_sessions": self.ogb_ws.ogb_sessions if self.ogb_ws else 0,
            })

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Get profile error: {str(e)}")
            await self._send_auth_response(event.data.get("event_id"), "error", f"Failed to get profile: {str(e)}")

    # =================================================================
    # Authentication
    # =================================================================

    async def _fetch_subscription_data_from_api(self) -> dict:
        """Fetch full subscription data from REST API (includes features, limits, tenant info).
        
        This is called after WebSocket authentication because WebSocket only sends minimal data (plan_name),
        but the UI needs full subscription data including features, limits, and tenant overrides.
        """
        try:
            import aiohttp
            
            if not self.user_id:
                _LOGGER.error(f"❌ {self.room} Cannot fetch subscription data: no user_id")
                return {}
            
            # Use the same endpoint that _perform_login uses
            api_url = "https://prem.opengrowbox.net/api/v1/user/subscription"
            
            headers = {
                "User-Agent": "OGB-Python-Client/1.0",
                "ogb-client-version": "1.4.2",
                "Authorization": f"Bearer {self.ogb_login_token}" if self.ogb_login_token else "",
            }
            
            timeout = aiohttp.ClientTimeout(total=10)
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        _LOGGER.info(f"✅ {self.room} Subscription data fetched from API")
                        return data.get("subscription_data", {})
                    else:
                        _LOGGER.warning(f"⚠️ {self.room} API subscription fetch failed: HTTP {response.status}")
                        return {}
        
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error fetching subscription data from API: {e}")
            return {}

    async def _on_prem_dev_login(self, event):
        """Enhanced dev login handler."""
        try:
            if self.room != event.data.get("room"):
                return

            email = event.data.get("email")
            ogbAccessToken = event.data.get("ogbAccessToken")
            ogbBetaToken = event.data.get("ogbBetaToken")
            event_id = event.data.get("event_id")

            success = await self.ogb_ws._perform_dev_login(
                email=email,
                ogbAccessToken=ogbAccessToken,
                ogbBetaToken=ogbBetaToken,
                room_id=self.room_id,
                room_name=self.room,
                event_id=event_id,
                auth_callback=self._send_auth_response
            )

            if success:
                user_info = self.ogb_ws.get_user_info()
                self.subscription_data = user_info["subscription_data"]
                self.user_id = user_info["user_id"]
                self.is_premium = user_info["is_premium"]
                self.is_logged_in = user_info["is_logged_in"]
            else:
                self.is_premium_selected = False
                _LOGGER.error(f"❌ {self.room} Premium login failed")

        except Exception as e:
            self.is_premium_selected = False
            _LOGGER.error(f"❌ {self.room} Premium login error: {e}")
        finally:
            self._login_in_progress = False

    async def _on_prem_login(self, event):
        """Enhanced login handler using integrated client."""
        event_room = event.data.get("room")
        if self.room != event_room:
            _LOGGER.debug(f"🔒 {self.room} Ignoring login event for room: {event_room}")
            return

        if self._login_in_progress:
            _LOGGER.warning(f"⚠️ {self.room} Login already in progress, ignoring duplicate request")
            return

        if not self.is_ready:
            _LOGGER.warning(f"⚠️ {self.room} Not ready for login, waiting for initialization...")
            _LOGGER.debug(f"⚠️ {self.room} is_ready check: _is_initialized={self._is_initialized}, ogb_ws={self.ogb_ws is not None}")
            # Wait up to 10 seconds for initialization to complete
            for i in range(20):
                await asyncio.sleep(0.5)
                if self.is_ready:
                    _LOGGER.info(f"✅ {self.room} Ready for login after {(i+1)*0.5}s")
                    break
            if not self.is_ready:
                _LOGGER.error(f"❌ {self.room} Still not ready after 10s, aborting login. _is_initialized={self._is_initialized}, ogb_ws={self.ogb_ws is not None}")
                # Send error response to UI using the event manager for consistency
                event_id = event.data.get("event_id")
                if event_id:
                    await self.event_manager.emit("ogb_premium_auth_response", {
                        "event_id": event_id,
                        "status": "error",
                        "message": "Premium manager not initialized - please wait and try again",
                        "data": {"room": self.room}
                    }, haEvent=True)
                return

        try:
            self._login_in_progress = True
            self.is_premium_selected = True

            email = event.data.get("email")
            OGBToken = event.data.get("OGBToken")
            event_id = event.data.get("event_id")

            _LOGGER.info(f"🔐 {self.room} Processing Premium login request")

            # login_and_connect now returns success immediately on WebSocket connection
            # The actual V1 authentication happens asynchronously and calls _handle_auth_result
            success = await self.ogb_ws.login_and_connect(
                email=email,
                OGBToken=OGBToken,
                room_id=self.room_id,
                room_name=self.room,
                event_id=event_id,
                auth_callback=self._handle_auth_result  # Use new handler for delayed callback
            )

            if not success:
                # WebSocket connection failed immediately
                self.is_premium_selected = False
                _LOGGER.error(f"❌ {self.room} WebSocket connection failed")
                # Auth response will be sent by the auth callback

        except Exception as e:
            self.is_premium_selected = False
            _LOGGER.error(f"❌ {self.room} Premium login error: {e}", exc_info=True)
        finally:
            self._login_in_progress = False

    async def _handle_auth_result(self, event_id: str, status: str, message: str, auth_data: dict = None):
        """Handle authentication result from WebSocket client (called after V1 auth success/failure)."""
        try:
            if status == "success":
                _LOGGER.critical(f"🎉 {self.room} V1 AUTHENTICATION SUCCESS: {message}")

                # Use auth data from V1 authentication
                if auth_data:
                    self.user_id = auth_data.get('user_id')
                    plan_name = auth_data.get('plan', 'free')
                    _LOGGER.info(f"📊 {self.room} Initial auth data: user={self.user_id}, plan={plan_name}")
                    
                    # CRITICAL FIX: Get full subscription data from WebSocket client
                    # The WebSocket client already fetched it during REST login
                    if self.ogb_ws and hasattr(self.ogb_ws, 'subscription_data'):
                        ws_subscription_data = self.ogb_ws.subscription_data
                        if ws_subscription_data and isinstance(ws_subscription_data, dict) and ws_subscription_data.get('features'):
                            # WebSocket client has full subscription data from REST API login
                            self.subscription_data = ws_subscription_data
                            _LOGGER.info(f"✅ {self.room} Using subscription data from WebSocket client: {len(ws_subscription_data.get('features', {}))} features")
                        else:
                            # WebSocket data incomplete - try fetching from API
                            _LOGGER.warning(f"⚠️ {self.room} WebSocket subscription_data incomplete, fetching from API...")
                            full_subscription_data = await self._fetch_subscription_data_from_api()
                            if full_subscription_data:
                                self.subscription_data = full_subscription_data
                                _LOGGER.info(f"✅ {self.room} Full subscription data fetched from API: {len(full_subscription_data.get('features', {}))} features")
                            else:
                                # Fallback to minimal data
                                self.subscription_data = {'plan_name': plan_name}
                                _LOGGER.warning(f"⚠️ {self.room} Using minimal subscription data (API fetch failed)")
                    else:
                        # No WebSocket client or no subscription_data - try API
                        _LOGGER.warning(f"⚠️ {self.room} No WebSocket subscription data, fetching from API...")
                        full_subscription_data = await self._fetch_subscription_data_from_api()
                        if full_subscription_data:
                            self.subscription_data = full_subscription_data
                        else:
                            self.subscription_data = {'plan_name': plan_name}
                    
                    self.is_premium = plan_name != 'free'

                # Set login state
                self.is_logged_in = True
                self.ogb_login_token = getattr(self, 'ogb_login_token', None)  # May be set earlier
                self.ogb_login_email = getattr(self, 'ogb_login_email', None)
                self.is_primary_auth_room = True

                # Switch to Premium mode
                await self._switch_to_premium_mode()

                # Ensure room is included in auth_data for frontend
                if auth_data is None:
                    auth_data = {}
                auth_data['room'] = self.room
                
                # CRITICAL: Add full subscription_data to auth_data for frontend
                auth_data['subscription_data'] = self.subscription_data
                auth_data['is_premium'] = self.is_premium
                
                # Store subscription_data in datastore for feature checks by other managers
                self.data_store.set("subscriptionData", self.subscription_data)
                _LOGGER.debug(f"📦 {self.room} Stored subscription_data in datastore for feature checks")
                
                # Initialize/update feature manager with subscription data
                self._update_feature_manager()
                
                sub_keys = list(self.subscription_data.keys()) if self.subscription_data else []
                _LOGGER.info(f"📤 {self.room} Sending LoginSuccess with subscription data: {sub_keys}")

                # Send success response
                await self._send_auth_response(event_id, "success", message, auth_data)

            else:
                _LOGGER.critical(f"❌ {self.room} V1 AUTHENTICATION FAILED: {message}")
                self.is_premium_selected = False
                self.is_logged_in = False

                # Send error response
                await self._send_auth_response(event_id, "error", message, auth_data)

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error handling auth result: {e}")
            self.is_premium_selected = False
            self.is_logged_in = False
            await self._send_auth_response(event_id, "error", f"Auth result handling failed: {str(e)}")

    async def _switch_to_premium_mode(self):
        """Switch to premium mode after successful V1 authentication."""
        _LOGGER.critical(f"🎛️ {self.room} Switching to Premium mode after V1 auth success")

        try:
            # CRITICAL: Protect WebSocket during HA operations to prevent connection interference
            ws_protected = await self._protect_websocket_during_ha_operations()

            # Set directly in data_store first (ensures grow data can be sent immediately)
            self.data_store.set("mainControl", "Premium")
            _LOGGER.debug(f"📊 {self.room} data_store mainControl set to Premium")

            # Debug entity ID construction
            entity_base = "select.ogb_maincontrol"
            if not entity_base.endswith(f"_{self.room.lower()}"):
                entity_id = f"{entity_base}_{self.room.lower()}"
            else:
                entity_id = entity_base
            _LOGGER.warning(f"🎛️ {self.room} Target entity ID: {entity_id}")

            # Update the HA entity for UI sync (use safe non-blocking version)
            await self._change_sensor_value_safe("SET", "select.ogb_maincontrol", "Premium")

            # Brief delay to allow HA operations to complete
            await asyncio.sleep(0.5)

            # Verify entity state after update
            entity_state = self.hass.states.get(entity_id)
            if entity_state:
                _LOGGER.warning(f"🎛️ {self.room} Entity state after update: {entity_state.state}")
                if entity_state.state == "Premium":
                    _LOGGER.critical(f"🎉 {self.room} SUCCESSFULLY switched to Premium mode!")
                else:
                    _LOGGER.error(f"⚠️ {self.room} Entity state is {entity_state.state}, expected Premium")
            else:
                _LOGGER.error(f"❌ {self.room} MainControl entity not found: {entity_id}")

            # End WebSocket protection
            if ws_protected:
                await self._end_websocket_protection()

            # Enable premium features
            self.is_premium_selected = True
            self.has_control_prem = True
            _LOGGER.info(f"⚡ {self.room} Premium features enabled")

            # Send auth to other rooms
            await self._send_auth_to_other_rooms()

            # Save state
            await self._save_request(True)

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Failed to switch to premium mode: {e}")
            import traceback
            _LOGGER.error(f"❌ {self.room} Full traceback: {traceback.format_exc()}")

    async def _protect_websocket_during_ha_operations(self):
        """Protect WebSocket connection during HA operations that might interfere."""
        if self.ogb_ws:
            # Pause connection monitoring temporarily
            self.ogb_ws._connection_monitoring_paused = True
            _LOGGER.debug(f"🛡️ {self.room} WebSocket protection enabled during HA operations")
            return True
        return False

    async def _end_websocket_protection(self):
        """End WebSocket protection after HA operations complete."""
        if self.ogb_ws:
            # Resume connection monitoring
            self.ogb_ws._connection_monitoring_paused = False
            _LOGGER.debug(f"🛡️ {self.room} WebSocket protection disabled")

    async def _change_sensor_value_safe(self, type="SET", entity="", value=None):
        """Safe version of _change_sensor_value that doesn't block WebSocket operations."""
        if value is None:
            return

        if not entity.endswith(f"_{self.room.lower()}"):
            entity_id = f"{entity}_{self.room.lower()}"
        else:
            entity_id = entity

        try:
            # Use fire-and-forget approach to avoid blocking WebSocket
            asyncio.create_task(
                self.hass.services.async_call(
                    domain="select",
                    service="select_option",
                    service_data={
                        "entity_id": entity_id,
                        "option": value
                    },
                    blocking=False  # Non-blocking call
                )
            )
            _LOGGER.debug(f"🔄 {self.room} HA service call queued (non-blocking): {entity_id} = {value}")
        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Failed to queue HA service call: {e}")

    async def _on_prem_logout(self, event):
        """Handle logout event."""
        try:
            _LOGGER.debug(f"{self.room} Processing logout request")
            event_id = event.data.get("event_id")

            if self.is_logged_in and self.ogb_ws:
                await self.ogb_ws.prem_event("logout", {"user_id": self.user_id})

            await self._cleanup_auth(event_id)

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Logout failed: {e}")
            event_id = getattr(event, "data", {}).get("event_id", None)
            await self._send_auth_response(event_id, "error", f"Logout failed: {str(e)}")

    async def _send_auth_to_other_rooms(self):
        """Send authentication data to other rooms."""
        try:
            auth_data = {
                "AuthenticatedRoom": self.room,
                "ogb_login_email": self.ogb_login_email,
                "ogb_login_token": self.ogb_login_token,
                "user_id": self.user_id,
                "is_logged_in": self.is_logged_in,
                "is_premium": self.is_premium,
                "subscription_data": self.subscription_data,
                "access_token": self.ogb_ws._access_token if self.ogb_ws else None,
                "token_expires_at": self.ogb_ws.token_expires_at if self.ogb_ws else None,
                "ogb_sessions": self.ogb_ws.ogb_sessions if self.ogb_ws else 0,
                "ogb_max_sessions": self.ogb_ws.ogb_max_sessions if self.ogb_ws else 0,
            }

            await self.event_manager.emit("isAuthenticated", auth_data, haEvent=True)
            _LOGGER.debug(f"📤 {self.room} Sent authentication data to other rooms")

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error sending auth to other rooms: {e}")

    async def _broadcast_restored_state(self):
        """Broadcast restored premium state to frontend after HA restart."""
        try:
            _LOGGER.info(f"📢 {self.room} Notifying frontend of restored Premium state")

            connection_info = self.ogb_ws.get_connection_info() if self.ogb_ws else {}

            profile_data = {
                "currentPlan": self.subscription_data.get("plan_name", "free") if self.subscription_data else "free",
                "is_premium": self.is_premium,
                "subscription_data": self.subscription_data,
                "ogb_max_sessions": self.ogb_ws.ogb_max_sessions if self.ogb_ws else 0,
                "ogb_sessions": self.ogb_ws.ogb_sessions if self.ogb_ws else 0,
                "user_id": self.user_id,
                "is_logged_in": self.is_logged_in,
                "connection_info": connection_info,
            }

            await self._send_auth_response(
                event_id="auto_restored",
                status="success",
                message="Premium state auto-restored from disk",
                data=profile_data
            )

            _LOGGER.debug(
                f"✅ {self.room} State broadcast complete: "
                f"plan={self.subscription_data.get('plan_name', 'unknown') if self.subscription_data else 'unknown'}"
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Failed to broadcast restored state: {e}")

    async def _handle_authenticated(self, event):
        """Handle authentication event from other rooms."""
        try:
            if self.room == "Ambient":
                return
            if self.room == event.data.get("AuthenticatedRoom"):
                return

            user_id = event.data.get("user_id")
            access_token = event.data.get("access_token")
            is_logged_in = event.data.get("is_logged_in", False)
            is_premium = event.data.get("is_premium", False)
            subscription_data = event.data.get("subscription_data", {})
            token_expires_at = event.data.get('token_expires_at')
            ogb_sessions = event.data.get("ogb_sessions")
            ogb_max_sessions = event.data.get("ogb_max_sessions")
            ogb_login_email = event.data.get("ogb_login_email")
            ogb_login_token = event.data.get("ogb_login_token")

            # Update local state
            self.user_id = user_id
            self.is_logged_in = is_logged_in
            self.is_premium = is_premium
            self.subscription_data = subscription_data
            self.ogb_login_email = ogb_login_email
            self.ogb_login_token = ogb_login_token

            if self.ogb_ws:
                self.ogb_ws._access_token = access_token
                self.ogb_ws.token_expires_at = token_expires_at
                self.ogb_ws._user_id = user_id
                self.ogb_ws.is_logged_in = is_logged_in
                self.ogb_ws.is_premium = is_premium
                self.ogb_ws.ogb_sessions = ogb_sessions
                self.ogb_ws.ogb_max_sessions = ogb_max_sessions

                if self.ogb_ws.is_logged_in:
                    self.ogb_ws.authenticated = True

            auth_data = {
                "user_id": user_id,
                "access_token": access_token,
                "is_premium": is_premium,
                "is_logged_in": is_logged_in,
                "subscription_data": subscription_data,
            }

            if self.is_logged_in:
                # Store auth credentials for manual room switching via GUI
                authenticated_room = event.data.get("AuthenticatedRoom")
                _LOGGER.info(f"📥 {self.room} Received auth credentials from {authenticated_room}")
                _LOGGER.info(f"🏠 {self.room} Staying in HomeAssistant mode - user can switch to Premium via GUI")
                
                # Get session info for logging
                max_sessions = self.ogb_ws.ogb_max_sessions if self.ogb_ws else 1
                if max_sessions is None:
                    max_sessions = 1
                    
                ogb_sessions_data = self.ogb_ws.ogb_sessions if self.ogb_ws else 0
                if isinstance(ogb_sessions_data, dict):
                    current_sessions = ogb_sessions_data.get('active', 0) or 0
                else:
                    current_sessions = ogb_sessions_data or 0
                
                plan_name = subscription_data.get('plan_name', 'free') if subscription_data else 'free'
                _LOGGER.info(f"📊 {self.room} Session info: {current_sessions}/{max_sessions} (plan: {plan_name})")
                
                # Save credentials to disk for persistence across HA restarts
                await self._save_request(True)
                
                # DO NOT set mainControl to Premium
                # DO NOT attempt WebSocket connection
                # User must manually select this room in ogb-ha-gui
                _LOGGER.debug(f"✅ {self.room} Credentials stored - ready for manual activation")
            else:
                _LOGGER.warning(f"⚠️ {self.room} Login state is False - credentials not stored")

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Handle authenticated error: {e}")

    # =================================================================
    # PREM API Response
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

        await self.event_manager.emit("ogb_premium_auth_response", response_data, haEvent=True)

    # =================================================================
    # FEATURE SERVICE HANDLERS (for frontend featureService.js)
    # =================================================================

    async def _handle_get_subscription(self, event):
        """Handle get_subscription request from frontend."""
        event_id = event.data.get("event_id")
        room = event.data.get("room")

        _LOGGER.debug(f"🔍 {self.room} Feature request: get_subscription (event_id: {event_id}, room: {room})")

        if room and room.lower() != self.room.lower():
            return

        try:
            if not self.subscription_data:
                _LOGGER.debug(f"⚠️ {self.room} subscription_data is None - user may not be logged in")
                self.hass.bus.async_fire("ogb_features_response", {
                    "event_id": event_id,
                    "status": "error",
                    "room": self.room,
                    "data": {
                        "code": "not_logged_in",
                        "message": "User not logged in - subscription data unavailable"
                    }
                })
                return

            response_data = {
                "plan_name": self.subscription_data.get("plan_name", "free"),
                "is_premium": self.is_premium,
                "features": self.subscription_data.get("features", {}),
                "limits": self.subscription_data.get("limits", {}),
                "user_id": self.user_id,
                "tenant_id": self.ogb_ws.tenant_id if self.ogb_ws and hasattr(self.ogb_ws, 'tenant_id') else None,
            }

            _LOGGER.debug(f"✅ {self.room} Subscription response: plan={response_data['plan_name']}")

            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "success",
                "room": self.room,
                "data": response_data
            })

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Get subscription error: {e}")
            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "error",
                "room": self.room,
                "data": {"error": str(e)}
            })

    async def _handle_get_available_features(self, event):
        """Handle get_available_features request from frontend."""
        event_id = event.data.get("event_id")
        room = event.data.get("room")

        if room and room.lower() != self.room.lower():
            return

        try:
            if not self.subscription_data:
                features = {}
            else:
                features = self.subscription_data.get("features", {})
            available_features = [
                {"name": name, "enabled": enabled}
                for name, enabled in features.items()
            ]

            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "success",
                "room": self.room,
                "data": {"features": available_features}
            })

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Get available features error: {e}")
            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "error",
                "room": self.room,
                "data": {"error": str(e)}
            })

    async def _handle_check_feature_access(self, event):
        """Handle check_feature_access request from frontend."""
        event_id = event.data.get("event_id")
        room = event.data.get("room")
        feature_name = event.data.get("feature_name")

        if room and room.lower() != self.room.lower():
            return

        try:
            if not self.subscription_data:
                features = {}
            else:
                features = self.subscription_data.get("features", {})
            has_access = features.get(feature_name, False)

            _LOGGER.debug(f"✅ {self.room} Feature '{feature_name}': {has_access}")

            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "success",
                "room": self.room,
                "data": {
                    "feature_name": feature_name,
                    "has_access": has_access,
                    "plan_name": self.subscription_data.get("plan_name", "free") if self.subscription_data else "free"
                }
            })

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Check feature access error: {e}")
            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "error",
                "room": self.room,
                "data": {"error": str(e)}
            })

    async def _handle_get_feature_definitions(self, event):
        """Handle get_feature_definitions request from frontend."""
        event_id = event.data.get("event_id")
        room = event.data.get("room")

        if room and room.lower() != self.room.lower():
            return

        try:
            definitions = []
            if self.subscription_data:
                features = self.subscription_data.get("features", {})
                definitions = [
                    {
                        "name": name,
                        "enabled": enabled,
                        "description": f"Feature: {name}"
                    }
                    for name, enabled in features.items()
                ]

            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "success",
                "room": self.room,
                "data": {"definitions": definitions}
            })

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Get feature definitions error: {e}")
            self.hass.bus.async_fire("ogb_features_response", {
                "event_id": event_id,
                "status": "error",
                "room": self.room,
                "data": {"error": str(e)}
            })

    # =================================================================
    # GROW DATA
    # =================================================================

    async def _send_ai_learn_data(self, grow_data):
        """Send grow data to AI learning webhook.
        
        Note: This is optional - if the webhook is not available, we just log and continue.
        """
        import aiohttp
        # TODO: Make this configurable or get from API
        webhook_url = "https://brain.azzitech.io/webhook-test/ogb/ai-learning"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=grow_data, timeout=10) as resp:
                    if resp.status == 200:
                        _LOGGER.debug("Grow data sent successfully to AI Learning Webhook")
                        return True
                    else:
                        # Don't spam logs - this is optional functionality
                        _LOGGER.debug(f"AI Learning Webhook returned status: {resp.status}")
                        return False
        except Exception as e:
            # Don't spam logs - webhook might not be available
            _LOGGER.debug(f"AI Learning Webhook not available: {e}")
            return False

    async def _send_growdata_to_prem_api(self, event):
        """Send grow data to Premium API.

        Each room sends its own grow data independently since each has its own WebSocket client.
        """
        import time
        current_time = time.time()
        
        # Debounce: Skip if called too soon after last send
        time_since_last = current_time - self._last_datarelease_time
        if time_since_last < self._datarelease_debounce_seconds:
            _LOGGER.debug(f"⏳ {self.room} DataRelease debounced - only {time_since_last:.1f}s since last send (min: {self._datarelease_debounce_seconds}s)")
            return
        
        self._datarelease_counter += 1
        event_id = f"DR{self._datarelease_counter:03d}"
        _LOGGER.debug(f"🚀 {self.room} DataRelease #{event_id} triggered - starting send process")

        # Check all conditions
        if not self.is_logged_in:
            _LOGGER.warning(f"❌ {self.room} #{event_id} Not logged in, skipping grow data send")
            return

        mainControl = self.data_store.get("mainControl")
        if mainControl != "Premium":
            _LOGGER.warning(f"❌ {self.room} #{event_id} Not in Premium mode: {mainControl}, skipping grow data send")
            return

        # Ensure we have a valid WebSocket connection
        if not self.ogb_ws:
            _LOGGER.warning(f"❌ {self.room} #{event_id} No WebSocket client available")
            return

        _LOGGER.warning(f"🔍 {self.room} #{event_id} WebSocket state: connected={self.ogb_ws.ws_connected}, authenticated={self.ogb_ws.authenticated}")

        if not self.ogb_ws.ws_connected:
            _LOGGER.warning(f"❌ {self.room} #{event_id} WebSocket not connected")
            return

        if not self.ogb_ws.authenticated:
            _LOGGER.warning(f"❌ {self.room} #{event_id} WebSocket not authenticated")
            return

        # Core grow data - essential fields only to avoid "Data too large" errors
        # actionData is populated by OGBActionManager.publicationActionHandler() for ALL modes
        grow_data = {
            "room": self.room,
            "mainControl": self.data_store.get("mainControl"),  # CRITICAL: API needs this to decide controller execution
            "tentMode": self.data_store.get("tentMode"),
            "strainName": self.data_store.get("strainName"),
            "plantStage": self.data_store.get("plantStage"),
            "planttype": self.data_store.get("plantType"),
            "cultivationArea": self.data_store.get("growAreaM2"),
            "vpd": self.data_store.get("vpd"),
            "isLightON": self.data_store.get("isPlantDay"),
            "plantDates": self.data_store.get("plantDates"),
            "tentData": self.data_store.get("tentData"),
            "Hydro": self.data_store.get("Hydro"),
            "growMediums": self.data_store.get("growMediums"),
            "controlOptions": self.data_store.get("controlOptions"),
            "capabilities": self.data_store.get("capabilities"),
            "vpdDetermination": self.data_store.get("vpdDetermination"),
            # CRITICAL: Include previous actions for API processing (CompactDataSchema.js)
            "previousActions": self.data_store.get("previousActions") or [],
            # CRITICAL: Include actionData for AI training (HistoricalDataTrainer.js)
            # This is populated by OGBActionManager.publicationActionHandler() for ALL control modes
            "actionData": self.data_store.get("actionData") or {},
        }
        
        # Add optional data if not too large
        optional_fields = {
            "CropSteering": self.data_store.get("CropSteering"),
            "specialLights": self.data_store.get("specialLights"),
            "weather": self.data_store.get("weather"),
            #"drying": self.data_store.get("drying"),          
        }
        
        # Only add optional fields if they're not None and reasonably sized
        import json
        for key, value in optional_fields.items():
            if value is not None:
                try:
                    # Check if adding this field would make data too large (>50KB)
                    test_data = {**grow_data, key: value}
                    if len(json.dumps(test_data)) < 50000:
                        grow_data[key] = value
                except:
                    pass

        # Send to AI learning webhook if enabled
        aiLearningActive = self.data_store.getDeep("controlOptions.aiLearning")
        if aiLearningActive:
            await self._send_ai_learn_data(grow_data)

        # V1-specific checks
        aes_status = getattr(self.ogb_ws, '_aes_gcm', None) is not None
        v1_ns = getattr(self.ogb_ws, '_v1_namespace', None)
        _LOGGER.debug(f"🔐 {self.room} #{event_id} V1 status: aes={aes_status}, namespace={v1_ns}")

        # Send via V1 encrypted messaging
        try:
            _LOGGER.debug(f"📤 {self.room} #{event_id} Attempting to send grow data (size: {len(str(grow_data))} chars)")

            # Use legacy encrypted messaging (like the working test)
            success = await self.ogb_ws.send_encrypted_message("grow-data", grow_data)

            if success:
                # Update last send time on success for debouncing
                self._last_datarelease_time = current_time
                _LOGGER.debug(f"✅ {self.room} #{event_id} Grow data sent successfully to Premium API")
                return True
            else:
                _LOGGER.warning(f"❌ {self.room} #{event_id} send_encrypted_message returned False - no network request sent")
                return False

        except Exception as e:
            _LOGGER.warning(f"💥 {self.room} #{event_id} Exception in send_encrypted_message: {type(e).__name__}: {e}")
            import traceback
            _LOGGER.warning(f"📋 {self.room} #{event_id} Full traceback: {traceback.format_exc()}")
            return False

    # =================================================================
    # GROW PLANS
    # =================================================================

    async def _ui_get_growplans_request(self, event):
        """Handle get grow plans request from frontend."""
        event_id = event.data.get("event_id")
        requestingRoom = event.data.get("requestingRoom")
        if self.room.lower() != requestingRoom.lower():
            return

        if not self.is_logged_in:
            return

        await self._get_prem_grow_plans(event)

        try:
            if self.ogb_ws and self.ogb_ws.ws_connected:
                growPlans = {
                    "AllPlans": self.growPlanManager.grow_plans if self.growPlanManager else [],
                    "PrivatePlans": self.growPlanManager.grow_plans_private if self.growPlanManager else [],
                    "PublicPlans": self.growPlanManager.grow_plans_public if self.growPlanManager else [],
                    "ActivePlan": self.growPlanManager.active_grow_plan if self.growPlanManager else None,
                }
                await self._send_auth_response(event_id, "success", "GrowPlans retrieved", growPlans)
        except Exception as e:
            _LOGGER.error(f"GET Grow Plan error: {str(e)}")
            await self._send_auth_response(event_id, "error", f"Failed to GET Grow Plans: {str(e)}")

    async def _get_prem_grow_plans(self, event):
        """Request grow plans from Premium API."""
        event_id = event.data.get("event_id")
        strain_name = self.data_store.get("strainName")

        if strain_name is None or strain_name == "":
            return

        _LOGGER.debug(f"Requesting Grow Plans for Strain: {strain_name}")
        planRequestData = {"event_id": event_id, "strain_name": strain_name}

        if self.ogb_ws:
            success = await self.ogb_ws.prem_event("get_grow_plans", planRequestData)
            if success and self.growPlanManager:
                await self._send_auth_response(event_id, "success", "GrowPlan Added", self.growPlanManager.grow_plans)

    async def _ui_grow_plan_add_request(self, event):
        """Handle add grow plan request from frontend."""
        if not self.is_logged_in:
            return

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
            return

        _LOGGER.debug(f"✅ Adding GrowPlan {grow_plan} Room:{self.room}")

        if self.ogb_ws:
            success = await self.ogb_ws.prem_event("add_grow_plan", grow_plan)
            _LOGGER.debug("Grow Plan sent successfully" if success else "Failed to send grow Plan")
            await self._send_auth_response(event_id, "success", "GrowPlan Added", True)
            return success

    async def _ui_grow_plan_del_request(self, event):
        """Handle delete grow plan request from frontend."""
        if not self.is_logged_in:
            return

        mainControl = self.data_store.get("mainControl")
        if mainControl != "Premium":
            return

        event_room = event.data.get("room") or ""
        if self.room.lower() != event_room.lower():
            return

        try:
            event_id = event.data.get("event_id")
            delPlan = event.data

            if self.ogb_ws:
                await self.ogb_ws.prem_event("del_grow_plan", delPlan)
            await self._send_auth_response(event_id, "success", "Delete Grow Plan", {})

        except Exception as e:
            _LOGGER.error(f"Delete Grow Plan error: {str(e)}")
            await self._send_auth_response(event.data.get("event_id"), "error", f"Failed to Delete Grow Plan: {str(e)}")

    async def _ui_grow_plan_activation(self, event):
        """Handle grow plan activation request from frontend."""
        if not self.is_logged_in:
            return

        requestingRoom = event.data.get("requestingRoom")

        if self.room.lower() != requestingRoom.lower():
            return

        growPlan = event.data.get("growPlan")

        _LOGGER.debug(f"{self.room} Grow Plan to Activate: {growPlan}")
        try:
            event_id = event.data.get("event_id")
            if event_id and self.ogb_ws:
                await self.ogb_ws.prem_event("grow_plan_activation", growPlan)
                await self._save_request(True)

        except Exception as e:
            _LOGGER.error(f"Activate Grow Plan error: {str(e)}")
            await self._send_auth_response(event.data.get("event_id"), "error", f"Failed to Activate Grow Plan: {str(e)}")

    # =================================================================
    # PREM UI CONTROL
    # =================================================================

    async def _handle_ctrl_change(self, data):
        """Handle control change from API."""
        _LOGGER.debug(f"CTRL Change from API : {self.room} ------ {data}")
        if not self.is_logged_in:
            return

        if isinstance(data, str):
            await self._change_ctrl_values(tentmode=data)
        elif isinstance(data, dict):
            tentmode = data.get("tentMode")
            controls = {k: v for k, v in data.items() if k != "tentMode"}
            await self._change_ctrl_values(tentmode=tentmode, controls=controls)
        else:
            _LOGGER.error(f"Unsupported data format: {data}")

    # =================================================================
    # HELPERS
    # =================================================================

    async def _change_ctrl_values(self, tentmode=None, controls=None):
        """Change control values."""
        if controls is None:
            controls = {}

        if tentmode is not None:
            tent_control = f"select.ogb_tentmode_{self.room.lower()}"
            self.data_store.set("tentMode", tentmode)
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
                "vpdDeviceDampening": f"ogb_vpd_devicedampening_{self.room.lower()}",
                "vpdLightControl": f"select.ogb_vpdlightcontrol_{self.room.lower()}",
                "lightbyOGBControl": f"select.ogb_lightcontrol_{self.room.lower()}",
            }

            for key, value in controls.items():
                entity_id = mapping.get(key)
                if not entity_id:
                    continue

                self.data_store.setDeep(f"controlOptions.{key}", value)
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

    async def _change_sensor_value(self, type="SET", entity="", value=None):
        """Change sensor/select value."""
        if value is None:
            return

        if not entity.endswith(f"_{self.room.lower()}"):
            entity_id = f"{entity}_{self.room.lower()}"
        else:
            entity_id = entity

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
        """Simplified health check."""
        try:
            ws_health = await self.ogb_ws.health_check() if self.ogb_ws else {}

            return {
                "room": self.room,
                "manager_ready": self.is_logged_in,
                "premium_selected": self.is_premium_selected,
                "is_premium": self.is_premium,
                "websocket_health": ws_health,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            _LOGGER.error(f"Health check error: {e}")
            return {"error": str(e)}

    async def _save_current_state(self):
        """Save current state securely."""
        TentMode = self.data_store.get("tentMode")
        StrainName = self.data_store.get("strainName")

        try:
            ws_backup = self.ogb_ws.get_session_backup_data() if self.ogb_ws else {}

            state_data = {
                "lastTentMode": TentMode,
                "user_id": self.user_id,
                "is_logged_in": self.is_logged_in,
                "is_premium": self.is_premium,
                "is_premium_selected": self.is_premium_selected,
                "is_primary_auth_room": self.is_primary_auth_room,  # Track which room did original login
                "room_id": self.room_id,
                "room_name": self.room,
                "subscription_data": self.subscription_data or {},
                "strain_name": StrainName,
                "growmanager_state": self.growPlanManager.managerActive if self.growPlanManager else None,
                "ogb_login_token": self.ogb_login_token,
                "ogb_login_email": self.ogb_login_email,
                "ws_data": {
                    "base_url": self.ogb_ws.base_url if self.ogb_ws else "",
                    "user_id": ws_backup.get("user_id"),
                    "client_id": ws_backup.get("client_id"),
                    "session_id": ws_backup.get("session_id"),
                    "ogb_sessions": ws_backup.get("ogb_sessions"),
                    "ogb_max_sessions": ws_backup.get("ogb_max_sessions"),
                    "is_premium": ws_backup.get("is_premium"),
                    "is_logged_in": ws_backup.get("is_logged_in"),
                    "subscription_data": ws_backup.get("subscription_data") or {},
                    "session_key": ws_backup.get("session_key"),
                    "access_token": self._encode_access_token(),
                    "token_expires_at": ws_backup.get("token_expires_at"),
                },
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            _LOGGER.info(
                f"💾 {self.room} Saving Premium state: "
                f"user={self.user_id[:8] if self.user_id else 'none'}, "
                f"plan={self.subscription_data.get('plan_name', 'unknown') if self.subscription_data else 'unknown'}, "
                f"logged_in={self.is_logged_in}"
            )
            await _save_state_securely(self.hass, state_data, self.room)
            _LOGGER.info(f"✅ {self.room} Premium state saved successfully")

        except Exception as e:
            import traceback
            _LOGGER.error(f"❌ {self.room} Error saving state: {e}\n{traceback.format_exc()}")

    def _update_feature_manager(self):
        """Initialize or update the feature manager with current subscription data."""
        if self.subscription_data:
            if self.feature_manager is None:
                self.feature_manager = OGBFeatureManager(
                    subscription_data=self.subscription_data,
                    tenant_id=self.tenant_id,
                    user_id=self.user_id,
                    room=self.room,
                    hass=self.hass,
                    event_manager=self.event_manager,
                )
                _LOGGER.info(
                    f"🔧 {self.room} Feature manager initialized "
                    f"(plan: {self.feature_manager.plan_name})"
                )
            else:
                self.feature_manager.update_subscription(self.subscription_data)
                _LOGGER.debug(
                    f"🔄 {self.room} Feature manager updated "
                    f"(plan: {self.feature_manager.plan_name})"
                )
        else:
            _LOGGER.debug(f"{self.room} No subscription data available for feature manager")

    def _get_available_premium_modes(self) -> list:
        """
        Get list of premium tent modes available based on subscription features.
        
        Uses feature flags from subscription_data.features to determine which
        premium control modes should be available to this user.
        
        Returns:
            List of available premium mode names (e.g., ["PID Control", "MPC Control"])
        """
        available_modes = []
        
        # Ensure feature manager is up to date
        self._update_feature_manager()
        
        if not self.feature_manager:
            _LOGGER.debug(f"{self.room} No feature manager - no premium modes available")
            return available_modes
        
        # Map feature keys to tent mode names
        # API sends camelCase: pidControllers, mcpControllers, aiControllers
        feature_to_mode = {
            "pid_controllers": "PID Control",
            "mpc_controllers": "MPC Control", 
            "ai_controllers": "AI Control",
        }
        
        for feature_key, mode_name in feature_to_mode.items():
            if self.feature_manager.has_feature(feature_key):
                available_modes.append(mode_name)
                _LOGGER.debug(f"{self.room} Feature '{feature_key}' enabled -> mode '{mode_name}' available")
            else:
                _LOGGER.debug(f"{self.room} Feature '{feature_key}' disabled -> mode '{mode_name}' NOT available")
        
        _LOGGER.info(
            f"🎮 {self.room} Available premium modes based on features: {available_modes} "
            f"(plan: {self.feature_manager.plan_name})"
        )
        
        return available_modes

    async def _managePremiumControls(self):
        """
        Manage premium control options based on subscription feature flags.
        
        This method:
        1. Gets available premium modes from feature flags (not hardcoded)
        2. Adds/removes tent mode options based on user's subscription
        3. Handles mode restoration after login
        4. Falls back to safe mode if current mode becomes unavailable
        """
        tent_control = f"select.ogb_tentmode_{self.room.lower()}"
        drying_modes = f"select.ogb_dryingmodes_{self.room.lower()}"

        # All possible premium modes (for removal when logged out)
        all_premium_modes = ["PID Control", "MPC Control", "AI Control"]
        
        # Get available modes based on feature flags
        available_modes = self._get_available_premium_modes()
        
        # Modes to remove (premium modes user doesn't have access to)
        modes_to_remove = [m for m in all_premium_modes if m not in available_modes]
        
        dry_options = []  # Drying modes expansion (future use)

        current_tent_mode = self.data_store.get("tentMode")

        _LOGGER.debug(
            f"{self.room} PREM-MODE-CHECK: "
            f"LAST:{self.lastTentMode} Current:{current_tent_mode} "
            f"Available:{available_modes} ToRemove:{modes_to_remove}"
        )

        # If not logged in, remove ALL premium modes
        if not self.is_logged_in:
            if current_tent_mode in all_premium_modes:
                _LOGGER.info(f"{self.room} Not logged in, switching from '{current_tent_mode}' to 'VPD Perfection'")
                await self.hass.services.async_call(
                    "select", "select_option",
                    {"entity_id": tent_control, "option": "VPD Perfection"},
                    blocking=True
                )
            # Remove all premium options
            for entity_id, options in [(tent_control, all_premium_modes), (drying_modes, dry_options)]:
                if options:
                    await self.hass.services.async_call(
                        "opengrowbox", "remove_select_options",
                        {"entity_id": entity_id, "options": options},
                        blocking=True
                    )
            return

        # User is logged in - add available modes, remove unavailable ones
        
        # First, remove modes the user doesn't have access to
        if modes_to_remove:
            # If current mode is being removed, switch to safe mode first
            if current_tent_mode in modes_to_remove:
                _LOGGER.warning(
                    f"{self.room} Current mode '{current_tent_mode}' not available in subscription, "
                    f"switching to 'VPD Perfection'"
                )
                await self.hass.services.async_call(
                    "select", "select_option",
                    {"entity_id": tent_control, "option": "VPD Perfection"},
                    blocking=True
                )
                current_tent_mode = "VPD Perfection"
            
            await self.hass.services.async_call(
                "opengrowbox", "remove_select_options",
                {"entity_id": tent_control, "options": modes_to_remove},
                blocking=True
            )
        
        # Then, add modes the user has access to
        if available_modes:
            await self.hass.services.async_call(
                "opengrowbox", "add_select_options",
                {"entity_id": tent_control, "options": available_modes},
                blocking=True
            )

        # Handle drying modes (if any)
        if dry_options:
            await self.hass.services.async_call(
                "opengrowbox", "add_select_options",
                {"entity_id": drying_modes, "options": dry_options},
                blocking=True
            )

        # Restore previous mode if applicable
        if current_tent_mode in [None, "Disabled", "VPD Perfection"] and self.lastTentMode in available_modes:
            # Only restore if the last mode is still available
            restore_mode = self.lastTentMode
        elif current_tent_mode in available_modes or current_tent_mode not in all_premium_modes:
            # Keep current mode if it's available or not a premium mode
            restore_mode = current_tent_mode or "VPD Perfection"
        else:
            # Current mode is a premium mode that's no longer available
            restore_mode = "VPD Perfection"

        _LOGGER.debug(f"{self.room} RESTORE-MODE: {restore_mode}")
        self.data_store.set("tentMode", restore_mode)
        await self.hass.services.async_call(
            "select", "select_option",
            {"entity_id": tent_control, "option": restore_mode},
            blocking=True
        )

    # =================================================================
    # UTILITIES
    # =================================================================

    def _encode_access_token(self) -> str:
        """Safely encode access token for storage."""
        try:
            if not self.ogb_ws or not self.ogb_ws._access_token:
                return ""
            
            token = self.ogb_ws._access_token
            # If it's already bytes, encode directly
            if isinstance(token, bytes):
                return base64.b64encode(token).decode('utf-8')
            # If it's a string, encode to bytes first
            elif isinstance(token, str):
                return base64.b64encode(token.encode('utf-8')).decode('utf-8')
            else:
                return ""
        except Exception as e:
            _LOGGER.debug(f"{self.room} Error encoding access token: {e}")
            return ""

    async def _save_request(self, event):
        """Handle save request."""
        await self._save_current_state()

    async def _get_or_create_room_id(self):
        """Get or create persistent room ID."""
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
            _LOGGER.debug(f"📁 Current Device-ID loaded for Room {self.room}: {room_id}")
            return room_id

        room_id = str(uuid.uuid4())

        def write_room_id():
            with open(room_id_path, 'w') as f:
                f.write(room_id)

        await asyncio.to_thread(write_room_id)

        _LOGGER.debug(f"🆕 New Device-ID Created for Room {self.room}: {room_id}")
        self.room_id = room_id
        if self.ogb_ws:
            self.ogb_ws._room_id = room_id
        return room_id

    def _check_if_premium_selected(self):
        """Check if Premium mode is currently enabled."""
        return self.data_store.get("mainControl") == "Premium"

    def _check_if_premium_control_active(self):
        """Check if Premium control is active."""
        if self.ogb_ws and self.ogb_ws.subscription_data:
            if self.ogb_ws.subscription_data.get("plan_name") in ["free"]:
                return False
        return True

    def _check_if_can_connect(self):
        """Check if room can connect based on session limits."""
        if self.room.lower() == "ambient":
            _LOGGER.debug(f"✅ {self.room} Ambient room bypass - always allowed")
            return True

        if self.ogb_ws:
            # ogb_sessions can be a dict like {'total': 2, 'active': 1, ...} or an int
            max_sessions = self.ogb_ws.ogb_max_sessions
            if max_sessions is None:
                max_sessions = 1
                
            ogb_sessions_data = self.ogb_ws.ogb_sessions
            if isinstance(ogb_sessions_data, dict):
                current_sessions = ogb_sessions_data.get('active', 0) or 0
            else:
                current_sessions = ogb_sessions_data or 0
            
            can_connect = current_sessions < max_sessions
            _LOGGER.debug(f"{'✅' if can_connect else '❌'} {self.room} Session check: {current_sessions}/{max_sessions}")
            return can_connect
        return False

    async def _handle_premium_change(self, data):
        """Handle premium mode changes."""
        current = data.get("currentValue")
        previous = data.get("lastValue")

        if previous != "Premium" and current == "Premium":
            await self._on_premium_selected()
        elif previous == "Premium" and current != "Premium":
            await self._on_premium_deselected()

    async def _on_premium_selected(self):
        """Handle Premium mode activation.
        
        Rooms can connect WebSocket based on plan limits:
        - Free: 1 room max
        - Basic: 3 rooms max  
        - Grower: 5 rooms max
        - etc.
        """
        try:
            _LOGGER.debug(f"Premium mode activated for {self.room}")
            self.is_premium_selected = True

            if self.is_logged_in and self.ogb_ws:
                if self.ogb_ws.ws_connected:
                    _LOGGER.debug(f"{self.room} is already connected over WS")
                    await self._managePremiumControls()
                    return

                if not self.ogb_ws.is_connected():
                    # Check session limits before connecting
                    if not self._check_if_can_connect():
                        _LOGGER.warning(f"{self.room} Session limit reached - cannot connect WebSocket")
                        await self._send_auth_response(
                            "error",
                            "to_many_rooms",
                            {
                                "success": "false",
                                "MSG": "Cannot activate this room in Premium. Reason: Session limit reached for your plan."
                            }
                        )
                        # Still manage controls for UI even without WebSocket
                        await self._managePremiumControls()
                        return

                    _LOGGER.warning(f"🔗 Triggering WebSocket connection for {self.room}")
                    await self.ogb_ws._connect_websocket()
                    await self._managePremiumControls()
                    await self._save_request(True)

        except Exception as e:
            _LOGGER.error(f"Premium selection error: {e}")

    async def _on_premium_deselected(self):
        """Handle Premium mode deactivation."""
        try:
            _LOGGER.warning(f"Premium mode deactivated for {self.room}")
            if not self.is_premium_selected:
                return

            self.is_premium_selected = False

            if self.ogb_ws:
                await self.ogb_ws.disconnect()
            await self._managePremiumControls()
            await self._save_request(True)

        except Exception as e:
            _LOGGER.error(f"Premium deselection error: {e}")

    async def _handle_analytics_update(self, event):
        """Handle AnalyticsUpdate events from V1 WebSocket and route to analytics modules."""
        try:
            update_type = event.data.get("type") if hasattr(event, 'data') else event.get("type")
            data = event.data if hasattr(event, 'data') else event.get("data", {})

            _LOGGER.info(f"📊 {self.room} Processing analytics update: {update_type}")

            # Update premium sensors (existing functionality)
            if update_type == "yield_prediction" and self._premium_sensors.get("yield_prediction"):
                self._premium_sensors["yield_prediction"].update_prediction(data)
                _LOGGER.debug(f"✅ {self.room} Updated yield_prediction sensor")

            elif update_type == "anomaly_detection" and self._premium_sensors.get("anomaly_score"):
                self._premium_sensors["anomaly_score"].update_anomalies(data)
                _LOGGER.debug(f"✅ {self.room} Updated anomaly_score sensor")

            elif update_type == "performance_metrics" and self._premium_sensors.get("performance_score"):
                self._premium_sensors["performance_score"].update_performance(data)
                _LOGGER.debug(f"✅ {self.room} Updated performance_score sensor")

            # TODO: Add analytics module processing once methods are available
            # Analytics modules are initialized but processing methods need to be added
                if self._premium_sensors.get("compliance_status"):
                    self._premium_sensors["compliance_status"].update_compliance(data)
                    _LOGGER.debug(f"✅ {self.room} Updated compliance_status sensor")

                if self._premium_sensors.get("violations_count"):
                    self._premium_sensors["violations_count"].update_violations(data)
                    _LOGGER.debug(f"✅ {self.room} Updated violations_count sensor")

            # Handle research updates
            elif update_type in ["dataset_update", "quality_metrics"]:
                if self._premium_sensors.get("dataset_count"):
                    self._premium_sensors["dataset_count"].update_dataset_count(data)
                    _LOGGER.debug(f"✅ {self.room} Updated dataset_count sensor")

                if self._premium_sensors.get("data_quality"):
                    self._premium_sensors["data_quality"].update_data_quality(data)
                    _LOGGER.debug(f"✅ {self.room} Updated data_quality sensor")

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error handling analytics update: {e}")

    async def _cleanup_auth(self, event_id: str = None):
        """Cleanup authentication data and remove state file.
        
        Args:
            event_id: Optional event ID for response tracking
        """
        try:
            _LOGGER.warning(f"🧹 {self.room} Starting authentication cleanup")

            # Disconnect WebSocket if connected
            if self.ogb_ws and self.ogb_ws.ws_connected:
                await self.ogb_ws.disconnect()

            # Reset all authentication state
            self.is_premium_selected = False
            self.is_logged_in = False
            self.is_premium = False
            self.has_control_prem = False
            self.user_id = None
            self.subscription_data = None
            self.ogb_login_token = None
            self.ogb_login_email = None
            self.is_primary_auth_room = False

            # Reset grow plan manager
            if self.growPlanManager:
                self.growPlanManager.active_grow_plan = None
                self.growPlanManager.managerActive = False

            # Reset mainControl to HomeAssistant if currently Premium
            try:
                if self._check_if_premium_selected():
                    self.data_store.set("mainControl", "HomeAssistant")
                    await self._change_sensor_value("SET", "select.ogb_maincontrol", "HomeAssistant")
                    _LOGGER.info(f"🎛️ {self.room} Reset mainControl to HomeAssistant")
            except Exception as e:
                _LOGGER.error(f"⚠️ {self.room} Error resetting mainControl: {e}")

            # Cleanup WebSocket resources
            try:
                if self.ogb_ws:
                    await self.ogb_ws.cleanup_prem(event_id or "cleanup")
            except Exception as e:
                _LOGGER.error(f"⚠️ {self.room} Error cleaning WebSocket: {e}")

            # Remove state file
            try:
                await _remove_state_file(self.hass, self.room)
                _LOGGER.info(f"🗑️ {self.room} State file removed successfully")
            except Exception as e:
                _LOGGER.error(f"❌ {self.room} Error removing state file: {e}")

            # Send logout response
            if event_id:
                try:
                    await self._send_auth_response(event_id, "success", "Logged out successfully")
                except Exception as e:
                    _LOGGER.error(f"⚠️ {self.room} Error sending logout response: {e}")

            _LOGGER.warning(f"✅ {self.room} Authentication cleanup completed")

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Cleanup auth error: {e}")
            if event_id:
                try:
                    await self._send_auth_response(event_id, "error", f"Logout failed: {str(e)}")
                except:
                    pass

    async def cleanup_auth_data(self, event_id: str = None):
        """Cleanup authentication data and remove state file.
        
        This is an alias for _cleanup_auth() for backward compatibility.
        
        Args:
            event_id: Optional event ID for response tracking
        """
        await self._cleanup_auth(event_id)

    def register_premium_sensor(self, sensor_type: str, sensor_entity):
        """Register a premium sensor for WebSocket updates."""
        self._premium_sensors[sensor_type] = sensor_entity
        _LOGGER.debug(f"✅ {self.room} Registered premium sensor: {sensor_type}")

    # =================================================================
    # Lifecycle
    # =================================================================

    async def async_shutdown(self):
        """Shutdown premium integration."""
        _LOGGER.info(f"Shutting down premium integration for {self.room}")

        if self.ogb_ws:
            try:
                await self.ogb_ws.disconnect()
            except Exception as e:
                _LOGGER.error(f"Error disconnecting WebSocket: {e}")

        _LOGGER.info(f"Premium integration shutdown complete for {self.room}")

    def __str__(self):
        return f"{self.name} - Premium: {self.is_premium}, Logged In: {self.is_logged_in}"

    def __repr__(self):
        return f"{self.name}(room='{self.room}', premium={self.is_premium}, logged_in={self.is_logged_in})"

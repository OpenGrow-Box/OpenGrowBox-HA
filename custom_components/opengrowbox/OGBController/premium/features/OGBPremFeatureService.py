"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                       🔧 FEATURE SERVICE MODULE 🔧                           ║
║              API-Compatible Feature Service for ogb-ha-gui                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

This module provides feature checking services that are compatible with the
frontend's featureService.js. It handles HA events for feature-related requests.

The frontend expects these capabilities:
- Get available features based on subscription tier
- Check if a specific feature is accessible
- Get feature definitions with metadata

Events Handled:
- ogb_features_get_available: Returns list of available feature IDs
- ogb_features_check_access: Checks if user has access to a feature
- ogb_features_get_definitions: Returns feature definitions with metadata

Events Emitted:
- ogb_features_response: Response to feature requests
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class OGBFeatureService:
    """
    Feature service that provides API-compatible feature checking.

    This replaces the broken featureService.js REST calls with HA event-based
    communication that goes through the OGBPremManager.
    """

    # Feature definitions with UI metadata (icons, categories, descriptions)
    FEATURE_DEFINITIONS = {
        # Core features (Free tier)
        "basic_monitoring": {
            "id": "basic_monitoring",
            "name": "Basic Monitoring",
            "description": "Core sensor monitoring functionality",
            "required_tier": "free",
            "ui_icon": "FaChartBar",
            "category": "core",
            "is_core": True,
        },
        "ai_controllers": {
            "id": "ai_controllers",
            "name": "AI Controllers",
            "description": "Intelligent environmental control",
            "required_tier": "free",
            "ui_icon": "FaRobot",
            "category": "core",
            "is_core": True,
        },
        "mobile_app": {
            "id": "mobile_app",
            "name": "Mobile App",
            "description": "Mobile app access",
            "required_tier": "free",
            "ui_icon": "FaMobile",
            "category": "core",
            "is_core": True,
        },
        "notifications": {
            "id": "notifications",
            "name": "Notifications",
            "description": "Push notifications and alerts",
            "required_tier": "basic",
            "ui_icon": "FaBell",
            "category": "notifications",
            "is_core": False,
        },
        "data_export": {
            "id": "data_export",
            "name": "Data Export",
            "description": "Export data in CSV, JSON formats",
            "required_tier": "basic",
            "ui_icon": "FaFileExport",
            "category": "data",
            "is_core": False,
        },
        # Analytics features (Basic+)
        "advanced_analytics": {
            "id": "advanced_analytics",
            "name": "Advanced Analytics",
            "description": "Environmental insights, yield prediction, ML models",
            "required_tier": "basic",
            "ui_icon": "FaChartLine",
            "category": "analytics",
            "is_core": False,
        },
        "analytics_sensors": {
            "id": "analytics_sensors",
            "name": "Analytics Sensors",
            "description": "Real-time analytics sensor data",
            "required_tier": "basic",
            "ui_icon": "FaTachometerAlt",
            "category": "analytics",
            "is_core": False,
        },
        "real_time_updates": {
            "id": "real_time_updates",
            "name": "Real-time Updates",
            "description": "Live data updates via WebSocket",
            "required_tier": "basic",
            "ui_icon": "FaSync",
            "category": "core",
            "is_core": False,
        },
        # Compliance features (Professional+)
        "compliance": {
            "id": "compliance",
            "name": "Compliance Tracking",
            "description": "Cannabis/healthcare compliance tracking",
            "required_tier": "professional",
            "ui_icon": "FaClipboardCheck",
            "category": "compliance",
            "is_core": False,
        },
        "basic_compliance": {
            "id": "basic_compliance",
            "name": "Basic Compliance",
            "description": "Basic compliance monitoring",
            "required_tier": "grower",
            "ui_icon": "FaClipboard",
            "category": "compliance",
            "is_core": False,
        },
        "full_compliance": {
            "id": "full_compliance",
            "name": "Full Compliance",
            "description": "Complete compliance suite with audit trail",
            "required_tier": "professional",
            "ui_icon": "FaShieldAlt",
            "category": "compliance",
            "is_core": False,
        },
        "advanced_compliance": {
            "id": "advanced_compliance",
            "name": "Advanced Compliance",
            "description": "Full audit trail, SOPs, validation",
            "required_tier": "professional",
            "ui_icon": "FaGavel",
            "category": "compliance",
            "is_core": False,
        },
        # Research features (Professional+)
        "research_data": {
            "id": "research_data",
            "name": "Research Data",
            "description": "Research-grade data export, citations, DOI",
            "required_tier": "professional",
            "ui_icon": "FaFlask",
            "category": "data",
            "is_core": False,
        },
        # API & Integration features (Professional+)
        "api_access": {
            "id": "api_access",
            "name": "API Access",
            "description": "REST API access with rate limits",
            "required_tier": "professional",
            "ui_icon": "FaCode",
            "category": "integration",
            "is_core": False,
        },
        "webhooks": {
            "id": "webhooks",
            "name": "Webhooks",
            "description": "Custom webhook integrations",
            "required_tier": "professional",
            "ui_icon": "FaLink",
            "category": "integration",
            "is_core": False,
        },
        # Enterprise features
        "multi_tenant": {
            "id": "multi_tenant",
            "name": "Multi-Tenant",
            "description": "Multi-organization support",
            "required_tier": "enterprise",
            "ui_icon": "FaBuilding",
            "category": "enterprise",
            "is_core": False,
        },
        "priority_support": {
            "id": "priority_support",
            "name": "Priority Support",
            "description": "Faster support response times",
            "required_tier": "enterprise",
            "ui_icon": "FaHeadset",
            "category": "support",
            "is_core": False,
        },
        "custom_integrations": {
            "id": "custom_integrations",
            "name": "Custom Integrations",
            "description": "Custom integration development",
            "required_tier": "enterprise",
            "ui_icon": "FaPuzzlePiece",
            "category": "integration",
            "is_core": False,
        },
        "sla": {
            "id": "sla",
            "name": "SLA Guarantee",
            "description": "99.9% uptime SLA",
            "required_tier": "enterprise",
            "ui_icon": "FaCertificate",
            "category": "enterprise",
            "is_core": False,
        },
    }

    # Tier hierarchy for comparison
    TIER_HIERARCHY = [
        "free",
        "starter",
        "basic",
        "grower",
        "professional",
        "enterprise",
    ]

    def __init__(self, hass, event_manager, room: str):
        """
        Initialize the feature service.

        Args:
            hass: Home Assistant instance
            event_manager: OGB Event Manager for emitting events
            room: Room name for this service instance
        """
        self.hass = hass
        self.event_manager = event_manager
        self.room = room
        self._feature_manager = None
        self._event_unsubscribers = []

        self._setup_event_listeners()
        _LOGGER.info(f"✅ {room} OGBFeatureService initialized")

    def set_feature_manager(self, feature_manager):
        """
        Set the feature manager reference (from OGBPremManager).

        Args:
            feature_manager: OGBFeatureManager instance
        """
        self._feature_manager = feature_manager
        _LOGGER.debug(f"{self.room} Feature manager set in service")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        # Listen for feature requests from frontend
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_features_get_available", self._handle_get_available
            )
        )
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_features_check_access", self._handle_check_access
            )
        )
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_features_get_definitions", self._handle_get_definitions
            )
        )
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_features_get_subscription", self._handle_get_subscription
            )
        )

    async def shutdown(self):
        """Cleanup event listeners."""
        for unsubscribe in self._event_unsubscribers:
            if callable(unsubscribe):
                unsubscribe()
        self._event_unsubscribers.clear()
        _LOGGER.info(f"{self.room} OGBFeatureService shutdown")

    async def _handle_get_available(self, event):
        """
        Handle request for available features.

        Returns list of feature IDs that are available for the current subscription.
        """
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return  # Not for this room

            event_id = event.data.get("event_id")

            available_features = self.get_available_features()

            await self._send_response(
                event_id,
                "success",
                "features_available",
                {
                    "features": available_features,
                    "tier": self._get_current_tier(),
                    "timestamp": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error getting available features: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                "failed_to_get_features",
                {"error": str(e)},
            )

    async def _handle_check_access(self, event):
        """
        Handle request to check access to a specific feature.

        Args in event.data:
            feature_id: Feature to check access for
        """
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return  # Not for this room

            event_id = event.data.get("event_id")
            feature_id = event.data.get("feature_id")

            if not feature_id:
                await self._send_response(
                    event_id,
                    "error",
                    "missing_feature_id",
                    {"error": "feature_id is required"},
                )
                return

            has_access = self.check_feature_access(feature_id)
            feature_info = self.get_feature_info(feature_id)

            await self._send_response(
                event_id,
                "success",
                "feature_access_checked",
                {
                    "feature_id": feature_id,
                    "accessible": has_access,
                    "feature_info": feature_info,
                    "current_tier": self._get_current_tier(),
                    "timestamp": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error checking feature access: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                "failed_to_check_access",
                {"error": str(e)},
            )

    async def _handle_get_definitions(self, event):
        """
        Handle request for feature definitions with metadata.

        Returns all feature definitions with icons, descriptions, categories.
        """
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return  # Not for this room

            event_id = event.data.get("event_id")

            definitions = self.get_all_definitions()

            await self._send_response(
                event_id,
                "success",
                "feature_definitions",
                {
                    "features": definitions,
                    "current_tier": self._get_current_tier(),
                    "timestamp": datetime.now().isoformat(),
                },
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error getting feature definitions: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                "failed_to_get_definitions",
                {"error": str(e)},
            )

    async def _handle_get_subscription(self, event):
        """
        Handle request for subscription summary.

        Returns current subscription tier and enabled features.
        """
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return  # Not for this room

            event_id = event.data.get("event_id")

            subscription_summary = self.get_subscription_summary()

            await self._send_response(
                event_id,
                "success",
                "subscription_summary",
                {**subscription_summary, "timestamp": datetime.now().isoformat()},
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error getting subscription: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                "failed_to_get_subscription",
                {"error": str(e)},
            )

    async def _send_response(
        self, event_id: str, status: str, message: str, data: Dict = None
    ):
        """Send response event to frontend."""
        response_data = {
            "event_id": event_id,
            "status": status,
            "message": message,
            "room": self.room,
            "timestamp": datetime.now().isoformat(),
        }

        if data:
            response_data["data"] = data

        await self.event_manager.emit(
            "ogb_features_response", response_data, haEvent=True
        )

    # =================================================================
    # Feature Access Methods
    # =================================================================

    def get_available_features(self) -> List[str]:
        """
        Get list of available feature IDs for current subscription.

        Returns:
            List of feature ID strings
        """
        if self._feature_manager:
            # Use feature manager to get accurate feature list
            all_features = self._feature_manager.list_available_features()
            return [name for name, enabled in all_features.items() if enabled]

        # Fallback: return free tier features
        return self._get_tier_features("free")

    def check_feature_access(self, feature_id: str) -> bool:
        """
        Check if user has access to a specific feature.

        Args:
            feature_id: Feature to check

        Returns:
            True if accessible, False otherwise
        """
        if not feature_id:
            return False

        if self._feature_manager:
            return self._feature_manager.has_feature(feature_id)

        # Fallback: check tier hierarchy
        current_tier = self._get_current_tier()
        feature_def = self.FEATURE_DEFINITIONS.get(feature_id)

        if not feature_def:
            _LOGGER.warning(f"Unknown feature requested: {feature_id}")
            return False

        required_tier = feature_def.get("required_tier", "enterprise")
        return self._tier_has_access(current_tier, required_tier)

    def get_feature_info(self, feature_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a feature.

        Args:
            feature_id: Feature to query

        Returns:
            Feature info dict or None
        """
        feature_def = self.FEATURE_DEFINITIONS.get(feature_id)
        if not feature_def:
            return None

        info = dict(feature_def)
        info["enabled"] = self.check_feature_access(feature_id)
        info["current_tier"] = self._get_current_tier()

        return info

    def get_all_definitions(self) -> List[Dict[str, Any]]:
        """
        Get all feature definitions with current accessibility status.

        Returns:
            List of feature definition dicts
        """
        definitions = []
        for feature_id, feature_def in self.FEATURE_DEFINITIONS.items():
            info = dict(feature_def)
            info["enabled"] = self.check_feature_access(feature_id)
            info["current_tier"] = self._get_current_tier()
            definitions.append(info)

        return definitions

    def get_subscription_summary(self) -> Dict[str, Any]:
        """
        Get summary of current subscription.

        Returns:
            Dict with subscription details
        """
        if self._feature_manager:
            return self._feature_manager.get_subscription_summary()

        # Fallback summary
        current_tier = self._get_current_tier()
        enabled_features = self.get_available_features()

        return {
            "plan_name": current_tier,
            "is_premium": current_tier != "free",
            "enabled_features": enabled_features,
            "feature_count": len(enabled_features),
            "limits": {},
            "last_update": datetime.now().isoformat(),
        }

    # =================================================================
    # Helper Methods
    # =================================================================

    def _get_current_tier(self) -> str:
        """Get current subscription tier."""
        if self._feature_manager:
            return self._feature_manager.plan_name
        return "free"

    def _tier_has_access(self, user_tier: str, required_tier: str) -> bool:
        """Check if user's tier has access to required tier."""
        try:
            user_index = self.TIER_HIERARCHY.index(user_tier.lower())
            required_index = self.TIER_HIERARCHY.index(required_tier.lower())
            return user_index >= required_index
        except ValueError:
            return False

    def _get_tier_features(self, tier: str) -> List[str]:
        """Get features available for a specific tier."""
        features = []
        for feature_id, feature_def in self.FEATURE_DEFINITIONS.items():
            required_tier = feature_def.get("required_tier", "enterprise")
            if self._tier_has_access(tier, required_tier):
                features.append(feature_id)
        return features

    def __repr__(self) -> str:
        """String representation for debugging."""
        tier = self._get_current_tier()
        feature_count = len(self.get_available_features())
        return (
            f"<OGBFeatureService room={self.room} tier={tier} features={feature_count}>"
        )

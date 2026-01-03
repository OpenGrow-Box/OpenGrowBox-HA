"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                          Feature Flag Management                             ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file manages PREMIUM feature access control.

OGBFeatureManager - Feature Flag Management

Manages feature flags based on user subscription tier.
Checks subscription_data.features from authentication response.

Feature flags control access to premium capabilities:
- advanced_analytics: Environmental insights, yield prediction, ML models (Basic+)
- compliance: Cannabis/healthcare compliance tracking (Professional+)
- advanced_compliance: Full audit trail, SOPs, validation (Professional+)
- research_data: Research-grade data export, citations, DOI (Professional+)
- multi_tenant: Multi-organization support (Enterprise)
- api_access: REST API access with rate limits (Professional+)
- webhooks: Custom webhook integrations (Professional+)
- priority_support: Faster support response times (Professional+)
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)


class OGBFeatureManager:
    """Feature flag manager for subscription-based access control.
    
    Feature Access Priority (highest to lowest):
    1. Kill switch (feature disabled globally via admin)
    2. Tenant-specific override (from feature_overrides table via admin)
    3. Subscription plan features (from subscription_plans.features)
    4. Global feature flags (from feature_flags_config with rollout)
    5. Default deny
    
    The API returns features in camelCase (e.g., 'aiControllers'), 
    this manager accepts both camelCase and snake_case.
    """

    # Mapping from API camelCase to internal snake_case
    # API sends: aiControllers, pidControllers, mcpControllers, etc.
    API_TO_INTERNAL = {
        # Controller features
        "aiControllers": "ai_controllers",
        "pidControllers": "pid_controllers",
        "mcpControllers": "mpc_controllers",
        # Analytics features
        "basicAnalytics": "basic_analytics",
        "advancedAnalytics": "advanced_analytics",
        # Compliance features  
        "basicCompliance": "basic_compliance",
        "advancedCompliance": "advanced_compliance",
        # Access features
        "WebAppAccess": "web_app_access",
        "emailSupport": "email_support",
        "prioritySupport": "priority_support",
        # Integration features
        "customIntegrations": "custom_integrations",
        "whiteLabel": "white_label",
    }
    
    # Reverse mapping for lookups
    INTERNAL_TO_API = {v: k for k, v in API_TO_INTERNAL.items()}

    # Feature definitions with descriptions (for UI/documentation)
    FEATURES = {
        # Controller features (from API)
        "ai_controllers": {
            "description": "AI environmental control",
            "api_key": "aiControllers",
        },
        "pid_controllers": {
            "description": "PID controller for precise environmental control",
            "api_key": "pidControllers",
        },
        "mpc_controllers": {
            "description": "Model Predictive Control for advanced optimization",
            "api_key": "mcpControllers",
        },
        # Analytics features
        "basic_analytics": {
            "description": "Basic analytics and monitoring",
            "api_key": "basicAnalytics",
        },
        "advanced_analytics": {
            "description": "Advanced analytics and insights",
            "api_key": "advancedAnalytics",
        },
        # Compliance features
        "basic_compliance": {
            "description": "Basic compliance tracking",
            "api_key": "basicCompliance",
        },
        "advanced_compliance": {
            "description": "Full compliance suite with audit logs",
            "api_key": "advancedCompliance",
        },
        # Access features
        "web_app_access": {
            "description": "Web application access",
            "api_key": "WebAppAccess",
        },
        "email_support": {
            "description": "Email support access",
            "api_key": "emailSupport",
        },
        "priority_support": {
            "description": "Priority support response",
            "api_key": "prioritySupport",
        },
        # Integration features
        "custom_integrations": {
            "description": "Custom API integrations",
            "api_key": "customIntegrations",
        },
        "white_label": {
            "description": "White label branding",
            "api_key": "whiteLabel",
        },
    }

    # Tier hierarchy
    TIER_HIERARCHY = ["free", "basic", "grower", "professional", "enterprise"]

    def __init__(
        self,
        subscription_data: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
        api_proxy: Optional[Any] = None,
        user_id: Optional[str] = None,
        room: Optional[str] = None,
        hass: Optional[Any] = None,
        event_manager: Optional[Any] = None,
    ):
        """
        Initialize feature manager with API integration for dynamic feature flags.

        Args:
            subscription_data: Subscription data from authentication response
                {
                    "plan_name": "professional",
                    "features": {
                        "advanced_analytics": true,
                        "compliance": true,
                        ...
                    },
                    "limits": {
                        "max_rooms": 10,
                        "api_calls_per_day": 10000
                    }
                }
            tenant_id: Tenant ID for fetching tenant-specific feature overrides
            api_proxy: OGBApiProxy instance for making API calls
            user_id: User ID for analytics tracking
            room: Room identifier for analytics tracking
            hass: Home Assistant instance for events
            event_manager: Event manager for upgrade prompts
        """
        self.subscription_data = subscription_data or {}
        self.tenant_id = tenant_id
        self.api_proxy = api_proxy
        self.user_id = user_id
        self.room = room or "default"

        self.plan_name = self.subscription_data.get("plan_name", "free")
        self.features = self.subscription_data.get("features", {})
        self.limits = self.subscription_data.get("limits", {})
        self.last_update = datetime.now()

        # Database-driven feature overrides (from admin dashboard)
        self.db_overrides: Dict[str, bool] = {}
        self.global_config: Dict[str, Dict[str, Any]] = {}

        # Cache management
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = 300  # 5 minutes

        # Analytics modules (optional - only initialized if dependencies provided)
        self.usage_metrics: Optional[Any] = None
        self.rate_limiter: Optional[Any] = None
        self.upgrade_prompts: Optional[Any] = None

        # Initialize analytics if we have the required data
        if user_id and room:
            try:
                from ..analytics.OGBUsageMetrics import OGBUsageMetrics
                from ..analytics.OGBRateLimiter import OGBRateLimiter
                from ..analytics.OGBUpgradePrompts import OGBUpgradePrompts

                self.usage_metrics = OGBUsageMetrics(
                    user_id, self.plan_name, room, tenant_id
                )
                self.rate_limiter = OGBRateLimiter(user_id, self.plan_name, room)
                
                if hass and event_manager:
                    self.upgrade_prompts = OGBUpgradePrompts(
                        room, self.plan_name, hass, event_manager
                    )
                    
                _LOGGER.info(
                    f"📊 Analytics modules initialized for {room} "
                    f"(plan: {self.plan_name})"
                )
            except ImportError as e:
                _LOGGER.warning(f"Analytics modules not available: {e}")

        _LOGGER.info(
            f"OGBFeatureManager initialized (plan: {self.plan_name}, tenant: {tenant_id})"
        )
        self._log_available_features()

    def _log_available_features(self):
        """Log available features for debugging."""
        enabled_features = [name for name, enabled in self.features.items() if enabled]
        if enabled_features:
            _LOGGER.debug(f"Enabled features: {', '.join(enabled_features)}")
        else:
            _LOGGER.debug("No premium features enabled (free tier)")

    def update_subscription(self, subscription_data: Dict[str, Any]):
        """
        Update subscription data (e.g., after upgrade/downgrade).

        Args:
            subscription_data: New subscription data
        """
        old_plan = self.plan_name
        self.subscription_data = subscription_data
        self.plan_name = subscription_data.get("plan_name", "free")
        self.features = subscription_data.get("features", {})
        self.limits = subscription_data.get("limits", {})
        self.last_update = datetime.now()

        if old_plan != self.plan_name:
            _LOGGER.info(f"Subscription updated: {old_plan} → {self.plan_name}")
        else:
            _LOGGER.debug(f"Subscription data refreshed (plan: {self.plan_name})")

        self._log_available_features()

    async def refresh_from_api(self) -> bool:
        """
        Fetch latest feature flags from API.

        Queries tenant-specific overrides and global feature configs.

        Returns:
            True if refresh successful, False otherwise
        """
        if not self.api_proxy or not self.tenant_id:
            _LOGGER.debug(
                "Cannot refresh feature flags: missing API proxy or tenant ID"
            )
            return False

        try:
            # Get tenant-specific overrides (highest priority)
            result = await self.api_proxy.get_tenant_overrides(self.tenant_id)

            if result and result.get("success"):
                overrides_data = result.get("data", {}).get("overrides", {})
                active_overrides = overrides_data.get("active", [])

                # Build override dict
                self.db_overrides = {
                    override["feature_key"]: override["enabled"]
                    for override in active_overrides
                    if "feature_key" in override and "enabled" in override
                }

                self._cache_timestamp = datetime.now()
                _LOGGER.info(
                    f"Feature flags refreshed: {len(self.db_overrides)} overrides loaded"
                )
                return True
            else:
                error_msg = (
                    result.get("error", "Unknown error") if result else "No response"
                )
                _LOGGER.warning(f"Failed to refresh feature flags: {error_msg}")
                return False

        except Exception as e:
            _LOGGER.error(f"Error refreshing feature flags from API: {e}")
            return False

    def is_cache_valid(self) -> bool:
        """Check if the feature flag cache is still valid."""
        if not self._cache_timestamp:
            return False
        age = (datetime.now() - self._cache_timestamp).total_seconds()
        return age < self._cache_ttl

    def invalidate_cache(self) -> None:
        """Invalidate the feature flag cache, forcing a refresh on next check."""
        self._cache_timestamp = None
        _LOGGER.debug("Feature flag cache invalidated")

    def update_override(self, feature_key: str, enabled: bool) -> None:
        """
        Update a single feature override (from WebSocket event).

        Args:
            feature_key: Feature identifier
            enabled: Whether the feature is enabled
        """
        self.db_overrides[feature_key] = enabled
        self._cache_timestamp = datetime.now()
        _LOGGER.info(f"Feature override updated: {feature_key}={enabled}")

    def _normalize_feature_key(self, feature_name: str) -> tuple:
        """
        Normalize feature key to handle both camelCase (API) and snake_case.
        
        Returns:
            Tuple of (internal_key, api_key) for lookups
        """
        # If it's a camelCase API key, convert to internal
        if feature_name in self.API_TO_INTERNAL:
            internal_key = self.API_TO_INTERNAL[feature_name]
            api_key = feature_name
        # If it's already internal snake_case
        elif feature_name in self.INTERNAL_TO_API:
            internal_key = feature_name
            api_key = self.INTERNAL_TO_API[feature_name]
        else:
            # Unknown key - use as-is for both
            internal_key = feature_name
            api_key = feature_name
        
        return internal_key, api_key

    def has_feature(
        self, feature_name: str, record_access: bool = True
    ) -> bool:
        """
        Check if user has access to a feature.

        Feature access priority:
        1. Kill switch (global disable)
        2. Database override (tenant-specific from admin dashboard)
        3. Subscription features (from subscription_plans.features via auth)
        4. Global feature flags (from feature_flags_config)
        5. Default deny

        Accepts both camelCase (API format) and snake_case feature names.

        Args:
            feature_name: Feature to check (e.g., 'ai_controllers' or 'aiControllers')
            record_access: Whether to record this access attempt (for analytics)

        Returns:
            True if feature is enabled, False otherwise
        """
        internal_key, api_key = self._normalize_feature_key(feature_name)
        
        has_access = False
        denial_reason = None

        # 1. Check kill switch in global config (highest priority)
        if api_key in self.global_config:
            config = self.global_config[api_key]
            if not config.get("enabled_globally", True):
                _LOGGER.debug(f"Feature {feature_name} blocked by kill switch")
                has_access = False
                denial_reason = "kill_switch"
                # Skip other checks - kill switch is absolute
                return self._record_and_return(feature_name, has_access, denial_reason, record_access)
        
        # Also check internal key in global config
        if internal_key in self.global_config:
            config = self.global_config[internal_key]
            if not config.get("enabled_globally", True):
                _LOGGER.debug(f"Feature {feature_name} blocked by kill switch")
                has_access = False
                denial_reason = "kill_switch"
                return self._record_and_return(feature_name, has_access, denial_reason, record_access)

        # 2. Check database override (tenant-specific from admin dashboard)
        if api_key in self.db_overrides:
            has_access = bool(self.db_overrides[api_key])
            denial_reason = None if has_access else "admin_override"
            return self._record_and_return(feature_name, has_access, denial_reason, record_access)
        
        if internal_key in self.db_overrides:
            has_access = bool(self.db_overrides[internal_key])
            denial_reason = None if has_access else "admin_override"
            return self._record_and_return(feature_name, has_access, denial_reason, record_access)

        # 3. Check subscription features (from auth response - uses API camelCase keys)
        # The API sends features like: {"aiControllers": false, "pidControllers": false, ...}
        if api_key in self.features:
            has_access = bool(self.features[api_key])
            denial_reason = None if has_access else "subscription_plan"
            return self._record_and_return(feature_name, has_access, denial_reason, record_access)
        
        # Also check internal key in features (fallback)
        if internal_key in self.features:
            has_access = bool(self.features[internal_key])
            denial_reason = None if has_access else "subscription_plan"
            return self._record_and_return(feature_name, has_access, denial_reason, record_access)

        # 4. Check global feature flags (from feature_flags_config)
        # These would come from global_config with rollout settings
        if api_key in self.global_config:
            config = self.global_config[api_key]
            has_access = config.get("enabled_globally", False)
            denial_reason = None if has_access else "global_feature_disabled"
            return self._record_and_return(feature_name, has_access, denial_reason, record_access)

        # 5. Default deny - feature not found in any source
        _LOGGER.debug(f"Feature {feature_name} not found in any source, denying access")
        has_access = False
        denial_reason = "feature_not_configured"

        return self._record_and_return(feature_name, has_access, denial_reason, record_access)

    def _record_and_return(
        self, feature_name: str, has_access: bool, denial_reason: Optional[str], record_access: bool
    ) -> bool:
        """
        Record access attempt and return result.
        
        Args:
            feature_name: Feature that was checked
            has_access: Whether access was granted
            denial_reason: Reason for denial (if any)
            record_access: Whether to record this access attempt
            
        Returns:
            has_access value
        """
        # Record access attempt for analytics (if analytics module is integrated)
        if record_access and self.usage_metrics is not None:
            self.usage_metrics.record_feature_access(
                feature_name, has_access, denial_reason
            )
            
            # Show upgrade prompt if access denied and user should see it
            if not has_access and self.upgrade_prompts is not None:
                if self.usage_metrics.should_show_upgrade_prompt():
                    import asyncio
                    asyncio.create_task(
                        self.upgrade_prompts.show_upgrade_prompt(feature_name)
                    )

        return has_access

    def _has_tier_or_higher(self, required_tier: str) -> bool:
        """Check if user's tier is equal to or higher than required tier."""
        try:
            user_tier_index = self.TIER_HIERARCHY.index(self.plan_name.lower())
            required_tier_index = self.TIER_HIERARCHY.index(required_tier.lower())
            return user_tier_index >= required_tier_index
        except ValueError:
            # Invalid tier name
            _LOGGER.error(
                f"Invalid tier comparison: user={self.plan_name}, required={required_tier}"
            )
            return False

    def get_limit(self, limit_name: str, default: int = 0) -> int:
        """
        Get a subscription limit value.

        Args:
            limit_name: Limit to check (e.g., 'max_rooms', 'api_calls_per_day')
            default: Default value if limit not found

        Returns:
            Limit value
        """
        return self.limits.get(limit_name, default)

    def get_feature_info(self, feature_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a feature.

        Args:
            feature_name: Feature to query

        Returns:
            Dict with feature info or None if not found
        """
        if feature_name not in self.FEATURES:
            return None

        feature_def = self.FEATURES[feature_name]
        is_enabled = self.has_feature(feature_name)

        return {
            "name": feature_name,
            "enabled": is_enabled,
            "required_tier": feature_def["required_tier"],
            "description": feature_def["description"],
            "current_tier": self.plan_name,
        }

    def list_available_features(self) -> Dict[str, bool]:
        """
        List all features and their availability.

        Returns:
            Dict mapping feature names to enabled status
        """
        return {
            feature_name: self.has_feature(feature_name)
            for feature_name in self.FEATURES.keys()
        }

    def get_upgrade_requirements(self, feature_name: str) -> Optional[Dict[str, Any]]:
        """
        Get upgrade requirements for a locked feature.

        Args:
            feature_name: Feature to check

        Returns:
            Dict with upgrade info, or None if feature is already enabled
        """
        if self.has_feature(feature_name):
            return None  # Already have access

        if feature_name not in self.FEATURES:
            return {"error": "Unknown feature"}

        required_tier = self.FEATURES[feature_name]["required_tier"]

        return {
            "feature": feature_name,
            "current_tier": self.plan_name,
            "required_tier": required_tier,
            "upgrade_url": f"https://opengrowbox.com/pricing?upgrade={required_tier}",
            "description": self.FEATURES[feature_name]["description"],
        }

    def is_premium(self) -> bool:
        """Check if user has any premium tier (not free)."""
        return self.plan_name != "free"

    def get_subscription_summary(self) -> Dict[str, Any]:
        """
        Get summary of current subscription.

        Returns:
            Dict with subscription details
        """
        enabled_features = [
            name for name, enabled in self.list_available_features().items() if enabled
        ]

        return {
            "plan_name": self.plan_name,
            "is_premium": self.is_premium(),
            "enabled_features": enabled_features,
            "feature_count": len(enabled_features),
            "limits": self.limits,
            "last_update": self.last_update.isoformat(),
        }

    def get_room_limit(self, tier: Optional[str]) -> int:
        """
        Get the room limit for a given tier.

        Uses dynamic limits from API subscription_data.limits.max_rooms.
        Defaults to 999 if not specified by API (effectively unlimited).

        Args:
            tier: Subscription tier name (for compatibility, not used)

        Returns:
            Maximum number of rooms allowed
        """
        # Get from dynamic API limits only
        max_rooms = self.limits.get("max_rooms")
        if max_rooms is not None:
            return max_rooms

        # Default to large number if API doesn't specify room limits
        return 999

    def can_create_room(
        self, current_rooms: int, tier: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check if user can create another room based on their subscription tier.

        Args:
            current_rooms: Number of rooms user currently has
            tier: Override tier (uses self.plan_name if None)

        Returns:
            Dict with 'allowed': bool and optional error message
        """
        if tier is None:
            tier = self.plan_name

        room_limit = self.get_room_limit(tier)
        """
        Check if user can create another room based on their subscription tier.

        Args:
            current_rooms: Number of rooms user currently has
            tier: Override tier (uses self.plan_name if None)

        Returns:
            Dict with 'allowed': bool and optional error message
        """
        if tier is None:
            tier = self.plan_name

        room_limit = self.get_room_limit(tier)

        if current_rooms < room_limit:
            return {
                "allowed": True,
                "current_rooms": current_rooms,
                "room_limit": room_limit,
                "remaining_rooms": (
                    room_limit - current_rooms
                    if room_limit != float("inf")
                    else float("inf")
                ),
            }
        else:
            # Room limit exceeded
            next_tier = self._get_next_tier_for_rooms(current_rooms + 1)
            return {
                "allowed": False,
                "reason": "room_limit_exceeded",
                "current_rooms": current_rooms,
                "room_limit": room_limit,
                "required_tier": next_tier,
                "upgrade_url": f"https://opengrowbox.com/pricing?upgrade={next_tier}",
                "message": f"Room limit exceeded ({current_rooms}/{room_limit}). Upgrade to {next_tier.title()} plan for more rooms.",
            }

    def _get_next_tier_for_rooms(self, required_rooms: int) -> str:
        """
        Get the minimum tier required for a given number of rooms.

        Args:
            required_rooms: Number of rooms needed

        Returns:
            Tier name that supports the required rooms
        """
        if required_rooms <= 1:
            return "free"
        elif required_rooms <= 2:
            return "starter"
        elif required_rooms <= 5:
            return "grower"
        elif required_rooms <= 15:
            return "professional"
        else:
            return "enterprise"

    def get_room_upgrade_info(self, current_rooms: int) -> Optional[Dict[str, Any]]:
        """
        Get upgrade information for room limits.

        Args:
            current_rooms: Current number of rooms

        Returns:
            Upgrade info dict if upgrade needed, None if current tier is sufficient
        """
        check_result = self.can_create_room(current_rooms)
        if check_result["allowed"]:
            return None  # No upgrade needed

        return {
            "type": "room_limit",
            "current_rooms": current_rooms,
            "room_limit": check_result["room_limit"],
            "required_tier": check_result["required_tier"],
            "upgrade_url": check_result["upgrade_url"],
            "message": check_result["message"],
        }

    def __repr__(self) -> str:
        """String representation for debugging."""
        feature_count = len([f for f, e in self.features.items() if e])
        return f"<OGBFeatureManager plan={self.plan_name} features={feature_count}>"

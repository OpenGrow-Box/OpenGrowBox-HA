"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                    Premium Service Decorators & Utilities                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file contains PREMIUM service gating logic.

Provides decorators to gate HA services behind feature flags.
Services requiring premium features will check subscription tier before execution.

Features:
- @require_feature() decorator for automatic service gating
- Feature access validation before service execution
- Automatic upgrade event firing when access denied
- 7 premium services + 1 utility service (clear_cache)
"""

import logging
from functools import wraps
from typing import Callable, Optional

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def require_feature(feature_name: str, required_tier: str = "professional"):
    """
    Decorator to gate a Home Assistant service behind a feature flag.

    Args:
        feature_name: Feature to check (e.g., 'advanced_analytics', 'compliance')
        required_tier: Minimum tier required (free, basic, professional, enterprise)

    Usage:
        @require_feature('advanced_analytics', 'basic')
        async def get_yield_prediction(hass, call):
            # Service implementation
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(hass: HomeAssistant, call: ServiceCall):
            """Wrapped service call with feature check."""
            try:
                # Get room from service call data
                room = call.data.get("room")
                if not room:
                    raise ServiceValidationError(
                        f"Service {func.__name__} requires 'room' parameter"
                    )

                # Get coordinator for this room
                coordinator = _get_coordinator_for_room(hass, room)
                if not coordinator:
                    raise ServiceValidationError(
                        f"No OpenGrowBox instance found for room: {room}"
                    )

                # Get feature manager from premium manager
                prem_manager = getattr(coordinator.OGB, room, None)
                if not prem_manager:
                    raise ServiceValidationError(
                        f"Premium manager not initialized for room: {room}"
                    )

                feature_manager = getattr(prem_manager, "feature_manager", None)
                if not feature_manager:
                    # No feature manager = free tier
                    _fire_upgrade_required_event(
                        hass, room, feature_name, required_tier, func.__name__
                    )
                    raise ServiceValidationError(
                        f"This service requires {required_tier} plan or higher. "
                        f"Current plan: free. Please upgrade to access '{feature_name}' feature."
                    )

                # Check if user has the required feature
                if not feature_manager.has_feature(feature_name):
                    current_tier = feature_manager.plan_name
                    _fire_upgrade_required_event(
                        hass, room, feature_name, required_tier, func.__name__
                    )
                    raise ServiceValidationError(
                        f"This service requires '{feature_name}' feature ({required_tier} plan). "
                        f"Current plan: {current_tier}. Please upgrade to access this service."
                    )

                # Feature check passed, execute service
                _LOGGER.debug(
                    f"✅ {room} Service {func.__name__} authorized "
                    f"(feature: {feature_name}, tier: {feature_manager.plan_name})"
                )
                return await func(hass, call)

            except ServiceValidationError:
                raise  # Re-raise validation errors
            except Exception as e:
                _LOGGER.error(
                    f"Error in premium service {func.__name__}: {e}", exc_info=True
                )
                raise ServiceValidationError(f"Service execution failed: {str(e)}")

        return wrapper

    return decorator


def _get_coordinator_for_room(hass: HomeAssistant, room: str):
    """Get coordinator instance for a specific room."""
    if DOMAIN not in hass.data:
        return None

    # Iterate through all config entries to find the right coordinator
    for entry_id, coordinator in hass.data[DOMAIN].items():
        if hasattr(coordinator, "OGB") and hasattr(coordinator.OGB, room):
            return coordinator

    return None


def _fire_upgrade_required_event(
    hass: HomeAssistant,
    room: str,
    feature_name: str,
    required_tier: str,
    service_name: str,
):
    """Fire an event to notify frontend that upgrade is required."""
    hass.bus.async_fire(
        "opengrowbox_upgrade_required",
        {
            "room": room,
            "feature_name": feature_name,
            "required_tier": required_tier,
            "service_name": service_name,
            "upgrade_url": f"https://opengrowbox.com/pricing?upgrade={required_tier}",
            "timestamp": hass.loop.time(),
        },
    )

    _LOGGER.warning(
        f"🔒 {room} Service '{service_name}' blocked: requires '{feature_name}' "
        f"feature ({required_tier} plan)"
    )


async def register_premium_services(hass: HomeAssistant, coordinator):
    """
    Register all premium services for OpenGrowBox.

    Called from coordinator during setup.

    Args:
        hass: Home Assistant instance
        coordinator: OGBIntegrationCoordinator instance
    """
    # Analytics Services

    @require_feature("advanced_analytics", "basic")
    async def get_yield_prediction(hass: HomeAssistant, call: ServiceCall):
        """Get AI-powered yield prediction for a grow plan."""
        room = call.data.get("room")
        grow_plan_id = call.data.get("grow_plan_id")

        prem_manager = getattr(coordinator.OGB, room)
        if not prem_manager.analytics:
            raise ServiceValidationError("Analytics module not initialized")

        result = await prem_manager.analytics.predict_yield(grow_plan_id)

        # Fire event with result
        hass.bus.async_fire(
            "opengrowbox_yield_prediction",
            {"room": room, "grow_plan_id": grow_plan_id, "prediction": result},
        )

        return result

    @require_feature("advanced_analytics", "basic")
    async def get_environmental_insights(hass: HomeAssistant, call: ServiceCall):
        """Get environmental performance insights."""
        room = call.data.get("room")
        timeframe = call.data.get("timeframe", "7d")

        prem_manager = getattr(coordinator.OGB, room)
        if not prem_manager.analytics:
            raise ServiceValidationError("Analytics module not initialized")

        result = await prem_manager.analytics.get_environmental_insights(timeframe)

        hass.bus.async_fire(
            "opengrowbox_environmental_insights",
            {"room": room, "timeframe": timeframe, "insights": result},
        )

        return result

    @require_feature("advanced_analytics", "basic")
    async def detect_anomalies(hass: HomeAssistant, call: ServiceCall):
        """Detect environmental anomalies using ML."""
        room = call.data.get("room")

        prem_manager = getattr(coordinator.OGB, room)
        if not prem_manager.analytics:
            raise ServiceValidationError("Analytics module not initialized")

        result = await prem_manager.analytics.detect_anomalies()

        hass.bus.async_fire(
            "opengrowbox_anomalies_detected", {"room": room, "anomalies": result}
        )

        return result

    # Compliance Services

    @require_feature("compliance", "professional")
    async def validate_compliance(hass: HomeAssistant, call: ServiceCall):
        """Validate grow plan against regulatory compliance rules."""
        room = call.data.get("room")
        grow_plan_id = call.data.get("grow_plan_id")
        industry = call.data.get("industry", "cannabis")

        prem_manager = getattr(coordinator.OGB, room)
        if not prem_manager.compliance:
            raise ServiceValidationError("Compliance module not initialized")

        result = await prem_manager.compliance.validate_compliance(
            grow_plan_id, industry
        )

        hass.bus.async_fire(
            "opengrowbox_compliance_validated",
            {
                "room": room,
                "grow_plan_id": grow_plan_id,
                "industry": industry,
                "validation": result,
            },
        )

        return result

    @require_feature("advanced_compliance", "professional")
    async def get_audit_trail(hass: HomeAssistant, call: ServiceCall):
        """Get compliance audit trail."""
        room = call.data.get("room")
        start_date = call.data.get("start_date")
        end_date = call.data.get("end_date")
        limit = call.data.get("limit", 100)

        prem_manager = getattr(coordinator.OGB, room)
        if not prem_manager.compliance:
            raise ServiceValidationError("Compliance module not initialized")

        result = await prem_manager.compliance.get_audit_trail(
            start_date, end_date, limit
        )

        return result

    # Research Services

    @require_feature("research_data", "professional")
    async def create_research_dataset(hass: HomeAssistant, call: ServiceCall):
        """Create a new research dataset."""
        room = call.data.get("room")
        name = call.data.get("name")
        description = call.data.get("description")
        grow_plan_ids = call.data.get("grow_plan_ids", [])

        prem_manager = getattr(coordinator.OGB, room)
        if not prem_manager.research:
            raise ServiceValidationError("Research module not initialized")

        result = await prem_manager.research.create_dataset(
            name=name, description=description, grow_plan_ids=grow_plan_ids
        )

        hass.bus.async_fire(
            "opengrowbox_dataset_created", {"room": room, "dataset": result}
        )

        return result

    @require_feature("research_data", "professional")
    async def export_research_data(hass: HomeAssistant, call: ServiceCall):
        """Export research dataset in specified format."""
        room = call.data.get("room")
        dataset_id = call.data.get("dataset_id")
        format = call.data.get("format", "csv")

        prem_manager = getattr(coordinator.OGB, room)
        if not prem_manager.research:
            raise ServiceValidationError("Research module not initialized")

        result = await prem_manager.research.export_data(dataset_id, format)

        return result

    # Cache Management Service (no feature flag required - available to all users)
    async def clear_cache(hass: HomeAssistant, call: ServiceCall):
        """
        Clear cached premium data to force refresh from API.

        Available to all users (no feature flag required).
        Useful for troubleshooting or forcing data refresh.
        """
        room = call.data.get("room")
        cache_type = call.data.get(
            "cache_type", "all"
        )  # all, analytics, compliance, research

        if not room:
            raise ServiceValidationError(
                "Service clear_cache requires 'room' parameter"
            )

        # Get coordinator for this room
        coordinator = _get_coordinator_for_room(hass, room)
        if not coordinator:
            raise ServiceValidationError(
                f"No OpenGrowBox instance found for room: {room}"
            )

        prem_manager = getattr(coordinator.OGB, room, None)
        if not prem_manager:
            raise ServiceValidationError(
                f"Premium manager not initialized for room: {room}"
            )

        # Check if cache is available
        if not prem_manager.cache:
            _LOGGER.warning(f"{room} No cache available to clear")
            return {"success": False, "reason": "Cache not initialized"}

        # Clear cache based on type
        try:
            if cache_type == "all":
                await prem_manager.cache.clear_all()
                _LOGGER.info(f"✅ {room} All caches cleared")
            elif cache_type == "analytics":
                if prem_manager.analytics:
                    await prem_manager.analytics.invalidate_cache()
                await prem_manager.cache.invalidate_analytics()
                _LOGGER.info(f"✅ {room} Analytics cache cleared")
            elif cache_type == "compliance":
                if prem_manager.compliance:
                    await prem_manager.compliance.invalidate_cache()
                await prem_manager.cache.invalidate_compliance()
                _LOGGER.info(f"✅ {room} Compliance cache cleared")
            elif cache_type == "research":
                if prem_manager.research:
                    await prem_manager.research.invalidate_cache()
                await prem_manager.cache.invalidate_datasets()
                _LOGGER.info(f"✅ {room} Research cache cleared")
            else:
                raise ServiceValidationError(
                    f"Invalid cache_type: {cache_type}. "
                    f"Must be one of: all, analytics, compliance, research"
                )

            # Fire event to notify frontend
            hass.bus.async_fire(
                "opengrowbox_cache_cleared",
                {"room": room, "cache_type": cache_type, "timestamp": hass.loop.time()},
            )

            return {"success": True, "cache_type": cache_type}

        except Exception as e:
            _LOGGER.error(f"❌ {room} Failed to clear cache: {e}")
            raise ServiceValidationError(f"Failed to clear cache: {str(e)}")

    # === Subscription Tier Management Services ===

    async def get_user_subscription(hass: HomeAssistant, call: ServiceCall):
        """Get user's subscription details."""
        try:
            user_id = call.data.get("user_id")
            if not user_id:
                raise ServiceValidationError("user_id is required")

            # Get any room to access the cache system
            for config_entry_id in hass.data[DOMAIN]:
                coordinator = hass.data[DOMAIN][config_entry_id]
                if hasattr(coordinator.OGB, "cache"):
                    cache = coordinator.OGB.cache
                    subscription = await cache.get_user_subscription(user_id)
                    return {"subscription": subscription}

            raise ServiceValidationError("Cache system not available")

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get user subscription: {e}")
            raise ServiceValidationError(f"Failed to get subscription: {str(e)}")

    async def update_user_subscription(hass: HomeAssistant, call: ServiceCall):
        """Update user's subscription tier."""
        try:
            user_id = call.data.get("user_id")
            tier_name = call.data.get("tier_name")
            room_count = call.data.get("room_count", 0)

            if not user_id or not tier_name:
                raise ServiceValidationError("user_id and tier_name are required")

            # Get any room to access the cache system
            for config_entry_id in hass.data[DOMAIN]:
                coordinator = hass.data[DOMAIN][config_entry_id]
                if hasattr(coordinator.OGB, "cache"):
                    cache = coordinator.OGB.cache
                    success = await cache.update_user_subscription(
                        user_id, tier_name, room_count
                    )

                    if success:
                        _LOGGER.info(
                            f"💳 Updated subscription: {user_id[:6]} → {tier_name}"
                        )

                        # Fire HA event for subscription update
                        hass.bus.async_fire(
                            "ogb_subscription_updated",
                            {
                                "user_id": user_id,
                                "tier_name": tier_name,
                                "room_count": room_count,
                                "timestamp": hass.loop.time(),
                            },
                        )

                        return {
                            "success": True,
                            "tier_name": tier_name,
                            "room_count": room_count,
                        }
                    else:
                        raise ServiceValidationError("Failed to update subscription")

            raise ServiceValidationError("Cache system not available")

        except Exception as e:
            _LOGGER.error(f"❌ Failed to update user subscription: {e}")
            raise ServiceValidationError(f"Failed to update subscription: {str(e)}")

    async def get_all_tiers(hass: HomeAssistant, call: ServiceCall):
        """Get all available subscription tiers."""
        try:
            # Get any room to access the cache system
            for config_entry_id in hass.data[DOMAIN]:
                coordinator = hass.data[DOMAIN][config_entry_id]
                if hasattr(coordinator.OGB, "cache"):
                    cache = coordinator.OGB.cache
                    tiers = await cache.get_all_tiers()
                    return {"tiers": tiers}

            raise ServiceValidationError("Cache system not available")

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get tiers: {e}")
            raise ServiceValidationError(f"Failed to get tiers: {str(e)}")

    async def increment_user_rooms(hass: HomeAssistant, call: ServiceCall):
        """Increment user's room count (system use)."""
        try:
            user_id = call.data.get("user_id")
            if not user_id:
                raise ServiceValidationError("user_id is required")

            # Get any room to access the cache system
            for config_entry_id in hass.data[DOMAIN]:
                coordinator = hass.data[DOMAIN][config_entry_id]
                if hasattr(coordinator.OGB, "cache"):
                    cache = coordinator.OGB.cache
                    success = await cache.increment_room_count(user_id)

                    if success:
                        _LOGGER.info(
                            f"🏠 Incremented room count for user: {user_id[:6]}"
                        )
                        return {"success": True}
                    else:
                        # User not found, create with free tier
                        await cache.update_user_subscription(user_id, "free", 1)
                        return {"success": True, "created": True}

            raise ServiceValidationError("Cache system not available")

        except Exception as e:
            _LOGGER.error(f"❌ Failed to increment user rooms: {e}")
            raise ServiceValidationError(f"Failed to increment rooms: {str(e)}")

    # Register all services
    hass.services.async_register(DOMAIN, "get_yield_prediction", get_yield_prediction)
    hass.services.async_register(
        DOMAIN, "get_environmental_insights", get_environmental_insights
    )
    hass.services.async_register(DOMAIN, "detect_anomalies", detect_anomalies)
    hass.services.async_register(DOMAIN, "validate_compliance", validate_compliance)
    hass.services.async_register(DOMAIN, "get_audit_trail", get_audit_trail)
    hass.services.async_register(
        DOMAIN, "create_research_dataset", create_research_dataset
    )
    hass.services.async_register(DOMAIN, "export_research_data", export_research_data)
    hass.services.async_register(DOMAIN, "clear_cache", clear_cache)

    # Register subscription management services
    hass.services.async_register(DOMAIN, "get_user_subscription", get_user_subscription)
    hass.services.async_register(
        DOMAIN, "update_user_subscription", update_user_subscription
    )
    hass.services.async_register(DOMAIN, "get_all_tiers", get_all_tiers)
    hass.services.async_register(DOMAIN, "increment_user_rooms", increment_user_rooms)

    # === Feature Management Services ===
    # These services provide API-compatible feature checking for the frontend

    async def get_available_features(hass: HomeAssistant, call: ServiceCall):
        """Get list of available features for current subscription."""
        try:
            room = call.data.get("room")
            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            if prem_manager.feature_service:
                features = prem_manager.feature_service.get_available_features()
                tier = prem_manager.feature_service._get_current_tier()
                return {"features": features, "tier": tier}
            else:
                # Fallback to free tier features
                return {
                    "features": ["basic_monitoring", "ai_controllers", "mobile_app"],
                    "tier": "free",
                }

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get available features: {e}")
            raise ServiceValidationError(f"Failed to get features: {str(e)}")

    async def check_feature_access(hass: HomeAssistant, call: ServiceCall):
        """Check if user has access to a specific feature."""
        try:
            room = call.data.get("room")
            feature_id = call.data.get("feature_id")

            if not room or not feature_id:
                raise ServiceValidationError("room and feature_id are required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            if prem_manager.feature_service:
                accessible = prem_manager.feature_service.check_feature_access(
                    feature_id
                )
                feature_info = prem_manager.feature_service.get_feature_info(feature_id)
                return {"accessible": accessible, "feature_info": feature_info}
            else:
                # Fallback - deny non-free features
                free_features = ["basic_monitoring", "ai_controllers", "mobile_app"]
                return {"accessible": feature_id in free_features, "feature_info": None}

        except Exception as e:
            _LOGGER.error(f"❌ Failed to check feature access: {e}")
            raise ServiceValidationError(f"Failed to check access: {str(e)}")

    async def get_feature_definitions(hass: HomeAssistant, call: ServiceCall):
        """Get all feature definitions with metadata."""
        try:
            room = call.data.get("room")
            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            if prem_manager.feature_service:
                definitions = prem_manager.feature_service.get_all_definitions()
                return {"features": definitions}
            else:
                # Return minimal fallback definitions
                from .OGBController.OGBFeatureService import OGBFeatureService

                return {
                    "features": list(OGBFeatureService.FEATURE_DEFINITIONS.values())
                }

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get feature definitions: {e}")
            raise ServiceValidationError(f"Failed to get definitions: {str(e)}")

    async def get_subscription_summary(hass: HomeAssistant, call: ServiceCall):
        """Get subscription summary."""
        try:
            room = call.data.get("room")
            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            if prem_manager.feature_service:
                summary = prem_manager.feature_service.get_subscription_summary()
                return summary
            else:
                return {
                    "plan_name": "free",
                    "is_premium": False,
                    "enabled_features": [
                        "basic_monitoring",
                        "ai_controllers",
                        "mobile_app",
                    ],
                    "feature_count": 3,
                    "limits": {"max_rooms": 1},
                }

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get subscription summary: {e}")
            raise ServiceValidationError(f"Failed to get summary: {str(e)}")

    async def get_room_limits(hass: HomeAssistant, call: ServiceCall):
        """Get room limit information."""
        try:
            room = call.data.get("room")
            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            if prem_manager.feature_manager:
                current_tier = prem_manager.feature_manager.plan_name
                room_limit = prem_manager.feature_manager.get_room_limit(current_tier)
                current_rooms = (
                    prem_manager.ogb_ws.ogb_sessions if prem_manager.ogb_ws else 0
                )
                return {
                    "tier": current_tier,
                    "room_limit": room_limit if room_limit != float("inf") else -1,
                    "current_rooms": current_rooms,
                    "can_create_more": current_rooms < room_limit,
                }
            else:
                return {
                    "tier": "free",
                    "room_limit": 1,
                    "current_rooms": 0,
                    "can_create_more": True,
                }

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get room limits: {e}")
            raise ServiceValidationError(f"Failed to get limits: {str(e)}")

    async def check_can_create_room(hass: HomeAssistant, call: ServiceCall):
        """Check if user can create a new room."""
        try:
            room = call.data.get("room")
            current_room_count = call.data.get("current_room_count", 0)

            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            if prem_manager.feature_manager:
                result = prem_manager.feature_manager.can_create_room(
                    current_room_count
                )
                return result
            else:
                # Free tier fallback
                allowed = current_room_count < 1
                return {
                    "allowed": allowed,
                    "current_rooms": current_room_count,
                    "room_limit": 1,
                    "message": "Free tier allows 1 room" if not allowed else None,
                }

        except Exception as e:
            _LOGGER.error(f"❌ Failed to check room creation: {e}")
            raise ServiceValidationError(f"Failed to check: {str(e)}")

    # Register feature management services
    hass.services.async_register(
        DOMAIN, "get_available_features", get_available_features
    )
    hass.services.async_register(DOMAIN, "check_feature_access", check_feature_access)
    hass.services.async_register(
        DOMAIN, "get_feature_definitions", get_feature_definitions
    )
    hass.services.async_register(
        DOMAIN, "get_subscription_summary", get_subscription_summary
    )
    hass.services.async_register(DOMAIN, "get_room_limits", get_room_limits)
    hass.services.async_register(DOMAIN, "check_can_create_room", check_can_create_room)

    # === Feature Flag Control Services (API Integration) ===

    async def refresh_feature_flags(hass: HomeAssistant, call: ServiceCall):
        """
        Refresh feature flags from the Premium API.

        Forces a refresh of feature flag overrides and global configs from the server.
        Useful when admin dashboard changes need to be reflected immediately.
        """
        try:
            room = call.data.get("room")
            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            # Call the refresh method
            result = await prem_manager.refresh_feature_flags()

            if result.get("success"):
                _LOGGER.info(f"✅ {room} Feature flags refreshed via service call")

                # Fire HA event
                hass.bus.async_fire(
                    "ogb_feature_flags_refreshed",
                    {
                        "room": room,
                        "plan_name": result.get("plan_name"),
                        "override_count": result.get("override_count"),
                        "timestamp": result.get("timestamp"),
                    },
                )

            return result

        except Exception as e:
            _LOGGER.error(f"❌ Failed to refresh feature flags: {e}")
            raise ServiceValidationError(f"Failed to refresh feature flags: {str(e)}")

    async def get_feature_flag_status(hass: HomeAssistant, call: ServiceCall):
        """
        Get status of a specific feature flag.

        Returns whether the feature is enabled, the source of the decision
        (subscription, override, or kill switch), and required tier information.
        """
        try:
            room = call.data.get("room")
            feature_key = call.data.get("feature_key")

            if not room or not feature_key:
                raise ServiceValidationError("room and feature_key are required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            # Get feature status
            result = prem_manager.get_feature_status(feature_key)
            return result

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get feature flag status: {e}")
            raise ServiceValidationError(f"Failed to get feature status: {str(e)}")

    async def get_all_feature_flags(hass: HomeAssistant, call: ServiceCall):
        """
        Get status of all feature flags.

        Returns a complete overview of all features, their enabled status,
        override sources, and tier requirements.
        """
        try:
            room = call.data.get("room")
            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            # Get all features status
            result = prem_manager.get_all_features_status()
            return result

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get all feature flags: {e}")
            raise ServiceValidationError(f"Failed to get feature flags: {str(e)}")

    async def check_feature_enabled(hass: HomeAssistant, call: ServiceCall):
        """
        Simple check if a feature is enabled.

        Returns a boolean indicating whether the feature is currently enabled
        for the user's subscription and any active overrides.
        """
        try:
            room = call.data.get("room")
            feature_key = call.data.get("feature_key")

            if not room or not feature_key:
                raise ServiceValidationError("room and feature_key are required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            # Quick feature check
            if prem_manager.feature_manager:
                enabled = prem_manager.feature_manager.has_feature(feature_key)
                return {"feature_key": feature_key, "enabled": enabled, "room": room}
            else:
                # No feature manager = free tier, check if it's a free feature
                free_features = ["basic_monitoring", "ai_controllers", "mobile_app"]
                return {
                    "feature_key": feature_key,
                    "enabled": feature_key in free_features,
                    "room": room,
                    "tier": "free",
                }

        except Exception as e:
            _LOGGER.error(f"❌ Failed to check feature: {e}")
            raise ServiceValidationError(f"Failed to check feature: {str(e)}")

    async def get_feature_overrides(hass: HomeAssistant, call: ServiceCall):
        """
        Get all active feature overrides for the current tenant.

        Returns the list of feature flags that have been overridden by admin.
        """
        try:
            room = call.data.get("room")
            if not room:
                raise ServiceValidationError("room is required")

            coordinator = _get_coordinator_for_room(hass, room)
            if not coordinator:
                raise ServiceValidationError(
                    f"No OpenGrowBox instance found for room: {room}"
                )

            prem_manager = getattr(coordinator.OGB, room, None)
            if not prem_manager:
                raise ServiceValidationError(
                    f"Premium manager not initialized for room: {room}"
                )

            if prem_manager.feature_manager:
                return {
                    "room": room,
                    "overrides": prem_manager.feature_manager.db_overrides,
                    "global_config": prem_manager.feature_manager.global_config,
                    "cache_valid": prem_manager.feature_manager.is_cache_valid(),
                }
            else:
                return {
                    "room": room,
                    "overrides": {},
                    "global_config": {},
                    "cache_valid": False,
                }

        except Exception as e:
            _LOGGER.error(f"❌ Failed to get feature overrides: {e}")
            raise ServiceValidationError(f"Failed to get overrides: {str(e)}")

    # Register feature flag control services
    hass.services.async_register(DOMAIN, "refresh_feature_flags", refresh_feature_flags)
    hass.services.async_register(
        DOMAIN, "get_feature_flag_status", get_feature_flag_status
    )
    hass.services.async_register(DOMAIN, "get_all_feature_flags", get_all_feature_flags)
    hass.services.async_register(DOMAIN, "check_feature_enabled", check_feature_enabled)
    hass.services.async_register(DOMAIN, "get_feature_overrides", get_feature_overrides)

    _LOGGER.info(
        "✅ Registered 23 services (7 premium + 1 utility + 4 subscription + 6 feature management + 5 feature flag control)"
    )

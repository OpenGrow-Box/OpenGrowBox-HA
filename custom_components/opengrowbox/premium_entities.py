"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                         Premium Entity Base Classes                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file contains PREMIUM entity base classes and mixins.

Provides base classes for entities that require premium features.
Entities will show "locked" state if user doesn't have required feature.

Features:
- PremiumEntityMixin: Mixin class for feature-gated entities
- Automatic "locked" state display for free tier
- Lock icon override when feature not available
- Upgrade URL attributes for frontend integration
- Entity auto-disable for free tier (prevents UI clutter)
"""

import logging
from typing import Optional

from homeassistant.helpers.entity import Entity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class PremiumEntityMixin:
    """
    Mixin for entities that require premium features.

    Add this mixin to any entity class to gate it behind a feature flag.

    Usage:
        class AnalyticsSensor(PremiumEntityMixin, CustomSensor):
            def __init__(self, ...):
                super().__init__(...)
                self.required_feature = 'advanced_analytics'
                self.required_tier = 'basic'
    """

    required_feature: Optional[str] = None
    required_tier: Optional[str] = "professional"

    def _has_feature_access(self) -> bool:
        """
        Check if user has access to this entity's required feature.

        Returns:
            True if user has access, False otherwise
        """
        if not self.required_feature:
            # No feature required, always accessible
            return True

        try:
            # Get coordinator
            coordinator = self.coordinator
            if not coordinator:
                _LOGGER.warning(f"{self.name}: No coordinator available")
                return False

            # Get feature manager from premium manager
            # Navigate: coordinator -> OGB -> {room} -> feature_manager
            room_name = self.room_name
            prem_manager = getattr(coordinator.OGB, room_name, None)

            if not prem_manager:
                _LOGGER.debug(
                    f"{self.name}: Premium manager not found for room {room_name}"
                )
                return False

            feature_manager = getattr(prem_manager, "feature_manager", None)
            if not feature_manager:
                # No feature manager = free tier
                return False

            # Check if user has the required feature
            has_access = feature_manager.has_feature(self.required_feature)

            if not has_access:
                _LOGGER.debug(
                    f"{self.name}: Feature '{self.required_feature}' not available "
                    f"(current tier: {feature_manager.plan_name}, required: {self.required_tier})"
                )

            return has_access

        except Exception as e:
            _LOGGER.error(f"{self.name}: Error checking feature access: {e}")
            return False

    @property
    def available(self) -> bool:
        """
        Override available property to show entity as unavailable if locked.

        Returns:
            False if feature not available, otherwise calls parent's available property
        """
        # Check feature access first
        if not self._has_feature_access():
            return False

        # If feature is available, check parent's availability
        if hasattr(super(), "available"):
            return super().available

        return True

    @property
    def state(self):
        """
        Override state to show locked indicator if no access.

        Returns:
            "locked" if feature not available, otherwise actual state
        """
        if not self._has_feature_access():
            return "locked"

        # Return actual state from parent class
        if hasattr(super(), "state"):
            return super().state

        return None

    @property
    def extra_state_attributes(self):
        """
        Add premium status to entity attributes.

        Returns:
            Dict with premium status and upgrade info
        """
        # Get parent attributes
        attrs = {}
        if hasattr(super(), "extra_state_attributes"):
            parent_attrs = super().extra_state_attributes
            if parent_attrs:
                attrs.update(parent_attrs)

        # Add premium status
        has_access = self._has_feature_access()
        attrs["is_premium_feature"] = True
        attrs["required_feature"] = self.required_feature
        attrs["required_tier"] = self.required_tier
        attrs["has_access"] = has_access

        if not has_access:
            attrs["upgrade_required"] = True
            attrs["upgrade_url"] = (
                f"https://opengrowbox.com/pricing?upgrade={self.required_tier}"
            )
            attrs["locked_reason"] = f"Requires {self.required_tier} plan or higher"

        return attrs

    @property
    def icon(self):
        """
        Show lock icon if feature not available.

        Returns:
            Lock icon if locked, otherwise parent's icon
        """
        if not self._has_feature_access():
            return "mdi:lock"

        if hasattr(super(), "icon"):
            return super().icon

        return None

    @property
    def entity_registry_enabled_default(self) -> bool:
        """
        Entities are disabled by default if feature not available.

        This prevents clutter in the UI for free tier users.

        Returns:
            False if feature not available, True otherwise
        """
        # Premium entities disabled by default for free tier
        # User can manually enable them to see upgrade prompts
        return self._has_feature_access()


def should_register_premium_entity(
    coordinator, room_name: str, feature_name: str
) -> bool:
    """
    Helper function to determine if a premium entity should be registered.

    Used during entity setup to conditionally register premium entities.

    Args:
        coordinator: OGBIntegrationCoordinator instance
        room_name: Room name
        feature_name: Required feature name

    Returns:
        True if entity should be registered (feature available or user opted in to see locked entities)

    Usage:
        if should_register_premium_entity(coordinator, room_name, 'advanced_analytics'):
            sensors.append(YieldPredictionSensor(...))
    """
    try:
        # Get premium manager
        prem_manager = getattr(coordinator.OGB, room_name, None)
        if not prem_manager:
            # No premium manager - don't register premium entities
            return False

        feature_manager = getattr(prem_manager, "feature_manager", None)
        if not feature_manager:
            # Free tier - register entities as locked (user can see upgrade prompts)
            # This is a UX decision: show locked entities vs hide completely
            # Currently: SHOW locked entities to encourage upgrades
            return True

        # Premium user - always register (will show as locked if feature not available)
        return True

    except Exception as e:
        _LOGGER.error(f"Error checking entity registration for {feature_name}: {e}")
        return False


class PremiumSensorConfig:
    """
    Configuration for premium sensors.

    Defines which sensors require which features.
    """

    ANALYTICS_SENSORS = {
        "yield_prediction": {
            "required_feature": "advanced_analytics",
            "required_tier": "basic",
            "name_suffix": "Yield_Prediction",
            "device_class": None,
            "unit": None,
            "icon": "mdi:chart-line",
        },
        "anomaly_score": {
            "required_feature": "advanced_analytics",
            "required_tier": "basic",
            "name_suffix": "Anomaly_Score",
            "device_class": None,
            "unit": "%",
            "icon": "mdi:alert-circle",
        },
        "performance_score": {
            "required_feature": "advanced_analytics",
            "required_tier": "basic",
            "name_suffix": "Performance_Score",
            "device_class": None,
            "unit": "%",
            "icon": "mdi:chart-box",
        },
    }

    COMPLIANCE_SENSORS = {
        "compliance_status": {
            "required_feature": "compliance",
            "required_tier": "professional",
            "name_suffix": "Compliance_Status",
            "device_class": None,
            "unit": None,
            "icon": "mdi:shield-check",
        },
        "violations_count": {
            "required_feature": "compliance",
            "required_tier": "professional",
            "name_suffix": "Compliance_Violations",
            "device_class": None,
            "unit": "violations",
            "icon": "mdi:alert",
        },
    }

    RESEARCH_SENSORS = {
        "dataset_count": {
            "required_feature": "research_data",
            "required_tier": "professional",
            "name_suffix": "Research_Datasets",
            "device_class": None,
            "unit": "datasets",
            "icon": "mdi:database",
        },
        "data_quality": {
            "required_feature": "research_data",
            "required_tier": "professional",
            "name_suffix": "Data_Quality_Score",
            "device_class": None,
            "unit": "%",
            "icon": "mdi:quality-high",
        },
    }

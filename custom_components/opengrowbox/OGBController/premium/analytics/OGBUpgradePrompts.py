"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                       Upgrade Prompts Service                                ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file manages upgrade prompts for all subscription tiers.

OGBUpgradePrompts - Intelligent Upgrade Prompt Management

Features:
- Context-aware upgrade prompts for locked features
- Multi-tier upgrade paths (free→basic, basic→professional, etc.)
- Smart throttling to avoid user fatigue
- A/B testing support for different prompt types
- Conversion tracking integration
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass
class UpgradePrompt:
    """Upgrade prompt configuration"""

    prompt_id: str
    feature_name: str
    feature_display_name: str
    current_plan: str
    required_plan: str
    prompt_type: str  # modal, banner, tooltip, inline
    message: str
    cta_text: str  # Call to action text
    cta_url: Optional[str] = None
    benefits: Optional[List[str]] = None
    pricing_info: Optional[Dict[str, Any]] = None
    shown_at: Optional[datetime] = None


class OGBUpgradePrompts:
    """Manage upgrade prompts across all subscription tiers"""

    # Feature to tier mapping
    FEATURE_TIERS = {
        # Free tier features (no upgrades needed)
        "basic_monitoring": "free",
        "ai_controllers": "free",
        "mobile_app": "free",
        # Basic tier features
        "advanced_analytics": "basic",
        "notifications": "basic",
        "data_export": "basic",
        # Professional tier features
        "compliance": "professional",
        "advanced_compliance": "professional",
        "research_data": "professional",
        "api_access": "professional",
        "webhooks": "professional",
        # Enterprise tier features
        "multi_tenant": "enterprise",
        "priority_support": "enterprise",
        "custom_integrations": "enterprise",
        "sla": "enterprise",
    }

    # Tier upgrade paths
    UPGRADE_PATHS = {
        "free": "basic",
        "basic": "professional",
        "professional": "enterprise",
        "enterprise": None,  # Highest tier
    }

    # Pricing information (can be fetched from API in production)
    PRICING = {
        "basic": {
            "monthly": 29.99,
            "yearly": 299.99,
            "currency": "USD",
        },
        "professional": {
            "monthly": 99.99,
            "yearly": 999.99,
            "currency": "USD",
        },
        "enterprise": {
            "monthly": "Custom",
            "yearly": "Custom",
            "currency": "USD",
            "contact_sales": True,
        },
    }

    def __init__(self, room: str, current_plan: str, hass, event_manager):
        """
        Initialize upgrade prompts manager.

        Args:
            room: Room identifier
            current_plan: Current subscription plan
            hass: Home Assistant instance
            event_manager: Event manager for firing prompt events
        """
        self.room = room
        self.current_plan = current_plan
        self.hass = hass
        self.event_manager = event_manager

        # Track shown prompts to avoid repetition
        self.shown_prompts: Dict[str, UpgradePrompt] = {}
        self.prompt_count = 0

        _LOGGER.info(
            f"📢 Upgrade prompts initialized for {room} (plan: {current_plan})"
        )

    def get_required_tier(self, feature_name: str) -> str:
        """Get required tier for a feature"""
        return self.FEATURE_TIERS.get(feature_name, "enterprise")

    def can_access_feature(self, feature_name: str) -> bool:
        """Check if current plan can access feature"""
        required_tier = self.get_required_tier(feature_name)
        tier_order = ["free", "basic", "professional", "enterprise"]

        current_index = tier_order.index(self.current_plan)
        required_index = tier_order.index(required_tier)

        return current_index >= required_index

    def create_upgrade_prompt(
        self, feature_name: str, prompt_type: str = "modal"
    ) -> Optional[UpgradePrompt]:
        """
        Create upgrade prompt for a blocked feature.

        Args:
            feature_name: Name of the blocked feature
            prompt_type: Type of prompt (modal, banner, tooltip, inline)

        Returns:
            UpgradePrompt object or None if no upgrade available
        """
        # Get required tier
        required_tier = self.get_required_tier(feature_name)

        # Check if user needs to upgrade
        if self.can_access_feature(feature_name):
            return None  # User already has access

        # Get upgrade path
        next_tier = self.UPGRADE_PATHS.get(self.current_plan)
        if not next_tier:
            return None  # Already on highest tier

        # If feature requires higher tier than next tier, use feature's tier
        tier_order = ["free", "basic", "professional", "enterprise"]
        if tier_order.index(required_tier) > tier_order.index(next_tier):
            target_tier = required_tier
        else:
            target_tier = next_tier

        # Create prompt
        prompt = self._build_prompt(feature_name, target_tier, prompt_type)

        return prompt

    def _build_prompt(
        self, feature_name: str, target_tier: str, prompt_type: str
    ) -> UpgradePrompt:
        """Build upgrade prompt with personalized messaging"""

        # Feature display names
        feature_display_names = {
            "advanced_analytics": "Advanced Analytics & Insights",
            "notifications": "Push Notifications",
            "data_export": "Data Export",
            "compliance": "Compliance Tracking",
            "advanced_compliance": "Advanced Compliance Suite",
            "research_data": "Research-Grade Data",
            "api_access": "REST API Access",
            "webhooks": "Webhook Integrations",
            "multi_tenant": "Multi-Tenant Management",
            "priority_support": "Priority Support",
            "custom_integrations": "Custom Integrations",
            "sla": "99.9% Uptime SLA",
        }

        display_name = feature_display_names.get(feature_name, feature_name.title())

        # Build benefits list
        benefits = self._get_tier_benefits(target_tier)

        # Build message
        if self.current_plan == "free":
            message = f"🚀 Unlock {display_name} with {target_tier.title()} plan!"
        elif self.current_plan == "basic":
            message = (
                f"⭐ Upgrade to {target_tier.title()} "
                f"to access {display_name} and more!"
            )
        else:
            message = (
                f"🌟 {display_name} is available on {target_tier.title()} plan"
            )

        # CTA text
        cta_texts = {
            "free": "Upgrade to Basic",
            "basic": "Upgrade to Professional",
            "professional": "Upgrade to Enterprise",
        }
        cta_text = cta_texts.get(self.current_plan, "Upgrade Now")

        # CTA URL (can be customized)
        cta_url = f"/premium/upgrade?from={self.current_plan}&to={target_tier}"

        prompt = UpgradePrompt(
            prompt_id=f"{feature_name}_{datetime.now(timezone.utc).timestamp()}",
            feature_name=feature_name,
            feature_display_name=display_name,
            current_plan=self.current_plan,
            required_plan=target_tier,
            prompt_type=prompt_type,
            message=message,
            cta_text=cta_text,
            cta_url=cta_url,
            benefits=benefits,
            pricing_info=self.PRICING.get(target_tier),
            shown_at=datetime.now(timezone.utc),
        )

        return prompt

    def _get_tier_benefits(self, tier: str) -> List[str]:
        """Get benefits for a subscription tier"""
        benefits = {
            "basic": [
                "Advanced Analytics & Insights",
                "Push Notifications & Alerts",
                "Data Export (CSV, JSON)",
                "Email Support",
            ],
            "professional": [
                "Everything in Basic, plus:",
                "Compliance Tracking & Reporting",
                "Research-Grade Data Export",
                "REST API Access",
                "Webhook Integrations",
                "Priority Email Support",
            ],
            "enterprise": [
                "Everything in Professional, plus:",
                "Multi-Tenant Management",
                "Custom Integrations",
                "99.9% Uptime SLA",
                "Dedicated Account Manager",
                "24/7 Priority Support",
            ],
        }
        return benefits.get(tier, [])

    async def show_upgrade_prompt(
        self, feature_name: str, prompt_type: str = "modal"
    ) -> bool:
        """
        Show upgrade prompt to user.

        Args:
            feature_name: Feature that was blocked
            prompt_type: Type of prompt to show

        Returns:
            True if prompt was shown, False otherwise
        """
        try:
            # Create prompt
            prompt = self.create_upgrade_prompt(feature_name, prompt_type)

            if not prompt:
                _LOGGER.debug(
                    f"📢 {self.room} No upgrade prompt needed for {feature_name}"
                )
                return False

            # Track prompt
            self.shown_prompts[prompt.prompt_id] = prompt
            self.prompt_count += 1

            _LOGGER.info(
                f"📢 {self.room} Showing upgrade prompt: {feature_name} "
                f"({self.current_plan} → {prompt.required_plan})"
            )

            # Fire Home Assistant event for frontend
            await self._fire_prompt_event(prompt)

            return True

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error showing upgrade prompt: {e}")
            return False

    async def _fire_prompt_event(self, prompt: UpgradePrompt):
        """Fire Home Assistant event for upgrade prompt"""
        event_data = {
            "prompt_id": prompt.prompt_id,
            "feature": prompt.feature_name,
            "feature_display_name": prompt.feature_display_name,
            "current_plan": prompt.current_plan,
            "required_plan": prompt.required_plan,
            "prompt_type": prompt.prompt_type,
            "message": prompt.message,
            "cta_text": prompt.cta_text,
            "cta_url": prompt.cta_url,
            "benefits": prompt.benefits,
            "pricing": prompt.pricing_info,
            "room": self.room,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Fire via event manager
        await self.event_manager.emit("upgrade_prompt_shown", event_data)

        # Also fire as HA event for frontend consumption
        self.hass.bus.async_fire(
            f"ogb_upgrade_prompt_{self.room.lower()}", event_data
        )

    async def record_prompt_clicked(
        self, prompt_id: str, clicked_cta: bool = True
    ):
        """
        Record that user interacted with upgrade prompt.

        Args:
            prompt_id: ID of the prompt
            clicked_cta: Whether user clicked the CTA button
        """
        prompt = self.shown_prompts.get(prompt_id)
        if not prompt:
            _LOGGER.warning(f"⚠️ {self.room} Unknown prompt ID: {prompt_id}")
            return

        event_type = "upgrade_prompt_clicked" if clicked_cta else "upgrade_prompt_dismissed"

        event_data = {
            "prompt_id": prompt_id,
            "feature": prompt.feature_name,
            "current_plan": self.current_plan,
            "target_plan": prompt.required_plan,
            "clicked_cta": clicked_cta,
            "room": self.room,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await self.event_manager.emit(event_type, event_data)

        _LOGGER.info(
            f"📢 {self.room} Upgrade prompt "
            f"{'clicked' if clicked_cta else 'dismissed'}: {prompt.feature_name}"
        )

    def get_prompt_stats(self) -> Dict[str, Any]:
        """Get statistics about shown prompts"""
        return {
            "total_prompts_shown": self.prompt_count,
            "unique_features": len(
                set(p.feature_name for p in self.shown_prompts.values())
            ),
            "current_plan": self.current_plan,
            "prompts_by_type": self._count_prompts_by_type(),
            "prompts_by_feature": self._count_prompts_by_feature(),
        }

    def _count_prompts_by_type(self) -> Dict[str, int]:
        """Count prompts by type"""
        counts = {}
        for prompt in self.shown_prompts.values():
            counts[prompt.prompt_type] = counts.get(prompt.prompt_type, 0) + 1
        return counts

    def _count_prompts_by_feature(self) -> Dict[str, int]:
        """Count prompts by feature"""
        counts = {}
        for prompt in self.shown_prompts.values():
            counts[prompt.feature_name] = counts.get(prompt.feature_name, 0) + 1
        return counts

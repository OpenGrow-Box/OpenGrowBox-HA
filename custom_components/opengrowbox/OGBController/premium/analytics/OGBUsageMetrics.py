"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                       Usage Metrics & Analytics Tracker                      ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file tracks usage metrics for ALL subscription tiers.

OGBUsageMetrics - Universal Usage Tracking & Analytics

Tracks for ALL plans (free, basic, professional, enterprise):
- Feature access attempts (successful and denied)
- API call counts and rate limiting per tier
- Session duration and activity patterns
- Conversion events (upgrade prompts shown, clicked)
- User engagement metrics
- Plan-specific usage patterns
- Churn prediction indicators
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from collections import defaultdict
import asyncio

_LOGGER = logging.getLogger(__name__)


class OGBUsageMetrics:
    """Track usage metrics for ALL subscription tiers and analytics"""

    def __init__(self, user_id: str, plan_name: str, room: str, tenant_id: Optional[str] = None):
        """
        Initialize usage metrics tracker for all subscription tiers.

        Args:
            user_id: User ID
            plan_name: Subscription plan name (free, basic, professional, enterprise)
            room: Room identifier
            tenant_id: Tenant ID for enterprise analytics
        """
        self.user_id = user_id
        self.plan_name = plan_name
        self.room = room
        self.tenant_id = tenant_id

        # Session tracking
        self.session_start = datetime.now(timezone.utc)
        self.last_activity = datetime.now(timezone.utc)
        self.total_sessions = 0
        self.session_id = f"{user_id[:8]}_{datetime.now(timezone.utc).timestamp()}"

        # Feature access tracking
        self.feature_access_attempts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"allowed": 0, "denied": 0}
        )
        self.last_feature_check = datetime.now(timezone.utc)

        # API rate limiting (for free tier)
        self.api_calls_today = 0
        self.api_calls_reset_time = self._get_next_reset_time()
        self.api_calls_history: List[datetime] = []

        # Conversion tracking
        self.upgrade_prompts_shown = 0
        self.upgrade_prompts_clicked = 0
        self.features_blocked_count = 0
        self.first_blocked_feature: Optional[str] = None
        self.first_blocked_at: Optional[datetime] = None

        # Engagement metrics
        self.actions_performed = 0
        self.pages_viewed = 0
        self.errors_encountered = 0

        _LOGGER.info(
            f"📊 Usage metrics initialized for {self.room} "
            f"(user: {user_id[:8]}, plan: {plan_name})"
        )

    def _get_next_reset_time(self) -> datetime:
        """Get next daily reset time (midnight UTC)"""
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)

    def _reset_daily_limits(self):
        """Reset daily API call counters if needed"""
        now = datetime.now(timezone.utc)
        if now >= self.api_calls_reset_time:
            _LOGGER.debug(
                f"📊 {self.room} Resetting daily API limits "
                f"(previous: {self.api_calls_today} calls)"
            )
            self.api_calls_today = 0
            self.api_calls_reset_time = self._get_next_reset_time()
            # Keep only last 7 days of history
            cutoff = now - timedelta(days=7)
            self.api_calls_history = [
                call_time for call_time in self.api_calls_history if call_time > cutoff
            ]

    def record_feature_access(
        self, feature_name: str, allowed: bool, reason: Optional[str] = None
    ):
        """
        Record feature access attempt.

        Args:
            feature_name: Name of the feature
            allowed: Whether access was granted
            reason: Reason for denial (if applicable)
        """
        self.last_activity = datetime.now(timezone.utc)
        self.last_feature_check = datetime.now(timezone.utc)

        if allowed:
            self.feature_access_attempts[feature_name]["allowed"] += 1
            _LOGGER.debug(f"📊 {self.room} Feature allowed: {feature_name}")
        else:
            self.feature_access_attempts[feature_name]["denied"] += 1
            self.features_blocked_count += 1

            # Track first blocked feature
            if not self.first_blocked_feature:
                self.first_blocked_feature = feature_name
                self.first_blocked_at = datetime.now(timezone.utc)

            _LOGGER.info(
                f"📊 {self.room} Feature denied: {feature_name} "
                f"(reason: {reason or 'subscription tier'})"
            )

    def record_api_call(self) -> bool:
        """
        Record API call and check rate limit.

        Returns:
            True if call is allowed, False if rate limit exceeded
        """
        self._reset_daily_limits()
        self.last_activity = datetime.now(timezone.utc)

        # Get daily limit based on plan
        daily_limits = {
            "free": 100,
            "basic": 1000,
            "professional": 10000,
            "enterprise": 100000,
        }
        limit = daily_limits.get(self.plan_name, 100)

        if self.api_calls_today >= limit:
            _LOGGER.warning(
                f"⚠️ {self.room} API rate limit exceeded "
                f"({self.api_calls_today}/{limit} calls today)"
            )
            return False

        self.api_calls_today += 1
        self.api_calls_history.append(datetime.now(timezone.utc))

        # Log warning at 80% usage
        if self.api_calls_today == int(limit * 0.8):
            _LOGGER.warning(
                f"⚠️ {self.room} Approaching API rate limit "
                f"({self.api_calls_today}/{limit} calls - 80% used)"
            )

        return True

    def record_upgrade_prompt_shown(self, feature_name: str, prompt_type: str):
        """
        Record that an upgrade prompt was shown.

        Args:
            feature_name: Feature that triggered the prompt
            prompt_type: Type of prompt (modal, banner, tooltip)
        """
        self.upgrade_prompts_shown += 1
        self.last_activity = datetime.now(timezone.utc)

        _LOGGER.info(
            f"📊 {self.room} Upgrade prompt shown: {feature_name} "
            f"(type: {prompt_type}, total: {self.upgrade_prompts_shown})"
        )

    def record_upgrade_prompt_clicked(self, feature_name: str):
        """
        Record that user clicked on upgrade prompt.

        Args:
            feature_name: Feature that triggered the click
        """
        self.upgrade_prompts_clicked += 1
        self.last_activity = datetime.now(timezone.utc)

        conversion_rate = (
            (self.upgrade_prompts_clicked / self.upgrade_prompts_shown * 100)
            if self.upgrade_prompts_shown > 0
            else 0
        )

        _LOGGER.info(
            f"📊 {self.room} Upgrade prompt clicked: {feature_name} "
            f"(clicks: {self.upgrade_prompts_clicked}, "
            f"conversion rate: {conversion_rate:.1f}%)"
        )

    def record_action(self, action_type: str):
        """
        Record user action (button click, setting change, etc).

        Args:
            action_type: Type of action performed
        """
        self.actions_performed += 1
        self.last_activity = datetime.now(timezone.utc)

        _LOGGER.debug(f"📊 {self.room} Action performed: {action_type}")

    def record_page_view(self, page_name: str):
        """
        Record page view.

        Args:
            page_name: Name of the page viewed
        """
        self.pages_viewed += 1
        self.last_activity = datetime.now(timezone.utc)

        _LOGGER.debug(f"📊 {self.room} Page viewed: {page_name}")

    def record_error(self, error_type: str, error_message: str):
        """
        Record error encountered.

        Args:
            error_type: Type of error
            error_message: Error message
        """
        self.errors_encountered += 1
        self.last_activity = datetime.now(timezone.utc)

        _LOGGER.warning(
            f"📊 {self.room} Error encountered: {error_type} - {error_message}"
        )

    def get_session_duration(self) -> timedelta:
        """Get current session duration"""
        return datetime.now(timezone.utc) - self.session_start

    def get_idle_time(self) -> timedelta:
        """Get time since last activity"""
        return datetime.now(timezone.utc) - self.last_activity

    def get_metrics_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive metrics summary.

        Returns:
            Dictionary with all tracked metrics
        """
        session_duration = self.get_session_duration()
        idle_time = self.get_idle_time()

        # Calculate conversion metrics
        prompt_conversion_rate = (
            (self.upgrade_prompts_clicked / self.upgrade_prompts_shown * 100)
            if self.upgrade_prompts_shown > 0
            else 0
        )

        # Calculate most denied features
        denied_features = {
            feature: counts["denied"]
            for feature, counts in self.feature_access_attempts.items()
            if counts["denied"] > 0
        }
        most_denied = sorted(denied_features.items(), key=lambda x: x[1], reverse=True)[
            :5
        ]

        # API usage percentage
        daily_limits = {
            "free": 100,
            "basic": 1000,
            "professional": 10000,
            "enterprise": 100000,
        }
        limit = daily_limits.get(self.plan_name, 100)
        api_usage_percent = (self.api_calls_today / limit * 100) if limit > 0 else 0

        return {
            "user_id": self.user_id,
            "plan": self.plan_name,
            "room": self.room,
            "session": {
                "start_time": self.session_start.isoformat(),
                "duration_seconds": int(session_duration.total_seconds()),
                "idle_seconds": int(idle_time.total_seconds()),
                "total_sessions": self.total_sessions,
            },
            "feature_access": {
                "total_attempts": sum(
                    counts["allowed"] + counts["denied"]
                    for counts in self.feature_access_attempts.values()
                ),
                "allowed": sum(
                    counts["allowed"]
                    for counts in self.feature_access_attempts.values()
                ),
                "denied": sum(
                    counts["denied"]
                    for counts in self.feature_access_attempts.values()
                ),
                "features_blocked": self.features_blocked_count,
                "first_blocked_feature": self.first_blocked_feature,
                "first_blocked_at": (
                    self.first_blocked_at.isoformat()
                    if self.first_blocked_at
                    else None
                ),
                "most_denied_features": dict(most_denied),
            },
            "api_usage": {
                "calls_today": self.api_calls_today,
                "daily_limit": limit,
                "usage_percent": round(api_usage_percent, 2),
                "reset_time": self.api_calls_reset_time.isoformat(),
                "calls_last_7_days": len(self.api_calls_history),
            },
            "conversion": {
                "upgrade_prompts_shown": self.upgrade_prompts_shown,
                "upgrade_prompts_clicked": self.upgrade_prompts_clicked,
                "conversion_rate_percent": round(prompt_conversion_rate, 2),
                "features_blocked_total": self.features_blocked_count,
            },
            "engagement": {
                "actions_performed": self.actions_performed,
                "pages_viewed": self.pages_viewed,
                "errors_encountered": self.errors_encountered,
                "last_activity": self.last_activity.isoformat(),
            },
        }

    def should_show_upgrade_prompt(self) -> bool:
        """
        Determine if upgrade prompt should be shown.

        Uses smart logic to avoid spamming users. Works for all tiers.

        Returns:
            True if prompt should be shown
        """
        # Don't show if already on highest tier
        if self.plan_name == "enterprise":
            return False

        # For free tier: show after first blocked feature
        if self.plan_name == "free":
            if (
                self.features_blocked_count >= 1
                and self.upgrade_prompts_shown == 0
            ):
                return True

            # Show after every 3 blocked features (but not too often)
            if (
                self.features_blocked_count > 0
                and self.features_blocked_count % 3 == 0
                and self.upgrade_prompts_shown < 10  # Max 10 prompts per session
            ):
                return True

        # For basic tier: show when attempting professional features
        if self.plan_name == "basic":
            if (
                self.features_blocked_count >= 2
                and self.upgrade_prompts_shown < 5  # Less aggressive
            ):
                return True

        # For professional tier: show when attempting enterprise features
        if self.plan_name == "professional":
            if (
                self.features_blocked_count >= 3
                and self.upgrade_prompts_shown < 3  # Even less aggressive
            ):
                return True

        # Show if approaching API limit (all tiers)
        daily_limits = {
            "free": 100,
            "basic": 1000,
            "professional": 10000,
            "enterprise": 100000,
        }
        limit = daily_limits.get(self.plan_name, 100)
        if self.api_calls_today >= int(limit * 0.9):  # 90% usage
            return True

        return False

    async def submit_to_api(self, api_proxy):
        """
        Submit usage metrics to Premium API for analytics.

        Args:
            api_proxy: OGBApiProxy instance for API calls
        """
        try:
            metrics = self.get_metrics_summary()

            result = await api_proxy.submit_usage_metrics(metrics)

            if result.get("success"):
                _LOGGER.debug(
                    f"📊 {self.room} Usage metrics submitted to API successfully"
                )
            else:
                _LOGGER.warning(
                    f"⚠️ {self.room} Failed to submit usage metrics: "
                    f"{result.get('error', 'Unknown error')}"
                )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Error submitting usage metrics: {e}")

    def __str__(self) -> str:
        """String representation of metrics"""
        summary = self.get_metrics_summary()
        return (
            f"UsageMetrics({self.room}, plan={self.plan_name}, "
            f"session={summary['session']['duration_seconds']}s, "
            f"api_calls={self.api_calls_today}, "
            f"blocked={self.features_blocked_count}, "
            f"prompts={self.upgrade_prompts_shown}/{self.upgrade_prompts_clicked})"
        )

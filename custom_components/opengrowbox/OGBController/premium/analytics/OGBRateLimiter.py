"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         🔒 PREMIUM FEATURE FILE 🔒                          ║
║                          Rate Limiting Service                               ║
╚══════════════════════════════════════════════════════════════════════════════╝

⚠️  IMPORTANT: This file manages rate limiting for all subscription tiers.

OGBRateLimiter - Intelligent Rate Limiting

Features:
- Per-tier rate limiting (free, basic, professional, enterprise)
- Multiple limit types (daily, hourly, per-minute)
- Graceful degradation with soft/hard limits
- Automatic limit reset
- Usage warnings before hitting limits
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from collections import deque
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass
class RateLimit:
    """Rate limit configuration"""

    limit_type: str  # daily, hourly, minute
    max_requests: int
    window_seconds: int
    soft_limit_percent: int = 80  # Warn at 80% usage
    hard_limit_percent: int = 100  # Block at 100% usage


class OGBRateLimiter:
    """Rate limiting for all subscription tiers"""

    # Rate limit configurations by tier and operation type
    RATE_LIMITS = {
        "free": {
            "api_calls": RateLimit("daily", 100, 86400, 80, 100),
            "sensor_reads": RateLimit("hourly", 60, 3600, 90, 100),
            "device_commands": RateLimit("hourly", 20, 3600, 80, 100),
        },
        "basic": {
            "api_calls": RateLimit("daily", 1000, 86400, 80, 100),
            "sensor_reads": RateLimit("hourly", 600, 3600, 90, 100),
            "device_commands": RateLimit("hourly", 200, 3600, 80, 100),
        },
        "professional": {
            "api_calls": RateLimit("daily", 10000, 86400, 90, 100),
            "sensor_reads": RateLimit("hourly", 6000, 3600, 95, 100),
            "device_commands": RateLimit("hourly", 2000, 3600, 90, 100),
        },
        "enterprise": {
            "api_calls": RateLimit("daily", 100000, 86400, 95, 100),
            "sensor_reads": RateLimit("hourly", 60000, 3600, 95, 100),
            "device_commands": RateLimit("hourly", 20000, 3600, 95, 100),
        },
    }

    def __init__(self, user_id: str, plan_name: str, room: str):
        """
        Initialize rate limiter.

        Args:
            user_id: User ID
            plan_name: Subscription plan
            room: Room identifier
        """
        self.user_id = user_id
        self.plan_name = plan_name
        self.room = room

        # Request tracking (using deque for efficient FIFO)
        self.request_history: Dict[str, deque] = {}
        
        # Warning tracking (to avoid spam)
        self.warnings_sent: Dict[str, datetime] = {}
        self.warning_cooldown = timedelta(minutes=15)  # Min time between warnings

        # Get limits for this tier
        self.limits = self.RATE_LIMITS.get(plan_name, self.RATE_LIMITS["free"])

        _LOGGER.info(
            f"⏱️ Rate limiter initialized for {room} "
            f"(user: {user_id[:8]}, plan: {plan_name})"
        )

    def check_limit(
        self, operation_type: str, increment: bool = True
    ) -> Dict[str, Any]:
        """
        Check if operation is within rate limit.

        Args:
            operation_type: Type of operation (api_calls, sensor_reads, device_commands)
            increment: Whether to increment counter if allowed

        Returns:
            Dictionary with:
                - allowed: bool (if operation is allowed)
                - remaining: int (requests remaining)
                - limit: int (total limit)
                - reset_at: datetime (when limit resets)
                - warning: bool (if approaching limit)
                - usage_percent: float (current usage percentage)
        """
        # Get rate limit config
        limit_config = self.limits.get(operation_type)
        if not limit_config:
            _LOGGER.warning(
                f"⚠️ {self.room} Unknown operation type: {operation_type}"
            )
            return {
                "allowed": True,
                "remaining": 999999,
                "limit": 999999,
                "reset_at": None,
                "warning": False,
                "usage_percent": 0.0,
            }

        # Initialize history for this operation if needed
        if operation_type not in self.request_history:
            self.request_history[operation_type] = deque()

        # Clean old requests outside window
        self._clean_old_requests(operation_type, limit_config.window_seconds)

        # Get current count
        current_count = len(self.request_history[operation_type])

        # Calculate usage
        usage_percent = (current_count / limit_config.max_requests) * 100
        remaining = max(0, limit_config.max_requests - current_count)

        # Check hard limit
        hard_limit_threshold = int(
            limit_config.max_requests * (limit_config.hard_limit_percent / 100)
        )
        allowed = current_count < hard_limit_threshold

        # Check soft limit (warning)
        soft_limit_threshold = int(
            limit_config.max_requests * (limit_config.soft_limit_percent / 100)
        )
        warning = current_count >= soft_limit_threshold

        # Calculate reset time
        reset_at = self._get_reset_time(limit_config.window_seconds)

        # Increment if allowed and requested
        if allowed and increment:
            self.request_history[operation_type].append(datetime.now(timezone.utc))

        # Send warning if needed
        if warning and not allowed:
            self._send_limit_warning(operation_type, usage_percent, reset_at)

        # Log if blocked
        if not allowed:
            _LOGGER.warning(
                f"⚠️ {self.room} Rate limit exceeded for {operation_type}: "
                f"{current_count}/{limit_config.max_requests} "
                f"(resets at {reset_at.isoformat()})"
            )

        return {
            "allowed": allowed,
            "remaining": remaining,
            "limit": limit_config.max_requests,
            "reset_at": reset_at,
            "warning": warning,
            "usage_percent": round(usage_percent, 2),
            "window_type": limit_config.limit_type,
        }

    def _clean_old_requests(self, operation_type: str, window_seconds: int):
        """Remove requests older than the window"""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=window_seconds)

        history = self.request_history[operation_type]

        # Remove old requests from the left side
        while history and history[0] < cutoff:
            history.popleft()

    def _get_reset_time(self, window_seconds: int) -> datetime:
        """Calculate when the rate limit will reset"""
        now = datetime.now(timezone.utc)
        return now + timedelta(seconds=window_seconds)

    def _send_limit_warning(
        self, operation_type: str, usage_percent: float, reset_at: datetime
    ):
        """Send warning about approaching limit"""
        # Check cooldown
        last_warning = self.warnings_sent.get(operation_type)
        if last_warning:
            time_since_warning = datetime.now(timezone.utc) - last_warning
            if time_since_warning < self.warning_cooldown:
                return  # Don't spam warnings

        _LOGGER.warning(
            f"⚠️ {self.room} Approaching rate limit for {operation_type}: "
            f"{usage_percent:.1f}% used (resets at {reset_at.isoformat()})"
        )

        self.warnings_sent[operation_type] = datetime.now(timezone.utc)

    def get_all_limits_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all rate limits"""
        status = {}

        for operation_type in self.limits.keys():
            status[operation_type] = self.check_limit(operation_type, increment=False)

        return status

    def reset_limit(self, operation_type: str):
        """Manually reset a rate limit (admin use)"""
        if operation_type in self.request_history:
            self.request_history[operation_type].clear()
            _LOGGER.info(f"✅ {self.room} Reset rate limit for {operation_type}")

    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive rate limit summary"""
        limits_status = self.get_all_limits_status()

        # Calculate overall usage
        total_allowed = sum(
            status["limit"] for status in limits_status.values()
        )
        total_used = sum(
            status["limit"] - status["remaining"]
            for status in limits_status.values()
        )
        overall_usage = (
            (total_used / total_allowed * 100) if total_allowed > 0 else 0
        )

        return {
            "user_id": self.user_id,
            "plan": self.plan_name,
            "room": self.room,
            "overall_usage_percent": round(overall_usage, 2),
            "limits": limits_status,
            "warnings_sent": len(self.warnings_sent),
        }

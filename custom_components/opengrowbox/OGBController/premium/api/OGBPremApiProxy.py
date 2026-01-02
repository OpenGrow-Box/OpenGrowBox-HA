"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        🌐 API PROXY MODULE 🌐                                ║
║               Proxy Service for ogb-grow-api v1 Endpoints                    ║
╚══════════════════════════════════════════════════════════════════════════════╝

This module provides a proxy service that allows the frontend (ogb-ha-gui) to
access ogb-grow-api v1 endpoints through Home Assistant events.

The frontend can send events to Home Assistant, which are then forwarded to
the ogb-grow-api and the response is sent back as an event.

Supported API Categories:
- Analytics: Environmental insights, yield predictions, anomalies
- Compliance: Compliance checks, audit trails
- Research: Dataset management, data export
- Subscriptions: Tier info, usage limits
- Premium: Feature status, limits

Events Handled:
- ogb_api_request: Generic API request proxy
- ogb_api_analytics: Analytics-specific requests
- ogb_api_compliance: Compliance-specific requests
- ogb_api_research: Research-specific requests
- ogb_api_subscription: Subscription-specific requests

Events Emitted:
- ogb_api_response: Response from API request
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import aiohttp

from ..const import PREM_WS_API

_LOGGER = logging.getLogger(__name__)


class OGBApiProxy:
    """
    Proxy service for ogb-grow-api v1 endpoints.

    Allows frontend to make API calls through Home Assistant events,
    providing a secure and consistent interface for premium features.
    """

    # API base URL (derived from WebSocket URL)
    API_BASE_URL = (
        PREM_WS_API.replace("wss://", "https://")
        .replace("ws://", "http://")
        .rstrip("/ws")
        .rstrip("/")
    )

    # Rate limiting
    MAX_REQUESTS_PER_MINUTE = 60
    REQUEST_TIMEOUT = 30  # seconds

    # Supported endpoint categories
    ALLOWED_ENDPOINTS = {
        "analytics": [
            "/api/v1/analytics/environmental/{planId}",
            "/api/v1/analytics/yield/{planId}",
            "/api/v1/analytics/anomalies/{planId}",
            "/api/v1/analytics/performance",
            "/api/v1/analytics/stats",
            "/api/v1/analytics/health",
        ],
        "compliance": [
            "/api/v1/compliance/rules",
            "/api/v1/compliance/status/{planId}",
            "/api/v1/compliance/audit",
            "/api/v1/compliance/report",
            "/api/v1/compliance/health",
        ],
        "research": [
            "/api/v1/research/datasets",
            "/api/v1/research/datasets/{id}",
            "/api/v1/research/query",
            "/api/v1/research/export",
            "/api/v1/research/health",
        ],
        "subscription": [
            "/api/v1/subscriptions/tiers",
            "/api/v1/subscriptions/current",
            "/api/v1/subscriptions/usage",
            "/api/v1/subscriptions/health",
        ],
        "premium": [
            "/api/v1/premium/features",
            "/api/v1/premium/status",
            "/api/v1/premium/limits",
        ],
        "config": [
            "/api/v1/config/stages",
            "/api/v1/config/stages/{stage}",
            "/api/v1/config/pid",
        ],
    }

    def __init__(self, hass, event_manager, room: str, access_token: str = None):
        """
        Initialize the API proxy.

        Args:
            hass: Home Assistant instance
            event_manager: OGB Event Manager
            room: Room name
            access_token: JWT access token for API authentication
        """
        self.hass = hass
        self.event_manager = event_manager
        self.room = room
        self._access_token = access_token
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_count = 0
        self._request_reset_time = datetime.now()
        self._event_unsubscribers = []

        self._setup_event_listeners()
        _LOGGER.info(f"✅ {room} OGBApiProxy initialized (API: {self.API_BASE_URL})")

    def set_access_token(self, token: str):
        """Update the access token."""
        self._access_token = token
        _LOGGER.debug(f"{self.room} API proxy access token updated")

    def _setup_event_listeners(self):
        """Setup Home Assistant event listeners."""
        self._event_unsubscribers.append(
            self.hass.bus.async_listen("ogb_api_request", self._handle_api_request)
        )
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_api_analytics", self._handle_analytics_request
            )
        )
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_api_compliance", self._handle_compliance_request
            )
        )
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_api_research", self._handle_research_request
            )
        )
        self._event_unsubscribers.append(
            self.hass.bus.async_listen(
                "ogb_api_subscription", self._handle_subscription_request
            )
        )

    async def shutdown(self):
        """Cleanup resources."""
        for unsubscribe in self._event_unsubscribers:
            if callable(unsubscribe):
                unsubscribe()
        self._event_unsubscribers.clear()

        if self._session:
            await self._session.close()
            self._session = None

        _LOGGER.info(f"{self.room} OGBApiProxy shutdown")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _check_rate_limit(self) -> bool:
        """Check if request is within rate limits."""
        now = datetime.now()

        # Reset counter every minute
        if (now - self._request_reset_time).total_seconds() >= 60:
            self._request_count = 0
            self._request_reset_time = now

        if self._request_count >= self.MAX_REQUESTS_PER_MINUTE:
            _LOGGER.warning(
                f"{self.room} Rate limit exceeded ({self.MAX_REQUESTS_PER_MINUTE}/min)"
            )
            return False

        self._request_count += 1
        return True

    def _validate_endpoint(self, endpoint: str, category: str = None) -> bool:
        """
        Validate that the endpoint is allowed.

        Args:
            endpoint: API endpoint path
            category: Optional category to restrict to

        Returns:
            True if endpoint is allowed
        """
        if category and category in self.ALLOWED_ENDPOINTS:
            patterns = self.ALLOWED_ENDPOINTS[category]
        else:
            # Check all categories
            patterns = []
            for cat_patterns in self.ALLOWED_ENDPOINTS.values():
                patterns.extend(cat_patterns)

        # Simple pattern matching (supports {param} placeholders)
        for pattern in patterns:
            if self._matches_pattern(endpoint, pattern):
                return True

        return False

    def _matches_pattern(self, endpoint: str, pattern: str) -> bool:
        """Check if endpoint matches a pattern with placeholders."""
        # Convert pattern to regex-like matching
        endpoint_parts = endpoint.strip("/").split("/")
        pattern_parts = pattern.strip("/").split("/")

        if len(endpoint_parts) != len(pattern_parts):
            return False

        for ep, pp in zip(endpoint_parts, pattern_parts):
            if pp.startswith("{") and pp.endswith("}"):
                continue  # Placeholder matches anything
            if ep != pp:
                return False

        return True

    async def _make_request(
        self, method: str, endpoint: str, data: Dict = None, params: Dict = None
    ) -> Dict[str, Any]:
        """
        Make an API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path
            data: Request body data
            params: Query parameters

        Returns:
            Response data or error dict
        """
        if not self._check_rate_limit():
            return {
                "success": False,
                "error": "rate_limited",
                "message": "Too many requests. Please wait.",
            }

        if not self._access_token:
            return {
                "success": False,
                "error": "not_authenticated",
                "message": "No access token available",
            }

        url = f"{self.API_BASE_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-OGB-Room": self.room,
        }

        try:
            session = await self._get_session()

            async with session.request(
                method=method.upper(),
                url=url,
                json=data,
                params=params,
                headers=headers,
            ) as response:

                response_data = await response.json()

                if response.status >= 400:
                    return {
                        "success": False,
                        "error": response_data.get("error", "api_error"),
                        "message": response_data.get(
                            "message", f"HTTP {response.status}"
                        ),
                        "status_code": response.status,
                    }

                return {
                    "success": True,
                    "data": response_data.get("data", response_data),
                    "timestamp": datetime.now().isoformat(),
                }

        except asyncio.TimeoutError:
            _LOGGER.error(f"{self.room} API request timeout: {endpoint}")
            return {
                "success": False,
                "error": "timeout",
                "message": "Request timed out",
            }
        except aiohttp.ClientError as e:
            _LOGGER.error(f"{self.room} API request error: {e}")
            return {"success": False, "error": "network_error", "message": str(e)}
        except Exception as e:
            _LOGGER.error(f"{self.room} API request exception: {e}")
            return {"success": False, "error": "internal_error", "message": str(e)}

    async def _send_response(self, event_id: str, status: str, data: Dict = None):
        """Send response event to frontend."""
        response_data = {
            "event_id": event_id,
            "status": status,
            "room": self.room,
            "timestamp": datetime.now().isoformat(),
        }

        if data:
            response_data.update(data)

        await self.event_manager.emit("ogb_api_response", response_data, haEvent=True)

    # =================================================================
    # Event Handlers
    # =================================================================

    async def _handle_api_request(self, event):
        """Handle generic API request."""
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return

            event_id = event.data.get("event_id")
            method = event.data.get("method", "GET")
            endpoint = event.data.get("endpoint")
            body = event.data.get("body")
            params = event.data.get("params")

            if not endpoint:
                await self._send_response(
                    event_id,
                    "error",
                    {"error": "missing_endpoint", "message": "endpoint is required"},
                )
                return

            # Validate endpoint
            if not self._validate_endpoint(endpoint):
                await self._send_response(
                    event_id,
                    "error",
                    {
                        "error": "invalid_endpoint",
                        "message": f"Endpoint not allowed: {endpoint}",
                    },
                )
                return

            result = await self._make_request(method, endpoint, body, params)
            await self._send_response(
                event_id, "success" if result["success"] else "error", result
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} API request handler error: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                {"error": "handler_error", "message": str(e)},
            )

    async def _handle_analytics_request(self, event):
        """Handle analytics API request."""
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return

            event_id = event.data.get("event_id")
            action = event.data.get("action")
            plan_id = event.data.get("plan_id")

            endpoint_map = {
                "environmental": f"/api/v1/analytics/environmental/{plan_id}",
                "yield": f"/api/v1/analytics/yield/{plan_id}",
                "anomalies": f"/api/v1/analytics/anomalies/{plan_id}",
                "performance": "/api/v1/analytics/performance",
                "stats": "/api/v1/analytics/stats",
                "health": "/api/v1/analytics/health",
            }

            endpoint = endpoint_map.get(action)
            if not endpoint:
                await self._send_response(
                    event_id,
                    "error",
                    {
                        "error": "invalid_action",
                        "message": f"Unknown analytics action: {action}",
                    },
                )
                return

            result = await self._make_request("GET", endpoint)
            await self._send_response(
                event_id, "success" if result["success"] else "error", result
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Analytics request handler error: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                {"error": "handler_error", "message": str(e)},
            )

    async def _handle_compliance_request(self, event):
        """Handle compliance API request."""
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return

            event_id = event.data.get("event_id")
            action = event.data.get("action")
            plan_id = event.data.get("plan_id")
            body = event.data.get("body")

            endpoint_map = {
                "rules": "/api/v1/compliance/rules",
                "status": f"/api/v1/compliance/status/{plan_id}",
                "audit": "/api/v1/compliance/audit",
                "report": "/api/v1/compliance/report",
                "health": "/api/v1/compliance/health",
            }

            endpoint = endpoint_map.get(action)
            if not endpoint:
                await self._send_response(
                    event_id,
                    "error",
                    {
                        "error": "invalid_action",
                        "message": f"Unknown compliance action: {action}",
                    },
                )
                return

            method = "POST" if action == "report" else "GET"
            result = await self._make_request(method, endpoint, body)
            await self._send_response(
                event_id, "success" if result["success"] else "error", result
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Compliance request handler error: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                {"error": "handler_error", "message": str(e)},
            )

    async def _handle_research_request(self, event):
        """Handle research API request."""
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return

            event_id = event.data.get("event_id")
            action = event.data.get("action")
            dataset_id = event.data.get("dataset_id")
            body = event.data.get("body")

            endpoint_map = {
                "list": "/api/v1/research/datasets",
                "get": f"/api/v1/research/datasets/{dataset_id}",
                "create": "/api/v1/research/datasets",
                "query": "/api/v1/research/query",
                "export": "/api/v1/research/export",
                "health": "/api/v1/research/health",
            }

            endpoint = endpoint_map.get(action)
            if not endpoint:
                await self._send_response(
                    event_id,
                    "error",
                    {
                        "error": "invalid_action",
                        "message": f"Unknown research action: {action}",
                    },
                )
                return

            method_map = {"create": "POST", "query": "POST", "export": "POST"}
            method = method_map.get(action, "GET")

            result = await self._make_request(method, endpoint, body)
            await self._send_response(
                event_id, "success" if result["success"] else "error", result
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Research request handler error: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                {"error": "handler_error", "message": str(e)},
            )

    async def _handle_subscription_request(self, event):
        """Handle subscription API request."""
        try:
            event_room = event.data.get("room", "")
            if event_room.lower() != self.room.lower():
                return

            event_id = event.data.get("event_id")
            action = event.data.get("action")

            endpoint_map = {
                "tiers": "/api/v1/subscriptions/tiers",
                "current": "/api/v1/subscriptions/current",
                "usage": "/api/v1/subscriptions/usage",
                "health": "/api/v1/subscriptions/health",
                "features": "/api/v1/premium/features",
                "status": "/api/v1/premium/status",
                "limits": "/api/v1/premium/limits",
            }

            endpoint = endpoint_map.get(action)
            if not endpoint:
                await self._send_response(
                    event_id,
                    "error",
                    {
                        "error": "invalid_action",
                        "message": f"Unknown subscription action: {action}",
                    },
                )
                return

            result = await self._make_request("GET", endpoint)
            await self._send_response(
                event_id, "success" if result["success"] else "error", result
            )

        except Exception as e:
            _LOGGER.error(f"❌ {self.room} Subscription request handler error: {e}")
            await self._send_response(
                event.data.get("event_id"),
                "error",
                {"error": "handler_error", "message": str(e)},
            )

    # =================================================================
    # Direct API Methods (for internal use)
    # =================================================================

    async def get_environmental_insights(self, plan_id: str) -> Dict[str, Any]:
        """Get environmental insights for a grow plan."""
        return await self._make_request(
            "GET", f"/api/v1/analytics/environmental/{plan_id}"
        )

    async def get_yield_prediction(self, plan_id: str) -> Dict[str, Any]:
        """Get yield prediction for a grow plan."""
        return await self._make_request("GET", f"/api/v1/analytics/yield/{plan_id}")

    async def detect_anomalies(self, plan_id: str) -> Dict[str, Any]:
        """Detect anomalies for a grow plan."""
        return await self._make_request("GET", f"/api/v1/analytics/anomalies/{plan_id}")

    async def get_compliance_status(self, plan_id: str) -> Dict[str, Any]:
        """Get compliance status for a grow plan."""
        return await self._make_request("GET", f"/api/v1/compliance/status/{plan_id}")

    async def get_subscription_tiers(self) -> Dict[str, Any]:
        """Get available subscription tiers."""
        return await self._make_request("GET", "/api/v1/subscriptions/tiers")

    async def get_current_subscription(self) -> Dict[str, Any]:
        """Get current subscription details."""
        return await self._make_request("GET", "/api/v1/subscriptions/current")

    async def get_premium_features(self) -> Dict[str, Any]:
        """Get available premium features."""
        return await self._make_request("GET", "/api/v1/premium/features")

    async def get_premium_limits(self) -> Dict[str, Any]:
        """Get premium usage limits."""
        return await self._make_request("GET", "/api/v1/premium/limits")

    # ========== Feature Flag Control API ==========

    async def get_feature_configs(self, category: str = None) -> Dict[str, Any]:
        """
        Get all feature configurations.

        Args:
            category: Optional category filter (analytics, controller, compliance, etc.)

        Returns:
            Dict with feature configurations
        """
        params = {"category": category} if category else None
        return await self._make_request(
            "GET", "/api/v1/admin/features/configs", params=params
        )

    async def get_feature_config(self, feature_key: str) -> Dict[str, Any]:
        """
        Get configuration for a specific feature.

        Args:
            feature_key: Feature identifier (e.g., 'ai_controllers')

        Returns:
            Dict with feature configuration
        """
        return await self._make_request(
            "GET", f"/api/v1/admin/features/{feature_key}/config"
        )

    async def get_tenant_overrides(self, tenant_id: str) -> Dict[str, Any]:
        """
        Get all feature overrides for a tenant.

        Args:
            tenant_id: Tenant UUID

        Returns:
            Dict with active and expired overrides
        """
        return await self._make_request(
            "GET", f"/api/v1/admin/tenants/{tenant_id}/overrides"
        )

    async def get_feature_overrides(self, feature_key: str) -> Dict[str, Any]:
        """
        Get all overrides for a specific feature.

        Args:
            feature_key: Feature identifier

        Returns:
            Dict with feature overrides
        """
        return await self._make_request(
            "GET", f"/api/v1/admin/features/{feature_key}/overrides"
        )

    async def check_feature_access(
        self, feature_key: str, tenant_id: str
    ) -> Dict[str, Any]:
        """
        Check if a tenant has access to a feature (server-side logic).

        This endpoint uses server-side logic to check:
        - Kill switch status
        - Tenant overrides
        - Rollout percentage
        - Subscription tier

        Args:
            feature_key: Feature identifier
            tenant_id: Tenant UUID

        Returns:
            Dict with access decision and reason
        """
        return await self._make_request(
            "GET", f"/api/v1/admin/features/{feature_key}/access/{tenant_id}"
        )

    async def get_feature_analytics(self, feature_key: str) -> Dict[str, Any]:
        """
        Get analytics for a specific feature.

        Args:
            feature_key: Feature identifier

        Returns:
            Dict with adoption rates, tenant counts, etc.
        """
        return await self._make_request(
            "GET", f"/api/v1/admin/features/{feature_key}/analytics"
        )

    async def get_features_summary(self) -> Dict[str, Any]:
        """
        Get summary of all features.

        Returns:
            Dict with total features, enabled/disabled counts, by category
        """
        return await self._make_request(
            "GET", "/api/v1/admin/features/analytics/summary"
        )

    def __repr__(self) -> str:
        """String representation for debugging."""
        has_token = "yes" if self._access_token else "no"
        return f"<OGBApiProxy room={self.room} token={has_token} requests={self._request_count}>"

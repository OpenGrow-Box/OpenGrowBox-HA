"""
OpenGrowBox Premium Module

This module contains all premium-related functionality including:
- WebSocket connectivity
- Feature management
- Analytics, Compliance, Research
- API proxy and caching
- Grow plan management

Directory Structure:
- websocket/: WebSocket client and related utilities
- features/: Feature flag management
- analytics/: Analytics, Compliance, Research modules
- api/: API proxy and caching
- growplans/: Grow plan management
- auth/: Authentication handlers

For backwards compatibility, classes can still be imported from their
original locations in OGBController/.
"""

# Import submodules for convenience (lazy import to avoid HA dependencies)
try:
    from . import analytics, api, auth, features, growplans, websocket
except ImportError:
    # Handle case where HA dependencies are not available
    websocket = None
    features = None
    analytics = None
    api = None
    growplans = None
    auth = None

__all__ = [
    "websocket",
    "features",
    "analytics",
    "api",
    "growplans",
    "auth",
]

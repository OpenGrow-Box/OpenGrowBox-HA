"""
OpenGrowBox Premium WebSocket Module

This module provides secure WebSocket connectivity for premium features.

Architecture:
------------
The WebSocket functionality is organized into modular components:

1. OGBPremWebSocketClient (OGBWebSocketConManager)
   - The main client class, located at utils/Premium/SecureWebSocketClient.py
   - Contains all WebSocket functionality in a single class
   - Use this for production

2. Mixin Classes (for reference, testing, and new implementations):
   - OGBPremWebSocketAuthMixin: Authentication logic (login, logout, dev login)
   - OGBPremWebSocketSessionMixin: Session management (rotation, restore, keep-alive)
   - OGBPremWebSocketReconnectMixin: Reconnection with exponential backoff

3. Utility Classes:
   - OGBPremWebSocketCrypto: Standalone AES-GCM encryption/decryption
   - OGBPremWebSocketEventHandlers: Factory for creating socket.io event handlers

Import Paths:
-------------
The main client (backwards compatible):
    from ...utils.Premium.SecureWebSocketClient import OGBWebSocketConManager

New recommended path:
    from .OGBController.premium.websocket import OGBPremWebSocketClient

Crypto utilities (standalone):
    from .OGBController.premium.websocket import OGBPremWebSocketCrypto

Event handler factory:
    from .OGBController.premium.websocket import get_all_event_handlers

Mixin classes (for new implementations or testing):
    from .OGBController.premium.websocket import (
        OGBPremWebSocketAuthMixin,
        OGBPremWebSocketSessionMixin,
        OGBPremWebSocketReconnectMixin,
    )

Notes:
------
- The mixin classes contain extracted logic from SecureWebSocketClient.py
- They are designed to be composable for new implementations
- The original SecureWebSocketClient.py remains the production client
- Type errors in mixins are expected (attributes come from parent class at runtime)
"""

# Import from original location for backwards compatibility
from ...utils.Premium.SecureWebSocketClient import OGBWebSocketConManager

# Alias for new naming convention
OGBPremWebSocketClient = OGBWebSocketConManager

# Import mixin classes (for use in new implementations)
from .OGBPremWebSocketAuth import OGBPremWebSocketAuthMixin
# Import crypto utilities
from .OGBPremWebSocketCrypto import OGBPremWebSocketCrypto
# Import event handler factory
from .OGBPremWebSocketEvents import (OGBPremWebSocketEventHandlers,
                                     get_all_event_handlers)
from .OGBPremWebSocketReconnect import OGBPremWebSocketReconnectMixin
from .OGBPremWebSocketSession import OGBPremWebSocketSessionMixin

__all__ = [
    # Main client class
    "OGBPremWebSocketClient",
    "OGBWebSocketConManager",  # Backwards compatibility
    # Crypto utilities
    "OGBPremWebSocketCrypto",
    # Event handlers
    "OGBPremWebSocketEventHandlers",
    "get_all_event_handlers",
    # Mixin classes
    "OGBPremWebSocketAuthMixin",
    "OGBPremWebSocketSessionMixin",
    "OGBPremWebSocketReconnectMixin",
]

"""
OpenGrowBox Premium WebSocket Client

Main WebSocket client for premium feature connectivity.
This file is a refactored version of SecureWebSocketClient.py.

For backwards compatibility, the old import path still works:
    from ...utils.Premium.SecureWebSocketClient import OGBWebSocketConManager
"""

# Re-export from the original location for now
# This allows gradual migration while maintaining backwards compatibility
from ...utils.Premium.SecureWebSocketClient import OGBWebSocketConManager

# Alias for new naming convention
OGBPremWebSocketClient = OGBWebSocketConManager

__all__ = ["OGBPremWebSocketClient", "OGBWebSocketConManager"]

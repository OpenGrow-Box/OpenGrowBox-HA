"""
OpenGrowBox Core Managers

Core manager classes for fundamental OpenGrowBox functionality.
These are the primary managers imported and used by the main OGB system.

Components:
- OGBMainController: Main system coordinator and manager orchestrator
- OGBConfigurationManager: Configuration management and settings
- OGBVPDManager: VPD calculation and management
"""

from .OGBMainController import OGBMainController
from .OGBConfigurationManager import OGBConfigurationManager
from .OGBVPDManager import OGBVPDManager

__all__ = [
    "OGBMainController",
    "OGBConfigurationManager",
    "OGBVPDManager",
]
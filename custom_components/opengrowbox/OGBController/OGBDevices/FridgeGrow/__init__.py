"""
FridgeGrow / Plantalytix Device Integration Module.

This module provides support for FridgeGrow 2.0 and Plantalytix devices
in OpenGrowBox via Home Assistant labels.

Usage:
    Devices are automatically recognized when they have both:
    1. A "fridgegrow" or "plantalytix" label
    2. An output type label (heater, light, dehumidifier, etc.)

Example:
    HA entity with labels ["fridgegrow", "heater"] will be recognized
    as a FridgeGrow heater and controlled appropriately.
"""

from .FridgeGrowDevice import FridgeGrowDevice

__all__ = ["FridgeGrowDevice"]

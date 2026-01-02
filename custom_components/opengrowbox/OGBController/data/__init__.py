"""
OpenGrowBox Data Definitions

Data structures, parameters, and type definitions for OpenGrowBox.

This package contains:
- OGBDataClasses: Data models and publication structures
- OGBParams: Configuration parameters and constants

Components:
- OGBDataClasses: Core data structures and publications
- OGBParams: System parameters, translations, and constants
"""

# Re-export for convenience
from .OGBDataClasses import *
from .OGBParams import *

__all__ = [
    "OGBDataClasses",
    "OGBParams",
]
"""Ambient room detection utilities.

Provides centralized functions to identify ambient rooms across the codebase,
eliminating magic string comparisons.
"""

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)


# Central definition of the ambient room identifier
AMBIENT_ROOM_NAME = "ambient"


def is_ambient_room(room_name: Optional[str]) -> bool:
    """Check if the given room name identifies an ambient room.

    Args:
        room_name: The room name to check.

    Returns:
        True if the room is the ambient room, False otherwise.
    """
    return room_name is not None and room_name.lower() == AMBIENT_ROOM_NAME


def is_not_ambient_room(room_name: Optional[str]) -> bool:
    """Check if the given room name is NOT an ambient room.

    Convenience wrapper for the common ``if self.room.lower() != "ambient"``
    pattern.

    Args:
        room_name: The room name to check.

    Returns:
        True if the room is NOT the ambient room, False if it is.
    """
    return not is_ambient_room(room_name)


# Keep legacy name for backward compatibility
async def do_nothing(room_name: Optional[str]) -> bool:
    """Legacy compatibility wrapper."""
    return is_ambient_room(room_name)

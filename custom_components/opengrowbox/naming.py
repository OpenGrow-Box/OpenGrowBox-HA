"""Naming helpers for OpenGrowBox entities."""

from __future__ import annotations

import re

from .const import DOMAIN

_ACRONYMS = {
    "ai",
    "co2",
    "dli",
    "ec",
    "gls",
    "led",
    "mpc",
    "ph",
    "pid",
    "ppfd",
    "uv",
    "vpd",
}


def legacy_object_id(raw_name: str) -> str:
    """Keep the historic OGB object_id format (critical for backend mapping)."""
    return str(raw_name or "").strip().lower().replace(" ", "_")


def legacy_entity_id(domain: str, raw_name: str) -> str:
    """Build entity_id with the legacy object_id format."""
    return f"{domain}.{legacy_object_id(raw_name)}"


def display_name_from_raw(raw_name: str, room_name: str | None) -> str:
    """Build a human-friendly display name from legacy OGB raw names."""
    name = str(raw_name or "").strip()
    if not name:
        return "OpenGrowBox"

    if name.upper().startswith("OGB_"):
        name = name[4:]

    room = (room_name or "").strip()
    if room:
        suffix = f"_{room}"
        if name.lower().endswith(suffix.lower()):
            name = name[: -len(suffix)]

    name = name.strip("_")
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    name = name.replace("_", " ")

    words = [word for word in name.split() if word]
    if not words:
        return "OpenGrowBox"

    normalized: list[str] = []
    for word in words:
        lower = word.lower()
        if lower in _ACRONYMS:
            normalized.append(lower.upper())
            continue

        if any(char.isdigit() for char in word) and any(char.isalpha() for char in word):
            normalized.append(word.upper())
            continue

        normalized.append(word.capitalize())

    return " ".join(normalized)


def room_device_info(room_name: str, model: str) -> dict:
    """Return stable room-scoped device info for all entities in the same room."""
    room = str(room_name or "Ambient").strip() or "Ambient"
    room_slug = room.lower().replace(" ", "_")
    return {
        "identifiers": {(DOMAIN, f"room_{room_slug}")},
        "name": f"OpenGrowBox {room}",
        "model": model,
        "manufacturer": "OpenGrowBox",
        "suggested_area": room,
    }

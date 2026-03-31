"""
Shared air-exchange cold guard logic.

This module prevents repeated cold-air exchange actions when ambient/outside
conditions are likely to worsen a too-cold room, while still allowing
life-safety overrides (critical O2/CO2 situations).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Tuple

AIR_EXCHANGE_CAPABILITIES = {"canExhaust", "canIntake", "canVentilate"}


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _contains_emergency_hint(message: str) -> bool:
    text = (message or "").lower()
    hints = (
        "emergency",
        "critical",
        "o2",
        "co2 safety",
        "safety",
    )
    return any(hint in text for hint in hints)


def _is_safety_override_active(data_store, message: str, priority: str) -> Tuple[bool, str]:
    if str(priority or "").lower() == "emergency":
        return True, "priority_emergency"

    if _contains_emergency_hint(message):
        return True, "message_emergency_hint"

    tent_data = data_store.get("tentData") or {}
    o2_level = _to_float(tent_data.get("o2Level"))
    if o2_level is not None and o2_level < 19.0:
        return True, f"critical_o2_{o2_level:.2f}"

    co2_level = _to_float(tent_data.get("co2Level"))
    max_ppm = _to_float(data_store.getDeep("controlOptionData.co2ppm.maxPPM"))
    co2_emergency_threshold = 2000.0
    if max_ppm is not None:
        co2_emergency_threshold = max(co2_emergency_threshold, max_ppm + 300.0)

    if co2_level is not None and co2_level >= co2_emergency_threshold:
        return True, f"critical_co2_{co2_level:.2f}"

    return False, ""


def evaluate_air_exchange_cold_guard(
    data_store,
    room: str,
    capability: str,
    action: str,
    *,
    message: str = "",
    priority: str = "",
    source: str = "unknown",
) -> Tuple[bool, Dict[str, Any]]:
    """
    Evaluate whether an air-exchange Increase action should be blocked.

    Returns:
        (should_block, metadata)
    """
    if capability not in AIR_EXCHANGE_CAPABILITIES or action != "Increase":
        return False, {"reason": "not_applicable", "source": source}

    now = time.time()
    tent_data = data_store.get("tentData") or {}

    state_path = "safety.airExchangeColdGuard"
    state = data_store.getDeep(state_path) or {}

    blocked_count = int(state.get("blockedCount", 0) or 0)
    window_start = _to_float(state.get("windowStart"))
    lock_until = _to_float(state.get("lockUntil"))

    # Tunables (defaults are intentionally conservative)
    ambient_delta = float(data_store.getDeep("controlOptions.airExchangeColdAmbientDelta") or 1.2)
    min_margin = float(data_store.getDeep("controlOptions.airExchangeColdMinMargin") or 0.8)
    humidity_delta = float(data_store.getDeep("controlOptions.airExchangeColdHumidityDelta") or 15.0)
    humidity_margin = float(data_store.getDeep("controlOptions.airExchangeColdHumidityMargin") or 5.0)
    window_minutes = float(data_store.getDeep("controlOptions.airExchangeColdWindowMinutes") or 30.0)
    lock_minutes = float(data_store.getDeep("controlOptions.airExchangeColdLockMinutes") or 60.0)
    unlock_margin = float(data_store.getDeep("controlOptions.airExchangeUnlockMargin") or 1.2)

    window_seconds = max(60.0, window_minutes * 60.0)
    lock_seconds = max(60.0, lock_minutes * 60.0)

    # Safety override can always bypass the cold guard
    safety_override, override_reason = _is_safety_override_active(data_store, message, priority)
    if safety_override:
        state.update(
            {
                "lastDecision": "allow_override",
                "lastReason": override_reason,
                "lastSource": source,
                "lastUpdate": now,
            }
        )
        data_store.setDeep(state_path, state)
        return False, {"reason": override_reason, "override": True, "source": source}

    indoor_temp = _to_float(tent_data.get("temperature"))
    min_temp = _to_float(tent_data.get("minTemp"))
    indoor_humidity = _to_float(tent_data.get("humidity"))
    min_humidity = _to_float(tent_data.get("minHumidity"))

    ambient_temp = _to_float(tent_data.get("AmbientTemp"))
    ambient_humidity = _to_float(tent_data.get("AmbientHum"))

    if ambient_temp is None:
        ambient_temp = _to_float(tent_data.get("OutsiteTemp"))
    if ambient_humidity is None:
        ambient_humidity = _to_float(tent_data.get("OutsiteHum"))

    has_ambient = ambient_temp is not None

    lock_active = lock_until is not None and now < lock_until
    if lock_active and has_ambient and indoor_temp is not None and min_temp is not None:
        can_unlock = (
            ambient_temp >= (indoor_temp - max(0.4, ambient_delta / 2.0))
            and indoor_temp > (min_temp + unlock_margin)
        )
        if can_unlock:
            lock_active = False
            lock_until = None
            blocked_count = 0
            window_start = None

    if lock_active:
        state.update(
            {
                "blockedCount": blocked_count,
                "windowStart": window_start,
                "lockUntil": lock_until,
                "lastDecision": "blocked_lock",
                "lastReason": "lock_active",
                "lastSource": source,
                "lastUpdate": now,
                "lastAmbientTemp": ambient_temp,
                "lastIndoorTemp": indoor_temp,
            }
        )
        data_store.setDeep(state_path, state)
        return True, {"reason": "lock_active", "lockUntil": lock_until, "source": source}

    near_temp_floor = (
        indoor_temp is not None and min_temp is not None and indoor_temp <= (min_temp + min_margin)
    )
    near_humidity_floor = (
        indoor_humidity is not None
        and min_humidity is not None
        and indoor_humidity <= (min_humidity + humidity_margin)
    )

    temp_risk = near_temp_floor and (
        (has_ambient and ambient_temp <= (indoor_temp - ambient_delta)) or not has_ambient
    )
    humidity_risk = (
        has_ambient
        and near_humidity_floor
        and ambient_humidity is not None
        and indoor_humidity is not None
        and ambient_humidity <= (indoor_humidity - humidity_delta)
    )

    if not temp_risk and not humidity_risk:
        if window_start is not None and now - window_start > window_seconds:
            blocked_count = 0
            window_start = None

        state.update(
            {
                "blockedCount": blocked_count,
                "windowStart": window_start,
                "lockUntil": lock_until,
                "lastDecision": "allow",
                "lastReason": "no_cold_risk",
                "lastSource": source,
                "lastUpdate": now,
                "lastAmbientTemp": ambient_temp,
                "lastIndoorTemp": indoor_temp,
            }
        )
        data_store.setDeep(state_path, state)
        return False, {"reason": "no_cold_risk", "source": source}

    if window_start is None or now - window_start > window_seconds:
        blocked_count = 0
        window_start = now

    blocked_count += 1
    if blocked_count >= 2:
        lock_until = now + lock_seconds

    reason = "temp_risk" if temp_risk else "humidity_risk"
    state.update(
        {
            "blockedCount": blocked_count,
            "windowStart": window_start,
            "lockUntil": lock_until,
            "lastDecision": "blocked",
            "lastReason": reason,
            "lastSource": source,
            "lastUpdate": now,
            "lastAmbientTemp": ambient_temp,
            "lastIndoorTemp": indoor_temp,
        }
    )
    data_store.setDeep(state_path, state)

    return True, {
        "reason": reason,
        "blockedCount": blocked_count,
        "lockUntil": lock_until,
        "ambientTemp": ambient_temp,
        "indoorTemp": indoor_temp,
        "source": source,
    }

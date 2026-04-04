"""
Intelligent Environment Guard.

This module consolidates and replaces the previous air-exchange cold guard logic.
It makes intelligent decisions about air exchange based on:
- Temperature: prevents bringing in cold air when too cold
- Humidity: prevents bringing in dry air when too dry
- Humidity emergencies: overrides temperature concerns when humidity is critical
- Intelligent source selection: chooses between ambient room and outside weather data

The guard considers plant stage settings (min/max temp & humidity from tentData)
to determine critical thresholds.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

AIR_EXCHANGE_CAPABILITIES = {"canExhaust", "canIntake", "canVentilate"}


def _to_float(value: Any) -> Optional[float]:
    """Convert value to float safely."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _contains_emergency_hint(message: str) -> bool:
    """Check if message indicates an emergency situation."""
    text = (message or "").lower()
    hints = ("emergency", "critical", "o2", "co2 safety", "safety")
    return any(hint in text for hint in hints)


def _is_safety_override_active(
    data_store, message: str, priority: str
) -> Tuple[bool, str]:
    """Check if emergency override should bypass all guards."""
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


def _get_config_values(data_store) -> Dict[str, float]:
    """Get environment guard configuration values."""
    return {
        "ambient_delta": float(
            data_store.getDeep("controlOptions.environmentGuardAmbientDelta") or 1.2
        ),
        "min_margin": float(
            data_store.getDeep("controlOptions.environmentGuardMinMargin") or 0.8
        ),
        "humidity_delta": float(
            data_store.getDeep("controlOptions.environmentGuardHumidityDelta") or 15.0
        ),
        "humidity_margin": float(
            data_store.getDeep("controlOptions.environmentGuardHumidityMargin") or 5.0
        ),
        "window_minutes": float(
            data_store.getDeep("controlOptions.environmentGuardWindowMinutes") or 30.0
        ),
        "lock_minutes": float(
            data_store.getDeep("controlOptions.environmentGuardLockMinutes") or 60.0
        ),
        "unlock_margin": float(
            data_store.getDeep("controlOptions.environmentGuardUnlockMargin") or 1.2
        ),
    }


def _select_best_air_source(
    indoor_temp: Optional[float],
    indoor_hum: Optional[float],
    ambient_temp: Optional[float],
    ambient_hum: Optional[float],
    outsite_temp: Optional[float],
    outsite_hum: Optional[float],
    has_intake: bool,
    is_intake_action: bool,
) -> Tuple[Optional[float], Optional[float], str]:
    """
    Intelligently select the best air source for the current situation.

    For intake actions: prioritizes outside weather data if available
    For exhaust/ventilate actions: uses ambient room data

    Returns: (selected_temp, selected_humidity, source_name)
    """
    has_outsite = outsite_temp is not None and outsite_hum is not None
    has_ambient = ambient_temp is not None and ambient_hum is not None

    if is_intake_action and has_outsite:
        return outsite_temp, outsite_hum, "outsite"

    if has_ambient:
        return ambient_temp, ambient_hum, "ambient"

    if has_outsite:
        return outsite_temp, outsite_hum, "outsite_fallback"

    return None, None, "none"


def _assess_risks(
    indoor_temp: Optional[float],
    indoor_hum: Optional[float],
    source_temp: Optional[float],
    source_hum: Optional[float],
    max_temp: Optional[float],
    max_hum: Optional[float],
    min_temp: Optional[float],
    min_hum: Optional[float],
    config: Dict[str, float],
) -> Dict[str, Any]:
    """
    Assess risks and benefits of bringing in air from the selected source.

    Returns dict with:
    - temp_risk: bringing in colder air when already too cold
    - humidity_risk: bringing in drier air when already too dry
    - temp_benefit: bringing in warmer air when too cold
    - humidity_benefit: bringing in drier air when too wet (good!)
    - humidity_critical: humidity is dangerously high (emergency!)
    - temp_critical: temperature is dangerously low
    """
    ambient_delta = config.get("ambient_delta", 1.2)
    humidity_delta = config.get("environmentGuardHumidityDelta", 15.0)
    humidity_margin = config.get("environmentGuardHumidityMargin", 5.0)
    min_margin = config.get("min_margin", 0.8)

    risk_assessment = {
        "temp_risk": False,
        "humidity_risk": False,
        "temp_benefit": False,
        "humidity_benefit": False,
        "humidity_critical": False,
        "humidity_critical_dry": False,
        "temp_critical": False,
    }

    if indoor_temp is None or source_temp is None:
        return risk_assessment

    if indoor_hum is None:
        return risk_assessment

    near_temp_floor = (
        min_temp is not None and indoor_temp <= (min_temp + min_margin)
    )
    near_humidity_floor = (
        min_hum is not None and indoor_hum <= (min_hum + humidity_margin)
    )

    risk_assessment["temp_risk"] = near_temp_floor and source_temp <= (
        indoor_temp - ambient_delta
    )

    risk_assessment["temp_benefit"] = (
        max_temp is not None
        and indoor_temp < max_temp
        and source_temp > indoor_temp
    )

    risk_assessment["humidity_risk"] = (
        has_source_hum := source_hum is not None,
        near_humidity_floor and has_source_hum and source_hum <= (indoor_hum - humidity_delta),
    )

    risk_assessment["humidity_benefit"] = (
        source_hum is not None
        and max_hum is not None
        and indoor_hum >= max_hum
        and source_hum < indoor_hum
    )

    risk_assessment["humidity_critical"] = (
        indoor_hum is not None
        and max_hum is not None
        and indoor_hum >= max_hum
    )

    risk_assessment["humidity_critical_dry"] = (
        indoor_hum is not None
        and min_hum is not None
        and indoor_hum <= min_hum
    )

    risk_assessment["temp_critical"] = (
        min_temp is not None and indoor_temp <= min_temp
    )

    return risk_assessment


def _decide_priority(
    risks: Dict[str, Any], indoor_hum: Optional[float], max_hum: Optional[float], min_hum: Optional[float] = None
) -> Tuple[bool, str, str]:
    """
    Decide whether to allow or block air exchange based on risk assessment.

    Priority hierarchy:
    1. humidity_critical (>= maxHumidity) → ALLOW (override everything, mold prevention)
    2. humidity_critical_dry (<= minHumidity) → ALLOW (need to add humidity)
    3. humidity_benefit (>= maxHumidity with drier source) → ALLOW (need to dry out)
    4. temp_benefit (warm air needed) → ALLOW
    5. temp_risk (too cold, source colder) → BLOCK
    6. humidity_risk (too dry, source drier) → BLOCK
    7. No risk → ALLOW

    Returns: (should_allow, reason, priority_level)
    """
    if risks.get("humidity_critical"):
        return True, "humidity_emergency_over_max", "emergency"

    if risks.get("humidity_critical_dry"):
        return True, "humidity_emergency_under_min", "emergency"

    if risks.get("humidity_benefit"):
        return True, "humidity_benefit_drying_needed", "high"

    if risks.get("temp_benefit"):
        return True, "temp_benefit_warming_needed", "high"

    if risks.get("temp_risk"):
        return False, "temp_risk_cold_source", "medium"

    if risks.get("humidity_risk"):
        return False, "humidity_risk_drying_source", "medium"

    return True, "no_risk_detected", "low"


def _evaluate_intake_source(
    indoor_temp: Optional[float],
    indoor_hum: Optional[float],
    ambient_temp: Optional[float],
    ambient_hum: Optional[float],
    outsite_temp: Optional[float],
    outsite_hum: Optional[float],
    config: Dict[str, float],
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluate which source is better for intake fan specifically.

    If outsite is available, compare both sources and choose the better one
    based on current needs.
    """
    has_outsite = outsite_temp is not None and outsite_hum is not None
    has_ambient = ambient_temp is not None and ambient_hum is not None

    if not has_outsite and not has_ambient:
        return True, "no_source_available", {}

    if has_outsite:
        outsite_risks = _assess_risks(
            indoor_temp, indoor_hum, outsite_temp, outsite_hum,
            None, None, None, None, config
        )
        should_allow_outsite, outsite_reason, _ = _decide_priority(
            outsite_risks, indoor_hum, None
        )

        if has_ambient:
            ambient_risks = _assess_risks(
                indoor_temp, indoor_hum, ambient_temp, ambient_hum,
                None, None, None, None, config
            )
            should_allow_ambient, ambient_reason, _ = _decide_priority(
                ambient_risks, indoor_hum, None
            )

            if should_allow_outsite and not should_allow_ambient:
                return True, f"outsite_better_than_ambient", {"source": "outsite", **outsite_risks}
            elif not should_allow_outsite and should_allow_ambient:
                return False, f"ambient_better_than_outsite", {"source": "ambient", **ambient_risks}
            elif should_allow_outsite:
                return True, "both_sources_allow_outsite_preferred", {"source": "outsite", **outsite_risks}
            else:
                return False, "both_sources_blocked", {"outsite_risks": outsite_risks, "ambient_risks": ambient_risks}

        return should_allow_outsite, outsite_reason, {"source": "outsite", **outsite_risks}

    if has_ambient:
        ambient_risks = _assess_risks(
            indoor_temp, indoor_hum, ambient_temp, ambient_hum,
            None, None, None, None, config
        )
        should_allow, reason, priority = _decide_priority(ambient_risks, indoor_hum, None)
        return should_allow, reason, {"source": "ambient", **ambient_risks}

    return True, "no_risk", {}


def evaluate_environment_guard(
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
    Main function to evaluate whether an air-exchange action should be allowed.

    This is the intelligent replacement for the previous air-exchange cold guard.

    Args:
        data_store: The OGB data store
        room: Room name
        capability: Device capability (canExhaust, canIntake, canVentilate)
        action: Action type (Increase, Reduce)
        message: Optional message for emergency detection
        priority: Priority level (emergency, high, medium, low)
        source: Source of the evaluation

    Returns:
        (should_block, metadata)
    """
    if capability not in AIR_EXCHANGE_CAPABILITIES or action != "Increase":
        return False, {"reason": "not_applicable", "source": source}

    now = time.time()
    tent_data = data_store.get("tentData") or {}
    config = _get_config_values(data_store)

    state_path = "safety.environmentGuard"
    state = data_store.getDeep(state_path) or {}

    blocked_count = int(state.get("blockedCount", 0) or 0)
    window_start = _to_float(state.get("windowStart"))
    lock_until = _to_float(state.get("lockUntil"))

    window_seconds = max(60.0, config.get("window_minutes", 30.0) * 60.0)
    lock_seconds = max(60.0, config.get("lock_minutes", 60.0) * 60.0)

    safety_override, override_reason = _is_safety_override_active(
        data_store, message, priority
    )
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
    indoor_hum = _to_float(tent_data.get("humidity"))
    max_temp = _to_float(tent_data.get("maxTemp"))
    max_hum = _to_float(tent_data.get("maxHumidity"))
    min_temp = _to_float(tent_data.get("minTemp"))
    min_hum = _to_float(tent_data.get("minHumidity"))

    ambient_temp = _to_float(tent_data.get("AmbientTemp"))
    ambient_hum = _to_float(tent_data.get("AmbientHum"))

    outsite_temp = _to_float(tent_data.get("OutsiteTemp"))
    outsite_hum = _to_float(tent_data.get("OutsiteHum"))

    capabilities = data_store.get("capabilities") or {}
    has_intake = capabilities.get("canIntake", {}).get("state", False)
    is_intake_action = capability == "canIntake"

    selected_temp, selected_hum, source_name = _select_best_air_source(
        indoor_temp, indoor_hum,
        ambient_temp, ambient_hum,
        outsite_temp, outsite_hum,
        has_intake, is_intake_action
    )

    risks = _assess_risks(
        indoor_temp, indoor_hum,
        selected_temp, selected_hum,
        max_temp, max_hum,
        min_temp, min_hum,
        config
    )

    should_allow, reason, priority_level = _decide_priority(risks, indoor_hum, max_hum, min_hum)

    lock_active = lock_until is not None and now < lock_until
    if lock_active and should_allow and selected_temp is not None and min_temp is not None:
        unlock_margin = config.get("unlock_margin", 1.2)
        can_unlock = selected_temp >= (indoor_temp - max(0.4, config.get("ambient_delta", 1.2) / 2.0)) and indoor_temp > (min_temp + unlock_margin)
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
                "selectedSource": source_name,
                "selectedTemp": selected_temp,
                "selectedHum": selected_hum,
            }
        )
        data_store.setDeep(state_path, state)
        return True, {
            "reason": "lock_active",
            "lockUntil": lock_until,
            "source": source,
            "selectedSource": source_name,
        }

    if not should_allow:
        if window_start is None or now - window_start > window_seconds:
            blocked_count = 0
            window_start = now

        blocked_count += 1
        if blocked_count >= 2:
            lock_until = now + lock_seconds

        state.update(
            {
                "blockedCount": blocked_count,
                "windowStart": window_start,
                "lockUntil": lock_until,
                "lastDecision": "blocked",
                "lastReason": reason,
                "lastSource": source,
                "lastUpdate": now,
                "selectedSource": source_name,
                "selectedTemp": selected_temp,
                "selectedHum": selected_hum,
                "priority": priority_level,
                "risks": {k: v for k, v in risks.items() if v},
            }
        )
        data_store.setDeep(state_path, state)

        return True, {
            "reason": reason,
            "blockedCount": blocked_count,
            "lockUntil": lock_until,
            "selectedSource": source_name,
            "selectedTemp": selected_temp,
            "selectedHum": selected_hum,
            "priority": priority_level,
            "source": source,
            "indoorTemp": indoor_temp,
            "indoorHum": indoor_hum,
            "maxTemp": max_temp,
            "maxHum": max_hum,
            "minTemp": min_temp,
            "minHum": min_hum,
        }

    if window_start is not None and now - window_start > window_seconds:
        blocked_count = 0
        window_start = None

    state.update(
        {
            "blockedCount": blocked_count,
            "windowStart": window_start,
            "lockUntil": lock_until,
            "lastDecision": "allowed",
            "lastReason": reason,
            "lastSource": source,
            "lastUpdate": now,
            "selectedSource": source_name,
            "selectedTemp": selected_temp,
            "selectedHum": selected_hum,
            "priority": priority_level,
            "risks": {k: v for k, v in risks.items() if v},
        }
    )
    data_store.setDeep(state_path, state)

    return False, {
        "reason": reason,
        "selectedSource": source_name,
        "selectedTemp": selected_temp,
        "selectedHum": selected_hum,
        "priority": priority_level,
        "source": source,
        "risks": {k: v for k, v in risks.items() if v},
        "indoorTemp": indoor_temp,
        "indoorHum": indoor_hum,
        "maxTemp": max_temp,
        "maxHum": max_hum,
        "minTemp": min_temp,
        "minHum": min_hum,
    }
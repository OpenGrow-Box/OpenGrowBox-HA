import re
from datetime import date

from ...const import LABELS_ONLY_SINCE
from ..data.OGBParams.OGBParams import DEVICE_TYPE_MAPPING
from ..data.OGBParams.OGBTranslations import SENSOR_TRANSLATIONS


def labels_only_enabled(now=None):
    """Return True once label-only sensor detection is in force."""
    return (now or date.today()) >= LABELS_ONLY_SINCE


SENSOR_DEVICE_KEYWORDS = {
    keyword
    for keyword in DEVICE_TYPE_MAPPING.get("Sensor", [])
    if keyword
}


# Context labels (medium area / room) must never be treated as a sensor type.
# They are only used for context detection (soil/water/air), but e.g. "soil"
# is also a moisture translation, which would misclassify a conductivity probe.
CONTEXT_LABEL_WORDS = {"soil", "substrat", "substrate", "boden", "medium"}


REMAPPABLE_SENSOR_TYPES = {"temperature", "humidity", "dewpoint", "co2"}
ENGLISH_SENSOR_FALLBACKS = {
    "_temperature": "temperature",
    "_humidity": "humidity",
    "_dewpoint": "dewpoint",
    "_dew_point": "dewpoint",
    "_co2": "co2",
    "_carbondioxide": "co2",
    "_leaf": "temperature",
    "_moisture": "moisture",
    "_energy": "energy",
    "_power": "power",
    "_voltage": "voltage",
    "_current": "current",
}


# Device-control/output metrics (duty cycle, intensity, frequency, ...) that are
# NOT climate readings and must never be classified as a sensor type. They would
# otherwise inherit a device label (e.g. "Humidifier" -> humidity via "hum") and
# pollute the air context used by VPD calculations.
# NOTE: power/energy stay resolvable - they are valid energy-context types.
NON_CLIMATE_CONTROL_SUFFIXES = {"duty", "intensity", "frequency"}


def _normalize_token(value):
    if value is None:
        return ""
    return str(value).lower().strip()


def _build_translation_cache():
    cache = {}
    for canonical_type, translations in SENSOR_TRANSLATIONS.items():
        cache[_normalize_token(canonical_type)] = canonical_type
        for translation in translations:
            cache[_normalize_token(translation)] = canonical_type
    return cache


TRANSLATION_CACHE = _build_translation_cache()


def _match_translation(value):
    normalized = _normalize_token(value)
    if not normalized:
        return None

    if normalized in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[normalized]

    compact = normalized.replace("_", " ").replace("-", " ")
    if compact in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[compact]

    object_tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", normalized) if token]
    for token in object_tokens:
        if token in TRANSLATION_CACHE:
            return TRANSLATION_CACHE[token]

    for translation, canonical_type in TRANSLATION_CACHE.items():
        # Avoid over-aggressive fuzzy matches for ultra-short abbreviations
        # (e.g. "v" from voltage matching "ventilation").
        if not translation or len(translation) < 3:
            continue
        # Require word boundaries for short substring matches to avoid false
        # positives like "hum" matching inside "dehumidifier". Still allow
        # matches at the start of a longer word (e.g. "hum" in "humidite").
        if len(translation) <= 4:
            pattern = r"(?:^|[^a-z0-9])" + re.escape(translation) + r"(?:[^a-z0-9]|$|[a-z0-9])"
            if re.search(pattern, normalized):
                return canonical_type
        elif translation in normalized:
            return canonical_type

    return None


def _extract_label_candidates(labels):
    candidates = []
    for label in labels or []:
        if not isinstance(label, dict):
            continue
        label_id = label.get("id")
        label_name = label.get("name")
        if label_id:
            candidates.append(label_id)
        if label_name:
            candidates.append(label_name)
    return candidates


def _has_sensor_device_label(labels):
    """Return True if any label identifies a Sensor-type device (label gate)."""
    for label in labels or []:
        if not isinstance(label, dict):
            continue
        for value in (label.get("id"), label.get("name")):
            if value and _normalize_token(value) in SENSOR_DEVICE_KEYWORDS:
                return True
    return False


def resolve_sensor_types(entity_id, labels=None):
    """Resolve canonical sensor types with label/translation priority."""
    resolved_types = []
    seen = set()

    def add(sensor_type):
        if sensor_type and sensor_type not in seen:
            seen.add(sensor_type)
            resolved_types.append(sensor_type)

    object_id = entity_id.split(".", 1)[-1].lower() if entity_id else ""

    # Frequency sensors must never be classified as temp/hum (or any other
    # remappable type) - they carry no climate value and would pollute VPD.
    # Same applies to other device-control metrics (duty, intensity).
    if any(token in object_id for token in NON_CLIMATE_CONTROL_SUFFIXES):
        return []

    labels_only = labels_only_enabled()

    # Branch logic with the label: in label-only mode, multilingual
    # entity-name resolution is only allowed when the device carries a
    # Sensor-type label (the label is the gate).
    allow_name_matching = not labels_only or _has_sensor_device_label(labels)

    # 1) Strongest signal: explicit legacy suffixes in entity_id
    if not labels_only:
        for fallback, sensor_type in ENGLISH_SENSOR_FALLBACKS.items():
            if fallback in object_id or object_id.endswith(fallback.lstrip("_")):
                add(sensor_type)

    # If we already have a deterministic remappable type from entity_id,
    # don't let generic labels (e.g. "Ventilation") override it.
    if resolved_types:
        return resolved_types

    # 2) Labels/translations
    for candidate in _extract_label_candidates(labels):
        if _normalize_token(candidate) in CONTEXT_LABEL_WORDS:
            continue
        add(_match_translation(candidate))

    if allow_name_matching and not resolved_types:
        entity_candidates = [object_id]

        if object_id:
            entity_candidates.extend(token for token in object_id.split("_") if token)
            entity_candidates.append(object_id.split("_")[-1])

        for candidate in entity_candidates:
            add(_match_translation(candidate))
            if resolved_types:
                break

    if not labels_only and not resolved_types:
        for fallback, sensor_type in ENGLISH_SENSOR_FALLBACKS.items():
            if fallback in object_id or object_id.endswith(fallback.lstrip("_")):
                add(sensor_type)
                break

    return resolved_types


def resolve_remappable_sensor_type(entity_id, labels=None):
    """Resolve one remappable sensor type for sensor-domain device splitting."""
    for sensor_type in resolve_sensor_types(entity_id, labels):
        if sensor_type in REMAPPABLE_SENSOR_TYPES:
            return sensor_type

    if labels_only_enabled():
        return None

    # Legacy compatibility: keep old suffix-based behavior for remap-critical types
    object_id = entity_id.split(".", 1)[-1].lower() if entity_id else ""
    if any(token in object_id for token in NON_CLIMATE_CONTROL_SUFFIXES):
        return None
    if "_temperature" in object_id or object_id.endswith("temperature"):
        return "temperature"
    if "_humidity" in object_id or object_id.endswith("humidity"):
        return "humidity"
    if "_dewpoint" in object_id or "_dew_point" in object_id or object_id.endswith("dewpoint"):
        return "dewpoint"
    if "_co2" in object_id or object_id.endswith("co2"):
        return "co2"
    if "_leaf" in object_id or object_id.endswith("leaf"):
        return "temperature"
    return None


def is_ogb_output_sensor(entity_name) -> bool:
    """Return True for OGB's own sensor entities.

    OGB writes these itself (ambient/outsite/VPD/avg mirrors) via the
    ``opengrowbox.update_sensor`` service. They are pure outputs, so they can
    never be configuration inputs and must not be routed into the
    configuration manager - doing so logged "Unhandled entity update" for every
    single update, once per room.
    """
    name = str(entity_name or "").strip().lower()
    return name.startswith("sensor.") and "ogb_" in name


def should_route_to_config_manager(entity_name) -> bool:
    """Return True when an OGB entity update belongs to the configuration manager."""
    name = str(entity_name or "").strip().lower()
    if "ogb_" not in name:
        return False
    return not is_ogb_output_sensor(name)

import re

from ..data.OGBParams.OGBTranslations import SENSOR_TRANSLATIONS


REMAPPABLE_SENSOR_TYPES = {"temperature", "humidity", "dewpoint", "co2"}
ENGLISH_SENSOR_FALLBACKS = {
    "_temperature": "temperature",
    "_humidity": "humidity",
    "_dewpoint": "dewpoint",
    "_dew_point": "dewpoint",
    "_co2": "co2",
    "_carbondioxide": "co2",
}


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
        if translation in normalized:
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


def resolve_sensor_types(entity_id, labels=None):
    """Resolve canonical sensor types with label/translation priority."""
    resolved_types = []
    seen = set()

    def add(sensor_type):
        if sensor_type and sensor_type not in seen:
            seen.add(sensor_type)
            resolved_types.append(sensor_type)

    object_id = entity_id.split(".", 1)[-1].lower() if entity_id else ""

    # 1) Strongest signal: explicit legacy suffixes in entity_id
    for fallback, sensor_type in ENGLISH_SENSOR_FALLBACKS.items():
        if fallback in object_id or object_id.endswith(fallback.lstrip("_")):
            add(sensor_type)

    # If we already have a deterministic remappable type from entity_id,
    # don't let generic labels (e.g. "Ventilation") override it.
    if resolved_types:
        return resolved_types

    # 2) Labels/translations
    for candidate in _extract_label_candidates(labels):
        add(_match_translation(candidate))

    if not resolved_types:
        entity_candidates = [object_id]

        if object_id:
            entity_candidates.extend(token for token in object_id.split("_") if token)
            entity_candidates.append(object_id.split("_")[-1])

        for candidate in entity_candidates:
            add(_match_translation(candidate))
            if resolved_types:
                break

    if not resolved_types:
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

    # Legacy compatibility: keep old suffix-based behavior for remap-critical types
    object_id = entity_id.split(".", 1)[-1].lower() if entity_id else ""
    if "_temperature" in object_id or object_id.endswith("temperature"):
        return "temperature"
    if "_humidity" in object_id or object_id.endswith("humidity"):
        return "humidity"
    if "_dewpoint" in object_id or "_dew_point" in object_id or object_id.endswith("dewpoint"):
        return "dewpoint"
    if "_co2" in object_id or object_id.endswith("co2"):
        return "co2"

    return None

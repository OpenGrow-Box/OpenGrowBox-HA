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


# Tokens whose only purpose is to describe how a device is being driven.
# An entity made up of these carries no measurement at all, so it is dropped
# during type resolution instead of being classified.
CONTROL_ONLY_TOKENS = frozenset({"duty", "intensity", "frequency"})

# Diagnostic/metric tokens: energy, power, signal, runtime, counters, ...
# An entity containing one of these may still resolve to a legitimate type
# (e.g. "s_consumption" -> energy, "signal_level" -> water_level), but it must
# never contribute a *climate* type. Otherwise the entity inherits the device's
# role label (a "Humidifier" label fuzzy-matches "hum" -> humidity) and is fed
# into the air context that drives the VPD calculation.
NON_CLIMATE_CONTROL_TOKENS = frozenset(
    CONTROL_ONLY_TOKENS
    | {
        "power",
        "watt",
        "watts",
        "kw",
        "kwh",
        "wh",
        "joule",
        "energy",
        "consumption",
        "verbrauch",
        "voltage",
        "volts",
        "current",
        "amperage",
        "amp",
        "signal",
        "rssi",
        "wifi",
        "bluetooth",
        "ble",
        "level",
        "brightness",
        "uptime",
        "duration",
        "runtime",
        "count",
        "counter",
        "status",
        "state",
        "mode",
        "cost",
        "tariff",
        "price",
        "memory",
        "heap",
        # German equivalents, which appear just as often in real installations
        "leistung",
        "energie",
        "spannung",
        "strom",
        "stromverbrauch",
        "laufzeit",
        "frequenz",
        "signalstaerke",
        "helligkeit",
        "zaehler",
        "zähler",
        "kosten",
        "modus",
    }
)

# Explicit evidence of a genuine climate reading. Checked before the metric
# tokens so a room/device name that happens to contain one of them
# ("power_tent_temperature") cannot disable the climate check.
CLIMATE_ENTITY_TOKENS = frozenset(
    {
        "temperature",
        "temperatur",
        "temp",
        "humidity",
        "feuchtigkeit",
        "feuchte",
        "luftfeuchtigkeit",
        "luftfeuchte",
        "dewpoint",
        "taupunkt",
    }
)

# Types that describe the surrounding air and therefore drive VPD.
CLIMATE_SENSOR_TYPES = frozenset({"temperature", "humidity", "dewpoint"})

# Backwards-compatible alias. Consumers only ever tested membership, and the
# VPD guard now applies the set to every token instead of the last one.
NON_CLIMATE_CONTROL_SUFFIXES = NON_CLIMATE_CONTROL_TOKENS


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


def _label_candidates_by_scope(labels):
    """Split label candidates into entity-scoped and device-scoped lists.

    An entity label is a statement about the entity itself, a device label only
    describes the role of the device it is attached to. Keeping them apart lets
    sensor-type resolution treat the device label as a fallback instead of an
    override. Labels without an explicit scope are treated as entity labels,
    which preserves the previous behaviour for callers that do not set scope.
    """
    entity_candidates = []
    device_candidates = []

    for label in labels or []:
        if not isinstance(label, dict):
            continue
        values = []
        if label.get("id"):
            values.append(label["id"])
        if label.get("name"):
            values.append(label["name"])
        bucket = device_candidates if label.get("scope") == "device" else entity_candidates
        bucket.extend(values)

    return entity_candidates, device_candidates


def _has_sensor_device_label(labels):
    """Return True if any label identifies a Sensor-type device (label gate)."""
    for label in labels or []:
        if not isinstance(label, dict):
            continue
        for value in (label.get("id"), label.get("name")):
            if value and _normalize_token(value) in SENSOR_DEVICE_KEYWORDS:
                return True
    return False


# Home Assistant entity ids may contain German umlauts ("zaehler" style tokens
# are common in real installs), but the tokenizer splits on ASCII only. Fold
# them to their two-letter form first so such entities match the vocabulary.
_GERMAN_TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def object_id_tokens(entity_id):
    """Split an entity_id into lowercase alphanumeric tokens.

    Splitting on every non-alphanumeric character (so ``_``, ``/`` and ``-``)
    matters for Tasmota-style ids such as
    ``sensor.dev_today/s_consumption`` where the meaningful part is not the
    final underscore-separated segment.
    """
    object_id = entity_id.split(".", 1)[-1].lower() if entity_id else ""
    object_id = object_id.translate(_GERMAN_TRANSLITERATION)
    return [token for token in re.split(r"[^a-z0-9]+", object_id) if token]


def is_non_climate_metric_entity(entity_id):
    """Return True for metric/diagnostic entities that are never climate readings.

    An explicit climate token always wins, so a device whose name contains a
    metric word (``power_tent_temperature``) still resolves as a temperature
    sensor.
    """
    tokens = set(object_id_tokens(entity_id))
    if not tokens:
        return False
    if tokens & CLIMATE_ENTITY_TOKENS:
        return False
    return bool(tokens & NON_CLIMATE_CONTROL_TOKENS)


def resolve_sensor_types(entity_id, labels=None):
    """Resolve canonical sensor types with label/translation priority."""
    resolved_types = []
    seen = set()

    metric_entity = is_non_climate_metric_entity(entity_id)

    def add(sensor_type):
        if not sensor_type or sensor_type in seen:
            return
        seen.add(sensor_type)
        # A metric entity may still resolve to energy/power/water_level, but it
        # must never contribute a climate type - that is precisely what pushes
        # it into the air context that feeds VPD.
        if metric_entity and sensor_type in CLIMATE_SENSOR_TYPES:
            return
        resolved_types.append(sensor_type)

    object_id = entity_id.split(".", 1)[-1].lower() if entity_id else ""

    # Pure control metrics (duty cycle, intensity, frequency) carry no
    # measurement at all, so there is nothing left to classify.
    if set(object_id_tokens(object_id)) & CONTROL_ONLY_TOKENS:
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

    # 2) The entity's own name. This is direct evidence about *this* entity and
    #    therefore outranks a label that only describes the device it sits on.
    #    Without this ordering a "Humidifier" device label turns every metric
    #    on that device (today/s_consumption, signal_level) into humidity.
    if allow_name_matching:
        entity_candidates = [object_id]

        if object_id:
            entity_candidates.extend(token for token in object_id.split("_") if token)
            entity_candidates.append(object_id.split("_")[-1])

        for candidate in entity_candidates:
            add(_match_translation(candidate))
            if resolved_types:
                break

    if resolved_types:
        return resolved_types

    entity_candidates, device_candidates = _label_candidates_by_scope(labels)

    # 3) Entity labels describe this exact entity, so they may confirm or
    #    override the name-based result.
    for candidate in entity_candidates:
        if _normalize_token(candidate) in CONTEXT_LABEL_WORDS:
            continue
        add(_match_translation(candidate))

    if resolved_types:
        return resolved_types

    # 4) Device labels describe the device *role*, not the entity. They are the
    #    weakest signal and only serve as a last resort.
    for candidate in device_candidates:
        if _normalize_token(candidate) in CONTEXT_LABEL_WORDS:
            continue
        add(_match_translation(candidate))

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
    if set(object_id_tokens(object_id)) & CONTROL_ONLY_TOKENS:
        return None
    if is_non_climate_metric_entity(object_id):
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

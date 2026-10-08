"""Metric entities of a device must never enter the air context (VPD).

Regression from a real installation: a Tasmota plug labelled "Humidifier"
carried energy/signal entities that all landed in the humidity group, so the
VPD calculation was fed

    sensor.heatertent_today/s_consumption        -> 0.06
    sensor.heatertent_this_month/s_consumption   -> 0.062
    sensor.heatertent_signal_level               -> 2.0

as if they were humidity readings (hum_count=5 instead of 2).

Two defects combined:

1. ``_match_translation`` fuzzy-matches the device label "Humidifier" to the
   humidity type ("hum" at a word start), and ``resolve_sensor_types`` consulted
   labels *before* the entity's own name. The entity id was never asked, even
   though "consumption" resolves to energy and "level" to water_level.
2. The VPD-side guard only compared the *last* underscore-separated segment, so
   ".../s_consumption" (last segment "s/consumption") passed unnoticed.
"""

from custom_components.opengrowbox.OGBController.managers.core.OGBVPDManager import (
    _is_vpd_climate_entity,
)
from custom_components.opengrowbox.OGBController.utils.sensor_identification import (
    is_non_climate_metric_entity,
    resolve_remappable_sensor_type,
    resolve_sensor_types,
)

# The exact entities from the bug report, with the device label that caused it.
HUMIDIFIER_DEVICE_LABEL = [{"name": "Humidifier", "scope": "device"}]

POLLUTING_ENTITIES = [
    "sensor.heatertent_today/s_consumption",
    "sensor.heatertent_this_month/s_consumption",
    "sensor.heatertent_signal_level",
]

# The two real air sensors of the same room.
REAL_AIR_SENSORS = [
    "sensor.sensor5tent_temperature",
    "sensor.sensor6tent_humidity",
]


class TestMetricEntitiesAreNotClimateReadings:
    def test_polluting_entities_do_not_resolve_as_climate(self):
        for entity_id in POLLUTING_ENTITIES:
            resolved = resolve_sensor_types(entity_id, HUMIDIFIER_DEVICE_LABEL)
            assert "humidity" not in resolved, entity_id
            assert "temperature" not in resolved, entity_id
            assert "dewpoint" not in resolved, entity_id

    def test_polluting_entities_resolve_their_real_type(self):
        assert "energy" in resolve_sensor_types(
            "sensor.heatertent_today/s_consumption", HUMIDIFIER_DEVICE_LABEL
        )
        assert "energy" in resolve_sensor_types(
            "sensor.heatertent_this_month/s_consumption", HUMIDIFIER_DEVICE_LABEL
        )
        assert "water_level" in resolve_sensor_types(
            "sensor.heatertent_signal_level", HUMIDIFIER_DEVICE_LABEL
        )

    def test_polluting_entities_have_no_remappable_type(self):
        # A remappable type is what routes an entity into a climate context.
        for entity_id in POLLUTING_ENTITIES:
            assert resolve_remappable_sensor_type(entity_id, HUMIDIFIER_DEVICE_LABEL) is None

    def test_german_device_label_is_also_rejected(self):
        german = [{"name": "Befeuchter", "scope": "device"}]
        for entity_id in POLLUTING_ENTITIES:
            resolved = resolve_sensor_types(entity_id, german)
            assert "humidity" not in resolved, entity_id

    def test_without_any_label_they_are_still_not_climate(self):
        for entity_id in POLLUTING_ENTITIES:
            resolved = resolve_sensor_types(entity_id)
            assert "humidity" not in resolved, entity_id


class TestRealAirSensorsSurvive:
    def test_real_sensors_still_resolve(self):
        for entity_id in REAL_AIR_SENSORS:
            resolved = resolve_sensor_types(entity_id, HUMIDIFIER_DEVICE_LABEL)
            assert resolved, entity_id

    def test_temperature_and_humidity_keep_their_type(self):
        assert resolve_sensor_types(
            "sensor.sensor5tent_temperature", HUMIDIFIER_DEVICE_LABEL
        ) == ["temperature"]
        assert resolve_sensor_types(
            "sensor.sensor6tent_humidity", HUMIDIFIER_DEVICE_LABEL
        ) == ["humidity"]

    def test_real_sensors_are_remappable(self):
        assert (
            resolve_remappable_sensor_type("sensor.sensor5tent_temperature")
            == "temperature"
        )
        assert resolve_remappable_sensor_type("sensor.sensor6tent_humidity") == "humidity"


class TestVpdGuardCoversEveryToken:
    def test_guard_rejects_the_polluting_entities(self):
        for entity_id in POLLUTING_ENTITIES:
            assert _is_vpd_climate_entity({"entity_id": entity_id}) is False, entity_id

    def test_guard_accepts_real_air_sensors(self):
        for entity_id in REAL_AIR_SENSORS:
            assert _is_vpd_climate_entity({"entity_id": entity_id}) is True, entity_id

    def test_guard_scans_all_tokens_not_just_the_last(self):
        # "s/consumption" is the final underscore segment - the old guard
        # compared only that string and let the entity through.
        assert "consumption" in POLLUTING_ENTITIES[0].rsplit("_", 1)[-1]
        assert _is_vpd_climate_entity({"entity_id": POLLUTING_ENTITIES[0]}) is False

    def test_guard_rejects_brightness_and_duty(self):
        assert _is_vpd_climate_entity({"entity_id": "sensor.lamp_brightness"}) is False
        assert _is_vpd_climate_entity({"entity_id": "sensor.devhumidifierdimm_duty"}) is False

    def test_guard_rejects_multilingual_metric_entities(self):
        assert _is_vpd_climate_entity({"entity_id": "sensor.steckdose_verbrauch"}) is False
        assert _is_vpd_climate_entity({"entity_id": "sensor.lampe_leistung"}) is False


class TestMetricDetection:
    def test_detects_the_reported_entities(self):
        for entity_id in POLLUTING_ENTITIES:
            assert is_non_climate_metric_entity(entity_id) is True, entity_id

    def test_climate_entities_are_not_metric(self):
        for entity_id in REAL_AIR_SENSORS:
            assert is_non_climate_metric_entity(entity_id) is False, entity_id

    def test_explicit_climate_token_beats_metric_token(self):
        # A room/device name may contain a metric word; the climate token wins.
        assert is_non_climate_metric_entity("sensor.power_tent_temperature") is False
        assert resolve_sensor_types("sensor.power_tent_temperature") == ["temperature"]

    def test_unknown_entities_are_not_metric(self):
        assert is_non_climate_metric_entity("sensor.sensor5tent") is False
        assert is_non_climate_metric_entity("") is False


class TestControlOnlyEntitiesAreDropped:
    def test_control_metrics_have_no_type_at_all(self):
        for entity_id in (
            "sensor.devhumidifierdimm_duty",
            "sensor.pump_intensity",
            "sensor.pump_frequency",
        ):
            assert resolve_sensor_types(entity_id) == []
            assert resolve_remappable_sensor_type(entity_id) is None


class TestLabelPrecedence:
    def test_entity_label_overrides_the_entity_name(self):
        labels = [{"name": "humidity", "scope": "entity"}]
        assert resolve_sensor_types("sensor.probe_one", labels) == ["humidity"]

    def test_device_label_is_only_a_last_resort(self):
        # No entity evidence at all -> the device label still resolves something.
        assert resolve_sensor_types(
            "sensor.probe_one", HUMIDIFIER_DEVICE_LABEL
        ) == ["humidity"]

    def test_entity_name_beats_the_device_label(self):
        resolved = resolve_sensor_types(
            "sensor.probe_humidity", HUMIDIFIER_DEVICE_LABEL
        )
        assert resolved == ["humidity"]

    def test_device_label_cannot_turn_energy_into_humidity(self):
        resolved = resolve_sensor_types(
            "sensor.probe_energy", HUMIDIFIER_DEVICE_LABEL
        )
        assert resolved == ["energy"]
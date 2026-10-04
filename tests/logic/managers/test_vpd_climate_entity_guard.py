"""VPD must only consume real climate readings, never device-control metrics.

Regression: a humidifier's `sensor.devhumidifierdimm_duty` inherited the
"humidity" type from its device label ("Humidifier" fuzzy-matches "hum" ->
humidity) and was fed into the VPD calculation as if it were a humidity reading.
"""

from custom_components.opengrowbox.OGBController.managers.core.OGBVPDManager import (
    NON_CLIMATE_CONTROL_SUFFIXES,
    _is_vpd_climate_entity,
)


def _entry(entity_id):
    return {"entity_id": entity_id, "state": "5.0"}


class TestIsVpdClimateEntity:
    def test_climate_sensors_pass(self):
        assert _is_vpd_climate_entity(_entry("sensor.devsensor1_temperature")) is True
        assert _is_vpd_climate_entity(_entry("sensor.devsensor1_humidity")) is True

    def test_multilingual_climate_sensors_pass(self):
        assert _is_vpd_climate_entity(_entry("sensor.devsensor3_temperatur")) is True
        assert _is_vpd_climate_entity(_entry("sensor.devsensor3_humidita")) is True

    def test_duty_excluded(self):
        assert _is_vpd_climate_entity(_entry("sensor.devhumidifierdimm_duty")) is False

    def test_power_energy_excluded(self):
        assert _is_vpd_climate_entity(_entry("sensor.devmainlight_power")) is False
        assert _is_vpd_climate_entity(_entry("sensor.devmainlight_energy")) is False

    def test_control_metrics_excluded(self):
        for suffix in NON_CLIMATE_CONTROL_SUFFIXES:
            assert _is_vpd_climate_entity(
                _entry(f"sensor.some_device_{suffix}")
            ) is False

    def test_missing_entity_id_excluded(self):
        assert _is_vpd_climate_entity({"state": "5.0"}) is False
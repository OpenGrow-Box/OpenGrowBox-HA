"""Tests for label-only sensor detection mode (active from 2027-01-01)."""

import datetime
import pytest

from custom_components.opengrowbox.const import LABELS_ONLY_SINCE
import custom_components.opengrowbox.OGBController.utils.sensor_identification as sid
from custom_components.opengrowbox.OGBController.utils.sensor_identification import (
    labels_only_enabled,
    resolve_sensor_types,
    resolve_remappable_sensor_type,
)


class TestLabelsOnlySwitch:
    """Test the date-gated switch."""

    def test_disabled_before_cutoff(self):
        assert labels_only_enabled(datetime.date(2026, 12, 31)) is False

    def test_enabled_at_cutoff(self):
        assert labels_only_enabled(datetime.date(2027, 1, 1)) is True

    def test_enabled_after_cutoff(self):
        assert labels_only_enabled(datetime.date(2027, 6, 1)) is True

    def test_cutoff_date_is_2027(self):
        assert LABELS_ONLY_SINCE == datetime.date(2027, 1, 1)


class TestLabelsOnlySensorTypes:
    """Name-based detection must be disabled in labels-only mode."""

    def test_suffix_detection_disabled(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.room_temperature") == []
        assert resolve_sensor_types("sensor.box_humidity") == []

    def test_translation_detection_disabled(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.carpa_temperatura") == []

    def test_labels_still_resolve(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        humidity_label = [{"id": "xlbl_hum", "name": "Luftfeuchtigkeit"}]
        temp_label = [{"id": "xlbl_temp", "name": "Temperatur"}]
        assert resolve_sensor_types("sensor.custom_probe", humidity_label) == ["humidity"]
        assert resolve_sensor_types("sensor.custom_probe", temp_label) == ["temperature"]

    def test_frequency_still_excluded(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.humidifier_frequency") == []


class TestLabelsOnlyControlMetricsExcluded:
    """Duty/intensity/frequency (device control outputs) must never be
    classified as climate types - even with a climate-matching device label.
    Regression: a humidifier's duty sensor inherited 'humidity' via the fuzzy
    'hum' inflection and polluted the VPD calculation."""

    HUMIDIFIER_LABELS = [
        {"id": "humidifier", "name": "Humidifier", "scope": "device"},
        {"id": "dimm", "name": "Dimm", "scope": "device"},
    ]

    def test_duty_never_climate_in_label_only_mode(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types(
            "sensor.devhumidifierdimm_duty", self.HUMIDIFIER_LABELS
        ) == []

    def test_duty_never_climate_in_legacy_mode(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: False)
        assert resolve_sensor_types(
            "sensor.devhumidifierdimm_duty", self.HUMIDIFIER_LABELS
        ) == []
        assert sid.resolve_remappable_sensor_type(
            "sensor.devhumidifierdimm_duty", self.HUMIDIFIER_LABELS
        ) is None

    def test_intensity_never_climate(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types(
            "sensor.devmainlight_intensity", [{"id": "light", "name": "Light"}]
        ) == []

    def test_frequency_still_excluded_with_humidifier_label(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types(
            "sensor.humidifier_frequency", self.HUMIDIFIER_LABELS
        ) == []

    def test_real_humidifier_humidity_sensor_still_resolves(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types(
            "sensor.devhumidifierdimm_humidity", self.HUMIDIFIER_LABELS
        ) == ["humidity"]

    def test_multilingual_climate_sensors_untouched(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        sensor_label = [{"id": "sensor", "name": "Sensor"}]
        assert resolve_sensor_types("sensor.devsensor3_humidita", sensor_label) == ["humidity"]
        assert resolve_sensor_types("sensor.devsensor3_temperatur", sensor_label) == ["temperature"]


class TestLabelsOnlyRemappable:
    """Remap-critical resolution must be label-only too."""

    def test_legacy_suffix_remap_disabled(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_remappable_sensor_type("sensor.box_temperature") is None
        assert resolve_remappable_sensor_type("sensor.box_humidity") is None

    def test_label_remap_still_works(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        humidity_label = [{"id": "xlbl_hum", "name": "Luftfeuchtigkeit"}]
        temp_label = [{"id": "xlbl_temp", "name": "Temperatur"}]
        assert resolve_remappable_sensor_type("sensor.custom_probe", humidity_label) == "humidity"
        assert resolve_remappable_sensor_type("sensor.custom_probe", temp_label) == "temperature"


class TestLabelsOnlySensorDeviceGate:
    """Sensor-device label is the gate for multilingual name resolution."""

    def test_sensor_label_enables_name_translation(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        sensor_label = [{"id": "sensor", "name": "Sensor"}]
        assert resolve_sensor_types("sensor.devsensor1_temperature", sensor_label) == ["temperature"]
        assert resolve_sensor_types("sensor.devsensor1_humidity", sensor_label) == ["humidity"]

    def test_sensor_label_enables_multilingual_translation(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        sensor_label = [{"id": "sensor", "name": "Sensor"}]
        assert resolve_sensor_types("sensor.devsensor1_temperatura", sensor_label) == ["temperature"]
        assert resolve_sensor_types("sensor.devsensor1_humedad", sensor_label) == ["humidity"]

    def test_sensor_label_enables_remap(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        sensor_label = [{"id": "sensor", "name": "Sensor"}]
        assert resolve_remappable_sensor_type("sensor.devsensor1_temperature", sensor_label) == "temperature"
        assert resolve_remappable_sensor_type("sensor.devsensor1_humidity", sensor_label) == "humidity"

    def test_non_sensor_label_blocks_name_resolution(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        neutral_label = [{"id": "garage", "name": "Garage"}]
        assert resolve_sensor_types("sensor.garage_temperature", neutral_label) == []
        assert resolve_remappable_sensor_type("sensor.garage_temperature", neutral_label) is None


class TestLabelsOnlyWaterDeviceGate:
    """The water label opens the sensor-device gate (devwatertester)."""

    WATER_LABELS = [{"id": "water", "name": "Water"}]

    def test_water_label_opens_gate(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert sid._has_sensor_device_label(self.WATER_LABELS) is True
        assert sid._has_sensor_device_label([{"id": "wasser", "name": "Wasser"}]) is True
        assert sid._has_sensor_device_label([{"id": "pump", "name": "Pump"}]) is False

    def test_water_label_enables_ec(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.devwatertester_ec", self.WATER_LABELS) == ["ec"]

    def test_water_label_enables_ph(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.devwatertester_ph", self.WATER_LABELS) == ["ph"]

    def test_water_label_enables_tds(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.devwatertester_tds", self.WATER_LABELS) == ["tds"]
        assert resolve_sensor_types("sensor.devwatertester_ppm", self.WATER_LABELS) == ["tds"]

    def test_water_label_enables_oxidation(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.devwatertester_orp", self.WATER_LABELS) == ["oxidation"]

    def test_water_label_enables_salinity(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        assert resolve_sensor_types("sensor.devwatertester_sal", self.WATER_LABELS) == ["salinity"]

    def test_water_label_without_sensor_gate_still_suffixes_legacy(self):
        assert resolve_sensor_types(
            "sensor.devwatertester_ec", self.WATER_LABELS
        ) == ["ec"]

    def test_pump_label_blocks_water_sensors(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: True)
        pump_label = [{"id": "pump", "name": "Pump"}]
        assert resolve_sensor_types("sensor.devwaterpump_ec", pump_label) == []


class TestLegacyModeStillWorks:
    """Before the cutoff the old behavior must be unchanged."""

    def test_suffix_detection_still_active(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: False)
        assert resolve_sensor_types("sensor.room_temperature") == ["temperature"]
        assert resolve_sensor_types("sensor.box_humidity") == ["humidity"]

    def test_legacy_remap_still_active(self, monkeypatch):
        monkeypatch.setattr(sid, "labels_only_enabled", lambda now=None: False)
        assert resolve_remappable_sensor_type("sensor.box_temperature") == "temperature"
        assert resolve_remappable_sensor_type("sensor.box_humidity") == "humidity"


class TestContextLabelsNotSensorTypes:
    """Area/context labels (soil, water, medium) must not override a
    specific entity-name match (e.g. conductivity -> ec)."""

    SOIL_LABELS = [
        {"id": "medium_1", "name": "Medium_1", "scope": "entity"},
        {"id": "soil", "name": "Soil", "scope": "device"},
    ]

    def test_conductivity_with_soil_label_stays_ec(self):
        assert resolve_sensor_types(
            "sensor.devsoilsensor_conductivity", self.SOIL_LABELS
        ) == ["ec"]

    def test_moisture_with_soil_label_stays_moisture(self):
        assert resolve_sensor_types(
            "sensor.devsoilsensor_moisture", self.SOIL_LABELS
        ) == ["moisture"]

    def test_temperature_with_soil_label_stays_temperature(self):
        assert resolve_sensor_types(
            "sensor.devsoilsensor_temperature", self.SOIL_LABELS
        ) == ["temperature"]

    def test_water_context_label_does_not_block_name_match(self):
        assert resolve_sensor_types(
            "sensor.tank_temperature", [{"id": "water", "name": "Water"}]
        ) == ["temperature"]
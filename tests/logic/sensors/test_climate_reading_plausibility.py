"""A climate reading must be plausible, not merely inside its domain.

Regression from a real installation: ``sensor.heatertent_signal_level`` reported
a constant ``2.0`` and was averaged into the room humidity as if it were 2%
relative humidity. The old guard only rejected ``value <= 0 or value > 100``, so
the mathematically legal but physically absurd reading passed and dragged the
room average from 80.7% down to 54.47%, which in turn pushed the computed VPD
from ~0.55 kPa to 1.29 kPa and made the controller run the humidifier while the
tent was actually too humid.

Two independent layers are tested here:

1. ``climate_reading_reject_reason`` - a per-reading plausibility band inside
   the measurement domain.
2. ``drop_climate_outliers`` - a cross-check against the median of the group,
   which catches a wrong-but-in-range value that no single reading can spot.
"""

import pytest

from custom_components.opengrowbox.OGBController.managers.core.OGBVPDManager import (
    OGBVPDManager,
    climate_reading_reject_reason,
    drop_climate_outliers,
)
from custom_components.opengrowbox.OGBController.utils.calcs import calculate_avg_value

# The exact readings from the bug report.
BUG_HUMIDITIES = [
    {"entity_id": "sensor.sensor6tent_humidity", "value": 79.5},
    {"entity_id": "sensor.sensor8tent_humidity", "value": 81.9},
    {"entity_id": "sensor.heatertent_signal_level", "value": 2.0},
]


def _rejected(sensor_type, value):
    return climate_reading_reject_reason(sensor_type, "sensor.example", value)


class TestHumidityPlausibility:
    def test_the_reported_two_percent_reading_is_rejected(self):
        assert _rejected("humidity", 2.0) is not None
        assert "implausible" in _rejected("humidity", 2.0)

    def test_domain_values_are_still_rejected(self):
        for value in (-1.0, 0.0, 101.0, 250.0):
            assert _rejected("humidity", value) is not None, value
            assert "impossible" in _rejected("humidity", value), value

    def test_normal_tent_readings_are_accepted(self):
        for value in (5.0, 45.0, 79.5, 81.9, 98.0, 100.0):
            assert _rejected("humidity", value) is None, value

    def test_boundary_five_percent_is_accepted(self):
        assert _rejected("humidity", 5.0) is None
        assert _rejected("humidity", 4.999) is not None

    def test_only_the_plausible_band_is_reported_as_plausible(self):
        assert "impossible" in _rejected("humidity", 0.0)
        assert "implausible" in _rejected("humidity", 2.0)


class TestTemperaturePlausibility:
    def test_impossible_domain_values(self):
        for value in (-5.0, 0.0, 40.1, 90.0):
            assert _rejected("temperature", value) is not None, value

    def test_implausible_but_in_domain_values(self):
        for value in (0.1, 1.0, 1.99):
            assert _rejected("temperature", value) is not None, value
            assert "implausible" in _rejected("temperature", value), value

    def test_normal_readings_are_accepted(self):
        for value in (2.0, 18.0, 23.15, 27.0, 40.0):
            assert _rejected("temperature", value) is None, value


class TestNonFiniteValuesAreRejected:
    def test_nan_never_passes_a_range_comparison(self):
        # NaN compares False against every bound - the old ``value <= 0 or
        # value > 100`` guard let it straight through into the average.
        assert _rejected("humidity", float("nan")) is not None
        assert _rejected("temperature", float("nan")) is not None

    def test_infinities_are_rejected(self):
        for value in (float("inf"), float("-inf")):
            assert _rejected("humidity", value) is not None, value
            assert _rejected("temperature", value) is not None, value

    def test_the_reason_mentions_the_non_finite_value(self):
        assert "non-finite" in _rejected("humidity", float("nan"))


class TestUnknownSensorTypeIsNotGuarded:
    def test_unknown_type_returns_none(self):
        # A type we do not know about must not be silently dropped - the helper
        # only guards the two climate types it has ranges for.
        assert climate_reading_reject_reason("energy", "sensor.x", 2.0) is None
        assert climate_reading_reject_reason("", "sensor.x", 2.0) is None


class TestGroupOutlierDetection:
    def test_the_bug_group_has_its_foreign_metric_dropped(self):
        kept, dropped = drop_climate_outliers(BUG_HUMIDITIES, "humidity")
        assert [r["entity_id"] for r in dropped] == ["sensor.heatertent_signal_level"]
        assert [r["value"] for r in kept] == [79.5, 81.9]

    def test_a_wrong_but_in_range_value_is_dropped(self):
        readings = [
            {"entity_id": "sensor.a", "value": 79.5},
            {"entity_id": "sensor.b", "value": 81.9},
            {"entity_id": "sensor.stuck", "value": 45.0},
        ]
        kept, dropped = drop_climate_outliers(readings, "humidity")
        assert [r["entity_id"] for r in dropped] == ["sensor.stuck"]
        assert len(kept) == 2

    def test_a_consistent_group_is_untouched(self):
        readings = [
            {"entity_id": "sensor.a", "value": 79.5},
            {"entity_id": "sensor.b", "value": 81.9},
            {"entity_id": "sensor.c", "value": 80.4},
        ]
        kept, dropped = drop_climate_outliers(readings, "humidity")
        assert dropped == []
        assert kept == readings

    def test_a_wide_but_plausible_spread_is_kept(self):
        # Two sensors in different corners of a large tent - 15 percentage
        # points apart is normal and must never be dropped.
        readings = [
            {"entity_id": "sensor.a", "value": 65.0},
            {"entity_id": "sensor.b", "value": 80.0},
            {"entity_id": "sensor.c", "value": 90.0},
        ]
        kept, dropped = drop_climate_outliers(readings, "humidity")
        assert dropped == []
        assert kept == readings

    def test_two_readings_are_never_judged(self):
        # Without a third opinion the median is ambiguous and a lone correct
        # sensor must not be dropped by a wrong one.
        readings = [
            {"entity_id": "sensor.a", "value": 79.5},
            {"entity_id": "sensor.stuck", "value": 2.0},
        ]
        kept, dropped = drop_climate_outliers(readings, "humidity")
        assert dropped == []
        assert kept == readings

    def test_a_single_reading_is_never_judged(self):
        readings = [{"entity_id": "sensor.a", "value": 2.0}]
        assert drop_climate_outliers(readings, "humidity") == (readings, [])

    def test_temperature_outlier_is_dropped(self):
        readings = [
            {"entity_id": "sensor.a", "value": 23.0},
            {"entity_id": "sensor.b", "value": 23.4},
            {"entity_id": "sensor.broken", "value": 39.0},
        ]
        kept, dropped = drop_climate_outliers(readings, "temperature")
        assert [r["entity_id"] for r in dropped] == ["sensor.broken"]

    def test_unknown_type_is_not_filtered(self):
        readings = [{"entity_id": "sensor.a", "value": 1.0}]
        assert drop_climate_outliers(readings, "energy") == (readings, [])


class TestBothLayersTogether:
    def test_the_bug_no_longer_reaches_the_average(self):
        # Mirrors the production flow: layer 1 runs per reading, layer 2 then
        # sees only what is left. The 2.0 reading never gets near the average.
        survivors = [
            r
            for r in BUG_HUMIDITIES
            if climate_reading_reject_reason("humidity", r["entity_id"], r["value"])
            is None
        ]
        assert [r["entity_id"] for r in survivors] == [
            "sensor.sensor6tent_humidity",
            "sensor.sensor8tent_humidity",
        ]
        kept, _ = drop_climate_outliers(survivors, "humidity")
        assert calculate_avg_value(kept) == 80.7

    def test_the_polluted_average_was_the_problem(self):
        # Sanity check of the original damage: averaging the raw trio.
        assert calculate_avg_value(BUG_HUMIDITIES) == 54.47


class TestManagerWiring:
    def _manager(self):
        manager = OGBVPDManager.__new__(OGBVPDManager)
        manager.room = "TentRoom"
        manager.notified = []
        return manager

    async def _notify(self, entity_id, sensor_type, value):
        self.manager.notified.append((entity_id, sensor_type, value))

    @pytest.mark.asyncio
    async def test_reject_climate_outliers_filters_and_alerts(self):
        self.manager = self._manager()
        self.manager._notify_sensor_failure = self._notify

        kept = await self.manager._reject_climate_outliers(BUG_HUMIDITIES, "humidity")

        assert [r["value"] for r in kept] == [79.5, 81.9]
        assert self.manager.notified == [
            ("sensor.heatertent_signal_level", "humidity", 2.0)
        ]

    @pytest.mark.asyncio
    async def test_reject_climate_outliers_keeps_a_small_group(self):
        self.manager = self._manager()
        self.manager._notify_sensor_failure = self._notify

        readings = BUG_HUMIDITIES[:2]
        kept = await self.manager._reject_climate_outliers(readings, "humidity")

        assert kept == readings
        assert self.manager.notified == []

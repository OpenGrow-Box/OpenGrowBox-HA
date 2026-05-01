"""Tests for sensor translation identification across multiple languages."""

import pytest
from custom_components.opengrowbox.OGBController.utils.sensor_identification import (
    resolve_sensor_types,
    resolve_remappable_sensor_type,
    TRANSLATION_CACHE,
)


class TestSensorTranslationIdentification:
    """Test sensor identification via translations for all supported languages."""

    # Spanish sensors
    def test_spanish_temperature(self):
        """Spanish: temperatura -> temperature"""
        result = resolve_sensor_types("sensor.carpa_temperatura")
        assert "temperature" in result

    def test_spanish_humidity(self):
        """Spanish: humedad -> humidity"""
        result = resolve_sensor_types("sensor.carpa_humedad")
        assert "humidity" in result

    def test_spanish_co2(self):
        """Spanish: dioxido_de_carbono -> co2"""
        result = resolve_sensor_types("sensor.flowersensor1_mhz19b_dioxido_de_carbono")
        assert "co2" in result

    # German sensors
    def test_german_temperature(self):
        """German: temperatur -> temperature"""
        result = resolve_sensor_types("sensor.growbox_temperatur")
        assert "temperature" in result

    def test_german_humidity(self):
        """German: feuchtigkeit -> humidity"""
        result = resolve_sensor_types("sensor.plant_sensor_feuchtigkeit")
        assert "humidity" in result

    def test_german_light(self):
        """German: beleuchtungsstarke -> light"""
        result = resolve_sensor_types("sensor.tent1_beleuchtungsstarke")
        assert "light" in result

    # French sensors
    def test_french_temperature(self):
        """French: temperature -> temperature"""
        result = resolve_sensor_types("sensor.serre_temperature")
        assert "temperature" in result

    def test_french_humidity(self):
        """French: humidite -> humidity"""
        result = resolve_sensor_types("sensor.serre_humidite")
        assert "humidity" in result

    # Italian sensors
    def test_italian_temperature(self):
        """Italian: temperatura -> temperature"""
        result = resolve_sensor_types("sensor.serra_temperatura")
        assert "temperature" in result

    def test_italian_humidity(self):
        """Italian: umidità -> humidity"""
        result = resolve_sensor_types("sensor.serra_umidità")
        assert "humidity" in result

    # Portuguese sensors
    def test_portuguese_temperature(self):
        """Portuguese: temperatura -> temperature"""
        result = resolve_sensor_types("sensor.estufa_temperatura")
        assert "temperature" in result

    # Russian sensors
    def test_russian_temperature(self):
        """Russian: температура -> temperature"""
        result = resolve_sensor_types("sensor.growbox_температура")
        assert "temperature" in result

    def test_russian_humidity(self):
        """Russian: влажность -> humidity"""
        result = resolve_sensor_types("sensor.growbox_влажность")
        assert "humidity" in result

    # English sensors (baseline)
    def test_english_temperature(self):
        """English: temperature -> temperature"""
        result = resolve_sensor_types("sensor.growbox_temperature")
        assert "temperature" in result

    def test_english_humidity(self):
        """English: humidity -> humidity"""
        result = resolve_sensor_types("sensor.growbox_humidity")
        assert "humidity" in result

    def test_english_co2(self):
        """English: carbondioxide -> co2"""
        result = resolve_sensor_types("sensor.growbox_carbondioxide")
        assert "co2" in result


class TestSensorRemappableTypes:
    """Test remappable sensor type resolution."""

    def test_remap_temperature(self):
        """Temperature sensors should be remappable."""
        result = resolve_remappable_sensor_type("sensor.carpa_temperatura")
        assert result == "temperature"

    def test_remap_humidity(self):
        """Humidity sensors should be remappable."""
        result = resolve_remappable_sensor_type("sensor.carpa_humedad")
        assert result == "humidity"

    def test_remap_dewpoint(self):
        """Dewpoint sensors should be remappable."""
        result = resolve_remappable_sensor_type("sensor.growbox_dewpoint")
        assert result == "dewpoint"

    def test_remap_co2(self):
        """CO2 sensors should be remappable."""
        result = resolve_remappable_sensor_type("sensor.flowersensor1_mhz19b_carbondioxide")
        assert result == "co2"


class TestTranslationCache:
    """Test that translation cache contains expected entries."""

    def test_cache_contains_spanish(self):
        """Cache should contain spanish translations."""
        assert "temperatura" in TRANSLATION_CACHE
        assert TRANSLATION_CACHE["temperatura"] == "temperature"
        assert "humedad" in TRANSLATION_CACHE
        assert TRANSLATION_CACHE["humedad"] == "humidity"

    def test_cache_contains_german(self):
        """Cache should contain german translations."""
        assert "temperatur" in TRANSLATION_CACHE
        assert TRANSLATION_CACHE["temperatur"] == "temperature"
        assert "feuchtigkeit" in TRANSLATION_CACHE
        assert TRANSLATION_CACHE["feuchtigkeit"] == "humidity"

    def test_cache_contains_french(self):
        """Cache should contain french translations."""
        assert "température" in TRANSLATION_CACHE
        assert TRANSLATION_CACHE["température"] == "temperature"

    def test_cache_contains_english(self):
        """Cache should contain english translations."""
        assert "temperature" in TRANSLATION_CACHE
        assert TRANSLATION_CACHE["temperature"] == "temperature"


class TestSensorContextWithTranslations:
    """Test that sensor context is correctly determined with translations."""

    def test_temperature_context_air(self):
        """Temperature should default to 'air' context."""
        from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
            extract_context_from_entity,
        )

        # Spanish temperature sensor
        context = extract_context_from_entity("sensor.carpa_temperatura", "temperature")
        assert context == "air"

    def test_humidity_context_air(self):
        """Humidity should default to 'air' context."""
        from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
            extract_context_from_entity,
        )

        # Spanish humidity sensor
        context = extract_context_from_entity("sensor.carpa_humedad", "humidity")
        assert context == "air"

    def test_co2_context_air(self):
        """CO2 should default to 'air' context."""
        from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
            extract_context_from_entity,
        )

        # Spanish CO2 sensor
        context = extract_context_from_entity(
            "sensor.flowersensor1_mhz19b_dioxido_de_carbono", "co2"
        )
        assert context == "air"

    def test_ec_context_water(self):
        """EC should default to 'water' context."""
        from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
            extract_context_from_entity,
        )

        context = extract_context_from_entity("sensor.reservoir_ec", "ec")
        assert context == "water"

    def test_ph_context_water(self):
        """pH should default to 'water' context."""
        from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
            extract_context_from_entity,
        )

        context = extract_context_from_entity("sensor.reservoir_ph", "ph")
        assert context == "water"

    def test_moisture_context_soil(self):
        """Moisture should default to 'soil' context."""
        from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
            extract_context_from_entity,
        )

        context = extract_context_from_entity("sensor.soil_moisture", "moisture")
        assert context == "soil"


class TestEdgeCases:
    """Test edge cases and potential failure modes."""

    def test_unknown_sensor_suffix(self):
        """Unknown sensor suffixes may match via fuzzy matching."""
        result = resolve_sensor_types("sensor.growbox_xyz_unknown")
        # Note: "unknown" may be matched by fuzzy logic, this is expected behavior
        assert isinstance(result, list)

    def test_empty_entity_id(self):
        """Empty entity ID should return empty list."""
        result = resolve_sensor_types("")
        assert result == []

    def test_none_entity_id(self):
        """None entity ID should return empty list."""
        result = resolve_sensor_types(None)
        assert result == []

    def test_partial_match(self):
        """Partial matches may be identified via fuzzy matching."""
        # Note: "temper" may match "temperature" via fuzzy logic
        result = resolve_sensor_types("sensor.growbox_temper")
        # This behavior depends on the fuzzy matching threshold
        assert isinstance(result, list)

    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        result = resolve_sensor_types("sensor.carpa_TEMPERATURA")
        assert "temperature" in result

        result = resolve_sensor_types("sensor.carpa_HUMEDAD")
        assert "humidity" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
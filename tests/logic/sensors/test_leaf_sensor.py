"""
Test leaf sensor detection and VPD calculation.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from tests.logic.helpers import FakeDataStore
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import extract_context_from_entity
from custom_components.opengrowbox.OGBController.utils.calcs import calculate_current_vpd, calculate_current_vpd_with_leaf_temp


class TestLeafSensorDetection:
    """Test leaf sensor context detection."""
    
    def test_entity_id_leaf_keyword(self):
        """Test that entity IDs with 'leaf' and 'temp' are detected as leaf context."""
        assert extract_context_from_entity("sensor.growbox_leaf_temperature") == "leaf"
        assert extract_context_from_entity("sensor.leaf_temp_1") == "leaf"
        assert extract_context_from_entity("sensor.blatt_temperature") == "leaf"
    
    def test_entity_id_no_leaf(self):
        """Test that non-leaf temperature sensors default to air."""
        assert extract_context_from_entity("sensor.growbox_temperature") == "air"
        # Unknown sensors default to "other"
        assert extract_context_from_entity("sensor.room_temp") == "other"
    
    def test_entity_id_water_reservoir_priority(self):
        """Test that water reservoir still has priority over leaf."""
        assert extract_context_from_entity("sensor.growbox_waterreservoir_temperature") == "water"


class TestVPDCalculationWithLeafSensor:
    """Test VPD calculation with direct leaf temperature."""
    
    def test_vpd_with_leaf_sensor(self):
        """Test VPD calculation with direct leaf temperature."""
        # Air: 25°C, Hum: 60%, Leaf: 23°C
        vpd = calculate_current_vpd_with_leaf_temp(25.0, 60.0, 23.0)
        assert vpd is not None
        assert vpd > 0
        
    def test_vpd_with_offset_vs_direct(self):
        """Test that leaf sensor gives same result via offset or direct."""
        air_temp = 25.0
        hum = 60.0
        leaf_temp = 23.0
        # New calculation: leaf_temp = air_temp + offset
        # So: offset = leaf_temp - air_temp = 23.0 - 25.0 = -2.0
        offset = leaf_temp - air_temp  # -2.0
        
        vpd_direct = calculate_current_vpd_with_leaf_temp(air_temp, hum, leaf_temp)
        vpd_offset = calculate_current_vpd(air_temp, hum, offset)
        
        # Should be exactly equal
        assert abs(vpd_direct - vpd_offset) < 0.01
    
    def test_vpd_invalid_leaf_temp(self):
        """Test VPD with invalid leaf temperature returns None."""
        vpd = calculate_current_vpd_with_leaf_temp(25.0, 60.0, None)
        assert vpd is None
        
    def test_vpd_cooler_leaf(self):
        """Test VPD when leaf is cooler than air."""
        # Cooler leaf = lower VPD
        vpd_cool = calculate_current_vpd_with_leaf_temp(25.0, 60.0, 23.0)
        vpd_warm = calculate_current_vpd_with_leaf_temp(25.0, 60.0, 25.0)
        
        assert vpd_cool < vpd_warm


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

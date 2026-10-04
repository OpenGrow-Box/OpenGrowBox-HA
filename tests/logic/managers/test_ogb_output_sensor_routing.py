import pytest

from custom_components.opengrowbox.OGBController.utils.sensor_identification import (
    is_ogb_output_sensor,
    should_route_to_config_manager,
)


class TestOgBOutputSensors:
    """OGB writes these itself on every cycle - they are never config inputs."""

    @pytest.mark.parametrize(
        "entity_name",
        [
            "sensor.ogb_ambienttemperature_dev_room",
            "sensor.ogb_ambienthumidity_dev_room",
            "sensor.ogb_ambientdewpoint_dev_room",
            "sensor.ogb_outsitetemperature_dev_room",
            "sensor.ogb_outsitehumidity_dev_room",
            "sensor.ogb_currentvpd_dev_room",
            "sensor.ogb_avgtemperature_dev_room",
            "sensor.ogb_avgdewpoint_ambient",
            "sensor.ogb_current_vpd_target_dev_room",
            "SENSOR.OGB_AMBIENTTEMPERATURE_DEV_ROOM",
        ],
    )
    def test_detected_as_output(self, entity_name):
        assert is_ogb_output_sensor(entity_name)

    @pytest.mark.parametrize(
        "entity_name",
        [
            "select.ogb_cropsteering_mode_dev_room",
            "number.ogb_feed_nutrient_a_dev_room",
            "number.ogb_feed_reservoir_a_ambient",
            "switch.ogb_light_uv_enabled_dev_room",
            "date.ogb_bloomswitchdate_dev_room",
            "text.ogb_strainname_veggient",
            "sensor.dev_room_devmainlight_power",
        ],
    )
    def test_config_entities_are_not_outputs(self, entity_name):
        assert not is_ogb_output_sensor(entity_name)

    @pytest.mark.parametrize("entity_name", [None, "", "   "])
    def test_empty_input_is_safe(self, entity_name):
        assert not is_ogb_output_sensor(entity_name)
        assert not should_route_to_config_manager(entity_name)


class TestConfigRouting:
    @pytest.mark.parametrize(
        "entity_name",
        [
            "select.ogb_cropsteering_mode_dev_room",
            "number.ogb_feed_nutrient_a_dev_room",
            "switch.ogb_light_uv_enabled_dev_room",
            "date.ogb_bloomswitchdate_dev_room",
        ],
    )
    def test_config_entities_are_routed(self, entity_name):
        assert should_route_to_config_manager(entity_name)

    @pytest.mark.parametrize(
        "entity_name",
        [
            "sensor.ogb_ambienttemperature_dev_room",
            "sensor.ogb_ambienthumidity_dev_room",
            "sensor.ogb_ambientdewpoint_dev_room",
        ],
    )
    def test_output_sensors_are_never_routed(self, entity_name):
        """This is what caused the endless 'Unhandled entity update' warnings."""
        assert not should_route_to_config_manager(entity_name)

    @pytest.mark.parametrize(
        "entity_name",
        ["switch.devmainlight", "sensor.dev_room_devmainlight_power"],
    )
    def test_foreign_entities_are_not_routed(self, entity_name):
        assert not should_route_to_config_manager(entity_name)
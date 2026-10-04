from custom_components.opengrowbox.OGBController.RegistryListener import (
    derive_device_name,
)


class TestRoomPrefixedEntities:
    """Room names containing an underscore must not become the device name.

    Cutting the object id at the first underscore yielded the first word of
    the *room* name, collapsing every entity of the room into one switch-less
    phantom device.
    """

    def test_helper_sensor_maps_back_to_its_device(self):
        assert (
            derive_device_name("sensor.dev_room_devmainlight_power", "dev_room")
            == "devmainlight"
        )
        assert (
            derive_device_name("sensor.dev_room_devuvlight_power", "dev_room")
            == "devuvlight"
        )

    def test_switch_of_same_device_derives_same_name(self):
        """Power sensor and switch must land in the same device group."""
        assert (
            derive_device_name("switch.dev_room_devmainlight", "dev_room")
            == "devmainlight"
        )
        assert (
            derive_device_name("light.dev_room_devuvlight", "dev_room")
            == "devuvlight"
        )

    def test_all_helper_sensor_suffixes_are_stripped(self):
        for suffix in ("power", "energy", "voltage", "current", "temperature"):
            entity_id = f"sensor.dev_room_devfan_{suffix}"
            assert derive_device_name(entity_id, "dev_room") == "devfan"

    def test_room_matching_is_case_insensitive(self):
        assert (
            derive_device_name("sensor.DEV_ROOM_devmainlight_power", "dev_room")
            == "devmainlight"
        )

    def test_ambient_room_prefix_is_unwrapped(self):
        assert derive_device_name("switch.ambient_intake", "ambient") == "intake"
        assert derive_device_name("switch.ambient_exhaust", "ambient") == "exhaust"


class TestLegacyBehaviourPreserved:
    """Entities without a room prefix keep their historic device name."""

    def test_simple_name_is_unchanged(self):
        assert derive_device_name("switch.devmainlight", "dev_room") == "devmainlight"

    def test_legacy_first_token_rule_is_kept(self):
        assert derive_device_name("switch.my_light", "ambient") == "my"

    def test_unrelated_prefix_is_not_stripped(self):
        """Only a real room prefix may be removed, never an arbitrary one."""
        assert derive_device_name("sensor.dev_room_power", "dev_room") == "power"

    def test_entity_without_domain_is_unknown(self):
        assert derive_device_name("noentityid", "dev_room") == "Unknown"

    def test_empty_object_id_is_unknown(self):
        assert derive_device_name("switch.", "dev_room") == "Unknown"

    def test_missing_room_name_keeps_legacy_derivation(self):
        assert derive_device_name("switch.my_light", "") == "my"
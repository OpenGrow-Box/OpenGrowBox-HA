from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import (
    CAP_MAPPING,
    DEFAULT_DEVICE_COOLDOWNS,
    DEVICE_TYPE_MAPPING,
    RELEVANT_PREFIXES,
)


def test_device_type_mapping_covers_core_device_families():
    expected_types = {
        "Sensor",
        "Exhaust",
        "Intake",
        "Window",
        "Ventilation",
        "Dehumidifier",
        "Humidifier",
        "Heater",
        "Cooler",
        "Climate",
        "LightFarRed",
        "LightUV",
        "LightBlue",
        "LightRed",
        "Light",
        "CO2",
        "Camera",
        "Pump",
        "Switch",
        "Fridge",
        "Door",
        "ModbusDevice",
        "ModbusSensor",
        "FridgeGrow",
    }
    assert expected_types.issubset(set(DEVICE_TYPE_MAPPING.keys()))


def test_device_type_mapping_keywords_exist_for_all_types():
    for device_type, keywords in DEVICE_TYPE_MAPPING.items():
        assert isinstance(keywords, list), f"{device_type} keywords must be list"
        assert keywords, f"{device_type} keywords must not be empty"


def test_cap_mapping_includes_all_control_caps():
    expected_caps = {
        "canHeat",
        "canCool",
        "canClimate",
        "canHumidify",
        "canDehumidify",
        "canVentilate",
        "canWindow",
        "canDoor",
        "canExhaust",
        "canIntake",
        "canLight",
        "canCO2",
        "canPump",
        "canWatch",
    }
    assert expected_caps.issubset(set(CAP_MAPPING.keys()))


def test_default_cooldowns_defined_for_runtime_caps():
    required_cooldowns = {
        "canHumidify",
        "canDehumidify",
        "canHeat",
        "canCool",
        "canExhaust",
        "canIntake",
        "canVentilate",
        "canWindow",
        "canDoor",
        "canLight",
        "canCO2",
        "canClimate",
    }
    assert required_cooldowns.issubset(set(DEFAULT_DEVICE_COOLDOWNS.keys()))
    for cap in required_cooldowns:
        assert DEFAULT_DEVICE_COOLDOWNS[cap] >= 1


def test_relevant_prefixes_include_all_main_entity_domains():
    expected_prefixes = {
        "number.",
        "select.",
        "switch.",
        "light.",
        "time.",
        "date.",
        "text.",
        "humidifier.",
        "climate.",
        "fan.",
        "camera.",
        "cover.",
        "binary_sensor.",
    }
    assert expected_prefixes.issubset(set(RELEVANT_PREFIXES))

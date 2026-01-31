## INT DATA
RELEVANT_PREFIXES = (
    "number.",
    "select.",
    "switch.",
    "light.",
    "time.",
    "date.",
    "text.",
    "humidifier.",
    "fan.",
    "camera.",
)
RELEVANT_KEYWORDS = (
    "_temperature",
    "_humidity",
    "_dewpoint",
    "_duty",
    "_voltage",
    "_co2",
    "_carbondioxide",
    "_lumen",
    "_lux",
    "_illuminance",
    "_intensity",
    "_moisture",
    "_ec",
    "_ph",
    "_conductivity",
    "_camera",
    "soil",
    "water",
    "medium,",
)
RELEVANT_TYPES = {
    "temperature": "Temperature entity found",
    "humidity": "Humidity entity found",
    "dewpoint": "Dewpoint entity found",
}

INVALID_VALUES = [None, "unknown", "unavailable", "Unbekannt"]

# Device-Type Defintion and Mapping List
# Note: Order matters! More specific types should come before generic ones.
# Special lights (light_fr, light_uv, etc.) must be checked BEFORE generic "light"
DEVICE_TYPE_MAPPING = {
    "Sensor": [
        "ogb",
        "sensor",
        "temperature",
        "temp",
        "humidity",
        "moisture",
        "dewpoint",
        "illuminance",
        "ppfd",
        "dli",
        "h5179",
        "govee",
        "ens160",
        "tasmota",
    ],
    "Exhaust": ["exhaust", "abluft"],
    "Intake": ["intake", "zuluft"],
    "Ventilation": ["vent", "vents", "venti", "ventilation", "inlet"],
    "Dehumidifier": ["dehumidifier", "entfeuchter"],
    "Humidifier": ["humidifier", "befeuchter"],
    "Heater": ["heater", "heizung"],
    "Cooler": ["cooler", "kuehler"],
    "Climate": ["climate", "klima"],
    # Special light types - must be checked BEFORE generic Light
    "LightFarRed": ["light_fr", "light_farred", "farred", "far_red", "farredlight", "far-red-light", "lightfarred"],
    "LightUV": ["light_uv", "light_uvb", "light_uva", "uvlight", "uvlight", "uv-light", "lightuv"],
    "LightBlue": ["light_blue", "blue_led", "bluelight", "bluelight", "blue-light", "lightblue"],
    "LightRed": ["light_red", "red_led", "redlight", "redlight", "red-light", "lightred"],
    # Generic light - checked last
    "Light": ["light", "lamp", "led"],
    "CO2": ["co2", "carbon"],
    "Camera": ["camera", "kamera", "cam", "video", "ipcam", "webcam", "surveillance"],
    "Pump": ["pump", "dripper", "feedsystem", "tank"],
    "Switch": ["generic", "switch"],
    "Fridge": ["fridge", "kuehlschrank"],
    # Modbus devices
    "ModbusDevice": ["modbus", "modbus_device", "modbus_rtu", "modbus_tcp"],
    "ModbusSensor": ["modbus_sensor", "modbus_temp", "modbus_humidity"],
    # FridgeGrow / Plantalytix devices - identified by label combination
    # Device must have "fridgegrow" or "plantalytix" label + output type label
    "FridgeGrow": ["fridgegrow", "plantalytix"],
}

# OGB ROOM DEVICE-CAPS Defintion and Mapping List
CAP_MAPPING = {
    "canHeat": ["heater"],
    "canCool": ["cooler"],
    "canClimate": ["climate"],
    "canHumidify": ["humidifier"],
    "canDehumidify": ["dehumidifier"],
    "canVentilate": ["ventilation"],
    "canExhaust": ["exhaust"],
    "canIntake": ["intake"],
    "canLight": ["light"],
    "canCO2": ["co2"],
    "canPump": ["pump"],
}

# Sensor-Kontexte definieren
SENSOR_CONTEXTS = {
    "air": {
        "name": "Air/Ambient",
        "description": "Luftbasierte Sensoren (Umgebung, Growbox)",
        "icon": "mdi:weather-partly-cloudy",
        "suffixes": ["", "air", "luft", "umgebung"],
    },
    "leaf": {
        "name": "Leaf/Blatt",
        "description": "Blattbasierte Sensoren (Plant, Growbox)",
        "icon": "mdi:weather-partly-cloudy",
        "suffixes": ["", "leaf", "blatt"],
    },
    "water": {
        "name": "Water/Hydro",
        "description": "Wasserbasierte Sensoren (Hydroponik, Reservoir)",
        "icon": "mdi:water",
        "suffixes": ["water", "hydro", "reservoir", "wasser", "tank"],
    },
    "soil": {
        "name": "Soil/Substrate",
        "description": "Bodenbasierte Sensoren (Erde, Substrat)",
        "icon": "mdi:flower",
        "suffixes": [
            "soil",
            "substrate",
            "ground",
            "boden",
            "erde",
            "substrat",
            "coco",
            "rockwoll",
            "medium",
        ],
    },
}

# Sensor-Typ Definitionen MIT Kontext-Unterstützung
SENSOR_TYPES = {
    "temperature": {
        "unit": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "precision": 1,
        "contexts": {
            "air": {
                "min_value": -10,
                "max_value": 50,
                "optimal_min": 18,
                "optimal_max": 28,
                "name": "Air Temperature",
            },
            "water": {
                "min_value": 0,
                "max_value": 40,
                "optimal_min": 18,
                "optimal_max": 22,
                "name": "Water Temperature",
            },
            "soil": {
                "min_value": 0,
                "max_value": 40,
                "optimal_min": 18,
                "optimal_max": 25,
                "name": "Soil Temperature",
            },
            "leaf": {
                "min_value": -5,
                "max_value": 40,
                "optimal_min": 0,
                "optimal_max": 2,
                "name": "Soil Temperature",
            },
        },
    },
    "humidity": {
        "unit": "%",
        "device_class": "humidity",
        "state_class": "measurement",
        "precision": 1,
        "contexts": {
            "air": {
                "min_value": 0,
                "max_value": 100,
                "optimal_min": 40,
                "optimal_max": 70,
                "name": "Air Humidity",
            }
        },
    },
    "ec": {
        "unit": "µS/cm",
        "device_class": "voltage",
        "state_class": "measurement",
        "precision": 0,
        "contexts": {
            "water": {
                "min_value": 0,
                "max_value": 5000,
                "optimal_min": 800,
                "optimal_max": 1800,
                "name": "Water EC",
            },
            "soil": {
                "min_value": 0,
                "max_value": 10000,
                "optimal_min": 1000,
                "optimal_max": 2500,
                "name": "Soil EC",
            },
        },
    },
    "ph": {
        "unit": "pH",
        "device_class": None,
        "state_class": "measurement",
        "precision": 2,
        "contexts": {
            "water": {
                "min_value": 4.0,
                "max_value": 9.0,
                "optimal_min": 5.5,
                "optimal_max": 6.5,
                "name": "Water pH",
            },
            "soil": {
                "min_value": 4.0,
                "max_value": 8.5,
                "optimal_min": 6.0,
                "optimal_max": 7.0,
                "name": "Soil pH",
            },
        },
    },
    "moisture": {
        "unit": "%",
        "device_class": "moisture",
        "state_class": "measurement",
        "precision": 1,
        "contexts": {
            "soil": {
                "min_value": 0,
                "max_value": 100,
                "optimal_min": 40,
                "optimal_max": 70,
                "name": "Soil Moisture",
            }
        },
    },
    "tds": {
        "unit": "ppm",
        "device_class": None,
        "state_class": "measurement",
        "precision": 0,
        "contexts": {
            "water": {
                "min_value": 0,
                "max_value": 3000,
                "optimal_min": 400,
                "optimal_max": 1200,
                "name": "Water TDS",
            }
        },
    },
    "light": {
        "unit": "lux",
        "device_class": "illuminance",
        "state_class": "measurement",
        "precision": 0,
        "contexts": {
            "air": {
                "min_value": 0,
                "max_value": 100000,
                "optimal_min": 15000,
                "optimal_max": 50000,
                "name": "Light Intensity",
            }
        },
    },
    "co2": {
        "unit": "ppm",
        "device_class": "carbon_dioxide",
        "state_class": "measurement",
        "precision": 0,
        "contexts": {
            "air": {
                "min_value": 0,
                "max_value": 5000,
                "optimal_min": 800,
                "optimal_max": 1500,
                "name": "CO2 Level",
            }
        },
    },
    "dewpoint": {
        "unit": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "precision": 1,
        "contexts": {"air": {"min_value": -40, "max_value": 50, "name": "Dew Point"}},
    },
    "vpd": {
        "unit": "kPa",
        "device_class": "pressure",
        "state_class": "measurement",
        "precision": 2,
        "contexts": {
            "air": {
                "min_value": 0,
                "max_value": 5,
                "optimal_min": 0.8,
                "optimal_max": 1.2,
                "name": "VPD",
            }
        },
    },
    "battery": {
        "unit": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "precision": 0,
        "contexts": {"air": {"min_value": 0, "max_value": 100}},
    },
    "oxidation": {
        "unit": "mV",
        "device_class": "voltage",
        "state_class": "measurement",
        "precision": 1,
        "contexts": {
            "water": {
                "min_value": -2000,
                "max_value": 2000,
                "name": "Oxidation Potential",
            }
        },
    },
    "salinity": {
        "unit": "ppt",
        "device_class": "salinity",
        "state_class": "measurement",
        "precision": 2,
        "contexts": {
            "water": {"min_value": 0, "max_value": 70, "name": "Water Salinity"}
        },
    },
    "weight": {
        "unit": "kg",
        "device_class": "weight",
        "state_class": "measurement",
        "precision": 3,
        "contexts": {"soil": {"min_value": 0, "max_value": 15, "name": "Pot Weight"}},
    },
}


def extract_context_from_entity(entity_id, sensor_type=None):
    """
    Extrahiert den Kontext aus einer Entity-ID.

    Args:
        entity_id: Die Entity-ID des Sensors
        sensor_type: Optional - der bereits identifizierte Sensor-Typ

    Beispiele:
        sensor.growbox_water_temperature -> water
        sensor.growbox_soil_ec -> soil
        sensor.growbox_temperature -> air (default)
    """
    # Priorisierung: Manche Sensor-Typen haben IMMER einen festen Kontext
    FIXED_CONTEXT_SENSORS = {
        "light": "air",
        "co2": "air",
        "dewpoint": "air",
        "vpd": "air",
        "humidity": "air",  # Luftfeuchtigkeit ist immer air
        "moisture": "soil",  # Bodenfeuchtigkeit ist immer soil
        "weight": "soil",
        "tds": "water",
        "salinity": "water",
        "oxidation": "water",
        # Medium-specific sensors (EC/pH typically in soil/substrate)
        "ec": "soil",
        "ph": "soil",
        "conductivity": "soil",
        "temperature": "soil",  # Substrate temperature sensors
        "battery": "soil",  # Battery status for soil sensors
        "illuminance": "soil",  # Light sensors at plant level
    }

    # Falls Sensor-Typ bekannt und in Fixed-Liste: verwende den
    if sensor_type and sensor_type in FIXED_CONTEXT_SENSORS:
        return FIXED_CONTEXT_SENSORS[sensor_type]

    entity_lower = entity_id.lower()

    # Prüfe alle Kontext-Suffixe
    for context, config in SENSOR_CONTEXTS.items():
        for suffix in config["suffixes"]:
            if suffix and suffix in entity_lower:
                return context

    # Standard: air
    return "air"


# Hilfsfunktion: Kontext-spezifische Konfiguration holen
def get_sensor_config(sensor_type, context="air"):
    """
    Holt die Konfiguration für einen Sensor-Typ mit Kontext.

    Args:
        sensor_type: Der kanonische Sensor-Typ (z.B. "temperature")
        context: Der Kontext (z.B. "water", "soil", "air")

    Returns:
        dict: Vollständige Sensor-Konfiguration
    """
    if sensor_type not in SENSOR_TYPES:
        return None

    base_config = SENSOR_TYPES[sensor_type].copy()

    # Kontext-spezifische Werte holen
    if "contexts" in base_config and context in base_config["contexts"]:
        context_config = base_config["contexts"][context]
        base_config.update(context_config)

    # Fallback zu "air" wenn Kontext nicht unterstützt
    elif "contexts" in base_config and context not in base_config["contexts"]:
        available_contexts = list(base_config["contexts"].keys())
        if available_contexts:
            default_context = available_contexts[0]
            context_config = base_config["contexts"][default_context]
            base_config.update(context_config)

    # contexts-Dict entfernen (nicht mehr benötigt)
    base_config.pop("contexts", None)

    return base_config


## VPD Intervals
VPD_INTERVALS = {
    "LIVE": 0,
    "15SECONDS": 15,
    "30SECONDS": 30,
    "1MIN": 60,
    "2_5MIN": 150,
    "5MIN": 300,
    "10MIN": 600,
}

CS_PARAMETER_MAPPING = {
    "shot_intervall": ("ShotIntervall", "value"),
    "shot_duration": ("ShotDuration", "value"),
    "shot_sum": ("ShotSum", "value"),
    "ec_target": ("ECTarget", "value"),
    "ec_dryback": ("ECDryBack", "value"),
    "moisture_dryback": ("MoistureDryBack", "value"),
    "maxec": ("MaxEC", "value"),
    "minec": ("MinEC", "value"),
    "vwc_target": ("VWCTarget", "value"),
    "vwc_max": ("VWCMax", "value"),
    "vwc_min": ("VWCMin", "value"),
}

## Device CoolDowns
DEFAULT_DEVICE_COOLDOWNS = {
    "canHumidify": 3,  # Befeuchter braucht Zeit
    "canDehumidify": 4,  # Entfeuchter braucht noch mehr Zeit
    "canHeat": 1,  # Heizung reagiert relativ schnell
    "canCool": 2,  # Kühlung braucht etwas Zeit
    "canExhaust": 1,  # Abluft reagiert schnell
    "canIntake": 1,  # Zuluft reagiert schnell
    "canVentilate": 1,  # Ventilation reagiert schnell
    "canLight": 1,  # Licht reagiert sofort, aber VPD-Effekt braucht Zeit
    "canCO2": 2,  # CO2 braucht Zeit zur Verteilung
    "canClimate": 2,  # Klima-System braucht Zeit
}

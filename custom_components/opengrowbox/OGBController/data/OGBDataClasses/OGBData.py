from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LightStage:
    min: int
    max: int
    phase: str

    def to_dict(self):
        return {"min": self.min, "max": self.max, "phase": self.phase}


@dataclass
class OGBConf:
    hass: Any
    room: str = ""
    vpdDetermination: str = ""
    tentMode: str = ""
    plantStage: str = ""
    plantType: str = ""
    strainName: str = ""
    mainControl: str = "HomeAssistant"  # Default to HomeAssistant to enable device initialization
    growManagerActive: bool = False
    growAreaM2: int = 0.0
    notifyControl: str = "Disabled"
    DeviceLabelIdent: bool = False
    Hydro: Dict[str, Any] = field(
        default_factory=lambda: {
            "Active": False,
            "Cycle": False,
            "Mode": None,
            "Intervall": None,
            "Duration": None,
            "Retrieve": None,
            "R_Active": False,
            "R_Intervall": None,
            "R_Duration": None,
            "ph_current": 0,
            "ec_current": 0,
            "tds_current": 0,
            "oxi_current": 0,
            "sal_current": 0,
            "current_temp": 0,
            "min_temp": 0,
            "max_temp": 0,
            "FeedMode": None,
            ## TANK FEED
            "FeedModeActive": False,
            "PH_Target": False,
            "EC_Target": None,
            "Nut_A_ml": None,
            "Nut_B_ml": None,
            "Nut_C_ml": None,
            "Nut_W_ml": False,
            "Nut_X_ml": None,
            "Nut_Y_ml": None,
            "Nut_PH_ml": None,
            "ReservoirVolume": 0,
        }
    )
    CropSteering: Dict[str, Any] = field(
        default_factory=lambda: {
            "Mode": None,
            "Active": False,
            "ActiveMode": None,
            "CropPhase": None,
            "currentPhase": None,
            "phaseStartTime": None,
            "lastCheck": None,
            "shotCounter": 0,
            "lastIrrigationTime": None,
            # Aktuelle Werte (Numerisch!)
            "irrigation_target_ec": 0,
            "ec_current": 0,
            "vwc_current": 0,
            "weight_current": 0,
            "startNightMoisture": None,
            # Target/System Werte
            "ec_target": 0,
            "ec_min": 0,
            "ec_max": 0,
            "weight_max": 0,
            "weight_min": 0,
            "max_moisture": 0,
            "min_moisture": 0,
            # Calibration data - persisted across restarts
            "Calibration": {
                "p1": {"VWCMax": None, "VWCMin": None, "timestamp": None},
                "p2": {"VWCMax": None, "VWCMin": None, "timestamp": None},
                "p3": {"VWCMax": None, "VWCMin": None, "timestamp": None},
                "LastRun": None,
            },
            # Phase-spezifische Werte für p0–p3
            **{
                key: {phase: {"value": 0} for phase in ["p0", "p1", "p2", "p3"]}
                for key in [
                    "ShotIntervall",
                    "ShotDuration",
                    "ShotSum",
                    "ECTarget",
                    "ECDryBack",
                    "MoistureDryBack",
                    "MaxWeight",
                    "MinWeight",
                    "MaxEC",
                    "MinEC",
                    "VWCTarget", "VWCMax",
                    "VWCMin",
                ]
            },
        }
    )
    growMediums: List[Any] = field(default_factory=list)
    Light: Dict[str, Any] = field(
        default_factory=lambda: {
            "DLICurrent": 0,
            "DLITarget": 0,
            "PPFDCurrent": 0,
            "PPFDTarget": 0,
            "ledType": "fullspektrum_grow",
            "luxToPPFDFactor": 15.0,
            "plans": {
                "photoperiodic": {
                    "veg": {
                        "curve": [
                            {"week": 1, "PPFDTarget": 200, "DLITarget": 12},
                            {"week": 2, "PPFDTarget": 300, "DLITarget": 20},
                            {"week": 3, "PPFDTarget": 350, "DLITarget": 25},
                            {"week": 4, "PPFDTarget": 400, "DLITarget": 30},
                        ],
                    },
                    "flower": {
                        "curve": [
                            {"week": 1, "PPFDTarget": 450, "DLITarget": 25},
                            {"week": 2, "PPFDTarget": 600, "DLITarget": 35},
                            {"week": 3, "PPFDTarget": 700, "DLITarget": 40},
                            {"week": 4, "PPFDTarget": 800, "DLITarget": 45},
                            {"week": 5, "PPFDTarget": 850, "DLITarget": 48},
                            {"week": 6, "PPFDTarget": 900, "DLITarget": 50},
                            {"week": 7, "PPFDTarget": 900, "DLITarget": 50},
                            {"week": 8, "PPFDTarget": 900, "DLITarget": 50},
                        ],
                    },
                },
                "auto": {
                    "Seedling": {
                        "curve": [
                            {"week": 1, "PPFDTarget": 200, "DLITarget": 12},
                            {"week": 2, "PPFDTarget": 250, "DLITarget": 16},
                        ],
                    },
                    "veg": {
                        "curve": [
                            {"week": 1, "PPFDTarget": 200, "DLITarget": 20},
                            {"week": 2, "PPFDTarget": 300, "DLITarget": 30},
                            {"week": 3, "PPFDTarget": 700, "DLITarget": 40},
                            {"week": 4, "PPFDTarget": 800, "DLITarget": 45},
                        ],
                    },
                    "flower": {
                        "curve": [
                            {"week": 1, "PPFDTarget": 500, "DLITarget": 45},
                        ],
                    },
                    "maturing": {
                        "curve": [
                            {"week": 1, "PPFDTarget": 700, "DLITarget": 40},
                            {"week": 2, "PPFDTarget": 550, "DLITarget": 36},
                            {"week": 3, "PPFDTarget": 400, "DLITarget": 32},
                        ],
                    },
                },
            },
        }
    )
    specialLights: Dict[str, Any] = field(
        default_factory=lambda: {
            # Far Red light settings (start/end of day timing)
            "farRed": {
                "enabled": False,
                "startDurationMinutes": 15,   # Duration at START of light cycle
                "endDurationMinutes": 15,     # Duration at END of light cycle
                "intensity": 100,             # Intensity percentage (if dimmable)
            },
            # UV light settings (mid-day timing)
            "uv": {
                "enabled": False,
                "delayAfterStartMinutes": 160,  # Wait after lights on before UV starts
                "stopBeforeEndMinutes": 160,    # Stop before lights off
                "maxDurationHours": 6,          # Maximum UV exposure per day
                "intensity": 100,               # Intensity percentage (if dimmable)
            },
            # Spectrum lights (blue/red intensity curves)
            "spectrum": {
                "blue": {
                    "enabled": False,
                    "morningBoostPercent": 100,   # Higher blue in morning
                    "eveningReducePercent": 50,   # Lower blue in evening
                    "transitionMinutes": 60,      # Transition duration
                },
                "red": {
                    "enabled": False,
                    "morningReducePercent": 70,   # Lower red in morning
                    "eveningBoostPercent": 100,   # Higher red in evening
                    "transitionMinutes": 60,      # Transition duration
                },
            },
        }
    )
    devices: List[Any] = field(default_factory=list)
    ownDeviceList: List[Any] = field(default_factory=list)
    capabilities: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "canHeat": {"state": False, "count": 0, "devEntities": []},
            "canCool": {"state": False, "count": 0, "devEntities": []},
            "canHumidify": {"state": False, "count": 0, "devEntities": []},
            "canClimate": {"state": False, "count": 0, "devEntities": []},
            "canDehumidify": {"state": False, "count": 0, "devEntities": []},
            "canVentilate": {"state": False, "count": 0, "devEntities": []},
            "canExhaust": {"state": False, "count": 0, "devEntities": []},
            "canIntake": {"state": False, "count": 0, "devEntities": []},
            "canLight": {"state": False, "count": 0, "devEntities": []},
            "canPump": {"state": False, "count": 0, "devEntities": []},
            "canCO2": {"state": False, "count": 0, "devEntities": []},
        }
    )
    previousActions: List[Any] = field(default_factory=list)
    tentData: Dict[str, Optional[Any]] = field(
        default_factory=lambda: {
            "leafTempOffset": None,
            "temperature": None,
            "humidity": None,
            "dewpoint": None,
            "maxTemp": None,
            "minTemp": None,
            "maxHumidity": None,
            "minHumidity": None,
            "co2Level": None,
            "DLI": None,
            "PPFD": None,
            "AmbientTemp": None,
            "AmbientHum": None,
            "OutsiteTemp": None,
            "OutsiteHum": None,
        }
    )
    vpd: Dict[str, Optional[Any]] = field(
        default_factory=lambda: {
            "current": None,
            "targeted": None,
            "range": None,
            "perfection": None,
            "perfectMin": None,
            "perfectMax": None,
            "tolerance": None,
        }
    )
    controlOptions: Dict[str, bool] = field(
        default_factory=lambda: {
            "nightVPDHold": False,
            "vpdDeviceDampening": False,
            "lightbyOGBControl": False,
            "lightControlType": "Default",
            "vpdLightControl": False,
            "co2Control": False,
            "workMode": False,
            "minMaxControl": False,
            "ownWeights": False,
            "ambientControl": False,
            "multiMediumControl": True,
            "aiLearning": False,
        }
    )
    controlOptionData: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "co2ppm": {"target": 0, "current": 400, "minPPM": 400, "maxPPM": 1800},
            "weights": {"temp": 0, "hum": 0, "defaultValue": 1},
            "minmax": {"minTemp": 0, "maxTemp": 0, "minHum": 0, "maxHum": 0},
        }
    )
    isPlantDay: Dict[str, Any] = field(
        default_factory=lambda: {
            "islightON": False,
            "lightOnTime": "",
            "lightOffTime": "",
            "sunRiseTime": "",
            "sunSetTime": "",
            "plantPhase": "",
            "generativeWeek": 0,
        }
    )
    plantsView: Dict[str, Any] = field(
        default_factory=lambda: {
            "isTimeLapseActive": False,
            "TimeLapseIntervall": 300,
            "StartDate": "",
            "EndDate": "",
            "OutPutFormat": "",
        }
    )
    plantStages: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "Germination": {
                "vpdRange": [0.35, 0.70],
                "minTemp": 20,
                "maxTemp": 24,
                "minHumidity": 78,
                "maxHumidity": 85,
            },
            "Clones": {
                "vpdRange": [0.40, 0.85],
                "minTemp": 20,
                "maxTemp": 24,
                "minHumidity": 72,
                "maxHumidity": 80,
            },
            "EarlyVeg": {
                "vpdRange": [0.60, 1.20],
                "minTemp": 22,
                "maxTemp": 26,
                "minHumidity": 65,
                "maxHumidity": 75,
            },
            "MidVeg": {
                "vpdRange": [0.75, 1.45],
                "minTemp": 23,
                "maxTemp": 27,
                "minHumidity": 60,
                "maxHumidity": 72,
            },
            "LateVeg": {
                "vpdRange": [0.90, 1.65],
                "minTemp": 24,
                "maxTemp": 27,
                "minHumidity": 55,
                "maxHumidity": 68,
            },
            "EarlyFlower": {
                "vpdRange": [0.80, 1.55],
                "minTemp": 22,
                "maxTemp": 26,
                "minHumidity": 55,
                "maxHumidity": 68,
            },
            "MidFlower": {
                "vpdRange": [0.90, 1.70],
                "minTemp": 21,
                "maxTemp": 25,
                "minHumidity": 48,
                "maxHumidity": 62,
            },
            "LateFlower": {
                "vpdRange": [0.90, 1.85],
                "minTemp": 19,
                "maxTemp": 24,
                "minHumidity": 42,
                "maxHumidity": 58,
            },
        }
    )
    plantDates: Dict[str, Any] = field(
        default_factory=lambda: {
            "isGrowing": False,
            "growstartdate": "",
            "bloomswitchdate": "",
            "breederbloomdays": 0,
            "planttotaldays": 0,
            "totalbloomdays": 0,
            "daysToChopChop": 0,
            "hasEndet": False,
        }
    )
    lightPlantStages: Dict[str, LightStage] = field(
        default_factory=lambda: {
            "Germination": LightStage(min=20, max=25, phase=""),
            "Clones": LightStage(min=20, max=25, phase=""),
            "EarlyVeg": LightStage(min=25, max=35, phase=""),
            "MidVeg": LightStage(min=35, max=45, phase=""),
            "LateVeg": LightStage(min=45, max=55, phase=""),
            "EarlyFlower": LightStage(min=70, max=100, phase=""),
            "MidFlower": LightStage(min=70, max=100, phase=""),
            "LateFlower": LightStage(min=70, max=100, phase=""),
        }
    )

    lightLedTyes: Dict[str, Any] = field(
        default_factory=lambda: {
            "fullspektrum_grow": 15,
            "quantum_board": 16,
            "red_blue_grow": 12,
            "high_end_grow": 18,
            "cob_grow": 20,
            "hps_equivalent": 15,
            "burple": 12,
            "white_led": 54,
            "manual": 0,
        }
    )

    # Premium subscription data (from API login)
    # Contains: plan_name, features, limits, usage
    subscriptionData: Dict[str, Any] = field(
        default_factory=lambda: {
            "plan_name": "free",
            "features": {},
            "limits": {},
            "usage": {},
        }
    )

    weather: Dict[str, Any] = field(
        default_factory=lambda: {
            "temperature": None,
            "humidity": None,
            "wind_speed": None,
            "description": "",
            "last_update": None,
        }
    )
    drying: Dict[str, Any] = field(
        default_factory=lambda: {
            "mode_start_time": None,
            "currentDryMode": "",
            "isRunning": False,
            "dewpointVPD": None,
            "vaporPressureActual": None,
            "vaporPressureSaturation": None,
            "5DayDryVPD": None,
            "modes": {
                "ElClassico": {
                    "isActive": False,
                    "phase": {
                        "start": {
                            "targetTemp": 20,
                            "targetHumidity": 62,
                            "durationHours": 72,
                        },
                        "halfTime": {
                            "targetTemp": 20,
                            "targetHumidity": 60,
                            "durationHours": 72,
                        },
                        "endTime": {
                            "targetTemp": 20,
                            "targetHumidity": 58,
                            "durationHours": 72,
                        },
                    },
                },
                "5DayDry": {
                    "isActive": False,
                    "phase": {
                        "start": {
                            "targetTemp": 22.2,
                            "targetHumidity": 55,
                            "targetVPD": 1.2,
                            "durationHours": 48,
                        },
                        "halfTime": {
                            "maxTemp": 23.3,
                            "targetHumidity": 52,
                            "targetVPD": 1.39,
                            "durationHours": 24,
                        },
                        "endTime": {
                            "maxTemp": 23.9,
                            "targetHumidity": 50,
                            "targetVPD": 1.5,
                            "durationHours": 48,
                        },
                    },
                },
                "DewBased": {
                    "isActive": False,
                    "phase": {
                        "start": {
                            "targetTemp": 20,
                            "targetDewPoint": 12.25,
                            "durationHours": 96,
                        },
                        "halfTime": {
                            "targetTemp": 20,
                            "targetDewPoint": 11.1,
                            "durationHours": 96,
                        },
                        "endTime": {
                            "targetTemp": 20,
                            "targetDewPoint": 11.1,
                            "durationHours": 48,
                        },
                    },
                },
            },
        }
    )
    workData: Dict[str, List[Any]] = field(
        default_factory=lambda: {
            "temperature": [],
            "humidity": [],
            "dewpoint": [],
            "moisture": [],
            "ec": [],
            "Devices": [],
        }
    )
    DeviceMinMax: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "Exhaust": {
                "active": False,
                "minDuty": 0,
                "maxDuty": 0,
                "Default": {"min": 10, "max": 95},
            },
            "Intake": {
                "active": False,
                "minDuty": 0,
                "maxDuty": 0,
                "Default": {"min": 10, "max": 95},
            },
            "Ventilation": {
                "active": False,
                "minDuty": 0,
                "maxDuty": 0,
                "Default": {"min": 85, "max": 100},
            },
            "Light": {
                "active": False,
                "minVoltage": 0,
                "maxVoltage": 0,
                "Default": {"min": 20, "max": 50},
            },
        }
    )
    DeviceProfiles: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "Exhaust": {
                "type": "both",
                "cap": "canExhaust",
                "direction": "reduce",
                "effect": 1.0,
                "sideEffect": {},
            },
            "Intake": {
                "type": "both",
                "cap": "canIntake",
                "direction": "reduce",
                "effect": 1.0,
                "sideEffect": {},
            },
            "Light": {
                "type": "temperature",
                "cap": "canLight",
                "direction": "increase",
                "effect": 1.0,
                "sideEffect": {"type": "temperature", "direction": "increase"},
            },
            "Ventilation": {
                "type": "both",
                "cap": "canVentilate",
                "direction": "increase",
                "effect": 0.5,
                "sideEffect": {},
            },
            "Heater": {
                "type": "temperature",
                "cap": "canHeat",
                "direction": "increase",
                "effect": 2.0,
                "sideEffect": {"type": "humidity", "direction": "reduce"},
            },
            "Cooler": {
                "type": "temperature",
                "cap": "canCool",
                "direction": "reduce",
                "effect": 2.0,
                "sideEffect": {"type": "humidity", "direction": "reduce"},
            },
            "Humidifier": {
                "type": "humidity",
                "cap": "canHumidify",
                "direction": "increase",
                "effect": 1.5,
                "sideEffect": {},
            },
            "Dehumidifier": {
                "type": "humidity",
                "cap": "canDehumidify",
                "direction": "increase",
                "effect": 2.0,
                "sideEffect": {"type": "temperature", "direction": "increase"},
            },
            "Climate": {
                "type": "both",
                "cap": "canClimate",
                "direction": "increase",
                "effect": 2.0,
                "sideEffect": {},
            },
        }
    )

    def __post_init__(self):
        """Wird nach der Initialisierung aufgerufen, um hass zu setzen"""
        # hass muss später manuell gesetzt werden
        pass

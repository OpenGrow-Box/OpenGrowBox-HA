from types import SimpleNamespace

from custom_components.opengrowbox.OGBController.managers.hydro.crop_steering.OGBCSManager import (
    CSMode,
    OGBCSManager,
)

from tests.logic.helpers import FakeDataStore


def _cs_manager(initial=None):
    manager = OGBCSManager.__new__(OGBCSManager)
    manager.room = "dev_room"
    manager.data_store = FakeDataStore(initial or {})
    return manager


def test_extract_phase_from_mode_and_value():
    manager = _cs_manager()

    assert manager._extract_phase_from_mode(CSMode.MANUAL_P2) == "p2"
    assert manager._extract_phase_from_value("P1") == "p1"
    assert manager._extract_phase_from_value("manual_p3") == "p3"
    assert manager._extract_phase_from_value(None) == "p0"


def test_get_drippers_filters_only_dripper_devices():
    manager = _cs_manager(
        {
            "capabilities": {
                "canPump": {
                    "devEntities": [
                        "switch.dripper_front",
                        "switch.water_pump",
                        "switch.dripper_back",
                    ]
                }
            }
        }
    )

    drippers = manager._get_drippers()
    assert "switch.dripper_front" in drippers
    assert "switch.dripper_back" in drippers
    assert "switch.water_pump" not in drippers


def test_get_automatic_timing_settings_reads_numeric_values():
    manager = _cs_manager(
        {
            "CropSteering": {
                "Substrate": {
                    "p1": {
                        "Shot_Duration_Sec": "45",
                        "Shot_Intervall": "30.5",
                        "Shot_Sum": "7",
                    }
                }
            }
        }
    )

    settings = manager._get_automatic_timing_settings("p1")
    assert settings["ShotDuration"] == 45
    assert settings["ShotIntervall"] == 30.5
    assert settings["ShotSum"] == 7


def test_get_manual_phase_settings_prefers_new_paths_and_converts_types():
    manager = _cs_manager(
        {
            "CropSteering": {
                "Substrate": {
                    "p2": {
                        "Shot_Intervall": "25",
                        "Shot_Duration_Sec": "40",
                        "Shot_Sum": "6",
                        "VWC_Target": "62.5",
                    }
                }
            }
        }
    )

    settings = manager._get_manual_phase_settings("p2")
    assert settings["ShotIntervall"]["value"] == 25.0
    assert settings["ShotDuration"]["value"] == 40
    assert settings["ShotSum"]["value"] == 6
    assert settings["VWCTarget"]["value"] == 62.5

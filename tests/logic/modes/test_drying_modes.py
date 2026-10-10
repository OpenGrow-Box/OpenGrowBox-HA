"""
Test drying modes use correct values from datastore and emit proper events.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from tests.logic.helpers import FakeDataStore, FakeEventManager
from custom_components.opengrowbox.OGBController.actions.DryingActions import DryingActions
from custom_components.opengrowbox.OGBController.data.OGBDataClasses.OGBData import OGBConf
from custom_components.opengrowbox.OGBController.utils.calcs import (
    calc_humidity_from_dew_point,
    calculate_dew_point,
)


class _FakeCooldownManager:
    """Cooldown manager that allows every action by default."""

    def __init__(self, allowed=None):
        self.allowed = set(allowed or [])
        self.registered = []

    async def is_allowed(self, capability: str, action: str, deviation: float = 0) -> bool:
        if not self.allowed:
            return True
        return (capability, action) in self.allowed

    async def register(self, capability: str, action: str, deviation: float = 0):
        self.registered.append((capability, action))


def _fake_cooldown_manager(allowed=None):
    return _FakeCooldownManager(allowed=allowed)


class TestElClassico:
    """Test ElClassico drying mode with phases."""
    
    @pytest.fixture
    def drying_actions(self):
        """Create DryingActions instance with mocked event manager."""
        data_store = FakeDataStore({
            "drying": {
                "currentDryMode": "ElClassico",
                "mode_start_time": datetime.now().isoformat(),
                "isRunning": True,
                "modes": {
                    "ElClassico": {
                        "isActive": True,
                        "phase": {
                            "start": {
                                "targetTemp": 20.0,
                                "targetHumidity": 62.0,
                                "durationHours": 24,
                            },
                            "halfTime": {
                                "targetTemp": 22.0,
                                "targetHumidity": 58.0,
                                "durationHours": 24,
                            },
                            "endTime": {
                                "targetTemp": 24.0,
                                "targetHumidity": 55.0,
                                "durationHours": 24,
                            },
                        }
                    }
                }
            },
            "tentData": {
                "temperature": 18.0,
                "humidity": 55.0,
            }
        })
        
        event_manager = FakeEventManager()
        # Track emitted events
        event_manager.emitted_events = []
        original_emit = event_manager.emit
        
        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)
        
        event_manager.emit = tracked_emit
        
        return DryingActions(data_store, event_manager, "test_room", cooldown_manager=_fake_cooldown_manager())
    
    @pytest.mark.asyncio
    async def test_elclassico_start_phase_temp_low(self, drying_actions):
        """Test ElClassico start phase with low temperature."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        # Should emit heater increase and cooler reduce
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
        assert "Reduce Cooler" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_start_phase_temp_high(self, drying_actions):
        """Test ElClassico start phase with high temperature."""
        drying_actions.data_store.setDeep("tentData.temperature", 25.0)
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Cooler" in events
        assert "Reduce Heater" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_humidity_low(self, drying_actions):
        """Test ElClassico with low humidity."""
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)  # In tolerance
        drying_actions.data_store.setDeep("tentData.humidity", 58.0)  # Below 62
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Humidifier" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_humidity_high(self, drying_actions):
        """Test ElClassico with high humidity."""
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)  # In tolerance
        drying_actions.data_store.setDeep("tentData.humidity", 65.0)  # Above 62
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Dehumidifier" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_no_conflict_actions(self, drying_actions):
        """Test that conflicting actions are prevented."""
        # Set temp very low and humidity very high - both need actions
        drying_actions.data_store.setDeep("tentData.temperature", 15.0)  # Heater ON
        drying_actions.data_store.setDeep("tentData.humidity", 70.0)  # Dehumidify ON
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should not have both Increase and Reduce for same device
        assert not ("Increase Heater" in events and "Reduce Heater" in events)
        assert not ("Increase Cooler" in events and "Reduce Cooler" in events)
    
    @pytest.mark.asyncio
    async def test_elclassico_no_actions_in_tolerance(self, drying_actions):
        """Test that no actions are emitted when conditions are within tolerance."""
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)  # Exact target
        drying_actions.data_store.setDeep("tentData.humidity", 62.0)  # Exact target
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should not emit any device control events
        assert "Increase Heater" not in events
        assert "Increase Cooler" not in events
        assert "Increase Humidifier" not in events
        assert "Increase Dehumidifier" not in events
    
    @pytest.mark.asyncio
    async def test_elclassico_hot_ambient_humidity_low_exhaust_not_deadlocked(self, drying_actions):
        """Exhaust-only box: warm air (temp high) must not override the humidity branch.

        Humidity drops below target - tolerance, so the exhaust has to be reduced
        even though the temperature is still above target.
        """
        drying_actions.data_store.setDeep("tentData.temperature", 22.0)  # target 20 +/- 1 -> high
        drying_actions.data_store.setDeep("tentData.humidity", 59.0)  # target 62 +/- 2 -> low
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")

        await drying_actions.handle_ElClassico(phase_config)

        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Reduce Exhaust" in events
        assert "Increase Exhaust" not in events
        assert "Increase Ventilation" in events
        assert "Reduce Ventilation" not in events

    @pytest.mark.asyncio
    async def test_elclassico_cold_ambient_humidity_high_exhaust_not_deadlocked(self, drying_actions):
        """Temp low + humidity high: exhaust is driven by humidity only."""
        drying_actions.data_store.setDeep("tentData.temperature", 18.0)  # low
        drying_actions.data_store.setDeep("tentData.humidity", 65.0)  # high
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")

        await drying_actions.handle_ElClassico(phase_config)

        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Exhaust" in events
        assert "Reduce Exhaust" not in events
        assert "Increase Ventilation" in events
        assert "Reduce Ventilation" not in events

    @pytest.mark.asyncio
    @pytest.mark.parametrize("temperature", [17.0, 23.0])
    async def test_elclassico_temperature_only_does_not_touch_exhaust_or_ventilation(self, drying_actions, temperature):
        """Temperature deviation alone must not emit any exhaust/ventilation action."""
        drying_actions.data_store.setDeep("tentData.temperature", temperature)
        drying_actions.data_store.setDeep("tentData.humidity", 62.0)  # in tolerance
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")

        await drying_actions.handle_ElClassico(phase_config)

        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert events, "temperature deviation should still trigger heater/cooler actions"
        for action in ("Increase Exhaust", "Reduce Exhaust", "Increase Ventilation", "Reduce Ventilation"):
            assert action not in events

    @pytest.mark.asyncio
    async def test_elclassico_halfTime_phase(self, drying_actions):
        """Test ElClassico halfTime phase with different targets."""
        # Set start time 25 hours ago (in halfTime phase)
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)  # Below halfTime target of 22
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        # Should use halfTime target (22°C) and see temp is low
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_endTime_phase(self, drying_actions):
        """Test ElClassico endTime phase with different targets."""
        # Set start time 50 hours ago (in endTime phase)
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 22.0)  # Below endTime target of 24
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        # Should use endTime target (24°C) and see temp is low
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_string_sensor_values(self, drying_actions):
        """Test ElClassico handles string sensor values."""
        drying_actions.data_store.setDeep("tentData.temperature", "18.5")
        drying_actions.data_store.setDeep("tentData.humidity", "55.0")
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_missing_sensor_data(self, drying_actions):
        """Test ElClassico handles missing sensor data gracefully."""
        drying_actions.data_store.setDeep("tentData.temperature", None)
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        await drying_actions.handle_ElClassico(phase_config)
        
        # Should not crash and should not emit actions
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" not in events
    
    @pytest.mark.asyncio
    async def test_elclassico_phase_progression(self, drying_actions):
        """Test that phases progress correctly over time."""
        # Test start phase (0-24h)
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        current_phase = drying_actions.get_current_phase(phase_config)
        assert current_phase is not None
        assert current_phase.get("phase_name") == "start"
        assert current_phase.get("targetTemp") == 20.0
        
        # Test halfTime phase (24-48h)
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        current_phase = drying_actions.get_current_phase(phase_config)
        assert current_phase is not None
        assert current_phase.get("phase_name") == "halfTime"
        assert current_phase.get("targetTemp") == 22.0
        
        # Test endTime phase (48-72h)
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        current_phase = drying_actions.get_current_phase(phase_config)
        assert current_phase is not None
        assert current_phase.get("phase_name") == "endTime"
        assert current_phase.get("targetTemp") == 24.0


class Test5DayDry:
    """Test 5DayDry drying mode."""
    
    @pytest.fixture
    def drying_actions(self):
        data_store = FakeDataStore({
            "drying": {
                "currentDryMode": "5DayDry",
                "mode_start_time": datetime.now().isoformat(),
                "isRunning": True,
                "modes": {
                    "5DayDry": {
                        "isActive": True,
                        "phase": {
                            "start": {
                                "targetTemp": 20.0,
                                "targetHumidity": 62.0,
                                "durationHours": 24,
                            },
                            "halfTime": {
                                "targetTemp": 22.0,
                                "targetHumidity": 58.0,
                                "durationHours": 24,
                            },
                            "endTime": {
                                "targetTemp": 24.0,
                                "targetHumidity": 55.0,
                                "durationHours": 24,
                            },
                        }
                    }
                }
            },
            "tentData": {
                "temperature": 18.0,
                "humidity": 55.0,
            },
            "vpd": {
                "current": 1.2,
            }
        })
        
        event_manager = FakeEventManager()
        event_manager.emitted_events = []
        original_emit = event_manager.emit
        
        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)
        
        event_manager.emit = tracked_emit
        
        return DryingActions(data_store, event_manager, "test_room", cooldown_manager=_fake_cooldown_manager())
    
    @pytest.mark.asyncio
    async def test_5daydry_temp_low(self, drying_actions):
        """Test 5DayDry with low temperature."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.5DayDry")
        await drying_actions.handle_5DayDry(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_5daydry_hum_high(self, drying_actions):
        """Test 5DayDry with high humidity."""
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)
        drying_actions.data_store.setDeep("tentData.humidity", 65.0)
        phase_config = drying_actions.data_store.getDeep("drying.modes.5DayDry")
        
        await drying_actions.handle_5DayDry(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Dehumidifier" in events
    
    @pytest.mark.asyncio
    async def test_5daydry_no_conflict(self, drying_actions):
        """Test 5DayDry prevents conflicting actions."""
        drying_actions.data_store.setDeep("tentData.temperature", 15.0)
        drying_actions.data_store.setDeep("tentData.humidity", 70.0)
        phase_config = drying_actions.data_store.getDeep("drying.modes.5DayDry")
        
        await drying_actions.handle_5DayDry(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert not ("Increase Heater" in events and "Reduce Heater" in events)


class TestDewBased:
    """Test DewBased drying mode."""
    
    @pytest.fixture
    def drying_actions(self):
        data_store = FakeDataStore({
            "drying": {
                "currentDryMode": "DewBased",
                "mode_start_time": datetime.now().isoformat(),
                "isRunning": True,
                "modes": {
                    "DewBased": {
                        "isActive": True,
                        "phase": {
                            "start": {
                                "targetTemp": 20.0,
                                "targetHumidity": 62.0,
                                "durationHours": 24,
                            },
                        }
                    }
                }
            },
            "tentData": {
                "temperature": 18.0,
                "humidity": 55.0,
                "dewpoint": 12.0,
            }
        })
        
        event_manager = FakeEventManager()
        event_manager.emitted_events = []
        original_emit = event_manager.emit
        
        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)
        
        event_manager.emit = tracked_emit
        
        return DryingActions(data_store, event_manager, "test_room", cooldown_manager=_fake_cooldown_manager())
    
    @pytest.mark.asyncio
    async def test_dewbased_temp_low(self, drying_actions):
        """Test DewBased with low temperature."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.DewBased")
        await drying_actions.handle_DewBased(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_dewbased_too_dry(self, drying_actions):
        """Test DewBased when too dry."""
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)
        drying_actions.data_store.setDeep("tentData.dewpoint", 8.0)  # Very low dewpoint
        phase_config = drying_actions.data_store.getDeep("drying.modes.DewBased")
        
        await drying_actions.handle_DewBased(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Humidifier" in events
    
    @pytest.mark.asyncio
    async def test_dewbased_too_humid(self, drying_actions):
        """Test DewBased when too humid."""
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)
        drying_actions.data_store.setDeep("tentData.dewpoint", 18.0)  # High dewpoint
        drying_actions.data_store.setDeep("tentData.humidity", 80.0)  # High humidity
        phase_config = drying_actions.data_store.getDeep("drying.modes.DewBased")
        
        await drying_actions.handle_DewBased(phase_config)
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Dehumidifier" in events


class TestDewBasedDefaults:
    """DewBased must work with the shipped defaults (targetTemp + targetDewPoint, no targetHumidity)."""

    @pytest.fixture
    def drying_actions(self):
        drying = OGBConf(hass=None).drying
        drying["currentDryMode"] = "DewBased"
        drying["mode_start_time"] = datetime.now().isoformat()
        drying["isRunning"] = True
        data_store = FakeDataStore({
            "drying": drying,
            "tentData": {"temperature": 20.0, "humidity": 61.0, "dewpoint": 12.25},
        })
        event_manager = FakeEventManager()
        event_manager.emitted_events = []
        original_emit = event_manager.emit

        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)

        event_manager.emit = tracked_emit
        return DryingActions(data_store, event_manager, "test_room", cooldown_manager=_fake_cooldown_manager())

    def _events(self, drying_actions):
        return [e[0] for e in drying_actions.event_manager.emitted_events]

    @pytest.mark.asyncio
    async def test_default_phase_has_no_target_humidity(self, drying_actions):
        """Guards the premise of these tests: defaults only define the dew point."""
        phase = drying_actions.data_store.getDeep("drying.modes.DewBased.phase.start")
        assert "targetHumidity" not in phase
        assert "targetDewPoint" in phase

    @pytest.mark.asyncio
    async def test_defaults_on_target_emit_no_actions(self, drying_actions):
        await drying_actions.handle_DewBased(drying_actions.data_store.getDeep("drying.modes.DewBased"))

        assert self._events(drying_actions) == []

    @pytest.mark.asyncio
    async def test_defaults_too_dry_humidifies(self, drying_actions):
        drying_actions.data_store.setDeep("tentData.humidity", 50.0)
        drying_actions.data_store.setDeep("tentData.dewpoint", calculate_dew_point(20.0, 50.0))

        await drying_actions.handle_DewBased(drying_actions.data_store.getDeep("drying.modes.DewBased"))

        events = self._events(drying_actions)
        assert "Increase Humidifier" in events
        assert "Increase Dehumidifier" not in events

    @pytest.mark.asyncio
    async def test_defaults_too_humid_dehumidifies(self, drying_actions):
        drying_actions.data_store.setDeep("tentData.humidity", 72.0)
        drying_actions.data_store.setDeep("tentData.dewpoint", calculate_dew_point(20.0, 72.0))

        await drying_actions.handle_DewBased(drying_actions.data_store.getDeep("drying.modes.DewBased"))

        events = self._events(drying_actions)
        assert "Increase Dehumidifier" in events
        assert "Increase Exhaust" in events

    @pytest.mark.asyncio
    async def test_defaults_temperature_only_leaves_exhaust_alone(self, drying_actions):
        drying_actions.data_store.setDeep("tentData.temperature", 24.0)
        # humidity and dew point stay on target so only the temperature branch is active

        await drying_actions.handle_DewBased(drying_actions.data_store.getDeep("drying.modes.DewBased"))

        events = self._events(drying_actions)
        assert "Increase Cooler" in events
        for action in ("Increase Exhaust", "Reduce Exhaust", "Reduce Ventilation"):
            assert action not in events

    @pytest.mark.asyncio
    async def test_explicit_target_humidity_wins_over_dew_point(self, drying_actions):
        phase = drying_actions.data_store.getDeep("drying.modes.DewBased.phase.start")
        phase["targetHumidity"] = 40.0  # data at 61 % is now far too humid

        await drying_actions.handle_DewBased(drying_actions.data_store.getDeep("drying.modes.DewBased"))

        assert "Increase Dehumidifier" in self._events(drying_actions)

    @pytest.mark.asyncio
    async def test_phase_without_any_humidity_target_is_skipped(self, drying_actions):
        phase = drying_actions.data_store.getDeep("drying.modes.DewBased.phase.start")
        del phase["targetDewPoint"]

        await drying_actions.handle_DewBased(drying_actions.data_store.getDeep("drying.modes.DewBased"))

        assert self._events(drying_actions) == []


class TestHumidityFromDewPoint:
    def test_matches_default_dew_points_at_20c(self):
        assert calc_humidity_from_dew_point(20, 12.25) == pytest.approx(61.0, abs=0.5)
        assert calc_humidity_from_dew_point(20, 11.1) == pytest.approx(56.6, abs=0.5)

    @pytest.mark.parametrize("temp,hum", [(15, 40), (20, 62), (25, 80)])
    def test_round_trips_with_calculate_dew_point(self, temp, hum):
        dew = calculate_dew_point(temp, hum)
        assert calc_humidity_from_dew_point(temp, dew) == pytest.approx(hum, abs=0.5)

    def test_invalid_input_returns_none(self):
        assert calc_humidity_from_dew_point(20, None) is None
        assert calc_humidity_from_dew_point("x", 10) is None


class TestOwnDry:
    """Test OwnDry drying mode."""
    
    @pytest.fixture
    def drying_actions(self):
        data_store = FakeDataStore({
            "drying": {
                "currentDryMode": "OwnDry",
                "mode_start_time": datetime.now().isoformat(),
                "isRunning": True,
            },
            "tentData": {
                "temperature": 18.0,
                "humidity": 55.0,
            },
            "controlOptionData": {
                "minmax": {
                    "minTemp": 17.0,
                    "maxTemp": 20.0,
                    "minHum": 55.0,
                    "maxHum": 62.0,
                }
            }
        })
        
        event_manager = FakeEventManager()
        event_manager.emitted_events = []
        original_emit = event_manager.emit
        
        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)
        
        event_manager.emit = tracked_emit
        
        return DryingActions(data_store, event_manager, "test_room", cooldown_manager=_fake_cooldown_manager())
    
    @pytest.mark.asyncio
    async def test_owndry_temp_low(self, drying_actions):
        """Test OwnDry with temperature below midpoint."""
        drying_actions.data_store.setDeep("tentData.temperature", 17.0)  # 1.5 below midpoint of 18.5
        drying_actions.data_store.setDeep("tentData.humidity", 58.5)  # At midpoint, no hum action
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_OwnDry()
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_owndry_temp_high(self, drying_actions):
        """Test OwnDry with temperature above midpoint."""
        drying_actions.data_store.setDeep("tentData.temperature", 22.0)
        
        await drying_actions.handle_OwnDry()
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Cooler" in events
    
    @pytest.mark.asyncio
    async def test_owndry_hum_low(self, drying_actions):
        """Test OwnDry with humidity below midpoint."""
        drying_actions.data_store.setDeep("tentData.temperature", 18.5)  # Close to midpoint
        drying_actions.data_store.setDeep("tentData.humidity", 50.0)  # Below 58.5 midpoint
        
        await drying_actions.handle_OwnDry()
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Humidifier" in events
    
    @pytest.mark.asyncio
    async def test_owndry_no_conflict(self, drying_actions):
        """Test OwnDry prevents conflicting actions."""
        drying_actions.data_store.setDeep("tentData.temperature", 15.0)  # Low
        drying_actions.data_store.setDeep("tentData.humidity", 70.0)  # High
        
        await drying_actions.handle_OwnDry()
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert not ("Increase Heater" in events and "Reduce Heater" in events)
    
    @pytest.mark.asyncio
    async def test_owndry_missing_minmax(self, drying_actions):
        """Test OwnDry handles missing min/max values."""
        drying_actions.data_store.setDeep("controlOptionData.minmax", {})
        
        await drying_actions.handle_OwnDry()
        
        # Should not crash, just not emit actions
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" not in events


class TestDryingModesGeneral:
    """Test general drying mode functionality."""
    
    @pytest.fixture
    def drying_actions(self):
        data_store = FakeDataStore({
            "drying": {
                "currentDryMode": "ElClassico",
                "mode_start_time": datetime.now().isoformat(),
                "isRunning": True,
                "modes": {
                    "ElClassico": {
                        "isActive": True,
                        "phase": {
                            "start": {
                                "targetTemp": 20.0,
                                "targetHumidity": 62.0,
                                "durationHours": 24,
                            },
                        }
                    }
                }
            },
            "tentData": {
                "temperature": 18.0,
                "humidity": 55.0,
            }
        })
        
        event_manager = FakeEventManager()
        event_manager.emitted_events = []
        original_emit = event_manager.emit
        
        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)
        
        event_manager.emit = tracked_emit
        
        return DryingActions(data_store, event_manager, "test_room", cooldown_manager=_fake_cooldown_manager())
    
    @pytest.mark.asyncio
    async def test_cleanup_drying_devices(self, drying_actions):
        """Test cleanup turns off all devices."""
        await drying_actions.cleanup_drying_devices()
        
        # Check that all reduce events were emitted
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Reduce Heater" in events
        assert "Reduce Cooler" in events
        assert "Reduce Humidifier" in events
        assert "Reduce Dehumidifier" in events
    
    @pytest.mark.asyncio
    async def test_start_drying_mode(self, drying_actions):
        """Test starting a drying mode sets correct values."""
        drying_actions.start_drying_mode("5DayDry")
        
        assert drying_actions.data_store.getDeep("drying.currentDryMode") == "5DayDry"
        assert drying_actions.data_store.getDeep("drying.isRunning") == True
        assert drying_actions.data_store.getDeep("drying.mode_start_time") is not None
    
    @pytest.mark.asyncio
    async def test_handle_drying_dispatcher(self, drying_actions):
        """Test main dispatcher routes to correct mode."""
        drying_actions.data_store.setDeep("drying.currentDryMode", "ElClassico")
        
        await drying_actions.handle_drying()
        
        # Should have processed (we can check by looking at logs or state)
        # The mode_start_time should be set if it was None
        assert drying_actions.data_store.getDeep("drying.mode_start_time") is not None
    
    @pytest.mark.asyncio
    async def test_handle_drying_no_dry(self, drying_actions):
        """Test dispatcher returns None for NO-Dry."""
        drying_actions.data_store.setDeep("drying.currentDryMode", "NO-Dry")
        
        result = await drying_actions.handle_drying()
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_handle_drying_invalid_mode(self, drying_actions):
        """Test dispatcher handles invalid mode."""
        drying_actions.data_store.setDeep("drying.currentDryMode", "InvalidMode")
        
        result = await drying_actions.handle_drying()
        
        assert result is None


class TestDryingCooldown:
    """Test that drying actions respect Global Cooldown (GCD)."""

    @pytest.fixture
    def drying_actions_blocked(self):
        """DryingActions with a cooldown manager that blocks canHumidify/canDehumidify."""
        data_store = FakeDataStore({
            "drying": {
                "currentDryMode": "ElClassico",
                "mode_start_time": datetime.now().isoformat(),
                "isRunning": True,
                "modes": {
                    "ElClassico": {
                        "isActive": True,
                        "phase": {
                            "start": {
                                "targetTemp": 20.0,
                                "targetHumidity": 62.0,
                                "durationHours": 24,
                            }
                        }
                    }
                }
            },
            "tentData": {
                "temperature": 20.0,
                "humidity": 65.0,  # Above target -> dehumidify wanted
            }
        })

        event_manager = FakeEventManager()
        event_manager.emitted_events = []
        original_emit = event_manager.emit

        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)

        event_manager.emit = tracked_emit

        # Block only humidity-related capabilities
        cooldown_manager = _fake_cooldown_manager(
            allowed={("canHeat", "Increase"), ("canCool", "Reduce")}
        )
        return DryingActions(
            data_store, event_manager, "test_room", cooldown_manager=cooldown_manager
        )

    @pytest.mark.asyncio
    async def test_actions_blocked_by_cooldown(self, drying_actions_blocked):
        """Humidity actions should be blocked when cooldown manager disallows them."""
        phase_config = drying_actions_blocked.data_store.getDeep("drying.modes.ElClassico")
        await drying_actions_blocked.handle_ElClassico(phase_config)

        events = [e[0] for e in drying_actions_blocked.event_manager.emitted_events]
        # Humidity actions must be blocked
        assert "Increase Dehumidifier" not in events
        assert "Reduce Humidifier" not in events

    @pytest.mark.asyncio
    async def test_cleanup_bypasses_cooldown(self, drying_actions_blocked):
        """Cleanup must always turn devices off, regardless of cooldown."""
        await drying_actions_blocked.cleanup_drying_devices()

        events = [e[0] for e in drying_actions_blocked.event_manager.emitted_events]
        assert "Reduce Humidifier" in events
        assert "Reduce Dehumidifier" in events


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

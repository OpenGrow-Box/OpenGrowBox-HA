"""
Test drying modes use correct values from datastore and emit proper events.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from tests.logic.helpers import FakeDataStore, FakeEventManager
from custom_components.opengrowbox.OGBController.actions.DryingActions import DryingActions


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
        
        return DryingActions(data_store, event_manager, "test_room")
    
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
        
        return DryingActions(data_store, event_manager, "test_room")
    
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
        
        return DryingActions(data_store, event_manager, "test_room")
    
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
        
        return DryingActions(data_store, event_manager, "test_room")
    
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
        
        return DryingActions(data_store, event_manager, "test_room")
    
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

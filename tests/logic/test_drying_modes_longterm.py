"""
Long-term test for drying modes across all stages.
Simulates a complete drying cycle from start to end.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from tests.logic.helpers import FakeDataStore, FakeEventManager
from custom_components.opengrowbox.OGBController.actions.DryingActions import DryingActions


class TestElClassicoLongTerm:
    """Long-term test simulating complete ElClassico drying cycle."""
    
    @pytest.fixture
    def drying_actions(self):
        """Create DryingActions with ElClassico mode."""
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
        event_manager.emitted_events = []
        original_emit = event_manager.emit
        
        async def tracked_emit(event_name, data=None, **kwargs):
            event_manager.emitted_events.append((event_name, data))
            return await original_emit(event_name, data, **kwargs)
        
        event_manager.emit = tracked_emit
        
        return DryingActions(data_store, event_manager, "test_room")
    
    @pytest.mark.asyncio
    async def test_elclassico_complete_cycle_start_phase(self, drying_actions):
        """Test ElClassico Start Phase (0-24h) with temperature control."""
        # Set start time to 1 hour ago (in start phase)
        start_time = datetime.now() - timedelta(hours=1)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        
        # Temperature is 18°C, target is 20°C
        drying_actions.data_store.setDeep("tentData.temperature", 18.0)
        drying_actions.data_store.setDeep("tentData.humidity", 55.0)
        
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        await drying_actions.handle_ElClassico(phase_config)
        
        # Should be in start phase
        current_phase = drying_actions.get_current_phase(phase_config)
        assert current_phase["phase_name"] == "start"
        assert current_phase["targetTemp"] == 20.0
        
        # Should heat up
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
        assert "Reduce Cooler" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_complete_cycle_halfTime_phase(self, drying_actions):
        """Test ElClassico HalfTime Phase (24-48h) with temperature control."""
        # Set start time to 25 hours ago (in halfTime phase)
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        
        # Temperature is 20°C, target is 22°C
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)
        drying_actions.data_store.setDeep("tentData.humidity", 56.0)
        
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        await drying_actions.handle_ElClassico(phase_config)
        
        # Should be in halfTime phase
        current_phase = drying_actions.get_current_phase(phase_config)
        assert current_phase["phase_name"] == "halfTime"
        assert current_phase["targetTemp"] == 22.0
        
        # Should heat up
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_complete_cycle_endTime_phase(self, drying_actions):
        """Test ElClassico EndTime Phase (48-72h) with temperature control."""
        # Set start time to 50 hours ago (in endTime phase)
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        
        # Temperature is 22°C, target is 24°C
        drying_actions.data_store.setDeep("tentData.temperature", 22.0)
        drying_actions.data_store.setDeep("tentData.humidity", 53.0)
        
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        await drying_actions.handle_ElClassico(phase_config)
        
        # Should be in endTime phase
        current_phase = drying_actions.get_current_phase(phase_config)
        assert current_phase["phase_name"] == "endTime"
        assert current_phase["targetTemp"] == 24.0
        
        # Should heat up
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
    
    @pytest.mark.asyncio
    async def test_elclassico_phase_temperature_progression(self, drying_actions):
        """Test that temperature targets increase across phases."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        # Start phase
        start_time = datetime.now() - timedelta(hours=1)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        phase = drying_actions.get_current_phase(phase_config)
        assert phase["targetTemp"] == 20.0
        
        # HalfTime phase
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        phase = drying_actions.get_current_phase(phase_config)
        assert phase["targetTemp"] == 22.0
        
        # EndTime phase
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        phase = drying_actions.get_current_phase(phase_config)
        assert phase["targetTemp"] == 24.0
    
    @pytest.mark.asyncio
    async def test_elclassico_phase_humidity_progression(self, drying_actions):
        """Test that humidity targets decrease across phases."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        # Start phase
        start_time = datetime.now() - timedelta(hours=1)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        phase = drying_actions.get_current_phase(phase_config)
        assert phase["targetHumidity"] == 62.0
        
        # HalfTime phase
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        phase = drying_actions.get_current_phase(phase_config)
        assert phase["targetHumidity"] == 58.0
        
        # EndTime phase
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        phase = drying_actions.get_current_phase(phase_config)
        assert phase["targetHumidity"] == 55.0
    
    @pytest.mark.asyncio
    async def test_elclassico_complete_cycle_all_conditions(self, drying_actions):
        """Test complete cycle with varying conditions."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.ElClassico")
        
        # Phase 1: Start - Temperature too low, humidity too low
        start_time = datetime.now() - timedelta(hours=1)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 16.0)
        drying_actions.data_store.setDeep("tentData.humidity", 50.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_ElClassico(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
        assert "Increase Humidifier" in events
        
        # Phase 2: HalfTime - Temperature too high, humidity too high
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 26.0)
        drying_actions.data_store.setDeep("tentData.humidity", 65.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_ElClassico(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Cooler" in events
        assert "Increase Dehumidifier" in events
        
        # Phase 3: EndTime - Perfect conditions
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 24.0)
        drying_actions.data_store.setDeep("tentData.humidity", 55.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_ElClassico(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should not emit any control actions when in tolerance
        assert "Increase Heater" not in events
        assert "Increase Cooler" not in events
        assert "Increase Humidifier" not in events
        assert "Increase Dehumidifier" not in events


class Test5DayDryLongTerm:
    """Long-term test simulating complete 5DayDry drying cycle."""
    
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
    async def test_5daydry_complete_cycle_all_phases(self, drying_actions):
        """Test complete 5DayDry cycle across all phases."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.5DayDry")
        
        # Phase 1: Start
        start_time = datetime.now() - timedelta(hours=1)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 18.0)
        drying_actions.data_store.setDeep("tentData.humidity", 55.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_5DayDry(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should take actions based on temp/hum and VPD
        assert len(events) > 0
        
        # Phase 2: HalfTime
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_5DayDry(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert len(events) > 0
        
        # Phase 3: EndTime
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_5DayDry(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert len(events) > 0


class TestDewBasedLongTerm:
    """Long-term test simulating complete DewBased drying cycle."""
    
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
    async def test_dewbased_complete_cycle_all_phases(self, drying_actions):
        """Test complete DewBased cycle across all phases."""
        phase_config = drying_actions.data_store.getDeep("drying.modes.DewBased")
        
        # Phase 1: Start - Low temp, low dewpoint
        start_time = datetime.now() - timedelta(hours=1)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 16.0)
        drying_actions.data_store.setDeep("tentData.dewpoint", 8.0)
        drying_actions.data_store.setDeep("tentData.humidity", 45.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_DewBased(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should heat and humidify
        assert "Increase Heater" in events
        assert "Increase Humidifier" in events
        
        # Phase 2: HalfTime - High temp, high dewpoint
        start_time = datetime.now() - timedelta(hours=25)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 26.0)
        drying_actions.data_store.setDeep("tentData.dewpoint", 20.0)
        drying_actions.data_store.setDeep("tentData.humidity", 75.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_DewBased(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should cool and dehumidify
        assert "Increase Cooler" in events
        assert "Increase Dehumidifier" in events
        
        # Phase 3: EndTime - Perfect conditions
        start_time = datetime.now() - timedelta(hours=50)
        drying_actions.data_store.setDeep("drying.mode_start_time", start_time.isoformat())
        drying_actions.data_store.setDeep("tentData.temperature", 24.0)
        drying_actions.data_store.setDeep("tentData.dewpoint", 14.0)
        drying_actions.data_store.setDeep("tentData.humidity", 55.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_DewBased(phase_config)
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should not emit control actions
        assert "Increase Heater" not in events
        assert "Increase Cooler" not in events


class TestOwnDryLongTerm:
    """Long-term test simulating OwnDry continuous control."""
    
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
    async def test_owndry_continuous_control(self, drying_actions):
        """Test OwnDry continuously controls within min/max bounds."""
        # Temperature below midpoint (18.5), humidity below midpoint (58.5)
        drying_actions.data_store.setDeep("tentData.temperature", 17.0)  # 1.5 below midpoint
        drying_actions.data_store.setDeep("tentData.humidity", 56.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_OwnDry()
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should heat (below 18.5 midpoint)
        assert "Increase Heater" in events
        assert "Increase Humidifier" in events
        
        # Temperature above midpoint, humidity above midpoint
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)  # 1.5 above midpoint
        drying_actions.data_store.setDeep("tentData.humidity", 61.0)  # 2.5 above midpoint
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_OwnDry()
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should cool (above 18.5 midpoint + tolerance)
        assert "Increase Cooler" in events
        assert "Increase Dehumidifier" in events
        
        # Perfect conditions at midpoint
        drying_actions.data_store.setDeep("tentData.temperature", 18.5)
        drying_actions.data_store.setDeep("tentData.humidity", 58.5)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_OwnDry()
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        # Should not emit control actions
        assert "Increase Heater" not in events
        assert "Increase Cooler" not in events
        assert "Increase Humidifier" not in events
        assert "Increase Dehumidifier" not in events
    
    @pytest.mark.asyncio
    async def test_owndry_boundary_conditions(self, drying_actions):
        """Test OwnDry at boundary conditions."""
        # At min values
        drying_actions.data_store.setDeep("tentData.temperature", 17.0)
        drying_actions.data_store.setDeep("tentData.humidity", 55.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_OwnDry()
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Heater" in events
        
        # At max values
        drying_actions.data_store.setDeep("tentData.temperature", 20.0)
        drying_actions.data_store.setDeep("tentData.humidity", 62.0)
        drying_actions.event_manager.emitted_events.clear()
        
        await drying_actions.handle_OwnDry()
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Increase Cooler" in events


class TestDryingModesIntegration:
    """Integration tests across all drying modes."""
    
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
    async def test_drying_modes_cleanup(self, drying_actions):
        """Test cleanup works across all modes."""
        await drying_actions.cleanup_drying_devices()
        
        events = [e[0] for e in drying_actions.event_manager.emitted_events]
        assert "Reduce Heater" in events
        assert "Reduce Cooler" in events
        assert "Reduce Humidifier" in events
        assert "Reduce Dehumidifier" in events
        assert "Reduce Exhaust" in events
        assert "Reduce Ventilation" in events
    
    @pytest.mark.asyncio
    async def test_drying_modes_dispatcher(self, drying_actions):
        """Test dispatcher routes to correct mode."""
        # Test ElClassico
        drying_actions.data_store.setDeep("drying.currentDryMode", "ElClassico")
        await drying_actions.handle_drying()
        
        # Test NO-Dry returns None
        drying_actions.data_store.setDeep("drying.currentDryMode", "NO-Dry")
        result = await drying_actions.handle_drying()
        assert result is None
        
        # Test invalid mode
        drying_actions.data_store.setDeep("drying.currentDryMode", "InvalidMode")
        result = await drying_actions.handle_drying()
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

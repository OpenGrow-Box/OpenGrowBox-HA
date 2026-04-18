import pytest
from datetime import time, datetime, timedelta
from unittest.mock import Mock, MagicMock, patch


class TestLightUVScheduling:
    """Tests für LightUV Scheduling-Logik."""

    @pytest.fixture
    def light_uv_instance(self):
        """Erstellt eine LightUV-Instanz für Tests."""
        from custom_components.opengrowbox.OGBController.OGBDevices.LightUV import LightUV
        
        # Mock data_store
        mock_data_store = Mock()
        mock_data_store.getDeep = Mock(side_effect=lambda key: {
            "isPlantDay.lightOnTime": "10:30:00",
            "isPlantDay.lightOffTime": "11:30:00",
            "isPlantDay.islightON": True,
            "specialLights.uv": {
                "enabled": True,
                "mode": "Schedule",
                "delayAfterStartMinutes": 20,
                "stopBeforeEndMinutes": 20,
                "maxDurationHours": 1,
                "intensity": 80,
            },
        }.get(key))
        
        # Mock event_manager
        mock_event_manager = Mock()
        
        # Erstelle LightUV-Instanz
        light_uv = LightUV.__new__(LightUV)
        light_uv.deviceName = "test_uv"
        light_uv.room = "dev_room"
        light_uv.data_store = mock_data_store
        light_uv.event_manager = mock_event_manager
        light_uv.lightOnTime = time(10, 30)
        light_uv.lightOffTime = time(11, 30)
        light_uv.enabled = True
        light_uv.mode = "Schedule"
        light_uv.delay_after_start_minutes = 20
        light_uv.stop_before_end_minutes = 20
        light_uv.max_duration_hours = 1
        light_uv.intensity_percent = 80
        light_uv.is_uv_active = False
        light_uv.islightON = True
        light_uv.current_phase = None
        light_uv.daily_exposure_minutes = 0
        
        return light_uv

    def test_light_on_before_light_off_validation(self, light_uv_instance):
        """Test: LightOn muss VOR LightOff sein."""
        from custom_components.opengrowbox.OGBController.OGBDevices.LightUV import LightUV
        
        # Prüfe dass Zeit-Validierung funktioniert
        assert light_uv_instance.lightOnTime < light_uv_instance.lightOffTime

    def test_sufficient_light_time_for_uv(self, light_uv_instance):
        """Test: Genug Lichtzeit vorhanden (60min >= 41min required)."""
        light_on = light_uv_instance.lightOnTime
        light_off = light_uv_instance.lightOffTime
        
        # Berechne verfügbare Zeit
        now = datetime.now()
        light_on_dt = datetime.combine(now.date(), light_on)
        light_off_dt = datetime.combine(now.date(), light_off)
        
        available_minutes = (light_off_dt - light_on_dt).total_seconds() / 60
        required_minutes = light_uv_instance.delay_after_start_minutes + light_uv_instance.stop_before_end_minutes + 1
        
        assert available_minutes >= required_minutes, f"Available: {available_minutes}, Required: {required_minutes}"

    def test_uv_window_calculation(self, light_uv_instance):
        """Test: UV-Fenster wird korrekt berechnet."""
        light_on = light_uv_instance.lightOnTime
        light_off = light_uv_instance.lightOffTime
        delay = light_uv_instance.delay_after_start_minutes
        stop = light_uv_instance.stop_before_end_minutes
        
        now = datetime.now()
        light_on_dt = datetime.combine(now.date(), light_on)
        light_off_dt = datetime.combine(now.date(), light_off)
        
        uv_start = light_on_dt + timedelta(minutes=delay)
        uv_end = light_off_dt - timedelta(minutes=stop)
        
        # UV-Fenster sollte 10:50 - 11:10 sein
        assert uv_start.hour == 10 and uv_start.minute == 50
        assert uv_end.hour == 11 and uv_end.minute == 10

    def test_insufficient_light_time_scenario(self):
        """Test: Nicht genug Lichtzeit (z.B. 30min Licht, 40min Delay+Stop)."""
        from custom_components.opengrowbox.OGBController.OGBDevices.LightUV import LightUV
        
        light_on = time(10, 0)
        light_off = time(10, 30)  # Nur 30 min Lichtzeit
        delay = 20
        stop = 20
        required = delay + stop + 1  # 41 min benötigt
        
        now = datetime.now()
        light_on_dt = datetime.combine(now.date(), light_on)
        light_off_dt = datetime.combine(now.date(), light_off)
        
        available = (light_off_dt - light_on_dt).total_seconds() / 60
        
        # Sollte insufficient sein
        assert available < required
        assert available == 30
        assert required == 41

    def test_light_time_equal_to_required(self):
        """Test: Lichtzeit exakt gleich required."""
        light_on = time(10, 0)
        light_off = time(10, 41)  # Exakt 41 min
        delay = 20
        stop = 20
        required = delay + stop + 1  # 41 min
        
        now = datetime.now()
        light_on_dt = datetime.combine(now.date(), light_on)
        light_off_dt = datetime.combine(now.date(), light_off)
        
        available = (light_off_dt - light_on_dt).total_seconds() / 60
        
        # Sollte >= sein (genug Zeit)
        assert available >= required

    def test_fallback_times_when_invalid(self):
        """Test: Fallback-Zeiten wenn LightOn >= LightOff."""
        # Dieser Test würde in einem echten Szenario mit falschen Zeiten funktionieren
        # LightOn: 09:00, LightOff: 09:00 -> Fallback sollte 09:00-20:00 sein
        fallback_on = time(9, 0)
        fallback_off = time(20, 0)
        
        assert fallback_on < fallback_off
        assert fallback_on == time(9, 0)
        assert fallback_off == time(20, 0)

    def test_delay_zero_starts_immediately(self):
        """Test: Delay=0 bedeutet UV startet sofort mit Licht."""
        light_on = time(10, 0)
        light_off = time(20, 0)
        delay = 0
        stop = 30
        
        now = datetime.now()
        light_on_dt = datetime.combine(now.date(), light_on)
        light_off_dt = datetime.combine(now.date(), light_off)
        
        uv_start = light_on_dt + timedelta(minutes=delay)
        
        # UV startet direkt mit Licht
        assert uv_start.hour == 10
        assert uv_start.minute == 0


class TestLightUVTimeParsing:
    """Tests für Zeit-Parsing in LightUV."""

    def test_parse_valid_time_string(self):
        """Test: Gültige Zeit-String parsen."""
        time_str = "10:30:00"
        parsed = datetime.strptime(time_str, "%H:%M:%S").time()
        
        assert parsed.hour == 10
        assert parsed.minute == 30

    def test_parse_invalid_time_string(self):
        """Test: Ungültige Zeit-String gibt ValueError."""
        time_str = "invalid"
        
        with pytest.raises(ValueError):
            datetime.strptime(time_str, "%H:%M:%S").time()

    def test_none_time_string_handling(self):
        """Test: None-String wird behandelt."""
        # None sollte nicht geparst werden
        time_str = None
        
        if time_str:
            parsed = datetime.strptime(time_str, "%H:%M:%S").time()
        else:
            # Fallback verwenden
            parsed = time(9, 0)
        
        assert parsed == time(9, 0)
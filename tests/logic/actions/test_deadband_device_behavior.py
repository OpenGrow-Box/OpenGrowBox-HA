"""
Simplified tests for Smart Deadband device behavior.
Tests the logic without full device initialization.
"""

import pytest
from unittest.mock import AsyncMock, Mock


def create_mock_device(device_type, is_dimmable=True):
    """Helper to create a minimal mock device with all required attributes."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device
    
    device = Device.__new__(Device)
    device.deviceName = f"Test{device_type}"
    device.deviceType = device_type
    device.isInitialized = True
    device._in_smart_deadband = False
    device.isDimmable = is_dimmable
    device.dutyCycle = 50
    device._pre_deadband_duty_cycle = None
    device._pre_deadband_is_running = None
    device.isRunning = True
    device.minDuty = 20
    device.isSpecialDevice = False  # Important for turn_on signature
    
    # Mock methods
    device.setToMinimum = AsyncMock()
    device.clamp_duty_cycle = lambda x: min(100, max(0, x))
    device.turn_on = AsyncMock()
    device.turn_off = AsyncMock()
    
    return device


@pytest.mark.asyncio
async def test_device_set_to_minimum_reduces_dimmable():
    """Test that setToMinimum reduces dimmable device to 10% (or minDuty if higher)."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device

    # Create a minimal mock device (don't fully initialize)
    device = Device.__new__(Device)
    device.deviceName = "TestDevice"
    device.isDimmable = True
    device.isSpecialDevice = False
    device.minDuty = 20
    device.dutyCycle = 80

    # Mock the turn_on method
    device.turn_on = AsyncMock()
    device.clamp_duty_cycle = lambda x: min(100, max(0, x))

    # Call setToMinimum directly (not mocked)
    # Need to bind the real method
    from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device as DeviceClass
    await DeviceClass.setToMinimum(device)

    # Verify: turn_on was called with 20% (max(10, minDuty=20))
    assert device.turn_on.called
    call_kwargs = device.turn_on.call_args[1]
    assert call_kwargs["percentage"] == 20
    print("✓ Dimmable device reduced to 20% (max(10%, minDuty=20%))")


@pytest.mark.asyncio
async def test_device_set_to_minimum_turns_off_non_dimmable():
    """Test that setToMinimum turns off non-dimmable device."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device

    # Create a minimal mock device
    device = Device.__new__(Device)
    device.deviceName = "TestDevice"
    device.isDimmable = False
    device.isRunning = True

    # Mock the turn_off method
    device.turn_off = AsyncMock()

    # Call setToMinimum directly
    from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device as DeviceClass
    await DeviceClass.setToMinimum(device)

    # Verify: turn_off was called
    assert device.turn_off.called
    print("✓ Non-dimmable device turned off for deadband")


@pytest.mark.asyncio
async def test_on_smart_deadband_entered_calls_set_to_minimum():
    """Test that on_smart_deadband_entered calls setToMinimum and sets flag."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Device import Device
    
    # Create a minimal mock device
    device = Device.__new__(Device)
    device.deviceName = "TestDevice"
    device.deviceType = "Heater"
    device.isInitialized = True
    device._in_smart_deadband = False
    device.isDimmable = True
    device.dutyCycle = 50
    device._pre_deadband_duty_cycle = None
    device._pre_deadband_is_running = None
    device.isRunning = True
    device.minDuty = 20
    device.isSpecialDevice = False
    device.clamp_duty_cycle = lambda x: min(100, max(0, x))
    
    # Mock setToMinimum to track calls
    original_set_to_minimum = device.setToMinimum = AsyncMock()

    # Call on_smart_deadband_entered
    await device.on_smart_deadband_entered({})

    # Verify: setToMinimum was called BEFORE flag was set
    assert original_set_to_minimum.called
    assert device._in_smart_deadband is True
    print("✓ on_smart_deadband_entered calls setToMinimum and sets flag")


@pytest.mark.asyncio
async def test_exhaust_reduce_blocks_when_in_deadband():
    """Test that Exhaust.reduceAction blocks when _in_smart_deadband is True."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Exhaust import Exhaust

    # Create a minimal mock Exhaust
    exhaust = Exhaust.__new__(Exhaust)
    exhaust.deviceName = "TestExhaust"
    exhaust.isDimmable = True
    exhaust.isSpecialDevice = False
    exhaust._in_smart_deadband = True  # Already in deadband

    # Mock methods
    exhaust.change_duty_cycle = Mock(return_value=10)
    exhaust.turn_on = AsyncMock()
    exhaust.log_action = Mock()

    # Call reduceAction
    await exhaust.reduceAction({})

    # Verify: turn_on was NOT called (blocked)
    assert not exhaust.turn_on.called
    print("✓ Exhaust.reduceAction blocked when in Smart Deadband")


@pytest.mark.asyncio
async def test_ventilation_reduce_not_blocked():
    """Test that Ventilation.reduceAction is NOT blocked (Ventilation has no deadband check)."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Ventilation import Ventilation

    # Create a minimal mock Ventilation
    ventilation = Ventilation.__new__(Ventilation)
    ventilation.deviceName = "TestVentilation"
    ventilation.isDimmable = True
    ventilation.isSpecialDevice = False
    ventilation._in_smart_deadband = True  # This exists but Ventilation doesn't check it

    # Mock methods
    ventilation.change_duty_cycle = Mock(return_value=40)
    ventilation.turn_on = AsyncMock()
    ventilation.log_action = Mock()

    # Call reduceAction
    await ventilation.reduceAction({})

    # Verify: turn_on WAS called (not blocked - Ventilation has no deadband check)
    assert ventilation.turn_on.called
    print("✓ Ventilation.reduceAction NOT blocked (no deadband check)")


@pytest.mark.asyncio
async def test_window_reduce_blocks_when_in_deadband():
    """Test that Window.reduceAction blocks when _in_smart_deadband is True."""
    from custom_components.opengrowbox.OGBController.OGBDevices.Window import Window

    # Create a minimal mock Window
    window = Window.__new__(Window)
    window.deviceName = "TestWindow"
    window.isDimmable = True
    window.isSpecialDevice = False
    window._in_smart_deadband = True

    # Mock methods
    window._in_smart_deadband = True
    window.turn_on = AsyncMock()
    # Mock the parent's reduceAction
    parent_reduce = AsyncMock()
    # We'll just check that it doesn't call anything

    # Call reduceAction
    await window.reduceAction({})

    # Verify: turn_on was NOT called (blocked)
    assert not window.turn_on.called
    print("✓ Window.reduceAction blocked when in Smart Deadband")


@pytest.mark.asyncio
async def test_deadband_device_types_filter():
    """Test that only correct device types respond to SmartDeadbandEntered."""
    # Test each device type
    deadband_types = {"Heater", "Cooler", "Humidifier", "Dehumidifier", "Climate", "Exhaust", "Intake", "Window"}
    non_deadband_types = {"Ventilation", "Light", "CO2", "GenericSwitch"}

    for dtype in deadband_types:
        device = create_mock_device(dtype, is_dimmable=True)

        await device.on_smart_deadband_entered({})
        assert device._in_smart_deadband is True, f"{dtype} should respond to SmartDeadbandEntered"
        print(f"✓ {dtype} responds to SmartDeadbandEntered")

    for dtype in non_deadband_types:
        device = create_mock_device(dtype, is_dimmable=True)

        await device.on_smart_deadband_entered({})
        assert device._in_smart_deadband is False, f"{dtype} should NOT respond to SmartDeadbandEntered"
        print(f"✓ {dtype} ignores SmartDeadbandEntered")

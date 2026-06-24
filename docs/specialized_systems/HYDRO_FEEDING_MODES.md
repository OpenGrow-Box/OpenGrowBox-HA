# Hydroponic Feeding Modes

## Overview

The current hydro feeding implementation uses three modes in `OGBTankFeedManager` and `OGBFeedLogicManager`.

## Air Pump Auto-Activation

When the system is in **Hydro mode** (`Hydro.Mode == "Hydro"`), any pump device matching the name patterns `airpump`, `luftpumpe`, `belüfter`, `air_pump`, `air pump`, or `lüfterpumpe` is automatically turned on for reservoir oxygenation.

### How It Works

1. **Device Detection**: During startup, HA entities with matching names are classified as `AirPump` type and stored in `capabilities.canAirPump.devEntities`.
2. **Auto-Activation**: When `Hydro.Mode == "Hydro"` is activated (or on startup if already set), the air pump is turned on via `PumpAction` event.
3. **Auto-Deactivation**: When Hydro mode is changed to OFF, Crop-Steering, Plant-Watering, or Config, the air pump is turned off.
4. **No Configuration Needed**: Detection and activation are fully automatic — no user setters or UI entities required.

### Supported Mode

| Mode | Air Pump State |
|------|---------------|
| **Hydro** | ON (auto-activated) |
| OFF | OFF |
| Crop-Steering | OFF |
| Plant-Watering | OFF |
| Config | OFF |

### Device Naming Examples

Any HA switch entity with these keywords in its entity_id will be auto-detected:

- `switch.airpump_growbox` ✅
- `switch.luftpumpe_reservoir` ✅
- `switch.belüfter_aquarium` ✅
- `switch.air_pump_main` ✅
- `switch.water_pump` ❌ (not an air pump)

### Monitoring

Air pump state (on/off) is tracked through the standard HA switch entity. Power consumption estimation is available via `OGBEnergyManager` with a default of 5.0W for `airpump`. No additional monitoring entities are required.

## Modes

### Disabled

- Automatic nutrient dosing is off
- Automatic pH correction is off
- This is the safe default
- Invalid mode values also fall back to `Disabled`

### Automatic

- Sensor values are evaluated automatically
- Nutrient dosing is calculated from configured targets, concentrations, and reservoir volume
- pH up/down dosing is triggered when needed
- Label-based pump discovery is used first, with fallback resolution where needed

### Config

- No active nutrient or pH dosing
- Used to keep UI configuration available without automatic control
- Intended for editing targets, concentrations, flow rates, and calibration values safely

## Removed Mode

### Own-Plan

`Own-Plan` is no longer an active execution mode.

Reason:

- it was not implemented end-to-end
- it created unsafe ambiguity in feeding behavior
- `Config` now covers the non-active UI/configuration use case

## Pump Flow Rate = 0

For feed and pH pumps, a flow rate of `0` means the pump is disabled.

This applies to:

- `A`
- `B`
- `C`
- `X`
- `Y`
- `PH_DOWN`
- `PH_UP`

Behavior:

- the pump is skipped during dosing
- no runtime is calculated
- no pump activation event is sent for that pump

This does not affect reservoir refill pumps.

## Safety Notes

- Unknown feed modes default to `Disabled`
- `Config` keeps UI control available without dosing
- Disabled feed/pH pumps are controlled by setting flow rate to `0`

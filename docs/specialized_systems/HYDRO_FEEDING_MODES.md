# Hydroponic Feeding Modes

## Overview

The current hydro feeding implementation uses three modes in `OGBTankFeedManager` and `OGBFeedLogicManager`.

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

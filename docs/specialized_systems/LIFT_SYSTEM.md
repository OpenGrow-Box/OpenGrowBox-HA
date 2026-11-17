# Plant Lift System

## Overview

The **Plant Lift** is a motorised actuator that raises or lowers the lamp above the
canopy. OpenGrowBox exposes it as the `Lift` device type with the `canLift`
capability and drives it through the standard action pipeline
(`Increase Lift` / `Reduce Lift`).

The measured lamp-to-canopy distance is provided by a separate ultrasonic
**plant distance** sensor (`plantdist`), which is stored in the data store and
broadcast as a `PlantDistanceUpdate` event.

---

## Components

| Part | Device type | Label(s) | Purpose |
|------|-------------|----------|---------|
| Lift actuator | `Lift` | `lift`, `hublift`, `lamp_lift`, `lamplift` | Moves the lamp up/down |
| Plant distance sensor | `Sensor` | `plantdist`, `plant_distance`, `distance`, `ultrasonic`, `abstand`, … | Reports lamp-to-canopy distance |

### Capability

| Capability | Device types | Events emitted |
|------------|--------------|----------------|
| `canLift` | `lift` | `Increase Lift`, `Reduce Lift` |

---

## Position Model

The lift position is a percentage:

| Position | Meaning |
|----------|---------|
| `0 %` | Lamp fully **lowered** (closest to the canopy) |
| `100 %` | Lamp fully **raised** (maximum distance to the canopy) |

- `Increase Lift` → raises the lamp (position **+** step)
- `Reduce Lift` → lowers the lamp (position **−** step)

The default step size is `5 %`. Bounds, step and all closed-loop/safety
parameters can be configured through `DeviceMinMax.Lift` (read-only from the
lift's own namespace — no shared MinMax code is touched):

```json
{
  "minPosition": 0,
  "maxPosition": 100,
  "positionStep": 5,
  "settleTimeSec": 2,
  "pulseDurationSec": 1,
  "toleranceCM": 1,
  "minDistanceCM": 0,
  "maxDistanceCM": 0,
  "fullTravelTimeSec": 30,
  "maxRuntimeSec": 60,
  "maxIterations": 20,
  "noProgressLimit": 3,
  "sensorMaxAgeSec": 0
}
```

`minDuty` / `maxDuty` / `step` are accepted as fallbacks. All keys are optional
and fall back to the class defaults.

### Setting values at runtime

There is no dedicated number/setter entity for the lift. Configure the values
through the **OGB console** with the `lift` command (writes to
`DeviceMinMax.Lift`; applied on the next action, no restart or reload):

```bash
lift                          # show current configuration (with defaults)
lift maxDistanceCM 150        # soft upper bound
lift minDistanceCM 20         # lower bound (lamp-in-canopy guard)
lift settleTimeSec 2
lift reset                    # back to defaults
```

See [Commands → lift](../COMMANDS.md#lift--lift-actuator-configuration) for the
full key reference.

---

## Closed-Loop Movement (Step & Check)

The lift does not rely on an exact `mm/s` calibration. Instead it moves
**closed loop** against the plant distance sensor:

1. read the current distance (`tentData.plantDistance`, normalised to cm)
2. if within `toleranceCM` of the target → done
3. otherwise move **one step** towards the target
4. wait `settleTimeSec` for the motor and the measurement to settle
5. read the distance again → continue or stop

This is used by `Set Lift` when a distance target is supplied
(`{"distance": 40}` / `{"target_distance": 40}`). `Increase Lift` /
`Reduce Lift` perform a single step in the given direction.

### Safety stops

The loop stops immediately when any of these is true:

| Condition | Parameter |
|-----------|-----------|
| Target reached | `toleranceCM` |
| Lamp reached the minimum distance to the canopy | `minDistanceCM` |
| Lamp reached the maximum distance to the canopy | `maxDistanceCM` |
| Hard runtime cap for one move exceeded | `maxRuntimeSec` |
| Step budget exhausted | `maxIterations` |
| Distance stopped changing (stall / endstop) | `noProgressLimit` |
| Sensor lost or stale | `sensorMaxAgeSec` |

`reduceAction` additionally refuses to lower at all when the lamp is already at
or below `minDistanceCM`, and `increaseAction` refuses to raise when the lamp is
at or above `maxDistanceCM`.

---

## Upper Bound / Max Height

The plant distance sensor is mounted on the lamp and points **down** at the
canopy. Raising the lamp *increases* the measured distance, so the very same
sensor bounds both ends:

| Bound | Meaning | Parameter |
|-------|---------|-----------|
| Lower (max depth) | Lamp in the canopy | `minDistanceCM` |
| Upper (max height) | Lamp too far from the canopy | `maxDistanceCM` |

`maxDistanceCM` is a **soft** (light-distance) bound. The **hard** mechanical
bound is the actuator itself:

- `maxPosition` is always clamped in `set_position`, and a `cover` has its own
  endstop. Use an **absolute** move (`Set Lift {"value": 100}`) to park the lamp
  at the very top — absolute moves are intentionally *not* limited by
  `maxDistanceCM`.
- Relative moves (`Increase Lift` / `Reduce Lift`) and closed-loop distance
  moves **are** limited by `maxDistanceCM`.

Because small tents never exceed the sensor range, the distance is always
available and `None` is never used as a "too high" signal. Should the sensor
report nothing (e.g. during startup), the mechanical endstop remains the guard.

Recommendation: set `maxDistanceCM` to the largest distance you still consider
acceptable for light coverage (e.g. `120`–`150` cm in a small tent). Default `0`
disables the soft upper bound.


---

## Actuator Resolution

For each lift device, OpenGrowBox resolves the output entity in this order:

1. **`cover.*`** → `cover.set_cover_position` with the clamped position
2. **`number.*`** → `number.set_value` with the clamped position
3. **`switch.*` pair** (up/down relay, no feedback) → pulsed for
   `pulseDurationSec`, direction chosen by the target

Relays are matched by name (`up` / `down` / `raise` / `lower` / `open` / `close`
/ `auf` / `ab` / …); if no keyword matches, the first two switches are treated
as `up` then `down`. A single relay cannot express a direction and is ignored.
Because relays have no position feedback, only distance targets
(`Set Lift` with `distance`) can be driven safely on relay hardware.

If no actuator exists, the move is skipped with a warning. Entities are
discovered from the device's own entity list, so the actuator only needs the
`lift` label.

Before moving, OpenGrowBox:

- clamps the target to `[minPosition, maxPosition]`
- skips the command if the target equals the current position
- skips the command if the device is offline/unavailable
- applies a short control lock to avoid HA state races

---

## Events

| Event | Payload | Description |
|-------|---------|-------------|
| `Increase Lift` | – | Raise lamp by one step |
| `Reduce Lift` | – | Lower lamp by one step |
| `Set Lift` | `{"value": <position>}` | Move to an absolute position (%) |
| `Set Lift` | `{"distance": <cm>}` | Closed-loop move to a target distance |
| `PlantDistanceUpdate` | `{"entity_id", "value", "unit", "room", "timestamp"}` | New distance reading |

`Increase Lift` / `Reduce Lift` are triggered automatically by the action
pipeline whenever a mode requests the `canLift` capability.

---

## Plant Distance Sensor

The distance sensor is resolved by the sensor-identification layer. Recommended
short label: **`plantdist`**.

Recognised labels / entity name tokens include:

`plantdist`, `plant_dist`, `plantdistance`, `plant_distance`, `planttolight`,
`plantdistancetolight`, `lamp_distance`, `lampdist`, `canopy_distance`,
`hangheight`, `distance`, `distanz`, `abstand`, `ultrasonic`, `ultraschall`.

The sensor is kept in its own `other` context (it never pollutes the air/VPD
context) and uses unit `cm`, device class `distance`.

Data store keys:

| Key | Description |
|-----|-------------|
| `tentData.plantDistance` | Last distance value |
| `tentData.plantDistanceUnit` | Unit reported by the sensor (`cm`/`m`/…) |
| `tentData.plantDistanceLastUpdate` | ISO timestamp of the last update |

---

## Configuration Example

```json
[
  {
    "name": "Lamp Lift",
    "entity_id": "cover.lamp_lift",
    "deviceType": "Lift",
    "labels": [{"name": "lift"}]
  },
  {
    "name": "Plant Distance",
    "entity_id": "sensor.plantdist",
    "deviceType": "Sensor",
    "labels": [{"name": "plantdist"}]
  }
]
```

---

## Troubleshooting

### Lift does not move

1. **Check the label:** the actuator device needs a `lift` / `hublift` /
   `lamp_lift` / `lamplift` label.
2. **Check the output entity:** the device must expose a `cover.*` or `number.*`
   entity, or a `switch.*` **up/down pair**.
3. **Check bounds:** verify `DeviceMinMax.Lift` (`minPosition` < `maxPosition`).
4. **Check logs:** look for `No cover/number entity found`, `Lift offline` or
   `Failed to set lift position`.

### Lift refuses to lower

1. **Minimum distance:** the lamp is at/below `minDistanceCM` — the safety guard
   blocks further lowering. Adjust `minDistanceCM` if appropriate.
2. **Check logs:** look for `refusing to lower`.

### Lift refuses to raise

1. **Maximum distance:** the lamp is at/above `maxDistanceCM` — adjust
   `maxDistanceCM`, or use an absolute `Set Lift {"value": 100}` to park at the
   mechanical top.
2. **Check logs:** look for `refusing to raise`.

### Closed-loop distance move does not run

1. **No fresh reading:** `tentData.plantDistance` is missing or older than
   `sensorMaxAgeSec`. Check the `plantdist` sensor.
2. **Check logs:** look for `cannot move closed loop` / `Plant distance lost`.

### Distance sensor not recognized

1. **Check the label / name:** use `plantdist` (or another recognised token
   above).
2. **Check the value:** the entity must report a numeric distance.
3. **Check logs:** look for `Plant distance updated` and the resolved
   `plant_distance` sensor type in the sensor map.

---

**Last Updated:** 2026
**Status:** Beta / Testable — closed loop against the plant distance sensor is
new and needs field validation. Verify `minDistanceCM` / `maxDistanceCM`
behaviour carefully before relying on it unattended.

# Crop Steering System - Advanced Irrigation Management

## Overview

The Crop Steering system is an advanced irrigation management system that uses soil moisture sensors (Volumetric Water Content - VWC) to provide intelligent, automated watering based on plant needs, growth stages, and environmental conditions. It replaces traditional timer-based irrigation with sensor-driven, precision watering.

## System Architecture

### Core Components

#### 1. OGBCSManager (Main Controller)
```python
class OGBCSManager:
    """Main crop steering controller coordinating all subsystems."""
```

#### 2. OGBCSConfigurationManager (Settings)
```python
class OGBCSConfigurationManager:
    """Manages crop steering configuration and presets."""
```

#### 3. OGBCSIrrigationManager (Watering Logic)
```python
class OGBCSIrrigationManager:
    """Handles irrigation scheduling and execution."""
```

#### 4. OGBCSPhaseManager (Plant Stages)
```python
class OGBCSPhaseManager:
    """Parses modes, determines initial phase, logs/emits transitions."""
    # Phase handlers are placeholders — real transition logic lives
    # inline in OGBCSManager._handle_phase_pX_auto()
```

#### 5. OGBCSCalibrationManager (Sensor Calibration)
```python
class OGBCSCalibrationManager:
    """Handles VWC sensor calibration and accuracy."""
```

#### 6. OGBAdvancedSensor (TDR Processing)
```python
class OGBAdvancedSensor:
    """Advanced sensor processing with TDR-style calculations."""
```

**Features:**
- Medium-specific VWC polynomial calibration (Teros-12 soilless)
- Pore water EC calculation (Hilhorst model + mass-balance hybrid)
- Temperature normalization for EC readings
- Validation and anomaly detection

## Crop Steering Modes

The CropSteering mode is selected via the mode entity (`select.ogb_cropsteering_mode_*`).
Available options:

| Select option | Meaning |
|---|---|
| `Automatic` | Fully sensor-driven 4-phase system (preset-driven, see §3) |
| `Manual-Transition` | Manual thresholds, but automatic light/VWC phase transitions + auto-calibration (the former single Manual behavior) |
| `Manual` | Pure manual: user-selected phase is kept, scheduled shots only, no auto-transitions, no auto-calibration; failsafes warn (see §4) |
| `Config` | Pre-configuration without activation (see §2) |
| `Disabled` | Safety mode, all automation disabled (see §1) |

### 1. Disabled Mode
- **Purpose**: Safety mode, all automation disabled
- **Watering**: Manual or external systems only
- **Use Case**: Maintenance, troubleshooting, manual control
- **Behavior on Switch**:
  - All running tasks are **immediately cancelled**
  - All drippers are turned OFF
  - P1 state tracking is **reset** (irrigation count, last VWC, last irrigation time)
  - Switching back to Automatic will start **fresh** (no waiting for old interval)

### 2. Config Mode
- **Purpose**: Pre-configuration without activation
- **Watering**: None - only settings are adjusted
- **Use Case**: Setting up parameters before going live
- **Behavior on Switch**:
  - All running tasks are **immediately cancelled**
  - All drippers are turned OFF
  - P1 state tracking is **reset** (same as Disabled)
  - User can safely adjust Duration, Interval, Shot Sum, VWC targets
  - Changes are saved to DataStore immediately and used on next Automatic start

### 3. Automatic Mode (Phase-Based)
- **Logic**: Sensor-driven, light-aware 4-phase system
- **Factors**: VWC, EC, light status, growth phase
- **Phases**: P0 (Monitor) → P1 (Saturate) → P2 (Maintain) → P3 (Dryback)
- **Use Case**: Optimal plant health with full automation

#### Automatic Mode Preset Hierarchy

In **Automatic** mode the active values are built from **presets only**. User settings entered via the HA number entities are **ignored** for control decisions (they are still used by Manual mode).

| Layer | Source | Affected Parameters | Can user override? |
|---|---|---|---|
| **1. Base Presets** | Hardcoded rockwool defaults in `OGBCSConfigurationManager.get_raw_base_presets()` | VWC/EC targets, limits, timing defaults | No |
| **2. Medium Adjustments** | `_medium_adjustments` table (rockwool/coco/soil/perlite/...) | `VWCMin`, `VWCMax`, `VWCTarget`, `ECTarget`, `MinEC`, `MaxEC` (offset only) | No – chosen by medium type |
| **3. Plant Phase Adjustments** | `get_phase_growth_adjustments(plant_phase, generative_week)` | VWC modifier, dryback modifier, EC modifier | No – driven by `plantStage` and week |
| **4. Dynamic Learning** | Observed max saturation, field capacity, night dryback minimum | Hard caps for flood/dryout guards and P2 target adaptation | No – learned from sensor data |

The system learns from the actual sensor behaviour and clamps all thresholds to safe ranges:
- `max_saturation_vwc` – highest VWC ever observed after P1 irrigation (monotonic) — the **capacity ceiling**: P1/P2 never target above it (no headroom, so the system works with capacity, never drives toward full saturation)
- `field_capacity_vwc` – highest stable post-irrigation plateau (ratchets up only) — day-to-day **capacity reference**, also part of the P2 ceiling
- `min_dryback_vwc` – lowest VWC observed during P3 night dryback
- `p1_peak_vwc` – achieved P1 saturation peak (EMA), becomes the **day target**
- `next_ec_target` – P3 dryback-based EC adjustment, effective on the **next day's** P1
- `p2_introduced` – whether P2 maintenance has been introduced ('auto' mode, once the daily
  dryback rate exceeds the threshold) — persisted like the other learned values

The effective capacity used by P1 (target cap) and P2 (dryback trigger + refill) is
`min(preset VWCMax, max_saturation_vwc, field_capacity_vwc)` — the medium is refilled to the
achievable capacity, **not** to full saturation (which would just produce runoff).

Absolute safety limits: **VWC never < 5% and never > 90%**.

**Base Presets (rockwool defaults):**

| Phase | Description | VWCTarget | VWCMin | VWCMax | ECTarget | Default Duration | Default Interval | Default Max Shots | Dryback % |
|---|---|---|---|---|---|---|---|---|---|
| **P0** | Monitoring | 58.0 | 55.0 | 65.0 | 2.0 | - | - | - | - |
| **P1** | Saturation | 68.0 | 55.0 | 70.0 | 1.8 | 45 s | 15 min | 10 | - |
| **P2** | Maintenance | 65.0 | 62.0 | 68.0 | 2.0 | 20 s | 60 s | 10 | 10% of VWCMax |
| **P3** | Night Dryback | 60.0 | 52.0 | 68.0 | 2.2 | 15 s | 5 min | 5 | - |

#### Automatic Mode Failsafe Guards

All guards immediately stop irrigation and send a **critical push notification** via `OGBNotificator`:

| Guard | Trigger | Action |
|---|---|---|
| **Flood Guard** | VWC ≥ 90% | Stop all irrigation, notify |
| **Dryout Guard** | VWC ≤ 5% | Stop all irrigation, notify (do not add more water until sensor is checked) |
| **Sensor Stuck** | VWC unchanged for 15 consecutive readings | Stop irrigation, notify |
| **Sensor Jump** | VWC change > 30% between two readings | Discard reading, notify |
| **Ineffective Irrigation** | 3+ shots without VWC rise ≥ 0.5% | Stop irrigation, notify (pump/empty/sensor issue) |
| **Max Runtime** | Total pump runtime per cycle ≥ 5 min | Stop irrigation, notify |

These guards are active in **Automatic** mode (hard stop) and in **Manual-Transition** mode.
In pure **Manual** mode the non-critical guards warn instead of block (see §4).


### 4. Manual Mode (Manual vs Manual-Transition)
Manual mode comes in two flavours, selected via the CropSteering mode entity:

- **Manual** (pure, default): The user-selected phase is **kept**. No automatic phase transitions
  and no auto-calibration (VWCMax/VWCMin). Irrigation only happens per the user's own timing
  settings (`Shot_Duration_Sec`, `Shot_Intervall`, `Shot_Sum`) for the active phase. P0/P3 are
  intentionally "dry" phases; pick P1/P2 to get scheduled shots. The shot counter resets after
  a full cycle so P1 keeps irrigating instead of completing itself.
- **Manual-Transition**: The previous behavior - the cycle still performs **automatic phase
  transitions** based on light status and VWC conditions, just like Automatic mode (P1/P2 → P3
  at lights-off, P3 → P0 at lights-on, P0 → P1 below VWCMin, P1 → P2 at target/cap/shot-count,
  plus auto-calibration of VWCMax/VWCMin).

- **Control**: Both flavours use **all user-configured settings** (VWC/EC targets, limits,
  timing) from `CropSteering.Substrate.{phase}.*` paths — **not** the automatic presets or
  plant-stage adjustments
- **Use Case**: Pure Manual for full control / testing (the user decides everything, including
  when to irrigate); Manual-Transition for "manual thresholds but safe light-based phase flow"
- **Phase Change**: Manual phase changes via the CropPhase selector are immediately signalled to
  the running manual cycle (`CSManualPhaseChanged` event), so the new phase starts without
  waiting for the previous cycle's sleep/irrigation to finish

> **Failsafes in Manual**: non-critical guards (sensor invalid/stuck, dryout, ineffective
> irrigation) **warn** (one notification per reason, rate-limited) but do **not** block manual
> irrigation — the user is in control. Only the hardware-critical guards (`flood_guard` VWC ≥ 90%,
> `max_runtime` pump cap) still hard-stop in Manual mode.

#### Manual Mode Phase Selection (v3.3)

Manual mode now correctly extracts the phase from the CropPhase selector:

```python
def _extract_phase_from_mode(self, mode: CSMode) -> str:
    """Extract phase identifier from Manual mode enum.

    Handles:
    - Enum value: "Manual-p1" -> "p1"
    - Enum name: "MANUAL_P1" -> "p1"
    """
    mode_value = mode.value
    if "-" in mode_value:
        return mode_value.split("-")[1].lower()

    mode_name = mode.name
    if "_" in mode_name:
        phase = mode_name.split("_")[-1].lower()
        if phase in ["p0", "p1", "p2", "p3"]:
            return phase

    return "p0"  # Default fallback

def _extract_phase_from_value(self, value: str) -> str:
    """Extract phase from stored value (e.g., "P1" -> "p1").

    Handles uppercase, lowercase, and numeric inputs.
    """
    if not value:
        return "p0"

    value_lower = value.lower()
    if value_lower in ["p0", "p1", "p2", "p3"]:
        return value_lower

    # Try extracting from end of string
    if len(value_lower) >= 2:
        possible_phase = value_lower[-2:]
        if possible_phase in ["p0", "p1", "p2", "p3"]:
            return possible_phase

    return "p0"
```

**Manual Mode Flow:**
1. User selects a mode in the CropSteering mode selector: `Manual` (pure) or `Manual-Transition`
2. User selects a phase in the CropPhase selector (e.g. "P1")
3. System reads `CropSteering.CropPhase` from DataStore
4. Phase extraction converts "P1" → "p1"
5. `_crop_steering_phase()` emits `CSManualPhaseChanged` event
6. `_run_manual_mode()` cancels the current phase cycle and restarts with the new phase immediately
7. Manual cycle runs with all user settings for that phase
8. In **Manual-Transition** mode the cycle additionally evaluates the same light/VWC triggers as
   Automatic mode and auto-transitions when conditions are met (P1 → P2 at target VWC, P1/P2 → P3
   at lights off, ...). In pure **Manual** mode this step is skipped entirely.

#### Manual Mode Phase Auto-Transitions (Manual-Transition only)

In **Manual-Transition** mode the selected phase is only the starting point. The manager monitors the same safety/light/VWC triggers as Automatic mode and switches phases automatically to protect the plants:

| From | To | Trigger Condition | Notes |
|------|-----|-------------------|-------|
| **P0** | **P1** | Lights ON and VWC < P0 `VWCMin` (inside irrigation window) | Uses user P0 thresholds |
| **P0** | **P3** | Lights OFF | Same as Automatic mode |
| **P1** | **P2** | VWC ≥ P1 `VWCTarget` | User target reached |
| **P1** | **P2** | VWC ≥ P1 `VWCMax` | User max cap reached |
| **P1** | **P2** | Max irrigation attempts (`Shot_Sum`) reached | Same as Automatic mode |
| **P1** | **P3** | Lights OFF or ≤ 2 hours before lights OFF | Prevents night irrigation |
| **P2** | **P3** | Lights OFF or ≤ 1 hour before lights OFF | Same as Automatic mode |
| **P3** | **P0** | Lights ON | New day begins |

**Why this matters**: You can still force a specific phase for testing or troubleshooting, but the system will not keep irrigating at night or stay stuck in a phase that the plant/environment conditions have already left.

## Phase System

### Phase Overview

The CropSteering system operates in 4 phases that follow the natural day/night cycle:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        LIGHT ON (Day)                               │
│                                                                     │
│   P0 (Monitor)  ──VWC drops──▶  P1 (Saturate)  ──target──▶  P2     │
│        │                              │                      │      │
│        │                              │                      │      │
│    VWC OK                        irrigating              maintain   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                        LIGHT OFF (Night)                            │
│                                                                     │
│                         P3 (Night Dryback)                          │
│                                                                     │
│              Monitor dryback, emergency irrigation only             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Phase Definitions

| Phase | Name | Light Status | Purpose | Actions |
|-------|------|--------------|---------|---------|
| **P0** | Monitoring | ON only | Wait for dryback signal | No irrigation, monitor VWC |
| **P1** | Saturation | ON only | Rapid block saturation | Multiple irrigation shots |
| **P2** | Maintenance | ON only | Controlled day dryback from VWCMax, then refill to capacity | Irrigation when dryback % reached |
| **P3** | Night Dryback | OFF only | Controlled dryback overnight | Emergency irrigation only |

### Complete Phase Transition Diagram

```
                              LIGHT ON (Day)
    ┌──────────────────────────────────────────────────────────────┐
    │                                                              │
    │   ┌─────────┐      VWC < VWCMin      ┌─────────┐             │
    │   │         │ ────────────────────► │         │             │
    │   │   P0    │                        │   P1    │             │
    │   │ Monitor │                        │Saturate │             │
    │   │         │ ◄──── VWC >= VWCMin    │         │             │
    │   └────┬────┘       (after P3)       └────┬────┘             │
    │        │                                  │                  │
    │        │ Lights OFF                       │ VWC >= VWCMax    │
    │        │                                  │ OR stagnation    │
    │        │                                  │ OR max_shots     │
    │        │                                  ▼                  │
    │        │                             ┌─────────┐             │
    │        │                             │         │             │
    │        │                             │   P2    │             │
    │        │                             │Maintain │             │
    │        │                             │         │             │
    │        │                             └────┬────┘             │
    │        │                                  │                  │
    │        │                                  │ Lights OFF       │
    │        │                                  │                  │
    └────────┼──────────────────────────────────┼──────────────────┘
             │                                  │
             │         LIGHT OFF (Night)        │
             │                                  │
             ▼                                  ▼
        ┌──────────────────────────────────────────┐
        │                                          │
        │                  P3                      │
        │            Night Dryback                 │
        │                                          │
        │   • Monitor dryback percentage           │
        │   • Emergency irrigation if VWC below    │
        │     emergency level (see P3 details)     │
        │   • Adjust EC based on dryback rate      │
        │                                          │
        └────────────────────┬─────────────────────┘
                             │
                             │ Lights ON
                             ▼
                        Back to P0
```

### Phase Transition Summary Table

| From | To | Trigger Condition |
|------|-----|-------------------|
| P0 | P1 | VWC < VWCMin (dryback detected) |
| P0 | P3 | Lights OFF (no saturation started yet → night dryback) |
| P1 | P2 | VWC >= effective target (`VWCTarget`, clamped by learned max saturation) |
| P1 | P2 | Stagnation detected (VWC not increasing after 3+ shots, VWC >= 25% / preset min) |
| P1 | P2 | Max irrigation attempts reached (`max_cycles`) |
| P1 | P3 | **Abort**: Lights OFF or ≤ 2 h before lights OFF (interrupts saturation → night dryback, **not** P2) |
| P2 | P3 | **Normal path**: ≤ 1 h before lights OFF (pre-night dryback start). **Fallback**: lights OFF directly (e.g. missed check after restart) |
| P3 | P0 | Lights ON (new day begins) |

### Light-Based Phase Transitions

**CRITICAL**: The light status is the PRIMARY factor for phase determination.

#### On Startup
```python
# System determines initial phase based on light status FIRST
if not is_light_on:
    # Night time → Always start in P3
    return "p3"
else:
    # Day time → Check VWC to determine P0, P1, or P2
    if vwc >= vwc_max:
        return "p2"  # Block full → Maintenance
    elif vwc < vwc_min:
        return "p1"  # Block dry → Saturation
    else:
        return "p0"  # Normal → Monitoring
```

#### During Operation
- **Light turns OFF (or within 1 hour of it)** → Any phase (P0, P1, P2) transitions to P3
- **Light turns ON** → P3 transitions back to P0 (monitoring)
- These light-driven transitions apply to **Automatic** mode and **Manual-Transition** mode.
  In pure **Manual** mode the user-selected phase is **kept** — no automatic light/VWC transitions
  happen; the cycle only irrigates per the user's timing settings (see §4).

### Reference Method Comparison

The 4-phase concept follows established crop-steering practice. The table below compares the
commonly published reference approach with the OGB implementation, per phase:

| Aspect | Reference practice | OGB implementation | Status |
|---|---|---|---|
| **P0: irrigation** | No irrigation, pure rest/monitoring | No irrigation, monitoring only | ✅ |
| **P0 → P1 trigger** | Relative dryback % from start-of-night moisture | Absolute: VWC < `VWCMin` | ⚠️ |
| **P0 → P3 (lights off)** | Night dryback starts | Immediate transition to P3 | ✅ |
| **P1: first shot timing** | 1–2 h after lights ON (plant starts transpiring) | P0 monitoring buffer; immediate if critically dry (safety) | ⚠️ |
| **P1: shot size** | Small volume shots (2–6 % of substrate volume) | Time-based: fixed duration (default 45 s) | ⚠️ |
| **P1: shot cadence** | Every 15–30 min | 15 min default (`wait_between`, user-adjustable) | ✅ |
| **P1: saturation stop** | Runoff detection (2–7 % runoff) | VWC target / stagnation / max shots | ⚠️ |
| **P1: peak = day target** | Reached peak VWC becomes day target | Yes — learned `p1_peak_vwc` (EMA) | ✅ |
| **P1: initial soak (veg start)** | One-time pre-soak to field capacity for fresh transplants | Yes — one-shot `InitialSoak` flag, then regular P1/P3 cycle | ✅ |
| **P2: dryback trigger** | Relative dryback from full saturation | Yes: `VWCMax × (1 − Moisture_Dryback/100)` | ✅ |
| **P2: refill** | Refill to full saturation without runoff | Yes: early-stop at `VWCMax` | ✅ |
| **P2: introduction timing** | Weekly decision — early veg runs P1+P3 only, introduce P2 when daily dryback rate > ~25 % | Yes — `P2_Introduction` = auto/enabled/disabled | ✅ |
| **P2: shot size as EC lever** | Adjust shot size to steer EC | Not implemented (time-based shots) | ❌ |
| **P2: weekly EC decision** | Weekly check of runoff EC vs. feed EC | Not implemented | ❌ |
| **P3: dryback control** | Relative dryback % from start-of-night moisture | Yes: `_compute_dryback_percent` | ✅ |
| **P3: target band** | Dryback into the 30–50 % band | Default band 8–12 % (configurable via presets) | ⚠️ |
| **P3: rate of dryback** | Monitor dryback *rate* (too fast / too slow) | Only level-based, no rate tracking | ❌ |
| **P3: irrigation** | No irrigation during night (normally) | None except emergency shots | ✅ |
| **P3: EC adjustment** | Adjust EC based on dryback performance | Yes: `next_ec_target` (1×/night, next-day effective) | ⚠️ |
| **EC fertigation control** | Actually dose to the EC target | TODO — Nutrient-System integration | ❌ |

Legend: ✅ implemented as in the reference · ⚠️ implemented differently/simplified ·
❌ deliberately not implemented (see below).

#### Deliberately Not Implemented

These parts of the reference practice are **intentionally** not implemented, with reasons:

- **Runoff-based stop criterion (2–7 % runoff)** — Detecting runoff needs a runoff sensor or
  precise volume metering. OGB stops saturation on the VWC target, stagnation, or max shot count,
  which is safe without runoff hardware.
- **Volume-based shots (2–6 % of substrate volume)** — No flow meter; shots are duration-based.
- **Shot-size as an EC lever in P2** — Requires per-shot volume control; not available.
- **Rate of dryback monitoring** — Only the dryback *level* is monitored, not its *rate*.
- **Weekly runoff-EC vs. feed-EC decision** — Requires runoff EC measurement; not available.

#### OGB-Specific Extensions

Features that go **beyond** the reference practice:

- **Emergency shots** in P2 and P3 with pre-night buffers (P1 aborts 2 h before lights OFF, P2
  ends 1 h before lights OFF) — the reference relies on runoff detection instead.
- **Absolute safety guards**: flood (VWC ≥ 90 %), dryout (VWC ≤ 5 %), sensor-stuck/jump,
  ineffective-irrigation and max-runtime guards.
- **Immediate saturation** when VWC is critically low, even before the 1–2 h transpiration buffer.
- **Dynamic VWCTarget learning** (`p1_peak_vwc` EMA): the actually achieved P1 peak becomes the
  day target — no hardcoded target needed.
- **P3 EC → `next_ec_target`**: the night's dryback-based EC adjustment is persisted and applied
  as the next day's effective EC target (1× per night, clamped to `[MinEC, MaxEC]`).
- **P2 introduction control** (`P2_Introduction` auto/enabled/disabled): P2 is held back until the
  daily dryback rate exceeds a threshold ('auto'), so early veg can run P1 + P3 only.
- **One-shot Initial Soak** (`InitialSoak`): veg-start pre-soak to full capacity, bypassing the P0
  buffer and any learned peak; auto-disarms after the first successful saturation.
- **Persistence & restart-safety**: all learned/calibration values survive restarts and are
  re-loaded (`p1_peak_vwc`, `next_ec_target`, `p2_introduced`, etc.).

### Phase Details

#### P0: Monitoring Phase
- **Active during**: Lights ON
- **Purpose**: Wait for natural dryback to trigger saturation
- **Trigger to P1**: VWC drops below VWCMin
- **On light OFF**: Transitions to P3

#### P1: Saturation Phase  
- **Active during**: Lights ON only
- **Purpose**: Saturate the growing medium from the night-dryback trough up to the day target, using **small, frequent shots** (2–6% of substrate volume in the reference practice) to avoid channeling — the shots here are time-based (default 45 s).
- **Actions**: Multiple small irrigation shots, spaced **15 min apart by default** (`wait_between`, within the reference 15–30 min band; user-adjustable via `Shot_Intervall`). The first shot normally comes **1–2 h after lights ON** (the P0 monitoring buffer gives the plant time to start transpiring). If VWC is already critically low the system skips the buffer and starts saturating immediately (safety override).
- **Completion Conditions**:
  1. VWC reaches the effective target (`VWCTarget`, clamped by learned `p1_peak_vwc` / `max_saturation_vwc`)
  2. Stagnation detected (VWC not increasing after 3+ shots)
  3. Max irrigation attempts reached (Shot Sum)
- **On light OFF (or ≤ 2 h before lights OFF)**: Aborts, transitions to P3 (night dryback)
- **Auto-calibration**: Updates `VWCMax` when saturation detected; the actually achieved peak is learned as `p1_peak_vwc` (EMA) and becomes the **day target** for the next days — the medium is only saturated as far as the plant actually pulled it.

##### P1 Initial Soak (Veg Start)

When a grow starts (freshly transplanted clones), the reference practice pre-soaks the medium
once to **field capacity** before the regular P1/P3 cycle begins. OGB implements this as a
**one-shot** flag:

- Arm it via `CropSteering.InitialSoak = true` (DataStore / integration) or the console command
  `cs_soak on` — the console works in **any week**, so a full pre-soak can also be triggered later
  (e.g. after transplanting or a restart).
- While armed, P1 **bypasses the P0 transpiration buffer** and saturates to **full container
  capacity** (`VWCMax`, the `min(VWCMax, max_saturation_vwc, field_capacity_vwc)` ceiling),
  ignoring any learned `p1_peak_vwc`.
- After a **successful** soak the flag is cleared automatically (`InitialSoak = false`) and the
  regular P1/P3 cycle resumes the next day. A failed soak (max attempts, VWC far below minimum —
  pump/sensor issue) stays armed so it retries the next day.

##### P1 Stagnation Detection & Calibration Safety

**CRITICAL**: The system includes safety checks to prevent invalid calibration values:

```python
# Stagnation is only accepted as "block full" if VWC >= 25% (or the preset minimum)
min_vwc_for_stagnation = max(25.0, preset_vwc_min)

if stagnation_detected and vwc >= min_vwc_for_stagnation:
    # Valid stagnation - block is actually full
    save_calibration(vwc)
else:
    # VWC too low - something is wrong (sensor, pump, water supply)
    log_warning("VWC stuck at low level - check system!")
    # Continue irrigating, do NOT save bad calibration
```

**Why this matters**: 
- If VWC stagnates at 19% after multiple shots, this indicates a problem (not a full block)
- Without this check, 19% would be saved as VWCMax, causing immediate P1→P2 transitions
- The system now warns about potential issues instead of saving incorrect calibration

#### P2: Day Maintenance Phase
- **Active during**: Lights ON (more than 1 hour before lights OFF)
- **Purpose**: Allow a slight controlled dryback from container capacity, then refill to capacity without drain
- **Actions**:
  - Track container capacity from `VWCMax` (calibrated or preset, clamped to `min(VWCMax, max_saturation_vwc, field_capacity_vwc)`)
  - Wait until VWC drops by the configured dryback percentage (`Moisture_Dryback`, default 10%)
  - Irrigate to `VWCMax` with early-stop safety (`max_vwc = VWCMax`) so no excess drain occurs
  - Repeat until 1 hour before lights OFF
- **Dryback trigger**: `VWCMax × (1 - Moisture_Dryback / 100)`
- **Refill target**: `VWCMax` (container capacity)
- **Safety**: Emergency irrigation triggers if VWC falls below the **emergency level**
  `max(ABS_VWC_MIN, VWCMin × emergency_threshold)` with `emergency_threshold` default **0.5**
  (= 50% of `VWCMin`) — well below the normal dryback trigger, so routine dryback is not treated as an emergency.
- **On light OFF or ≤1h before light OFF**: Transitions to P3

##### P2 Introduction (weekly decision)

The reference practice treats **when to run P2 at all** as a weekly decision: early veg often runs
**P1 + P3 only** (plants are small and transpire slowly); P2 maintenance is introduced once the
daily dryback rate climbs above roughly **25 %**. OGB exposes this via
`CropSteering.P2_Introduction`:

- **`auto`** (default): P2 is skipped until the **daily dryback rate** — measured relative to the
  day's P1 saturation peak (`day_peak_vwc`) — exceeds `CropSteering.P2_Intro_Dryback_Threshold`
  (default **25 %**). From then on `CropSteering.p2_introduced` is `true` (persisted) and P2 runs.
  To restart a grow, reset `p2_introduced = false` (console: `cs_p2 reset`).
- **`enabled`**: P2 always runs after P1 saturation (pre-v3.9 behavior).
- **`disabled`**: P2 never runs — after P1 saturation the system returns to **P0 monitoring**
  (no mid-day refills). VWC is still protected: if it drops below `VWCMin` during the day, P0
  starts another P1 saturation.

Mode, threshold and introduced state can be inspected/changed via the console
(`cs_p2 status`, `cs_p2 mode <auto|enabled|disabled>`, `cs_p2 threshold <n>`, `cs_p2 reset`).

While P2 is not in use, `_determine_initial_phase` also returns P0 instead of P2 at startup.

**Why VWCMax (and not VWCTarget) is used for the refill target**: VWCTarget is only a lower hold level. Refilling to VWCTarget would leave the medium underfilled and cause a constant deficit. VWCMax represents the calibrated container capacity — the level the block reaches after a full irrigation shot. By stopping early at VWCMax we avoid drain while still returning to full capacity.

> **Steering dimensions**: P2 steers watering via **amount** (refill to `VWCMax`) and **shot count**
> per cycle. The reference practice additionally uses **shot size** as an EC lever (reduce shot size
> to raise EC, increase it to lower EC) and a **weekly runoff-EC review**; both are deliberately not
> implemented (see "Deliberately Not Implemented").

#### P3: Night Dryback Phase
- **Active during**: Lights OFF only
- **Purpose**: Allow controlled dryback overnight
- **Actions**:
  - Monitor dryback percentage from `startNightMoisture`
  - Adjust EC target based on dryback rate (Automatic only — **log-only**, see note below)
  - Emergency irrigation if VWC critically low
- **Emergency threshold**: `max(ABS_VWC_MIN, VWCMin × emergency_threshold, learned_min_dryback + 3.0)`
  with `emergency_threshold` default **0.5**. (This is **not** `VWCMax × 0.85` — that formula was removed in v3.5.)
- **Emergency shot**: duration = `min(irrigation_duration, 15)` s, max **5 per night**, min **5 min** between shots
- **On light ON**: Transitions to P0

> **EC adjustment during P3 (`next_ec_target`)**: Adjusting the EC *while* P3 runs cannot
> influence the current dryback — the medium is not being fertigated during the night. The
> `_adjust_ec_for_dryback()` call in Automatic P3 therefore computes a **next-day EC target**
> (`CropSteering.Learned.next_ec_target`) instead of changing the running feed:
> - At most **one adjustment per night** (`CropSteering.p3_ec_adjusted` flag, reset on P3 entry/exit)
>   to prevent runaway steps.
> - The new target is **clamped to `[MinEC, MaxEC]`** of the active phase preset.
> - The value is **persisted** and used by the **next day's P1** as its effective `ECTarget`
>   (see `_get_effective_ec_target()`), so the dryback-based EC adjustment takes effect on the
>   next fertigation.
> - It still does **not** dose anything (actual nutrient dosing is a TODO — Nutrient-System integration);
>   only the target value is persisted and shown.

## Plant Growth Phase (veg / flower)

The system determines **veg** or **flower** from the room-level `plantStage` (set via `select.ogb_plantstage_*`):

| Phase | plantStage values |
|-------|------------------|
| **veg** | `Germination`, `Clones`, `EarlyVeg`, `MidVeg`, `LateVeg` |
| **flower** | `EarlyFlower`, `MidFlower`, `LateFlower`, `Flush` |

```python
FLOWER_STAGES = {"EarlyFlower", "MidFlower", "LateFlower", "Flush"}
is_flower = room_stage in FLOWER_STAGES  # → True/False
```

### How it works

1. **User sets `plantStage`** via the select entity in the UI
2. The value is stored as `plantStage` (room-level, DataStore) and synced to all `GrowMedium` objects
3. When `plantStage` changes to a flower stage and no `bloom_switch_date` exists yet, it is **auto-set to `datetime.now()`** — no separate bloom switch date entry needed
4. `isPlantDay.plantPhase` and `isPlantDay.generativeWeek` are synced from `plantStage` for legacy fallback paths
5. Crop Steering reads `plantStage` directly (authoritative source) and applies growth-specific adjustments

### Priority

```
plantStage (room-level, via UI)
  ├── determines veg/flower via FLOWER_STAGES set
  ├── overrides individual medium bloom_switch_date
  └── synced to isPlantDay.plantPhase
```

### Phase-Specific Adjustments

#### Vegetative Phase (Germination … LateVeg)
- **Higher moisture retention** for rapid growth
- **More frequent checks** to prevent drying out
- **Balanced irrigation** to support leaf development

#### Generative Phase / Flowering (EarlyFlower … Flush)
- **Gradually drier conditions** to stress plants for flowering
- **Reduced irrigation frequency** to prevent bud rot
- **Environmental adaptation** based on humidity/temperature

## VWC Sensor Technology

### Volumetric Water Content (VWC)

VWC measures the percentage of water volume in the soil:
- **0%**: Completely dry soil
- **100%**: Saturated soil (not recommended)
- **Optimal Range**: 50-80% depending on plant phase

### Sensor Data Source

Crop Steering reads its VWC, EC and temperature values from the **live `GrowMedium` objects** managed by `OGBMediumManager`. Each medium already aggregates the latest registered sensor readings in its `current_moisture`, `current_ec` and `current_temp` fields.

The Crop Steering manager:
1. Collects the current values from every medium in the room.
2. Ignores missing / zero values (treated as uninitialised).
3. Averages the remaining values.
4. Applies medium-specific VWC calibration and EC unit conversion (µS → mS).
5. Computes pore-water EC and validation.
6. Writes the result to `CropSteering.vwc_current` / `CropSteering.ec_current` for the current cycle.

```python
# Conceptual flow
mediums = self.medium_manager.get_mediums()
vwc_values = [m.current_moisture for m in mediums if m.current_moisture]
avg_vwc = mean(calibrate_vwc(v, medium_type) for v in vwc_values)
self.data_store.setDeep("CropSteering.vwc_current", avg_vwc)
```

If `medium_manager` is unavailable, the system falls back to the legacy `workData.moisture` / `workData.ec` buffers until the medium manager is ready.

### Sensor Calibration

#### VWC Calibration Overview

The CropSteering system requires calibration to understand the VWC (Volumetric Water Content) range of your specific growing medium. There are two types of calibration:

| Type | Purpose | Trigger |
|------|---------|---------|
| **VWC Max** | Find saturation point | `cs_calibrate max` or auto during P1/P2 |
| **VWC Min** | Find safe dryback minimum | `cs_calibrate min` or auto during P3 |

#### Console Commands for Calibration

```bash
# Show current calibration status
cs_status

# Start VWC Maximum calibration (saturation point)
cs_calibrate max
cs_calibrate max p1    # Specific phase

# Start VWC Minimum calibration (dryback monitoring)
cs_calibrate min
cs_calibrate min p2    # Specific phase

# Stop running calibration
cs_calibrate stop
```

#### Example Console Output

```
$ cs_status

🌱 CropSteering Status:
==================================================

📊 Mode: Automatic
   Active Mode: Automatic-Generative
   Active: Yes
   Current Phase: p2

📈 Current Readings:
   VWC: 45.2%
   EC: 2.35 mS/cm

🔧 Calibration Values:
   P1:
      VWC Max: 68.5%
      VWC Min: 32.1%
      Last Cal: 2026-01-03T14:30
   P2: Not calibrated
   P3: Not calibrated

==================================================
💡 Use 'cs_calibrate max' or 'cs_calibrate min' to calibrate
```

#### Console Commands for Initial Soak & P2 Introduction (v3.9)

```bash
# Arm the one-shot Initial Soak - works in ANY week, not just veg start
# (next successful P1 fills the medium to full capacity)
cs_soak on

# Disarm it manually
cs_soak off

# Show current state
cs_soak status

# P2 introduction - control mode
cs_p2 status                              # Show mode, threshold, introduced state
cs_p2 mode auto                           # Default: introduce P2 via weekly dryback rule
cs_p2 mode enabled                        # Always run P2 after P1 (pre-v3.9 behavior)
cs_p2 mode disabled                       # P1 + P3 only (early veg)

# P2 introduction - threshold + reset
cs_p2 threshold 25                        # Daily dryback % that triggers P2 (default 25)
cs_p2 reset                               # Hold P2 back again (clears introduced state)
```

`cs_status` additionally shows the steering settings block:

```
🎛 Steering Settings:
   Initial Soak: ARMED
   P2 Mode: auto (introduced: yes, threshold: 25.0%)
   Today's saturation peak: 68.5%
   Current dryback vs peak: 41.6%     ← how close P2 introduction is (needs ≥ threshold)
   Learned P1 peak: 61.5%
   Next-day EC target: 2.30
```

`cs_p2 status` shows the same dryback line with the "needs ≥ X% in 'auto' mode" note, so you can
directly judge how necessary P2 is right now.

#### Calibration Manager Architecture

```python
class OGBCSCalibrationManager:
    """
    Dedicated calibration manager for VWC sensors.
    
    Handles all calibration procedures with:
    - Sensor stabilization monitoring
    - Multiple reading averaging (6 readings)
    - Timeout handling
    - Storage of calibrated values (runtime; persistence pending)
    - Real irrigation via callback to OGBCSManager._irrigate()
    """

    def __init__(self, room, data_store, event_manager, advanced_sensor,
                 hass=None, irrigate_callback=None):
        # irrigate_callback: async callable(duration_secs)
        #   → passed from OGBCSManager: self._irrigate
        #   → performs actual pump irrigation, not just sleep

    def _get_current_vwc_reading(self) -> Optional[float]:
        # Reads from DataStore: CropSteering.vwc_current
        # (written every cycle by OGBCSManager._automatic_cycle)
        return self.data_store.getDeep("CropSteering.vwc_current")

    async def _irrigate_for_calibration(self, duration: int) -> bool:
        # Uses self._irrigate_callback(duration) if set
        # Falls back to asyncio.sleep(duration) if no callback

    async def _wait_for_vwc_stabilization(self, timeout=300, check_interval=10):
        """
        Wait until VWC reading stabilizes.
        
        Collects up to 6 readings (every check_interval seconds),
        checks if max - min deviation is within self.stability_tolerance.
        """
```

#### Calibration Data Persistence

Calibration values are stored in the runtime DataStore under `CropSteering.Calibration`:

```python
# Storage structure in CropSteering.Calibration
{
    "p1": {
        "VWCMax": 68.5,      # Maximum VWC (saturation point)
        "VWCMin": 32.1,      # Minimum VWC (safe dryback)
        "timestamp": "2026-01-03T14:30:00"
    },
    "p2": { ... },
    "p3": { ... },
    "LastRun": "2026-01-03T14:30:00"
}
```

> **Persistence (since v3.8.1)**: `CropSteering.Calibration` and `CropSteering.Learned` **are now
> persisted** to `ogb_data/ogb_<room>_state.json` and restored on HA restart (`CropSteering` is in
> `PRESERVED_STATE_KEYS`; only the `Calibration` + `Learned` subtrees are written, all runtime state
> such as `Mode`, `shotCounter`, `vwc_current`, `phaseStartTime` is deliberately excluded).

#### Auto-Calibration During Regular Operation

The system automatically calibrates VWC values during normal operation across multiple phases:

> **Mode note**: Auto-calibration runs only in **Automatic** mode and **Manual-Transition** mode.
> In pure **Manual** mode there is **no** auto-calibration — the user is in control and must
> calibrate manually via `cs_calibrate max` / `cs_calibrate min`.

| Phase | Value | Method | Conditions |
|-------|-------|--------|------------|
| **P1** | VWCMax | Stagnation detection | VWC stops increasing after 3+ shots, VWC >= 25% |
| **P2** | VWCMax | Post-irrigation peak tracking | 3+ consistent peaks within 2% tolerance, 3+ cycles |
| **P3** | VWCMin | Night dryback minimum | 2+ consistent night minima within 2% tolerance |

##### P1: VWC Max via Stagnation Detection

During the P1 (Saturation) phase, the system automatically calibrates VWCMax when:
- VWC stops increasing after irrigation (stagnation detected, VWC >= 25%)
- Maximum irrigation attempts reached at a reasonable VWC level

This is a "passive" calibration that happens as part of normal operation.

##### P2: VWC Max via Post-Irrigation Peak Tracking

During P2 (Maintenance), each irrigation shot raises VWC. The system tracks the peak VWC reached after each shot. When the same peak is observed consistently across multiple cycles, it is saved as the calibrated VWCMax for P2.

```python
# Peak tracking logic
peaks = []  # Rolling list of post-irrigation VWC peaks (max 5)
if len(peaks) >= 3 and irrigation_count >= 3:
    if max(peaks) - min(peaks) <= 2.0:  # Within 2% tolerance
        save_calibration("p2", "VWCMax", avg(peaks))
```

**This means**: If your maintenance irrigation consistently brings the medium to ~72% VWC after each shot across 3 irrigation cycles, P2 VWCMax is automatically set to 72%.

##### P3: VWC Min via Night Dryback Observation

At the end of each night (P3→P0 transition), the current VWC represents the natural dryback minimum. The system tracks this value across multiple nights. When consistent minima are observed, VWCMin is auto-calibrated.

```python
# Night minimum tracking
night_mins = []  # Rolling list of end-of-night VWC values (max 5)
if len(night_mins) >= 2:
    if max(night_mins) - min(night_mins) <= 2.0:  # Within 2% tolerance
        save_calibration("p3", "VWCMin", avg(night_mins))
```

**This means**: If the medium naturally dries back to ~38% for two consecutive nights, P3 VWCMin is automatically set to 38%.

#### First-Start Calibration Indicator

On first startup (or when no calibrations exist for a phase), the system emits a `CSWARNING` log message once per day listing all phases that need calibration:

```
⚠ Calibration needed: P1, P2, P3 — run VWC calibration cycle
```

This runs once per day to avoid spamming the logs.

#### Periodic Re-Calibration Reminder

When a calibration is older than 4 weeks, the system emits a reminder:

```
⚠ Re-calibration recommended: P1 (32d old), P2 (45d old) — older than 4 weeks
```

This encourages users to keep calibrations current as the growing medium and root structure change over time.

#### Advanced Sensor Processing

```python
class OGBAdvancedSensor:
    """TDR-style sensor processing with polynomial calibration."""

    def calculate_vwc(self, raw_reading: float, medium_type: str) -> float:
        """Calculate VWC using polynomial calibration."""
        # Apply medium-specific polynomial
        # coeffs = self.get_medium_calibration(medium_type)
        # vwc = coeffs[0]*R^3 + coeffs[1]*R^2 + coeffs[2]*R + coeffs[3]

    def calculate_pore_ec(self, bulk_ec: float, vwc: float, temp: float, medium_type: str) -> float:
        """Calculate pore water EC using hybrid model."""
        # Hilhorst model + mass-balance correction
        # Temperature normalization
        # Medium-specific adjustments

    def validate_readings(self, vwc: float, bulk_ec: float, pore_ec: float, temp: float, medium_type: str):
        """Validate sensor readings for reasonableness."""
        # Range checking
        # Rate of change validation
        # Cross-correlation between sensors
        # Anomaly detection
```

### Medium-Specific Calibrations

```python
# Pre-defined calibrations for each medium type
VWC_CALIBRATIONS = {
    "rockwool": {
        "polynomial_coeffs": (6.771e-10, -5.105e-6, 1.302e-2, -10.848),
        "offset": 0.0,
        "scale": 1.0,
        "valid_range": (0.20, 0.80)
    },
    "coco": {
        "polynomial_coeffs": (6.771e-10, -5.105e-6, 1.302e-2, -10.848),
        "offset": 5.0,  # +5% for higher bound water
        "scale": 1.0,
        "valid_range": (0.25, 0.85)
    },
    "soil": {
        "polynomial_coeffs": (4.824e-10, -3.478e-6, 8.502e-3, -7.082),
        "offset": -8.0, # -8% for lower available water
        "scale": 1.0,
        "valid_range": (0.15, 0.75)
    }
}
```

## Irrigation Logic

### Irrigation Triggers

#### 1. VWC Threshold Crossing
```python
def should_irrigate_vwc(self) -> bool:
    """Check if irrigation needed based on VWC levels."""
    current_vwc = self.get_average_vwc()
    vwc_min = self.get_phase_vwc_min()

    return current_vwc < vwc_min
```

#### 2. Time-Based Safety Irrigation
```python
def should_irrigate_safety(self) -> bool:
    """Safety irrigation to prevent complete drying."""
    time_since_last_irrigation = datetime.now() - self.last_irrigation_time
    max_dry_time = timedelta(hours=self.get_max_dry_hours())

    return time_since_last_irrigation > max_dry_time
```

#### 3. Environmental Adaptation
```python
def calculate_environmental_adjustment(self) -> float:
    """Adjust irrigation based on environmental conditions."""
    temperature = self.get_current_temperature()
    humidity = self.get_current_humidity()

    # Higher temperature = more evaporation = more irrigation needed
    temp_factor = (temperature - 20) * 0.02  # 2% more water per °C above 20

    # Lower humidity = more evaporation = more irrigation needed
    humidity_factor = (60 - humidity) * 0.005  # 0.5% more water per % below 60

    return temp_factor + humidity_factor
```

### Irrigation Execution

#### Smart Irrigation Algorithm
```python
async def irrigate(self, duration: int = 30, is_emergency: bool = False) -> bool:
    """Execute intelligent irrigation cycle."""

    # Get available drippers
    drippers = self.get_drippers()
    if not drippers:
        _LOGGER.error(f"{self.room} - No drippers available")
        return False

    # Validate duration
    duration = max(10, min(300, duration))  # 10s to 5min safety limits

    try:
        # Turn on all drippers
        for dripper in drippers:
            entity_id = dripper.get("entity_id")
            if entity_id:
                await self.event_manager.emit("PumpAction", {
                    "Name": self.room,
                    "Action": "on",
                    "Device": entity_id,
                    "Cycle": False
                })

        # Log irrigation start
        await self.event_manager.emit("LogForClient", {
            "Name": self.room,
            "Type": "CSLOG",
            "Message": f"Irrigation started ({duration}s)"
        }, haEvent=True)

        # Wait for irrigation duration
        await asyncio.sleep(duration)

        # Turn off all drippers
        for dripper in drippers:
            entity_id = dripper.get("entity_id")
            if entity_id:
                await self.event_manager.emit("PumpAction", {
                    "Name": self.room,
                    "Action": "off",
                    "Device": entity_id,
                    "Cycle": False
                })

        # Emit AI learning event
        await self.event_manager.emit("CSIrrigation", {
            "room": self.room,
            "duration": duration,
            "is_emergency": is_emergency
        })

        return True

    except Exception as e:
        _LOGGER.error(f"{self.room} - Irrigation error: {e}")
        # Emergency stop all drippers
        await self._emergency_stop_drippers()
        return False
```

#### Dripper Management
```python
def _get_drippers(self):
    """Get valid dripper devices from canPump capability.

    Filter returns only devices that contain 'dripper' keyword in name.
    This excludes cloner pumps and other non-irrigation pumps.
    """
    dripperDevices = self.data_store.getDeep("capabilities.canPump")
    if not dripperDevices:
        _LOGGER.warning(f"{self.room} - _get_drippers: No canPump capability found!")
        return []

    devices = dripperDevices.get("devEntities", [])
    if not devices:
        _LOGGER.warning(f"{self.room} - _get_drippers: No pump devices found!")
        return []

    valid_keywords = ["dripper"]

    dripper_devices = [
        dev for dev in devices
        if any(keyword in dev.lower() for keyword in valid_keywords)
    ]

    if not dripper_devices:
        _LOGGER.warning(f"{self.room} - _get_drippers: No dripper devices found in: {devices}")

    _LOGGER.warning(f"{self.room} - _get_drippers: Returning {len(dripper_devices)} dripper(s): {dripper_devices}")
    return dripper_devices
```

## Medium-Specific Logic

### Medium Types and Properties

```python
MEDIUM_PROPERTIES = {
    "rockwool": {
        "drainage_rate": 0.8,      # Fast drainage
        "water_retention": 0.6,    # Moderate retention
        "optimal_vwc_range": [0.6, 0.8],
        "irrigation_frequency": "moderate",
        "calibration_offset": 0.05
    },
    "coco": {
        "drainage_rate": 0.6,      # Moderate drainage
        "water_retention": 0.8,    # Good retention
        "optimal_vwc_range": [0.65, 0.85],
        "irrigation_frequency": "moderate",
        "calibration_offset": 0.03
    },
    "soil": {
        "drainage_rate": 0.4,      # Slow drainage
        "water_retention": 0.9,    # High retention
        "optimal_vwc_range": [0.5, 0.75],
        "irrigation_frequency": "low",
        "calibration_offset": 0.1
    },
    "hydroponic": {
        "drainage_rate": 1.0,      # Instant drainage
        "water_retention": 0.3,    # Low retention
        "optimal_vwc_range": [0.7, 0.9],
        "irrigation_frequency": "high",
        "calibration_offset": 0.0
    }
}
```

### Medium-Based Adjustments

```python
def apply_medium_adjustments(self, irrigation_params):
    """Adjust irrigation parameters based on growing medium."""

    medium_type = self.get_current_medium_type()
    properties = MEDIUM_PROPERTIES.get(medium_type, MEDIUM_PROPERTIES["rockwool"])

    # Adjust VWC targets
    irrigation_params.vwc_min *= (1 + properties["calibration_offset"])
    irrigation_params.vwc_max *= (1 + properties["calibration_offset"])

    # Adjust irrigation frequency
    if properties["irrigation_frequency"] == "high":
        irrigation_params.check_interval *= 0.7  # Check more often
    elif properties["irrigation_frequency"] == "low":
        irrigation_params.check_interval *= 1.3  # Check less often

    return irrigation_params
```

## Safety and Monitoring

### Over-Irrigation Prevention

```python
def prevent_over_irrigation(self) -> bool:
    """Prevent excessive irrigation that could harm plants."""

    # Check recent irrigation history
    recent_irrigation = self.get_recent_irrigation_volume()

    # Maximum irrigation per hour
    max_hourly = self.get_max_irrigation_per_hour()
    if recent_irrigation > max_hourly:
        _LOGGER.warning(f"Over-irrigation detected: {recent_irrigation}L/hr")
        return False

    # Check for runoff (if sensors available)
    if self.has_runoff_sensor():
        runoff_detected = self.check_runoff_level()
        if runoff_detected:
            _LOGGER.warning("Runoff detected - stopping irrigation")
            return False

    return True
```

### Irrigation Effectiveness Validation

```python
async def _validate_irrigation_effectiveness(self):
    """Validate that irrigation achieved desired VWC increase."""

    # Wait for water to soak in
    await asyncio.sleep(300)  # 5 minutes

    # Check VWC improvement
    pre_vwc = self.pre_irrigation_vwc
    post_vwc = self.get_average_vwc()

    improvement = post_vwc - pre_vwc
    expected_improvement = self.expected_vwc_improvement

    if improvement < (expected_improvement * 0.5):
        _LOGGER.warning(f"Poor irrigation effectiveness: "
                       f"Expected {expected_improvement}%, got {improvement}%")

        # Trigger calibration check
        await self.calibration_manager.schedule_calibration_check()
```

## Configuration and Setup

### User Settings from DataStore

User-configured values are loaded from the DataStore and merged with defaults. 

#### Medium-Based Configuration (Current)

The modular version uses medium-based paths for user settings:

```python
# Shot Duration (irrigation duration in seconds)
CropSteering.Substrate.{phase}.Shot_Duration_Sec  →  irrigation_duration

# Shot Interval (minutes in UI → converted to seconds internally)
CropSteering.Substrate.{phase}.Shot_Intervall     →  wait_between (converted: value * 60)

# Shot Sum (max irrigation attempts)
CropSteering.Substrate.{phase}.Shot_Sum           →  max_cycles

# VWC Targets
CropSteering.Substrate.{phase}.VWC_Target         →  VWCTarget
CropSteering.Substrate.{phase}.VWC_Min            →  VWCMin  
CropSteering.Substrate.{phase}.VWC_Max            →  VWCMax

# EC Targets
CropSteering.Substrate.{phase}.EC_Target          →  ECTarget
CropSteering.Substrate.{phase}.Min_EC             →  MinEC
CropSteering.Substrate.{phase}.Max_EC             →  MaxEC
```

Where `{phase}` is one of: `p0`, `p1`, `p2`, `p3`

#### Home Assistant Entity Names

Entity names follow this pattern:
```
number.ogb_cropsteering_{phase}_{parameter}_{roomname}

Examples:
- number.ogb_cropsteering_p1_shot_duration_veggitent
- number.ogb_cropsteering_p1_shot_intervall_veggitent  
- number.ogb_cropsteering_p1_shot_sum_veggitent
```

#### Value Loading Priority

1. **User Entity Value** (highest priority) - Values set via HA UI entities
2. **Medium Adjustments** - Applied only to VWC/EC thresholds
3. **Default Presets** (lowest priority) - Fallback values per medium type

**IMPORTANT**: User timing values (duration, interval, shot_sum) are used **exactly as configured** - no drainage_factor adjustments are applied to timing parameters.

##### Value Validation

User values are validated by `_is_valid_user_value()` which accepts any parseable number including `0`:
- `EC=0` → allowed (flush with pure water)
- `Shot_Sum=0` → allowed (disables automatic irrigation for that phase)
- `Shot_Duration=0` → allowed (skips irrigation shot)
- `None` / empty string / unparseable → rejected, falls back to default

**Automatic Mode Logic:**
- **Timing**: User settings override defaults; if not set, preset defaults are used
- **VWC/EC Thresholds**: User settings override defaults; base preset values are then adjusted by medium offset + plant phase/week modifier
- **Logging**: Shows both raw user values and the final active thresholds after adjustments

### Medium-Specific Adjustments

The system includes medium-specific adjustments for optimal performance:

```python
MEDIUM_ADJUSTMENTS = {
    "rockwool": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
    "coco": {"vwc_offset": 3, "ec_offset": -0.1, "drainage_factor": 0.9},
    "soil": {"vwc_offset": -5, "ec_offset": 0.2, "drainage_factor": 0.7},
    "perlite": {"vwc_offset": -8, "ec_offset": 0.1, "drainage_factor": 1.2},
    "aero": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
    "water": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0},
    "custom": {"vwc_offset": 0, "ec_offset": 0, "drainage_factor": 1.0}
}
```

These adjustments are applied on top of user settings.

##### Entity Sync

After applying all adjustments (user settings + medium offsets + growth phase adjustments), the final values are written back to the HA number entities every cycle. This ensures the UI always shows the **actual active thresholds**, not just the raw user-configured values.

| Synced parameters | Entity pattern |
|---|---|
| VWCTarget, VWCMin, VWCMax, ECTarget | `number.ogb_cropsteering_{phase}_{param}_{room}` |

### Phase Determination (veg vs flower)

The authoritative source for veg/flower is the room-level `plantStage`:

```python
FLOWER_STAGES = {"EarlyFlower", "MidFlower", "LateFlower", "Flush"}

room_stage = self.data_store.get("plantStage")
if room_stage:
    phase = "flower" if room_stage in FLOWER_STAGES else "veg"
```

### Growth Phase Adjustments

```python
# Vegetative Phase: Promote growth
veg_adjustments = {
    "vwc_modifier": 2.0,      # +2% moisture
    "dryback_modifier": -2.0, # -2% dryback (less stress)
    "ec_modifier": -0.1       # Slightly lower EC
}

# Flowering Phase: Promote flowering
flower_adjustments = {
    "vwc_modifier": -2.0,     # -2% moisture
    "dryback_modifier": 2.0,  # +2% dryback (more stress)
    "ec_modifier": 0.2        # Higher EC
}
```

### Automatic Mode Setup

```python
async def setup_automatic_mode(self):
    """Setup automatic crop steering mode."""

    # Sync medium type
    await self._sync_medium_type()

    # Get plant phase and week (plantStage is authoritative)
    plant_phase, generative_week = self._get_plant_info_from_medium()

    # Apply growth phase adjustments
    adjustments = self.get_phase_growth_adjustments(plant_phase, generative_week)

    # Start automatic cycle
    await self._automatic_cycle()
```

### Sensor Configuration
```python
async def _configure_vwc_sensors(self):
    """Configure VWC sensors for crop steering."""

    # Discover available sensors
    available_sensors = await self._discover_vwc_sensors()

    for sensor in available_sensors:
        # Register sensor
        await self.medium_manager.register_sensor_to_medium(
            sensor["entity_id"], sensor["medium_id"]
        )

        # Configure sensor parameters
        await self._configure_sensor_parameters(sensor)

        # Calibrate if needed
        if sensor["needs_calibration"]:
            await self.calibration_manager.calibrate_sensor(sensor["entity_id"])
```

## Integration with Other Systems

### VPD System Integration
```python
async def coordinate_with_vpd_system(self):
    """Coordinate irrigation with VPD-based environmental control."""

    # Get current VPD status
    vpd_status = await self.vpd_manager.get_current_status()

    if vpd_status["too_dry"]:
        # Plants need more water - increase irrigation frequency
        self.adjust_irrigation_frequency(+0.2)  # 20% more frequent

    elif vpd_status["too_humid"]:
        # Environment is humid - reduce irrigation to prevent issues
        self.adjust_irrigation_frequency(-0.1)  # 10% less frequent
```

### Premium Analytics Integration
```python
async def submit_irrigation_analytics(self):
    """Submit irrigation data to premium analytics."""

    if not self.premium_manager or not self.premium_manager.is_logged_in:
        return

    analytics_data = {
        "type": "irrigation",
        "timestamp": datetime.now().isoformat(),
        "room": self.room,
        "irrigation_events": self.irrigation_history[-24:],  # Last 24 hours
        "vwc_trends": self.vwc_history[-168:],  # Last week
        "water_usage": self.calculate_water_usage(),
        "efficiency_score": self.calculate_irrigation_efficiency()
    }

    await self.premium_manager.submit_analytics(analytics_data)
```

## Troubleshooting

### Common Issues

#### System Starts in Wrong Phase
- **Symptom**: P1 irrigation shots during night, or P3 during day
- **Cause**: `isPlantDay.islightON` not correctly set
- **Solution**: 
  1. Check that light schedule is configured correctly
  2. Verify `isPlantDay.islightON` in DataStore reflects actual light status
  3. System should always start in P3 if lights are OFF

#### VWC Sensors Reading Incorrectly
- **Symptom**: Irrigations at wrong times or not at all
- **Cause**: Poor calibration or sensor placement
- **Solution**: Recalibrate sensors, check sensor depth

#### Over/Under Watering
- **Symptom**: Plants showing stress despite irrigation
- **Cause**: Wrong VWC targets for plant phase/medium
- **Solution**: Adjust phase-specific VWC ranges

#### P1 Not Stopping When Lights Turn Off
- **Symptom**: Irrigation continues at night
- **Cause**: Light status not updating (older versions)
- **Solution**: Update to latest version - P1 now checks light status each cycle

#### System Not Responding
- **Symptom**: No irrigation despite low VWC
- **Cause**: Emergency stop or calibration issues
- **Solution**: Check system status, recalibrate if needed

#### Irrigation Duration Not Using User Settings
- **Symptom**: Default duration (45s, 20s, 15s) instead of configured value
- **Cause**: DataStore path mismatch or value not set
- **Solution**: 
  1. Check `CropSteering.ShotDuration.{phase}.value` is set
  2. Ensure value is numeric (not string)
  3. Restart integration to reload settings

### Diagnostic Tools

#### System Health Check
```python
async def run_system_diagnostics(self):
    """Run comprehensive crop steering diagnostics."""

    diagnostics = {
        "sensor_status": await self._check_sensor_health(),
        "calibration_status": self._check_calibration_validity(),
        "phase_status": self._get_current_phase_status(),
        "irrigation_capability": self._check_irrigation_system(),
        "medium_sync": await self._verify_medium_sync(),
        "performance_metrics": self._calculate_system_performance(),
        "recommendations": self._generate_diagnostic_recommendations()
    }

    return diagnostics

async def _check_sensor_health(self):
    """Check VWC and EC sensor health."""
    sensor_data = await self._get_sensor_averages()

    if not sensor_data:
        return {"status": "error", "message": "No sensor data available"}

    health = {
        "vwc_sensors": len(sensor_data.get("vwc_values", [])),
        "ec_sensors": len(sensor_data.get("ec_values", [])),
        "validation_status": sensor_data.get("validation_valid", False),
        "last_update": sensor_data.get("timestamp")
    }

    return health
```

#### Calibration Validation
```python
def _check_calibration_validity(self):
    """Check if calibrations are current and valid."""

    calibrations = {
        "p1_vwc_max": self.data_store.getDeep("CropSteering.Calibration.p1.VWCMax"),
        "p1_timestamp": self.data_store.getDeep("CropSteering.Calibration.p1.timestamp"),
        "medium_type": self.medium_type
    }

    # Check if calibration exists
    if not calibrations["p1_vwc_max"]:
        return {
            "status": "needs_calibration",
            "message": "No VWC max calibration found",
            "recommendation": "Run automatic calibration"
        }

    # Check calibration age
    if calibrations["p1_timestamp"]:
        import datetime
        cal_date = datetime.fromisoformat(calibrations["p1_timestamp"])
        age_days = (datetime.now() - cal_date).days

        if age_days > 30:
            return {
                "status": "outdated",
                "age_days": age_days,
                "message": f"Calibration is {age_days} days old",
                "recommendation": "Re-run calibration"
            }

    return {
        "status": "valid",
        "vwc_max": calibrations["p1_vwc_max"],
        "medium": calibrations["medium_type"]
    }
```

#### Performance Analytics
```python
def _calculate_system_performance(self):
    """Calculate irrigation system performance metrics."""

    # Get irrigation history
    irrigation_events = self.data_store.getDeep("CropSteering.irrigation_history") or []

    if not irrigation_events:
        return {"status": "no_data", "message": "No irrigation history available"}

    # Calculate metrics
    total_irrigation = sum(event.get("duration", 0) for event in irrigation_events)
    total_events = len(irrigation_events)

    # Calculate efficiency (VWCs achieved per liter)
    vwc_improvements = []
    for event in irrigation_events:
        pre_vwc = event.get("pre_vwc")
        post_vwc = event.get("post_vwc")
        duration = event.get("duration", 0)

        if pre_vwc is not None and post_vwc is not None and duration > 0:
            improvement = post_vwc - pre_vwc
            efficiency = improvement / duration if duration > 0 else 0
            vwc_improvements.append(efficiency)

    avg_efficiency = sum(vwc_improvements) / len(vwc_improvements) if vwc_improvements else 0

    return {
        "total_irrigation_seconds": total_irrigation,
        "total_events": total_events,
        "average_efficiency": avg_efficiency,
        "efficiency_unit": "vwc_percent_per_second"
    }
```

---

## Console Commands

The CropSteering system provides console commands for monitoring and calibration:

### Available Commands

| Command | Description | Example |
|---------|-------------|---------|
| `cs_status` | Show current CS status and calibration values | `cs_status` |
| `cs_calibrate max` | Start VWC max calibration | `cs_calibrate max p1` |
| `cs_calibrate min` | Start VWC min calibration | `cs_calibrate min p2` |
| `cs_calibrate stop` | Stop running calibration | `cs_calibrate stop` |

### Usage Examples

```bash
# Check current status
$ cs_status

# Start max calibration for P1 phase
$ cs_calibrate max

# Start min calibration for P2 phase  
$ cs_calibrate min p2

# Stop any running calibration
$ cs_calibrate stop

# Get help
$ cs_calibrate -h
```

---

## Implementation Status

### Core Components ✅ **IMPLEMENTED**

| Component | Lines | Status | Description |
|-----------|-------|--------|-------------|
| **OGBCSManager** | ~1450 | ✅ Ready | Main controller, coordinates all subsystems |
| **OGBCSConfigurationManager** | ~320 | ✅ Ready | Settings, presets, medium adjustments |
| **OGBCSIrrigationManager** | ~200 | ✅ Ready | Water delivery, dripper control |
| **OGBCSPhaseManager** | ~140 | ✅ Ready | Mode parsing, initial phase, transition logging |
| **OGBCSCalibrationManager** | ~400 | ✅ Ready | VWC max/min calibration procedures |
| **OGBAdvancedSensor** | ~300 | ✅ Ready | TDR polynomial calculations |

### Key Features ✅ **FULLY IMPLEMENTED**

- **4-Phase Automatic Mode**: P0-P3 with intelligent transitions
- **Manual Mode**: Two flavours via mode selector - `Manual` (pure: user keeps the selected phase, only scheduled shots, failsafes warn) and `Manual-Transition` (previous behavior: manual thresholds + automatic light/VWC phase transitions). Both use user-configurable timing per phase
- **Medium-Specific Adjustments**: Rockwool, coco, soil, perlite, aero, water
- **Growth Phase Optimization**: Vegetative vs generative watering strategies (driven by `plantStage`)
- **VWC Calibration**: Dedicated CalibrationManager (auto in Automatic/Manual-Transition, manual via `cs_calibrate`)
- **Auto-Calibration P1**: VWCMax via stagnation detection during saturation
- **Auto-Calibration P2**: VWCMax via post-irrigation peak tracking
- **Auto-Calibration P3**: VWCMin via night dryback minimum observation
- **Calibration Status Monitoring**: First-start indicator + 4-week re-cal reminder
- **Console Commands**: `cs_status`, `cs_calibrate` for user interaction
- **Advanced Sensor Processing**: TDR-style polynomial calculations
- **EC Management**: Pore water EC with temperature normalization
- **Irrigation Validation**: Effectiveness monitoring and anomaly detection
- **Emergency Systems**: Safety irrigation and dryback protection
- **AI Learning Integration**: Sensor data collection for analytics
- **Calibration Persistence**: `Calibration` + `Learned` persisted across restarts (v3.8.1)

### Integration Points ✅ **CONNECTED**

- **VPD System**: Coordinates with environmental control
- **Premium Analytics**: Sends irrigation data for AI learning
- **Medium Manager**: Syncs growing medium type
- **HA Entities**: Controls pumps, valves, sensors
- **Event System**: Emits irrigation events for monitoring
- **Console Manager**: Exposes `cs_calibrate` and `cs_status` commands
- **DataStore**: Calibration + Learned storage (persisted in `ogb_data/ogb_<room>_state.json`)

---

**Last Updated**: August 14, 2026
**Version**: 3.9 (Reference Alignment: P1 Cadence & Next-Day EC Target)
**Status**: ✅ **PRODUCTION READY**

### Changelog v3.9 (August 14, 2026)
- **Changed**: Capacity semantics cleaned up — the P1/P2 capacity ceiling is now
  `min(preset VWCMax, max_saturation_vwc, field_capacity_vwc)`:
  - Removed the **+5 % headroom** on the learned `max_saturation_vwc` clamp — the observed P1
    peak is now the hard ceiling (no more overshoot toward full saturation).
  - `field_capacity_vwc` is now actually **used as a capacity reference** (it was learned and
    displayed but never used in control). Its learning **ratchets up only**, so undershooting
    P2 shots can never drag the day-to-day capacity down.
- **Changed**: Automatic P1 shot cadence now follows the reference practice band — `wait_between`
  default **180 s → 900 s (15 min)** (reference: 15–30 min between small shots). User-adjustable via
  P1 `Shot_Intervall`.
- **Changed**: P3 dryback-based EC adjustment is no longer log-only. `_adjust_ec_for_dryback()`
  now writes a **persisted next-day EC target** (`CropSteering.Learned.next_ec_target`), clamped to
  `[MinEC, MaxEC]`, with **at most one adjustment per night** (`CropSteering.p3_ec_adjusted` flag,
  reset on P3 entry/exit). The next day's P1 uses it as its effective `ECTarget`
  (`_get_effective_ec_target()`). Actual dosing remains a TODO (Nutrient-System integration).
- **Fixed**: Manual P2 never incremented `CropSteering.p2_shot_count` (only the manual
  `shotCounter`), so `_track_p2_vwc_peak()`'s cycle requirement (`irrigation_count >= 3`) was never
  met and the P2 VWCMax auto-calibration could never fire in Manual/Manual-Transition. The manual
  P2 handler now also records the shot via `_record_p2_irrigation()`.
- **Added**: Documentation section "Reference Method Comparison" (P0–P3 matrix, ✅/⚠️/❌),
  "Deliberately Not Implemented" (runoff, volume-based shots, shot-size EC lever, dryback rate,
  weekly runoff-EC review) and "OGB-Specific Extensions" (emergency shots, pre-night buffers,
  absolute guards, `p1_peak_vwc` learning, `next_ec_target`, persistence).
- **Fixed**: P3 EC adjustment note updated from "log-only" to the new persisted `next_ec_target`
  behavior.
- **Fixed**: Failsafe guard table corrected — Sensor Stuck threshold is **15** consecutive
  unchanged readings (matches `_SENSOR_STUCK_THRESHOLD`), not 10. Tests updated accordingly.
- **Added**: **P2 introduction control** (reference practice — weekly decision). New
  `CropSteering.P2_Introduction` setting: `auto` (default — P2 skipped until the **daily dryback
  rate** relative to the day's P1 peak exceeds `P2_Intro_Dryback_Threshold`, default 25 %; the
  resulting `p2_introduced` state is persisted), `enabled` (always run P2), `disabled` (early veg
  runs P1 + P3 only — after saturation the system returns to P0 monitoring). When P2 is not in
  use, `_determine_initial_phase` returns P0 instead of P2.
- **Added**: **Initial Soak at veg start** (reference practice — P1 special case). New one-shot
  `CropSteering.InitialSoak` flag: while armed, P0 starts P1 immediately (no transpiration buffer)
  and P1 saturates to **full container capacity** ignoring any learned `p1_peak_vwc`; the flag
  auto-disarms after the first **successful** soak. A failed soak stays armed (retry next day).
- **Fixed**: **P3 → P0 bounce during the end-of-day ramp-down.** P1/P2 transition to P3 early
  (up to 2 h / 1 h before the scheduled light-off, see "Light-Based Phase Transitions"), but the
  light entity still reports "on" during that ramp-down window. Both the forced light transition
  (`_check_forced_light_phase_transition`) and the P3 handler / Manual-Transition P3 then forced
  P3 → P0 again, undoing the early night transition every cycle. All three now respect
  `_is_near_light_off()`: while lights are going off soon, P3 keeps running the night dryback
  instead of bouncing back to P0. Regression tests added for the forced transition, the automatic
  P3 handler and Manual-Transition P3.
- **Added**: `p2_introduced` to the persisted learned values (`CROP_STEERING_LEARNED_KEYS`).
- **Added**: Console helper commands for crop steering:
  - `cs_soak [on|off|status]` — arm the one-shot Initial Soak in **any week** (not just veg
    start), disarm it, or show its state.
  - `cs_p2 [status|mode <auto|enabled|disabled>|threshold <n>|reset]` — control the P2
    introduction mode, its dryback threshold, and reset the introduced state.
  - `cs_status` now shows the steering settings block (Initial Soak, P2 mode/introduced/
    threshold, today's saturation peak + **current dryback vs peak**, learned P1 peak,
    next-day EC target).
- **Fixed**: **Dryout override (rescue shots for stuck sensors) now fires in all phases and uses the last trusted VWC.** Previously the override only ran in P2/P3 and gated against the *current* (stuck) reading, so a sensor stuck at 30.5 % with emergency level 28 % blocked the rescue even though the medium was critically dry. Now:
  - Works in P0–P3 (dedicated per-phase emergency counters `p0_emergency_count`, `p1_emergency_count`).
  - For `sensor_invalid` the override judges against the last reading before the stuck window (in-memory `_vwc_history`, then persisted `lastIrrigationVWC`/`day_peak_vwc`), not the stale sensor value.
  - Bounded as before: max 5 shots à 15 s, 5-min interval. Flood-guard and max-runtime remain hard stops.
  - Regression tests cover P0/P1/P3 firing, trusted-vs-stuck decision, fallback, and counter limits.

### Changelog v3.8.1 (August 14, 2026)
- **Changed**: VWC calibration data (`CropSteering.Calibration` + `CropSteering.Learned`) is now **persisted across HA restarts** (`CropSteering` added to `PRESERVED_STATE_KEYS`; only Calibration + Learned subtrees are written, runtime state excluded). Removed the previous "runtime-only / known open item" notices.
- **Fixed**: Documentation errors relative to the implementation:
  - P1 stagnation threshold corrected from `max(40.0, preset_vwc_min)` to `max(25.0, preset_vwc_min)` (matches code)
  - P3 emergency threshold corrected — it is `max(ABS_VWC_MIN, VWCMin × emergency_threshold, learned_min_dryback + 3.0)`, **not** `VWCMax × 0.85`
  - P2 emergency level clarified (`VWCMin × emergency_threshold`, default 0.5)
  - P1→P2 vs. P1→P3 triggers separated: lights-off during P1 always goes to **P3** (abort), never to P2
  - P2→P3 documented as **pre-night (≤1 h before lights off) = normal path**, lights-off = fallback
  - P3 EC adjustment documented as **log-only** (no DataStore write, no dosing) with effect on the **next** fertigation only

### Changelog v3.8 (August 13, 2026)
- **Fixed**: Documentation corrected to match the Manual/Manual-Transition implementation:
  - Mode overview now lists all select options (`Automatic`, `Manual-Transition`, `Manual`, `Config`, `Disabled`)
  - Light-driven phase transitions documented as **Automatic + Manual-Transition only** (pure Manual keeps the selected phase)
  - Auto-calibration documented as **Automatic + Manual-Transition only** (pure Manual calibrates manually via `cs_calibrate`)
  - **Corrected false persistence claims**: VWC calibration values are **runtime-only** and **lost on HA restart** (`CropSteering` excluded from the persisted state file). Persistence is a known open item.

### Changelog v3.7 (August 13, 2026)
- **Added**: New mode `Manual` (pure): user-selected phase is kept, **no** automatic transitions and **no** auto-calibration. The cycle only irrigates per the user's timing settings (P0/P3 stay dry, P1/P2 shoot on schedule; P1 shot counter resets after a full cycle).
- **Added**: New mode `Manual-Transition` = previous Manual behavior (automatic light/VWC/shot-based phase transitions + auto-calibration).
- **Changed**: In pure `Manual` mode non-critical failsafe guards (sensor invalid/stuck, dryout, ineffective irrigation) now **warn** (rate-limited notification) instead of silently blocking irrigation. Only `flood_guard` (VWC ≥ 90%) and `max_runtime` (pump cap) hard-stop.
- **Fixed**: Restored `_run_manual_mode()` which had been left orphaned as dead code (Manual mode crashed with `AttributeError`).

### Changelog v3.6 (August 12, 2026)
- **Updated**: Manual mode documentation to reflect that P0–P3 phases now auto-transition based on light status and VWC conditions, just like Automatic mode. The user-selected phase is only the starting phase; the system will not irrigate at night or stay stuck in a completed phase.

### Changelog v3.5 (August 8, 2026)
- **Fixed**: P0 → P1 automatic transition was unreachable due to indentation bug in `_handle_phase_p0_auto()`
- **Fixed**: Manual phase changes now immediately restart the running manual cycle via `CSManualPhaseChanged` event (no more 10-second polling delay)
- **Changed**: Automatic mode is now fully **preset-driven**. User settings from `CropSteering.Substrate.{phase}.*` entities are no longer used for control decisions; only base presets + medium offset + plant phase/week adjustments apply.
- **Added**: Bulletproof failsafe guards with critical push notifications via `OGBNotificator`: flood guard, dryout guard, sensor stuck, sensor jump, ineffective irrigation, max runtime.
- **Added**: Dynamic learning of `max_saturation_vwc`, `field_capacity_vwc`, and `min_dryback_vwc` from sensor data to clamp safe thresholds.
- **Changed**: P3 emergency irrigation now triggers on the highest of: hard minimum (5%), learned dryback minimum + safety margin, or preset VWCMin. No longer based on `VWCMax * 0.85`.
- **Changed**: P1/P2/P3 timing values in Automatic mode are now read from bulletproof base presets instead of user timing entities.
- **Updated**: Manual mode documentation to clarify it uses **all** user settings (VWC/EC thresholds + timing), not only timing.
- **Updated**: Removed duplicate P3 → P0 transition code block.

### Changelog v3.4 (June 24, 2026)
- **Added**: P2 VWC Max auto-calibration via post-irrigation peak tracking (3+ consistent peaks within 2%)
- **Added**: P3 VWC Min auto-detection via night dryback minimum observation (2+ consistent nights within 2%)
- **Added**: First-start calibration indicator — warns when no calibrations exist yet
- **Added**: Periodic re-calibration reminder — warns when calibration older than 4 weeks
- **Added**: `_track_p2_vwc_peak()` method for P2 peak detection and calibration
- **Added**: `_check_calibration_status()` method for daily calibration health check
- **Updated**: Reset methods `_reset_p2_state_tracking()` and `_reset_p3_state_tracking()` to clear irrigation timer
- **Updated**: P2 handler now saves `p2_last_irrigation_time` for accurate peak window detection
- **Updated**: P3→P0 transition now tracks `p3_night_vwc_mins` rolling list for auto-calibration

### Changelog v3.3 (January 6, 2026)
- **Fixed**: Manual mode phase extraction bug - now correctly reads from CropPhase selector (P1 vs p0)
- **Fixed**: Pump filtering bug - `_get_drippers()` now filters by "dripper" keyword to exclude pumpcloner
- **Added**: Automatic mode user timing settings - Duration/Interval/ShotSum now use user values instead of hardcoded presets
- **Added**: Phase extraction helper methods `_extract_phase_from_mode()` and `_extract_phase_from_value()`
- **Fixed**: Indentation and syntax errors in `_irrigate()` method try/catch blocks
- **Improved**: Better logging for user timing values vs preset VWC/EC values in Automatic mode

### Changelog v3.2 (January 5, 2026)
- **Fixed**: Config/Disabled mode now properly cancels running tasks
- **Fixed**: P1 state tracking reset when entering Config/Disabled (no stale interval waits)
- **Fixed**: Stagnation detection safety - requires VWC >= 40% before saving calibration
- **Fixed**: Auto-reset of invalid calibration values (< 40% or < preset minimum)
- **Added**: Detailed phase transition diagram
- **Added**: Medium-based DataStore paths documentation
- **Added**: Entity naming convention documentation
- **Improved**: P1 shot logging now includes duration and next interval time
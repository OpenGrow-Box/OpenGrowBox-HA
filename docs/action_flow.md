# OpenGrowBox - Action Cycle Overview

## Table of Contents
1. [VPD Perfection Mode](#vpd-perfection-mode)
2. [VPD Target Mode](#vpd-target-mode)
3. [Closed Environment Mode](#closed-environment-mode)
4. [Safety Mechanisms](#safety-mechanisms)
5. [Deadband & Quiet Zone](#deadband--quiet-zone)
6. [Conflict Resolution](#conflict-resolution)
7. [Adaptive Cooldown](#adaptive-cooldown)
8. [Environment Guard Details](#environment-guard-details)

---

## VPD Perfection Mode

### Trigger
- **Event:** `VPDCreation` (triggered by new sensor data)
- **Source:** `OGBVPDManager` calculates VPD from Temperature + Humidity sensors

### Action Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: VPD CALCULATION                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBVPDManager.calculateVPD()                                                │
│                                                                             │
│ • Calculates VPD from temperature and relative humidity                    │
│ • Stores: vpd.current, vpd.perfection, vpd.perfectMin, vpd.perfectMax     │
│ • Triggers: VPDCreation Event                                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: MODE MANAGER                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBModeManager.handle_vpd_perfection()                                      │
│                                                                             │
│ 2.1 Reads VPD values:                                                       │
│     • currentVPD = data_store.getDeep("vpd.current")                        │
│     • perfectionVPD = data_store.getDeep("vpd.perfection")                  │
│     • perfectMinVPD = data_store.getDeep("vpd.perfectMin")                  │
│     • perfectMaxVPD = data_store.getDeep("vpd.perfectMax")                  │
│                                                                             │
 │ 2.2 🆕 SMART DEADBAND CHECK WITH HYSTERESIS (UPDATED!)                           │
 │     deadband = 0.05 (default)                                               │
 │     deviation = |currentVPD - perfectionVPD|                                │
 │     exit_threshold = deadband * 1.15 = 0.0575 (15% hysteresis)            │
 │                                                                             │
 │     IF deviation <= deadband:                                               │
 │       → _handle_smart_deadband() called                                     │
 │       → Climate devices reduced to minimum (10%-25%-50%)                    │
 │       → Air exchange devices (Exhaust, Intake, Window) reduced             │
 │       → Ventilation continues running                                      │
 │       → Hold time: 2.5 minutes                                              │
 │       → LogForClient with hysteresis info                                   │
 │       → RETURN (no VPD events!)                                             │
 │     ELIF deviation > exit_threshold:                                         │
 │       → Exit deadband (15% buffer to prevent oscillation)                   │
 │       → _reset_deadband_state()                                             │
 │       → Continue to Step 2.3                                                │
 │     ELSE:                                                                   │
 │       → Continue to Step 2.3                                                │
│                                                                             │
 │ 2.3 VPD Decision (only if outside deadband):                               │
 │     IF currentVPD < perfectMinVPD:                                          │
 │        → Emit: "increase_vpd"                                               │
 │     ELIF currentVPD > perfectMaxVPD:                                        │
 │        → Emit: "reduce_vpd"                                                 │
 │     ELSE:                                                                   │
 │        → NO ACTION (VPD within range)                                       │
 │        * FineTune REMOVED - Deadband handles this *                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │ STEP 2: MODE MANAGER                                                       │
  ├─────────────────────────────────────────────────────────────────────────────┤
  │ OGBModeManager.handle_vpd_perfection()                                      │
  │                                                                             │
  │ Responsibility: Decide WHICH event to emit (no control logic)              │
  │                                                                             │
  │ Decision Logic:                                                             │
  │ • currentVPD < perfectMinVPD  → emit("increase_vpd")                      │
  │ • currentVPD > perfectMaxVPD  → emit("reduce_vpd")                          │
  │ • in Range                 → NO EVENT (handled by ActionManager)             │
  │                                                                             │
  │ Note: NO deadband check here - handled by ActionManager                     │
  └─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 3: ACTION MANAGER - DEADBAND CHECK 🎯                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.checkLimitsAndPublicate()                                  │
│                                                                             │
│ • Calls _is_vpd_in_deadband()                                               │
│ • Checks if currentVPD is within ±0.05 kPa of target                         │
│ • If in deadband: emit quiet zone signal and return (early exit)            │
│ • If NOT in deadband: continue to action processing                          │
│                                                                             │
│ This is the QUIET ZONE - devices pause when VPD is good!                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: WEIGHTED DEVIATIONS CALCULATION (Central) 🎯                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager._calculate_weighted_deviations()                          │
│                                                                             │
│ • Calculates temp_weight, hum_weight (user- or plant-stage-specific)       │
│ • Calculates temp_deviation, hum_deviation                                   │
│ • Emit OGBWeightPublication (for all 3 modes!)                              │
│                                                                             │
│ Weighted deviations are used for Dampening and Cooldown                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 5: VPD ACTIONS                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBVPDActions.increase_vpd() / reduce_vpd()                                 │
│                                                                             │
│ Creates Action Map based on Capabilities:                                   │
│ • canExhaust    → Increase/Reduce                                          │
│ • canIntake     → Reduce/Increase                                         │
│ • canVentilate  → Increase/Reduce                                          │
│ • canHumidify   → Reduce/Increase                                          │
│ • canDehumidify→ Increase/Reduce                                           │
│ • canHeat       → Increase/Reduce                                          │
│ • canCool       → Reduce/Increase                                          │
│ • canClimate    → Eval                                                     │
│ • canCO2        → Increase/Reduce (depending on light)                     │
│ • canLight      → Increase (if vpdLightControl=True)                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: NIGHT HOLD CHECK 🌙                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager._check_vpd_night_hold()                                    │
│                                                                             │
│ IF light OFF AND nightVPDHold=False:                                        │
│   → _night_hold_fallback() is called                                        │
│                                                                             │
│   Night Hold Power-Saving Logic:                                            │
│   ┌─────────────────────────────────────────────────────────────────┐       │
│   │ Climate Devices (Save Power):                                   │       │
│   │ • canHeat       → Reduce  (Heater off)                         │       │
│   │ • canCool       → Reduce  (Cooler off)                         │       │
│   │ • canHumidify   → Reduce  (Humidifier off)                     │       │
│   │ • canDehumidify → Reduce  (Dehumidifier off)                   │       │
│   │ • canClimate    → Reduce  (Climate off)                        │       │
│   │ • canCO2        → Reduce  (CO2 off)                            │       │
│   │ • canLight      → Reduce  (Light off)                          │       │
│   └─────────────────────────────────────────────────────────────────┘       │
│   ┌─────────────────────────────────────────────────────────────────┐       │
│   │ Ventilation (Mold Prevention):                                  │       │
│   │ • canExhaust    → Increase  (Air exchange!)                    │       │
│   │ • canVentilate  → Increase  (Air circulation!)                 │       │
│   │ • canWindow     → Increase  (Air exchange!)                    │       │
│   │ • canIntake     → Variable  (based on outside temp)            │       │
│   │                                                                             │
│   │   Intake Logic:                                                              │
│   │   IF outside_temp >= (minTemp - 3°C):                                        │
│   │      → Increase (Outside air is warm enough)                                │
│   │   ELSE:                                                                      │
│   │      → Reduce (Too cold, save heating)                                      │
│   └─────────────────────────────────────────────────────────────────┘       │
│                                                                             │
│   Log: "NightHold: Power-Saving Mode - Climate minimized, Ventilation active"│
│                                                                             │
│ ELSE (light ON OR nightVPDHold=True):                                       │
│   → Continue to Step 6 (normal VPD processing)                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: WEIGHTED DEVIATIONS CALCULATION (Central) 🎯                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.checkLimitsAndPublicate()                                 │
│                                                                             │
│ • Calculates temp_weight, hum_weight (user- or plant-stage-specific)       │
│ • Calculates temp_deviation = (temp - min/max) * temp_weight               │
│ • Calculates hum_deviation = (hum - min/max) * hum_weight                  │
│ • Emit OGBWeightPublication (for all 3 modes!)                             │
│                                                                             │
│ Weighting Example:                                                          │
│ • humidity=2, temp=0 → Humidity error has 2x priority                     │
│ • humidity=0, temp=1 → Temperature error has 1x priority                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 5: DAMPENING ACTIONS                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBDampeningActions.process_actions_basic()                                │
│                                                                             │
│ • Receives temp_deviation, hum_deviation (from ActionManager)              │
│ • Applies Buffer Zones (prevents oscillation)                              │
│ • Resolves Action conflicts (highest priority per capability)              │
│ • Filters through Dampening/Cooldown (uses weighted deviations)            │
│                                                                             │
│ IMPORTANT: Weighted Deviations are NOT recalculated!                       │
│ They are calculated centrally in ActionManager and used here.              │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 6: PUBLICATION ACTION HANDLER                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.publicationActionHandler()                                 │
│                                                                             │
│ 7.1 Tent Mode Check: if Disabled → STOP                                   │
│                                                                             │
│ 7.2 🛡️ ENVIRONMENT GUARD APPLIED (STEP 8)                                   │
│                                                                             │
│ 7.3 Stores Actions for Analytics:                                          │
│     • previousActions (max 5)                                               │
│     • actionData (AI Training)                                              │
│                                                                             │
│ 7.4 Emit Device Events:                                                    │
│     canExhaust    → "Increase Exhaust" / "Reduce Exhaust"                 │
│     canIntake     → "Increase Intake" / "Reduce Intake"                    │
│     canVentilate  → "Increase Ventilation" / "Reduce Ventilation"         │
│     canHeat       → "Increase Heater" / "Reduce Heater"                   │
│     canCool       → "Increase Cooler" / "Reduce Cooler"                    │
│     ...                                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 7: ENVIRONMENT GUARD 🛡️ (Detailed)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager._apply_environment_guard()                                │
│ OGBEnvironmentGuard.evaluate_environment_guard()                           │
│                                                                             │
│ ONLY FOR: canExhaust, canIntake WITH Increase                               │
│ (Note: canVentilate is NOT handled by EnvironmentGuard -                   │
│       internal air circulation, no air exchange with outside)               │
│ 1. SELECT AIR SOURCE:                                                      │
│    • Intake → Outsite (Weather data) if available                          │
│    • Exhaust → Ambient (Room data)                                         │
│                                                                             │
│ 2. EVALUATE RISKS:                                                         │
│    • temp_risk: Too cold inside + source even colder                       │
│    • humidity_risk: Too dry + source even drier                            │
│    • temp_benefit: Too cold + source warmer                                │
│    • humidity_benefit: Too wet + source drier                              │
│    • humidity_critical: humidity >= maxHumidity (MOLD RISK!)               │
│    • humidity_critical_dry: humidity <= minHumidity (TOO DRY!)             │
│                                                                             │
│ 3. PRIORITY DECISION:                                                      │
│    1️⃣ humidity_critical → ALLOW (Emergency override!)                      │
│    2️⃣ humidity_critical_dry → ALLOW (Emergency override!)                  │
│    3️⃣ humidity_benefit → ALLOW (Drying needed)                             │
│    4️⃣ temp_benefit → ALLOW (Heating needed)                                │
│    5️⃣ temp_risk → BLOCK (Too cold!)                                       │
│    6️⃣ humidity_risk → BLOCK (Too dry!)                                     │
│    7️⃣ No risk → ALLOW                                                      │
│                                                                             │
│ 4. RESULT:                                                                 │
│    BLOCKED → Action "Increase" → "Reduce" rewritten                        │
│    ALLOWED → Action remains unchanged                                      │
│                                                                             │
│ 5. LOG FOR CLIENT:                                                         │
│    • On Block: WARNING with detailed info                                  │
│    • On Allow: DEBUG                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 8: DEVICE EXECUTION                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ Device-specific on() handlers receive events:                              │
│                                                                             │
│ • Exhaust.on("Increase Exhaust") → increaseAction()                       │
│ • Intake.on("Increase Intake") → increaseAction()                         │
│ • Heater.on("Increase Heater") → increaseAction()                         │
│                                                                             │
│ IMPORTANT: Device.should_block_air_exchange_increase() also checks         │
│ EnvironmentGuard (for direct Increase-Actions)!                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## VPD Target Mode

### Differences to VPD Perfection

| Aspect | VPD Perfection | VPD Target |
|--------|---------------|------------|
| **VPD Calculation** | Compares with perfectMin/max | Compares with targetedMin/Max |
| **Event Names** | `increase_vpd`, `reduce_vpd` | `vpdt_increase_vpd`, `vpdt_reduce_vpd` |
| **VPD Actions** | `increase_vpd()` | `increase_vpd_target()` |
| **Handler** | `_handle_increase_vpd()` | `_handle_vpdt_increase_vpd()` |
| **Deviation** | Temp/Hum weighted (Plant Stage) | **Only VPD Deviation** (Current - Target) |
| **Dampening** | `process_actions_basic()` | `process_actions_target_basic()` |
| **WeightPublication** | ✅ Emitted | ❌ Not emitted (only VPD deviation) |

### Action Flow (identical to VPD Perfection except Step 3)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ DIFFERENCES:                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│ STEP 3: handle_targeted_vpd()                                              │
│ • Reads: vpd.targeted, vpd.targetedMin, vpd.targetedMax, vpd.tolerance     │
│ • Calculates: min_vpd = targeted - (targeted * tolerance/100)               │
│              max_vpd = targeted + (targeted * tolerance/100)               │
│                                                                             │
 │ • 🆕 SMART DEADBAND CHECK WITH HYSTERESIS (UPDATED!):                              │
 │   deadband = 0.05 (default)                                                 │
 │   deviation = |currentVPD - targetedVPD|                                    │
 │   exit_threshold = deadband * 1.15 = 0.0575 (15% hysteresis)                │
 │                                                                             │
 │   IF deviation <= deadband:                                                 │
 │     → _handle_smart_deadband() called                                       │
 │     → Climate devices reduced to minimum (10%-25%-50%)                      │
 │     → Air exchange devices (Exhaust, Intake, Window) reduced               │
 │     → Ventilation continues running                                        │
 │     → Hold time: 2.5 minutes                                                │
 │     → LogForClient with hysteresis info                                     │
 │     → RETURN (no VPD events!)                                               │
 │   ELIF deviation > exit_threshold:                                           │
 │     → Exit deadband (15% buffer to prevent oscillation)                     │
 │     → _reset_deadband_state()                                               │
 │                                                                             │
│                                                                             │
│ • Decision (only if outside deadband):                                      │
│   IF currentVPD < min_vpd:                                                  │
│      → Emit: "vpdt_increase_vpd"                                            │
│   ELIF currentVPD > max_vpd:                                                │
│      → Emit: "vpdt_reduce_vpd"                                              │
│                                                                             │
│ STEP 4: VPD DEVIATION ONLY (NEW!)                                          │
│ • checkLimitsAndPublicateTarget() calculates ONLY VPD Deviation:           │
│   vpd_deviation = currentVPD - targetVPD                                    │
│                                                                             │
│   ❌ NO Temp/Hum weighted deviations!                                       │
│   ❌ NO Plant Stage Limits!                                                 │
│   ✅ ONLY VPD-based!                                                        │
│                                                                             │
│   Example:                                                                  │
│   • currentVPD = 0.62 kPa                                                   │
│   • targetVPD = 1.20 kPa                                                    │
│   • vpd_deviation = -0.58 kPa                                               │
│                                                                             │
│   LogForClient contains:                                                    │
│   • "vpdDeviation": -0.58                                                   │
│   • "currentVPD": 0.62                                                      │
│   • "targetVPD": 1.20                                                       │
│                                                                             │
│ • No Dampening-Weighted Deviations (only VPD-based)                        │
│                                                                             │
│ ALL OTHER STEPS ARE IDENTICAL!                                              │
│ • Conflict Resolution (Step 4)                                             │
│ • Dampening Actions (Step 5)                                               │
│ • Night Hold Check (Step 3)                                                │
│ • Environment Guard (Step 7)                                                │
│ • Device Execution (Step 8)                                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Closed Environment Mode

### Overview

Closed Environment mode is designed for **recirculating systems in sealed chambers** where temperature and humidity control is critical.

**Key Characteristics:**
- **Control Targets**: Uses `tentData.minTemp`, `tentData.maxTemp`, `tentData.minHumidity`, `tentData.maxHumidity` (ALWAYS filled)
- **Data Source**: tentData is ALWAYS filled with either:
  - Plant-stage-specific min/max values (from plantStages config)
  - OR user-defined min/max values (from UI)
- **Control Logic**: Maintains temperature and humidity within min/max limits
- **VPD Usage**: VPD is ONLY used for Smart Deadband (informational, NOT for control)
- **Night Mode**: Power-saving mode when light OFF and nightVPDHold=False
- **Purpose**: Optimized for recirculating systems where ambient exchange is minimal

### Differences to VPD Perfection

| Aspect | VPD Perfection | Closed Environment |
|--------|---------------|-------------------|
| **Mode Handler** | `handle_vpd_perfection()` | `handle_closed_environment()` |
| **Manager** | OGBModeManager → ClosedEnvironmentManager | |
| **Action Handler** | ClosedActions.execute_closed_environment_cycle() | |
| **Night Hold** | ✅ Active | ✅ Active (Power-Saving Mode) |
| **VPD Usage** | ✅ Primary control target | ⚠️ Only for Smart Deadband (NOT for control) |
| **Control Targets** | VPD-based (perfectMin/max) | **tentData min/max** (ALWAYS filled) |
| **Data Source** | vpd.perfection (calculated) | tentData.min/max (from UI or plantStages) |
| **Weighted Deviations** | ✅ Central calculation | ✅ Central calculation (0,0,0,0) |
| **WeightPublication** | ✅ Emitted | ✅ Emitted |
| **Environment Guard** | ✅ Active | ✅ Active |
| **checkLimitsAndPublicate** | `checkLimitsAndPublicate()` | `checkLimitsAndPublicateNoVPD()` |
| **CO2 Control in Deadband** | ❌ No (only outside deadband) | ✅ Yes (important for sealed chambers) |
| **Device State Restoration** | ✅ Yes (restoreFromMinimum) | ✅ Yes (restoreFromMinimum) |

### Control Logic (How It Works)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ CLOSED ENVIRONMENT CONTROL LOGIC                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  DATA SOURCE (ALWAYS FILLED):                                             │
│  ├─ tentData.minTemp  ← User-defined OR plant-stage-specific           │
│  ├─ tentData.maxTemp  ← User-defined OR plant-stage-specific           │
│  ├─ tentData.minHumidity  ← User-defined OR plant-stage-specific       │
│  └─ tentData.maxHumidity  ← User-defined OR plant-stage-specific       │
│                                                                             │
│  CONTROL DECISION:                                                           │
│  IF currentTemp < minTemp:                                               │
│    → Heat until minTemp reached                                           │
│  ELIF currentTemp > maxTemp:                                             │
│    → Cool until maxTemp reached                                           │
│  ELSE:                                                                     │
│    → No action (temperature in range)                                      │
│                                                                             │
│  IF currentHumidity < minHumidity:                                       │
│    → Humidify until minHumidity reached                                   │
│  ELIF currentHumidity > maxHumidity:                                     │
│    → Dehumidify until maxHumidity reached                                 │
│  ELSE:                                                                     │
│    → No action (humidity in range)                                        │
│                                                                             │
│  VPD IS ONLY USED FOR:                                                     │
│  • Smart Deadband check (informational)                                   │
│  • Logging/display purposes                                                │
│  • NOT for control decisions!                                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Example: Late Flower Scenario

```
User Configuration:
┌─────────────────────────────────────────────────────────────────────────┐
│ tentData.minTemp:      20.0°C (from user UI)                         │
│ tentData.maxTemp:      28.0°C (from user UI)                         │
│ tentData.minHumidity:  40.0% (from user UI)                         │
│ tentData.maxHumidity:  60.0% (from user UI)                         │
└─────────────────────────────────────────────────────────────────────────┘

Control Behavior:
┌─────────────────────────────────────────────────────────────────────────┐
│ Current: 25.5°C / 58% RH                                                │
│                                                                          │
│ Temperature:                                                            │
│   • 25.5°C is within 20.0-28.0°C range → NO ACTION                 │
│                                                                          │
│ Humidity:                                                              │
│   • 58% is within 40-60% range → NO ACTION                            │
│                                                                          │
│ VPD (informational):                                                    │
│   • VPD = 1.05 kPa (calculated from T/H, NOT used for control)        │
│   • vpdCurrent displayed in logs for monitoring only                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Action Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: MODE MANAGER                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBModeManager.handle_closed_environment()                                │
│                                                                             │
  │ 1.0 🆕 NIGHT MODE CHECK (NEW!)                                            │
  │     is_light_on = data_store.getDeep("isPlantDay.islightON")             │
  │     night_vpd_hold = data_store.getDeep("controlOptions.nightVPDHold")   │
  │                                                                             │
  │     IF NOT is_light_on AND NOT night_vpd_hold:                            │
  │       → Night Mode Power-Saving is handled in Step 2                      │
  │       → Continue to Step 1.1                                               │
  │                                                                             │
  │ 1.1 🆕 SMART DEADBAND CHECK WITH HYSTERIS (VPD-based)                   │
  │     currentVPD = data_store.getDeep("vpd.current")                        │
  │     targetVPD = data_store.getDeep("vpd.targeted") or vpd.perfection     │
  │                                                                             │
  │     IF currentVPD is not None AND targetVPD is not None:                   │
  │       deadband = 0.05 (default)                                            │
  │       deviation = |currentVPD - targetVPD|                                 │
  │       exit_threshold = deadband * 1.15 = 0.0575 (15% hysteresis)           │
  │                                                                             │
  │       IF deviation <= deadband:                                            │
  │         → deadband_active = _handle_smart_deadband() returns bool        │
  │                                                                             │
  │         IF deadband_active == True:                                        │
  │           → Climate devices reduced to minimum (10%-25%-50%)               │
  │           → Air exchange devices (Exhaust, Intake, Window) reduced        │
  │           → Ventilation continues running                                 │
  │           → Hold time: 2.5 minutes                                        │
  │           → LogForClient with hysteresis info                             │
  │           → CO2 Control still executed (important!)                       │
  │           → RETURN (no normal Closed Env Actions!)                         │
  │         ELSE (deadband_active == False):                                   │
  │           → Deadband blocked (e.g., night mode without nightVPDHold)       │
  │           → Continue to Step 1.2                                           │
  │       ELIF deviation > exit_threshold:                                     │
  │         → Exit deadband (15% buffer to prevent oscillation)                │
  │         → _reset_deadband_state()                                          │
  │                                                                             │
  │ 1.2 Normal Closed Environment Cycle:                                      │
  │     → ClosedEnvironmentManager.execute_cycle()                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: CLOSED ACTIONS                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.execute_closed_environment_cycle()                           │
│                                                                             │
│ 2.0 🆕 NIGHT MODE POWER-SAVING (NEW!)                                       │
│     is_light_on = dataStore.getDeep("isPlantDay.islightON")                 │
│     night_vpd_hold = dataStore.getDeep("controlOptions.nightVPDHold")      │
│                                                                             │
│     IF NOT is_light_on AND NOT night_vpd_hold:                              │
│       → _handle_night_mode_power_saving() called                            │
│                                                                             │
│       Night Mode Power-Saving Logic:                                        │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │ Climate Devices (Save Power):                                   │   │
│       │ • canHeat, canCool, canHumidify, canDehumidify → Reduce (OFF) │   │
│       │ • canClimate, canCO2, canLight → Reduce (OFF)                   │   │
│       └─────────────────────────────────────────────────────────────────┘   │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │ Ventilation (Mold Prevention):                                  │   │
│       │ • canExhaust, canVentilate, canWindow → Increase               │   │
│       │ • canIntake → Variable (based on outside temp)                  │
│       └─────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│       → LogForClient with isNightMode=True, nightVPDHold=False             │
│       → RETURN (no normal Closed Env Actions!)                              │
│                                                                             │
│ 2.1 NOTE: Smart Deadband (VPD-based) DEAKTIVIERT für Closed Environment │
│     → Closed Environment uses ONLY temp/hum deadbands (NOT VPD-based)        │
│                                                                             │
│ 2.2 Check Closed Environment specific deadbands (Temp and Humidity):       │
│     closed_db_status = await _check_closed_deadbands()                      │
│     temp_in_db = closed_db_status["temp_in_deadband"]                       │
│     hum_in_db = closed_db_status["hum_in_deadband"]                         │
│     temp_dev = closed_db_status["temp_deviation"]                           │
│     hum_dev = closed_db_status["hum_deviation"]                             │
│     temp_target = closed_db_status["temp_target"]  ← (min + max) / 2      │
│     hum_target = closed_db_status["hum_target"]    ← (min + max) / 2      │
│                                                                             │
│ 2.3 Collect all actions:                                                    │
│     • o2_actions = monitor_o2_safety()                                     │
│     • co2_actions = maintain_co2()                                          │
│     • temp_actions = control_temperature_closed() (if NOT in temp db)      │
│     • hum_actions = control_humidity_closed() (if NOT in hum db)           │
│     • air_actions = optimize_air_recirculation() (if NOT in any db)        │
│                                                                             │
│     NOTE: All actions are collected and executed in ONE batch!             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 3: TEMPERATURE/HUMIDITY CONTROL                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.control_temperature_closed()                                 │
│ ClosedActions.control_humidity_closed()                                    │
│                                                                             │
│ Uses: _increase_temperature() / _decrease_temperature()                   │
│       _increase_humidity() / _decrease_humidity()                         │
│                                                                             │
│ Each method returns action_map (does NOT execute immediately!)             │
│ Actions are collected for batch execution                                  │
│                                                                             │
│ IMPORTANT: Uses tentData min/max limits for control                        │
│ • Control Logic: current vs min/max (NOT VPD targets!)                  │
│ • temp_target in log = (minTemp + maxTemp) / 2 (for display only)          │
│ • hum_target in log = (minHumidity + maxHumidity) / 2 (for display only)    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: BATCH EXECUTION                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.execute_closed_environment_cycle()                           │
│                                                                             │
│ All actions combined into single action_map                                │
│ → action_manager.checkLimitsAndPublicateNoVPD(all_actions)                 │
│                                                                             │
│ Single LogForClient Event:                                                  │
│ {                                                                           │
│   "Name": "dev_room",                                                       │
│   "message": "Closed Environment: 5 actions executed",                     │
│   "actions": "canExhaust:Increase, canCool:Reduce, canDehumidify:Increase",│
│   "actionCount": 5,                                                         │
│   "tempDeviation": 1.6,                                                     │
│   "humDeviation": 0.8,                                                      │
│   "tempCurrent": 25.5,                                                      │
│   "tempTarget": 24.0,  ⚠️ (minTemp + maxTemp) / 2, NOT VPD target!          │
│   "humCurrent": 58.0,                                                       │
│   "humTarget": 50.0,   ⚠️ (minHumidity + maxHumidity) / 2, NOT VPD target!  │
│   "vpdCurrent": 1.05,  ⚠️ For informational purposes only (NOT used for control)│
│   "smartDeadbandActive": false                                              │
│ }                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: CLOSED ACTIONS                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.execute_closed_environment_cycle()                           │
│                                                                             │
│ 2.0 🆕 NIGHT MODE POWER-SAVING (NEW!)                                       │
│     is_light_on = dataStore.getDeep("isPlantDay.islightON")                 │
│     night_vpd_hold = dataStore.getDeep("controlOptions.nightVPDHold")      │
│                                                                             │
│     IF NOT is_light_on AND NOT night_vpd_hold:                              │
│       → _handle_night_mode_power_saving() called                            │
│                                                                             │
│       Night Mode Power-Saving Logic:                                        │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │ Climate Devices (Save Power):                                   │   │
│       │ • canHeat, canCool, canHumidify, canDehumidify → Reduce (OFF) │   │
│       │ • canClimate, canCO2, canLight → Reduce (OFF)                   │   │
│       └─────────────────────────────────────────────────────────────────┘   │
│       ┌─────────────────────────────────────────────────────────────────┐   │
│       │ Ventilation (Mold Prevention):                                  │   │
│       │ • canExhaust, canVentilate, canWindow → Increase               │   │
│       │ • canIntake → Variable (based on outside temp)                  │   │
│       └─────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│       → LogForClient with isNightMode=True, nightVPDHold=False             │
│       → RETURN (no normal Closed Env Actions!)                              │
│                                                                             │
│ 2.1 NOTE: Smart Deadband (VPD-based) DEAKTIVIERT für Closed Environment │
│     → Closed Environment uses ONLY temp/hum deadbands (NOT VPD-based)        │
│                                                                             │
│ 2.2 Check Closed Environment specific deadbands (Temp and Humidity):       │
│     closed_db_status = await _check_closed_deadbands()                      │
│     temp_in_db = closed_db_status["temp_in_deadband"]                       │
│     hum_in_db = closed_db_status["hum_in_deadband"]                         │
│     temp_dev = closed_db_status["temp_deviation"]                           │
│     hum_dev = closed_db_status["hum_deviation"]                             │
│     temp_target = closed_db_status["temp_target"]  ⚠️ OWN TARGET, NOT VPD! │
│     hum_target = closed_db_status["hum_target"]    ⚠️ OWN TARGET, NOT VPD! │
│                                                                             │
│ 2.3 Collect all actions:                                                    │
│     • o2_actions = monitor_o2_safety()                                     │
│     • co2_actions = maintain_co2()                                          │
│     • temp_actions = control_temperature_closed() (if NOT in temp db)      │
│     • hum_actions = control_humidity_closed() (if NOT in hum db)           │
│     • air_actions = optimize_air_recirculation() (if NOT in any db)        │
│                                                                             │
│     NOTE: All actions are collected and executed in ONE batch!             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 3: TEMPERATURE/HUMIDITY CONTROL                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.control_temperature_closed()                                 │
│ ClosedActions.control_humidity_closed()                                    │
│                                                                             │
│ Uses: _increase_temperature() / _decrease_temperature()                   │
│       _increase_humidity() / _decrease_humidity()                         │
│                                                                             │
│ Each method returns action_map (does NOT execute immediately!)             │
│ Actions are collected for batch execution                                  │
│                                                                             │
│ IMPORTANT: Uses OWN temp/hum targets, NOT VPD targets!                    │
│ • temp_target = await _get_reference_temperature_target()                   │
│ • hum_target = await _get_reference_humidity_target()                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: BATCH EXECUTION                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.execute_closed_environment_cycle()                           │
│                                                                             │
│ All actions combined into single action_map                                │
│ → action_manager.checkLimitsAndPublicateNoVPD(all_actions)                 │
│                                                                             │
│ Single LogForClient Event:                                                  │
│ {                                                                           │
│   "Name": "dev_room",                                                       │
│   "message": "Closed Environment: 5 actions executed",                     │
│   "actions": "canExhaust:Increase, canCool:Reduce, canDehumidify:Increase",│
│   "actionCount": 5,                                                         │
│   "tempDeviation": 1.6,                                                     │
│   "humDeviation": 0.8,                                                      │
│   "co2Status": "anheben",                                                   │
│   "tempStatus": "kuehlen",                                                  │
│   "humStatus": "entfeuchten",                                               │
│   "tempCurrent": 24.5,                                                      │
│   "tempTarget": 26.0,  ⚠️ OWN TARGET, NOT VPD TARGET!                     │
│   "humCurrent": 58.0,                                                       │
│   "humTarget": 60.0,   ⚠️ OWN TARGET, NOT VPD TARGET!                     │
│   "co2Current": 750,                                                        │
│   "co2TargetMin": 800,                                                      │
│   "co2TargetMax": 1500,                                                     │
│   "vpdCurrent": 1.05,  ⚠️ For informational purposes only!               │
│   "smartDeadbandActive": false                                              │
│ }                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 5: CONFLICT RESOLUTION & ENVIRONMENT GUARD                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.checkLimitsAndPublicateNoVPD()                            │
│                                                                             │
│ • Resolve action conflicts                                                  │
│ • Apply Environment Guard                                                   │
│ • Execute actions                                                           │
│                                                                             │
│ NOTE: No Core VPD Logic (Buffer Zones, VPD Context, Deviations)           │
│       Closed Environment has its own control logic!                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 6: DEVICE EXECUTION                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ ✅ IDENTICAL TO VPD PERFECTION                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Safety Mechanisms

### Overview

| Safety | VPD Perf | VPD Target | Closed Env |
|------------|-----------|------------|------------|
| **Night Hold** | ✅ (Power-Saving Mode) | ✅ (Power-Saving Mode) | ✅ (Power-Saving Mode - NEW!) |
| **Smart Deadband** | ✅ (with 15% hysteresis, climate reduced, air exchange reduced) | ✅ (with 15% hysteresis, climate reduced, air exchange reduced) | ✅ (VPD-based, with 15% hysteresis, returns bool) |
| **Humidity Critical Override** | ✅ | ✅ | ✅ |
| **Environment Guard** | ✅ (optional) | ✅ (optional) | ✅ (optional) |
| **Buffer Zones** | ✅ | ✅ | ✅ |
| **Conflict Resolution** | ✅ | ✅ | ✅ |
| **Weighted Deviations** | ✅ | ✅ | ✅ (all 0) |
| **Dampening/Cooldown** | ✅ (optional) | ✅ (optional) | ❌ |

### Smart Deadband (Advanced)

The Smart Deadband has been enhanced with **dynamic behavior**, **predictive logic**, and **energy-saving features**.

#### Dynamic Deadband Based on Plant Stage (VPD Perfection only)

```python
# Plant stage specific deadband values:
stage_deadbands = {
    "Germination": 0.03,  # Very sensitive phase
    "Clones": 0.03,       # Very sensitive phase
    "EarlyVeg": 0.05,     # Normal
    "MidVeg": 0.05,       # Normal
    "LateVeg": 0.04,      # More precise (transition to flower)
    "EarlyFlower": 0.04,  # More precise
    "MidFlower": 0.05,    # Normal
    "LateFlower": 0.05,   # Normal
}
```

**Why different deadbands?**
- **Sensitive phases** (Germination/Clones): Tighter control (±0.03 kPa)
- **Transition phases** (LateVeg/EarlyFlower): More precise (±0.04 kPa)
- **Stable phases** (MidVeg/MidFlower/LateFlower): Standard control (±0.05 kPa)

#### 3-Stage Gradual Reduction

Instead of immediately reducing all devices to minimum, the Smart Deadband now uses **gradual reduction** based on VPD stability:

| Stage | VPD Deviation | Climate Devices | Air-Exchange Devices | Description |
|-------|---------------|-----------------|---------------------|-------------|
| **Stage 1 (Soft)** | 0.02-0.04 kPa | 50% | 75% | Slight reduction, VPD close to target |
| **Stage 2 (Medium)** | 0.04-0.05 kPa | 25% | 50% | Moderate reduction, VPD in deadband |
| **Stage 3 (Full)** | Stable 2+ min | 10% | 25% | Maximum reduction, VPD stable |

**Benefits:**
- ✅ Faster response when VPD changes (not everything at minimum)
- ✅ Less oscillation (gradual changes)
- ✅ Better energy saving (only reduce what's needed)

#### Predictive Behavior with Trend Analysis

The Smart Deadband analyzes the **last 3 VPD values** to predict future behavior:

```python
def _calculate_trend(current_vpd: float) -> str:
    # Returns: 'towards_target', 'away_from_target', or 'stable'
    
    if current_deviation < previous_deviation * 0.9:
        return "towards_target"  # Getting better
    elif current_deviation > previous_deviation * 1.1:
        return "away_from_target"  # Getting worse
    else:
        return "stable"  # No significant change
```

**Trend-based Adjustments:**

| Trend | Stage 1 Threshold | Stage 2 Threshold | Behavior |
|-------|-------------------|-------------------|----------|
| **Towards Target** | 60% of deadband | 80% of deadband | More aggressive, enter earlier |
| **Stable** | 80% of deadband | 90% of deadband | Normal thresholds |
| **Away from Target** | 90% of deadband | 95% of deadband | Conservative, enter later |

**Example:**
- VPD deviation: 0.03 kPa
- Deadband: 0.05 kPa
- Trend: towards_target
- Result: Enter Stage 1 at 0.03 kPa (60% threshold) instead of 0.04 kPa (80% threshold)

#### Night Mode Behavior with Hysteresis

The Smart Deadband behavior changes based on `is_light_on` and `nightVPDHold` settings:

```python
is_night = not is_light_on
night_vpd_hold = controlOptions.nightVPDHold
```

**Behavior Matrix:**

| Light Status | NightHoldVPD | Deadband Behavior | Device Behavior |
|-------------|--------------|-------------------|-----------------|
| **ON** | Any | ✅ Active with hysteresis | Normal VPD control |
| **OFF** | `True` | ✅ Active with hysteresis | Normal VPD control at night |
| **OFF** | `False` | ❌ Inactive (blocked) | Power-saving mode only |

**Power-Saving Mode (Light OFF + NightHoldVPD=False):**
```python
if is_night and not night_vpd_hold:
    # Deadband is NOT active
    # Night Hold Fallback is used instead
    
    # Climate Devices (Minimized to save power):
    • canHeat, canCool, canHumidify, canDehumidify → Reduce (OFF)
    • canClimate, canCO2, canLight → Reduce (OFF)
    
    # Ventilation Devices (Active for mold prevention):
    • canExhaust → Increase (Air exchange!)
    • canVentilate → Increase (Air circulation!)
    • canWindow → Increase (Air exchange!)
    • canIntake → Variable (based on outside temp)
```

**Why this behavior?**
- **Night with NightHoldVPD=True**: User wants VPD control at night → Deadband runs with hysteresis
- **Night with NightHoldVPD=False**: Power-saving priority → No deadband, only active ventilation for mold prevention

#### Hysteresis for Oscillation Prevention (NEW!)

The Smart Deadband now includes **15% hysteresis** to prevent oscillation at deadband boundaries:

```python
# Configuration
hysteresis_factor = 1.15  # 15% hysteresis
min_hold_after_exit = 120  # Max 2 minutes hold after exit
```

**How Hysteresis Works:**

```
Example: Deadband = ±0.05 kPa, Target = 1.10 kPa

Entry Condition:
  deviation <= deadband (e.g., VPD=1.08 → |1.08-1.10| = 0.02 ≤ 0.05 ✅)
  → Enters deadband
  
Exit Condition (WITH Hysteresis):
  deviation > exit_threshold (e.g., VPD=1.16 → |1.16-1.10| = 0.06 > 0.0575 ✅)
  → Exits deadband

  where exit_threshold = deadband * 1.15 = 0.05 * 1.15 = 0.0575
  
Boundary Oscillation Example:
  VPD: 1.049 → deviation=0.051 > 0.0575? NO ✅ → Still in deadband
  VPD: 1.050 → deviation=0.050 > 0.0575? NO ✅ → Still in deadband
  VPD: 1.051 → deviation=0.049 > 0.0575? NO ✅ → Still in deadband
  
  → NO OSCILLATION even with slight fluctuations!
```

**Re-Entry Block after Exit:**

```python
# After exiting deadband, wait minimum 120s before re-entering
if _deadband_last_exit_time:
    time_since_exit = now - _deadband_last_exit_time
    if time_since_exit < 120:  # 2 minutes
        return  # Block re-entry
```

**Benefits of Hysteresis:**
- ✅ **Stability**: Prevents constant entering/exiting at boundaries
- ✅ **Reduced Device Wear**: Fewer device state changes
- ✅ **Smoother Operation**: Gradual transitions instead of rapid toggling
- ✅ **Better Energy Efficiency**: Devices stay in optimal state longer

**Logging (INFO Level + LogForClient):**
```json
{
  "Name": "Room1",
  "message": "Smart Deadband Stage 2 active - hold: 120s, trend: stable",
  "VPDStatus": "InDeadband",
  "currentVPD": 1.10,
  "targetVPD": 1.10,
  "deadband": 0.05,
  "exitThreshold": 0.0575,
  "deviation": 0.00,
  "holdTimeRemaining": 120,
  "holdDuration": 300,
  "stage": 2,
  "trend": "stable",
  "mode": "VPD Perfection",
  "hysteresisFactor": 1.15,
  "deadbandActive": true
}
```

#### Maximum Deadband Time with Periodic Checks

```python
# Configuration
max_deadband_time = 600 seconds  # 10 minutes
deadband_check_interval = 30 seconds  # Check every 30 seconds
deadband_hold_duration = 300 seconds  # 5 minutes hold time
hysteresis_factor = 1.15  # 15% hysteresis for exit
min_hold_after_exit = 120  # Max 2 minutes hold after exit
```

**Behavior:**
1. **Max Time (10 min)**: After 10 minutes in deadband, automatically exit
2. **Periodic Checks (30 sec)**: Every 30 seconds, check if VPD still stable
3. **Auto-Extension**: If VPD stable and trending towards target, extend for another 5 minutes
4. **Early Exit with Hysteresis**: If `deviation > deadband * 1.15`, exit immediately (prevents oscillation)
5. **Re-Entry Block**: Minimum 120 seconds wait time after exit before re-entering deadband

### Implementation Details

**_handle_smart_deadband() (OGBModeManager.py) - Updated with Return Value:**

```python
async def _handle_smart_deadband(
    self, current_vpd: float, target_vpd: float, deadband: float, mode_name: str
) -> bool:
    """
    Smart Deadband Handler - Advanced version with dynamic stages and predictive logic.

    Returns:
        bool: True if deadband is active, False if deadband is blocked
              (e.g., night mode without nightVPDHold)
    """
    # ... deadband logic ...

    if is_night and not night_vpd_hold:
        # Night mode without VPD hold - no deadband
        if self._is_in_deadband:
            self._reset_deadband_state()
        return False  # Deadband is NOT active

    # ... deadband logic ...

    # Fixed Hold Time Extension Logic:
    # Extend only if trend is good AND within hysteresis zone
    if hold_remaining <= 0:
        if trend == "stable" or trend == "towards_target":
            if deviation <= self._deadband_exit_threshold:
                # Both conditions met: stable trend AND within hysteresis zone
                self._deadband_hold_start = now
                _LOGGER.info("Extending deadband - VPD stable and within hysteresis zone")
            else:
                # Trend is good but outside hysteresis zone - exit
                self._reset_deadband_state()
                return False
        else:
            # Trend is bad - exit
            self._reset_deadband_state()
            return False

    return True  # Deadband IS active
```

**Device State Restoration (Device.py):**

```python
async def on_smart_deadband_entered(self, data) -> None:
    """Save current state before entering deadband."""
    if self.isDimmable:
        self._pre_deadband_duty_cycle = self.dutyCycle
    else:
        self._pre_deadband_is_running = self.isRunning

    await self.setToMinimum()
    self._in_smart_deadband = True

async def on_smart_deadband_exited(self, data) -> None:
    """Restore previous state when exiting deadband."""
    if self._in_smart_deadband:
        self._in_smart_deadband = False
        await self.restoreFromMinimum()

async def restoreFromMinimum(self):
    """Restore device to previous state before entering deadband."""
    if self.isDimmable:
        if self._pre_deadband_duty_cycle is not None:
            clamped = self.clamp_duty_cycle(self._pre_deadband_duty_cycle)
            await self.turn_on(percentage=clamped)
    else:
        if self._pre_deadband_is_running is not None:
            if self._pre_deadband_is_running:
                await self.turn_on()
            else:
                await self.turn_off()
```

**_calculate_dynamic_deadband() (OGBModeManager.py):**

```python
def _calculate_dynamic_deadband(self, mode_name: str) -> float:
    """Calculate dynamic deadband based on plant stage and mode."""
    
    if mode_name == "VPD Perfection":
        plant_stage = self.data_store.get("plantStage") or "MidVeg"
        stage_deadbands = {
            "Germination": 0.03,
            "Clones": 0.03,
            "EarlyVeg": 0.05,
            "MidVeg": 0.05,
            "LateVeg": 0.04,
            "EarlyFlower": 0.04,
            "MidFlower": 0.05,
            "LateFlower": 0.05,
        }
        return stage_deadbands.get(plant_stage, 0.05)
        
    elif mode_name == "VPD Target":
        # Use tolerance from settings
        tolerance = self.data_store.getDeep("controlOptionData.deadband.vpdTargetDeadband")
        return float(tolerance) if tolerance else 0.05
        
    elif mode_name == "Closed Environment":
        return 0.05  # Fixed deadband
```

**_determine_deadband_stage() (OGBModeManager.py):**

```python
def _determine_deadband_stage(self, deviation: float, deadband: float, trend: str) -> int:
    """Determine reduction stage based on deviation and trend."""
    
    relative_deviation = deviation / deadband
    
    # Trend-based thresholds
    if trend == "towards_target":
        stage_1_threshold = 0.60  # 60%
        stage_2_threshold = 0.80  # 80%
    elif trend == "away_from_target":
        stage_1_threshold = 0.90  # 90%
        stage_2_threshold = 0.95  # 95%
    else:
        stage_1_threshold = 0.80  # 80%
        stage_2_threshold = 0.90  # 90%
    
    # Determine stage
    if relative_deviation < stage_1_threshold:
        return 1  # Soft
    elif relative_deviation < stage_2_threshold:
        return 2  # Medium
    elif stability_duration >= 120:  # 2 minutes stable
        return 3  # Full
    else:
        return 2  # Stay at medium
```

#### Log Output Example

```json
{
  "Name": "Room1",
  "message": "Smart Deadband Stage 2 active - hold: 245s, trend: towards_target",
  "VPDStatus": "InDeadband",
  "currentVPD": 1.23,
  "targetVPD": 1.20,
  "deadband": 0.05,
  "deviation": 0.03,
  "holdTimeRemaining": 245,
  "holdDuration": 300,
  "stage": 2,
  "trend": "towards_target",
  "mode": "VPD Perfection",
  "devicesDimmed": ["canCool:25%", "canExhaust:50%"],
  "devicesReduced": [],
  "ventilationRunning": ["canVentilate"],
  "lightStatus": "running (unchanged)",
  "deadbandActive": true
}
```

### Summary of Smart Deadband Features

| Feature | Before | After |
|---------|--------|-------|
| **Deadband Size** | Fixed 0.05 kPa | Dynamic based on plant stage |
| **Reduction** | Immediate to 10% | 3-stage gradual (50% → 25% → 10%) |
| **Trend Analysis** | None | Last 3 VPD values analyzed |
| **Night Mode** | Always active | Only with nightVPDHold |
| **Max Time** | 2.5 minutes | 10 minutes with 30s checks |
| **Extension** | Automatic | Only if stable/towards_target |
| **Exit Threshold** | Immediate at deadband | 15% hysteresis (deadband * 1.15) |
| **Re-Entry Block** | None | Minimum 120 seconds after exit |

---

## Deadband & Quiet Zone

### Overview

The Smart Deadband is a sophisticated feature that reduces device activity when VPD is within an acceptable range, providing:

- **Energy Savings**: Devices run at minimum (10%-25%-50%)
- **Reduced Wear**: Fewer device state changes
- **Oscillation Prevention**: 15% hysteresis prevents boundary oscillation
- **Stability Tracking**: Trend analysis and periodic checks

### How It Works

```
┌─────────────────────────────────────────────────────────────────────┐
│ DEADBAND FLOW DIAGRAM                                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  VPD Reading → Calculate Deviation → Check Deadband                │
│                                                                     │
│  IF deviation <= deadband (e.g., 0.05 kPa):                       │
│    ├─ Check night mode + nightVPDHold                             │
│    │  ├─ Light OFF + nightVPDHold=False → RETURN False (blocked) │
│    │  └─ Light ON OR nightVPDHold=True → CONTINUE                 │
│    │                                                               │
│    ├─ Check if already in deadband                               │
│    │  ├─ NO → Enter deadband (set flag, emit events)            │
│    │  └─ YES → Continue (skip entry)                             │
│    │                                                               │
│    ├─ Check time since exit (re-entry block)                      │
│    │  └─ < 120s → RETURN False (block re-entry)                  │
│    │                                                               │
│    ├─ Determine stage (based on deviation + trend)                │
│    │  ├─ Stage 1 (Soft): Climate 50%, Air 75%                   │
│    │  ├─ Stage 2 (Medium): Climate 25%, Air 50%                 │
│    │  └─ Stage 3 (Full): Climate 10%, Air 25%                   │
│    │                                                               │
│    ├─ Emit SmartDeadbandEntered events                            │
│    ├─ Reduce devices according to stage                           │
│    ├─ Emit LogForClient (INFO + hysteresis info)                  │
│    └─ RETURN True (deadband active)                               │
│                                                                     │
│  ELIF deviation > exit_threshold (deadband * 1.15):                │
│    ├─ Exit deadband                                                │
│    ├─ Record exit time (for re-entry block)                       │
│    ├─ Emit SmartDeadbandExited events                             │
│    ├─ Reset all deadband state                                    │
│    └─ RETURN False (deadband not active)                          │
│                                                                     │
│  ELSE:                                                             │
│    ├─ Continue normal operation                                    │
│    └─ Process VPD actions (increase/reduce)                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

🆕 NEW: _handle_smart_deadband() now returns bool:
     • True  → Deadband is active (devices reduced)
     • False → Deadband is NOT active (blocked, e.g., night mode)
```

### Deadband vs Quiet Zone

| Aspect | Smart Deadband | Quiet Zone |
|--------|----------------|------------|
| **Purpose** | Reduce device activity when VPD is good | Pause all VPD control temporarily |
| **Trigger** | VPD within ±deadband of target | VPD in deadband AND quiet zone active |
| **Duration** | Up to 10 minutes (extendable) | Until conditions change |
| **Device State** | Reduced (10%-25%-50%) | Paused |
| **Events** | SmartDeadbandEntered/Exited | Quiet Zone signal |
| **Log Level** | INFO (with hysteresis info) | INFO (devices paused) |

### Example Scenarios

**Scenario 1: Perfect VPD (Deadband Active)**
```
VPD = 1.10, Target = 1.10, Deadband = ±0.05
Deviation = 0.00 ≤ 0.05 → IN DEADBAND

Stage 3 (Full): Stable 2+ minutes
- Heater: 10% (reduced from 80%)
- Cooler: 10% (reduced from 60%)
- Exhaust: 25% (reduced from 70%)
- Ventilation: 100% (unchanged)

Log: "Smart Deadband Stage 3 active - hold: 120s, trend: stable"
```

**Scenario 2: Boundary Oscillation (Hysteresis Prevents Chaos)**
```
VPD oscillates: 1.049, 1.050, 1.051, 1.050, 1.049
Target = 1.10, Deadband = ±0.05, Exit Threshold = 0.0575

Cycle 1: VPD=1.049, deviation=0.051, exit_threshold=0.0575
  → 0.051 > 0.0575? NO → STAYS IN DEADBAND

Cycle 2: VPD=1.050, deviation=0.050, exit_threshold=0.0575
  → 0.050 > 0.0575? NO → STAYS IN DEADBAND

Cycle 3: VPD=1.051, deviation=0.049, exit_threshold=0.0575
  → 0.049 > 0.0575? NO → STAYS IN DEADBAND

Result: NO OSCILLATION, devices remain reduced!
```

**Scenario 3: Night Mode with NightHoldVPD=True**
```
Light: OFF, NightHoldVPD: TRUE
VPD = 1.08, Target = 1.10, Deadband = ±0.05
Deviation = 0.02 ≤ 0.05 → DEADBAND ACTIVE WITH HYSTERESIS

Deadband runs normally at night:
- Climate devices reduced to 10%-25%-50%
- Air exchange devices reduced
- Ventilation continues for air circulation
- Hold time: 2.5 minutes
- Exit threshold: 0.0575 (15% hysteresis)

Log: "Night mode WITH NightHoldVPD - deadband active with hysteresis"
```

**Scenario 4: Night Mode with NightHoldVPD=False**
```
Light: OFF, NightHoldVPD: FALSE
VPD = 1.08, Target = 1.10

DEADBAND IS NOT ACTIVE (blocked by night mode)

Night Hold Power-Saving Mode:
- Climate devices: Reduce (OFF) - save power
- Ventilation devices: Increase - mold prevention!
  • Exhaust: Increase (air exchange)
  • Ventilate: Increase (air circulation)
  • Window: Increase (air exchange)
  • Intake: Variable (based on outside temp)

Log: "Night Hold Power-Saving Mode - Climate minimized, Ventilation active"
```

### Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `deadband` | 0.05 kPa | Base deadband value (varies by plant stage) |
| `hysteresis_factor` | 1.15 (15%) | Exit threshold = deadband × 1.15 |
| `min_hold_after_exit` | 120 seconds | Minimum wait time before re-entry |
| `max_deadband_time` | 600 seconds (10 min) | Maximum time in deadband |
| `deadband_check_interval` | 30 seconds | Check interval during deadband |
| `deadband_hold_duration` | 300 seconds (5 min) | Base hold time |

### Logging

**Deadband Entry (INFO):**
```json
{
  "Name": "Room1",
  "message": "VPD 1.10 entered deadband ±0.05 of target 1.10 - starting smart deadband (hold: 300s)",
  "currentVPD": 1.10,
  "targetVPD": 1.10,
  "deadband": 0.05,
  "exitThreshold": 0.0575,
  "hysteresisFactor": 1.15,
  "mode": "VPD Perfection"
}
```

**Deadband Exit (INFO):**
```json
{
  "Name": "Room1",
  "message": "VPD 1.16 EXITED deadband with hysteresis (deviation: 0.06 > exit_threshold: 0.0575, deadband: 0.05, last_exit: 0s ago) - exiting deadband"
}
```

**Deadband Active (INFO - Every 30s):**
```json
{
  "Name": "Room1",
  "message": "Smart Deadband Stage 2 active - hold: 120s, trend: stable",
  "VPDStatus": "InDeadband",
  "currentVPD": 1.10,
  "targetVPD": 1.10,
  "deadband": 0.05,
  "exitThreshold": 0.0575,
  "deviation": 0.00,
  "holdTimeRemaining": 120,
  "holdDuration": 300,
  "stage": 2,
  "trend": "stable",
  "mode": "VPD Perfection",
  "hysteresisFactor": 1.15,
  "deadbandActive": true,
  "devicesDimmed": ["canHeat:25%", "canCool:25%"],
  "devicesReduced": ["canHumidify", "canDehumidify"],
  "ventilationRunning": ["canVentilate"]
}
```

---

## Closed Environment Night Mode Power-Saving

### Overview

Closed Environment Mode now includes **Night Mode Power-Saving** functionality, which is activated when:
- Light is OFF (`isPlantDay.islightON = False`)
- AND `nightVPDHold = False`

### Power-Saving Logic

When Night Mode Power-Saving is active, the system optimizes for energy efficiency while maintaining mold prevention:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ NIGHT MODE POWER-SAVING FOR CLOSED ENVIRONMENT                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│ Climate Devices (Minimized to Save Power):                                  │
│ • canHeat       → Reduce (Heater OFF)                                      │
│ • canCool       → Reduce (Cooler OFF)                                      │
│ • canHumidify   → Reduce (Humidifier OFF)                                  │
│ • canDehumidify→ Reduce (Dehumidifier OFF)                                │
│ • canClimate    → Reduce (Climate OFF)                                     │
│ • canCO2        → Reduce (CO2 OFF)                                         │
│ • canLight      → Reduce (Light OFF)                                       │
│                                                                             │
│ Ventilation Devices (Active for Mold Prevention):                           │
│ • canExhaust    → Increase (Air exchange!)                                 │
│ • canVentilate  → Increase (Air circulation!)                              │
│ • canWindow     → Increase (Air exchange!)                                 │
│ • canIntake     → Variable (based on outside temperature)                   │
│                                                                             │
│ Intake Logic:                                                              │
│ IF outside_temp >= (minTemp - 3°C):                                         │
│    → Increase (Outside air is warm enough)                                 │
│ ELSE:                                                                      │
│    → Reduce (Too cold, save heating)                                       │
│                                                                             │
│ ⚠️ IMPORTANT: VPD is NOT used for control in Closed Environment!           │
│ • VPD is only used for Smart Deadband check                                 │
│ • Temperature and Humidity use their OWN targets                            │
│ • VPD in log is for informational purposes only                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Example Log Output (Night Mode Power-Saving)

```json
{
  "Name": "dev_room",
  "message": "Night Mode Power-Saving: Climate minimized, Ventilation active - 8 actions",
  "actions": "canHeat:Reduce, canCool:Reduce, canHumidify:Reduce, canDehumidify:Reduce, canClimate:Reduce, canCO2:Reduce, canExhaust:Increase, canVentilate:Increase",
  "actionCount": 8,
  "tempCurrent": 18.5,
  "humCurrent": 65.0,
  "co2Current": 600,
  "vpdCurrent": 0.65,
  "isNightMode": true,
  "nightVPDHold": false
}
```

### Key Differences to VPD Perfection/Target Night Mode

| Aspect | VPD Perfection/Target | Closed Environment |
|--------|---------------------|-------------------|
| **Night Mode Trigger** | `is_light_on = False AND nightVPDHold = False` | Same |
| **Climate Devices** | Reduced/Minimized | Reduced (OFF) |
| **VPD Control** | Paused (no VPD actions) | N/A (no VPD control) |
| **Temp/Hum Control** | Paused | Uses tentData min/max limits (for display: midpoint of min/max) |
| **Ventilation** | Active (mold prevention) | Active (mold prevention) |
| **VPD in Log** | `vpdCurrent`, `vpdTarget`, `vpdDeviation`, `vpdStatus` | `vpdCurrent` (informational only) |
| **Control Logic** | VPD-based control paused | Control based on tentData min/max limits |

---

## Recent Bug Fixes and Improvements

### Bug Fixes (2026-04-07)

#### 1. **✅ Fixed: Hold Time Extension Logic** (OGBModeManager.py:518-537)

**Problem:** When hold time elapsed and trend was "away_from_target", the deadband state was reset then immediately extended, causing inconsistent behavior.

**Fix:** Modified the hold time extension logic to only extend when BOTH conditions are met:
- Trend is good ("stable" OR "towards_target")
- AND VPD is within hysteresis zone (`deviation <= exit_threshold`)

**Before:**
```python
if hold_remaining <= 0:
    if trend == "stable" or trend == "towards_target":
        self._deadband_hold_start = now  # Extend
    else:
        self._reset_deadband_state()  # Reset
    if deviation <= self._deadband_exit_threshold:
        self._deadband_hold_start = now  # CONFLICT: Reset then extended!
```

**After:**
```python
if hold_remaining <= 0:
    if trend == "stable" or trend == "towards_target":
        if deviation <= self._deadband_exit_threshold:
            # Both conditions met: extend
            self._deadband_hold_start = now
        else:
            # Trend is good but outside hysteresis zone: exit
            self._reset_deadband_state()
            return False
    else:
        # Trend is bad: exit
        self._reset_deadband_state()
        return False
```

#### 2. **✅ Fixed: Device State Not Restored on Exit** (Device.py:2592-2612)

**Problem:** When devices exited deadband, they only set `_in_smart_deadband = False` but did NOT restore their previous state, potentially leaving devices at minimum for an extended period.

**Fix:** Implemented state saving and restoration:
1. Save current state (`_pre_deadband_duty_cycle` or `_pre_deadband_is_running`) before entering deadband
2. Restore previous state when exiting deadband via `restoreFromMinimum()`

**Changes:**
- Added `_pre_deadband_duty_cycle` and `_pre_deadband_is_running` attributes
- Modified `on_smart_deadband_entered()` to save current state
- Added `restoreFromMinimum()` method to restore previous state
- Modified `on_smart_deadband_exited()` to call `restoreFromMinimum()`

#### 3. **✅ Removed: Redundant "Reduce" Events** (OGBModeManager.py:494-516)

**Problem:** Deadband handler emitted "Reduce" events for devices, but these were immediately blocked by device handlers because `_in_smart_deadband` was already `True`. This was inefficient and unnecessary.

**Fix:** Removed the redundant "Reduce" event emission (lines 494-516). The `SmartDeadbandEntered` event already handles device reduction via `setToMinimum()`.

**Impact:** Reduced event traffic and improved efficiency.

### CO2 Control Behavior (Design Decision)

The CO2 control behavior is intentionally different across modes:

| Mode | CO2 in Deadband | CO2 outside Deadband | Reasoning |
|------|----------------|------------------------|-----------|
| VPD Perfection | ❌ No | ✅ Yes | Optimize for energy when VPD is already in target zone |
| Closed Environment | ✅ Yes | ✅ Yes | CO2 is critical for sealed chambers, maintain even in deadband |
| VPD Target | ❌ No | ❌ No | Simpler mode focused on VPD control only |

This is a **design decision**, not a bug. Each mode has its own optimization strategy.

### Test Coverage

All fixes are covered by comprehensive tests:

- **test_deadband_hysteresis.py**: 17 tests (all passing)
  - Hysteresis exit threshold
  - Night mode blocking
  - Smart Deadband return values
  - Hold time extension with good trend

- **test_closed_environment_deadband.py**: 6 tests (all passing)
  - Night mode power-saving
  - Smart Deadband respect
  - Own targets vs VPD targets
  - VPD as informational only

- **test_closed_environment_manager.py**: 5 tests (all passing)
  - Cycle execution
  - Target usage
  - Night mode handling

- **test_deadband_return_value.py**: 6 tests (all passing)
  - Return value consistency across all modes
  - Caller usage verification

---

## Dampening Control (Core Logic vs Dampening Features)

### Concept

The system has been refactored to clearly separate **Core VPD Logic** from **Dampening Features**:

1. **Core VPD Logic** (ALWAYS active for VPD Perfection and VPD Target):
   - Buffer Zones (prevent oscillation near limits)
   - VPD Context (priorities based on VPD status)
   - Deviations-based actions (intelligent additional actions)
   - Conflict resolution (resolve contradictory actions)

2. **Dampening Features** (only when `vpdDeviceDampening = True`):
   - Cooldown filtering (user-defined base cooldowns)
   - Repeat cooldown (prevent immediate same action)
   - Emergency override (bypass cooldown in critical conditions)

3. **Closed Environment**: Has its own cycle, only needs:
   - Conflict resolution
   - Environment Guard
   - Execute

**IMPORTANT**: Core VPD Logic is **ONLY** applied to VPD Perfection and VPD Target modes, NOT to Closed Environment or Premium modes (PID/MPC/AI).

### Mode-Specific Behavior

| Mode | Core VPD Logic | Dampening Features | Environment Guard |
|------|----------------|-------------------|-------------------|
| **VPD Perfection** | ✅ Active (Buffer Zones, VPD Context, Conflicts) | ⚙️ Optional (if `vpdDeviceDampening = True`) | ✅ Active |
| **VPD Target** | ✅ Active (Buffer Zones, VPD Context, Conflicts) | ⚙️ Optional (if `vpdDeviceDampening = True`) | ✅ Active |
| **Closed Environment** | ❌ Not used (has own logic) | ❌ Not used | ✅ Active |
| **PID/MPC/AI Control** | ❌ Not used | ❌ Not used | ✅ Active |
| **Script Mode** | ❌ Not used | ❌ Not used | ✅ Active |

### Configuration

```python
"controlOptions": {
    "vpdDeviceDampening": False,  # Default: False (user-defined base cooldowns only)
    "adaptiveCooldownEnabled": False,  # Default: False (user says x, gets x)
    "emergencyCooldownFactor": 0.5,  # Default: 0.5 (50% reduction in emergency)
    "adaptiveCooldownThresholds": {  # Optional: Only if adaptiveCooldownEnabled = True
        "critical": 5.0,
        "high": 3.0,
        "near": 1.0,
        "veryNear": 0.5
    },
    "adaptiveCooldownFactors": {  # Optional: Only if adaptiveCooldownEnabled = True
        "critical": 1.5,
        "high": 1.2,
        "near": 2.0,
        "veryNear": 3.0
    }
}
```

### Default Device Cooldowns

```python
DEFAULT_DEVICE_COOLDOWNS = {
    "canHumidify": 5,   # Befeuchter braucht Zeit
    "canDehumidify": 3, # Entfeuchter braucht noch mehr Zeit
    "canHeat": 3,       # Heizung reagiert relativ schnell
    "canCool": 3,       # Kühlung braucht etwas Zeit
    "canExhaust": 1,    # Abluft reagiert schnell
    "canIntake": 1,     # Zuluft reagiert schnell
    "canVentilate": 1,  # Ventilation reagiert schnell
    "canWindow": 1,     # Window actuator reacts quickly
    "canDoor": 1,       # Door contact events should not spam
    "canLight": 1,      # Licht reagiert sofort, aber VPD-Effekt braucht Zeit
    "canCO2": 2,        # CO2 braucht Zeit zur Verteilung
    "canClimate": 2,    # Klima-System braucht Zeit
}
```

### Implementation

**VPD Perfection Mode (OGBActionManager.py):**

```python
async def checkLimitsAndPublicate(self, actionMap: List):
    """Process VPD Perfection actions with clean separation of Core Logic and Dampening."""
    
    # Check mode - Core VPD Logic only for VPD Perfection and VPD Target
    mode = self.data_store.get("tentMode")
    vpd_modes = {"VPD Perfection", "VPD Target"}
    is_vpd_mode = mode in vpd_modes
    
    if not is_vpd_mode:
        # For non-VPD modes (Closed Environment, PID, MPC, AI, Script)
        final_actions = await self._apply_environment_guard(actionMap)
        await self.publicationActionHandler(final_actions)
        return
    
    # Night Hold and Deadband checks
    if not await self._check_vpd_night_hold(actionMap):
        return
    
    in_deadband, reason = self._is_vpd_in_deadband()
    if in_deadband:
        await self._emit_quiet_zone_idle()
        return
    
    # Calculate deviations
    (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
     tempWeight, humWeight, tempPercentage, humPercentage, weightMessage) = \
        self._calculate_weighted_deviations(tent_data)
    
    # STEP 1: CORE VPD LOGIC (ALWAYS active)
    if self.dampening_actions:
        enhanced_actions = await self.dampening_actions.process_core_vpd_logic(
            actionMap, weighted_temp_dev, weighted_hum_dev, tent_data
        )
    else:
        enhanced_actions = self._resolve_action_conflicts(actionMap)
    
    # STEP 2: DAMPENING FEATURES (only if enabled)
    dampening_enabled = self.data_store.getDeep("controlOptions.vpdDeviceDampening", False)
    blocked_actions = []
    
    if dampening_enabled and self.dampening_actions:
        filtered_actions, blocked_actions = await self.dampening_actions.process_dampening_features(
            enhanced_actions, weighted_temp_dev, weighted_hum_dev, tent_data
        )
        final_actions = self._resolve_action_conflicts(filtered_actions)
    else:
        final_actions = enhanced_actions
    
    # STEP 3: ENVIRONMENT GUARD (always active)
    final_actions = await self._apply_environment_guard(final_actions)
    
    # STEP 4: Execute actions
    await self.publicationActionHandler(final_actions)
    
    # Log results
    await self._log_vpd_results(
        real_temp_dev, real_hum_dev, tempPercentage, humPercentage,
        final_actions, blocked_actions, dampening_enabled
    )
```

**VPD Target Mode (OGBActionManager.py):**

Similar to VPD Perfection, but uses VPD deviation only (no temp/hum weighted deviations).

**Closed Environment Mode (OGBActionManager.py):**

```python
async def checkLimitsAndPublicateNoVPD(self, actionMap: List):
    """
    Closed Environment: Only conflict resolution + Environment Guard.
    No Core VPD Logic (Buffer Zones, VPD Context, Deviations-based Actions).
    """
    if not actionMap:
        return
    
    # Calculate deviations for logging only
    tent_data = self.data_store.get("tentData")
    (real_temp_dev, real_hum_dev, weighted_temp_dev, weighted_hum_dev,
     tempWeight, humWeight, tempPercentage, humPercentage, weightMessage) = \
        self._calculate_weighted_deviations(tent_data)
    
    final_actions = actionMap
    
    # Only conflict resolution (no Core VPD Logic)
    if self.dampening_actions:
        final_actions = self.dampening_actions._resolve_action_conflicts(actionMap)
    
    # Environment Guard
    final_actions = await self._apply_environment_guard(final_actions)
    await self.publicationActionHandler(final_actions)
```

### Core VPD Logic Details

**process_core_vpd_logic() (OGBDampeningActions.py):**

```python
async def process_core_vpd_logic(
    self, action_map: List, temp_deviation: float, hum_deviation: float, 
    tent_data: Dict[str, Any]
) -> List:
    """Process Core VPD Logic (ALWAYS active)."""
    
    # 1. Enhance action map
    enhanced_actions = self._enhance_action_map(
        action_map, temp_deviation, hum_deviation, tent_data, caps,
        vpd_light_control, is_light_on, optimal_devices, vpd_status
    )
    #   - Apply buffer zones (prevent oscillation near limits)
    #   - Add VPD context enhancements (priorities based on VPD status)
    #   - Add deviations-based actions (intelligent additional actions)
    
    # 2. Resolve conflicts
    final_actions = self._resolve_action_conflicts(enhanced_actions)
    #   - Remove contradictory actions
    #   - Keep highest priority action per capability
    
    return final_actions
```

### Dampening Features Details

**process_dampening_features() (OGBDampeningActions.py):**

```python
async def process_dampening_features(
    self, action_map: List, temp_deviation: float, hum_deviation: float,
    tent_data: Dict[str, Any]
) -> Tuple[List, List]:
    """Process Dampening Features (only when vpdDeviceDampening = True)."""
    
    # 1. Check emergency conditions
    emergency_conditions = self.action_manager._getEmergencyOverride(tent_data)
    if emergency_conditions:
        self.action_manager._clearCooldownForEmergency(emergency_conditions)
    
    # 2. Apply cooldown filtering
    dampened_actions, blocked_actions = (
        self.action_manager._filterActionsByDampening(
            action_map, temp_deviation, hum_deviation
        )
    )
    #   - Check if device is in cooldown
    #   - Check if same action is repeating too quickly
    #   - Apply user-defined base cooldowns
    
    # 3. Resolve conflicts
    final_actions = self._resolve_action_conflicts(dampened_actions)
    
    return final_actions, blocked_actions
```

### Adaptive Cooldown Behavior

**Default (adaptiveCooldownEnabled = False):**

```python
def _calculateAdaptiveCooldown(self, capability: str, deviation: float) -> float:
    baseCooldown = self.defaultCooldownMinutes.get(capability, 2)
    
    # Check if adaptive cooldown is enabled
    adaptive_enabled = self.data_store.getDeep("controlOptions.adaptiveCooldownEnabled", False)
    if not adaptive_enabled:
        # User says x, user gets x!
        if self._emergency_mode:
            # In emergency: Reduce cooldown for faster response
            emergency_factor = self.data_store.getDeep("controlOptions.emergencyCooldownFactor", 0.5)
            return baseCooldown * emergency_factor
        return baseCooldown
    
    # Adaptive cooldown is enabled - apply factors
    # (only if user explicitly enabled this feature)
    ...
```

**Key Points:**
- **Default**: `adaptiveCooldownEnabled = False`
- **Normal mode**: User says 3 minutes, gets 3 minutes
- **Emergency mode**: Cooldown reduced by 50% for faster response
- **Optional adaptive**: User can enable adaptive cooldown with configurable thresholds and factors

### Log Differences

**With Dampening (vpdDeviceDampening = True):**
```json
{
  "Name": "Room1",
  "message": "VPD Perfection: Core Logic + Dampening: 5 actions executed (2 blocked by cooldown)",
  "actions": "canExhaust:Increase, canCool:Reduce, canDehumidify:Increase",
  "actionCount": 5,
  "blockedActions": 2,
  "dampeningEnabled": true,
  "tempDeviation": 1.6,
  "humDeviation": 0.8,
  "tempPercentage": 40.0,
  "humPercentage": 5.7,
  "vpdStatus": "medium"
}
```

**Without Dampening (vpdDeviceDampening = False):**
```json
{
  "Name": "Room1",
  "message": "VPD Perfection: Core Logic only (dampening disabled): 7 actions executed",
  "actions": "canExhaust:Increase, canCool:Reduce, canHumidify:Increase, canDehumidify:Increase",
  "actionCount": 7,
  "blockedActions": 0,
  "dampeningEnabled": false,
  "tempDeviation": 1.6,
  "humDeviation": 0.8,
  "tempPercentage": 40.0,
  "humPercentage": 5.7
}
```

### Summary of Changes

**Before Refactor:**
- `vpdDeviceDampening = False` → NO Core VPD Logic, NO Dampening → Oszillation possible
- `vpdDeviceDampening = True` → Everything (Core VPD Logic + Dampening)
- Adaptive Cooldown was always active (hardcoded)

**After Refactor:**
- Core VPD Logic (Buffer Zones, VPD Context, Conflicts) → **ALWAYS** active for VPD Perfection and VPD Target
- Dampening Features (Cooldown, Emergency Override) → **ONLY** if `vpdDeviceDampening = True`
- Adaptive Cooldown → **Disabled by default** (user says x, gets x)
- Closed Environment → Uses only Conflict Resolution + Environment Guard (no Core VPD Logic)
- Premium Modes (PID/MPC/AI) → No Core VPD Logic
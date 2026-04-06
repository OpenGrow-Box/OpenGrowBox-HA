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
│ 2.2 🆕 SMART DEADBAND CHECK (NEW!)                                          │
│     deadband = 0.05 (default)                                               │
│     deviation = |currentVPD - perfectionVPD|                                │
│                                                                             │
│     IF deviation <= deadband:                                               │
│       → _handle_smart_deadband() called                                     │
│       → Climate devices reduced to minimum                                  │
│       → Air exchange devices (Exhaust, Intake, Window) reduced             │
│       → Ventilation continues running                                      │
│       → Hold time: 2.5 minutes                                              │
│       → LogForClient: "Smart Deadband active"                               │
│       → RETURN (no VPD events!)                                             │
│     ELSE:                                                                   │
│       → _reset_deadband_state()                                             │
│       → Continue to Step 2.3                                                │
│                                                                             │
│ 2.3 VPD Decision (only if outside deadband):                               │
│     IF currentVPD < perfectMinVPD:                                          │
│        → Emit: "increase_vpd"                                               │
│     ELIF currentVPD > perfectMaxVPD:                                        │
│        → Emit: "reduce_vpd"                                                 │
│     ELIF currentVPD != perfectionVPD:                                       │
│        → Emit: "FineTune_vpd"                                               │
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
│ • 🆕 SMART DEADBAND CHECK (NEW!):                                          │
│   deadband = 0.05 (default)                                                 │
│   deviation = |currentVPD - targetedVPD|                                    │
│                                                                             │
│   IF deviation <= deadband:                                                 │
│     → _handle_smart_deadband() called                                       │
│     → Climate devices reduced to minimum                                    │
│     → Air exchange devices (Exhaust, Intake, Window) reduced               │
│     → Ventilation continues running                                        │
│     → Hold time: 2.5 minutes                                                │
│     → LogForClient: "Smart Deadband active"                                 │
│     → RETURN (no VPD events!)                                               │
│   ELSE:                                                                     │
│     → _reset_deadband_state()                                               │
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

### Differences to VPD Perfection

| Aspect | VPD Perfection | Closed Environment |
|--------|---------------|-------------------|
| **Mode Handler** | `handle_vpd_perfection()` | `handle_closed_environment()` |
| **Manager** | OGBModeManager → ClosedEnvironmentManager | |
| **Action Handler** | ClosedActions.execute_closed_environment_cycle() | |
| **Night Hold** | ✅ Active | ❌ Bypassed |
| **VPD Deviation** | ✅ Active | ❌ Bypassed |
| **Weighted Deviations** | ✅ Central calculation | ✅ Central calculation (0,0,0,0) |
| **WeightPublication** | ✅ Emitted | ✅ Emitted |
| **Environment Guard** | ✅ Active | ✅ Active |
| **checkLimitsAndPublicate** | `checkLimitsAndPublicate()` | `checkLimitsAndPublicateNoVPD()` |

### Action Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: MODE MANAGER                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBModeManager.handle_closed_environment()                                │
│                                                                             │
│ 1.1 🆕 SMART DEADBAND CHECK (NEW! - VPD-based)                              │
│     currentVPD = data_store.getDeep("vpd.current")                          │
│     targetVPD = data_store.getDeep("vpd.targeted") or vpd.perfection       │
│                                                                             │
│     IF currentVPD is not None AND targetVPD is not None:                    │
│       deadband = 0.05 (default)                                             │
│       deviation = |currentVPD - targetVPD|                                  │
│                                                                             │
│       IF deviation <= deadband:                                             │
│         → _handle_smart_deadband() called                                   │
│         → Climate devices reduced to minimum                                │
│         → Air exchange devices (Exhaust, Intake, Window) reduced           │
│         → Ventilation continues running                                    │
│         → Hold time: 2.5 minutes                                            │
│         → LogForClient: "Smart Deadband active"                             │
│         → CO2 Control still executed (important!)                          │
│         → RETURN (no normal Closed Env Actions!)                            │
│       ELSE:                                                                 │
│         → _reset_deadband_state()                                           │
│                                                                             │
│ 1.2 Normal Closed Environment Cycle (only if outside deadband):            │
│     → ClosedEnvironmentManager.execute_cycle()                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: CLOSED ACTIONS                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager._handle_closed_environment_cycle()                        │
│                                                                             │
│ → ClosedActions.execute_closed_environment_cycle()                         │
│                                                                             │
│ Executes:                                                                   │
│ 1. monitor_o2_safety()                                                     │
│ 2. maintain_co2()                                                          │
│ 3. control_temperature_closed()                                           │
│ 4. control_humidity_closed()                                               │
│ 5. optimize_air_recirculation()                                            │
│                                                                             │
│ NOTE: All actions are collected and executed in ONE batch!                 │
│ (Unlike before where each method sent separate LogForClient events)        │
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
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: BATCH EXECUTION                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.execute_closed_environment_cycle()                           │
│                                                                             │
│ Collects all actions:                                                       │
│ • co2_actions from maintain_co2()                                          │
│ • o2_actions from monitor_o2_safety()                                      │
│ • temp_actions from control_temperature_closed()                           │
│ • hum_actions from control_humidity_closed()                               │
│ • air_actions from optimize_air_recirculation()                            │
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
│   "humStatus": "entfeuchten"                                                │
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
| **Night Hold** | ✅ | ✅ | ❌ |
| **Smart Deadband** | ✅ (climate reduced, air exchange reduced) | ✅ (climate reduced, air exchange reduced) | ✅ (VPD-based) |
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

#### Night Mode Behavior

The Smart Deadband only activates during night when `nightVPDHold` is enabled:

```python
is_night = not is_light_on
night_vpd_hold = controlOptions.nightVPDHold

if is_night and not night_vpd_hold:
    # Night mode without VPD hold - no deadband
    # Use power-saving mode instead
    return
```

**Why?**
- Night with VPD hold: Devices need to control VPD → Deadband active
- Night without VPD hold: Power-saving mode → No deadband needed

#### Maximum Deadband Time with Periodic Checks

```python
# Configuration
max_deadband_time = 600 seconds  # 10 minutes
deadband_check_interval = 30 seconds  # Check every 30 seconds
deadband_hold_duration = 300 seconds  # 5 minutes hold time
```

**Behavior:**
1. **Max Time (10 min)**: After 10 minutes in deadband, automatically exit
2. **Periodic Checks (30 sec)**: Every 30 seconds, check if VPD still stable
3. **Auto-Extension**: If VPD stable and trending towards target, extend for another 5 minutes
4. **Early Exit**: If VPD leaves deadband at any time, exit immediately

#### Implementation Details

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
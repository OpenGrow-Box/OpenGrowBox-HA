# OpenGrowBox - Action Cycle Übersicht

## Inhaltsverzeichnis
1. [VPD Perfection Mode](#vpd-perfection-mode)
2. [VPD Target Mode](#vpd-target-mode)
3. [Closed Environment Mode](#closed-environment-mode)
4. [Sicherheitsmechanismen](#sicherheitsmechanismen)
5. [Environment Guard Details](#environment-guard-details)

---

## VPD Perfection Mode

### Trigger
- **Event:** `VPDCreation` (ausgelöst bei neuen Sensordaten)
- **Quelle:** `OGBVPDManager` berechnet VPD aus Temperature + Humidity Sensoren

### Action Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: VPD GENERIERUNG                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBVPDManager.handle_new_vpd()                                             │
│                                                                             │
│ Berechnet: currentVPD = f(avgTemp, avgHum, leafTempOffset)                │
│ Speichert in: data_store["vpd.current"]                                    │
│                                                                             │
│ emit("selectActionMode", OGBModeRunPublication)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: MODE MANAGER                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBModeManager.selectActionMode()                                          │
│                                                                             │
│ if tentMode == "VPD Perfection":                                           │
│     handle_vpd_perfection()                                                │
│                                                                             │
│ Decision Logic:                                                             │
│ • currentVPD < perfectMinVPD  → emit("increase_vpd")                      │
│ • currentVPD > perfectMaxVPD  → emit("reduce_vpd")                          │
│ • in Range                 → emit("FineTune_vpd")                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 3: VPD ACTIONS                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBVPDActions.increase_vpd() / reduce_vpd() / fine_tune_vpd()               │
│                                                                             │
│ Erstellt Action Map basierend auf Capabilities:                            │
│ • canExhaust    → Increase/Reduce                                          │
│ • canIntake     → Reduce/Increase                                         │
│ • canVentilate  → Increase/Reduce                                          │
│ • canHumidify   → Reduce/Increase                                          │
│ • canDehumidify→ Increase/Reduce                                           │
│ • canHeat       → Increase/Reduce                                          │
│ • canCool       → Reduce/Increase                                          │
│ • canClimate    → Eval                                                     │
│ • canCO2        → Increase/Reduce (abhängig von Licht)                     │
│ • canLight      → Increase (wenn vpdLightControl=True)                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: TEMPERATURE SAFETY OVERRIDE ⚠️                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBVPDActions._apply_temperature_safety_overrides()                       │
│                                                                             │
│ Prüft:                                                                     │
│ 1. cold_guard = minTemp + coolerBuffer                                     │
│ 2. hot_guard = maxTemp - heaterBuffer                                      │
│ 3. humidity_critical = (humidity >= maxHumidity) OR (humidity <= minHumidity)│
│                                                                             │
│ BEI KALT (temp <= cold_guard):                                            │
│ • canHeat → Increase ✅                                                    │
│ • canCool → Reduce  ✅                                                    │
│ • canExhaust/Intake/Ventilate →                                            │
│     - WENN humidity_critical: Increase ✅ (Notfall!)                        │
│     - SONST: Reduce ❌ (Blockiert)                                         │
│                                                                             │
│ BEI HEISS (temp >= hot_guard):                                             │
│ • Alle Air-Exchange → Increase ✅                                          │
│ • canHeat → Reduce ✅                                                      │
│ • canCool → Increase ✅                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 5: NIGHT HOLD CHECK 🌙                                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager._check_vpd_night_hold()                                    │
│                                                                             │
│ if NOT islightON AND NOT nightVPDHold:                                     │
│     → _night_hold_fallback() → VPD Actions BLOCKIERT                      │
│ else:                                                                       │
│     → Weiter zu Step 6                                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 5: WEIGHTED DEVIATIONS CALCULATION (Central) 🎯                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.checkLimitsAndPublicate()                                 │
│                                                                             │
│ • Berechnet temp_weight, hum_weight (user- oder plant-stage-spezifisch)    │
│ • Berechnet temp_deviation = (temp - min/max) * temp_weight                │
│ • Berechnet hum_deviation = (hum - min/max) * hum_weight                   │
│ • Emit OGBWeightPublication (für alle 3 Modes!)                            │
│                                                                             │
│ Gewichtungs-Beispiel:                                                       │
│ • humidity=2, temp=0 → Humidity-Error hat 2x Priorität                    │
│ • humidity=0, temp=1 → Temperature-Error hat 1x Priorität                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 6: DAMPENING ACTIONS                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBDampeningActions.process_actions_basic()                                │
│                                                                             │
│ • Empfängt temp_deviation, hum_deviation (vom ActionManager)               │
│ • Wendet Buffer Zones an (verhindert Oszillation)                         │
│ • Löst Action-Konflikte (höchste Priorität pro Capability)                │
│ • Filtert durch Dampening/Cooldown (nutzt weighted deviations)            │
│                                                                             │
│ WICHTIG: Weighted Deviations werden NICHT neu berechnet!                  │
│ Sie werden zentral in ActionManager berechnet und hier verwendet.         │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 7: PUBLICATION ACTION HANDLER                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.publicationActionHandler()                                 │
│                                                                             │
│ 7.1 Tent Mode Check: if Disabled → STOP                                   │
│                                                                             │
│ 7.2 🛡️ ENVIRONMENT GUARD ANGEWENDET (STEP 8)                               │
│                                                                             │
│ 7.3 Speichert Actions für Analytics:                                       │
│     • previousActions (max 5)                                              │
│     • actionData (AI Training)                                             │
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
│ STEP 8: ENVIRONMENT GUARD 🛡️ (Detailliert)                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager._apply_environment_guard()                                │
│ OGBEnvironmentGuard.evaluate_environment_guard()                           │
│                                                                             │
│ NUR FÜR: canExhaust, canIntake, canVentilate MIT Increase                  │
│                                                                             │
│ 1. AIR SOURCE AUSWÄHLEN:                                                   │
│    • Intake → Outsite (Wetterdaten) wenn verfügbar                         │
│    • Exhaust → Ambient (Raumdaten)                                         │
│                                                                             │
│ 2. RISIKEN BEWERTEN:                                                       │
│    • temp_risk: Zu kalt drinnen + Quelle noch kälter                     │
│    • humidity_risk: Zu trocken + Quelle noch trockener                    │
│    • temp_benefit: Zu kalt + Quelle wärmer                                │
│    • humidity_benefit: Zu nass + Quelle trockener                         │
│    • humidity_critical: humidity >= maxHumidity (SCHIMMELGEFAHR!)          │
│    • humidity_critical_dry: humidity <= minHumidity (ZUTROCKEN!)            │
│                                                                             │
│ 3. PRIORITÄTSENTSCHEIDUNG:                                                 │
│    1️⃣ humidity_critical → ALLOW (Notfall override!)                       │
│    2️⃣ humidity_critical_dry → ALLOW (Notfall override!)                   │
│    3️⃣ humidity_benefit → ALLOW (Trocknen nötig)                           │
│    4️⃣ temp_benefit → ALLOW (Wärmen nötig)                                  │
│    5️⃣ temp_risk → BLOCK (Zu kalt!)                                        │
│    6️⃣ humidity_risk → BLOCK (Zu trocken!)                                  │
│    7️⃣ No risk → ALLOW                                                       │
│                                                                             │
│ 4. ERGEBNIS:                                                               │
│    BLOCKED → Action "Increase" → "Reduce" umgeschrieben                   │
│    ALLOWED → Action bleibt unverändert                                     │
│                                                                             │
│ 5. LOG FOR CLIENT:                                                          │
│    • Bei Block: WARNING mit详细 infos                                     │
│    • Bei Allow: DEBUG                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 9: DEVICE EXECUTION                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ Device-spezifische on() Handler empfangen Events:                         │
│                                                                             │
│ • Exhaust.on("Increase Exhaust") → increaseAction()                       │
│ • Intake.on("Increase Intake") → increaseAction()                         │
│ • Heater.on("Increase Heater") → increaseAction()                         │
│                                                                             │
│ WICHTIG: Device.should_block_air_exchange_increase() prüft auch            │
│ EnvironmentGuard (für direkte Increase-Actions)!                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## VPD Target Mode

### Unterschiede zu VPD Perfection

| Aspekt | VPD Perfection | VPD Target |
|--------|---------------|------------|
| **VPD Berechnung** | Vergleicht mit perfectMin/max | Vergleicht mit targetedMin/Max |
| **Event Namen** | `increase_vpd`, `reduce_vpd` | `vpdt_increase_vpd`, `vpdt_reduce_vpd` |
| **VPD Actions** | `increase_vpd()` | `increase_vpd_target()` |
| **Handler** | `_handle_increase_vpd()` | `_handle_vpdt_increase_vpd()` |
| **Weighted Deviations** | ✅ Zentral berechnet (Step 5) | ✅ Zentral berechnet (Step 5) |
| **Dampening** | `process_actions_basic()` | `process_actions_target_basic()` |
| **WeightPublication** | ✅ Emittiert | ✅ Emittiert |

### Action Flow (identisch zu VPD Perfection bis auf Step 3-5)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ UNTERSCHIEDE:                                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│ STEP 3: handle_targeted_vpd()                                              │
│ • Liest: vpd.targeted, vpd.targetedMin, vpd.targetedMax                 │
│ • Decision: < min → increase, > max → reduce, in range → finetune         │
│                                                                             │
│ STEP 5: WEIGHTED DEVIATIONS (identisch zu VPD Perfection!)                │
│ • checkLimitsAndPublicateTarget() berechnet weighted deviations            │
│ • Nutzt: process_actions_target_basic() mit weighted deviations            │
│ • Emit OGBWeightPublication                                                │
│                                                                             │
│ ALLE ANDEREN STEPS SIND IDENTISCH!                                         │
│ • Temperature Safety Override (Step 4)                                    │
│ • Night Hold Check (Step 6)                                               │
│ • Environment Guard (Step 8)                                               │
│ • Device Execution (Step 9)                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Closed Environment Mode

### Unterschiede zu VPD Perfection

| Aspekt | VPD Perfection | Closed Environment |
|--------|---------------|-------------------|
| **Mode Handler** | `handle_vpd_perfection()` | `handle_closed_environment()` |
| **Manager** | OGBModeManager → ClosedEnvironmentManager | |
| **Action Handler** | ClosedActions.execute_closed_environment_cycle() | |
| **Night Hold** | ✅ Aktiv | ❌ Bypassed |
| **VPD Deviation** | ✅ Aktiv | ❌ Bypassed |
| **Weighted Deviations** | ✅ Zentral berechnet | ✅ Zentral berechnet (0,0,0,0) |
| **WeightPublication** | ✅ Emittiert | ✅ Emittiert |
| **Environment Guard** | ✅ Aktiv | ✅ Aktiv |
| **checkLimitsAndPublicate** | `checkLimitsAndPublicate()` | `checkLimitsAndPublicateNoVPD()` |

### Action Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 1: MODE MANAGER                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBModeManager.handle_closed_environment()                                │
│                                                                             │
│ → ClosedEnvironmentManager.execute_cycle()                                 │
│ → emit("closed_environment_cycle", capabilities)                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 2: CLOSED ACTIONS                                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager._handle_closed_environment_cycle()                        │
│                                                                             │
│ → ClosedActions.execute_closed_environment_cycle()                         │
│                                                                             │
│ Führt aus:                                                                  │
│ 1. monitor_o2_safety()                                                     │
│ 2. maintain_co2()                                                          │
│ 3. control_temperature_closed()                                           │
│ 4. control_humidity_closed()                                               │
│ 5. optimize_air_recirculation()                                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 3: TEMPERATURE/HUMIDITY CONTROL                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ ClosedActions.control_temperature_closed()                                 │
│ ClosedActions.control_humidity_closed()                                    │
│                                                                             │
│ Nutzt: _increase_temperature() / _decrease_temperature()                 │
│       _increase_humidity() / _decrease_humidity()                         │
│                                                                             │
│ Jede Methode ruft:                                                          │
│ → action_manager.checkLimitsAndPublicateNoVPD(action_map)                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 4: WEIGHTED DEVIATIONS & NO NIGHT HOLD CHECK ⚠️                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.checkLimitsAndPublicateNoVPD()                            │
│                                                                             │
│ Weighted Deviations (neu!):                                                 │
│ • Berechnet weighted deviations (aber alle = 0,0,0,0 da keine VPD Limits)  │
│ • Emit OGBWeightPublication                                                │
│                                                                             │
│ Bypass Logik:                                                               │
│ "Closed Environment must bypass all VPD-specific processing.                │
│ We only keep lightweight per-capability conflict resolution"               │
│                                                                             │
│ • ❌ Kein Night Hold Check                                                 │
│ • ❌ Kein VPD Deviation Filter                                             │
│ • ✅ WeightPublication (mit 0-Deviations)                                  │
│ • ✅ Conflict Resolution                                                   │
│ ✅ ENVIRONMENT GUARD (Step 5)                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 5: ENVIRONMENT GUARD 🛡️                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│ OGBActionManager.publicationActionHandler()                                │
│                                                                             │
│ → _apply_environment_guard()                                               │
│                                                                             │
│ ✅ IDENTISCH ZU VPD PERFECTION!                                            │
│ • Blockiert bei temp_risk                                                 │
│ • Blockiert bei humidity_risk                                             │
│ • Erlaubt bei humidity_critical (≥maxHum)                                   │
│ • Erlaubt bei humidity_critical_dry (≤minHum)                              │
│ • Intelligente Quellen-Auswahl (Ambient/Outsite)                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ STEP 6: DEVICE EXECUTION                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ ✅ IDENTISCH ZU VPD PERFECTION                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Sicherheitsmechanismen

### Übersicht

| Sicherheit | VPD Perf | VPD Target | Closed Env |
|------------|-----------|------------|------------|
| **Night Hold** | ✅ | ✅ | ❌ |
| **Temperature Safety** | ✅ | ✅ | ❌ (in ClosedActions) |
| **Humidity Critical Override** | ✅ | ✅ | ✅ |
| **Environment Guard** | ✅ | ✅ | ✅ |
| **Buffer Zones** | ✅ | ✅ | ✅ |
| **Conflict Resolution** | ✅ | ✅ | ✅ |
| **Weighted Deviations** | ✅ | ✅ | ✅ (alle 0) |
| **Dampening/Cooldown** | ✅ | ✅ | ❌ |

### Priority Order (wenn mehrere Risiken aktiv)

```
1. humidity_critical (≥maxHum) → IMMER ERLAUBEN (Schimmelgefahr!)
2. humidity_critical_dry (≤minHum) → IMMER ERLAUBEN (zu trocken!)
3. humidity_benefit → ERLAUBEN (Trocknen nötig)
4. temp_benefit → ERLAUBEN (Wärmen nötig)
5. temp_risk → BLOCK (Zu kalt)
6. humidity_risk → BLOCK (Zu trocken)
7. No risk → ERLAUBEN
```

---

## Weighted Deviations (Zentrale Berechnung)

### Konzept

Weighted Deviations erlauben es, Prioritäten zwischen Temperatur und Luftfeuchtigkeit zu setzen. Wenn ein Benutzer z.B. `humidity=2` und `temp=0` einstellt, wird der Luftfeuchtigkeits-Error mit doppelter Priorität behandelt.

### Berechnung (in OGBActionManager._calculate_weighted_deviations())

```python
# Schritt 1: Gewichte bestimmen
if own_weights:
    temp_weight = controlOptionData.weights.temp    # z.B. 0.0, 1.0, 2.0, etc.
    hum_weight = controlOptionData.weights.hum      # z.B. 2.0, 1.0, 0.0, etc.
else:
    # Plant-stage-spezifische Gewichte
    plant_stage = "MidFlower"
    temp_weight, hum_weight = get_plant_stage_weights(plant_stage)

# Schritt 2: Temperature Deviation
if temp > maxTemp:
    temp_deviation = (temp - maxTemp) * temp_weight
elif temp < minTemp:
    temp_deviation = (temp - minTemp) * temp_weight
else:
    temp_deviation = 0

# Schritt 3: Humidity Deviation
if hum > maxHumidity:
    hum_deviation = (hum - maxHumidity) * hum_weight
elif hum < minHumidity:
    hum_deviation = (hum - minHumidity) * hum_weight
else:
    hum_deviation = 0

# Schritt 4: Emit WeightPublication
emit("LogForClient", OGBWeightPublication(
    tempDeviation=temp_deviation,
    humDeviation=hum_deviation,
    tempWeight=temp_weight,
    humWeight=hum_weight,
    message=f"Temp Too High: Deviation {temp_deviation}"
))
```

### Anwendung (in OGBDampeningActions)

```python
# VPD Perfection Mode
async def process_actions_basic(action_map, temp_deviation, hum_deviation):
    # temp_deviation und hum_deviation werden vom ActionManager übergeben
    # NICHT neu berechnet!

    # Filtert Actions basierend auf weighted deviations
    filtered_actions = action_manager._filterActionsByDampening(
        action_map, temp_deviation, hum_deviation
    )

# VPD Target Mode
async def process_actions_target_basic(action_map, temp_deviation, hum_deviation):
    # Identische Logik wie VPD Perfection
    filtered_actions = action_manager._filterActionsByDampening(
        action_map, temp_deviation, hum_deviation
    )

# Closed Environment Mode
async def checkLimitsAndPublicateNoVPD(action_map):
    # Berechnet weighted deviations (aber alle = 0)
    temp_deviation, hum_deviation, temp_weight, hum_weight, message = \
        _calculate_weighted_deviations(tent_data)

    # Emit WeightPublication (mit 0-Deviations)
    await emit("LogForClient", OGBWeightPublication(...))
```

### Beispiele

#### Beispiel 1: Luftfeuchtigkeit hat Priorität (humidity=2, temp=0)

```
Aktuelle Bedingungen: temp=25°C, hum=80%
Grenzen: minTemp=20°C, maxTemp=27°C, minHum=50%, maxHum=70%

Berechnung:
• temp_deviation = 0 (im Bereich)
• hum_deviation = (80% - 70%) * 2.0 = 20.0% (gewichtet!)

Ergebnis:
• Dehumidifier Actions werden mit hoher Priorität ausgeführt
• Temp-Actions haben niedrige Priorität
```

#### Beispiel 2: Temperatur hat Priorität (humidity=0, temp=2)

```
Aktuelle Bedingungen: temp=29°C, hum=60%
Grenzen: minTemp=20°C, maxTemp=27°C, minHum=50%, maxHum=70%

Berechnung:
• temp_deviation = (29°C - 27°C) * 2.0 = 4.0°C (gewichtet!)
• hum_deviation = 0 (im Bereich)

Ergebnis:
• Cooling Actions werden mit hoher Priorität ausgeführt
• Humidity-Actions haben niedrige Priorität
```

#### Beispiel 3: Ausgewogene Priorität (humidity=1, temp=1)

```
Aktuelle Bedingungen: temp=29°C, hum=80%
Grenzen: minTemp=20°C, maxTemp=27°C, minHum=50%, maxHum=70%

Berechnung:
• temp_deviation = (29°C - 27°C) * 1.0 = 2.0°C
• hum_deviation = (80% - 70%) * 1.0 = 10.0%

Ergebnis:
• Sowohl Cooling als auch Dehumidifying werden ausgeführt
• Priority basiert auf absoluter Deviation (10% > 2°C)
```

### Plant-Stage-spezifische Gewichte

| Plant Stage | Temp Weight | Hum Weight | Begründung |
|-------------|-------------|------------|------------|
| Germination | 1.3 | 0.9 | Temp-Stabilität wichtig für Wurzelbildung |
| EarlyVeg | 1.3 | 0.9 | Wie Germination |
| MidVeg | 1.1 | 1.1 | Ausgewogenes Wachstum |
| LateVeg | 1.1 | 1.1 | Wie MidVeg |
| EarlyFlower | 1.0 | 1.0 | Übergang zu Blüte |
| MidFlower | 1.0 | 1.25 | Luftfeuchtigkeit wichtig für Blütenentwicklung |
| LateFlower | 1.0 | 1.25 | Wie MidFlower |
| Clones | 1.0 | 1.0 | Ausgewogen |

### WeightPublication Event

```json
{
  "Name": "Room1",
  "message": "Humidity Too High: Deviation 20.0",
  "tempDeviation": 0.0,
  "humDeviation": 20.0,
  "tempWeight": 0.0,
  "humWeight": 2.0
}
```

---

## Environment Guard Details

### Konfiguration (controlOptions)

```python
{
    "environmentGuardEnabled": True,
    "environmentGuardAmbientDelta": 1.2,      # °C Differenz für Temp-Risiko
    "environmentGuardMinMargin": 0.8,         # °C Margin zu minTemp
    "environmentGuardHumidityDelta": 15.0,    # % Differenz für Hum-Risiko
    "environmentGuardHumidityMargin": 5.0,    # % Margin zu minHumidity
    "environmentGuardWindowMinutes": 30.0,    # Zeitfenster für Block-Zählung
    "environmentGuardLockMinutes": 60.0,      # Lock-Dauer nach 2 Blocks
    "environmentGuardUnlockMargin": 1.2,      # °C Margin zum Entsperren
}
```

### State (safety.environmentGuard)

```python
{
    "blockedCount": 0,         # Blocks im aktuellen Zeitfenster
    "windowStart": None,      # Startzeit des Zeitfensters
    "lockUntil": None,         # Zeit bis zu der gelockt ist
    "lastDecision": None,      # "blocked" oder "allowed"
    "lastReason": None,       # Grund für letzte Entscheidung
    "selectedSource": None,   # "ambient" oder "outsite"
    "selectedTemp": None,     # Temperatur der gewählten Quelle
    "selectedHum": None,      # Feuchtigkeit der gewählten Quelle
    "indoorTemp": None,       # Aktuelle Innentemperatur
    "indoorHum": None,        # Aktuelle Innenfeuchtigkeit
    "maxHumidity": None,      # maxHumidity aus Plant Stage
    "minHumidity": None,      # minHumidity aus Plant Stage
}
```

---

## Plant Stage min/max Werte (Standard)

| Stage | minTemp | maxTemp | minHumidity | maxHumidity |
|-------|---------|----------|-------------|-------------|
| Germination | 20°C | 24°C | 78% | 85% |
| Clones | 20°C | 24°C | 72% | 80% |
| EarlyVeg | 22°C | 26°C | 65% | 75% |
| MidVeg | 23°C | 27°C | 60% | 72% |
| LateVeg | 24°C | 27°C | 55% | 68% |
| EarlyFlower | 22°C | 26°C | 55% | 68% |
| MidFlower | 21°C | 25°C | 48% | 62% |
| LateFlower | 20°C | 24°C | 40% | 55% |

---

## Log For Client Events

### Environment Guard Blocked
```json
{
  "Name": "Room1",
  "Action": "EnvironmentGuard",
  "Device": "canExhaust",
  "From": "Increase",
  "To": "Reduce",
  "Reason": "temp_risk_cold_source",
  "Message": "EnvironmentGuard blocked canExhaust: temp_risk_cold_source (indoor=19.0°C/60%, source=ambient/10.0°C/40%)",
  "selectedSource": "ambient",
  "selectedTemp": 10.0,
  "selectedHum": 40.0,
  "indoorTemp": 19.0,
  "indoorHum": 60.0,
  "maxHumidity": 75.0,
  "minHumidity": 50.0,
  "priority": "medium"
}
```

### Environment Guard Allowed (Critical Humidity)
```json
{
  "Name": "Room1",
  "Action": "EnvironmentGuard",
  "Device": "canExhaust",
  "From": "Increase",
  "To": "Increase",
  "Reason": "humidity_emergency_over_max",
  "Message": "EnvironmentGuard allowed canExhaust: humidity_emergency_over_max (indoor=22.0°C/76%, source=ambient/18.0°C/50%)",
  "priority": "emergency"
}
```
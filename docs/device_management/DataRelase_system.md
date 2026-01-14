# DataRelease System - Grow Data Synchronization

## Overview

The DataRelease system is responsible for synchronizing grow room data with the OpenGrowBox Premium API. It collects comprehensive environmental and device state data after each control action cycle and sends it to the Premium API for AI processing, analytics, and compliance tracking.

## Modular Architecture

The system uses a modular architecture with specialized action modules:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Modular Data Flow                                      │
│                                                                                  │
│   Sensor Updates                                                                 │
│        ↓                                                                         │
│   OGB.py (OpenGrowBox main controller)                                           │
│        ↓                                                                         │
│   OGBMainController → VPD calculation                                            │
│        ↓                                                                         │
│   emit("selectActionMode", tentMode)                                             │
│        ↓                                                                         │
│   ┌─────────────────────────────────────────────────────────────────────────┐    │
│   │                      OGBModeManager                                     │    │
│   │                                                                         │    │
│   │   selectActionMode() → determines control mode:                         │    │
│   │     • VPD Perfection → emit("increase_vpd|reduce_vpd|FineTune_vpd")     │    │
│   │     • VPD Target → emit("increase_vpd|reduce_vpd|FineTune_vpd")         │    │
│   │     • AI Control → emit("DataRelease", True) + emit("AIActions", data)  │    │
│   │     • PID Control → emit("PIDActions", data)                            │    │
│   │     • MPC Control → emit("MPCActions", data)                            │    │
│   │     • Drying → drying mode handlers                                     │    │
│   └────────────────────────────────────┬────────────────────────────────────┘    │
│                                        ↓                                         │
│   ┌─────────────────────────────────────────────────────────────────────────┐    │
│   │                 actions/OGBActionManager (Base)                         │    │
│   │                                                                         │    │
│   │   Event Listeners:                                                      │    │
│   │     • "increase_vpd" → _handle_increase_vpd() → OGBVPDActions           │    │
│   │     • "reduce_vpd" → _handle_reduce_vpd() → OGBVPDActions               │    │
│   │     • "FineTune_vpd" → _handle_fine_tune_vpd() → OGBVPDActions          │    │
│   │     • "PIDActions" → _handle_pid_actions() → OGBPremiumActions          │    │
│   │     • "MPCActions" → _handle_mpc_actions() → OGBPremiumActions          │    │
│   │                                                                         │    │
│   │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │    │
│   │   │  OGBVPDActions  │  │OGBDampeningAct. │  │OGBPremiumActions│         │    │
│   │   │                 │  │                 │  │                 │         │    │
│   │   │ increase_vpd()  │  │ process_with_   │  │ PIDActions()    │         │    │
│   │   │ reduce_vpd()    │→ │ dampening()     │  │ MPCActions()    │         │    │
│   │   │ fine_tune()     │  │                 │  │ AIActions()     │         │    │
│   │   └────────┬────────┘  └────────┬────────┘  └─────────────────┘         │    │
│   │            │                    │                                       │    │
│   │            ↓                    ↓                                       │    │
│   │   checkLimitsAndPublicate() / checkLimitsAndPublicateWithDampening()    │    │
│   │            │                                                            │    │
│   │            ↓                                                            │    │
│   │   publicationActionHandler() → Device commands                          │    │
│   │            │                                                            │    │
│   │            ↓                                                            │    │
│   │   emit("DataRelease", True) ← PRIMARY TRIGGER                           │    │
│   └────────────────────────────────────┬────────────────────────────────────┘    │
│                                        ↓                                         │
│   ┌─────────────────────────────────────────────────────────────────────────┐    │
│   │                      OGBPremManager                                     │    │
│   │                                                                         │    │
│   │   Event Listener:                                                       │    │
│   │     event_manager.on("DataRelease", _send_growdata_to_prem_api)         │    │
│   │                                                                         │    │
│   │   _send_growdata_to_prem_api():                                         │    │
│   │     1. Check if user is logged in                                       │    │
│   │     2. Check if mainControl == "Premium"                                │    │
│   │     3. Collect grow data from data_store                                │    │
│   │     4. Optional: Send to AI learning webhook                            │    │
│   │     5. Send to Premium API via WebSocket                                │    │
│   └────────────────────────────────────┬────────────────────────────────────┘    │
│                                        ↓                                         │
│                          Premium API (grow-data event)                           │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## File Structure

```
OGBController/
├── OGB.py                          # Main controller (OpenGrowBox class)
├── OGBPremManager.py               # Premium API integration & DataRelease listener
├── OGBModeManager.py               # Mode selection & AI Control trigger
├── actions/
│   ├── OGBActionManager.py         # Base action manager with publicationActionHandler
│   ├── OGBVPDActions.py            # VPD increase/reduce/fine-tune logic
│   ├── OGBDampeningActions.py      # Dampening and conflict resolution
│   ├── OGBEmergencyActions.py      # Emergency action handling
│   └── OGBPremiumActions.py        # PID/MPC/AI action handling
└── managers/
    ├── OGBMainController.py        # Main controller orchestration
    └── OGBVPDManager.py            # VPD calculation manager
```

## Implementation Details

### 1. Event Registration (OGBPremManager.py)

```python
# Line 107 in _setup_event_listeners()
self.event_manager.on("DataRelease", self._send_growdata_to_prem_api)
```

The `OGBPremManager` registers a listener for the `"DataRelease"` event during initialization.

### 2. Primary Trigger - publicationActionHandler (actions/OGBActionManager.py)

```python
async def publicationActionHandler(self, actionMap: List):
    """
    Execute device actions and emit DataRelease event.
    
    This is the core action execution method that:
    1. Stores actions in history for analytics
    2. Emits device-specific events
    3. Triggers DataRelease for Premium API sync
    """
    _LOGGER.debug(f"{self.room}: Executing {len(actionMap)} validated actions")

    # Store previous actions for analytics
    previousActions = self.data_store.get("previousActions") or []
    current_time = time.time()

    previousActions.append({
        "capability": [getattr(a, 'capability', '') for a in actionMap],
        "action": [getattr(a, 'action', '') for a in actionMap],
        "message": [getattr(a, 'message', '') for a in actionMap],
        "time": current_time,
    })

    # Clean up old actions (older than 15 minutes)
    previousActions = [a for a in previousActions if current_time - a["time"] < 900]
    self.data_store.set("previousActions", previousActions)

    # Execute device-specific actions
    for action in actionMap:
        actionCap = getattr(action, 'capability', None)
        actionType = getattr(action, 'action', None)
        
        # Emit device-specific events (Exhaust, Intake, Ventilation, etc.)
        if actionCap == "canExhaust":
            await self.event_manager.emit(f"{actionType} Exhaust", actionType)
        # ... (all device types)

    # CRITICAL: Emit DataRelease event for Premium API synchronization
    await self.event_manager.emit("DataRelease", True)
```

### 3. Action Flow Delegation

The modular system uses delegation for action processing:

```python
# In OGBVPDActions.py
async def increase_vpd(self, capabilities):
    action_map = []
    # Build action map...
    await self.action_manager.checkLimitsAndPublicate(action_map)

# In actions/OGBActionManager.py
async def checkLimitsAndPublicate(self, actionMap: List):
    """Process actions with basic limit checking (no dampening)."""
    if self.dampening_actions:
        await self.dampening_actions.process_actions_basic(actionMap)
    else:
        await self.publicationActionHandler(actionMap)

async def checkLimitsAndPublicateWithDampening(self, actionMap: List):
    """Process actions with full dampening logic."""
    if self.dampening_actions:
        await self.dampening_actions.process_actions_with_dampening(actionMap)
    else:
        filtered_actions, _ = self._filterActionsByDampening(actionMap)
        if filtered_actions:
            await self.publicationActionHandler(filtered_actions)
```

### 4. Secondary Trigger - AI Control Mode (OGBModeManager.py)

```python
# Lines 202-220 in handle_premium_modes()
async def handle_premium_modes(self, data):
    if data == False:
        return
    
    controllerType = data.get("controllerType")
    
    if controllerType == "PID":
        await self.event_manager.emit("PIDActions", data)
    elif controllerType == "MPC":
        await self.event_manager.emit("MPCActions", data)
    elif controllerType == "AI":
        # AI Control triggers DataRelease BEFORE AI actions
        await self.event_manager.emit("DataRelease", True)
        await self.event_manager.emit("AIActions", data)
        
        # Start AI Data Bridge for cropsteering learning
        if not self._ai_bridge_started:
            await self.start_ai_data_bridge()
```

### 5. Data Collection & Transmission (OGBPremManager.py)

```python
# Lines 1064-1115 in _send_growdata_to_prem_api()
async def _send_growdata_to_prem_api(self, event):
    """Send comprehensive grow data to Premium API."""
    
    # Guard: Only send if logged in
    if not self.is_logged_in:
        return
    
    # Guard: Only send if Premium control is selected
    mainControl = self.data_store.get("mainControl")
    if mainControl != "Premium":
        return
    
    # Collect comprehensive grow data
    grow_data = {
        "tentMode": self.data_store.get("tentMode"),
        "strainName": self.data_store.get("strainName"),
        "plantStage": self.data_store.get("plantStage"),
        "vpd": self.data_store.get("vpd"),
        "tentData": self.data_store.get("tentData"),
        "previousActions": self.data_store.get("previousActions"),
        # ... (all data fields)
    }
    
    # Optional: Send to AI Learning Webhook
    if self.data_store.getDeep("controlOptions.aiLearning"):
        await self._send_ai_learn_data(grow_data)
    
    # Send to Premium API via WebSocket
    success = await self.ogb_ws.prem_event("grow-data", grow_data)
    return success
```

## Data Payload Structure

### Complete Grow Data Object

```json
{
  "tentMode": "VPD Perfection",
  "strainName": "Northern Lights",
  "plantStage": "MidFlower",
  "planttype": "Photoperiod",
  "cultivationArea": 1.2,
  
  "vpd": {
    "current": 1.15,
    "perfection": 1.20,
    "perfectMin": 1.10,
    "perfectMax": 1.30
  },
  
  "tentData": {
    "temperature": 25.5,
    "humidity": 55,
    "dewpoint": 15.2,
    "minTemp": 20,
    "maxTemp": 30,
    "minHumidity": 40,
    "maxHumidity": 70
  },
  
  "previousActions": [
    {
      "capability": ["canExhaust", "canHumidify"],
      "action": ["Increase", "Reduce"],
      "message": ["VPD-Increase Action", "VPD-Increase Action"],
      "time": 1703323456.789
    }
  ],
  
  "devCaps": {
    "canExhaust": {"state": true},
    "canIntake": {"state": true},
    "canHumidify": {"state": true}
  },
  
  "controlOptions": {
    "ownWeights": false,
    "vpdLightControl": true,
    "nightVPDHold": true,
    "vpdDeviceDampening": true,
    "aiLearning": true
  }
}
```

## Event Flow Summary

```
VPD Control Flow (Modular):
  Sensor Update → OGBMainController → VPD calculation
       ↓
  emit("selectActionMode", tentMode)
       ↓
  OGBModeManager.selectActionMode() → emit("increase_vpd" | "reduce_vpd")
       ↓
  actions/OGBActionManager._handle_increase_vpd() → OGBVPDActions.increase_vpd()
       ↓
  checkLimitsAndPublicate() → OGBDampeningActions.process_actions_basic()
       ↓
  publicationActionHandler() → Device events executed
       ↓
  emit("DataRelease", True) → OGBPremManager._send_growdata_to_prem_api()
       ↓
  ogb_ws.prem_event("grow-data", grow_data) → Premium API


Premium Control Flow (AI Mode):
  Sensor Update → OGBMainController → VPD calculation
       ↓
  emit("selectActionMode", tentMode)
       ↓
  OGBModeManager.selectActionMode() → handle_premium_modes()
       ↓
  emit("DataRelease", True)  ←  IMMEDIATE TRIGGER FOR AI
       ↓
  emit("AIActions", data)
       ↓
  OGBPremManager._send_growdata_to_prem_api()
       ↓
  Premium API returns AI-generated actions
       ↓
  OGBPremiumActions.AIActions() → execute AI-recommended commands
```

## File References (Modular)

| Component | File | Key Lines |
|-----------|------|-----------|
| Event Registration | `OGBPremManager.py` | 107 |
| Data Collection | `OGBPremManager.py` | 1064-1115 |
| Primary Trigger | `actions/OGBActionManager.py` | `publicationActionHandler()` |
| AI Mode Trigger | `OGBModeManager.py` | 202-220 |
| VPD Actions | `actions/OGBVPDActions.py` | `increase_vpd()`, `reduce_vpd()` |
| Dampening Logic | `actions/OGBDampeningActions.py` | `process_actions_with_dampening()` |
| GrowCompleted Event | `premium/OGBPremiumIntegration.py` | 280, 483-560 |
| Finish Grow Handler | `managers/medium/OGBMediumManager.py` | `finish_medium_grow()` |
| Finish Grow Service | `sensor.py` | `handle_finish_grow()` |

## Trigger Conditions

### When DataRelease is Emitted

1. **After VPD Actions Complete** (Primary Trigger)
   - Location: `actions/OGBActionManager.publicationActionHandler()`
   - Triggered after: `increase_vpd`, `reduce_vpd`, `FineTune_vpd` actions
   - Occurs: Every time device actions are executed

2. **During AI Control Mode** (Secondary Trigger)
   - Location: `OGBModeManager.handle_premium_modes()`
   - Triggered when: `controllerType == "AI"`
   - Occurs: Before AI actions are processed

### When GrowCompleted is Emitted

3. **After Grow Cycle Completion** (Harvest Trigger)
   - Location: `OGBMediumManager.finish_medium_grow()`
   - Triggered when: User clicks "Finish Grow Cycle" in GrowBook UI
   - Occurs: Once per harvest, per medium
   - Purpose: Archives harvest data for analytics, compliance, and AI learning

#### GrowCompleted Event Flow

```
User clicks "Finish Grow Cycle" in GrowBook
        ↓
opengrowbox.finish_grow service called
        ↓
OGBMediumManager._on_finish_grow()
        ↓
finish_medium_grow() → emit("GrowCompleted", harvest_data)
        ↓
OGBPremiumIntegration._on_grow_completed()
        ↓
ogb_ws.send_encrypted_message("grow-completed", payload)
        ↓
Premium API receives harvest data
```

#### GrowCompleted Payload

```json
{
  "event_type": "grow_completed",
  "room_id": "uuid",
  "room_name": "FlowerTent",
  "tenant_id": "uuid",
  "timestamp": "2024-12-24T12:00:00Z",
  
  "medium_index": 0,
  "medium_name": "coco_1",
  "medium_type": "COCO",
  
  "plant_name": "Northern Lights #1",
  "breeder_name": "Sensi Seeds",
  "plant_type": "photoperiodic",
  
  "grow_start_date": "2024-01-15",
  "bloom_switch_date": "2024-03-01",
  "harvest_date": "2024-05-01",
  
  "total_days": 106,
  "bloom_days": 61,
  "breeder_bloom_days": 60,
  
  "final_readings": {
    "temperature": 24.5,
    "humidity": 55,
    "vwc": 0.45,
    "ec": 1.8
  },
  
  "notes": "Optional harvest notes"
}
```

This data enables:
- **Harvest History** - Track all completed grows
- **Analytics** - Analyze performance across grows
- **Compliance** - Audit trail for regulations
- **AI Learning** - Improve predictions based on outcomes

### Guard Conditions

The `_send_growdata_to_prem_api()` method will NOT send data if:

1. `is_logged_in == False` - User not authenticated
2. `mainControl != "Premium"` - Premium mode not selected

## Troubleshooting

### Data Not Being Sent

1. **Check Action Module Initialization**:
   ```python
   # In OGBActionManager
   status = action_manager.get_action_status()
   # Verify all modules are initialized
   ```

2. **Check Authentication**:
   - `is_logged_in` must be `True`
   - Check via `OGBPremManager.health_check()`

3. **Check Main Control**:
   - `mainControl` must be `"Premium"`
   - Set via `select.ogb_maincontrol_{room}` entity

4. **Enable Debug Logging**:
   ```yaml
   logger:
     logs:
       custom_components.opengrowbox.OGBController.actions: debug
       custom_components.opengrowbox.OGBController.OGBPremManager: debug
   ```

5. **Look for these log messages**:
   - `"Executing X validated actions"` - Actions being processed
   - `"DataRelease emitted after action execution"` - Trigger fired
   - `"Grow data sent successfully"` - Data transmitted

---

*Document Version: 2.0 (Modular Architecture)*
*Last Updated: 2024-12-23*
*Applies to: OGB-HA-Backend Modular Architecture*

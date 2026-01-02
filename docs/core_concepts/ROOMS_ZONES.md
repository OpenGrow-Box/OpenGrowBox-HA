# Rooms, Zones, and Multi-Room Architecture

## Overview

OpenGrowBox supports multiple independent grow rooms, each with its own complete set of sensors, devices, and control logic. This architecture enables users to manage complex grow operations with multiple rooms while maintaining complete isolation between them.

## Room Architecture

### Room Concept

A "room" in OpenGrowBox represents a complete, independent grow environment with:

- **Dedicated Sensors**: Temperature, humidity, VPD, light sensors
- **Independent Devices**: Fans, heaters, humidifiers, lights, pumps
- **Isolated Control Logic**: Separate VPD calculations and action execution
- **Premium Features**: Individual premium subscriptions and feature access
- **Data Isolation**: Separate data stores and configuration

### Room Components

```
Room "GrowRoom1"
├── Sensors (temperature, humidity, VPD, light)
├── Devices (fans, heaters, humidifiers, lights)
├── Control Logic (VPD calculations, mode management)
├── Configuration (targets, tolerances, schedules)
├── Premium Features (analytics, AI control)
└── Data Store (state, history, settings)

Room "FlowerTent"
├── Sensors (temperature, humidity, VPD, light)
├── Devices (fans, heaters, humidifiers, lights)
├── Control Logic (VPD calculations, mode management)
├── Configuration (targets, tolerances, schedules)
├── Premium Features (analytics, AI control)
└── Data Store (state, history, settings)
```

## Room Management

### Room Creation

Rooms are created through the Home Assistant configuration flow:

1. **User Input**: Room name and basic configuration
2. **Entity Registration**: HA entities are prefixed with room name
3. **Data Isolation**: Separate data stores for each room
4. **Manager Initialization**: Independent manager instances per room

### Room Entity Naming Convention

All Home Assistant entities follow a consistent naming pattern:

```
sensor.ogb_{measurement}_{room}
switch.ogb_{device}_{room}
select.ogb_{setting}_{room}
climate.ogb_{room}
```

#### Examples:
- `sensor.ogb_temperature_growroom1`
- `switch.ogb_exhaust_fan_growroom1`
- `select.ogb_tentmode_growroom1`
- `climate.ogb_growroom1`

### Room Selection

Users can switch between rooms using the room selector entity:

- **Entity**: `select.ogb_rooms`
- **Function**: Changes active room context for UI and controls
- **Persistence**: Room selection is maintained across sessions
- **Premium Isolation**: Each room has separate premium authentication

## Zone Architecture

### Zone Concept

Within each room, OpenGrowBox supports multiple "zones" for different plant types or growth stages:

- **Zone Types**: Germination, EarlyVeg, MidVeg, LateVeg, EarlyFlower, MidFlower, LateFlower
- **Independent Control**: Each zone can have different environmental targets
- **Shared Infrastructure**: Zones share room-level devices and sensors
- **Coordinated Operation**: Room-level logic coordinates zone activities

### Zone Implementation

Zones are implemented through:

1. **Plant Stage Configuration**: Different targets per growth stage
2. **Device Assignment**: Devices can be assigned to specific zones
3. **VPD Targets**: Zone-specific VPD targets and tolerances
4. **Light Schedules**: Zone-specific lighting requirements

### Multi-Zone Operation

```
Room Level
├── Zone 1 (EarlyVeg)
│   ├── VPD Target: 0.60-1.20 kPa
│   ├── Temperature: 22-26°C
│   ├── Humidity: 65-75%
│   └── Light Schedule: 18/6
├── Zone 2 (MidFlower)
│   ├── VPD Target: 0.90-1.70 kPa
│   ├── Temperature: 21-25°C
│   ├── Humidity: 48-62%
│   └── Light Schedule: 12/12
└── Shared Devices
    ├── Exhaust Fans (room-level control)
    ├── HVAC System (room-level control)
    └── Sensors (room-level monitoring)
```

## Room Controller Architecture

### OGBRoomController

The room controller (`OGBRoomController`) manages the lifecycle of each room:

#### Responsibilities:
- **Initialization**: Set up all managers and components for the room
- **Startup Sequence**: Coordinate room startup and monitoring activation
- **Event Routing**: Route events to appropriate room-specific handlers
- **Manager Coordination**: Coordinate between different managers within the room
- **Status Reporting**: Provide comprehensive room status information

#### Key Methods:
- `initialize_room()`: Set up room components
- `start_room()`: Begin room operation and monitoring
- `stop_room()`: Clean shutdown and resource cleanup
- `coordinate_managers()`: Route actions between managers

### Room Settings Management

#### OGBRoomSettings

Manages all configuration settings for a room:

- **Control Options**: Main control mode (HA/Premium), notifications
- **VPD Settings**: Targets, tolerances, dampening
- **Plant Configuration**: Stages, types, dates
- **Light Settings**: Schedules, DLI control, voltage limits
- **Environmental Limits**: Min/max temperatures, humidities
- **Device Settings**: Calibration, capabilities

## Data Isolation and Security

### Data Separation

Each room maintains complete data isolation:

- **Separate DataStores**: Independent configuration and state storage
- **Isolated Event Managers**: Room-specific event routing
- **Premium Separation**: Individual authentication per room
- **Entity Namespacing**: HA entities prefixed by room name

### Security Boundaries

- **Room-Level Authentication**: Premium features require per-room login
- **Data Access Control**: Components can only access their room's data
- **Event Isolation**: Events are scoped to specific rooms
- **Device Control**: Devices are managed per-room basis

## Room Lifecycle Management

### Initialization Sequence

1. **HA Integration Setup**: Coordinator creates room instance
2. **Manager Initialization**: All managers initialized for the room
3. **Event Setup**: Room-specific event listeners registered
4. **Data Loading**: Configuration and calibration data loaded
5. **Device Discovery**: Available devices detected and configured
6. **Monitoring Start**: Background monitoring tasks activated

### Startup Flow

```
HA Config Entry
    ↓
Coordinator.startOGB()
    ↓
OGB.__init__(room)
    ↓
Room Controller.initialize_room()
    ↓
Managers Initialized
    ↓
Event Listeners Setup
    ↓
Room Controller.start_room()
    ↓
Monitoring Started
    ↓
Grow Plans Activated (Premium)
    ↓
Room Operational
```

### Shutdown Sequence

1. **Graceful Stop**: Room controller stops monitoring
2. **Resource Cleanup**: Background tasks cancelled
3. **Data Persistence**: Final state saved
4. **Event Cleanup**: Listeners removed
5. **Manager Shutdown**: All managers properly shut down

## Premium Room Features

### Individual Subscriptions

Each room can have its own premium subscription:

- **Separate Authentication**: Login required per room
- **Feature Access**: Room-specific feature enablement
- **Data Isolation**: Analytics and compliance per room
- **Grow Plans**: Room-specific automated growth schedules

### Room-Specific Premium Data

- **Analytics**: Room-specific environmental data and trends
- **Compliance**: Individual compliance tracking per room
- **AI Control**: Room-specific AI model training and optimization
- **Research**: Room-specific data contribution to research programs

## Room Monitoring and Health

### Health Checks

The system provides comprehensive room health monitoring:

```python
def get_room_status(self) -> dict:
    return {
        "room": self.ogb.room,
        "initialized": True/False,
        "managers": {
            "data_store": True/False,
            "event_manager": True/False,
            "device_manager": True/False,
            "action_manager": True/False,
            "premium_manager": True/False,
        },
        "monitoring": {
            "fallback_active": True/False,
            "cleanup_active": True/False,
        },
    }
```

### Status Indicators

- **Manager Status**: All required managers properly initialized
- **Monitoring Status**: Background tasks running correctly
- **Data Integrity**: Configuration and state data valid
- **Device Connectivity**: All configured devices responding
- **Premium Status**: Authentication and feature access working

## Troubleshooting Multi-Room Issues

### Common Problems

1. **Room Not Appearing**: Check HA configuration and entity registration
2. **Data Cross-Contamination**: Verify data store isolation
3. **Event Routing Issues**: Check event manager scoping
4. **Premium Login Problems**: Ensure room-specific authentication
5. **Device Conflicts**: Verify device assignment to correct rooms

### Diagnostic Tools

- **Room Status API**: `get_room_status()` method for detailed diagnostics
- **Event Debugging**: Enable room-specific event logging
- **Data Store Inspection**: Check data isolation between rooms
- **Entity Verification**: Confirm proper entity naming and registration

## Configuration Examples

### Single Room Setup
```yaml
# Home Assistant configuration.yaml
opengrowbox:
  - room_name: "GrowRoom"
    host: "192.168.1.100"
```

### Multi-Room Setup
```yaml
# Home Assistant configuration.yaml
opengrowbox:
  - room_name: "Vegetative"
    host: "192.168.1.100"
  - room_name: "Flowering"
    host: "192.168.1.101"
  - room_name: "Cloning"
    host: "192.168.1.102"
```

### Room-Specific Settings
```python
# Room configuration in DataStore
{
    "room": "GrowRoom1",
    "mainControl": "Premium",
    "tentMode": "VPD Perfection",
    "plantStage": "gen",  # germ, veg, or gen
    "vpd": {
        "target": 1.2,
        "tolerance": 0.1,
        "dampening": 0.05
    },
    "plantStages": {
        "Germination": {"vpdRange": [0.35, 0.70], ...},
        "EarlyVeg": {"vpdRange": [0.60, 1.20], ...},
        "MidFlower": {"vpdRange": [0.90, 1.70], ...},
        ...
    },
    "plantStage": "MidFlower",
    "plantDates": {
        "growstartdate": "2025-01-15",
        "bloomswitchdate": "2025-03-15"
    }
    }
}
```

---

**Last Updated**: December 24, 2025
**Architecture Version**: 2.0 (Modular)
**Status**: Production Ready
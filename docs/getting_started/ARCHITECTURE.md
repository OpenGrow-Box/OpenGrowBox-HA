# OpenGrowBox HA Integration - Architecture Overview

## System Overview

OpenGrowBox (OGB) is a comprehensive Home Assistant integration for automated indoor gardening. It provides intelligent climate control, sensor monitoring, device automation, and premium AI-driven features for optimal plant growth.

### Key Features

- **Multi-room Support**: Manage multiple independent grow rooms
- **Advanced Climate Control**: VPD-based (Vapor Pressure Deficit) environmental control
- **Modular Architecture**: Extensible system with pluggable managers
- **Premium Integration**: AI control, analytics, compliance, and research features
- **Real-time Monitoring**: Comprehensive sensor data and device state tracking
- **Event-driven Design**: Asynchronous communication patterns throughout

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           OpenGrowBox HA Integration                         │
│                                                                             │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │   Frontend      │    │   Home Assistant │    │   Premium API   │         │
│  │   (React/Vite)  │◄──►│   (Core)        │◄──►│   (ogb-grow-api)│         │
│  │                 │    │                 │    │                 │         │
│  │ - Dashboard     │    │ - Entity Registry│ WS │ - Auth/Sessions │         │
│  │ - Controls      │    │ - State Machine  │    │ - Feature Flags │         │
│  │ - Analytics     │    │ - Event Bus      │    │ - AI Control    │         │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘         │
│           │                │                        │                      │
│           │   WebSocket     │                        │                      │
│           │◄───────────────►│                        │                      │
│           │                │                        │                      │
│           │   REST API      │   WebSocket/REST       │                      │
│           └────────────────►├────────────────────────┘                      │
│                            │                                                 │
│                 ┌──────────▼──────────┐                                      │
│                 │  Custom Component   │                                      │
│                 │  (ogb-ha-backend)   │                                      │
│                 └──────────┬──────────┘                                      │
│                            │                                                 │
│                 ┌──────────▼──────────┐                                      │
│                 │   OGB Controller    │                                      │
│                 │   (Python)          │                                      │
│                 └──────────┬──────────┘                                      │
│                            │                                                 │
│              ┌─────────────┼─────────────┐                                  │
│              │             │             │                                  │
│       ┌──────▼─────┐ ┌─────▼─────┐ ┌─────▼─────┐                            │
│       │   Sensors  │ │  Devices  │ │ Premium   │                            │
│       │   & VPD    │ │  Control  │ │ Features  │                            │
│       └────────────┘ └───────────┘ └───────────┘                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Home Assistant Custom Component (`custom_components/opengrowbox/`)

The HA integration provides the bridge between OGB logic and HA infrastructure.

#### Key Files:
- `__init__.py` - Component setup and lifecycle management
- `coordinator.py` - DataUpdateCoordinator for sensor synchronization
- `sensor.py` - Sensor entities and data publishing
- `switch.py` - Device control switches
- `select.py` - Mode and configuration selectors
- `config_flow.py` - User configuration flow

#### Responsibilities:
- HA entity management and state synchronization
- Configuration flow and user setup
- Platform forwarding and service registration
- Frontend integration and panel registration

### 2. OGB Controller (`OGBController/`)

The core business logic that implements grow room automation.

#### Main Components:
- **`OGB.py`** - Main controller interface and manager orchestration
- **`OGBMainController.py`** - Primary control loop and sensor processing
- **`OGBModeManager.py`** - Control mode selection and action triggering
- **`OGBActionManager.py`** - Action execution and device control
- **`OGBVPDManager.py`** - VPD calculations and environmental control

#### Modular Managers:
- **`managers/`** - Core orchestration managers
- **`actions/`** - Action execution modules (VPD, PID, MPC, AI)
- **`devices/`** - Device-specific control logic
- **`sensors/`** - Sensor data processing and validation
- **`premium/`** - Premium feature integration

### 3. Premium API Integration (`premium/`)

Advanced features requiring subscription and external API connectivity.

#### Key Components:
- **`OGBPremiumIntegration.py`** - Main premium integration coordinator
- **`websocket/`** - WebSocket client for real-time communication
- **`features/`** - Feature flag management and access control
- **`analytics/`** - Data analytics and reporting
- **`growplans/`** - Automated grow plan management

#### External Dependencies:
- **ogb-grow-api** - Premium backend service
- **WebSocket Connection** - Real-time data synchronization
- **JWT Authentication** - Secure API access

## Data Flow Architecture

### Sensor Data Pipeline

```
Sensor Reading → Validation → Processing → VPD Calculation → Action Trigger → Device Control
     ↓             ↓           ↓             ↓              ↓             ↓
Raw Data    → Outlier Detection → Averaging → Environmental → Mode Logic → Command Execution
```

#### 1. Data Collection
- Sensors report data through HA entity updates
- RegistryListener captures HA bus events
- DataStore maintains current state and history

#### 2. Processing Pipeline
- SensorValidationManager: Data quality checks
- SensorCalibrationManager: Calibration adjustments
- SensorReadingManager: Averaging and filtering

#### 3. VPD Calculations
- OGBVPDManager: Core VPD algorithm implementation
- Environmental parameter calculations
- Target vs. actual comparisons

#### 4. Action Generation
- OGBModeManager: Mode-specific action selection
- Action weighting and conflict resolution
- Dampening logic for smooth transitions

### Control Loop Flow

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Sensor Update │───►│  VPD Processing │───►│ Action Selection│
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│Device Execution │◄───│  Conflict       │◄───│   Mode Logic    │
│                 │    │  Resolution     │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  State Update   │───►│   DataRelease   │───►│ Premium Sync    │
│                 │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Control Modes

### 1. VPD Perfection Mode
- Maintains optimal VPD for current plant stage (germ/veg/gen)
- Automatic device adjustments based on environmental targets
- Uses stage-specific VPD ranges with tolerance bands
- Fine-tuned control with dampening to prevent oscillations

### 2. VPD Target Mode
- User-defined VPD targets with custom ranges
- Manual override of automatic VPD perfection
- Direct control of environmental setpoints

### 3. AI Control Mode (Premium)
- Machine learning-driven environmental optimization
- Real-time adaptation based on plant response data
- Requires premium subscription and API connectivity
- Predictive control using historical performance data

### 4. PID Control Mode (Premium)
- Proportional-Integral-Derivative control algorithms
- Precise environmental parameter maintenance
- Advanced tuning with Kp, Ki, Kd parameters
- Requires premium subscription

### 5. MPC Control Mode (Premium)
- Model Predictive Control for multi-variable optimization
- Predictive adjustments based on forecasted conditions
- Advanced constraint handling and optimization
- Requires premium subscription

### 6. Drying Mode
- Specialized mode for harvest/drying phases
- Different environmental targets for post-harvest processing
- Reduced humidity and temperature control

### 7. Disabled Mode
- All automatic control disabled
- Manual operation only
- Safety mode for maintenance or troubleshooting

## Event-Driven Communication

### Internal Event System
- **OGBEventManager**: Centralized event routing
- **Event Types**: Sensor updates, mode changes, actions, data sync
- **Async Processing**: Non-blocking event handling throughout

### Home Assistant Integration
- **Entity State**: Real-time sensor and device state updates
- **Services**: User-triggered actions and configuration changes
- **Events**: Cross-component communication and notifications

### Premium API Communication
- **WebSocket**: Real-time data synchronization and control commands
- **REST API**: Feature access, analytics, and configuration
- **Event Streaming**: Bidirectional event flow for premium features

## Data Storage and State Management

### DataStore Architecture
- **Hierarchical Storage**: Room-based data isolation
- **Persistent State**: Configuration and calibration data
- **Runtime State**: Current sensor values and device states
- **Historical Data**: Action history and performance metrics

### State Synchronization
- **HA State Machine**: Entity state management
- **DataRelease System**: Premium API data synchronization
- **Configuration Persistence**: User settings and calibration data

## Security and Authentication

### Local Security
- **HA Authentication**: Integration with HA user management
- **Room Isolation**: Data separation between grow rooms
- **Input Validation**: Comprehensive parameter validation

### Premium Security
- **JWT Tokens**: Secure API authentication
- **Session Management**: Automatic token refresh and session handling
- **Feature Gates**: Subscription-based access control

## Performance and Scalability

### Asynchronous Design
- **Async/Await**: Non-blocking operations throughout
- **Task Management**: Background task tracking and cleanup
- **Resource Pooling**: Efficient connection and resource management

### Optimization Features
- **Data Dampening**: Smooth control transitions
- **Caching**: Sensor data and API response caching
- **Batch Processing**: Efficient bulk operations

## Development and Testing

### Modular Architecture Benefits
- **Testability**: Isolated components with clear interfaces
- **Maintainability**: Clear separation of concerns
- **Extensibility**: Pluggable managers and action modules

### Quality Assurance
- **Type Hints**: Comprehensive Python type annotations
- **Async Testing**: Specialized testing for async operations
- **Integration Tests**: End-to-end testing scenarios

## Deployment and Configuration

### Home Assistant Integration
- **HACS Compatible**: Easy installation through HACS
- **Config Flow**: User-friendly setup process
- **Auto-Discovery**: Automatic device and sensor detection

### Premium Feature Management
- **Graceful Degradation**: Core functionality works without premium features
- **Feature Flags**: Runtime feature enablement and disablement
- **Subscription Management**: Automatic feature access based on subscription tier

---

**Last Updated**: December 24, 2025
**Architecture Version**: 2.0 (Modular)
**Status**: Production Ready
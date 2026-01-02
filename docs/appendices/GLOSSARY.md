# Glossary of Terms

## Core Concepts

### VPD (Vapor Pressure Deficit)
The difference between the amount of moisture in the air and how much moisture the air can hold when saturated. Measured in kPa (kilopascals). Optimal VPD ranges vary by plant growth stage.

### VWC (Volumetric Water Content)
The percentage of water volume in the growing medium. Measured as a percentage (0-100%). Optimal ranges depend on medium type and plant stage.

### EC (Electrical Conductivity)
A measure of nutrient concentration in solution, measured in μS/cm (microsiemens per centimeter). Also known as TDS (Total Dissolved Solids).

### PPFD (Photosynthetic Photon Flux Density)
The amount of photosynthetic light reaching plant surfaces, measured in μmol/m²/s (micromoles per square meter per second).

### DLI (Daily Light Integral)
The total amount of photosynthetic light delivered over a 24-hour period, measured in mol/m²/day (moles per square meter per day).

## System Components

### ESPHome
An open-source framework for programming ESP32/ESP8266 devices with Home Assistant integration.

### HACS (Home Assistant Community Store)
A marketplace for Home Assistant integrations and frontend themes.

### MQTT
A lightweight messaging protocol for IoT devices, commonly used for device-to-device communication.

### WebSocket
A communication protocol providing full-duplex communication channels over a single TCP connection.

### GPIO (General Purpose Input/Output)
Physical pins on microcontrollers that can be configured as inputs or outputs for various functions.

### PWM (Pulse Width Modulation)
A technique for controlling power delivered to devices by rapidly switching them on and off.

## Growing Concepts

### Vegetative Stage
The growth phase where plants focus on developing leaves, stems, and roots. Requires higher VWC and specific light spectra.

### Generative Stage (Flowering)
The reproductive phase where plants produce flowers/fruits. Requires different environmental conditions including drier medium and adjusted light spectra.

### Photoperiod
The duration of light and darkness in a 24-hour cycle that affects plant growth and flowering.

### Medium
The material in which plants grow (rockwool, coco coir, soil, hydroponic, etc.).

### Reservoir
The container holding nutrient solution in hydroponic systems.

### Runoff
Excess water that drains from the growing medium after irrigation.

## Control Modes

### VPD Perfection Mode
An advanced control algorithm that maintains optimal VPD by coordinating temperature, humidity, and ventilation systems.

### VPD Target Mode
A simplified VPD control that maintains a specific target VPD value.

### Target Mode
Basic setpoint control for individual environmental parameters.

### PID Control
Proportional-Integral-Derivative control algorithm for precise environmental regulation.

### MPC (Model Predictive Control)
Advanced control algorithm that predicts future system behavior and optimizes control actions.

## Device Types

### Actuator
A device that performs physical actions (pumps, fans, valves, heaters, lights).

### Sensor
A device that measures environmental parameters (temperature, humidity, VWC, pH, EC).

### Controller
A device or software system that makes decisions and coordinates other devices.

### Dripper
A small irrigation device that delivers water/nutrients to individual plants or zones.

### Relay Module
An electronic switch that allows low-power devices to control high-power devices.

## Technical Terms

### API (Application Programming Interface)
A set of rules and protocols for accessing a software application or web service.

### JSON (JavaScript Object Notation)
A lightweight data interchange format that's easy for humans to read and write.

### REST (Representational State Transfer)
An architectural style for designing networked applications using HTTP methods.

### Async/Await
Programming patterns for handling asynchronous operations in Python.

### Event-Driven Architecture
A software architecture where system components communicate through events rather than direct method calls.

### State Machine
A mathematical model of computation that can be in exactly one of a finite number of states at any given time.

## Calibration Terms

### Dry Calibration
The process of calibrating sensors to read 0% when placed in completely dry medium.

### Wet Calibration
The process of calibrating sensors to read 100% when saturated in water.

### Offset
A fixed adjustment added to or subtracted from sensor readings.

### Multiplier
A scaling factor applied to sensor readings for calibration.

### Hysteresis
The dependence of sensor output on the history of input values, often requiring different calibration curves for rising vs. falling values.

## Medium-Specific Terms

### Rockwool
A sterile growing medium made from basalt rock and chalk, providing excellent water retention and aeration.

### Coco Coir
A growing medium made from coconut husks, providing good water retention with natural pH buffering.

### Perlite
A volcanic glass expanded by heat, used to improve drainage and aeration in soil mixes.

### Vermiculite
A mica mineral expanded by heat, used for water retention and nutrient exchange.

### Hydroponic
A method of growing plants without soil, using mineral nutrient solutions in water.

### Aeroponic
A hydroponic system where roots are suspended in air and misted with nutrient solution.

## Safety Terms

### GFCI (Ground Fault Circuit Interrupter)
A safety device that shuts off electric power when it detects an imbalance between incoming and outgoing current.

### TVS (Transient Voltage Suppressor) Diode
A protection device that clamps voltage spikes to safe levels.

### Opto-isolation
Electrical isolation using light signals, preventing electrical noise and ground loops.

### Emergency Stop
A safety mechanism that immediately shuts down all automated systems in case of problems.

## Analytics Terms

### Baseline
A reference point or normal operating range for system parameters.

### Anomaly
A deviation from normal or expected behavior that may indicate a problem.

### Trend Analysis
The practice of collecting data over time to identify patterns or changes.

### Correlation
A statistical relationship between two or more variables.

### Root Cause Analysis
A method of problem-solving used to identify the underlying causes of faults or problems.

## Premium Features

### AI Learning
Machine learning algorithms that analyze system data to optimize performance.

### Compliance Reporting
Automated generation of reports for regulatory compliance.

### Research Mode
Advanced data collection and analysis for research purposes.

### Multi-Tenant
Support for multiple users or installations within a single system.

### Session Management
Management of user sessions, authentication, and authorization.

---

*This glossary covers the most common terms used in OpenGrowBox documentation. For specific technical details, refer to the relevant guide sections.*</content>
<parameter name="filePath">docs/appendices/GLOSSARY.md
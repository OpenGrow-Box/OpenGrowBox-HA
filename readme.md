# 🌱 OpenGrowBox - Advanced Home Assistant Grow Control

[![GitHub stars](https://img.shields.io/github/stars/OpenGrow-Box/OpenGrowBox-HA?style=flat-square)](https://github.com/OpenGrow-Box/OpenGrowBox-HA/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/OpenGrow-Box/OpenGrowBox-HA?style=flat-square)](https://github.com/OpenGrow-Box/OpenGrowBox-HA/issues)
[![License](https://img.shields.io/badge/license-OGBCL-blue?style=flat-square)](LICENSE)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.x+-green?style=flat-square)](https://www.home-assistant.io)

**Professional-grade grow room automation for Home Assistant.** Control climate, lighting, irrigation, and hydroponics with VPD-based intelligence, AI optimization, and comprehensive analytics.

📖 **[Full Documentation](docs/)** | ⚙️ **[Quick Start Guide](docs/getting_started/INSTALLATION.md)** | 🐛 **[Troubleshooting](docs/system_management/TROUBLESHOOTING.md)**

## 🏗️ Architecture Overview

OpenGrowBox features a **modular, production-ready architecture** with clean separation of concerns:

- **🌡️ Core System**: VPD controllers, climate management, and system orchestration
- **🔌 Device Layer**: Universal device support via Home Assistant integration
- **🤖 Premium Features**: AI optimization, advanced analytics, and research tools
- **💧 Hydroponics**: Complete nutrient delivery and irrigation automation
- **🧪 Quality Assurance**: Comprehensive testing with 100% smoke test success rate

---

## 📋 Table of Contents

- [🚀 Quick Start](#-quick-start)
- [✨ Key Features](#-key-features)
- [🔧 Installation](#-installation)
- [⚙️ Configuration](#️-configuration)
- [📊 Supported Hardware](#-supported-hardware)
- [🔍 Recent Updates](#-recent-updates)
- [📚 Documentation](#-documentation)
- [🐛 Troubleshooting](#-troubleshooting)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)

---

## 🚀 Quick Start

### Prerequisites
- ✅ **Home Assistant** 2024.x+ running
- ✅ **Python** 3.9+ (included with HA)
- ✅ **MQTT Broker** (optional, for MQTT devices)

### 5-Minute Setup

1. **Install via HACS** (recommended):
   ```bash
   # In HA: Settings → Devices & Services → Add Integration
   # Search for "OpenGrowBox" and install
   ```

2. **Basic Configuration**:
   ```yaml
   # configuration.yaml
   opengrowbox:
     mainControl: "HomeAssistant"
   ```

3. **Add Your First Room**:
   - Go to OGB Integration in HA
   - Create a "Grow Room" area
   - Add sensors and devices
   - Set targets: 25°C, 60% RH, VPD 1.2

4. **Done!** Your grow room is now automated.

**[Detailed Setup →](docs/getting_started/INSTALLATION.md)**

---

## ✨ Key Features

### 🌡️ Climate Intelligence
- **VPD Automation**: Maintain optimal Vapor Pressure Deficit automatically
- **PID Controllers**: Precision temperature, humidity, and CO₂ control
- **Multi-Zone Support**: Different settings per room/area
- **Weather Integration**: External weather compensation

### 🔌 Universal Device Support
- **Any HA Device**: Lights, sensors, switches, climate devices
- **FridgeGrow Integration**: Native Plantalytix controller support
- **Modbus Devices**: Industrial sensors and controllers
- **ESPHome Devices**: Custom firmware for unlimited possibilities

### 🤖 AI & Analytics (Premium)
- **AI Optimization**: Machine learning for optimal grow conditions
- **Yield Prediction**: Harvest weight forecasting
- **Anomaly Detection**: Early problem identification
- **Research Tools**: A/B testing and experimental protocols

### 💧 Complete Hydroponics
- **Nutrient Automation**: pH/EC monitoring and adjustment
- **Irrigation Control**: Timed and sensor-based watering
- **Crop Steering**: Dynamic nutrient profiles by growth stage
- **Reservoir Management**: Level monitoring and alerts

### 📊 Professional Monitoring
- **Real-time Dashboards**: Comprehensive grow metrics
- **Historical Analytics**: Long-term trend analysis
- **Compliance Reporting**: Regulatory documentation
- **Alert System**: Customizable notifications

**[Full Feature List →](docs/premium_features/PREMIUM_FEATURES_OVERVIEW.md)**

---

## 📊 Supported Hardware

### 🌡️ Sensors
- **Temperature/Humidity**: DHT11/22, SHT30, BME280, industrial sensors
- **CO₂ Sensors**: MH-Z19, SenseAir S8, industrial CO₂ monitors
- **Soil Sensors**: Capacitive moisture, pH/EC probes, temperature
- **Light Sensors**: PAR meters, lux sensors, spectrum analyzers

### 🔌 Actuators & Controllers
- **Climate Control**: Heaters, coolers, humidifiers, dehumidifiers
- **Lighting**: LED grow lights, dimmable ballasts, spectrum controllers
- **Ventilation**: Exhaust/intake fans, speed controllers, dampers
- **Irrigation**: Pumps, solenoids, peristaltic dosing pumps
- **FridgeGrow**: Plantalytix FridgeGrow 2.0, AIR, LIGHT, Smart Socket

### 🔧 Integration Methods
- **ESPHome**: Custom firmware for ESP32/RPi
- **Zigbee/Z-Wave**: Wireless mesh networks
- **WiFi**: Shelly, Sonoff, Tuya devices
- **MQTT**: FridgeGrow, custom controllers
- **Modbus**: Industrial equipment
- **Ethernet**: BACnet, custom protocols

**[Hardware Compatibility →](docs/device_management/supported_devices_hardware.md)**

---

## 🔧 Installation

### Method 1: HACS (Recommended)

1. **Install HACS** in Home Assistant
2. **Add Repository**: Search for "OpenGrowBox"
3. **Install** latest version
4. **Restart** Home Assistant

### Method 2: Manual Installation

```bash
# Download and install
cd /config
git clone https://github.com/OpenGrow-Box/OpenGrowBox-HA.git
cp -r OpenGrowBox-HA/custom_components/opengrowbox /config/custom_components/

# Restart HA
ha core restart
```

### Method 3: Docker

```yaml
# docker-compose.yml
version: '3'
services:
  homeassistant:
    image: homeassistant/home-assistant:latest
    volumes:
      - ./config:/config
      - ./custom_components/opengrowbox:/config/custom_components/opengrowbox
```

**[Detailed Installation →](docs/getting_started/INSTALLATION.md)**

---

## ⚙️ Configuration

### Basic Setup

```yaml
# configuration.yaml
opengrowbox:
  mainControl: "HomeAssistant"  # Required
  updateInterval: 30           # Update frequency (seconds)
```

### Room Configuration

```yaml
# Define grow rooms
opengrowbox:
  rooms:
    main_tent:
      area_id: "grow_room"
      mode: "vegetative"
      vpd_target: 1.2
      temperature_target: 25.0
      humidity_target: 60.0
      co2_target: 800
```

### Device Setup

```yaml
# Device mapping with labels
opengrowbox:
  devices:
    temperature_sensor:
      entity_id: "sensor.grow_room_temperature"
      capabilities: ["canMeasureTemp"]

    heater:
      entity_id: "switch.heater"
      capabilities: ["canHeat"]

    # FridgeGrow device (automatic recognition)
    fridgegrow_heater:
      entity_id: "number.fridgegrow_abc123_heater"
      labels: ["fridgegrow", "heater"]
      # OGB detects this automatically!
```

**[Advanced Configuration →](docs/configuration/CONFIGURATION.md)**

---

## 🔍 Recent Updates

### ✅ **FridgeGrow Integration** (Latest)
- **Native Support**: Plantalytix FridgeGrow 2.0 controllers
- **Auto-Discovery**: Label-based device recognition
- **MQTT Control**: Direct device communication
- **Range Scaling**: Automatic 0-1 ↔ 0-100% conversion

### ✅ **Modular Architecture** (v1.4.x)
- **32 Managers**: Clean separation of concerns
- **100% Compatibility**: All original features preserved
- **Production Ready**: Comprehensive testing and error handling
- **Premium Features**: AI, analytics, and research tools

---

## 📚 Documentation

### 📖 Core Documentation
- **[Quick Start Guide](docs/getting_started/INSTALLATION.md)** - 5-minute setup
- **[User Manual](docs/core_concepts/ROOMS_ZONES.md)** - Complete usage guide
- **[Configuration Guide](docs/configuration/CONFIGURATION.md)** - Advanced setup
- **[API Reference](docs/technical_reference/API_REFERENCE.md)** - Developer docs

### 🔧 Device Management
- **[Supported Hardware](docs/device_management/supported_devices_hardware.md)** - Compatible devices
- **[FridgeGrow Integration](docs/device_management/FRIDGEGROW_INTEGRATION.md)** - Plantalytix support
- **[Modbus Integration](docs/device_management/MODBUS_INTEGRATION.md)** - Industrial devices

### ⚡ Specialized Systems
- **[VPD Control](docs/core_concepts/action_cycles/VPD_MODES_COMPLETE_IMPLEMENTATION.md)** - Climate automation
- **[Hydroponics](docs/specialized_systems/HYDRO_FEEDING_SYSTEM.md)** - Nutrient management
- **[Crop Steering](docs/specialized_systems/CROP_STEERING.md)** - Growth optimization

### 🧪 Premium Features
- **[AI & Analytics](docs/premium_features/PREMIUM_FEATURES_OVERVIEW.md)** - Advanced capabilities
- **[Research Tools](docs/premium_features/DATARELEASE_SYSTEM.md)** - Scientific features

### 🛠️ System Management
- **[Deployment Guide](docs/system_management/DEPLOYMENT.md)** - Production setup
- **[Troubleshooting](docs/system_management/TROUBLESHOOTING.md)** - Problem solving
- **[Performance](docs/development/PERFORMANCE.md)** - Optimization

### 🧑‍💻 Development
- **[Architecture](docs/getting_started/ARCHITECTURE.md)** - System design
- **[Testing](docs/development/TESTING.md)** - Quality assurance
- **[Debugging](docs/development/DEBUGGING.md)** - Development tools

---

## 🐛 Troubleshooting

### Common Issues

#### "Integration not loading"
```bash
# Check HA logs
grep -i opengrowbox /config/home-assistant.log
```
**Solutions:**
- Restart HA completely
- Check file permissions
- Verify Python dependencies

#### "No devices found"
**Causes:**
- Devices not in HA areas
- Incorrect entity labels
- Missing device capabilities

**Fix:**
- Assign devices to HA areas
- Add proper labels (e.g., `heater`, `sensor`)
- Check device configuration

#### "VPD calculations failing"
**Symptoms:**
- "NO Sensors Found to calc VPD"
- Incorrect VPD values

**Fix:**
- Ensure temperature + humidity sensors are configured
- Check sensor calibration
- Verify sensor placement

**[Full Troubleshooting →](docs/system_management/TROUBLESHOOTING.md)**

---

## 🤝 Contributing

We welcome contributions! Here's how to get started:

### Development Setup
```bash
# Clone repository
git clone https://github.com/OpenGrow-Box/OpenGrowBox-HA.git
cd OpenGrowBox-HA

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
python3 smoke_test_modular.py
```

### Code Standards
- **Linting**: `flake8 custom_components/opengrowbox/`
- **Type Checking**: `mypy custom_components/opengrowbox/`
- **Testing**: 100% smoke test pass rate required
- **Documentation**: Update docs for new features

### Pull Request Process
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests and documentation
5. Submit PR with description

**[Contributing Guidelines →](docs/development/CONTRIBUTING.md)**

---

## 📄 License

This project is licensed under the **OGBCL (OpenGrowBox Community License)**.

**Core functionality is free and open source.** Premium features (AI, advanced analytics, research tools) require a commercial subscription and are subject to separate licensing terms.

- ✅ **Free**: Climate control, device management, hydroponics
- 🔒 **Premium**: AI optimization, compliance reporting, multi-site management

---

## 🌟 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=OpenGrow-Box/OpenGrowBox-HA&type=Date)](https://www.star-history.com/#OpenGrow-Box/OpenGrowBox-HA&Date)

---

## 📞 Support & Community

- **📧 Email**: support@opengrowbox.com
- **💬 Discord**: [Join our community](https://discord.gg/opengrowbox)
- **🐛 Issues**: [GitHub Issues](https://github.com/OpenGrow-Box/OpenGrowBox-HA/issues)
- **📖 Wiki**: [Community Wiki](https://github.com/OpenGrow-Box/OpenGrowBox/wiki/)
- **📧 Newsletter**: [Stay updated](https://opengrowbox.com/newsletter)

---

*Built with ❤️ for the growing community. Happy growing! 🌱*

---


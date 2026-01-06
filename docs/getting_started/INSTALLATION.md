# OpenGrowBox Installation Guide

## Overview

This guide provides step-by-step instructions for installing and setting up OpenGrowBox HA Integration on your Home Assistant system.

## Prerequisites

### System Requirements

- **Home Assistant**: Version 2024.1.0 or later
- **Python**: 3.9 or higher (included with HA)
- **Disk Space**: Minimum 500MB free space
- **Network**: Stable internet connection for updates and premium features

### Hardware Requirements

- **Supported Controllers**: OpenGrowBox hardware or compatible systems
- **Network Connectivity**: Ethernet or WiFi connection to HA network
- **Power Supply**: Stable power source for controller hardware

## Installation Methods

### Method 1: HACS Installation (Recommended)

#### Step 1: Install HACS
If you don't have HACS installed:

1. Open your Home Assistant web interface
2. Go to **Settings** → **Add-ons, Backups & Supervisor** → **Add-on Store**
3. Search for and install **"HACS"** (Home Assistant Community Store)
4. Restart Home Assistant after installation

#### Step 2: Install OpenGrowBox Integration
1. Open HACS from the sidebar
2. Click on **"Integrations"** tab
3. Search for **"OpenGrowBox"**
4. Click **"Download"** on the OpenGrowBox integration
5. Restart Home Assistant

#### Step 3: Add Integration to Home Assistant
1. Go to **Settings** → **Devices & Services**
2. Click **"Add Integration"**
3. Search for **"OpenGrowBox"**
4. Follow the configuration wizard

### Method 2: Manual Installation

#### Step 1: Download Integration Files
```bash
# Create custom_components directory if it doesn't exist
mkdir -p /config/custom_components

# Clone or download OpenGrowBox integration
cd /config/custom_components
git clone https://github.com/your-repo/opengrowbox-ha.git opengrowbox
# OR download and extract ZIP file to opengrowbox directory
```

#### Step 2: Restart Home Assistant
```bash
# From Home Assistant UI: Settings → System → Restart
# OR from terminal: ha core restart
```

#### Step 3: Add Integration
Follow Step 3 from HACS installation method above.

## Initial Configuration

### Basic Setup Wizard

1. **Device Discovery**
   - The integration will automatically scan your network for OpenGrowBox devices
   - Select your controller from the discovered devices
   - If not found automatically, enter the IP address manually

2. **Authentication**
   - Enter your OpenGrowBox device credentials
   - For premium features, provide your account credentials
   - Choose subscription tier if applicable

3. **Plant Configuration**
   - Select plant type (Cannabis, Tomatoes, Peppers, etc.)
   - Set current growth stage (Germination, Vegetative, Flowering)
   - Configure environmental targets

4. **System Preferences**
   - Choose control mode (VPD Perfection, VPD Target, etc.)
   - Set notification preferences
   - Configure safety limits

### Advanced Configuration

#### Network Settings
```yaml
# configuration.yaml
opengrowbox:
  - host: "192.168.1.100"  # Controller IP address
    port: 80               # Usually 80
    ssl: false            # Usually false for local devices
    timeout: 30           # Connection timeout in seconds
```

#### Environmental Targets
```yaml
# Advanced environmental configuration
opengrowbox:
  - name: "Main Grow Tent"
    plant_config:
      type: "Cannabis"
      stage: "MidFlower"
      targets:
        temperature: [22, 26]    # Min, Max in °C
        humidity: [55, 68]       # Min, Max in %
        vpd: [0.90, 1.70]        # Target VPD range
        co2: [800, 1200]         # CO2 ppm range
```

## Device Setup

### Controller Configuration

1. **Power On**: Ensure your OpenGrowBox controller is powered on and connected to network
2. **Network Access**: Verify the controller is accessible from your HA system
3. **Firmware Update**: Check for and install any available firmware updates
4. **Sensor Calibration**: Follow the calibration procedures for accurate readings

### Sensor Configuration

#### VWC Sensors (Moisture)
1. Insert sensors into growing medium at proper depth (usually 1/3 from bottom)
2. Ensure good contact with medium (avoid air gaps)
3. Calibrate sensors using the calibration wizard
4. Test readings by watering and observing response

#### Environmental Sensors
1. Place temperature/humidity sensors at plant canopy level
2. Ensure good airflow around sensors
3. Avoid placing near heat sources or direct light
4. Verify readings match expected environmental conditions

### Device Integration

#### Lighting Systems
1. Configure light schedule based on plant stage
2. Set up sunrise/sunset transitions if supported
3. Configure spectrum control for different growth phases
4. Set DLI targets for optimal photosynthesis

#### Irrigation Systems
1. Configure irrigation mode (VWC-based, timed, or manual)
2. Set moisture thresholds for automatic watering
3. Configure pump flow rates and durations
4. Test irrigation system for proper operation

## Premium Features Setup

### Account Linking

1. **Create Account**: Visit the OpenGrowBox premium portal
2. **Link Device**: Enter your device serial number
3. **Subscribe**: Choose appropriate subscription plan
4. **Activate**: Enter credentials in HA integration settings

### Feature Activation

#### AI Control
1. Enable AI control in device settings
2. Allow initial learning period (24-48 hours)
3. Monitor AI recommendations and adjust as needed
4. Review AI performance analytics

#### Analytics & Compliance
1. Enable data collection in privacy settings
2. Configure compliance requirements for your jurisdiction
3. Set up automated reporting schedules
4. Review analytics dashboards

## Troubleshooting Installation

### Common Issues

#### Integration Not Found
**Symptom**: OpenGrowBox doesn't appear in integrations list
**Solution**:
- Restart Home Assistant
- Clear browser cache
- Check HACS installation
- Manually install if HACS fails

#### Device Not Discovered
**Symptom**: Controller not found during setup
**Solution**:
- Verify controller is powered on and connected
- Check network connectivity
- Enter IP address manually
- Verify firewall settings

#### Authentication Failed
**Symptom**: Unable to connect to controller
**Solution**:
- Verify username/password
- Check controller firmware version
- Reset controller to factory settings if needed
- Contact support for credential recovery

#### Sensor Readings Incorrect
**Symptom**: Sensors showing unrealistic values
**Solution**:
- Recalibrate sensors using calibration wizard
- Check sensor placement and connections
- Verify sensor compatibility
- Replace faulty sensors

### Logs and Diagnostics

#### Enable Debug Logging
```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.opengrowbox: debug
    custom_components.opengrowbox.OGBController: debug
```

#### Access Logs
- **HA UI**: Settings → System → Logs
- **Terminal**: `docker logs homeassistant` (if using Docker)
- **File**: `/config/home-assistant.log`

### Support Resources

- **Documentation**: See full documentation in `/docs` directory
- **Community Forum**: OpenGrowBox community discussions
- **GitHub Issues**: Report bugs and request features
- **Premium Support**: Contact support for subscription issues

## Post-Installation Checklist

- [ ] Integration installed and configured
- [ ] Controller connected and responding
- [ ] Sensors calibrated and reading accurately
- [ ] Environmental targets set appropriately
- [ ] Lighting schedule configured
- [ ] Irrigation system tested
- [ ] Notifications configured
- [ ] Premium features activated (if applicable)
- [ ] Backup configuration created

## Updating the Integration

### Automatic Updates (HACS)
1. Open HACS
2. Go to **Integrations** tab
3. Find OpenGrowBox
4. Click **"Update"** if available

### Manual Updates
```bash
# Update via git (if installed manually)
cd /config/custom_components/opengrowbox
git pull origin main
# Restart Home Assistant
```

### Firmware Updates
1. Check for controller firmware updates in HA integration
2. Follow on-screen instructions for firmware update
3. Do not power off during firmware update
4. Verify functionality after update

---

**Installation completed successfully!** Your OpenGrowBox is now integrated with Home Assistant and ready for automated plant cultivation.

**Next Steps:**
1. Review the [Getting Started Guide](../getting_started/DOCUMENTATION_INDEX.md)
2. Configure your [Plant Settings](../core_concepts/ROOMS_ZONES.md)
3. Set up [Environmental Controls](../core_concepts/CONTROL_MODES.md)
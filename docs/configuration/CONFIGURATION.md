# OpenGrowBox Configuration Guide

## Quick Start (5 minutes)

OpenGrowBox uses a simple integration-based setup - **no configuration.yaml needed**. One integration creates one complete grow room area.

## Step 1: Install Integration

### Method A: HACS Installation (Recommended)
1. Open Home Assistant
2. Navigate to **HACS** → **Integrations**
3. Click **Explore & Download Repositories**
4. Search for "**OpenGrowBox**"
5. Click **Download** → **Install**
6. Click **Restart** to restart Home Assistant

### Method B: Manual Download
1. Download latest release from [OpenGrowBox GitHub](https://github.com/your-repo/OpenGrowBox)
2. Extract to `/config/custom_components/opengrowbox/`
3. Restart Home Assistant

## Step 2: Add Integration

1. Open Home Assistant
2. Navigate to **Settings** → **Devices & Services** → **Integrations**
3. Click **+ Add Integration**
4. Search for "**OpenGrowBox**"
5. Click **OpenGrowBox**
6. Follow setup wizard

## Step 3: Configure Room

**One Integration = One Complete Room**

### Basic Room Setup
```
Room Name: My Grow Room          # Your room name
Controller IP: 192.168.1.100    # OGB controller address
Controller Port: 80              # Usually 80
Room Icon: mdi:leaf              # Optional room icon
```

### Automatic Device Management
**OpenGrowBox handles everything automatically:**

✅ **Creates room/area** in Home Assistant  
✅ **Discovers all connected devices**  
✅ **Proper naming and labeling**  
✅ **Organizes devices by room**  
✅ **Sets up sensors and controls**  
✅ **No manual device management needed**

## Step 4: Plant Setup

### Plant Configuration (Web Interface)
After integration is added, access the OpenGrowBox configuration through:

- **Home Assistant → Integrations → OpenGrowBox → Configure**
- **Or directly through any OGB device card**

### Basic Settings
```
Plant Type: Cannabis                # Dropdown: Cannabis, Tomato, Pepper, Lettuce
Growth Stage: EarlyVeg             # Auto-detected with visual indicators
Plant Count: 4                    # Number of plants in room
Container Size: 50L               # Growing container size
```

### Growth Stage Visual Indicators
- **Germination** 🌱 - Seed starting
- **EarlyVeg** 🌿 - Early vegetation  
- **MidVeg** 🌿 - Mid vegetation
- **LateVeg** 🌿 - Late vegetation
- **EarlyFlower** 🌻 - Early flowering
- **MidFlower** 🌺 - Mid flowering
- **LateFlower** 🌺 - Late flowering

## What OpenGrowBox Does Automatically

### Room Organization
```
Your Room Name/
├── Light Controls/
│   ├── Main Light (devmainlight)
│   ├── UV Light (devuvlight)
│   ├── Far Red Light (devfarredlight)
│   ├── Blue Spectrum (devbluelight)
│   └── Red Spectrum (devredlight)
├── Environmental Controls/
│   ├── Exhaust Fan (devexhaustfan)
│   ├── Intake Fan (devintakefan)
│   ├── Ventilation (devventilationfan)
│   ├── Humidifier (devhumidifier)
│   ├── Dehumidifier (devdehumidifier)
│   ├── Heater (devheater)
│   └── CO2 Controller (devco2controller)
├── Environmental Sensors/
│   ├── Temperature (devtempsensor)
│   ├── Humidity (devhumiditysensor)
│   ├── VPD (devvpdsensor)
│   └── CO2 (devco2sensor)
└── Hydroponics/
    ├── Water Pump (devpump)
    ├── EC Sensor (devecsensor)
    ├── pH Sensor (devphsensor)
    └── Water Level Sensor (devwaterlevelsensor)
```

### Device Naming Convention
- **Auto-discovery:** All devices found automatically
- **Consistent naming:** dev[device_name] format
- **Room assignment:** All devices automatically assigned to your room
- **Entity organization:** Sensors, switches, numbers properly categorized
- **Friendly names:** Auto-generated for dashboard display

## Web Interface Access

### Configuration Options
Access through any of these methods:

1. **Home Assistant:** Settings → Integrations → OpenGrowBox → Configure
2. **Device Cards:** Click any OpenGrowBox device → Configure
3. **Service Calls:** Developer Tools → Services → OpenGrowBox

### Main Configuration Tabs

**Environmental Control**
- Temperature targets and limits
- Humidity targets and limits  
- VPD (Vapor Pressure Deficit) control
- CO2 enrichment settings
- Safety limits and emergency stops

**Lighting Control**
- Automatic sunrise/sunset transitions
- Spectrum control (blue, red, UV, far-red)
- Intensity and DLI (Daily Light Integral) management
- Photoperiod control

**Hydroponics**
- Automated watering schedules
- Nutrient mixing (EC/pH control)
- Drainage systems
- Monitoring and alerts

**Notifications**
- Alert levels and channels
- Quiet hours
- Mobile notifications
- Webhook integration

## Common Setup Issues

### Problem: Integration not showing up
**Solution:** 
- Ensure restart completed after installation
- Check HACS updates and restart again
- Verify `/config/custom_components/opengrowbox/` folder exists

### Problem: Devices not discovered
**Solution:**
- Verify OGB controller is powered and on network
- Check IP address and port accessibility
- Restart OGB controller and Home Assistant

### Problem: Devices in wrong room
**Solution:**
- This should never happen - OGB handles room assignment
- If it occurs, delete and re-add integration

### Problem: No control options showing
**Solution:**
- Navigate to device card in Home Assistant
- Click "Configure" 
- Check integration is fully loaded (no integration errors)

### Problem: Sensor values not updating
**Solution:**
- Check network connectivity to OGB controller
- Verify OGB firmware is up to date
- Check Home Assistant logs for connection errors

## Advanced Settings (Optional)

All advanced settings are available through the web interface:

### Custom Plant Profiles
- Save specific settings for different plant types
- Upload custom environmental targets
- Create custom growth stage profiles

### Automation Rules
- Environmental response automation
- Backup and restore settings
- Schedule-based adjustments

### Air Exchange Cold Guard (Advanced)

OpenGrowBox can automatically suppress repeated cold-air exchange actions when
ambient/outside air is too cold for the room's current minimum temperature range.

Optional tunables (stored in `controlOptions`):

- `airExchangeColdAmbientDelta` (default `1.2` C)
- `airExchangeColdMinMargin` (default `0.8` C)
- `airExchangeColdHumidityDelta` (default `15` %RH)
- `airExchangeColdHumidityMargin` (default `5` %RH)
- `airExchangeColdWindowMinutes` (default `30`)
- `airExchangeColdLockMinutes` (default `60`)
- `airExchangeUnlockMargin` (default `1.2` C)

Runtime state is stored in `safety.airExchangeColdGuard` and includes block count,
lock timeout, and last decision metadata for troubleshooting.

### Analytics (Premium)
- Growth tracking and reporting
- Environmental history
- Optimization recommendations
- Compliance reporting

## Getting Help

### Support Resources
- **Documentation:** Available in Home Assistant integration
- **Community:** OpenGrowBox Discord/Forum
- **Issues:** GitHub repository issues
- **Troubleshooting:** Check Home Assistant logs for OpenGrowBox errors

### Information to Include in Support Requests
```
- OpenGrowBox version (from integration info)
- Home Assistant version
- OGB controller firmware version
- Network setup details
- Error messages from logs
```

---

## ✅ Configuration Complete!

Your OpenGrowBox integration is now:
- **Installed and connected** to your OGB controller
- **Automatically managing** all devices in one room
- **Ready for plant growth** with default settings optimized for your plant type

**Next Steps:**
1. Verify devices appear in Home Assistant dashboard
2. Test basic controls (lights, fans, etc.)
3. Adjust environmental targets if needed
4. Start growing!

**No manual configuration files required - everything handled through the web interface!**

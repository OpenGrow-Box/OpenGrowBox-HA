# 🚀 OpenGrowBox Installation Guide

**5-Minute Setup for Home Assistant Integration**

---

## 📋 Prerequisites

### ✅ System Requirements
- **Home Assistant**: 2024.x or later
- **Python**: 3.9+ (included with HA)
- **Network**: Stable connection
- **Storage**: 100MB free space

### ✅ Hardware (Optional)
- **Grow Equipment**: Any HA-compatible devices (lights, sensors, pumps, etc.)
- **FridgeGrow**: Plantalytix controllers (optional)

**No special hardware required!** OGB works with any Home Assistant devices.

---

## 🛠️ Installation (Choose One Method)

### Method 1: HACS (Recommended - 2 Minutes)

1. **Install HACS** (skip if already installed):
   - Go to **Settings** → **Add-ons, Backups & Supervisor** → **Add-on Store**
   - Search and install **"HACS"**
   - Restart Home Assistant

2. **Install OpenGrowBox**:
   - Open **HACS** from sidebar
   - Go to **Integrations** tab
   - Search **"OpenGrowBox"**
   - Click **Download**
   - **Restart Home Assistant**

3. **Add Integration**:
   - Go to **Settings** → **Devices & Services**
   - Click **+ Add Integration**
   - Search **"OpenGrowBox"**
   - Click **Add**
   - **Done!** 🎉

### Method 2: Manual Installation (3 Minutes)

```bash
# 1. Download integration
cd /config
git clone https://github.com/OpenGrow-Box/OpenGrowBox-HA.git
cp -r OpenGrowBox-HA/custom_components/opengrowbox /config/custom_components/

# 2. Restart HA
# From UI: Settings → System → Restart
# OR: ha core restart

# 3. Add Integration (same as HACS method above)
```

### Method 3: Docker Installation

For custom HA setups:

```dockerfile
# Add to your docker-compose.yml
services:
  homeassistant:
    volumes:
      - ./custom_components/opengrowbox:/config/custom_components/opengrowbox
```

---

## ⚙️ Initial Setup (2 Minutes)

### Step 1: Create Your First Room

1. **Open OGB Integration**:
   - Go to **Settings** → **Devices & Services**
   - Find **OpenGrowBox** integration
   - Click **Configure**

2. **Add Grow Room**:
   - Click **"Add Room"**
   - Name: `"Main Grow Tent"`
   - Area: Select your grow area (or create new)
   - Click **Save**

### Step 2: Configure Environment

1. **Set Plant Type**:
   - Choose: `"Tomato"`, `"Cannabis"`, `"Herbs"`, etc.
   - Or select `"Custom"` for manual settings

2. **Growth Stage**:
   - `"Germination"`, `"Vegetative"`, `"Flowering"`, `"Drying"`

3. **Environmental Targets**:
   - **Temperature**: 20-28°C (plant-dependent)
   - **Humidity**: 40-70% (stage-dependent)
   - **VPD Target**: 1.2 kPa (auto-calculated)

### Step 3: Add Devices (Optional)

**No devices needed for basic functionality!** OGB can monitor and control using any HA entities.

#### Quick Device Setup:

1. **Assign Existing Devices**:
   - Go to your HA devices
   - Add labels to entities:
     - Temperature sensor: `Sensor`
     - Humidity sensor: `Sensor`
     - Heater: `heater`
     - Light: `light`
     - Exhaust fan: `exhaust`

2. **FridgeGrow Setup** (if you have one):
   - Connect FridgeGrow to MQTT
   - Entities appear in HA automatically
   - Add labels: `fridgegrow` + output type
   - Example: `number.fridgegrow_heater` → labels: `["fridgegrow", "heater"]`

**[Device Setup Details →](../device_management/supported_devices_hardware.md)**

---

## ✅ Verification (1 Minute)

### Check Integration Status

1. **Dashboard**: OGB entities should appear
2. **VPD Display**: Current VPD calculation
3. **Device Control**: Manual controls working
4. **Logs**: No errors in HA logs

### Test Basic Functions

```bash
# Check OGB status
curl http://localhost:8123/api/states | jq '.[] | select(.entity_id | contains("ogb"))'
```

**Expected Result**: OGB sensors and controls are active

---

## 🎛️ Advanced Configuration

### Premium Features Setup

1. **Get Premium Account**:
   - Visit [OpenGrowBox Premium](https://opengrowbox.com/premium)
   - Create account and subscribe

2. **Activate in HA**:
   - Go to OGB Integration settings
   - Enter tenant ID and API key
   - Features activate automatically

### Multi-Room Setup

1. **Add Multiple Rooms**:
   - Different areas for veg/flower
   - Separate environmental controls
   - Independent automation

2. **Room Configuration**:
   ```yaml
   # Advanced room settings (optional)
   opengrowbox:
     rooms:
       veg_tent:
         plant_type: "cannabis"
         stage: "vegetative"
         vpd_target: 1.0
       flower_tent:
         plant_type: "cannabis"
         stage: "flowering"
         vpd_target: 1.4
   ```

### Custom Automation

**Example: Night Time Humidity Boost**

```yaml
automation:
  - alias: "Night Humidity Boost"
    trigger:
      platform: time
      at: "22:00"
    action:
      service: opengrowbox.set_target
      data:
        room: "grow_tent"
        humidity: 65
```

**[Advanced Config →](../configuration/CONFIGURATION.md)**

---

## 🐛 Troubleshooting

### Integration Won't Load

**Symptoms**: OGB not appearing in integrations

**Solutions**:
```bash
# 1. Check logs
grep -i opengrowbox /config/home-assistant.log

# 2. Restart HA
ha core restart

# 3. Check file permissions
ls -la /config/custom_components/opengrowbox/
```

### No Devices Found

**Symptoms**: "No devices initialized"

**Solutions**:
- Check device labels are applied correctly
- Ensure devices are in correct HA area
- Verify device compatibility

### VPD Not Calculating

**Symptoms**: VPD shows "unavailable"

**Solutions**:
- Ensure temperature + humidity sensors exist
- Check sensor labels: `temperature`, `humidity`
- Verify sensors are in grow room area

**[Full Troubleshooting →](../system_management/TROUBLESHOOTING.md)**

---

## 📚 Next Steps

### Learn the Basics
- **[VPD Control](../core_concepts/action_cycles/VPD_MODES_COMPLETE_IMPLEMENTATION.md)** - Understand climate automation
- **[Device Management](../device_management/device_management.md)** - Add more equipment
- **[Hydroponics](../specialized_systems/HYDRO_FEEDING_SYSTEM.md)** - Nutrient management

### Advanced Features
- **[AI Optimization](../premium_features/PREMIUM_FEATURES_OVERVIEW.md)** - Premium climate control
- **[Analytics](../premium_features/DATARELEASE_SYSTEM.md)** - Growth tracking
- **[Crop Steering](../specialized_systems/CROP_STEERING.md)** - Automated nutrient profiles

### Community & Support
- **📖 [Full Documentation](../)**
- **💬 [Discord Community](https://discord.gg/opengrowbox)**
- **🐛 [GitHub Issues](https://github.com/OpenGrow-Box/OpenGrowBox-HA/issues)**

---

## 🎉 Success Checklist

- [x] **Integration Installed**: OGB appears in HA integrations
- [x] **Room Created**: Grow area configured
- [x] **Environment Set**: Plant type and targets configured
- [x] **Devices Working**: Sensors reading, controls responding
- [x] **VPD Active**: Climate automation running
- [x] **No Errors**: Clean HA logs

**Your automated grow system is ready! Happy growing! 🌱**

---

*Need help? Check the [troubleshooting guide](../system_management/TROUBLESHOOTING.md) or join our [community](https://discord.gg/opengrowbox).*

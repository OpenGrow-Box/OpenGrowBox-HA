# Comprehensive Troubleshooting Guide

## Overview

This guide provides comprehensive troubleshooting for OpenGrowBox issues, organized by category and severity. Use this guide to diagnose and resolve common problems with your grow setup.

## Quick Diagnosis Checklist

### Before Troubleshooting

1. **Check System Status**
   ```bash
   # Check HA logs for OGB messages
   grep -i "opengrowbox\|ogb" /config/home-assistant.log

   # Check if OGB integration is loaded
   curl http://localhost:8123/api/states | grep opengrowbox
   ```

2. **Verify Basic Setup**
   - ✅ HA version compatible (2024.x+)
   - ✅ OGB integration installed and loaded
   - ✅ Rooms/zones configured in HA
   - ✅ Devices assigned to correct areas
   - ✅ mainControl set to "HomeAssistant"

3. **Check Device Status**
   - ✅ Devices powered on and connected
   - ✅ HA entities available and updating
   - ✅ Device labels correctly applied

## Common Issues by Category

## 🔧 Initialization & Startup Issues

### **"OpenGrowBox integration not loading"**

**Symptoms:**
- OGB not appearing in HA integrations
- No OGB entities in HA
- Integration shows "Failed to set up"

**Causes & Solutions:**

#### 1. Import Errors
```bash
# Check for Python import errors
tail -f /config/home-assistant.log | grep -i "import\|module"
```

**Common fixes:**
- Restart HA completely
- Check for conflicting integrations
- Verify OGB files are in correct location

#### 2. Configuration Errors
```yaml
# Check configuration.yaml syntax
opengrowbox:
  mainControl: "HomeAssistant"  # Required
  updateInterval: 30           # Optional
```

**Fix:** Ensure basic configuration is present

#### 3. Permission Issues
- Check file permissions on OGB directory
- Ensure HA user can read/write OGB files

### **"No devices initialized"**

**Symptoms:**
- OGB loads but shows no devices
- Sensors show "unavailable"
- VPD calculations fail

**Debug Steps:**
1. Check device assignment to HA areas
2. Verify device labels are applied
3. Check OGB logs for device discovery messages

**Common Causes:**
- Devices not assigned to HA areas
- Incorrect device labels
- Room configuration missing

## 📊 Sensor & Data Issues

### **"Sensors not updating"**

**Symptoms:**
- Sensor values stuck at old values
- VPD calculations showing errors
- "NO Sensors Found to calc VPD"

**Diagnosis:**
```bash
# Check sensor entity states
curl http://localhost:8123/api/states | jq '.[] | select(.entity_id | contains("sensor")) | {entity_id, state, last_updated}'
```

**Solutions:**

#### 1. Sensor Configuration Issues
- Verify sensors are assigned to correct HA area
- Check sensor calibration settings
- Ensure sensors have proper labels

#### 2. Communication Problems
- Check physical connections (I2C, OneWire, etc.)
- Verify sensor power supply
- Test sensors with alternative software

#### 3. HA Entity Issues
- Restart sensor devices
- Reload HA integrations
- Check for conflicting sensor integrations

### **"VPD calculations incorrect"**

**Symptoms:**
- VPD values don't match expected ranges
- Controls not activating when they should
- "VPD calculation failed" errors

**Debug:**
```bash
# Check current sensor readings
curl http://localhost:8123/api/states | jq '.[] | select(.entity_id | contains("temperature") or contains("humidity"))'
```

**Causes:**
- Incorrect sensor placement (too close to equipment)
- Sensor calibration drift
- Environmental factors affecting readings

**Solutions:**
- Recalibrate sensors
- Move sensors away from heat sources
- Verify sensor accuracy with known standards

## 🎛️ Device Control Issues

### **"Devices not responding to OGB commands"**

**Symptoms:**
- Manual HA control works, OGB control doesn't
- Devices show correct state in HA but not physical state
- "Device command failed" errors

**Debug:**
```bash
# Check device entity states
curl http://localhost:8123/api/states | jq '.[] | select(.entity_id | contains("switch.") or contains("light.")) | {entity_id, state}'
```

**Common Issues:**

#### 1. Device Labels Missing/Incorrect
```yaml
# Correct device labeling
device_labels:
  - name: "heater"    # For heater devices
  - name: "fridgegrow"  # For FridgeGrow devices
```

#### 2. Capability Registration Failed
- Check device capabilities in OGB logs
- Verify device type mapping
- Ensure device supports required HA entity types

#### 3. Event System Issues
- Check event manager registration
- Verify action events are firing
- Debug event handler execution

### **"FridgeGrow devices not working"**

**Symptoms:**
- FridgeGrow entities visible in HA but not controlled by OGB
- MQTT connection issues
- Value scaling problems

**Specific Fixes:**
1. **Labels:** Ensure entities have both `fridgegrow` and output type labels
2. **MQTT:** Check MQTT broker connection and credentials
3. **Keepalive:** Verify MQTT control mode is active
4. **Scaling:** OGB uses 0-100%, FridgeGrow uses 0-1

## 🌡️ Climate Control Problems

### **"Temperature control not working"**

**Symptoms:**
- Heater/cooler not activating
- Temperature drifting outside targets
- PID control oscillations

**Debug:**
```bash
# Check current climate state
curl http://localhost:8123/api/states | jq '.[] | select(.entity_id | contains("climate") or contains("temperature"))'
```

**Solutions:**
- Verify heater/cooler device labels
- Check PID controller configuration
- Ensure safety limits are not exceeded

### **"Humidity control erratic"**

**Symptoms:**
- Humidifier/dehumidifier cycling rapidly
- Humidity overshooting targets
- Equipment damage from cycling

**Causes:**
- Hysteresis settings too narrow
- Sensor placement affecting readings
- Environmental factors causing rapid changes

**Solutions:**
- Adjust control deadbands
- Improve sensor placement
- Add dampening filters

## 💧 Hydroponics Issues

### **"Nutrient dosing not working"**

**Symptoms:**
- Pumps not activating for nutrient dosing
- pH/EC readings not updating
- "Hydroponics system offline" errors

**Debug:**
```bash
# Check hydroponics sensor states
curl http://localhost:8123/api/states | jq '.[] | select(.entity_id | contains("ph") or contains("ec"))'
```

**Common Issues:**
- Pump calibration incorrect
- Sensor calibration drift
- Timing conflicts with other systems

### **"Crop steering not activating"**

**Symptoms:**
- Nutrient profiles not adjusting
- pH/EC targets not changing
- "Crop steering disabled" warnings

**Causes:**
- Plant stage not set correctly
- Nutrient profile configuration errors
- Timing issues with dosing cycles

## 🔄 System Performance Issues

### **"High CPU/memory usage"**

**Symptoms:**
- HA slow or unresponsive
- OGB integration consuming excessive resources
- System crashes under load

**Diagnosis:**
```bash
# Check HA resource usage
top -p $(pgrep hass)

# Check OGB thread count
ps aux | grep opengrowbox | wc -l
```

**Solutions:**
- Reduce update intervals
- Limit concurrent operations
- Optimize polling frequencies
- Check for memory leaks

### **"Event system lag"**

**Symptoms:**
- Commands delayed in execution
- UI not updating in real-time
- "Event queue full" warnings

**Causes:**
- Too many simultaneous events
- Event handler blocking operations
- Queue overflow conditions

**Solutions:**
- Implement async event handlers
- Add rate limiting
- Increase event queue size

## 🔧 Premium Feature Issues

### **"Premium features not available"**

**Symptoms:**
- AI controls not working
- Analytics dashboard empty
- "Premium subscription required" messages

**Solutions:**
- Verify subscription status
- Check API credentials
- Restart OGB integration
- Check network connectivity to premium API

### **"Analytics data not collecting"**

**Symptoms:**
- Historical data missing
- Charts not populating
- Export functions failing

**Debug:**
```bash
# Check analytics service status
curl http://localhost:8123/api/states | grep analytics
```

**Causes:**
- Database connection issues
- Data retention policy exceeded
- Storage quota reached

## 🐛 Advanced Debugging

### **Enable Debug Logging**

Add to `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.opengrowbox: debug
    custom_components.opengrowbox.premium: debug
    custom_components.opengrowbox.devices: debug
```

### **Log Analysis Commands**

```bash
# Search for specific error patterns
grep -i "error\|exception\|failed" /config/home-assistant.log

# Check device initialization
grep -A 5 -B 5 "device.*init" /config/home-assistant.log

# Monitor event system
grep -i "emit\|event" /config/home-assistant.log

# Check VPD calculations
grep -i "vpd\|calculate" /config/home-assistant.log
```

### **Database Inspection**

```python
# Access OGB data store (in HA developer tools)
from custom_components.opengrowbox.OGBController.data.OGBParams.OGBParams import dataStore
print(dataStore.get("capabilities"))
print(dataStore.get("rooms"))
```

### **HA Developer Tools Scripts**

```python
# Check OGB integration status
hass.states.get('sensor.ogb_status')

# List all OGB entities
ogb_entities = [e for e in hass.states.all() if 'ogb' in e.entity_id.lower()]
for entity in ogb_entities:
    print(f"{entity.entity_id}: {entity.state}")

# Test device control
await hass.services.async_call('switch', 'turn_on', {'entity_id': 'switch.ogb_heater'})
```

## 🚨 Emergency Procedures

### **System Shutdown**
If OGB is causing system instability:

1. **Disable OGB Integration**
   - Go to HA Settings → Devices & Services
   - Find OpenGrowBox integration
   - Click "Disable"

2. **Manual Device Control**
   - Use HA dashboard for direct device control
   - Disable OGB automations temporarily

3. **Safe State Reset**
   - Turn off all grow equipment manually
   - Reset environmental controls to safe defaults
   - Monitor conditions manually

### **Data Recovery**
If configuration is corrupted:

1. **Backup Current Config**
   ```bash
   cp /config/.storage/opengrowbox* /config/backups/
   ```

2. **Reset to Defaults**
   - Remove OGB integration
   - Delete OGB storage files
   - Reinstall and reconfigure

3. **Restore from Backup**
   - Restore configuration files
   - Re-enable integration

## 📞 Getting Help

### **Community Support**
- **GitHub Issues**: Report bugs with full logs
- **HA Community Forum**: General questions
- **Discord/Slack**: Real-time help

### **Required Information for Support**
When reporting issues, always include:

1. **System Info**
   - HA version
   - OGB version
   - Hardware setup

2. **Logs**
   ```bash
   # Get recent logs
   tail -n 100 /config/home-assistant.log | grep -i opengrowbox
   ```

3. **Configuration**
   ```yaml
   # Relevant configuration sections
   opengrowbox:
     # Your config here
   ```

4. **Steps to Reproduce**
   - Detailed reproduction steps
   - Expected vs actual behavior

### **Commercial Support**
For enterprise installations and premium support:
- Contact OpenGrowBox support team
- Premium SLA options available
- On-site troubleshooting services

## 🔄 Recovery Procedures

### **Factory Reset**
Complete reset of OGB configuration:

1. **Stop HA**
2. **Remove OGB Files**
   ```bash
   rm -rf /config/custom_components/opengrowbox
   rm -f /config/.storage/opengrowbox*
   ```
3. **Restart HA**
4. **Clean Reinstall**
   ```bash
   git clone https://github.com/OpenGrow-Box/OpenGrowBox-HA.git
   cp -r OpenGrowBox-HA/custom_components/opengrowbox /config/custom_components/
   ```

### **Partial Reset**
Reset specific components:

```bash
# Reset device configurations
rm -f /config/.storage/opengrowbox_devices*

# Reset data store
rm -f /config/.storage/opengrowbox_datastore*

# Reset premium settings
rm -f /config/.storage/opengrowbox_premium*
```

## 📋 Checklist for New Installations

### **Pre-Installation**
- [ ] HA version 2024.x or later
- [ ] Sufficient system resources (2GB RAM recommended)
- [ ] MQTT broker configured (if using MQTT devices)
- [ ] Backup of existing HA configuration

### **Installation**
- [ ] Download correct OGB version
- [ ] Verify file integrity
- [ ] Copy to custom_components directory
- [ ] Set correct file permissions

### **Configuration**
- [ ] Basic OGB configuration in configuration.yaml
- [ ] Room/area setup in HA
- [ ] Device assignment to rooms
- [ ] Device labeling for OGB recognition
- [ ] Sensor calibration

### **Testing**
- [ ] HA restart successful
- [ ] OGB integration loads without errors
- [ ] Devices discovered and initialized
- [ ] Basic controls working (manual test)
- [ ] VPD calculations functioning
- [ ] Automations triggering correctly

### **Monitoring**
- [ ] Log monitoring setup
- [ ] Alert configuration
- [ ] Backup procedures in place
- [ ] Documentation updated

This comprehensive troubleshooting guide should resolve most OpenGrowBox issues. If problems persist, gather the required diagnostic information and seek help from the community or commercial support channels.
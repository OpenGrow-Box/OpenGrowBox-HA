# Troubleshooting Guide

## Overview

This guide provides comprehensive troubleshooting procedures for common OpenGrowBox issues, organized by system component and symptom.

## Quick Diagnosis Flow

### Step 1: Check System Status
```bash
# Check overall system health
curl http://your-ogb-device/api/v1/status

# Check HA integration
curl http://homeassistant:8123/api/states/sensor.ogb_zone1_temperature
```

### Step 2: Check Logs
```bash
# Check OGB logs
tail -f /var/log/opengrowbox/ogb.log

# Check HA logs for OGB entries
grep "opengrowbox" /config/home-assistant.log
```

### Step 3: Identify Problem Area
- **Sensors not updating** → Sensor Issues
- **Devices not responding** → Device Control Issues
- **Irrigation not working** → Crop Steering Issues
- **HA integration broken** → Integration Issues
- **System performance poor** → Performance Issues

## Sensor Issues

### Temperature/Humidity Sensors Not Reading

**Symptoms:**
- Sensor shows "unavailable" or "unknown"
- Values don't change
- HA entity state is "unavailable"

**Common Causes:**
1. **Wiring Issues**: Loose connections, incorrect pins
2. **Power Supply**: Insufficient voltage (need 3.3-5V)
3. **GPIO Conflicts**: Pin used by another device
4. **Sensor Damage**: Moisture ingress or physical damage

**Troubleshooting Steps:**
```bash
# 1. Check HA entity state
curl http://homeassistant:8123/api/states/sensor.your_temperature_sensor

# 2. Verify ESPHome device connectivity
curl http://esphome-device/status

# 3. Check GPIO pin availability
gpio readall  # On Raspberry Pi
```

**Solutions:**
```yaml
# For ESPHome devices - check sensor configuration
sensor:
  - platform: dht
    pin: GPIO4
    model: DHT22
    temperature:
      name: "Temperature"
      filters:
        - heartbeat: 30s  # Force updates every 30s
    humidity:
      name: "Humidity"
```

### VWC Sensors Reading Incorrectly

**Symptoms:**
- VWC values seem too high/low
- Irrigation triggers at wrong times
- Inconsistent readings

**Common Causes:**
1. **Calibration Issues**: Sensor not calibrated for medium
2. **Placement Problems**: Sensor not properly inserted
3. **Medium Type Mismatch**: Wrong calibration for growing medium
4. **Temperature Effects**: Extreme temperatures affecting readings

**Calibration Procedure:**
```bash
# Start VWC max calibration
curl -X POST http://your-ogb/api/v1/crop-steering/calibrate \
  -d '{"action": "start_max", "phase": "p1"}'

# Monitor calibration progress
curl http://your-ogb/api/v1/crop-steering/status
```

**Medium-Specific Troubleshooting:**
```python
# Check medium type setting
medium_type = ogb.get_medium_type()
print(f"Current medium: {medium_type}")

# Verify calibration exists for medium
calibrations = ogb.get_calibrations()
if medium_type not in calibrations:
    print(f"No calibration for {medium_type}")
```

## Device Control Issues

### Devices Not Responding to Commands

**Symptoms:**
- Lights don't turn on/off
- Pumps don't activate
- Relays don't click

**Common Causes:**
1. **Entity ID Mismatch**: Wrong entity ID in OGB configuration
2. **HA Service Issues**: HA services not working
3. **Device Offline**: Physical device disconnected
4. **Power Issues**: Insufficient power to relays

**Troubleshooting:**
```bash
# 1. Test HA entity directly
curl -X POST http://homeassistant:8123/api/services/switch/turn_on \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"entity_id": "switch.your_device"}'

# 2. Check device entity state
curl http://homeassistant:8123/api/states/switch.your_device

# 3. Verify OGB device configuration
curl http://your-ogb/api/v1/devices
```

**Power Supply Checks:**
```python
# Check voltage at relay module
# Should be 3.3V for signal, 12V/5A for device power
# Use multimeter to verify:
# - VCC pin: 3.3V
# - Relay power: 12V
# - Ground continuity
```

### Intermittent Device Operation

**Symptoms:**
- Devices work sometimes but not others
- Random failures
- Timing-related issues

**Common Causes:**
1. **Timing Conflicts**: Multiple systems controlling same device
2. **Power Spikes**: Electrical interference
3. **Software Race Conditions**: Concurrent operations
4. **HA Restart Issues**: Entities not available immediately

**Solutions:**
```yaml
# Add delays between operations
automation:
  - alias: "Staggered Device Control"
    trigger:
      platform: time
      at: "06:00:00"
    action:
      - delay: "00:00:05"  # 5 second delay
      - service: switch.turn_on
        entity_id: switch.exhaust_fan
      - delay: "00:00:02"  # 2 second delay
      - service: switch.turn_on
        entity_id: switch.grow_lights
```

## Crop Steering Issues

### Irrigation Not Triggering

**Symptoms:**
- Plants drying out despite system running
- No pump activation
- VWC readings normal but no watering

**Common Causes:**
1. **VWC Sensor Issues**: Sensors not reading correctly
2. **Phase Configuration**: Wrong VWC thresholds for phase
3. **Dripper Configuration**: No valid pump devices configured
4. **Safety Overrides**: Emergency stop active

**Diagnostic Commands:**
```bash
# Check current crop steering status
curl http://your-ogb/api/v1/crop-steering/status

# Get VWC sensor readings
curl http://your-ogb/api/v1/sensors/vwc

# Check phase configuration
curl http://your-ogb/api/v1/crop-steering/config
```

**Phase Reset Procedure:**
```bash
# Reset to monitoring phase (P0)
curl -X POST http://your-ogb/api/v1/crop-steering/reset-phase \
  -d '{"phase": "p0"}'

# Force irrigation test
curl -X POST http://your-ogb/api/v1/crop-steering/test-irrigation \
  -d '{"duration": 30}'
```

### Over/Under Watering

**Symptoms:**
- Plants showing stress (yellowing, wilting)
- Constant runoff or completely dry medium
- Frequent emergency irrigations

**Diagnosis:**
```python
# Check VWC trends over time
vwc_history = ogb.get_vwc_history(hours=24)
print("VWC Range:", min(vwc_history), "to", max(vwc_history))

# Analyze irrigation frequency
irrigation_events = ogb.get_irrigation_history(hours=24)
print("Irrigations in 24h:", len(irrigation_events))

# Check calibration validity
calibration_status = ogb.check_calibration()
if not calibration_status["valid"]:
    print("Calibration issue:", calibration_status["message"])
```

**Adjustment Procedures:**
```json
// Adjust VWC thresholds for current phase
{
  "phase": "p2",
  "adjustments": {
    "vwc_min": 60.0,  // Increase minimum
    "vwc_max": 75.0,  // Decrease maximum
    "irrigation_duration": 25  // Shorter irrigation
  }
}

// Recalibrate for medium type
{
  "medium_type": "coco",
  "recalibrate": true
}
```

## Integration Issues

### HA Connection Problems

**Symptoms:**
- OGB shows "disconnected" from HA
- No sensor data in OGB
- Device controls not working

**Troubleshooting:**
```bash
# 1. Check HA API availability
curl http://homeassistant:8123/api/

# 2. Verify OGB HA configuration
curl http://your-ogb/api/v1/config/ha

# 3. Test HA token validity
curl http://homeassistant:8123/api/states \
  -H "Authorization: Bearer YOUR_OGB_TOKEN"
```

**HA Integration Reset:**
```bash
# Stop OGB integration
sudo systemctl stop opengrowbox

# Clear HA token
rm /config/opengrowbox_token

# Restart HA
ha core restart

# Restart OGB with new token
sudo systemctl start opengrowbox
```

### WebSocket Connection Issues

**Symptoms:**
- Real-time updates not working
- "Connection lost" messages
- Delayed sensor updates

**WebSocket Diagnostics:**
```javascript
// Test WebSocket connection
const ws = new WebSocket('ws://your-ogb-device/ws');

ws.onopen = () => console.log('WebSocket connected');
ws.onmessage = (event) => console.log('Message:', event.data);
ws.onerror = (error) => console.error('WebSocket error:', error);
ws.onclose = (event) => console.log('Closed:', event.code, event.reason);
```

**Common Fixes:**
```python
# Check firewall settings
sudo ufw status
sudo ufw allow 80
sudo ufw allow 443

# Verify WebSocket port
netstat -tlnp | grep :80

# Check SSL certificate validity
openssl s_client -connect your-ogb-device:443 -servername your-ogb-device
```

## Performance Issues

### High CPU Usage

**Symptoms:**
- System running slow
- Delayed responses
- High CPU temperature

**Performance Analysis:**
```bash
# Check system resources
top -p $(pgrep -f opengrowbox)

# Profile Python process
python -m cProfile -s cumulative /path/to/opengrowbox/main.py

# Check memory usage
ps aux --sort=-%mem | head -10
```

**Optimization Steps:**
```python
# Reduce sensor polling frequency
sensor_config = {
    "update_interval": 60,  # Increase from 30s
    "averaging_window": 5   # Average over 5 readings
}

# Enable caching for expensive operations
cache_config = {
    "vwc_calculations": 300,  # Cache for 5 minutes
    "sensor_readings": 30     # Cache for 30 seconds
}
```

### Memory Leaks

**Symptoms:**
- Memory usage continuously increasing
- System requires frequent restarts
- Out of memory errors

**Memory Analysis:**
```python
import tracemalloc

# Start memory tracing
tracemalloc.start()

# Take snapshots
snapshot1 = tracemalloc.take_snapshot()
# ... run some operations ...
snapshot2 = tracemalloc.take_snapshot()

# Compare snapshots
stats = snapshot2.compare_to(snapshot1, 'traceback')
for stat in stats[:10]:
    print(f"{stat.size_diff} bytes: {stat.traceback}")
```

**Memory Leak Fixes:**
```python
# Clear old event listeners
async def cleanup_event_listeners(self):
    """Remove unused event listeners."""
    active_listeners = []
    for listener in self.event_listeners:
        if listener.is_active():
            active_listeners.append(listener)

    self.event_listeners = active_listeners

# Implement connection pooling
async def get_database_connection(self):
    """Reuse database connections."""
    if not self.connection_pool:
        self.connection_pool = await create_connection_pool()

    return await self.connection_pool.acquire()
```

## Emergency Procedures

### Complete System Failure

**Immediate Actions:**
1. **Stop all automated systems**
2. **Switch to manual control**
3. **Check power and connections**
4. **Monitor plants manually**

**Emergency Commands:**
```bash
# Emergency stop all systems
curl -X POST http://your-ogb/api/v1/emergency/stop

# Enable manual override
curl -X POST http://your-ogb/api/v1/mode/manual

# Reset all device states
curl -X POST http://your-ogb/api/v1/devices/reset
```

### Data Recovery

**Configuration Backup:**
```bash
# Backup current configuration
curl http://your-ogb/api/v1/config > ogb_config_backup.json

# Backup calibration data
curl http://your-ogb/api/v1/calibrations > calibrations_backup.json
```

**Factory Reset:**
```bash
# Reset to factory defaults (CAUTION!)
curl -X POST http://your-ogb/api/v1/factory-reset \
  -d '{"confirm": "YES_I_WANT_TO_RESET"}'

# Restore from backup
curl -X POST http://your-ogb/api/v1/config/restore \
  -d @ogb_config_backup.json
```

## Advanced Diagnostics

### System Log Analysis

**Log Pattern Recognition:**
```bash
# Search for error patterns
grep "ERROR\|CRITICAL" /var/log/opengrowbox/*.log | tail -20

# Find sensor failures
grep "sensor.*fail" /var/log/opengrowbox/*.log

# Check for memory issues
grep "MemoryError\|OutOfMemory" /var/log/opengrowbox/*.log
```

### Network Analysis

**Connectivity Testing:**
```bash
# Test network latency
ping -c 10 your-ogb-device

# Check port availability
nmap -p 80,443 your-ogb-device

# Monitor network traffic
tcpdump -i eth0 host your-ogb-device -w capture.pcap
```

### Database Issues

**Database Health Check:**
```bash
# Check database size
du -sh /path/to/ogb/database.db

# Run integrity check
sqlite3 /path/to/ogb/database.db "PRAGMA integrity_check;"

# Analyze query performance
sqlite3 /path/to/ogb/database.db "EXPLAIN QUERY PLAN SELECT * FROM sensors;"
```

## Getting Help

### Support Resources

1. **Documentation**: Check relevant guides first
2. **Logs**: Provide relevant log excerpts
3. **Configuration**: Include your current config
4. **System Info**: Hardware, HA version, OGB version

### Diagnostic Report Generation

```bash
# Generate comprehensive diagnostic report
curl http://your-ogb/api/v1/diagnostics/full > diagnostic_report.json

# Include system information
uname -a > system_info.txt
python --version > python_version.txt
```

---

**Remember**: Most issues can be resolved by checking connections, verifying configurations, and ensuring proper power supply. Start with the basics before diving into advanced troubleshooting.

**For urgent issues affecting plant health, always have manual backup systems ready.**</content>
<parameter name="filePath">docs/appendices/TROUBLESHOOTING.md
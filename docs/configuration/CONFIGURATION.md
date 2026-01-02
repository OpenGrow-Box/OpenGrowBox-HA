# Configuration and Settings Guide

## Overview

The OpenGrowBox system provides extensive configuration options for customizing environmental control, device behavior, and system preferences. This guide covers all configuration areas and settings.

## Configuration Architecture

### Configuration Storage

OpenGrowBox uses a hierarchical configuration system:

```python
# Primary configuration sources
configuration_sources = {
    "home_assistant": "HA config entry data",
    "data_store": "Persistent runtime configuration",
    "plant_stages": "Plant-specific environmental targets",
    "device_config": "Hardware device settings",
    "user_preferences": "Personalization settings"
}
```

### Configuration Hierarchy

1. **System Defaults**: Built-in fallback values
2. **Plant Stages**: Plant-specific environmental targets
3. **User Configuration**: Custom settings via HA interface
4. **Runtime Overrides**: Dynamic adjustments during operation

## Core System Configuration

### Basic Integration Settings

```yaml
# Home Assistant configuration.yaml
opengrowbox:
  - name: "Main Grow Room"          # Friendly name
    host: "192.168.1.100"           # Controller IP address
    port: 80                        # Usually 80
    ssl: false                      # Usually false for local
    timeout: 30                     # Connection timeout (seconds)
    update_interval: 30             # Data refresh interval (seconds)
```

### Control Options

```yaml
# Main control configuration
control_options:
  main_control: "Premium"           # "HomeAssistant" or "Premium"
  tent_mode: "VPD Perfection"       # Active control mode
  min_max_control: true             # Enable environmental limits
  own_weights: false                # Use custom humidity weights
  vpd_light_control: false          # Allow light adjustments for VPD
  emergency_stop: false             # Emergency stop active
```

## Plant Configuration

### Plant Type Settings

```python
# Plant configuration structure
plant_config = {
    "type": "Cannabis",              # Plant species
    "stage": "MidFlower",            # Current growth stage
    "strain": "Blue Dream",          # Specific strain/variety
    "start_date": "2025-01-15",      # Growth cycle start date
    "expected_harvest": "2025-03-15", # Estimated harvest date
    "container_size": 50,            # Growing container size (liters)
    "plant_count": 4                 # Number of plants
}
```

### Plant Stage Environmental Targets

```python
# Detailed plant stage configuration
plant_stages = {
    "Germination": {
        "vpd_range": [0.35, 0.70],    # VPD range (kPa)
        "min_temp": 20,               # Minimum temperature (°C)
        "max_temp": 24,               # Maximum temperature (°C)
        "min_humidity": 78,           # Minimum humidity (%)
        "max_humidity": 85,           # Maximum humidity (%)
        "light_schedule": "18/6",     # Light/dark hours
        "irrigation_freq": 2,         # Hours between watering
        "nutrient_strength": 0.5      # Nutrient concentration multiplier
    },
    "EarlyVeg": {
        "vpd_range": [0.60, 1.20],
        "min_temp": 22,
        "max_temp": 26,
        "min_humidity": 65,
        "max_humidity": 75,
        "light_schedule": "18/6",
        "irrigation_freq": 4,
        "nutrient_strength": 0.75
    },
    "MidFlower": {
        "vpd_range": [0.90, 1.70],
        "min_temp": 22,
        "max_temp": 26,
        "min_humidity": 55,
        "max_humidity": 68,
        "light_schedule": "12/12",
        "irrigation_freq": 6,
        "nutrient_strength": 1.0
    }
}
```

## Device Configuration

### Device Registration

```python
# Device configuration structure
devices = [
    {
        "name": "Main Exhaust Fan",
        "type": "Exhaust",
        "entity_id": "switch.exhaust_fan_main",
        "capabilities": ["canExhaust"],
        "settings": {
            "min_voltage": 20,
            "max_voltage": 100,
            "calibration_factor": 1.05,
            "safety_timeout": 300
        }
    },
    {
        "name": "Humidity Controller",
        "type": "Humidifier",
        "entity_id": "switch.humidifier_main",
        "capabilities": ["canHumidify"],
        "settings": {
            "binary_control": true,
            "max_runtime": 1800,
            "cooldown_period": 300
        }
    }
]
```

### Device Capability Mapping

```python
# Device capability definitions
device_capabilities = {
    "canExhaust": {
        "description": "Exhaust fan control",
        "voltage_range": [0, 100],
        "dampening_required": true,
        "emergency_capable": true
    },
    "canHumidify": {
        "description": "Humidity control",
        "binary_control": true,
        "dampening_required": true,
        "max_duty_cycle": 70
    },
    "canHeat": {
        "description": "Heating control",
        "voltage_range": [0, 100],
        "dampening_required": true,
        "safety_limits": {"max_temp": 30}
    }
}
```

## Environmental Control Settings

### VPD Configuration

```python
# VPD control settings
vpd_config = {
    "tolerance": 10,                 # Tolerance percentage (1-25%)
    "targeted": 1.25,                # User-set VPD target (VPD Target mode)
    "perfection": 1.3,               # Calculated perfect VPD
    "perfect_min": 1.17,             # Lower tolerance bound
    "perfect_max": 1.43,             # Upper tolerance bound
    "range": [1.2, 1.4],             # Current stage VPD range
    "current": 1.28                  # Current measured VPD
}
```

### Temperature and Humidity Settings

```python
# Environmental limits
environmental_limits = {
    "temperature": {
        "min": 18,                   # Minimum safe temperature (°C)
        "max": 32,                   # Maximum safe temperature (°C)
        "target": 25,                # Target temperature (°C)
        "tolerance": 2               # Acceptable deviation (°C)
    },
    "humidity": {
        "min": 40,                   # Minimum safe humidity (%)
        "max": 90,                   # Maximum safe humidity (%)
        "target": 60,                # Target humidity (%)
        "tolerance": 5               # Acceptable deviation (%)
    },
    "co2": {
        "min": 300,                  # Minimum safe CO2 (ppm)
        "max": 2000,                 # Maximum safe CO2 (ppm)
        "target": 800,               # Target CO2 (ppm)
        "control_enabled": true      # Enable CO2 control
    }
}
```

## Lighting Configuration

### Light Schedule Settings

```python
# Lighting configuration
lighting_config = {
    "schedule": {
        "light_on_time": "06:00",     # Sunrise time
        "light_off_time": "18:00",    # Sunset time
        "sunrise_duration": 30,       # Sunrise transition (minutes)
        "sunset_duration": 30         # Sunset transition (minutes)
    },
    "spectrum": {
        "mode": "auto",               # auto, blue, red, full_spectrum
        "blue_ratio": 0.4,            # Blue spectrum ratio (0-1)
        "red_ratio": 0.4,             # Red spectrum ratio (0-1)
        "white_ratio": 0.2            # White spectrum ratio (0-1)
    },
    "intensity": {
        "max_ppfd": 800,              # Maximum PPFD (μmol/m²/s)
        "dli_target": 35,             # Daily Light Integral target
        "dimming_enabled": true       # Allow automatic dimming
    }
}
```

### DLI and Photoperiod Control

```python
# Advanced lighting settings
advanced_lighting = {
    "dli_control": {
        "enabled": true,
        "target_dli": 35,             # mol/m²/day
        "seasonal_adjustment": true,  # Adjust for seasons
        "latitude": 45.0             # For seasonal calculations
    },
    "photoperiod": {
        "current_schedule": "12/12",  # Light/dark hours
        "auto_adjust": false,         # Automatic schedule changes
        "force_mode": false           # Override automatic changes
    }
}
```

## Irrigation and Feeding Configuration

### Automated Irrigation Settings

```python
# Irrigation configuration
irrigation_config = {
    "mode": "vwc_based",             # vwc_based, timed, manual
    "vwc_thresholds": {
        "min": 0.4,                  # Minimum VWC before watering
        "max": 0.8,                  # Maximum VWC after watering
        "tolerance": 0.05            # Acceptable deviation
    },
    "timing": {
        "check_interval": 1800,      # Check every 30 minutes
        "max_daily_water": 2000,     # Maximum water per day (ml)
        "safety_delay": 300          # Minimum delay between watering
    },
    "pump_settings": {
        "flow_rate": 50,             # ml/minute
        "calibration_factor": 1.02,  # Calibration adjustment
        "safety_timeout": 600        # Maximum run time (seconds)
    }
}
```

### Hydroponic Feeding Settings

```python
# Nutrient feeding configuration
hydroponic_config = {
    "feeding_mode": "automatic",     # automatic, manual, schedule
    "nutrient_profiles": {
        "cannabis": {
            "veg": {"A": 2.0, "B": 1.0, "C": 0.8, "pH": 5.8, "EC": 1.2},
            "flower": {"A": 1.2, "B": 3.0, "C": 1.5, "pH": 6.0, "EC": 2.2}
        }
    },
    "ph_control": {
        "target": 6.0,               # Target pH
        "tolerance": 0.1,            # Acceptable pH range
        "adjustment_volume": 5       # ml per adjustment
    },
    "ec_control": {
        "target": 2.0,               # Target EC (mS/cm)
        "tolerance": 0.1,            # Acceptable EC range
        "dilution_factor": 0.1       # Water addition ratio
    }
}
```

## System Preferences

### Notification Settings

```python
# Notification configuration
notification_config = {
    "enabled": true,
    "channels": ["persistent", "push"],  # Available: persistent, push, email, webhook
    "levels": {
        "critical": true,             # System failures, safety issues
        "warning": true,              # Performance issues, maintenance
        "info": false                 # Status updates, informational
    },
    "quiet_hours": {
        "enabled": true,
        "start_time": "22:00",
        "end_time": "08:00"
    },
    "throttling": {
        "max_per_hour": {
            "critical": 5,
            "warning": 10,
            "info": 30
        },
        "cooldown_minutes": {
            "critical": 10,
            "warning": 5,
            "info": 1
        }
    }
}
```

### Performance and Optimization

```python
# System performance settings
performance_config = {
    "monitoring": {
        "sensor_interval": 30,        # Sensor polling interval (seconds)
        "data_retention": 7,          # Days to keep raw sensor data
        "aggregation_interval": 3600  # Data aggregation interval (seconds)
    },
    "optimization": {
        "adaptive_cooldown": true,    # Use adaptive action cooldowns
        "batch_operations": true,     # Enable batch device operations
        "caching_enabled": true,      # Cache frequently used data
        "compression_threshold": 30   # Days before data compression
    },
    "safety": {
        "emergency_stop_enabled": true,
        "max_action_frequency": 10,   # Maximum actions per minute
        "fail_safe_mode": true        # Enable fail-safe operation
    }
}
```

## Premium Configuration

### Analytics and Compliance Settings

```python
# Premium feature configuration
premium_config = {
    "analytics": {
        "enabled": true,
        "data_collection": true,
        "privacy_level": "standard",   # minimal, standard, comprehensive
        "retention_period": 365       # Days to keep analytics data
    },
    "compliance": {
        "jurisdiction": "general",    # general, california, canada, eu
        "reporting_enabled": true,
        "audit_frequency": "monthly"  # daily, weekly, monthly
    },
    "ai_control": {
        "enabled": true,
        "learning_rate": 0.1,         # AI adaptation speed
        "confidence_threshold": 0.8,  # Minimum confidence for AI actions
        "fallback_mode": "VPD Perfection"  # Fallback when AI unavailable
    }
}
```

### Subscription and Feature Management

```python
# Subscription configuration
subscription_config = {
    "tier": "professional",          # basic, professional, enterprise
    "features": {
        "ai_control": true,
        "pid_control": true,
        "mpc_optimization": false,
        "analytics": true,
        "compliance": true,
        "research": false
    },
    "limits": {
        "max_rooms": 3,
        "max_devices": 50,
        "api_calls_per_hour": 1000,
        "data_retention_days": 365
    }
}
```

## Configuration Management

### Configuration Validation

```python
# Configuration validation schema
CONFIG_VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "plant_config": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["Cannabis", "Tomato", "Pepper", "Lettuce"]},
                "stage": {"type": "string", "enum": ["Germination", "EarlyVeg", "MidVeg", "LateVeg", "EarlyFlower", "MidFlower", "LateFlower"]},
                "plant_count": {"type": "integer", "minimum": 1, "maximum": 100}
            },
            "required": ["type", "stage"]
        },
        "environmental_limits": {
            "type": "object",
            "properties": {
                "temperature": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "number", "minimum": 10, "maximum": 40},
                        "max": {"type": "number", "minimum": 10, "maximum": 40}
                    }
                }
            }
        }
    }
}
```

### Configuration Backup and Restore

```python
# Configuration backup structure
backup_config = {
    "version": "2.0",
    "timestamp": "2025-12-24T10:00:00Z",
    "system_config": {...},
    "plant_config": {...},
    "device_config": {...},
    "environmental_config": {...},
    "user_preferences": {...}
}
```

### Configuration Migration

```python
# Handle configuration updates between versions
async def migrate_configuration(self, from_version: str, to_version: str):
    """Migrate configuration between versions."""

    migration_functions = {
        "1.0_to_2.0": self._migrate_1_0_to_2_0,
        "2.0_to_2.1": self._migrate_2_0_to_2_1,
    }

    migration_func = migration_functions.get(f"{from_version}_to_{to_version}")
    if migration_func:
        await migration_func()
        _LOGGER.info(f"Configuration migrated from {from_version} to {to_version}")
    else:
        _LOGGER.warning(f"No migration path from {from_version} to {to_version}")
```

## Configuration Interface

### Home Assistant Configuration UI

The OpenGrowBox integration provides a comprehensive configuration interface through Home Assistant:

1. **Basic Setup**: Initial device connection and plant configuration
2. **Environmental Targets**: Temperature, humidity, VPD, CO2 settings
3. **Device Management**: Individual device configuration and calibration
4. **Lighting Control**: Schedule, spectrum, and intensity settings
5. **Irrigation Settings**: Automated watering and nutrient delivery
6. **Notification Preferences**: Alert levels and delivery methods
7. **Premium Features**: Subscription and advanced feature configuration

### Advanced Configuration Options

For advanced users, additional configuration is available through:

- **YAML Configuration**: Direct editing in `configuration.yaml`
- **API Endpoints**: RESTful configuration management
- **Database Direct Access**: For development and troubleshooting
- **Configuration Templates**: Pre-built setups for common scenarios

---

**Configuration completed!** Your OpenGrowBox system is now fully configured and ready for optimal plant growth.

**Configuration Areas Covered:**
- ✅ System and integration settings
- ✅ Plant and environmental targets
- ✅ Device and hardware configuration
- ✅ Lighting and spectrum control
- ✅ Irrigation and feeding systems
- ✅ Notification and alert preferences
- ✅ Premium feature settings
- ✅ Performance and safety options


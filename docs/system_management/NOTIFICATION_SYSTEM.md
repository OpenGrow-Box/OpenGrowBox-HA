# Notification System - Alert Management & User Communication

## Overview

The OpenGrowBox Notification System provides intelligent alert management and user communication for critical system events, maintenance reminders, and performance monitoring. It features rate limiting, priority-based delivery, and multi-channel notification support.

## System Architecture

### Core Components

#### 1. OGBNotificator (`OGBNotifyManager.py`)
```python
class OGBNotificator:
    """Main notification system with rate limiting and priority management."""
```

#### 2. Notification Types
- **Critical**: System failures, safety issues
- **Warning**: Performance issues, maintenance needs
- **Info**: Status updates, informational messages

#### 3. Delivery Channels
- **Persistent Notifications**: Home Assistant UI notifications
- **Push Notifications**: Mobile app alerts
- **External Services**: Custom notification services

## Notification Priority Levels

### Critical Notifications
- **Purpose**: Immediate attention required
- **Examples**:
  - System failures or crashes
  - Sensor malfunctions
  - Safety violations (temperature extremes, equipment failure)
  - Emergency shutdowns
- **Rate Limit**: 5 per hour
- **Cooldown**: 10 minutes between notifications
- **Delivery**: Immediate, persistent until acknowledged

### Warning Notifications
- **Purpose**: Issues requiring attention but not immediate danger
- **Examples**:
  - Calibration drift detected
  - Performance degradation
  - Maintenance reminders
  - Resource warnings (low nutrients, filter replacement)
- **Rate Limit**: 10 per hour
- **Cooldown**: 5 minutes between notifications
- **Delivery**: Prompt, visible for extended period

### Info Notifications
- **Purpose**: Status updates and informational messages
- **Examples**:
  - System status reports
  - Calibration completion
  - Growth stage transitions
  - Performance summaries
- **Rate Limit**: 30 per hour
- **Cooldown**: 1 minute between notifications
- **Delivery**: Standard, auto-dismiss after reasonable time

## Rate Limiting Implementation

### Adaptive Rate Limiting

```python
class NotificationRateLimiter:
    """Manages notification frequency to prevent spam."""

    def __init__(self):
        self.rate_limits = {
            "critical": {"max_per_hour": 5, "cooldown_minutes": 10},
            "warning": {"max_per_hour": 10, "cooldown_minutes": 5},
            "info": {"max_per_hour": 30, "cooldown_minutes": 1}
        }
        self.notification_history = {
            "critical": [],
            "warning": [],
            "info": []
        }

    async def check_rate_limit(self, level: str, identifier: str) -> bool:
        """Check if notification can be sent based on rate limits."""

        limits = self.rate_limits[level]
        history = self.notification_history[level]

        # Remove old notifications outside time window
        cutoff_time = datetime.now() - timedelta(hours=1)
        history[:] = [n for n in history if n["timestamp"] > cutoff_time]

        # Check hourly limit
        if len(history) >= limits["max_per_hour"]:
            return False

        # Check cooldown for same identifier
        for notification in history:
            if notification["identifier"] == identifier:
                time_since_last = datetime.now() - notification["timestamp"]
                if time_since_last < timedelta(minutes=limits["cooldown_minutes"]):
                    return False

        return True

    def record_notification(self, level: str, identifier: str):
        """Record a sent notification for rate limiting."""

        self.notification_history[level].append({
            "identifier": identifier,
            "timestamp": datetime.now()
        })
```

## Notification Content Management

### Message Templates

```python
NOTIFICATION_TEMPLATES = {
    "sensor_failure": {
        "title": "Sensor Failure Detected",
        "template": "Sensor '{sensor_name}' has failed. Last reading: {last_value}. System switching to fallback mode.",
        "level": "critical",
        "actions": ["acknowledge", "troubleshoot"]
    },
    "calibration_drift": {
        "title": "Calibration Drift Warning",
        "template": "Sensor '{sensor_name}' calibration has drifted by {drift_percent}%. Recalibration recommended.",
        "level": "warning",
        "actions": ["recalibrate", "dismiss"]
    },
    "maintenance_due": {
        "title": "Maintenance Required",
        "template": "{component_name} maintenance is due. Last serviced: {last_service_date}.",
        "level": "warning",
        "actions": ["schedule_maintenance", "snooze"]
    },
    "system_startup": {
        "title": "System Started Successfully",
        "template": "OpenGrowBox system initialized. All components operational. Monitoring active.",
        "level": "info",
        "actions": ["view_status"]
    }
}
```

### Contextual Information

```python
def enrich_notification_context(self, notification_type: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Add contextual information to notifications."""

    enriched_context = context.copy()

    # Add system status
    enriched_context["system_status"] = self.get_system_status_summary()

    # Add room information
    enriched_context["room_name"] = self.room
    enriched_context["timestamp"] = datetime.now().isoformat()

    # Add severity assessment
    enriched_context["severity_score"] = self.calculate_severity_score(notification_type, context)

    # Add recommended actions
    enriched_context["recommended_actions"] = self.get_recommended_actions(notification_type)

    return enriched_context
```

## Multi-Channel Delivery

### Home Assistant Persistent Notifications

```python
async def send_persistent_notification(self, title: str, message: str, level: str):
    """Send notification via Home Assistant persistent notifications."""

    service_data = {
        "title": f"🌱 OGB {self.room}: {title}",
        "message": message
    }

    # Add level-specific formatting
    if level == "critical":
        service_data["title"] = f"🚨 {service_data['title']}"
    elif level == "warning":
        service_data["title"] = f"⚠️ {service_data['title']}"
    else:
        service_data["title"] = f"ℹ️ {service_data['title']}"

    await self.hass.services.async_call(
        "persistent_notification", "create", service_data, blocking=True
    )
```

### Mobile Push Notifications

```python
async def send_push_notification(self, title: str, message: str, level: str, service: str = None):
    """Send push notification to mobile devices."""

    # Use specified service or default mobile app service
    notification_service = service or "notify.mobile_app"

    service_data = {
        "title": title,
        "message": message,
        "data": {
            "ttl": self.get_ttl_for_level(level),
            "priority": self.get_priority_for_level(level),
            "channel": "ogb_notifications",
            "tag": f"ogb_{self.room}_{level}"
        }
    }

    try:
        await self.hass.services.async_call(
            notification_service, service_data, blocking=False
        )
    except Exception as e:
        _LOGGER.warning(f"Push notification failed: {e}")
        # Fallback to persistent notification
        await self.send_persistent_notification(title, message, level)
```

### Custom Notification Services

```python
async def send_custom_notification(self, title: str, message: str, service_config: Dict[str, Any]):
    """Send notification via custom service (email, SMS, webhooks)."""

    service_type = service_config.get("type")

    if service_type == "email":
        await self.send_email_notification(title, message, service_config)
    elif service_type == "webhook":
        await self.send_webhook_notification(title, message, service_config)
    elif service_type == "sms":
        await self.send_sms_notification(title, message, service_config)
    else:
        _LOGGER.error(f"Unsupported notification service type: {service_type}")
```

## Smart Notification Filtering

### Context-Aware Suppression

```python
def should_suppress_notification(self, notification_type: str, context: Dict[str, Any]) -> bool:
    """Determine if notification should be suppressed based on context."""

    # Suppress if system is in maintenance mode
    if self.is_maintenance_mode_active():
        return True

    # Suppress duplicate notifications within short time
    if self.is_duplicate_notification(notification_type, context):
        return True

    # Suppress based on user preferences
    user_prefs = self.get_user_notification_preferences()
    if not user_prefs.get(notification_type, True):
        return True

    # Suppress low-priority notifications during certain times
    if self.is_quiet_hours() and context.get("level") == "info":
        return True

    return False
```

### Escalation Logic

```python
async def handle_notification_escalation(self, notification_type: str, attempts: int):
    """Escalate notification delivery if initial attempts fail."""

    if attempts == 1:
        # Try alternative delivery method
        await self.retry_with_alternative_method(notification_type)
    elif attempts == 2:
        # Escalate to higher priority
        await self.escalate_notification_priority(notification_type)
    elif attempts >= 3:
        # Final escalation - ensure delivery
        await self.force_delivery_notification(notification_type)
```

## User Preferences and Customization

### Notification Profiles

```python
NOTIFICATION_PROFILES = {
    "minimal": {
        "critical": True,
        "warning": False,
        "info": False,
        "quiet_hours": True,
        "channels": ["persistent"]
    },
    "standard": {
        "critical": True,
        "warning": True,
        "info": False,
        "quiet_hours": True,
        "channels": ["persistent", "push"]
    },
    "comprehensive": {
        "critical": True,
        "warning": True,
        "info": True,
        "quiet_hours": False,
        "channels": ["persistent", "push", "email"]
    }
}
```

### Quiet Hours Management

```python
def is_quiet_hours(self) -> bool:
    """Check if current time is within quiet hours."""

    now = datetime.now().time()
    quiet_start = datetime.strptime(self.quiet_start_time, "%H:%M").time()
    quiet_end = datetime.strptime(self.quiet_end_time, "%H:%M").time()

    if quiet_start <= quiet_end:
        # Same day quiet hours
        return quiet_start <= now <= quiet_end
    else:
        # Overnight quiet hours
        return now >= quiet_start or now <= quiet_end
```

## Notification Analytics and Reporting

### Delivery Tracking

```python
class NotificationAnalytics:
    """Track notification delivery and effectiveness."""

    def __init__(self):
        self.delivery_stats = {}
        self.user_interactions = {}

    def record_delivery(self, notification_id: str, channel: str, success: bool):
        """Record notification delivery attempt."""

        if notification_id not in self.delivery_stats:
            self.delivery_stats[notification_id] = {
                "attempts": [],
                "successful_channels": [],
                "failed_channels": []
            }

        attempt = {
            "channel": channel,
            "timestamp": datetime.now(),
            "success": success
        }

        self.delivery_stats[notification_id]["attempts"].append(attempt)

        if success:
            self.delivery_stats[notification_id]["successful_channels"].append(channel)
        else:
            self.delivery_stats[notification_id]["failed_channels"].append(channel)

    def generate_delivery_report(self) -> Dict[str, Any]:
        """Generate notification delivery analytics report."""

        total_notifications = len(self.delivery_stats)
        successful_deliveries = sum(
            1 for stats in self.delivery_stats.values()
            if stats["successful_channels"]
        )

        success_rate = (successful_deliveries / total_notifications) * 100 if total_notifications > 0 else 0

        channel_performance = self.analyze_channel_performance()

        return {
            "total_notifications": total_notifications,
            "successful_deliveries": successful_deliveries,
            "success_rate": success_rate,
            "channel_performance": channel_performance,
            "top_failing_notifications": self.get_top_failing_notifications()
        }
```

### User Interaction Tracking

```python
def record_user_interaction(self, notification_id: str, action: str, timestamp: datetime = None):
    """Record user interaction with notifications."""

    if timestamp is None:
        timestamp = datetime.now()

    if notification_id not in self.user_interactions:
        self.user_interactions[notification_id] = []

    self.user_interactions[notification_id].append({
        "action": action,
        "timestamp": timestamp,
        "time_to_action": self.calculate_time_to_action(notification_id, timestamp)
    })
```

## System Integration

### Event-Driven Notifications

```python
async def setup_event_listeners(self):
    """Set up event listeners for automatic notifications."""

    # System events
    self.event_manager.on("SystemStartup", self.notify_system_startup)
    self.event_manager.on("SystemShutdown", self.notify_system_shutdown)
    self.event_manager.on("SystemError", self.notify_system_error)

    # Device events
    self.event_manager.on("DeviceFailure", self.notify_device_failure)
    self.event_manager.on("DeviceRecovery", self.notify_device_recovery)

    # Sensor events
    self.event_manager.on("SensorDrift", self.notify_sensor_drift)
    self.event_manager.on("SensorCalibrationDue", self.notify_calibration_due)

    # Environmental events
    self.event_manager.on("EnvironmentalAlert", self.notify_environmental_alert)
    self.event_manager.on("VPDOutOfRange", self.notify_vpd_alert)

    # Maintenance events
    self.event_manager.on("MaintenanceDue", self.notify_maintenance_due)
    self.event_manager.on("CalibrationComplete", self.notify_calibration_complete)
```

### Batch Notification Processing

```python
async def process_notification_queue(self):
    """Process queued notifications with batching and prioritization."""

    # Group notifications by priority
    critical_notifications = [n for n in self.notification_queue if n["level"] == "critical"]
    warning_notifications = [n for n in self.notification_queue if n["level"] == "warning"]
    info_notifications = [n for n in self.notification_queue if n["level"] == "info"]

    # Process in priority order
    for notification in critical_notifications + warning_notifications + info_notifications:
        if await self.check_rate_limit(notification["level"], notification["id"]):
            await self.send_notification(notification)
            self.notification_queue.remove(notification)

            # Small delay between notifications to prevent overwhelming
            await asyncio.sleep(0.1)
```

## Configuration and Management

### Notification Configuration Schema

```python
NOTIFICATION_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "default": True},
        "profile": {
            "type": "string",
            "enum": ["minimal", "standard", "comprehensive"],
            "default": "standard"
        },
        "channels": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["persistent", "push", "email", "webhook", "sms"]
            }
        },
        "quiet_hours": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": True},
                "start_time": {"type": "string", "pattern": "^\\d{2}:\\d{2}$", "default": "22:00"},
                "end_time": {"type": "string", "pattern": "^\\d{2}:\\d{2}$", "default": "08:00"}
            }
        },
        "custom_services": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["email", "webhook", "sms"]},
                    "config": {"type": "object"}
                }
            }
        }
    }
}
```

---

**Last Updated**: December 24, 2025
**Version**: 2.0 (Intelligent Notification Management)
**Status**: Production Ready
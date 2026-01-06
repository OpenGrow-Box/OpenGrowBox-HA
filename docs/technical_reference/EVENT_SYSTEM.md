# Event-Driven Architecture

## Overview

OpenGrowBox uses a comprehensive event-driven architecture to coordinate actions between sensors, devices, controllers, and external systems. This system enables real-time responses to environmental changes and provides a robust foundation for automation.

## Event System Architecture

### Core Event Components

```python
# Main event system components
event_system = {
    "event_manager": OGBEventManager,          # Central event coordinator
    "event_bus": AsyncEventBus,                # Async event distribution
    "event_handlers": {},                      # Registered event handlers
    "event_queue": asyncio.Queue,              # Event processing queue
    "event_history": EventHistory,             # Event logging and replay
    "event_filters": EventFilters              # Event filtering and routing
}
```

### Event Types and Categories

#### Sensor Events

Sensor events are triggered by environmental data changes:

```python
sensor_events = {
    "sensor_reading": {
        "type": "sensor.temperature",
        "data": {
            "sensor_id": "zone1_temp",
            "value": 24.5,
            "unit": "celsius",
            "timestamp": "2025-01-15T10:30:00Z",
            "quality": "good"
        },
        "metadata": {
            "zone": "zone1",
            "sensor_type": "temperature",
            "calibration_applied": true
        }
    },
    "sensor_threshold_breached": {
        "type": "sensor.threshold.exceeded",
        "data": {
            "sensor_id": "zone1_humidity",
            "value": 85.0,
            "threshold": 80.0,
            "direction": "above",
            "severity": "warning"
        }
    },
    "sensor_calibration_due": {
        "type": "sensor.maintenance.calibration_due",
        "data": {
            "sensor_id": "zone1_co2",
            "days_overdue": 15,
            "last_calibrated": "2024-12-01T00:00:00Z"
        }
    }
}
```

#### Device Events

Device events track hardware state changes and actions:

```python
device_events = {
    "device_state_changed": {
        "type": "device.state.changed",
        "data": {
            "device_id": "exhaust_fan_main",
            "old_state": "off",
            "new_state": "on",
            "trigger": "vpd_control",
            "power_level": 75
        }
    },
    "device_action_executed": {
        "type": "device.action.executed",
        "data": {
            "device_id": "humidifier_main",
            "action": "activate",
            "duration": 300,
            "reason": "humidity_below_target",
            "success": true
        }
    },
    "device_error": {
        "type": "device.error",
        "data": {
            "device_id": "irrigation_pump",
            "error_code": "E_PUMP_STALLED",
            "message": "Pump motor stalled during operation",
            "recovery_action": "emergency_stop"
        }
    }
}
```

#### Control Events

Control events manage automation and decision-making:

```python
control_events = {
    "control_mode_changed": {
        "type": "control.mode.changed",
        "data": {
            "old_mode": "VPD_Target",
            "new_mode": "VPD_Perfection",
            "trigger": "user_request",
            "transition_duration": 300
        }
    },
    "environmental_target_updated": {
        "type": "control.target.updated",
        "data": {
            "parameter": "vpd",
            "old_target": 1.2,
            "new_target": 1.4,
            "reason": "plant_stage_transition"
        }
    },
    "automation_paused": {
        "type": "control.automation.paused",
        "data": {
            "reason": "maintenance_mode",
            "estimated_duration": 3600,
            "affected_systems": ["climate", "irrigation"]
        }
    }
}
```

#### System Events

System-level events for monitoring and maintenance:

```python
system_events = {
    "system_startup": {
        "type": "system.lifecycle.started",
        "data": {
            "version": "2.1.0",
            "startup_time": "2025-01-15T08:00:00Z",
            "configuration_loaded": true,
            "devices_initialized": 12
        }
    },
    "system_health_check": {
        "type": "system.health.check",
        "data": {
            "overall_status": "healthy",
            "component_status": {
                "sensors": "healthy",
                "devices": "warning",  # One device offline
                "controllers": "healthy",
                "network": "healthy"
            },
            "next_check": "2025-01-15T08:30:00Z"
        }
    },
    "system_configuration_changed": {
        "type": "system.config.changed",
        "data": {
            "changes": [
                {"section": "plant_config", "parameter": "stage", "old": "veg", "new": "flower"},
                {"section": "environmental_limits", "parameter": "temperature_max", "old": 30, "new": 28}
            ],
            "applied_by": "user",
            "requires_restart": false
        }
    }
}
```

## Event Processing Pipeline

### Event Ingestion

Events enter the system through multiple channels:

```python
class OGBEventManager:
    """Central event management system."""

    def __init__(self):
        self.event_queue = asyncio.Queue(maxsize=1000)
        self.event_handlers = defaultdict(list)
        self.processing_tasks = set()
        self.event_filters = EventFilters()

    async def ingest_event(self, event: Event) -> None:
        """Ingest and queue event for processing."""
        # Apply input filtering
        if not await self.event_filters.should_process(event):
            return

        # Add to processing queue
        await self.event_queue.put(event)

        # Update event metrics
        self.metrics.record_event_ingested(event.type)

    async def process_events(self) -> None:
        """Main event processing loop."""
        while True:
            try:
                event = await self.event_queue.get()

                # Process event through pipeline
                await self._process_event_pipeline(event)

                self.event_queue.task_done()

            except Exception as e:
                _LOGGER.error(f"Event processing error: {e}")
                await self.handle_processing_error(event, e)
```

### Event Filtering and Routing

Events are filtered and routed based on rules:

```python
class EventFilters:
    """Event filtering and routing system."""

    def __init__(self):
        self.filters = {
            "priority_filter": self._priority_filter,
            "zone_filter": self._zone_filter,
            "type_filter": self._type_filter,
            "rate_limit_filter": self._rate_limit_filter
        }

    async def should_process(self, event: Event) -> bool:
        """Determine if event should be processed."""
        for filter_func in self.filters.values():
            if not await filter_func(event):
                return False
        return True

    async def _priority_filter(self, event: Event) -> bool:
        """Filter based on event priority."""
        critical_events = ["device.error", "system.emergency", "safety.violation"]
        if event.type in critical_events:
            return True  # Always process critical events

        # Rate limit non-critical events
        return self.rate_limiter.allow(event.type)

    async def _zone_filter(self, event: Event) -> bool:
        """Filter events by zone relevance."""
        event_zone = event.metadata.get("zone")
        if not event_zone:
            return True  # System-wide events

        # Check if zone is active and configured
        return event_zone in self.active_zones
```

### Event Handler Registration

Handlers register for specific event types:

```python
# Event handler registration system
event_handler_registry = {
    "sensor.temperature": [
        TemperatureController.handle_temperature_reading,
        VPDCalculator.update_temperature_data,
        AnalyticsCollector.record_temperature
    ],
    "device.state.changed": [
        StateTracker.update_device_state,
        ActionLogger.log_device_action,
        NotificationManager.check_alert_conditions
    ],
    "control.mode.changed": [
        ModeCoordinator.update_active_mode,
        DeviceManager.reconfigure_devices,
        UIManager.update_control_interface
    ]
}

# Handler registration method
async def register_event_handler(self, event_type: str, handler: Callable) -> None:
    """Register an event handler for a specific event type."""
    if event_type not in self.event_handlers:
        self.event_handlers[event_type] = []

    self.event_handlers[event_type].append(handler)
    _LOGGER.info(f"Registered handler {handler.__name__} for {event_type}")

# Bulk handler registration
async def register_handlers(self, handler_mappings: dict) -> None:
    """Register multiple event handlers at once."""
    for event_type, handlers in handler_mappings.items():
        for handler in handlers:
            await self.register_event_handler(event_type, handler)
```

### Event Processing Pipeline

Events flow through a multi-stage processing pipeline:

```python
async def _process_event_pipeline(self, event: Event) -> None:
    """Process event through the complete pipeline."""

    # Stage 1: Validation
    if not await self._validate_event(event):
        return

    # Stage 2: Enrichment
    enriched_event = await self._enrich_event(event)

    # Stage 3: Correlation
    correlated_event = await self._correlate_event(enriched_event)

    # Stage 4: Routing
    routing_decision = await self._route_event(correlated_event)

    # Stage 5: Handler Execution
    await self._execute_handlers(correlated_event, routing_decision)

    # Stage 6: Logging and Metrics
    await self._log_and_metrics(correlated_event)

async def _validate_event(self, event: Event) -> bool:
    """Validate event structure and data."""
    required_fields = ["type", "data", "timestamp"]
    for field in required_fields:
        if field not in event:
            _LOGGER.warning(f"Invalid event missing {field}: {event}")
            return False
    return True

async def _enrich_event(self, event: Event) -> Event:
    """Enrich event with additional context."""
    # Add system context
    event.metadata["system_version"] = self.system_version
    event.metadata["processing_node"] = self.node_id

    # Add environmental context
    if "zone" in event.metadata:
        zone_data = await self.zone_manager.get_zone_data(event.metadata["zone"])
        event.metadata["zone_context"] = zone_data

    return event

async def _correlate_event(self, event: Event) -> Event:
    """Correlate event with related events."""
    # Find related events in time window
    correlation_window = timedelta(seconds=30)
    related_events = await self.event_history.find_related_events(
        event, correlation_window
    )

    # Add correlation data
    event.metadata["correlated_events"] = [e.id for e in related_events]
    event.metadata["correlation_pattern"] = self._identify_pattern(related_events)

    return event
```

## Event-Driven Control Loops

### VPD Control Loop

The VPD control system uses events to maintain optimal conditions:

```python
class VPDController:
    """VPD control using event-driven architecture."""

    async def initialize(self):
        """Set up event handlers for VPD control."""
        handlers = {
            "sensor.temperature": [self.handle_temperature_update],
            "sensor.humidity": [self.handle_humidity_update],
            "control.target.vpd_updated": [self.handle_vpd_target_update],
            "control.mode.changed": [self.handle_mode_change]
        }

        for event_type, event_handlers in handlers.items():
            for handler in event_handlers:
                await self.event_manager.register_event_handler(event_type, handler)

    async def handle_temperature_update(self, event: Event) -> None:
        """Handle temperature sensor reading."""
        zone = event.metadata["zone"]
        temperature = event.data["value"]

        # Update VPD calculation
        await self.vpd_calculator.update_temperature(zone, temperature)

        # Trigger VPD recalculation
        await self.event_manager.fire_event({
            "type": "vpd.recalculation_required",
            "data": {"zone": zone, "trigger": "temperature_update"},
            "metadata": {"zone": zone}
        })

    async def handle_vpd_recalculation(self, event: Event) -> None:
        """Recalculate VPD and take action if needed."""
        zone = event.data["zone"]

        # Calculate current VPD
        current_vpd = await self.vpd_calculator.calculate_current_vpd(zone)
        target_vpd = await self.get_target_vpd(zone)

        # Check if action needed
        if abs(current_vpd - target_vpd) > self.tolerance:
            action = self.determine_vpd_action(current_vpd, target_vpd)

            # Fire action event
            await self.event_manager.fire_event({
                "type": "control.vpd.action_required",
                "data": {
                    "zone": zone,
                    "current_vpd": current_vpd,
                    "target_vpd": target_vpd,
                    "action": action
                },
                "metadata": {"zone": zone, "priority": "high"}
            })
```

### Device Action Coordination

Device actions are coordinated through events:

```python
class DeviceCoordinator:
    """Coordinate device actions using events."""

    async def handle_device_action_request(self, event: Event) -> None:
        """Handle request for device action."""
        device_id = event.data["device_id"]
        action = event.data["action"]

        # Check device availability
        if not await self.device_manager.is_device_available(device_id):
            await self.event_manager.fire_event({
                "type": "device.action.failed",
                "data": {
                    "device_id": device_id,
                    "action": action,
                    "reason": "device_unavailable"
                }
            })
            return

        # Check action dampening
        if await self.action_dampener.should_dampen(device_id, action):
            _LOGGER.info(f"Action dampened for {device_id}: {action}")
            return

        # Execute action
        success = await self.device_manager.execute_action(device_id, action)

        # Fire result event
        event_type = "device.action.success" if success else "device.action.failed"
        await self.event_manager.fire_event({
            "type": event_type,
            "data": {
                "device_id": device_id,
                "action": action,
                "result": "success" if success else "failed"
            }
        })
```

## Event Persistence and Replay

### Event History System

Events are persisted for analysis and replay:

```python
class EventHistory:
    """Event history and replay system."""

    async def store_event(self, event: Event) -> None:
        """Store event in persistent storage."""
        event_record = {
            "id": event.id,
            "type": event.type,
            "data": event.data,
            "metadata": event.metadata,
            "timestamp": event.timestamp,
            "processed_at": datetime.now(),
            "processing_duration": time.time() - event.created_at
        }

        await self.storage.store_event(event_record)

    async def replay_events(self, start_time: datetime, end_time: datetime,
                          event_types: list = None) -> list:
        """Replay events from history."""
        events = await self.storage.get_events(start_time, end_time, event_types)

        replayed_events = []
        for event_data in events:
            # Recreate event object
            event = Event.from_dict(event_data)

            # Process through normal pipeline
            await self.event_manager._process_event_pipeline(event)
            replayed_events.append(event)

        return replayed_events

    async def find_related_events(self, event: Event, time_window: timedelta) -> list:
        """Find events related to the given event."""
        start_time = event.timestamp - time_window
        end_time = event.timestamp + time_window

        # Find events in same zone or affecting same devices
        zone = event.metadata.get("zone")
        device_id = event.data.get("device_id")

        related_events = await self.storage.find_events(
            start_time, end_time,
            zone=zone,
            device_id=device_id,
            exclude_event_id=event.id
        )

        return related_events
```

### Event Analytics and Insights

Events are analyzed for system insights:

```python
class EventAnalytics:
    """Analyze event patterns for system insights."""

    async def analyze_event_patterns(self, time_window: timedelta) -> dict:
        """Analyze event patterns for insights."""
        events = await self.event_history.get_recent_events(time_window)

        analysis = {
            "event_frequency": self._calculate_event_frequency(events),
            "failure_patterns": self._identify_failure_patterns(events),
            "performance_metrics": self._calculate_performance_metrics(events),
            "anomaly_detection": self._detect_anomalies(events)
        }

        return analysis

    def _calculate_event_frequency(self, events: list) -> dict:
        """Calculate event frequency by type."""
        frequency = defaultdict(int)
        for event in events:
            frequency[event.type] += 1

        # Calculate rates per hour
        hours = len(events) / 3600 if events else 1
        return {event_type: count / hours for event_type, count in frequency.items()}

    def _identify_failure_patterns(self, events: list) -> list:
        """Identify patterns in failure events."""
        failure_events = [e for e in events if "error" in e.type or "failed" in e.type]

        patterns = []
        # Group by device, time, error type
        device_failures = defaultdict(list)
        for event in failure_events:
            device_id = event.data.get("device_id", "system")
            device_failures[device_id].append(event)

        # Find devices with multiple failures
        for device_id, device_events in device_failures.items():
            if len(device_events) >= 3:  # Multiple failures
                patterns.append({
                    "device_id": device_id,
                    "failure_count": len(device_events),
                    "time_span": self._calculate_time_span(device_events),
                    "pattern": "repeated_failures"
                })

        return patterns
```

## Event Monitoring and Alerting

### Event-Based Alerting

Alerts are triggered by event patterns:

```python
class EventAlertManager:
    """Manage alerts based on event patterns."""

    def __init__(self):
        self.alert_rules = {
            "device_failure_burst": {
                "condition": "device.error events > 5 in 10 minutes",
                "severity": "critical",
                "action": "emergency_stop"
            },
            "sensor_calibration_overdue": {
                "condition": "sensor.calibration_due > 30 days",
                "severity": "warning",
                "action": "notification"
            },
            "vpd_out_of_range": {
                "condition": "vpd deviation > 20% for > 15 minutes",
                "severity": "warning",
                "action": "notification"
            }
        }

    async def check_alert_conditions(self, event: Event) -> None:
        """Check if event triggers any alerts."""
        for rule_name, rule in self.alert_rules.items():
            if await self._evaluate_rule(rule, event):
                await self._trigger_alert(rule_name, rule, event)

    async def _evaluate_rule(self, rule: dict, event: Event) -> bool:
        """Evaluate if rule condition is met."""
        condition = rule["condition"]

        if "device.error" in condition and event.type == "device.error":
            # Check for burst pattern
            recent_errors = await self.event_history.count_events(
                event_type="device.error",
                time_window=timedelta(minutes=10)
            )
            return recent_errors > 5

        elif "calibration_due" in condition and "calibration_due" in event.type:
            days_overdue = event.data.get("days_overdue", 0)
            return days_overdue > 30

        elif "vpd deviation" in condition and event.type == "sensor.vpd":
            # Check VPD stability
            return await self._check_vpd_stability(event)

        return False

    async def _trigger_alert(self, rule_name: str, rule: dict, event: Event) -> None:
        """Trigger alert for matched rule."""
        alert = {
            "rule": rule_name,
            "severity": rule["severity"],
            "message": f"Alert triggered: {rule['condition']}",
            "event": event,
            "timestamp": datetime.now(),
            "action_taken": rule["action"]
        }

        # Execute alert action
        await self._execute_alert_action(rule["action"], alert)

        # Store alert
        await self.storage.store_alert(alert)
```

### Event System Health Monitoring

Monitor the health of the event system itself:

```python
class EventSystemHealth:
    """Monitor health of the event system."""

    async def perform_health_check(self) -> dict:
        """Perform comprehensive health check."""
        health_status = {
            "queue_health": await self._check_queue_health(),
            "handler_health": await self._check_handler_health(),
            "storage_health": await self._check_storage_health(),
            "performance_metrics": await self._get_performance_metrics()
        }

        # Overall health determination
        health_status["overall_health"] = self._determine_overall_health(health_status)

        return health_status

    async def _check_queue_health(self) -> dict:
        """Check event queue health."""
        queue_size = self.event_manager.event_queue.qsize()
        max_queue_size = getattr(self.event_manager.event_queue, '_maxsize', 1000)

        return {
            "queue_size": queue_size,
            "queue_utilization": queue_size / max_queue_size,
            "healthy": queue_size < max_queue_size * 0.8
        }

    async def _check_handler_health(self) -> dict:
        """Check event handler health."""
        handler_status = {}
        for event_type, handlers in self.event_manager.event_handlers.items():
            handler_status[event_type] = {
                "handler_count": len(handlers),
                "all_handlers_registered": len(handlers) > 0
            }

        return {
            "handler_status": handler_status,
            "total_handlers": sum(len(h) for h in self.event_manager.event_handlers.values()),
            "healthy": all(status["all_handlers_registered"] for status in handler_status.values())
        }
```

---

## Event System Summary

**Event-driven architecture implemented!** The system provides robust coordination between all OpenGrowBox components.

**Key Features:**
- ✅ Asynchronous event processing with queue management
- ✅ Event filtering, routing, and correlation
- ✅ Comprehensive event types for sensors, devices, and controls
- ✅ Event persistence and replay capabilities
- ✅ Real-time analytics and pattern detection
- ✅ Event-based alerting and monitoring
- ✅ Health monitoring and diagnostics

**Event Processing Pipeline:**
1. **Ingestion** → Event validation and queuing
2. **Filtering** → Priority and relevance filtering
3. **Enrichment** → Context and correlation data addition
4. **Routing** → Handler selection and distribution
5. **Execution** → Asynchronous handler processing
6. **Logging** → Metrics and history recording

**For API reference details, see [API Reference](API_REFERENCE.md)**

**For data models used in events, see [Data Models](DATA_MODELS.md)**</content>
<parameter name="filePath">docs/technical_reference/EVENT_SYSTEM.md
"""
OGB AI Data Bridge - Collects cropsteering data and sends to ogb-grow-api for AI learning

This module bridges the cropsteering execution in HA backend with the AI learning
system in the ogb-grow-api. It:
- Listens to cropsteering events (phase transitions, irrigations, sensor readings)
- Batches and sends data to the API via WebSocket
- Receives optimized parameters from the AI system
"""

import asyncio
import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


@dataclass
class CropSteeringEvent:
    """Base class for cropsteering events to send to AI"""

    event_type: str
    timestamp: float
    room: str
    medium_type: str
    data: Dict[str, Any]


class OGBAIDataBridge:
    """Bridge between HA cropsteering and API AI learning system"""

    def __init__(
        self, hass, eventManager, dataStore, room: str, websocket_manager=None
    ):
        self.hass = hass
        self.event_manager = eventManager
        self.data_store = dataStore
        self.room = room
        self.websocket_manager = websocket_manager

        # Event buffering
        self.event_buffer: deque = deque(maxlen=1000)
        self.batch_size = 10
        self.flush_interval = 60  # seconds

        # State tracking
        self.current_phase = "p0"
        self.last_irrigation_time = None
        self.cycle_start_time = None
        self.daily_irrigation_count = 0
        self.last_vwc = None
        self.last_ec = None

        # Sensor reading buffer for averaging
        self.sensor_buffer: deque = deque(maxlen=10)

        # AI recommendations (received from API)
        self.ai_recommendations: Dict[str, Any] = {}
        self.last_optimization_time = None

        # Background task
        self._flush_task = None
        self._is_enabled = False

        _LOGGER.info(f"{self.room} - AI Data Bridge initialized")

    async def start(self):
        """Start the AI data bridge"""
        if self._is_enabled:
            return

        self._is_enabled = True

        # Subscribe to cropsteering events
        self.event_manager.on("CSPhaseChange", self._on_phase_change)
        self.event_manager.on("CSIrrigation", self._on_irrigation)
        self.event_manager.on("CSSensorUpdate", self._on_sensor_update)
        self.event_manager.on("CSDrybackComplete", self._on_dryback_complete)
        self.event_manager.on("CSPerformanceMetric", self._on_performance_metric)

        # Start periodic flush task
        self._flush_task = asyncio.create_task(self._periodic_flush())

        _LOGGER.info(f"{self.room} - AI Data Bridge started")

    async def stop(self):
        """Stop the AI data bridge"""
        self._is_enabled = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Flush remaining events
        await self._flush_buffer()

        _LOGGER.info(f"{self.room} - AI Data Bridge stopped")

    # ==================== EVENT HANDLERS ====================

    async def _on_phase_change(self, data: Dict[str, Any]):
        """Handle phase transition events"""
        if data.get("room") != self.room:
            return

        from_phase = data.get("from_phase", self.current_phase)
        to_phase = data.get("to_phase", "p0")

        event = CropSteeringEvent(
            event_type="phase_transition",
            timestamp=datetime.now().timestamp() * 1000,
            room=self.room,
            medium_type=self._get_medium_type(),
            data={
                "fromPhase": from_phase,
                "toPhase": to_phase,
                "trigger": data.get("trigger", "unknown"),
                "vwc": data.get("vwc"),
                "ec": data.get("ec"),
                "poreEC": data.get("pore_ec"),
                "temperature": data.get("temperature"),
                "vwcMin": data.get("vwc_min"),
                "vwcMax": data.get("vwc_max"),
                "ecTarget": data.get("ec_target"),
                "lightStatus": data.get("light_status"),
                "irrigationCount": self.daily_irrigation_count,
            },
        )

        self.event_buffer.append(event)
        self.current_phase = to_phase

        # Reset cycle tracking on P0 start
        if to_phase == "p0":
            self.cycle_start_time = datetime.now().timestamp() * 1000
            self.daily_irrigation_count = 0

        _LOGGER.debug(
            f"{self.room} - AI logged phase transition: {from_phase} -> {to_phase}"
        )

    async def _on_irrigation(self, data: Dict[str, Any]):
        """Handle irrigation events"""
        if data.get("room") != self.room:
            return

        now = datetime.now().timestamp() * 1000
        time_since_last = None
        if self.last_irrigation_time:
            time_since_last = now - self.last_irrigation_time

        event = CropSteeringEvent(
            event_type="irrigation",
            timestamp=now,
            room=self.room,
            medium_type=self._get_medium_type(),
            data={
                "phase": self.current_phase,
                "shotNumber": data.get("shot_number", self.daily_irrigation_count + 1),
                "duration": data.get("duration", 0),
                "volume": data.get("volume"),
                "preVWC": data.get("pre_vwc", self.last_vwc),
                "preEC": data.get("pre_ec", self.last_ec),
                "prePoreEC": data.get("pre_pore_ec"),
                "preTemperature": data.get("pre_temperature"),
                "interval": data.get("interval"),
                "targetVWC": data.get("target_vwc"),
                "maxShots": data.get("max_shots"),
                "isEmergency": data.get("is_emergency", False),
                "timeSinceLastIrrigation": time_since_last,
            },
        )

        self.event_buffer.append(event)
        self.last_irrigation_time = now
        self.daily_irrigation_count += 1

        _LOGGER.debug(
            f"{self.room} - AI logged irrigation: phase={self.current_phase}, shot={self.daily_irrigation_count}"
        )

    async def _on_sensor_update(self, data: Dict[str, Any]):
        """Handle sensor reading updates"""
        if data.get("room") != self.room:
            return

        # Update last known values
        self.last_vwc = data.get("vwc", self.last_vwc)
        self.last_ec = data.get("ec", self.last_ec)

        # Add to sensor buffer
        self.sensor_buffer.append(
            {
                "timestamp": datetime.now().timestamp() * 1000,
                "vwc": data.get("vwc"),
                "ec": data.get("ec"),
                "poreEC": data.get("pore_ec"),
                "temperature": data.get("temperature"),
                "soilTemp": data.get("soil_temp"),
            }
        )

        # Create event (batched - only every 5th reading)
        if len(self.sensor_buffer) % 5 == 0:
            time_since_irrigation = None
            if self.last_irrigation_time:
                time_since_irrigation = (
                    datetime.now().timestamp() * 1000 - self.last_irrigation_time
                )

            event = CropSteeringEvent(
                event_type="sensor_reading",
                timestamp=datetime.now().timestamp() * 1000,
                room=self.room,
                medium_type=self._get_medium_type(),
                data={
                    "phase": self.current_phase,
                    "vwc": data.get("vwc"),
                    "vwcRaw": data.get("vwc_raw"),
                    "ec": data.get("ec"),
                    "ecRaw": data.get("ec_raw"),
                    "poreEC": data.get("pore_ec"),
                    "temperature": data.get("temperature"),
                    "soilTemp": data.get("soil_temp"),
                    "vwcMin": data.get("vwc_min"),
                    "vwcMax": data.get("vwc_max"),
                    "ecTarget": data.get("ec_target"),
                    "airTemp": data.get("air_temp"),
                    "humidity": data.get("humidity"),
                    "vpd": data.get("vpd"),
                    "lightIntensity": data.get("light_intensity"),
                    "lightStatus": data.get("light_status"),
                    "timeSinceIrrigation": time_since_irrigation,
                },
            )

            self.event_buffer.append(event)

    async def _on_dryback_complete(self, data: Dict[str, Any]):
        """Handle dryback cycle completion"""
        if data.get("room") != self.room:
            return

        event = CropSteeringEvent(
            event_type="dryback_cycle",
            timestamp=datetime.now().timestamp() * 1000,
            room=self.room,
            medium_type=self._get_medium_type(),
            data={
                "startTime": data.get("start_time"),
                "endTime": data.get("end_time"),
                "duration": data.get("duration"),
                "vwcStart": data.get("vwc_start"),
                "vwcEnd": data.get("vwc_end"),
                "vwcMin": data.get("vwc_min"),
                "vwcMax": data.get("vwc_max"),
                "irrigationCount": data.get(
                    "irrigation_count", self.daily_irrigation_count
                ),
                "totalWaterVolume": data.get("total_water_volume"),
                "phaseDistribution": data.get("phase_distribution"),
                "avgAirTemp": data.get("avg_air_temp"),
                "avgHumidity": data.get("avg_humidity"),
                "avgVPD": data.get("avg_vpd"),
                "avgLightIntensity": data.get("avg_light_intensity"),
            },
        )

        self.event_buffer.append(event)

        _LOGGER.info(f"{self.room} - AI logged dryback cycle complete")

    async def _on_performance_metric(self, data: Dict[str, Any]):
        """Handle performance metric updates"""
        if data.get("room") != self.room:
            return

        event = CropSteeringEvent(
            event_type="performance",
            timestamp=datetime.now().timestamp() * 1000,
            room=self.room,
            medium_type=self._get_medium_type(),
            data={
                "phase": self.current_phase,
                "vwcAccuracy": data.get("vwc_accuracy"),
                "vwcStability": data.get("vwc_stability"),
                "ecAccuracy": data.get("ec_accuracy"),
                "ecStability": data.get("ec_stability"),
                "irrigationEfficiency": data.get("irrigation_efficiency"),
                "overshootCount": data.get("overshoot_count"),
                "undershootCount": data.get("undershoot_count"),
                "phaseTimingAccuracy": data.get("phase_timing_accuracy"),
                "plantStress": data.get("plant_stress"),
            },
        )

        self.event_buffer.append(event)

    # ==================== DATA TRANSMISSION ====================

    async def _periodic_flush(self):
        """Periodically flush buffered events to API"""
        while self._is_enabled:
            try:
                await asyncio.sleep(self.flush_interval)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.error(f"{self.room} - Error in periodic flush: {e}")

    async def _flush_buffer(self):
        """Flush buffered events to the API"""
        if not self.event_buffer:
            return

        # Convert events to dictionaries
        events_to_send = []
        while self.event_buffer and len(events_to_send) < 100:
            event = self.event_buffer.popleft()
            events_to_send.append(asdict(event))

        if not events_to_send:
            return

        try:
            await self._send_to_api(
                {
                    "type": "cropsteering_events",
                    "room": self.room,
                    "events": events_to_send,
                    "timestamp": datetime.now().timestamp() * 1000,
                }
            )

            _LOGGER.debug(
                f"{self.room} - Flushed {len(events_to_send)} AI events to API"
            )

        except Exception as e:
            _LOGGER.error(f"{self.room} - Failed to send AI events to API: {e}")
            # Re-add events to buffer (at front) for retry
            for event_dict in reversed(events_to_send):
                event = CropSteeringEvent(**event_dict)
                self.event_buffer.appendleft(event)

    async def _send_to_api(self, data: Dict[str, Any]):
        """Send data to the ogb-grow-api via WebSocket"""
        if self.websocket_manager:
            try:
                await self.websocket_manager.emit("ai_cropsteering_data", data)
            except Exception as e:
                _LOGGER.warning(f"{self.room} - WebSocket send failed: {e}")
        else:
            # Emit to eventManager for alternative handling
            await self.event_manager.emit("AICropSteeringData", data)

    # ==================== AI RECOMMENDATIONS ====================

    async def receive_ai_recommendations(self, recommendations: Dict[str, Any]):
        """
        Receive AI-optimized parameters from the API.
        These can be applied to the cropsteering manager.
        """
        self.ai_recommendations = recommendations
        self.last_optimization_time = datetime.now().timestamp() * 1000

        _LOGGER.info(
            f"{self.room} - Received AI recommendations: {list(recommendations.keys())}"
        )

        # Emit event for cropsteering manager to pick up
        await self.event_manager.emit(
            "AIRecommendations",
            {
                "room": self.room,
                "recommendations": recommendations,
                "timestamp": self.last_optimization_time,
            },
        )

    def get_ai_recommendations(self) -> Dict[str, Any]:
        """Get current AI recommendations"""
        return self.ai_recommendations

    def get_recommended_parameter(self, param_name: str, default: Any = None) -> Any:
        """Get a specific AI-recommended parameter"""
        return self.ai_recommendations.get(param_name, default)

    # ==================== HELPERS ====================

    def _get_medium_type(self) -> str:
        """Get current medium type from dataStore"""
        try:
            medium_type = self.data_store.getDeep("CropSteering.MediumType")
            if medium_type:
                return medium_type.lower()

            grow_mediums = self.data_store.get("growMediums") or []
            if grow_mediums and len(grow_mediums) > 0:
                first_medium = grow_mediums[0]
                if hasattr(first_medium, "medium_type"):
                    return first_medium.medium_type.value.lower()
                elif isinstance(first_medium, dict) and "type" in first_medium:
                    return first_medium["type"].lower()
        except Exception:
            pass

        return "rockwool"

    def log_irrigation_complete(
        self, post_vwc: float, post_ec: float, post_pore_ec: Optional[float] = None
    ):
        """
        Call this after irrigation settling to update the last irrigation event
        with post-irrigation sensor data.
        """
        if not self.event_buffer:
            return

        # Find the most recent irrigation event and update it
        for i in range(len(self.event_buffer) - 1, -1, -1):
            event = self.event_buffer[i]
            if event.event_type == "irrigation":
                event.data["postVWC"] = post_vwc
                event.data["postEC"] = post_ec
                event.data["postPoreEC"] = post_pore_ec
                event.data["settlingTime"] = (
                    datetime.now().timestamp() * 1000 - event.timestamp
                )
                event.data["vwcIncrease"] = post_vwc - (event.data.get("preVWC") or 0)
                event.data["ecChange"] = post_ec - (event.data.get("preEC") or 0)
                break

    def get_status(self) -> Dict[str, Any]:
        """Get current status of the AI data bridge"""
        return {
            "enabled": self._is_enabled,
            "room": self.room,
            "current_phase": self.current_phase,
            "events_buffered": len(self.event_buffer),
            "daily_irrigation_count": self.daily_irrigation_count,
            "last_irrigation_time": self.last_irrigation_time,
            "last_vwc": self.last_vwc,
            "last_ec": self.last_ec,
            "has_ai_recommendations": bool(self.ai_recommendations),
            "last_optimization_time": self.last_optimization_time,
        }

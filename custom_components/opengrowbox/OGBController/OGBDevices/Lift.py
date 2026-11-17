from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from .Device import Device

_LOGGER = logging.getLogger(__name__)


class Lift(Device):
    """Plant lift actuator (lamp height above the canopy).

    Position is expressed in percent:
        0   -> lamp fully lowered (closest to the canopy)
        100 -> lamp fully raised (maximum distance to the canopy)

    "Increase Lift" raises the lamp, "Reduce Lift" lowers it.

    Movement is *closed loop* against the plant distance sensor
    (``tentData.plantDistance``): the lift moves one step, waits, reads the
    distance and decides whether to continue or stop. This makes an exact
    ``mm/s`` calibration unnecessary - the sensor is the ground truth - while
    still enforcing safety limits (``minDistanceCM``, ``maxRuntimeSec``).

    All parameters are read from the device's own ``DeviceMinMax.<type>``
    namespace and fall back to the class defaults, so no shared MinMax /
    configuration code is touched.

    Status: beta / testable - the closed-loop behaviour against the plant
    distance sensor needs field validation before unattended use.
    """

    DEFAULT_MIN_POSITION = 0.0
    DEFAULT_MAX_POSITION = 100.0
    DEFAULT_STEP = 5.0

    # Closed-loop / safety defaults
    DEFAULT_SETTLE_TIME = 2.0
    DEFAULT_PULSE_DURATION = 1.0
    DEFAULT_TOLERANCE_CM = 1.0
    DEFAULT_MIN_DISTANCE_CM = 0.0
    DEFAULT_MAX_DISTANCE_CM = 0.0
    DEFAULT_FULL_TRAVEL_TIME = 30.0
    DEFAULT_MAX_RUNTIME = 60.0
    DEFAULT_MAX_ITERATIONS = 20
    DEFAULT_NO_PROGRESS_LIMIT = 3
    DEFAULT_SENSOR_MAX_AGE = 0.0  # 0 disables the staleness check

    _UP_KEYWORDS = ("up", "raise", "increase", "auf", "hoch", "hoher")
    _DOWN_KEYWORDS = ("down", "lower", "reduce", "close", "ab", "zu", "runter", "tiefer")

    def __init__(
        self,
        deviceName,
        deviceData,
        eventManager,
        dataStore,
        deviceType,
        inRoom,
        hass,
        deviceLabel="EMPTY",
        allLabels=[],
    ):
        # Closed-loop state must exist before deviceInit() runs identifyIfRunningState().
        self.position = None
        self.minPosition = self.DEFAULT_MIN_POSITION
        self.maxPosition = self.DEFAULT_MAX_POSITION
        self.positionStep = self.DEFAULT_STEP
        self.settleTimeSec = self.DEFAULT_SETTLE_TIME
        self.pulseDurationSec = self.DEFAULT_PULSE_DURATION
        self.toleranceCM = self.DEFAULT_TOLERANCE_CM
        self.minDistanceCM = self.DEFAULT_MIN_DISTANCE_CM
        self.maxDistanceCM = self.DEFAULT_MAX_DISTANCE_CM
        self.fullTravelTimeSec = self.DEFAULT_FULL_TRAVEL_TIME
        self.maxRuntimeSec = self.DEFAULT_MAX_RUNTIME
        self.maxIterations = self.DEFAULT_MAX_ITERATIONS
        self.noProgressLimit = self.DEFAULT_NO_PROGRESS_LIMIT
        self.sensorMaxAgeSec = self.DEFAULT_SENSOR_MAX_AGE
        self._move_in_progress = False

        super().__init__(
            deviceName,
            deviceData,
            eventManager,
            dataStore,
            deviceType,
            inRoom,
            hass,
            deviceLabel,
            allLabels,
        )

        self._load_position_config()
        self._load_closed_loop_config()
        self.position = self._read_current_position()

        self.event_manager.on("Increase Lift", self.increaseAction)
        self.event_manager.on("Reduce Lift", self.reduceAction)
        self.event_manager.on("Set Lift", self.setPositionAction)

        _LOGGER.debug(f"{self.deviceName}: Lift device initialized (position={self.position})")

    # ------------------------------------------------------------------ config
    def _load_position_config(self) -> None:
        """Load position bounds and step size from DeviceMinMax.<type>."""
        minMax = self._get_device_minmax()
        if minMax is None:
            return

        raw_min = minMax.get("minPosition", minMax.get("minDuty"))
        raw_max = minMax.get("maxPosition", minMax.get("maxDuty"))
        raw_step = minMax.get("positionStep", minMax.get("step"))

        try:
            if raw_min is not None:
                self.minPosition = float(raw_min)
            if raw_max is not None:
                self.maxPosition = float(raw_max)
            if raw_step is not None:
                self.positionStep = abs(float(raw_step))
        except (TypeError, ValueError):
            _LOGGER.warning(f"{self.deviceName}: Invalid Lift position config, using defaults")

        if self.maxPosition < self.minPosition:
            self.minPosition, self.maxPosition = self.maxPosition, self.minPosition

    def _load_closed_loop_config(self) -> None:
        """Load closed-loop / safety parameters from DeviceMinMax.<type>."""
        minMax = self._get_device_minmax()
        if minMax is None:
            return

        def _num(key, default, minimum=None):
            raw = minMax.get(key)
            if raw is None:
                return default
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return default
            if minimum is not None and value < minimum:
                return default
            return value

        self.settleTimeSec = _num("settleTimeSec", self.DEFAULT_SETTLE_TIME, 0.0)
        self.pulseDurationSec = _num("pulseDurationSec", self.DEFAULT_PULSE_DURATION, 0.0)
        self.toleranceCM = _num("toleranceCM", self.DEFAULT_TOLERANCE_CM, 0.0)
        self.minDistanceCM = _num("minDistanceCM", self.DEFAULT_MIN_DISTANCE_CM, 0.0)
        self.maxDistanceCM = _num("maxDistanceCM", self.DEFAULT_MAX_DISTANCE_CM, 0.0)
        self.fullTravelTimeSec = _num("fullTravelTimeSec", self.DEFAULT_FULL_TRAVEL_TIME, 0.0)
        self.maxRuntimeSec = _num("maxRuntimeSec", self.DEFAULT_MAX_RUNTIME, 0.0)
        self.maxIterations = int(_num("maxIterations", self.DEFAULT_MAX_ITERATIONS, 1.0))
        self.noProgressLimit = int(_num("noProgressLimit", self.DEFAULT_NO_PROGRESS_LIMIT, 1.0))
        self.sensorMaxAgeSec = _num("sensorMaxAgeSec", self.DEFAULT_SENSOR_MAX_AGE, 0.0)

    def _get_device_minmax(self):
        try:
            minMax = self.dataStore.getDeep(f"DeviceMinMax.{self.deviceType}")
        except AttributeError:
            return None
        return minMax if isinstance(minMax, dict) else None

    def _refresh_config(self) -> None:
        """Re-read DeviceMinMax.<type> so live changes apply without a restart.

        The console ``lift`` command writes to ``DeviceMinMax.Lift``; reloading
        here picks the new values up on the next action.
        """
        self._load_position_config()
        self._load_closed_loop_config()

    # ----------------------------------------------------------------- actuator
    def _resolve_actuator(self):
        """Return (kind, ref) of the lift actuator, or (None, None).

        kind is one of "cover", "number" or "switch". For "cover"/"number" ref
        is the entity_id; for "switch" ref is {"up": entity_id, "down": entity_id}.
        """
        for entity in self.switches or []:
            entity_id = entity.get("entity_id", "")
            if entity_id.startswith("cover."):
                return "cover", entity_id

        for source in (self.options, self.switches):
            for entity in source or []:
                entity_id = entity.get("entity_id", "")
                if entity_id.startswith("number."):
                    return "number", entity_id

        relays = self._resolve_relays()
        if relays:
            return "switch", relays

        return None, None

    def _resolve_relays(self):
        """Find the up/down relay pair (e.g. two switches), or None."""
        candidates = []
        for source in (self.switches, self.options):
            for entity in source or []:
                entity_id = entity.get("entity_id", "")
                if entity_id.startswith(("switch.", "light.")):
                    candidates.append(entity_id)

        if len(candidates) < 2:
            return None

        up = down = None
        for entity_id in candidates:
            low = entity_id.lower()
            if up is None and any(k in low for k in self._UP_KEYWORDS):
                up = entity_id
            elif down is None and any(k in low for k in self._DOWN_KEYWORDS):
                down = entity_id

        if up is None and down is None:
            up, down = candidates[0], candidates[1]

        if not up or not down or up == down:
            return None
        return {"up": up, "down": down}

    def _clamp_position(self, value) -> float:
        """Limit a position value to the configured bounds."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            _LOGGER.warning(f"{self.deviceName}: Invalid Lift position '{value}', using minimum")
            return self.minPosition
        return max(self.minPosition, min(self.maxPosition, value))

    def _read_current_position(self):
        """Read the live lift position from HA, or None when unknown."""
        kind, entity_id = self._resolve_actuator()
        if kind not in ("cover", "number") or not entity_id or not self.hass:
            return None

        state = self.hass.states.get(entity_id)
        if state is None:
            return None

        if kind == "cover":
            raw = state.attributes.get("current_position")
        else:
            raw = state.state

        if raw is None:
            return None

        try:
            return self._clamp_position(float(raw))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ sensor
    def _read_plant_distance_cm(self):
        """Return the latest plant/lamp distance in centimetres, or None."""
        try:
            raw = self.dataStore.getDeep("tentData.plantDistance")
        except AttributeError:
            return None
        if raw is None:
            return None

        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None

        try:
            unit = str(self.dataStore.getDeep("tentData.plantDistanceUnit") or "").lower()
        except AttributeError:
            unit = ""

        if unit in ("m", "meter", "meters", "metre", "metres"):
            value *= 100.0
        return value

    def _plant_distance_is_stale(self) -> bool:
        """True when a staleness check is enabled and the reading is too old."""
        if self.sensorMaxAgeSec <= 0:
            return False
        try:
            raw = self.dataStore.getDeep("tentData.plantDistanceLastUpdate")
        except AttributeError:
            return True
        if not raw:
            return True
        try:
            stamp = datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        return (datetime.now() - stamp).total_seconds() > self.sensorMaxAgeSec

    def _lowering_is_safe(self) -> bool:
        """Refuse to lower when the lamp is already at the minimum distance."""
        if self.minDistanceCM <= 0:
            return True
        distance = self._read_plant_distance_cm()
        if distance is None:
            return True
        if distance <= self.minDistanceCM:
            _LOGGER.warning(
                f"{self.deviceName}: Plant distance {distance:.1f}cm at/below "
                f"minimum {self.minDistanceCM:.1f}cm - refusing to lower"
            )
            return False
        return True

    def _raising_is_safe(self) -> bool:
        """Refuse to raise once the lamp is at/above the maximum distance.

        The plant distance sensor always reports a value in small tents, so the
        upper bound is derived from the measured distance. ``None`` is *not*
        treated as "too high" - it only means the sensor has no reading (e.g.
        during startup), so the mechanical cover endstop remains the guard.
        """
        if self.maxDistanceCM <= 0:
            return True
        distance = self._read_plant_distance_cm()
        if distance is None:
            _LOGGER.debug(
                f"{self.deviceName}: No plant distance reading - allowing raise "
                f"(mechanical endstop guards the top)"
            )
            return True
        if distance >= self.maxDistanceCM:
            _LOGGER.warning(
                f"{self.deviceName}: Plant distance {distance:.1f}cm at/above "
                f"maximum {self.maxDistanceCM:.1f}cm - refusing to raise"
            )
            return False
        return True

    def _clamp_distance_target(self, target: float) -> float:
        """Clamp a distance target into the configured [min, max] window."""
        if self.minDistanceCM > 0 and target < self.minDistanceCM:
            _LOGGER.warning(
                f"{self.deviceName}: Target {target:.1f}cm below minimum "
                f"{self.minDistanceCM:.1f}cm - clamping"
            )
            target = self.minDistanceCM
        if self.maxDistanceCM > 0 and target > self.maxDistanceCM:
            _LOGGER.warning(
                f"{self.deviceName}: Target {target:.1f}cm above maximum "
                f"{self.maxDistanceCM:.1f}cm - clamping"
            )
            target = self.maxDistanceCM
        return target

    # ------------------------------------------------------------------ moving
    def _set_control_lock(self, duration: float) -> None:
        self._control_lock_until = time.time() + max(0.0, duration)

    async def _pulse_relay(self, entity_id) -> bool:
        """Drive a relay for one pulse (relay lifts have no position feedback)."""
        if not self.hass:
            return False
        if not self._is_device_online():
            _LOGGER.warning(f"{self.deviceName}: Lift offline - skipping relay pulse")
            return False

        domain = entity_id.split(".")[0]
        try:
            await self.hass.services.async_call(
                domain, "turn_on", {"entity_id": entity_id}
            )
            self._commanded_state = "on"
            self._set_control_lock(self.pulseDurationSec + 1.0)
            await asyncio.sleep(self.pulseDurationSec)
            await self.hass.services.async_call(
                domain, "turn_off", {"entity_id": entity_id}
            )
            self._commanded_state = "off"
            return True
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to pulse lift relay {entity_id}: {e}")
            return False

    async def set_position(self, value) -> bool:
        """Move the lift to an absolute position (percent), clamped to bounds."""
        if not self.hass:
            return False

        target = self._clamp_position(value)
        kind, entity_id = self._resolve_actuator()

        if kind not in ("cover", "number") or not entity_id:
            _LOGGER.warning(f"{self.deviceName}: No cover/number entity found - cannot move lift")
            return False

        if not self._is_device_online():
            _LOGGER.warning(f"{self.deviceName}: Lift offline - skipping move to {target}%")
            return False

        current = self.position if self.position is not None else self._read_current_position()
        if current is not None and abs(current - target) < 0.5:
            _LOGGER.debug(f"{self.deviceName}: Already at {current}%, skipping move to {target}%")
            return True

        self._commanded_state = "on"
        self._set_control_lock(5.0)

        try:
            if kind == "cover":
                await self.hass.services.async_call(
                    domain="cover",
                    service="set_cover_position",
                    service_data={"entity_id": entity_id, "position": int(round(target))},
                )
            else:
                await self.hass.services.async_call(
                    domain="number",
                    service="set_value",
                    service_data={"entity_id": entity_id, "value": target},
                )
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to set lift position {target}%: {e}")
            return False

        self.position = target
        _LOGGER.debug(f"{self.deviceName}: Lift position set to {target}%")
        return True

    async def _nudge(self, direction: int) -> bool:
        """Perform a single movement step in the given direction (+1 up, -1 down)."""
        kind, ref = self._resolve_actuator()

        if kind in ("cover", "number"):
            base = self.position if self.position is not None else self._read_current_position()
            if base is None:
                base = self.maxPosition if direction < 0 else self.minPosition
            return await self.set_position(base + direction * self.positionStep)

        if kind == "switch":
            relay = ref.get("up") if direction > 0 else ref.get("down")
            if not relay:
                _LOGGER.warning(f"{self.deviceName}: No relay for direction {direction}")
                return False
            return await self._pulse_relay(relay)

        _LOGGER.warning(f"{self.deviceName}: No lift actuator found - cannot move")
        return False

    def _estimate_lock(self, direction: int) -> float:
        """Estimated time (s) a single step needs before the sensor settles."""
        travel = 0.0
        if self.fullTravelTimeSec > 0 and self.maxPosition > self.minPosition:
            travel = (self.positionStep / 100.0) * self.fullTravelTimeSec
        return max(5.0, travel + self.settleTimeSec + 1.0)

    async def move_to_distance(self, target_cm) -> None:
        """Closed-loop move: step towards a target distance, checking the sensor.

        Stops when the measured distance is within ``toleranceCM`` of the target,
        when ``minDistanceCM`` / ``maxDistanceCM`` would be crossed, when
        ``maxRuntimeSec`` / ``maxIterations`` are exceeded, when the sensor is
        lost, or when the distance stops changing (``noProgressLimit``).
        """
        if self._move_in_progress:
            _LOGGER.debug(f"{self.deviceName}: Lift move already in progress - ignoring")
            return

        self._refresh_config()

        try:
            target = float(target_cm)
        except (TypeError, ValueError):
            _LOGGER.warning(f"{self.deviceName}: Invalid lift distance target '{target_cm}'")
            return

        current = self._read_plant_distance_cm()
        if current is None or self._plant_distance_is_stale():
            _LOGGER.warning(
                f"{self.deviceName}: No fresh plant distance - cannot move closed loop"
            )
            return

        target = self._clamp_distance_target(target)

        self._move_in_progress = True
        start = time.time()
        stalled = 0
        previous = None
        try:
            for _ in range(max(1, self.maxIterations)):
                if self.maxRuntimeSec > 0 and (time.time() - start) > self.maxRuntimeSec:
                    _LOGGER.warning(f"{self.deviceName}: Lift move aborted - max runtime exceeded")
                    break

                distance = self._read_plant_distance_cm()
                if distance is None:
                    _LOGGER.warning(f"{self.deviceName}: Plant distance lost mid-move - stopping")
                    break

                if abs(distance - target) <= self.toleranceCM:
                    _LOGGER.debug(f"{self.deviceName}: Target distance {target:.1f}cm reached")
                    break

                direction = 1 if target > distance else -1

                if direction < 0 and self.minDistanceCM > 0 and distance <= self.minDistanceCM:
                    _LOGGER.warning(
                        f"{self.deviceName}: Minimum distance reached - stopping"
                    )
                    break

                if direction > 0 and self.maxDistanceCM > 0 and distance >= self.maxDistanceCM:
                    _LOGGER.warning(
                        f"{self.deviceName}: Maximum distance reached - stopping"
                    )
                    break

                if previous is not None and abs(distance - previous) < self.toleranceCM:
                    stalled += 1
                    if stalled >= self.noProgressLimit:
                        _LOGGER.warning(
                            f"{self.deviceName}: No distance change over "
                            f"{stalled} steps - stopping"
                        )
                        break
                else:
                    stalled = 0
                previous = distance

                moved = await self._nudge(direction)
                if not moved:
                    break

                self._set_control_lock(self._estimate_lock(direction))
                if self.settleTimeSec > 0:
                    await asyncio.sleep(self.settleTimeSec)
        finally:
            self._move_in_progress = False
            self.identifyIfRunningState()

    # ------------------------------------------------------------------ actions
    async def increaseAction(self, data=None) -> None:
        """Raises the lamp by one step (guarded by the maximum distance)."""
        self._refresh_config()
        self.log_action("IncreaseAction")
        if not self._raising_is_safe():
            return
        await self._nudge(1)

    async def reduceAction(self, data=None) -> None:
        """Lowers the lamp by one step (guarded by the minimum distance)."""
        self._refresh_config()
        self.log_action("ReduceAction")
        if not self._lowering_is_safe():
            return
        await self._nudge(-1)

    async def setPositionAction(self, data=None) -> None:
        """Handle a "Set Lift" event.

        Accepts either an absolute position (``{"value": 60}``) or a target
        distance in cm (``{"distance": 40}`` / ``{"target_distance": 40}``).
        """
        self._refresh_config()
        if isinstance(data, dict) and ("distance" in data or "target_distance" in data):
            target = data.get("distance", data.get("target_distance"))
            await self.move_to_distance(target)
            return

        value = data.get("value") if isinstance(data, dict) else data
        await self.set_position(value)

    async def turn_on(self, **kwargs) -> None:
        """Raise the lift (on = up)."""
        await self.increaseAction(None)

    async def turn_off(self, **kwargs) -> None:
        """Lower the lift (off = down)."""
        await self.reduceAction(None)

    def identifyIfRunningState(self) -> None:
        """Sync the internal position from the live HA state."""
        current = self._read_current_position()
        if current is not None:
            self.position = current
        self.isRunning = self.position is not None and self.position > self.minPosition

    def log_action(self, action_name) -> None:
        _LOGGER.debug(f"{action_name}: {self.deviceName} LiftPosition: {self.position}%")

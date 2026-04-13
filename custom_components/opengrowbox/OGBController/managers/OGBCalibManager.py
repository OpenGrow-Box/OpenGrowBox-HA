"""Capability calibration manager for OpenGrowBox.

Measures actual environmental impact per capability (e.g. canHeat, canCool,
canHumidify, canCO2) by running controlled on/off cycles and computing
delta-per-minute for temperature, humidity and CO2.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils.sensorUpdater import _update_specific_select

_LOGGER = logging.getLogger(__name__)


class OGBCalibManager:
    """Manages per-capability device calibration."""

    # Measurement durations in seconds
    BASELINE_DURATION = 60
    COOLDOWN_DURATION = 30

    # Dimmable device step calibration
    DIMMABLE_STEPS = [25, 50, 75, 100]
    DIMMABLE_STEP_DURATION = 90  # seconds per step
    DIMMABLE_FULL_DURATION = 120  # seconds for 100% step

    # Non-dimmable fallback durations
    EFFECT_DURATIONS = {
        "canHeat": 180,
        "canCool": 180,
        "canHumidify": 240,
        "canDehumidify": 240,
        "canClimate": 300,
        "canLight": 300,
        "canCO2": 120,
    }

    # Safety abort thresholds
    SAFETY_LIMITS = {
        "temperature": {"max": 35.0, "min": 10.0},
        "humidity": {"max": 95.0, "min": 30.0},
        "co2": {"max": 2500.0, "min": 400.0},
    }

    # Metrics to collect per capability
    METRIC_KEYS = {
        "canHeat": ["temperature", "humidity"],
        "canCool": ["temperature", "humidity"],
        "canHumidify": ["temperature", "humidity"],
        "canDehumidify": ["temperature", "humidity"],
        "canClimate": ["temperature", "humidity"],
        "canLight": ["temperature", "humidity"],
        "canCO2": ["co2"],
    }

    def __init__(self, hass, data_store, event_manager, room, notificator=None):
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.notificator = notificator

        self._calibration_task: Optional[asyncio.Task] = None
        self._abort_requested = False
        self._original_tent_mode: Optional[str] = None

        # Listen for console commands
        self.hass.bus.async_listen("ogb_cap_calibration_command", self._handle_command)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def _handle_command(self, event):
        """Handle incoming calibration commands from the console."""
        event_room = event.data.get("room")
        _LOGGER.info(f"[{self.room}] _handle_command fired: room={event_room}, action={event.data.get('action')}, cap={event.data.get('cap')}")
        if event_room != self.room:
            _LOGGER.info(f"[{self.room}] _handle_command ignored: wrong room (expected {self.room})")
            return

        action = event.data.get("action")
        cap = event.data.get("cap")

        if action == "start":
            await self.start_calibration(cap)
        elif action == "stop":
            await self.stop_calibration()

    # Capabilities with multiple operational modes (heat/cool/dehumidify)
    MULTI_MODE_CAPS = {"canClimate"}

    async def start_calibration(self, cap: str):
        """Start a calibration run for the given capability."""
        _LOGGER.info(f"[{self.room}] start_calibration called for {cap}")
        if self._calibration_task and not self._calibration_task.done():
            _LOGGER.warning(f"[{self.room}] Calibration already running – abort it first.")
            await self._notify("⚠️ Calibration already running. Use 'cap_cal_stop' first.")
            return

        # Validate capability
        capabilities = self.data_store.get("capabilities") or {}
        cap_data = capabilities.get(cap)
        if not cap_data:
            _LOGGER.warning(f"[{self.room}] Unknown capability: '{cap}'")
            await self._notify(f"⚠️ Unknown capability: '{cap}'")
            return

        dev_entities = cap_data.get("devEntities", [])
        if not dev_entities:
            _LOGGER.warning(f"[{self.room}] No devices found for capability '{cap}'.")
            await self._notify(f"⚠️ No devices found for capability '{cap}'.")
            return

        devices = self.data_store.get("devices") or []
        target_devices = [d for d in devices if getattr(d, "deviceName", None) in dev_entities]
        if not target_devices:
            _LOGGER.warning(f"[{self.room}] Could not resolve device objects for '{cap}'.")
            await self._notify(f"⚠️ Could not resolve device objects for '{cap}'.")
            return

        if cap in self.MULTI_MODE_CAPS:
            await self._notify(
                f"ℹ️ Note: '{cap}' devices can heat, cool and dehumidify. "
                "The calibration result reflects ONLY the currently active mode."
            )

        self._abort_requested = False
        self._calibration_task = asyncio.create_task(
            self._run_calibration(cap, target_devices)
        )
        _LOGGER.info(f"[{self.room}] Started calibration task for {cap}")

    async def stop_calibration(self):
        """Abort an active calibration and restore device states."""
        if not self._calibration_task or self._calibration_task.done():
            await self._notify("ℹ️ No calibration is currently running.")
            return

        self._abort_requested = True
        self._calibration_task.cancel()
        try:
            await self._calibration_task
        except asyncio.CancelledError:
            pass
        self._calibration_task = None

        # Restore original tent mode if saved
        if self._original_tent_mode is not None:
            self.data_store.set("tentMode", self._original_tent_mode)
            self._original_tent_mode = None

        # Clear active flag
        self.data_store.setDeep("capCalibration.active", None)
        await self.event_manager.emit("SaveState", True)

        await self._notify("🛑 Calibration aborted. Tent mode restored.")
        _LOGGER.info(f"[{self.room}] Calibration aborted by user.")

    def _has_dimmable_devices(self, cap: str) -> bool:
        """Return True if any device in the capability is dimmable."""
        capabilities = self.data_store.get("capabilities") or {}
        cap_data = capabilities.get(cap, {})
        dev_entities = cap_data.get("devEntities", [])
        devices = self.data_store.get("devices") or []
        return any(
            getattr(d, "deviceName", None) in dev_entities and getattr(d, "isDimmable", False)
            for d in devices
        )

    # ------------------------------------------------------------------
    # Core calibration loop
    # ------------------------------------------------------------------

    async def _run_calibration(self, cap: str, devices: List[Any]):
        """Orchestrate baseline -> effect steps -> cooldown -> compute."""
        _LOGGER.info(f"[{self.room}] _run_calibration started for {cap}")

        try:
            metrics = self.METRIC_KEYS.get(cap, ["temperature", "humidity"])
            is_dimmable = self._has_dimmable_devices(cap)

            # Save original mode and disable control
            self._original_tent_mode = self.data_store.get("tentMode")
            
            _LOGGER.info(f"[{self.room}] Original tentMode saved: {self._original_tent_mode}")
            self.data_store.set("tentMode", "Disabled")

            await _update_specific_select("ogb_tentmode", self.room, "Disabled", self.hass)
            await asyncio.sleep(3)
            # Mark as active immediately so UI/status can see it
            active_info = {
                "cap": cap,
                "state": "preparing",
                "started_at": datetime.now().isoformat(),
                "is_dimmable": is_dimmable,
            }
            self.data_store.setDeep("capCalibration.active", active_info)
            _LOGGER.info(f"[{self.room}] capCalibration.active set to preparing (dimmable={is_dimmable})")

            mode_str = "response-curve" if is_dimmable else "on/off"
            await self._notify(
                f"🔄 Starting {mode_str} calibration for '{cap}' with {len(devices)} device(s).\n"
                f"Metrics: {', '.join(metrics)}\n"
                f"Original mode '{self._original_tent_mode}' paused.\n"
                f"Turning off all devices and waiting 10s for stabilization..."
            )

            await self.event_manager.emit("selectActionMode", "Disabled")
            await self.event_manager.emit("CalibOff", {"room": self.room, "cap": cap})
            await asyncio.sleep(10)

            _LOGGER.info(f"[{self.room}] All devices turned off, tentMode set to Disabled")

            # Update state to baseline
            self.data_store.setDeep("capCalibration.active.state", "baseline")
            _LOGGER.info(f"[{self.room}] capCalibration.active.state updated to baseline")

            await self._notify(f"Phase 1/3: Baseline measurement ({self.BASELINE_DURATION}s)")

            # Phase 1: Baseline
            baseline = await self._measure_phase(metrics, self.BASELINE_DURATION, cap)
            if self._abort_requested:
                return

            if is_dimmable:
                # Phase 2: Response curve steps
                step_results: Dict[int, Dict[str, List[float]]] = {}
                for i, step in enumerate(self.DIMMABLE_STEPS):
                    duration = self.DIMMABLE_FULL_DURATION if step == 100 else self.DIMMABLE_STEP_DURATION
                    self.data_store.setDeep("capCalibration.active.state", f"effect_{step}%")
                    await self._notify(
                        f"Phase 2/{len(self.DIMMABLE_STEPS)}: Measuring effect at {step}% ({duration}s)"
                    )
                    await self.event_manager.emit(
                        "CalibStart", {"room": self.room, "cap": cap, "level": step}
                    )
                    step_readings = await self._measure_phase(metrics, duration, cap)
                    if self._abort_requested:
                        return
                    step_results[step] = step_readings

                # Compute response curve results
                results = self._compute_response_curve_results(
                    cap, baseline, step_results, metrics
                )
            else:
                # Phase 2: Single on/off effect
                effect_duration = self.EFFECT_DURATIONS.get(cap, 180)
                self.data_store.setDeep("capCalibration.active.state", "effect")
                await self._notify(
                    f"Phase 2/3: Measuring effect ({effect_duration}s) – turning devices ON"
                )
                await self.event_manager.emit("CalibStart", {"room": self.room, "cap": cap})

                effect = await self._measure_phase(metrics, effect_duration, cap)
                if self._abort_requested:
                    return

                results = self._compute_results(cap, baseline, effect, metrics, effect_duration)

            # Phase 3: Cooldown (turn devices OFF)
            self.data_store.setDeep("capCalibration.active.state", "cooldown")
            await self._notify(
                f"Phase 3/3: Cooldown ({self.COOLDOWN_DURATION}s) – turning devices OFF"
            )
            await self.event_manager.emit("CalibOff", {"room": self.room, "cap": cap})

            await asyncio.sleep(self.COOLDOWN_DURATION)

            # Store results
            self.data_store.setDeep(f"capCalibration.results.{cap}", results)
            self.data_store.setDeep("capCalibration.active", None)

            await self.event_manager.emit("SaveState", True)

            await self._notify(self._format_results(cap, results, metrics))
            _LOGGER.info(f"[{self.room}] Calibration for {cap} completed: {results}")

        except asyncio.CancelledError:
            _LOGGER.info(f"[{self.room}] Calibration task cancelled")
            raise
        except Exception as e:
            _LOGGER.error(f"[{self.room}] Calibration error: {e}", exc_info=True)
            await self._notify(f"❌ Calibration failed: {e}")
        finally:
            await self.event_manager.emit("CalibOff", {"room": self.room, "cap": cap})
            if self._original_tent_mode is not None:
                self.data_store.set("tentMode", self._original_tent_mode)
                await _update_specific_select("ogb_tentmode", self.room, self._original_tent_mode, self.hass)
                await self.event_manager.emit("selectActionMode", self._original_tent_mode)
                self._original_tent_mode = None
            self.data_store.setDeep("capCalibration.active", None)
            await self._notify("🔁 Original tent mode restored.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _measure_phase(
        self, metrics: List[str], duration: int, cap: str
    ) -> Dict[str, List[float]]:
        """Collect sensor readings every 10 seconds for *duration* seconds."""
        readings: Dict[str, List[float]] = {m: [] for m in metrics}
        elapsed = 0
        interval = 10

        while elapsed < duration:
            if self._abort_requested:
                return readings

            # Safety check
            if await self._safety_violated(metrics):
                await self._notify("🚨 Safety limit exceeded – aborting calibration!")
                self._abort_requested = True
                return readings

            for metric in metrics:
                value = self._get_current_metric(metric)
                if value is not None:
                    readings[metric].append(value)

            elapsed += interval
            await asyncio.sleep(interval)

        return readings

    def _get_current_metric(self, metric: str) -> Optional[float]:
        """Read the latest sensor value from the data store."""
        if metric == "temperature":
            return self.data_store.getDeep("tentData.temperature")
        elif metric == "humidity":
            return self.data_store.getDeep("tentData.humidity")
        elif metric == "co2":
            return self.data_store.getDeep("tentData.co2Level")
        return None

    async def _safety_violated(self, metrics: List[str]) -> bool:
        """Return True if any monitored metric exceeds safety thresholds."""
        for metric in metrics:
            value = self._get_current_metric(metric)
            if value is None:
                continue
            limits = self.SAFETY_LIMITS.get(metric)
            if limits:
                if value > limits["max"] or value < limits["min"]:
                    _LOGGER.warning(
                        f"[{self.room}] Safety violation: {metric}={value} "
                        f"(limits {limits['min']}-{limits['max']})"
                    )
                    return True
        return False

    def _compute_results(
        self,
        cap: str,
        baseline: Dict[str, List[float]],
        effect: Dict[str, List[float]],
        metrics: List[str],
        effect_duration: int,
    ) -> Dict[str, Any]:
        """Calculate delta-per-minute and confidence for each metric (non-dimmable)."""
        results: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "isDimmable": False,
        }

        for metric in metrics:
            base_vals = baseline.get(metric, [])
            effect_vals = effect.get(metric, [])

            if not base_vals or not effect_vals:
                results[metric] = {"delta_per_min": 0.0, "confidence": 0.0, "note": "insufficient_data"}
                continue

            base_avg = sum(base_vals) / len(base_vals)
            effect_avg = sum(effect_vals) / len(effect_vals)
            delta = effect_avg - base_avg
            delta_per_min = delta / (effect_duration / 60.0)
            confidence = min(1.0, len(effect_vals) / 30.0)

            results[metric] = {
                "baseline_avg": round(base_avg, 2),
                "effect_avg": round(effect_avg, 2),
                "delta": round(delta, 2),
                "delta_per_min": round(delta_per_min, 3),
                "confidence": round(confidence, 2),
            }

        if cap in self.MULTI_MODE_CAPS:
            results["note"] = "Multi-mode device: result reflects only the currently active mode (heat/cool/dehumidify)."

        return results

    def _compute_response_curve_results(
        self,
        cap: str,
        baseline: Dict[str, List[float]],
        step_results: Dict[int, Dict[str, List[float]]],
        metrics: List[str],
    ) -> Dict[str, Any]:
        """Calculate response-curve results for dimmable devices."""
        results: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "isDimmable": True,
        }

        for metric in metrics:
            base_vals = baseline.get(metric, [])
            if not base_vals:
                results[metric] = {"note": "insufficient_baseline_data"}
                continue

            base_avg = sum(base_vals) / len(base_vals)
            response_curve = {}
            xs = []
            ys = []

            for step in self.DIMMABLE_STEPS:
                effect_vals = step_results.get(step, {}).get(metric, [])
                duration = self.DIMMABLE_FULL_DURATION if step == 100 else self.DIMMABLE_STEP_DURATION
                if not effect_vals:
                    response_curve[step] = {"delta_per_min": 0.0, "confidence": 0.0, "note": "insufficient_data"}
                    continue

                effect_avg = sum(effect_vals) / len(effect_vals)
                delta = effect_avg - base_avg
                delta_per_min = delta / (duration / 60.0)
                confidence = min(1.0, len(effect_vals) / 20.0)

                response_curve[step] = {
                    "delta": round(delta, 2),
                    "delta_per_min": round(delta_per_min, 3),
                    "confidence": round(confidence, 2),
                }
                xs.append(float(step))
                ys.append(delta_per_min)

            # Simple linear regression for slope_per_percent
            slope = 0.0
            r_squared = 0.0
            if len(xs) >= 2:
                n = len(xs)
                sum_x = sum(xs)
                sum_y = sum(ys)
                sum_xy = sum(x * y for x, y in zip(xs, ys))
                sum_x2 = sum(x * x for x in xs)
                denominator = (n * sum_x2) - (sum_x ** 2)
                if denominator != 0:
                    slope = ((n * sum_xy) - (sum_x * sum_y)) / denominator
                    y_mean = sum_y / n
                    ss_res = sum((y - (slope * x + (sum_y - slope * sum_x) / n)) ** 2 for x, y in zip(xs, ys))
                    ss_tot = sum((y - y_mean) ** 2 for y in ys)
                    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot != 0 else 1.0

            results[metric] = {
                "baseline_avg": round(base_avg, 2),
                "response_curve": response_curve,
                "slope_per_percent": round(slope, 4),
                "r_squared": round(r_squared, 3),
            }

        if cap in self.MULTI_MODE_CAPS:
            results["note"] = "Multi-mode device: result reflects only the currently active mode (heat/cool/dehumidify)."

        return results

    def _format_results(self, cap: str, results: Dict[str, Any], metrics: List[str]) -> str:
        """Build a human-readable result string for the console."""
        is_dimmable = results.get("isDimmable", False)
        lines = [
            f"✅ Calibration complete for '{cap}'",
            f"Timestamp: {results.get('timestamp', 'N/A')}",
            f"Mode: {'Dimmable (response curve)' if is_dimmable else 'On/Off'}",
            "=" * 40,
        ]

        for metric in metrics:
            data = results.get(metric, {})
            note = data.get("note")
            if note:
                lines.append(f"{metric}: {note}")
            elif is_dimmable:
                curve = data.get("response_curve", {})
                slope = data.get("slope_per_percent", 0)
                r2 = data.get("r_squared", 0)
                lines.append(f"{metric}: slope={slope:+.4f}/% (R²={r2})")
                for step in sorted(curve.keys()):
                    sdata = curve[step]
                    if "note" in sdata:
                        lines.append(f"  {step}%: {sdata['note']}")
                    else:
                        lines.append(
                            f"  {step}%: Δ {sdata.get('delta', 0):+.2f} "
                            f"({sdata.get('delta_per_min', 0):+.3f}/min) "
                            f"conf={sdata.get('confidence', 0)}"
                        )
            else:
                lines.append(
                    f"{metric}: Δ {data.get('delta', 0):+.2f}  "
                    f"({data.get('delta_per_min', 0):+.3f}/min)  "
                    f"confidence={data.get('confidence', 0)}"
                )

        global_note = results.get("note")
        if global_note:
            lines.append(f"\n⚠️  Note: {global_note}")
        lines.append("=" * 40)
        return "\n".join(lines)

    async def _notify(self, message: str):
        """Send a console response notification."""
        event_type = "ogb_console_response"
        event_data = {
            "room": self.room,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        self.hass.bus.async_fire(event_type, event_data)

    # ------------------------------------------------------------------
    # Status helpers (used by console)
    # ------------------------------------------------------------------

    def get_active_calibration(self) -> Optional[Dict[str, Any]]:
        """Return the currently active calibration info, if any."""
        return self.data_store.getDeep("capCalibration.active")

    def get_calibration_results(self, cap: Optional[str] = None) -> Dict[str, Any]:
        """Return stored calibration results."""
        results = self.data_store.getDeep("capCalibration.results", {})
        if cap:
            return results.get(cap, {})
        return results

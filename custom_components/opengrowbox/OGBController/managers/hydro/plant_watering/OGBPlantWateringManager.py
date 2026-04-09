import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ....data.OGBDataClasses.OGBPublications import OGBHydroAction

_LOGGER = logging.getLogger(__name__)


class OGBPlantWateringManager:
    """Simple sensor-driven plant watering for hydro media."""

    def __init__(self, hass, data_store, event_manager, room, medium_manager, cast_manager):
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.medium_manager = medium_manager
        self.cast_manager = cast_manager
        self._last_client_signature = None
        self._sensor_blocked = False
        self._wet_lock_active = False

        # AMBIENT ROOM CHECK: Ambient rooms don't use Plant Watering
        if self.room.lower() == "ambient":
            _LOGGER.debug(f"{self.room}: Plant Watering Manager disabled - ambient room")
            return

    async def start(self, duration: float, pump_devices, cooldown_minutes: Optional[float] = None):
        """Start the plant-watering monitor loop."""
        # AMBIENT ROOM CHECK
        if self.room.lower() == "ambient":
            _LOGGER.debug(f"{self.room}: Plant Watering skipped - ambient room")
            return None

        cooldown = self._resolve_cooldown_minutes(cooldown_minutes)
        active_pumps = self._get_active_pumps(pump_devices)

        if not active_pumps:
            await self._emit_log(
                message="Plant Watering: keine passenden Pumpen gefunden.",
                actions=["Pumpen: nicht verfuegbar"],
                devices=[],
            )
            return None

        await self._emit_status_log(
            action="Monitor aktiv",
            moisture_snapshot=self._get_moisture_snapshot(),
            duration=duration,
            cooldown_minutes=cooldown,
            devices=active_pumps,
            force=True,
        )

        return asyncio.create_task(
            self._run_monitor_loop(
                duration=float(duration),
                cooldown_minutes=cooldown,
                active_pumps=active_pumps,
            )
        )

    async def run_single_shot(self, duration: float, pump_devices):
        """Run one immediate watering shot."""
        active_pumps = self._get_active_pumps(pump_devices)
        if not active_pumps:
            await self._emit_log(
                message="Plant Watering: keine passenden Pumpen gefunden.",
                actions=["Pumpen: nicht verfuegbar"],
                devices=[],
            )
            return

        await self._run_watering_shot(active_pumps, float(duration), reason="Manueller Shot")

    async def _run_monitor_loop(self, duration: float, cooldown_minutes: float, active_pumps: List[str]):
        try:
            while True:
                snapshot = self._get_moisture_snapshot()

                if snapshot.get("sensor_count", 0) <= 0:
                    self._sensor_blocked = True
                    await self._turn_off_pumps(active_pumps)
                    await self._emit_log(
                        message="Plant-Watering Sensor-Based: NO SENSOR - I will stop using pump until sensor available.",
                        actions=["SAFETY STOP", "No sensor available"],
                        devices=active_pumps,
                        force=False,
                    )
                    await asyncio.sleep(60)
                    continue

                if self._sensor_blocked:
                    self._sensor_blocked = False
                    await self._emit_log(
                        message="Plant-Watering Sensor-Based: Sensor available again - monitoring resumed.",
                        actions=["Safety cleared"],
                        devices=active_pumps,
                        force=True,
                    )

                should_water, reason = self._should_water(snapshot, cooldown_minutes)

                if should_water:
                    await self._run_watering_shot(active_pumps, duration, reason)
                else:
                    await self._emit_status_log(
                        action=reason,
                        moisture_snapshot=snapshot,
                        duration=duration,
                        cooldown_minutes=cooldown_minutes,
                        devices=active_pumps,
                        force=False,
                    )

                await asyncio.sleep(60)
        except asyncio.CancelledError:
            await self._turn_off_pumps(active_pumps)
            raise

    def _get_active_pumps(self, pump_devices) -> List[str]:
        """
        Find watering pumps using label-based discovery.
        
        Search order:
        1. Check OGB devices with WateringPump labels
        2. Fallback to entity ID keyword matching (backward compatibility)
        
        Args:
            pump_devices: Dictionary with device entities
            
        Returns:
            List of watering pump entity IDs
        """
        if not pump_devices or "devEntities" not in pump_devices:
            return []
        
        # WateringPump labels from DEVICE_TYPE_MAPPING
        watering_labels = [
            "watering", "plant_water", "irrigation", "watering_pump", "irrigate",
            "bewässerung", "bewaesserung"
        ]
        
        devices = pump_devices["devEntities"]
        active_pumps = []
        
        # Search 1: Check OGB devices by labels
        ogb_devices = self.data_store.getDeep("devices", [])
        for device in ogb_devices:
            device_labels = [lbl.get("name", "").lower() for lbl in device.get("labels", [])]
            
            # Check if any device label matches watering labels
            for label in watering_labels:
                if any(label.lower() == dl or label.lower() in dl for dl in device_labels):
                    # Found matching device, get its entity ID
                    entities = device.get("entities", [])
                    for entity in entities:
                        entity_id = entity.get("entity_id", "")
                        if entity_id in devices:
                            active_pumps.append(entity_id)
                            _LOGGER.debug(
                                f"[{self.room}] Found watering pump via label '{label}': {entity_id}"
                            )
                            break
        
        # Search 2: Fallback to entity ID keyword matching (backward compatibility)
        if not active_pumps:
            valid_keywords = ["water", "cast", "feedpump_w"]
            for dev in devices:
                if any(keyword in dev.lower() for keyword in valid_keywords):
                    active_pumps.append(dev)
                    _LOGGER.debug(
                        f"[{self.room}] Found watering pump via entity ID fallback: {dev}"
                    )
        
        return active_pumps

    def _get_moisture_snapshot(self) -> Dict[str, Any]:
        mediums = self.medium_manager.get_mediums() if self.medium_manager else []
        moisture_values = []
        threshold_min_values = []
        threshold_max_values = []
        source_names = []

        for medium in mediums:
            moisture = getattr(medium, "current_moisture", None)
            thresholds = getattr(medium, "thresholds", None)
            threshold_min = getattr(thresholds, "moisture_min", None)
            threshold_max = getattr(thresholds, "moisture_max", None)

            try:
                if moisture is not None:
                    moisture_values.append(float(moisture))
                    source_names.append(medium.name)
            except (TypeError, ValueError):
                pass

            try:
                if threshold_min is not None:
                    threshold_min_values.append(float(threshold_min))
            except (TypeError, ValueError):
                pass

            try:
                if threshold_max is not None:
                    threshold_max_values.append(float(threshold_max))
            except (TypeError, ValueError):
                pass

        average_moisture = sum(moisture_values) / len(moisture_values) if moisture_values else None
        threshold_min = (
            sum(threshold_min_values) / len(threshold_min_values)
            if threshold_min_values
            else None
        )
        threshold_max = (
            sum(threshold_max_values) / len(threshold_max_values)
            if threshold_max_values
            else None
        )

        dryback_midpoint = None
        if threshold_min is not None and threshold_max is not None:
            dryback_midpoint = (threshold_min + threshold_max) / 2.0

        return {
            "average_moisture": average_moisture,
            "threshold_min": threshold_min,
            "threshold_max": threshold_max,
            "dryback_midpoint": dryback_midpoint,
            "sensor_count": len(moisture_values),
            "sources": source_names,
            "last_watering": self.data_store.getDeep("Hydro.PlantWatering.lastWatering"),
        }

    def _should_water(self, snapshot: Dict[str, Any], cooldown_minutes: float):
        average_moisture = snapshot.get("average_moisture")
        threshold_min = snapshot.get("threshold_min")
        threshold_max = snapshot.get("threshold_max")
        dryback_midpoint = snapshot.get("dryback_midpoint")

        if self._sensor_blocked:
            return False, "SAFETY: Sensor fehlt - Pumpe gesperrt"

        if average_moisture is None:
            return False, "Warte: kein Medium-Feuchtesensor"

        if threshold_min is None:
            return False, "Warte: kein Feuchte-Schwellwert"

        if threshold_max is not None and average_moisture >= threshold_max:
            self._wet_lock_active = True
            return False, "Stop: Feuchte-Maximum erreicht"

        if self._wet_lock_active:
            release_threshold = dryback_midpoint if dryback_midpoint is not None else threshold_min
            if average_moisture > release_threshold:
                return (
                    False,
                    f"Warte: Dryback-Lock aktiv bis <= {self._format_value(release_threshold)}",
                )
            self._wet_lock_active = False

        if average_moisture >= threshold_min:
            return False, "Warte: Medium feucht genug"

        if not self._cooldown_elapsed(snapshot.get("last_watering"), cooldown_minutes):
            return False, "Warte: Cooldown aktiv"

        return True, "Bewaesserung: unter Feuchte-Minimum"

    def _cooldown_elapsed(self, last_watering_raw, cooldown_minutes: float) -> bool:
        if not last_watering_raw:
            return True

        try:
            last_watering = datetime.fromisoformat(last_watering_raw)
        except (TypeError, ValueError):
            return True

        return datetime.now() - last_watering >= timedelta(minutes=cooldown_minutes)

    def _resolve_cooldown_minutes(self, cooldown_minutes: Optional[float]) -> float:
        if cooldown_minutes is not None and cooldown_minutes > 0:
            return float(cooldown_minutes)

        interval_raw = self.data_store.getDeep("Hydro.Intervall")
        try:
            if interval_raw is not None and float(interval_raw) > 0:
                return float(interval_raw)
        except (TypeError, ValueError):
            pass

        return 30.0

    async def _run_watering_shot(self, active_pumps: List[str], duration: float, reason: str):
        snapshot = self._get_moisture_snapshot()

        if snapshot.get("sensor_count", 0) <= 0:
            self._sensor_blocked = True
            await self._turn_off_pumps(active_pumps)
            await self._emit_log(
                message="Plant-Watering Sensor-Based: NO SENSOR - I will stop using pump until sensor available.",
                actions=["SAFETY STOP", "No sensor available"],
                devices=active_pumps,
                force=True,
            )
            return

        max_threshold = snapshot.get("threshold_max")
        avg_moisture = snapshot.get("average_moisture")
        if (
            max_threshold is not None
            and avg_moisture is not None
            and avg_moisture >= max_threshold
        ):
            self._wet_lock_active = True
            await self._emit_status_log(
                action="Stop: Feuchte-Maximum bereits erreicht",
                moisture_snapshot=snapshot,
                duration=duration,
                cooldown_minutes=self._resolve_cooldown_minutes(None),
                devices=active_pumps,
                force=True,
            )
            return

        await self._emit_status_log(
            action=reason,
            moisture_snapshot=snapshot,
            duration=duration,
            cooldown_minutes=self._resolve_cooldown_minutes(None),
            devices=active_pumps,
            force=True,
        )

        for dev_id in active_pumps:
            await self.cast_manager._register_pump_operation(dev_id, "plant_watering")
            pump_action = OGBHydroAction(
                Name=self.room, Action="on", Device=dev_id, Cycle=True
            )
            await self.event_manager.emit("PumpAction", pump_action)

        remaining = max(float(duration), 0.0)
        check_step_sec = 2.0

        while remaining > 0:
            sleep_for = min(check_step_sec, remaining)
            await asyncio.sleep(sleep_for)
            remaining -= sleep_for

            current_snapshot = self._get_moisture_snapshot()
            if current_snapshot.get("sensor_count", 0) <= 0:
                self._sensor_blocked = True
                await self._emit_log(
                    message="Plant-Watering Sensor-Based: SENSOR LOST during shot - pump stopped immediately.",
                    actions=["SAFETY STOP", "No sensor available"],
                    devices=active_pumps,
                    force=True,
                )
                break

            current_max = current_snapshot.get("threshold_max")
            current_avg = current_snapshot.get("average_moisture")
            if (
                current_max is not None
                and current_avg is not None
                and current_avg >= current_max
            ):
                self._wet_lock_active = True
                await self._emit_status_log(
                    action="Stop: Feuchte-Maximum erreicht",
                    moisture_snapshot=current_snapshot,
                    duration=duration,
                    cooldown_minutes=self._resolve_cooldown_minutes(None),
                    devices=active_pumps,
                    force=True,
                )
                break

        await self._turn_off_pumps(active_pumps)
        self.data_store.setDeep("Hydro.PlantWatering.lastWatering", datetime.now().isoformat())

    async def _turn_off_pumps(self, active_pumps: List[str]):
        for dev_id in active_pumps:
            pump_action = OGBHydroAction(
                Name=self.room, Action="off", Device=dev_id, Cycle=True
            )
            await self.event_manager.emit("PumpAction", pump_action)
            await self.cast_manager._unregister_pump_operation(dev_id)

    async def _emit_status_log(
        self,
        action: str,
        moisture_snapshot: Dict[str, Any],
        duration: float,
        cooldown_minutes: float,
        devices: List[str],
        force: bool,
    ):
        moisture = moisture_snapshot.get("average_moisture")
        threshold_min = moisture_snapshot.get("threshold_min")
        threshold_max = moisture_snapshot.get("threshold_max")
        dryback_midpoint = moisture_snapshot.get("dryback_midpoint")
        sources = ", ".join(moisture_snapshot.get("sources") or []) or "N/A"

        await self._emit_log(
            message=(
                f"Medium {self._format_value(moisture)} / Min {self._format_value(threshold_min)} / "
                f"Max {self._format_value(threshold_max)} / Mid {self._format_value(dryback_midpoint)} | "
                f"Shot {duration:.0f}s | Cooldown {cooldown_minutes:.0f}m"
            ),
            actions=[action, f"Sensoren: {sources}"],
            devices=devices,
            force=force,
        )

    async def _emit_log(self, message: str, actions: List[str], devices: List[str], force: bool = False):
        signature = (message, tuple(actions), tuple(devices))
        if not force and signature == self._last_client_signature:
            return

        self._last_client_signature = signature
        await self.event_manager.emit(
            "LogForClient",
            {
                "Name": self.room,
                "Mode": "Plant-Watering",
                "Action": "Plant-Watering",
                "Type": "HYDRO",
                "Message": message,
                "actions": actions,
                "Devices": devices,
            },
            haEvent=True,
        )

    def _format_value(self, value) -> str:
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):
            return "N/A"

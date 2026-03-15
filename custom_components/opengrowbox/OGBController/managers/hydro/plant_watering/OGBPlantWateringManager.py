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

    async def start(self, duration: float, pump_devices, cooldown_minutes: Optional[float] = None):
        """Start the plant-watering monitor loop."""
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
        if not pump_devices or "devEntities" not in pump_devices:
            return []

        valid_keywords = ["water", "cast"]
        return [
            dev
            for dev in pump_devices["devEntities"]
            if any(keyword in dev.lower() for keyword in valid_keywords)
        ]

    def _get_moisture_snapshot(self) -> Dict[str, Any]:
        mediums = self.medium_manager.get_mediums() if self.medium_manager else []
        moisture_values = []
        threshold_values = []
        source_names = []

        for medium in mediums:
            moisture = getattr(medium, "current_moisture", None)
            threshold = getattr(getattr(medium, "thresholds", None), "moisture_min", None)

            try:
                if moisture is not None:
                    moisture_values.append(float(moisture))
                    source_names.append(medium.name)
            except (TypeError, ValueError):
                pass

            try:
                if threshold is not None:
                    threshold_values.append(float(threshold))
            except (TypeError, ValueError):
                pass

        average_moisture = sum(moisture_values) / len(moisture_values) if moisture_values else None
        threshold = sum(threshold_values) / len(threshold_values) if threshold_values else None

        return {
            "average_moisture": average_moisture,
            "threshold": threshold,
            "sensor_count": len(moisture_values),
            "sources": source_names,
            "last_watering": self.data_store.getDeep("Hydro.PlantWatering.lastWatering"),
        }

    def _should_water(self, snapshot: Dict[str, Any], cooldown_minutes: float):
        average_moisture = snapshot.get("average_moisture")
        threshold = snapshot.get("threshold")

        if average_moisture is None:
            return False, "Warte: kein Medium-Feuchtesensor"

        if threshold is None:
            return False, "Warte: kein Feuchte-Schwellwert"

        if average_moisture >= threshold:
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

        await asyncio.sleep(duration)
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
        threshold = moisture_snapshot.get("threshold")
        sources = ", ".join(moisture_snapshot.get("sources") or []) or "N/A"

        await self._emit_log(
            message=(
                f"Medium {self._format_value(moisture)} / Min {self._format_value(threshold)} | "
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

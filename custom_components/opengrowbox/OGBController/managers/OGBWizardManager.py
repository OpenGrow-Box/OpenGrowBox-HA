import logging
from copy import deepcopy

from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


class OGBWizardManager:
    """Handles frontend wizard event requests and responses."""

    REQUEST_EVENT = "needPlantConfig"
    RESPONSE_EVENT = "needPlantConfigResult"
    SAVE_EVENT = "savePlantConfig"
    SAVE_RESPONSE_EVENT = "savePlantConfigResult"
    GET_PLANT_STAGES_EVENT = "givePlantStages"
    GET_PLANT_STAGES_RESPONSE_EVENT = "ogbPlantStagesResponse"
    DEBUG_REQUEST_EVENT = "giveDebugInfo"
    DEBUG_RESPONSE_EVENT = "ogbDebugInfoResponse"
    LIVE_CONFIG_URL = "https://prem.opengrowbox.net/api/v1/plantstages"
    STAGE_KEY_MAP = {
        "germination": "Germination",
        "clones": "Clones",
        "earlyveg": "EarlyVeg",
        "midveg": "MidVeg",
        "lateveg": "LateVeg",
        "earlyflower": "EarlyFlower",
        "midflower": "MidFlower",
        "lateflower": "LateFlower",
    }
    LIGHT_INTENSITY_PRESETS = {
        "low": (0, 30),
        "low to moderate": (30, 50),
        "moderate": (50, 70),
        "high": (70, 85),
        "very high": (80, 95),
        "moderate to low": (55, 75),
    }

    def __init__(self, hass, data_store, event_manager, room):
        self.hass = hass
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.default_plant_stages = deepcopy(self._get_plant_config() or {})
        self.default_light_plant_stages = deepcopy(self._get_light_plant_stages() or {})
        self._register_event_handlers()

    def _register_event_handlers(self):
        self.hass.bus.async_listen(self.REQUEST_EVENT, self._handle_need_plant_config)
        self.hass.bus.async_listen(self.SAVE_EVENT, self._handle_save_plant_config)
        self.hass.bus.async_listen(self.GET_PLANT_STAGES_EVENT, self._handle_give_plant_stages)
        self.hass.bus.async_listen(self.DEBUG_REQUEST_EVENT, self._handle_give_debug_info)

    async def _handle_need_plant_config(self, event):
        """Return plant config to the frontend wizard via the HA event bus."""
        try:
            event_data = getattr(event, "data", {}) or {}
            request_id = event_data.get("requestId") or event_data.get("request_id")
            requested_room = str(event_data.get("room") or "").lower()
            mode = event_data.get("mode", "default")
            if mode == "auto":
                mode = "live"
            active_source = mode

            _LOGGER.warning(
                "Wizard plant config request received | room=%s requested_room=%s mode=%s request_id=%s",
                self.room,
                requested_room,
                mode,
                request_id,
            )

            if requested_room and requested_room != str(self.room).lower():
                _LOGGER.warning(
                    "Ignoring wizard plant config request for room %s because current controller is %s",
                    requested_room,
                    self.room,
                )
                return

            if mode == "current":
                active_source = self._get_active_source()
                plant_config = self.data_store.get("plantStages") or self._get_plant_config()
                light_plant_stages = self.data_store.get("lightPlantStages") or self._get_light_plant_stages()
            elif mode == "live":
                plant_config = await self._get_live_plant_config()
                light_plant_stages = self._normalize_light_plant_stages_for_store(plant_config)
            elif mode == "custom":
                plant_config = self._get_custom_plant_config()
                light_plant_stages = self._get_custom_light_plant_config()
            else:
                plant_config = self._get_plant_config()
                light_plant_stages = self._get_light_plant_stages()
                active_source = "default"

            if plant_config is None:
                _LOGGER.warning(
                    "No plant config available for mode %s in room %s",
                    mode,
                    self.room,
                )
                await self.event_manager.emit("LogForClient", {
                    "Name": self.room,
                    "Type": "CONFIG",
                    "Message": "Plant config unavailable",
                    "mode": mode
                }, haEvent=True, debug_type="WARNING")
                self._fire_result(
                    request_id=request_id,
                    success=False,
                    mode=mode,
                    error=(
                        "Live plant config is currently unavailable"
                        if mode == "live"
                        else "Plant config not available in dataStore"
                    ),
                )
                return

            source_key = active_source if mode == "current" else mode
            source = self._get_source_label(source_key)

            self._fire_result(
                request_id=request_id,
                success=True,
                mode=mode,
                source=source,
                active_source=source_key,
                plant_stages=plant_config,
                light_plant_stages=light_plant_stages,
            )
            _LOGGER.warning(
                "Wizard plant config response sent | room=%s mode=%s request_id=%s source=%s",
                self.room,
                mode,
                request_id,
                source,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to handle needPlantConfig for room %s: %s",
                self.room,
                err,
                exc_info=True,
            )
            self._fire_result(
                request_id=(getattr(event, "data", {}) or {}).get("requestId"),
                success=False,
                error=str(err),
            )

    async def _handle_save_plant_config(self, event):
        """Persist and activate a selected plant config source."""
        event_data = getattr(event, "data", {}) or {}
        request_id = event_data.get("requestId") or event_data.get("request_id")
        requested_room = str(event_data.get("room") or "").lower()
        mode = event_data.get("mode", "default")
        if mode == "auto":
            mode = "live"
        incoming_plant_stages = event_data.get("plantStages")

        if requested_room and requested_room != str(self.room).lower():
            return

        try:
            result = await self.apply_plant_stage_config(mode, incoming_plant_stages)
            applied_stages, applied_light_stages = result if result is not None else (None, None)

            if applied_stages is None:
                self._fire_save_result(
                    request_id=request_id,
                    success=False,
                    mode=mode,
                    error="Could not save plant config",
                )
                return

            await self.event_manager.emit(
                "SaveState",
                {"source": "WizardManager", "action": "savePlantConfig", "mode": mode},
            )

            self._fire_save_result(
                request_id=request_id,
                success=True,
                mode=mode,
                source=self._get_source_label(self._get_active_source()),
                active_source=self._get_active_source(),
                plant_stages=applied_stages,
                light_plant_stages=applied_light_stages,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to save plant config for room %s: %s",
                self.room,
                err,
                exc_info=True,
            )
            self._fire_save_result(
                request_id=request_id,
                success=False,
                mode=mode,
                error=str(err),
            )

    async def apply_plant_stage_config(self, mode, plant_stages=None):
        """Apply selected plant stage source to active datastore plantStages."""
        normalized_mode = str(mode or "default").lower()
        if normalized_mode == "auto":
            normalized_mode = "live"

        if normalized_mode == "custom":
            if not plant_stages:
                return None, None

            normalized = self._normalize_plant_config_for_store(plant_stages)
            if normalized is None:
                return None, None
            normalized_light = self._normalize_light_plant_stages_for_store(plant_stages)
            self.data_store.set("customPlantStages", normalized)
            self.data_store.set("customLightPlantStages", normalized_light)
            self.data_store.set("plantStageSource", "custom")
            self.data_store.set("plantStages", normalized)
            self.data_store.set("lightPlantStages", normalized_light)
            await self._emit_current_stage_reapply("custom")
            return normalized, normalized_light

        if normalized_mode == "live":
            live_config = await self._get_live_plant_config()
            if live_config is None:
                live_config = self.data_store.get("livePlantStagesCache")

            if live_config is None:
                return None, None

            normalized = self._normalize_plant_config_for_store(live_config)
            if normalized is None:
                return None, None
            normalized_light = self._normalize_light_plant_stages_for_store(live_config)
            self.data_store.set("livePlantStagesCache", normalized)
            self.data_store.set("liveLightPlantStagesCache", normalized_light)
            self.data_store.set("plantStageSource", "live")
            self.data_store.set("plantStages", normalized)
            self.data_store.set("lightPlantStages", normalized_light)
            await self._emit_current_stage_reapply("live")
            return normalized, normalized_light

        default_config = deepcopy(self.default_plant_stages or self._get_plant_config())
        if default_config is None:
            return None, None

        default_light_config = deepcopy(self.default_light_plant_stages or self._get_light_plant_stages())

        self.data_store.set("plantStageSource", "default")
        self.data_store.set("plantStages", default_config)
        self.data_store.set("lightPlantStages", default_light_config)
        await self._emit_current_stage_reapply("default")
        return default_config, default_light_config

    async def _emit_current_stage_reapply(self, source):
        """Re-emit current plant stage so listeners apply updated stage presets immediately."""
        current_stage = self.data_store.get("plantStage")
        if not current_stage:
            _LOGGER.warning(
                "[%s] No current plantStage set after wizard %s apply; skipping reapply event",
                self.room,
                source,
            )
            return

        await self.event_manager.emit(
            "PlantStageChange",
            {
                "room": self.room,
                "plantStage": current_stage,
                "source": f"wizard_{source}",
                "reapply": True,
            },
        )
        _LOGGER.info(
            "[%s] Re-applied plant stage '%s' after wizard %s apply",
            self.room,
            current_stage,
            source,
        )

    async def restore_active_plant_stage_config(self):
        """Restore active plantStages from the saved source on startup."""
        source = self._get_active_source()
        self.data_store.set("plantStageSource", source)
        restored = await self.apply_plant_stage_config(source)
        if restored is None:
            _LOGGER.warning("Could not restore plant stage source %s for room %s", source, self.room)
        else:
            _LOGGER.info("Restored plant stage source %s for room %s", source, self.room)
        return restored

    async def _handle_give_plant_stages(self, event):
        """Return current plant stages to the frontend via event bus."""
        try:
            event_data = getattr(event, "data", {}) or {}
            request_id = event_data.get("requestId") or event_data.get("request_id")
            requested_room = str(event_data.get("room") or "").lower()
            
            # Only respond if this is for our room
            if requested_room and requested_room != str(self.room).lower():
                return
            
            # Get plant stages from datastore
            plant_stages = self.data_store.get("plantStages") or self.default_plant_stages
            light_plant_stages = self.data_store.get("lightPlantStages") or self.default_light_plant_stages
            active_source = self._get_active_source()
            current_stage = self.data_store.get("plantStage") or "germination"
            
            # Fire response event
            self.hass.bus.async_fire(self.GET_PLANT_STAGES_RESPONSE_EVENT, {
                "room": self.room,
                "requestId": request_id,
                "success": True,
                "plantStages": plant_stages,
                "lightPlantStages": light_plant_stages,
                "activeSource": active_source,
                "currentStage": current_stage,
            })
            _LOGGER.info(f"{self.room} - Sent plant stages via event: {active_source}")
            
        except Exception as err:
            _LOGGER.error(f"{self.room} - Error sending plant stages: {err}")
            self.hass.bus.async_fire(self.GET_PLANT_STAGES_RESPONSE_EVENT, {
                "room": self.room,
                "requestId": request_id,
                "success": False,
                "error": str(err),
            })

    async def _handle_give_debug_info(self, event):
        """Return debug information from datastore to frontend."""
        try:
            event_data = getattr(event, "data", {}) or {}
            request_id = event_data.get("requestId") or event_data.get("request_id")
            requested_room = str(event_data.get("room") or "").lower()
            request_type = event_data.get("request", "all")
            
            # Only respond if this is for our room
            if requested_room and requested_room != str(self.room).lower():
                return
            
            debug_data = {}
            
            # Get requested data from datastore
            if request_type in ("all", "plantStages"):
                debug_data["plantStages"] = self.data_store.get("plantStages") or self.default_plant_stages
                debug_data["plantStageSource"] = self.data_store.get("plantStageSource")
                debug_data["currentPlantStage"] = self.data_store.get("plantStage")
            
            if request_type in ("all", "tentData"):
                debug_data["tentData"] = self.data_store.get("tentData")
            
            if request_type in ("all", "vpd"):
                debug_data["vpd"] = self.data_store.get("vpd")
            
            if request_type in ("all", "mediums"):
                debug_data["mediums"] = self.data_store.get("mediums")
            
            if request_type in ("all", "growData"):
                debug_data["growData"] = {
                    "growstartdate": self.data_store.get("growstartdate"),
                    "bloomswitchdate": self.data_store.get("bloomswitchdate"),
                    "plantStage": self.data_store.get("plantStage"),
                }
            
            if request_type in ("all", "control"):
                debug_data["controlOptions"] = self.data_store.get("controlOptions")
                debug_data["controlOptionData"] = self.data_store.get("controlOptionData")
            
            # Fire response event
            self.hass.bus.async_fire(self.DEBUG_RESPONSE_EVENT, {
                "room": self.room,
                "requestId": request_id,
                "success": True,
                "requestType": request_type,
                "data": debug_data,
            })
            _LOGGER.info(f"{self.room} - Sent debug info: {request_type}")
            
        except Exception as err:
            _LOGGER.error(f"{self.room} - Error sending debug info: {err}")
            self.hass.bus.async_fire(self.DEBUG_RESPONSE_EVENT, {
                "room": self.room,
                "requestId": request_id,
                "success": False,
                "error": str(err),
            })

    def _get_active_source(self):
        source = str(self.data_store.get("plantStageSource") or "default").lower()
        if source not in {"default", "custom", "live"}:
            return "default"
        return source

    def _get_source_label(self, source):
        if source == "custom":
            return "OpenGrowBox Custom Library"
        if source == "live":
            return "OpenGrowBox Live Library"
        return "OpenGrowBox Default Library"

    def _fire_result(self, request_id=None, success=False, mode=None, source=None, active_source=None, plant_stages=None, light_plant_stages=None, error=None):
        payload = {
            "requestId": request_id,
            "success": success,
            "room": self.room,
        }

        if mode is not None:
            payload["mode"] = mode
        if source is not None:
            payload["source"] = source
        if active_source is not None:
            payload["activeSource"] = active_source
        if plant_stages is not None:
            payload["plantStages"] = plant_stages
        if light_plant_stages is not None:
            payload["lightPlantStages"] = light_plant_stages
        if error is not None:
            payload["error"] = error

        self.hass.bus.async_fire(self.RESPONSE_EVENT, payload)

    def _fire_save_result(self, request_id=None, success=False, mode=None, source=None, active_source=None, plant_stages=None, light_plant_stages=None, error=None):
        payload = {
            "requestId": request_id,
            "success": success,
            "room": self.room,
        }

        if mode is not None:
            payload["mode"] = mode
        if source is not None:
            payload["source"] = source
        if active_source is not None:
            payload["activeSource"] = active_source
        if plant_stages is not None:
            payload["plantStages"] = plant_stages
        if light_plant_stages is not None:
            payload["lightPlantStages"] = light_plant_stages
        if error is not None:
            payload["error"] = error

        self.hass.bus.async_fire(self.SAVE_RESPONSE_EVENT, payload)

    def _get_plant_config(self):
        """Read plant config from datastore with compatibility fallbacks."""
        lookup_order = [
            ("get", "plantConfig"),
            ("getDeep", "plantConfig"),
            ("get", "plantStages"),
            ("getDeep", "plantStages"),
        ]

        for method_name, key in lookup_order:
            method = getattr(self.data_store, method_name, None)
            if callable(method):
                value = method(key)
                if value is not None:
                    return value

        state = getattr(self.data_store, "state", None)
        if state is not None:
            for attr in ("plantConfig", "plantStages"):
                value = getattr(state, attr, None)
                if value is not None:
                    return value

        return None

    def _get_custom_plant_config(self):
        for method_name, key in (("get", "customPlantStages"), ("getDeep", "customPlantStages")):
            method = getattr(self.data_store, method_name, None)
            if callable(method):
                value = method(key)
                if value:
                    return value

        state = getattr(self.data_store, "state", None)
        if state is not None:
            value = getattr(state, "customPlantStages", None)
            if value:
                return value

        return None

    def _get_custom_light_plant_config(self):
        for method_name, key in (("get", "customLightPlantStages"), ("getDeep", "customLightPlantStages")):
            method = getattr(self.data_store, method_name, None)
            if callable(method):
                value = method(key)
                if value:
                    return value

        state = getattr(self.data_store, "state", None)
        if state is not None:
            value = getattr(state, "customLightPlantStages", None)
            if value:
                return value

        return None

    def _get_light_plant_stages(self):
        lookup_order = [
            ("get", "lightPlantStages"),
            ("getDeep", "lightPlantStages"),
        ]

        for method_name, key in lookup_order:
            method = getattr(self.data_store, method_name, None)
            if callable(method):
                value = method(key)
                if value is not None:
                    return self._coerce_light_stages_dict(value)

        state = getattr(self.data_store, "state", None)
        if state is not None:
            value = getattr(state, "lightPlantStages", None)
            if value is not None:
                return self._coerce_light_stages_dict(value)

        return None

    def _coerce_light_stages_dict(self, value):
        if not isinstance(value, dict):
            return value

        coerced = {}
        for key, item in value.items():
            if hasattr(item, "to_dict"):
                coerced[key] = item.to_dict()
            elif isinstance(item, dict):
                coerced[key] = item
            else:
                coerced[key] = {
                    "min": getattr(item, "min", 0),
                    "max": getattr(item, "max", 100),
                    "phase": getattr(item, "phase", ""),
                }
        return coerced

    def _normalize_plant_config_for_store(self, plant_config):
        """Normalize incoming frontend/live config to the HA datastore plantStages structure."""
        if not plant_config:
            return None

        extracted = (
            plant_config.get("plantStages")
            if isinstance(plant_config, dict) and plant_config.get("plantStages") is not None
            else plant_config.get("data", {}).get("plantStages")
            if isinstance(plant_config, dict) and isinstance(plant_config.get("data"), dict)
            else plant_config.get("data")
            if isinstance(plant_config, dict) and plant_config.get("data") is not None
            else plant_config
        )

        if isinstance(extracted, dict):
            normalized = {}
            for raw_key, raw_stage in extracted.items():
                store_key = self.STAGE_KEY_MAP.get(str(raw_key).replace(" ", "").lower())
                if not store_key or not isinstance(raw_stage, dict):
                    continue

                normalized[store_key] = self._normalize_single_stage(raw_stage)

            return normalized or None

        if isinstance(extracted, list):
            normalized = {}
            for stage in extracted:
                stage_key = stage.get("key") or stage.get("id")
                store_key = self.STAGE_KEY_MAP.get(str(stage_key).replace(" ", "").lower())
                if not store_key:
                    continue

                normalized[store_key] = self._normalize_live_stage(stage)

            return normalized or None

        return None

    def _normalize_single_stage(self, stage):
        fallback_key = self.STAGE_KEY_MAP.get(str(stage.get("key") or stage.get("id") or "").replace(" ", "").lower())
        fallback = (self.default_plant_stages or {}).get(fallback_key, {}) if fallback_key else {}

        if "vpdRange" in stage and "minTemp" in stage:
            return {
                "vpdRange": list(stage.get("vpdRange") or fallback.get("vpdRange") or [0.8, 1.2]),
                "minTemp": stage.get("minTemp", fallback.get("minTemp")),
                "maxTemp": stage.get("maxTemp", fallback.get("maxTemp")),
                "minHumidity": stage.get("minHumidity", fallback.get("minHumidity")),
                "maxHumidity": stage.get("maxHumidity", fallback.get("maxHumidity")),
            }

        min_vpd = stage.get("minVPD")
        max_vpd = stage.get("maxVPD")
        return {
            "vpdRange": [
                min_vpd if min_vpd is not None else (fallback.get("vpdRange") or [0.8, 1.2])[0],
                max_vpd if max_vpd is not None else (fallback.get("vpdRange") or [0.8, 1.2])[1],
            ],
            "minTemp": stage.get("minTemp", fallback.get("minTemp")),
            "maxTemp": stage.get("maxTemp", fallback.get("maxTemp")),
            "minHumidity": stage.get("minHumidity", fallback.get("minHumidity")),
            "maxHumidity": stage.get("maxHumidity", fallback.get("maxHumidity")),
        }

    def _normalize_live_stage(self, stage):
        stage_key = stage.get("key") or stage.get("id")
        store_key = self.STAGE_KEY_MAP.get(str(stage_key).replace(" ", "").lower())
        fallback = (self.default_plant_stages or {}).get(store_key, {}) if store_key else {}
        temperature = (stage.get("environmental", {}).get("temperature", {}).get("optimal") or [])
        humidity = (stage.get("environmental", {}).get("humidity", {}).get("optimal") or [])
        vpd = (stage.get("environmental", {}).get("vpd", {}).get("optimal") or [])

        return {
            "vpdRange": [
                vpd[0] if len(vpd) > 0 else (fallback.get("vpdRange") or [0.8, 1.2])[0],
                vpd[1] if len(vpd) > 1 else (fallback.get("vpdRange") or [0.8, 1.2])[1],
            ],
            "minTemp": temperature[0] if len(temperature) > 0 else fallback.get("minTemp"),
            "maxTemp": temperature[1] if len(temperature) > 1 else fallback.get("maxTemp"),
            "minHumidity": humidity[0] if len(humidity) > 0 else fallback.get("minHumidity"),
            "maxHumidity": humidity[1] if len(humidity) > 1 else fallback.get("maxHumidity"),
        }

    def _normalize_light_plant_stages_for_store(self, plant_config):
        fallback_light = self.default_light_plant_stages or {}
        extracted = (
            plant_config.get("plantStages")
            if isinstance(plant_config, dict) and plant_config.get("plantStages") is not None
            else plant_config.get("data", {}).get("plantStages")
            if isinstance(plant_config, dict) and isinstance(plant_config.get("data"), dict)
            else plant_config.get("data")
            if isinstance(plant_config, dict) and plant_config.get("data") is not None
            else plant_config
        )

        normalized_light = {}

        if isinstance(extracted, list):
            for stage in extracted:
                stage_key = stage.get("key") or stage.get("id")
                store_key = self.STAGE_KEY_MAP.get(str(stage_key).replace(" ", "").lower())
                if not store_key:
                    continue

                fallback = self._coerce_single_light_stage(fallback_light.get(store_key, {}))
                lighting_data = stage.get("lighting", {})
                raw_min = None
                raw_max = None

                if isinstance(lighting_data, dict):
                    raw_min = lighting_data.get("minLight")
                    raw_max = lighting_data.get("maxLight")

                if raw_min is None:
                    raw_min = stage.get("minLight")
                if raw_max is None:
                    raw_max = stage.get("maxLight")

                if raw_min is not None or raw_max is not None:
                    min_light = self._normalize_light_value_to_percent(
                        raw_min,
                        fallback.get("min", 0),
                        store_key,
                        "minLight",
                    )
                    max_light = self._normalize_light_value_to_percent(
                        raw_max,
                        fallback.get("max", 100),
                        store_key,
                        "maxLight",
                    )
                else:
                    intensity = str(stage.get("lighting", {}).get("intensity", "")).lower()
                    min_light, max_light = self.LIGHT_INTENSITY_PRESETS.get(
                        intensity,
                        (fallback.get("min", 0), fallback.get("max", 100)),
                    )

                if min_light > max_light:
                    _LOGGER.warning(
                        "[%s] Light stage '%s' has min > max after normalization (%s > %s). Swapping values.",
                        self.room,
                        store_key,
                        min_light,
                        max_light,
                    )
                    min_light, max_light = max_light, min_light

                normalized_light[store_key] = {
                    "min": min_light,
                    "max": max_light,
                    "phase": stage.get("lighting", {}).get("cycle", fallback.get("phase", "")),
                }

            return normalized_light or None

        if isinstance(extracted, dict):
            for raw_key, stage_data in extracted.items():
                store_key = self.STAGE_KEY_MAP.get(str(raw_key).replace(" ", "").lower())
                if not store_key or not isinstance(stage_data, dict):
                    continue

                fallback = self._coerce_single_light_stage(fallback_light.get(store_key, {}))
                min_light = self._normalize_light_value_to_percent(
                    stage_data.get("minLight"),
                    fallback.get("min", 0),
                    store_key,
                    "minLight",
                )
                max_light = self._normalize_light_value_to_percent(
                    stage_data.get("maxLight"),
                    fallback.get("max", 100),
                    store_key,
                    "maxLight",
                )

                if min_light > max_light:
                    _LOGGER.warning(
                        "[%s] Light stage '%s' has min > max after normalization (%s > %s). Swapping values.",
                        self.room,
                        store_key,
                        min_light,
                        max_light,
                    )
                    min_light, max_light = max_light, min_light

                normalized_light[store_key] = {
                    "min": min_light,
                    "max": max_light,
                    "phase": stage_data.get("phase", fallback.get("phase", "")),
                }

            return normalized_light or None

        return None

    def _coerce_single_light_stage(self, stage):
        if hasattr(stage, "to_dict"):
            return stage.to_dict()
        if isinstance(stage, dict):
            return stage
        return {
            "min": getattr(stage, "min", 0),
            "max": getattr(stage, "max", 100),
            "phase": getattr(stage, "phase", ""),
        }

    def _normalize_light_value_to_percent(self, value, fallback, stage_key, field_name):
        """Normalize light values to brightness percent (0-100).

        Wizard payloads can provide either brightness percent (0-100) or PPFD-like
        values (for example 150-800). Values above 100 are interpreted as PPFD and
        converted to percent via 10:1 scaling.
        """
        fallback_value = fallback if isinstance(fallback, (int, float)) else 0

        if value is None:
            return int(round(max(0, min(100, fallback_value))))

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "[%s] Invalid %s for stage '%s': %s. Using fallback %s%%.",
                self.room,
                field_name,
                stage_key,
                value,
                fallback_value,
            )
            return int(round(max(0, min(100, fallback_value))))

        if numeric > 100:
            converted = numeric / 10.0
            _LOGGER.info(
                "[%s] Converted %s for stage '%s' from PPFD-like value %s to brightness %s%%.",
                self.room,
                field_name,
                stage_key,
                numeric,
                round(converted, 2),
            )
            numeric = converted

        return int(round(max(0, min(100, numeric))))

    async def _get_live_plant_config(self):
        """Fetch live plant config server-side to avoid frontend CORS issues."""
        session = async_get_clientsession(self.hass)

        try:
            async with session.get(
                self.LIVE_CONFIG_URL,
                timeout=15,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "OpenGrowBox-HA/1.0",
                },
            ) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "Live plant config request failed for room %s with status %s",
                        self.room,
                        response.status,
                    )
                    await self.event_manager.emit("LogForClient", {
                        "Name": self.room,
                        "Type": "CONFIG",
                        "Message": "Could not fetch live plant config",
                        "status": response.status
                    }, haEvent=True, debug_type="WARNING")
                    return None

                return await response.json()
        except Exception as err:
            _LOGGER.error(
                "Failed to fetch live plant config for room %s: %s",
                self.room,
                err,
                exc_info=True,
            )
            await self.event_manager.emit("LogForClient", {
                "Name": self.room,
                "Type": "CONFIG",
                "Message": "Could not fetch live plant config",
                "error": str(err)
            }, haEvent=True, debug_type="WARNING")
            return None

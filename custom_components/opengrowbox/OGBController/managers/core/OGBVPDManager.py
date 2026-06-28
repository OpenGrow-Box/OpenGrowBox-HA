import asyncio
import logging
import math
from datetime import datetime, timezone
from ...utils.calcs import (calculate_avg_value, calculate_current_vpd,
                          calculate_current_vpd_with_leaf_temp, calculate_dew_point)
from ...utils.sensorUpdater import (_update_specific_number,
                                  _update_specific_sensor,
                                  update_sensor_via_service)
from ...data.OGBDataClasses.OGBPublications import OGBInitData, OGBVPDPublication, OGBModeRunPublication
from ...utils.ambient import is_ambient_room, is_not_ambient_room

_LOGGER = logging.getLogger(__name__)


class OGBVPDManager:
    """Manages Vapor Pressure Deficit (VPD) calculations and sensor data processing."""

    def __init__(self, data_store, event_manager, room, hass, prem_manager=None):
        """Initialize the VPD manager.

        Args:
            data_store: Reference to the data store
            event_manager: Reference to the event manager
            room: Room identifier
            hass: Home Assistant instance
            prem_manager: Reference to Premium manager (for analytics)
        """
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.hass = hass
        self.prem_manager = prem_manager

        # VPD determination mode
        self.vpd_determination = "LIVE"  # LIVE or INTERVAL

        # Weather data rate limiting (10 minute cooldown)
        self._weather_last_fetch = None
        self._weather_cache = None
        self._weather_min_interval = 600  # 10 minutes in seconds
        
        # 429 Rate limiting backoff
        self._weather_429_backoff_until = 0
        self._weather_429_backoff_seconds = 60  # Start with 60s backoff
        
        # 502 retry tracking
        self._weather_502_retry_count = 0
        self._weather_max_502_retries = 3
        self._weather_502_backoff_seconds = 120  # min interval after max retries exceeded

        # Sensor failure notification tracking (30 minute cooldown)
        self._sensor_failure_notifications = {}
        self._sensor_failure_cooldown = 1800  # 30 minutes in seconds

        # Register event handlers
        self.event_manager.on("VPDCreation", self.handle_new_vpd)

    async def handle_new_vpd(self, data):
        """Handle new VPD data - ORIGINAL IMPLEMENTATION"""

        controlOption = self.data_store.get("mainControl")
        if controlOption not in ["HomeAssistant", "Premium"]:
            return

        devices = self.data_store.get("devices")

        if devices is None or len(devices) == 0:
            _LOGGER.debug(f"NO Sensors Found to calc VPD in {self.room}")
            return

        temperatures = []
        humidities = []

        for dev in devices:
            # Nur initialisierte Sensor-Objekte prüfen
            if hasattr(dev, 'sensorReadings') and dev.isInitialized:
                air_context = dev.getSensorsByContext("air")

                has_temp = "temperature" in air_context
                has_hum = "humidity" in air_context

                tempSensors = []
                humSensors = []

                if has_temp:
                    tempSensors = air_context["temperature"]
                    for t in tempSensors:
                        try:
                            value = float(t.get("state"))
                            name = t.get("entity_id")
                            label = t.get("label")
                            
                            # Check for impossible temperature values
                            if value <= 0 or value > 40:
                                _LOGGER.warning(
                                    f"CRITICAL: Sensor {name} reports impossible temperature "
                                    f"of {value}°C - likely sensor failure!"
                                )
                                await self._notify_sensor_failure(name, "temperature", value)
                                continue  # Skip this sensor
                            
                            temperatures.append({"entity_id":name,"value":value,"label":label})
                        except (ValueError, TypeError):
                            _LOGGER.error(f"Ungültiger Temperaturwert in {t.get('entity_id')}: {t.get('state')}")

                if has_hum:
                    humSensors = air_context["humidity"]
                    for h in humSensors:
                        try:
                            value = float(h.get("state"))
                            name = h.get("entity_id")
                            label = h.get("label")
                            
                            # Check for impossible humidity values
                            if value <= 0 or value > 100:
                                _LOGGER.warning(
                                    f"CRITICAL: Sensor {name} reports impossible humidity "
                                    f"of {value}% - likely sensor failure!"
                                )
                                await self._notify_sensor_failure(name, "humidity", value)
                                continue  # Skip this sensor
                            
                            humidities.append({"entity_id":name,"value":value,"label":label})
                        except (ValueError, TypeError):
                            _LOGGER.error(f"Ungültiger Feuchtigkeitswert in {h.get('entity_id')}: {h.get('state')}")

        _LOGGER.warning(f"{self.room} VPD-CALC VALUES: Temp:{temperatures} --- HUMS:{humidities}")

        self.data_store.setDeep("workData.temperature",temperatures)
        self.data_store.setDeep("workData.humidity",humidities)
        
        # Durchschnittswerte asynchron berechnen (VOR Leaf Sensor Logik!)
        avgTemp = calculate_avg_value(temperatures)
        self.data_store.setDeep("tentData.temperature", avgTemp)
        avgHum = calculate_avg_value(humidities)
        self.data_store.setDeep("tentData.humidity", avgHum)
        
        # NEW: Read leaf temperature sensors
        leafTemperatures = []
        for dev in devices:
            if hasattr(dev, 'sensorReadings') and dev.isInitialized:
                leaf_context = dev.getSensorsByContext("leaf")
                if leaf_context:
                    _LOGGER.warning(
                        f"{self.room}: 🍃 DEBUG getSensorsByContext('leaf') for {dev.deviceName}: "
                        f"{leaf_context}"
                    )
                if "temperature" in leaf_context:
                    _LOGGER.warning(
                        f"{self.room}: 🍃 FOUND temperature in leaf context: "
                        f"{leaf_context['temperature']}"
                    )
                    for t in leaf_context["temperature"]:
                        try:
                            value = float(t.get("state"))
                            name = t.get("entity_id")
                            _LOGGER.warning(
                                f"{self.room}: 🍃 Reading leaf sensor {name}: {value}°C"
                            )
                            if value > 0 and value <= 40:
                                leafTemperatures.append({"entity_id":name,"value":value})
                        except (ValueError, TypeError):
                            pass
        
        # Calculate leaf temperature average if sensors available
        # Skip for ambient room - leaf sensors only make sense for grow rooms
        if is_not_ambient_room(self.room):
            leafTemp = None
            if leafTemperatures:
                leafTemp = calculate_avg_value(leafTemperatures)
                self.data_store.setDeep("tentData.leafTemperature", leafTemp)
                _LOGGER.warning(
                    f"{self.room}: 🍃 Leaf sensor detected | "
                    f"Sensors: {len(leafTemperatures)} | Leaf: {leafTemp}°C"
                )
                
                # NEW: Calculate and update leaf temperature offset automatically
                if avgTemp != "unavailable" and avgTemp is not None:
                    calculated_offset = round(leafTemp - avgTemp, 1)
                    current_offset = self.data_store.getDeep("tentData.leafTempOffset")
                    
                    # Hysteresis: only update if offset changed by more than 0.2°C
                    if current_offset is None or abs(calculated_offset - float(current_offset)) > 0.2:
                        _LOGGER.warning(
                            f"{self.room}: 🍃 Auto offset | "
                            f"Air: {avgTemp}°C | Leaf: {leafTemp}°C | "
                            f"Offset: {calculated_offset}°C | "
                            f"Previous: {current_offset}°C"
                        )
                        
                        try:
                            await _update_specific_number(
                                "ogb_leaftemp_offset_",
                                self.room,
                                calculated_offset,
                                self.hass
                            )
                            _LOGGER.warning(
                                f"{self.room}: 🍃 Updated number.ogb_leaftemp_offset_ "
                                f"to {calculated_offset}°C"
                            )
                        except Exception as e:
                            _LOGGER.warning(
                                f"{self.room}: 🍃 Failed to update leaf offset number: {e}"
                            )
                    else:
                        _LOGGER.debug(
                            f"{self.room}: 🍃 Offset unchanged ({calculated_offset}°C), "
                            f"skipping number update (hysteresis: 0.2°C)"
                        )
                else:
                    _LOGGER.warning(
                        f"{self.room}: 🍃 Cannot calculate offset - air temp unavailable"
                    )
            else:
                self.data_store.setDeep("tentData.leafTemperature", None)
                current_offset = self.data_store.getDeep("tentData.leafTempOffset")
                if current_offset is not None:
                    _LOGGER.debug(
                        f"{self.room}: 🍃 No leaf sensor, using manual offset: {current_offset}°C"
                    )

        # Taupunkt asynchron berechnen
        avgDew = calculate_dew_point(avgTemp, avgHum) if avgTemp != "unavailable" and avgHum != "unavailable" else None
        self.data_store.setDeep("tentData.dewpoint", avgDew if avgDew else "unavailable")

        lastVpd = self.data_store.getDeep("vpd.current")
        
        # For ambient room, always use manual offset (no leaf sensor support)
        if is_ambient_room(self.room):
            leafTempOffset = self.data_store.getDeep("tentData.leafTempOffset")
            currentVPD = calculate_current_vpd(avgTemp, avgHum, leafTempOffset)
        elif leafTemp is not None:
            currentVPD = calculate_current_vpd_with_leaf_temp(avgTemp, avgHum, leafTemp)
        else:
            leafTempOffset = self.data_store.getDeep("tentData.leafTempOffset")
            currentVPD = calculate_current_vpd(avgTemp, avgHum, leafTempOffset)

        # Convert "unavailable" to None for publications
        def convert_value(val):
            return None if val == "unavailable" else val

        if isinstance(data, OGBInitData):
            #_LOGGER.debug(f"OGBInitData erkannt: {data}")
            return
        else:
            # Spezifische Aktion für OGBEventPublication
            if currentVPD != lastVpd:
                self.data_store.setDeep("vpd.current", currentVPD)
                vpdPub = OGBVPDPublication(Name=self.room, VPD=currentVPD, AvgTemp=convert_value(avgTemp), AvgHum=convert_value(avgHum), AvgDew=convert_value(avgDew))
                await update_sensor_via_service(self.room,vpdPub,self.hass)
                _LOGGER.debug(f"New-VPD: {vpdPub} newStoreVPD:{currentVPD}, lastStoreVPD:{lastVpd}")

                # Submit VPD analytics to Premium API if connected
                if hasattr(self, 'prem_manager') and self.prem_manager and hasattr(self.prem_manager, 'ogb_ws'):
                    try:
                        if self.prem_manager.ogb_ws and self.prem_manager.is_logged_in:
                            await self.prem_manager.ogb_ws.submit_analytics({
                                "type": "vpd",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "room": self.room,
                                "vpd": currentVPD if currentVPD != "unavailable" else None,
                                "temperature": avgTemp if avgTemp != "unavailable" else None,
                                "humidity": avgHum if avgHum != "unavailable" else None,
                                "dewpoint": avgDew if avgDew else None,
                                "target_vpd": self.data_store.getDeep("vpd.targeted") if self.data_store.get("tentMode") == "VPD Target" else self.data_store.getDeep("vpd.perfection"),
                            })
                    except Exception as e:
                        _LOGGER.debug(f"📊 {self.room} VPD analytics submission failed: {e}")

                currentMode = self.data_store.get("tentMode")
                tentMode = OGBModeRunPublication(currentMode=currentMode)

                currentMode = self.data_store.get("tentMode")
                tentMode = OGBModeRunPublication(currentMode=currentMode)

                if is_ambient_room(self.room):
                    _LOGGER.debug(f"📡 {self.room} AmbientData emitted: VPD={currentVPD}, Temp={convert_value(avgTemp)}, Hum={convert_value(avgHum)}")
                    await self.event_manager.emit("AmbientData",vpdPub,haEvent=True)
                    # Also emit selectActionMode for ambient rooms so mode manager processes VPD changes
                    await self.event_manager.emit("selectActionMode",tentMode)
                    await self.get_weather_data()
                    return

                await self.event_manager.emit("selectActionMode",tentMode)
                await self.event_manager.emit("LogForClient",vpdPub,haEvent=True, debug_type="DEBUG")

                # GROW DATA - DataRelease is emitted by OGBActionManager.publicationActionHandler()
                # after actual device actions are taken (NOT on every VPD calculation)
                #await self.event_manager.emit("DataRelease",vpdPub)

                return vpdPub

            else:
                vpdPub = OGBVPDPublication(Name=self.room, VPD=currentVPD, AvgTemp=convert_value(avgTemp), AvgHum=convert_value(avgHum), AvgDew=convert_value(avgDew))
                _LOGGER.debug(f"Same-VPD: {vpdPub} currentVPD:{currentVPD}, lastStoreVPD:{lastVpd}")
                await update_sensor_via_service(self.room,vpdPub,self.hass)

    async def _notify_sensor_failure(self, entity_id: str, sensor_type: str, value: float):
        """Send critical notification for sensor with impossible values.
        
        Args:
            entity_id: The sensor entity ID
            sensor_type: "temperature" or "humidity"
            value: The impossible value
        """
        now = datetime.now(timezone.utc).timestamp()
        
        # Check cooldown (30 minutes)
        last_notified = self._sensor_failure_notifications.get(entity_id, 0)
        if now - last_notified < self._sensor_failure_cooldown:
            return
        
        # Update last notification time
        self._sensor_failure_notifications[entity_id] = now
        
        # Build notification message
        if sensor_type == "temperature":
            unit = "°C"
            limit_text = "valid range: 0-40°C"
        else:
            unit = "%"
            limit_text = "valid range: 0-100%"
        
        message = (
            f"🚨 CRITICAL SENSOR FAILURE\n\n"
            f"Sensor: {entity_id}\n"
            f"Type: {sensor_type.title()}\n"
            f"Value: {value}{unit} (IMPOSSIBLE)\n"
            f"{limit_text}\n\n"
            f"Please check sensor connection immediately!"
        )
        
        try:
            # Try to get notificator from main controller
            if hasattr(self, 'notificator') and self.notificator:
                await self.notificator.critical(
                    message=message,
                    title=f"OGB {self.room}: {sensor_type.title()} Sensor Failure"
                )
                _LOGGER.debug(f"Sent critical notification for failed sensor {entity_id}")
            else:
                _LOGGER.warning(f"No notificator available for sensor failure alert: {entity_id}")
        except Exception as e:
            _LOGGER.error(f"Failed to send sensor failure notification: {e}")

    async def initialize_vpd_data(self, init_data):
        """Initialize VPD calculations from sensor data.

        Args:
            init_data: Initialization data flag

        Returns:
            OGBVPDPublication or None
        """
        if init_data is not True:
            return

        devices = self.data_store.get("devices")
        if devices is None or len(devices) == 0:
            _LOGGER.warning(
                f"NO Sensors Found to calc VPD in {self.room} Room. NOTE: Do not forgett to create an OGB-Ambient Room if you ned an Ambient VPD"
            )
            return

        temperatures = []
        humidities = []
        for dev in devices:
            # Only process initialized sensor objects
            if hasattr(dev, 'sensorReadings') and dev.isInitialized:
                air_context = dev.getSensorsByContext("air")

                has_temp = "temperature" in air_context
                has_hum = "humidity" in air_context

                tempSensors = []
                humSensors = []

                if has_temp:
                    tempSensors = air_context["temperature"]
                    for t in tempSensors:
                        try:
                            value = float(t.get("state"))
                            name = t.get("entity_id")
                            label = t.get("label")
                            
                            # Check for impossible temperature values
                            if value <= 0 or value > 40:
                                _LOGGER.warning(
                                    f"CRITICAL: Sensor {name} reports impossible temperature "
                                    f"of {value}°C - likely sensor failure!"
                                )
                                await self._notify_sensor_failure(name, "temperature", value)
                                continue
                            
                            temperatures.append(
                                {"entity_id": name, "value": value, "label": label}
                            )
                        except (ValueError, TypeError):
                            _LOGGER.error(
                                f"Ungültiger Temperaturwert in {t.get('entity_id')}: {t.get('state')}"
                            )

                if has_hum:
                    humSensors = air_context["humidity"]
                    for h in humSensors:
                        try:
                            value = float(h.get("state"))
                            name = h.get("entity_id")
                            label = h.get("label")
                            
                            # Check for impossible humidity values
                            if value <= 0 or value > 100:
                                _LOGGER.warning(
                                    f"CRITICAL: Sensor {name} reports impossible humidity "
                                    f"of {value}% - likely sensor failure!"
                                )
                                await self._notify_sensor_failure(name, "humidity", value)
                                continue
                            
                            humidities.append(
                                {"entity_id": name, "value": value, "label": label}
                            )
                        except (ValueError, TypeError):
                            _LOGGER.error(
                                f"Ungültiger Feuchtigkeitswert in {h.get('entity_id')}: {h.get('state')}"
                            )

        # Store work data
        self.data_store.setDeep("workData.temperature", temperatures)
        self.data_store.setDeep("workData.humidity", humidities)
        
        # Calculate averages FIRST (needed for leaf offset calculation)
        avgTemp = calculate_avg_value(temperatures)
        self.data_store.setDeep("tentData.temperature", avgTemp)

        avgHum = calculate_avg_value(humidities)
        self.data_store.setDeep("tentData.humidity", avgHum)
        
        # NEW: Read leaf temperature sensors (second path)
        leafTemperatures = []
        for dev in devices:
            if hasattr(dev, 'sensorReadings') and dev.isInitialized:
                leaf_context = dev.getSensorsByContext("leaf")
                if "temperature" in leaf_context:
                    for t in leaf_context["temperature"]:
                        try:
                            value = float(t.get("state"))
                            if value > 0 and value <= 40:
                                leafTemperatures.append({"entity_id": t.get("entity_id"), "value": value})
                        except (ValueError, TypeError):
                            pass
        
        # Calculate leaf temperature average if sensors available
        # Skip for ambient room - leaf sensors only make sense for grow rooms
        leafTemp = None
        if is_not_ambient_room(self.room):
            if leafTemperatures:
                leafTemp = calculate_avg_value(leafTemperatures)
                self.data_store.setDeep("tentData.leafTemperature", leafTemp)
                _LOGGER.warning(
                    f"{self.room}: 🍃 Leaf sensor detected | "
                    f"Sensors: {len(leafTemperatures)} | Leaf: {leafTemp}°C"
                )
                
                # NEW: Calculate and update leaf temperature offset automatically
                if avgTemp != "unavailable" and avgTemp is not None:
                    calculated_offset = round(leafTemp - avgTemp, 1)
                    current_offset = self.data_store.getDeep("tentData.leafTempOffset")
                    
                    # Hysteresis: only update if offset changed by more than 0.2°C
                    if current_offset is None or abs(calculated_offset - float(current_offset)) > 0.2:
                        _LOGGER.warning(
                            f"{self.room}: 🍃 Auto offset | "
                            f"Air: {avgTemp}°C | Leaf: {leafTemp}°C | "
                            f"Offset: {calculated_offset}°C | "
                            f"Previous: {current_offset}°C"
                        )
                        
                        try:
                            await _update_specific_number(
                                "ogb_leaftemp_offset_",
                                self.room,
                                calculated_offset,
                                self.hass
                            )
                            _LOGGER.warning(
                                f"{self.room}: 🍃 Updated number.ogb_leaftemp_offset_ "
                                f"to {calculated_offset}°C"
                            )
                        except Exception as e:
                            _LOGGER.warning(
                                f"{self.room}: 🍃 Failed to update leaf offset number: {e}"
                            )
                    else:
                        _LOGGER.debug(
                            f"{self.room}: 🍃 Offset unchanged ({calculated_offset}°C), "
                            f"skipping number update (hysteresis: 0.2°C)"
                        )
                else:
                    _LOGGER.warning(
                        f"{self.room}: 🍃 Cannot calculate offset - air temp unavailable"
                    )
            else:
                self.data_store.setDeep("tentData.leafTemperature", None)
                current_offset = self.data_store.getDeep("tentData.leafTempOffset")
                if current_offset is not None:
                    _LOGGER.debug(
                        f"{self.room}: 🍃 No leaf sensor, using manual offset: {current_offset}°C"
                    )
        else:
            self.data_store.setDeep("tentData.leafTemperature", None)

        avgDew = (
            calculate_dew_point(avgTemp, avgHum)
            if avgTemp != "unavailable" and avgHum != "unavailable"
            else "unavailable"
        )
        self.data_store.setDeep("tentData.dewpoint", avgDew)

        # Calculate VPD
        lastVpd = self.data_store.getDeep("vpd.current")
        
        # For ambient room, always use manual offset (no leaf sensor support)
        if is_ambient_room(self.room):
            leafTempOffset = self.data_store.getDeep("tentData.leafTempOffset")
            currentVPD = calculate_current_vpd(avgTemp, avgHum, leafTempOffset)
        elif leafTemp is not None:
            currentVPD = calculate_current_vpd_with_leaf_temp(avgTemp, avgHum, leafTemp)
        else:
            leafTempOffset = self.data_store.getDeep("tentData.leafTempOffset")
            currentVPD = calculate_current_vpd(avgTemp, avgHum, leafTempOffset)

        # Validate VPD value - must be positive and within reasonable range
        if currentVPD is None:
            _LOGGER.error(f"VPD calculation returned None for {self.room}")
            return
        if currentVPD <= 0:
            _LOGGER.error(f"Invalid VPD value: {currentVPD} (must be positive) in {self.room}")
            return
        if currentVPD > 5.0:
            _LOGGER.error(f"VPD value too high: {currentVPD} (possible sensor error) in {self.room}")
            return

        # Create VPD publication - convert "unavailable" to None
        def convert_value(val):
            return None if val == "unavailable" else val

        vpdPub = OGBVPDPublication(
            Name=self.room,
            VPD=currentVPD,
            AvgTemp=convert_value(avgTemp),
            AvgHum=convert_value(avgHum),
            AvgDew=convert_value(avgDew),
        )

        # Update sensor via service
        await update_sensor_via_service(self.room, vpdPub, self.hass)

        _LOGGER.debug(
            f"New-VPD: {vpdPub} newStoreVPD:{currentVPD}, lastStoreVPD:{lastVpd}"
        )

        # Handle ambient room special case
        if is_ambient_room(self.room):
            _LOGGER.warning(f"📡 {self.room} AmbientData emitted (init): VPD={currentVPD}, Temp={convert_value(avgTemp)}, Hum={convert_value(avgHum)}")
            await self.event_manager.emit("AmbientData", vpdPub, haEvent=True)
            await self.get_weather_data()
            return

        # Update mode and emit events
        currentMode = self.data_store.get("tentMode")
        tentMode = OGBModeRunPublication(currentMode=currentMode)
        _LOGGER.debug(
            f"Action Init for {self.room} with {tentMode} "
        )
        await self.event_manager.emit("selectActionMode", tentMode)
        await self.event_manager.emit("LogForClient", vpdPub, haEvent=True, debug_type="DEBUG")

        return vpdPub

    async def get_weather_data(self):
        """Fetch weather data from Open-Meteo API with rate limiting and retry logic."""
        import time

        now = time.time()

        # Check if we're in 429 backoff period
        if now < self._weather_429_backoff_until:
            wait_time = int(self._weather_429_backoff_until - now)
            _LOGGER.warning(f"🌤️ {self.room} In 429 backoff period, skipping weather fetch (wait {wait_time}s)")
            await self._emit_cached_weather()
            return

        # Check if we can use cached data (within cooldown period)
        if self._weather_last_fetch and self._weather_cache:
            elapsed = now - self._weather_last_fetch
            if elapsed < self._weather_min_interval:
                _LOGGER.debug(f"🌤️ {self.room} Using cached weather data (age: {int(elapsed)}s)")
                await self._emit_cached_weather()
                return

        # Check if we need to fetch (allow one fetch even without cache)
        can_fetch = True
        if self._weather_last_fetch and not self._weather_cache:
            elapsed = now - self._weather_last_fetch
            if elapsed < self._weather_502_backoff_seconds:
                can_fetch = False
                _LOGGER.warning(
                    f"🌤️ {self.room} Skipping weather fetch - too soon after failed attempt "
                    f"(wait {int(self._weather_502_backoff_seconds - elapsed)}s)"
                )

        if not can_fetch:
            await self._emit_cached_weather()
            return

        try:
            lat = self.hass.config.latitude
            lon = self.hass.config.longitude

            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m&timezone=auto"
            )

            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                data = await self._fetch_with_retries(session, url)

            if data is None:
                # Retries exhausted or non-retryable error; fallback handled inside
                return

            current = data.get("current", {})
            temperature = round(current.get("temperature_2m", 20.0), 1)
            humidity = current.get("relative_humidity_2m", 60)

            weather_data = {"temperature": temperature, "humidity": humidity}

            # Cache the successful result
            self._weather_cache = weather_data
            self._weather_last_fetch = now
            # Reset backoff on success
            self._weather_429_backoff_seconds = 60
            self._weather_429_backoff_until = 0
            self._weather_502_retry_count = 0
            self._weather_502_backoff_seconds = 120

            _LOGGER.debug(f"🌤️ {self.room} Open-Meteo: {temperature}°C, {humidity}% (cached)")
            await self.event_manager.emit("OutsiteData", weather_data, haEvent=True)

        except asyncio.TimeoutError:
            _LOGGER.warning(f"🌤️ {self.room} Timeout fetching Open-Meteo data")
            self._weather_last_fetch = now
            await self._emit_cached_weather()
        except aiohttp.ClientError as e:
            _LOGGER.warning(f"🌤️ {self.room} Open-Meteo connection error: {e}")
            self._weather_last_fetch = now
            await self._emit_cached_weather()
        except Exception as e:
            _LOGGER.error(f"🌤️ {self.room} Fetch Error Open-Meteo: {e}")
            self._weather_last_fetch = now
            await self._emit_cached_weather()

    async def _emit_cached_weather(self):
        """Emit cached weather data if available."""
        if self._weather_cache:
            await self.event_manager.emit("OutsiteData", self._weather_cache, haEvent=True)

    async def _fetch_with_retries(self, session, url):
        """Fetch and parse Open-Meteo data with retry loop and rate-limit handling.

        Returns the parsed JSON dict on success, or None if all retries are exhausted
        or a non-retryable response is received.
        """
        import time

        now = time.time()
        import aiohttp

        for attempt in range(1, self._weather_max_502_retries + 1):
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data

                    if response.status == 429:
                        self._weather_last_fetch = now
                        self._weather_429_backoff_until = now + self._weather_429_backoff_seconds
                        _LOGGER.error(
                            f"🌤️ {self.room} Open-Meteo Rate Limited (429). "
                            f"Backing off for {self._weather_429_backoff_seconds}s"
                        )
                        self._weather_429_backoff_seconds = min(self._weather_429_backoff_seconds * 2, 480)
                        await self._emit_cached_weather()
                        return None

                    if response.status == 502:
                        if attempt < self._weather_max_502_retries:
                            wait_time = 2 ** attempt  # 2s, 4s, 8s
                            _LOGGER.warning(
                                f"🌤️ {self.room} Open-Meteo Server Error (502). "
                                f"Retry {attempt}/{self._weather_max_502_retries} in {wait_time}s"
                            )
                            await asyncio.sleep(wait_time)
                            continue

                        _LOGGER.error(
                            f"🌤️ {self.room} Open-Meteo Server Error (502). "
                            f"Max retries ({self._weather_max_502_retries}) exceeded. "
                            f"Next attempt in {self._weather_502_backoff_seconds}s"
                        )
                        self._weather_last_fetch = now
                        self._weather_502_retry_count = 0
                        self._weather_502_backoff_seconds = min(self._weather_502_backoff_seconds * 2, 900)
                        await self._emit_cached_weather()
                        return None

                    # Any other non-200 status
                    _LOGGER.error(f"🌤️ {self.room} Open-Meteo API Error: {response.status}")
                    self._weather_last_fetch = now
                    await self._emit_cached_weather()
                    return None

            except aiohttp.ClientError as e:
                _LOGGER.warning(
                    f"🌤️ {self.room} Open-Meteo connection error (attempt {attempt}): {e}"
                )
                if attempt < self._weather_max_502_retries:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                    continue
                _LOGGER.error(
                    f"🌤️ {self.room} Open-Meteo connection failed after "
                    f"{self._weather_max_502_retries} attempts"
                )
                self._weather_last_fetch = now
                await self._emit_cached_weather()
                return None

        return None

    def update_vpd_determination(self, value):
        """Update VPD determination mode."""
        current_main_control = self.data_store.get("vpdDetermination")
        if current_main_control != value:
            self.data_store.set("vpdDetermination", value)
            self.vpd_determination = value
            # Emit event for determination change with error handling
            async def _emit_with_retry():
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        await self.event_manager.emit("VPDDeterminationChange", value)
                        return
                    except Exception as e:
                        if attempt < max_retries - 1:
                            _LOGGER.warning(f"Failed to emit VPDDeterminationChange (attempt {attempt + 1}), retrying: {e}")
                            await asyncio.sleep(0.1 * (attempt + 1))
                        else:
                            _LOGGER.error(f"Failed to emit VPDDeterminationChange after {max_retries} attempts: {e}")
            
            asyncio.create_task(_emit_with_retry())

    def get_vpd_status(self):
        """Get current VPD status information."""
        mode = self.data_store.get("tentMode")
        if mode == "VPD Target":
            target_vpd = self.data_store.getDeep("vpd.targeted")
        elif mode == "VPD Perfection":
            target_vpd = self.data_store.getDeep("vpd.perfection")
        else:
            target_vpd = None
        return {
            "current_vpd": self.data_store.getDeep("vpd.current"),
            "target_vpd": target_vpd,
            "vpd_range": self.data_store.getDeep("vpd.range"),
            "perfection": self.data_store.getDeep("vpd.perfection"),
            "tolerance": self.data_store.getDeep("vpd.tolerance"),
            "determination_mode": self.vpd_determination,
        }

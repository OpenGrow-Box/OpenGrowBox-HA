import asyncio
import logging
import math
from datetime import datetime, timezone
from ...utils.calcs import calculate_current_vpd, calculate_avg_value, calculate_dew_point
from ...utils.sensorUpdater import update_sensor_via_service
from ...data.OGBDataClasses.OGBPublications import OGBInitData, OGBVPDPublication, OGBModeRunPublication
from ...data.OGBDataClasses.OGBPublications import OGBVPDPublication, OGBModeRunPublication, OGBInitData
from ...utils.calcs import (calculate_avg_value, calculate_current_vpd,
                          calculate_dew_point)
from ...utils.sensorUpdater import (_update_specific_sensor,
                                  update_sensor_via_service)

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

        # Register event handlers
        self.event_manager.on("VPDCreation", self.handle_new_vpd)

    async def handle_new_vpd(self, data):
        """Handle new VPD data - ORIGINAL IMPLEMENTATION"""

        controlOption = self.data_store.get("mainControl")
        if controlOption not in ["HomeAssistant", "Premium"]:
            return

        devices = self.data_store.get("devices")

        if devices is None or len(devices) == 0:
            _LOGGER.warning(f"NO Sensors Found to calc VPD in {self.room}")
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
                            humidities.append({"entity_id":name,"value":value,"label":label})
                        except (ValueError, TypeError):
                            _LOGGER.error(f"Ungültiger Feuchtigkeitswert in {h.get('entity_id')}: {h.get('state')}")

        _LOGGER.warning(f"{self.room} VPD-CALC VALUES: Temp:{temperatures} --- HUMS:{humidities}")

        self.data_store.setDeep("workData.temperature",temperatures)
        self.data_store.setDeep("workData.humidity",humidities)
        leafTempOffset = self.data_store.getDeep("tentData.leafTempOffset")

        # Durchschnittswerte asynchron berechnen
        avgTemp = calculate_avg_value(temperatures)
        self.data_store.setDeep("tentData.temperature", avgTemp)
        avgHum = calculate_avg_value(humidities)
        self.data_store.setDeep("tentData.humidity", avgHum)

        # Taupunkt asynchron berechnen
        avgDew = calculate_dew_point(avgTemp, avgHum) if avgTemp != "unavailable" and avgHum != "unavailable" else None
        self.data_store.setDeep("tentData.dewpoint", avgDew if avgDew else "unavailable")

        lastVpd = self.data_store.getDeep("vpd.current")
        currentVPD = calculate_current_vpd(avgTemp, avgHum, leafTempOffset)

        # Convert "unavailable" to None for publications
        def convert_value(val):
            return None if val == "unavailable" else val

        if isinstance(data, OGBInitData):
            #_LOGGER.info(f"OGBInitData erkannt: {data}")
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
                                "target_vpd": self.data_store.getDeep("vpd.target"),
                            })
                    except Exception as e:
                        _LOGGER.debug(f"📊 {self.room} VPD analytics submission failed: {e}")

                currentMode = self.data_store.get("tentMode")
                tentMode = OGBModeRunPublication(currentMode=currentMode)

                currentMode = self.data_store.get("tentMode")
                tentMode = OGBModeRunPublication(currentMode=currentMode)

                if self.room.lower() == "ambient":
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

        # Calculate averages
        leafTempOffset = self.data_store.getDeep("tentData.leafTempOffset")
        avgTemp = calculate_avg_value(temperatures)
        self.data_store.setDeep("tentData.temperature", avgTemp)

        avgHum = calculate_avg_value(humidities)
        self.data_store.setDeep("tentData.humidity", avgHum)

        avgDew = (
            calculate_dew_point(avgTemp, avgHum)
            if avgTemp != "unavailable" and avgHum != "unavailable"
            else "unavailable"
        )
        self.data_store.setDeep("tentData.dewpoint", avgDew)

        # Calculate VPD
        lastVpd = self.data_store.getDeep("vpd.current")
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
        if self.room.lower() == "ambient":
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
            if self._weather_cache:
                await self.event_manager.emit("OutsiteData", self._weather_cache, haEvent=True)
            return
        
        # Check if we can use cached data (within cooldown period)
        if self._weather_last_fetch and self._weather_cache:
            elapsed = now - self._weather_last_fetch
            if elapsed < self._weather_min_interval:
                _LOGGER.debug(f"🌤️ {self.room} Using cached weather data (age: {int(elapsed)}s)")
                await self.event_manager.emit("OutsiteData", self._weather_cache, haEvent=True)
                return
        
        # Check if we need to fetch (allow one fetch even without cache)
        can_fetch = True
        if self._weather_last_fetch and not self._weather_cache:
            elapsed = now - self._weather_last_fetch
            if elapsed < 120:  # Increased to 120 seconds between failed attempts
                can_fetch = False
                _LOGGER.warning(f"🌤️ {self.room} Skipping weather fetch - too soon after failed attempt (wait {int(120 - elapsed)}s)")
        
        if not can_fetch:
            return
        
        try:
            lat = self.hass.config.latitude
            lon = self.hass.config.longitude

            url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m&timezone=auto"

            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()

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

                        _LOGGER.debug(f"🌤️ {self.room} Open-Meteo: {temperature}°C, {humidity}% (cached)")
                        await self.event_manager.emit("OutsiteData", weather_data, haEvent=True)
                    
                    elif response.status == 429:
                        # Rate limited - implement exponential backoff
                        self._weather_last_fetch = now
                        self._weather_429_backoff_until = now + self._weather_429_backoff_seconds
                        _LOGGER.error(
                            f"🌤️ {self.room} Open-Meteo Rate Limited (429). "
                            f"Backing off for {self._weather_429_backoff_seconds}s"
                        )
                        # Double the backoff time for next attempt (max 480s = 8 min)
                        self._weather_429_backoff_seconds = min(self._weather_429_backoff_seconds * 2, 480)
                        # Use cached data if available
                        if self._weather_cache:
                            await self.event_manager.emit("OutsiteData", self._weather_cache, haEvent=True)
                    
                    elif response.status == 502:
                        # Server error - retry with exponential backoff
                        self._weather_last_fetch = now
                        self._weather_502_retry_count += 1
                        
                        if self._weather_502_retry_count <= self._weather_max_502_retries:
                            wait_time = 2 ** self._weather_502_retry_count  # 2s, 4s, 8s
                            _LOGGER.warning(
                                f"🌤️ {self.room} Open-Meteo Server Error (502). "
                                f"Retry {self._weather_502_retry_count}/{self._weather_max_502_retries} in {wait_time}s"
                            )
                            await asyncio.sleep(wait_time)
                            # Retry recursively (will increment retry count)
                            await self.get_weather_data()
                        else:
                            _LOGGER.error(
                                f"🌤️ {self.room} Open-Meteo Server Error (502). "
                                f"Max retries ({self._weather_max_502_retries}) exceeded"
                            )
                            self._weather_502_retry_count = 0
                            # Use cached data if available
                            if self._weather_cache:
                                await self.event_manager.emit("OutsiteData", self._weather_cache, haEvent=True)
                    
                    else:
                        _LOGGER.error(f"🌤️ {self.room} Open-Meteo API Error: {response.status}")
                        self._weather_last_fetch = now

        except asyncio.TimeoutError:
            _LOGGER.error(f"🌤️ {self.room} Timeout fetching Open-Meteo data")
            self._weather_last_fetch = now
        except Exception as e:
            _LOGGER.error(f"🌤️ {self.room} Fetch Error Open-Meteo: {e}")
            self._weather_last_fetch = now

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
        return {
            "current_vpd": self.data_store.getDeep("vpd.current"),
            "target_vpd": self.data_store.getDeep("vpd.targeted"),
            "vpd_range": self.data_store.getDeep("vpd.range"),
            "perfection": self.data_store.getDeep("vpd.perfection"),
            "tolerance": self.data_store.getDeep("vpd.tolerance"),
            "determination_mode": self.vpd_determination,
        }

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

                if self.room.lower() == "ambient":
                    _LOGGER.debug(f"New-Ambient-VPD: {vpdPub} newStoreVPD:{currentVPD}, lastStoreVPD:{lastVpd}")
                    await self.event_manager.emit("AmbientData",vpdPub,haEvent=True)
                    await self.get_weather_data()
                    return

                await self.event_manager.emit("selectActionMode",tentMode)
                await self.event_manager.emit("LogForClient",vpdPub,haEvent=True)

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

        if currentVPD == 0.0 or currentVPD == 0:
            _LOGGER.error(f"VPD 0.0 FOUND {self.room}")
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
        await self.event_manager.emit("LogForClient", vpdPub, haEvent=True)

        return vpdPub

    async def get_weather_data(self):
        """Fetch weather data from Open-Meteo API."""
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

                        _LOGGER.debug(
                            f"{self.room} Open-Meteo: {temperature}°C, {humidity}%"
                        )
                        await self.event_manager.emit(
                            "OutsiteData",
                            {"temperature": temperature, "humidity": humidity},
                            haEvent=True,
                        )
                    else:
                        _LOGGER.error(f"Open-Meteo API Error: {response.status}")
                        return None, None

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout Open-Meteo")
            return 20.0, 60.0
        except Exception as e:
            _LOGGER.error(f"Fetch Error Open-Meteo: {e}")
            return 20.0, 60.0

    def update_vpd_determination(self, value):
        """Update VPD determination mode."""
        current_main_control = self.data_store.get("vpdDetermination")
        if current_main_control != value:
            self.data_store.set("vpdDetermination", value)
            self.vpd_determination = value
            # Emit event for determination change
            asyncio.create_task(
                self.event_manager.emit("VPDDeterminationChange", value)
            )

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

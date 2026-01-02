import logging
import re
from datetime import date, datetime, time
from typing import Any, List, Optional, Union

_LOGGER = logging.getLogger(__name__)


async def update_sensor_via_service(room, vpdPub, hass):
    vpd_value = vpdPub.VPD
    temp_value = vpdPub.AvgTemp
    hum_value = vpdPub.AvgHum
    dew_value = vpdPub.AvgDew
    vpd_entity = f"sensor.ogb_currentvpd_{room.lower()}"
    avgTemp_entity = f"sensor.ogb_avgtemperature_{room.lower()}"
    avgHum_entity = f"sensor.ogb_avghumidity_{room.lower()}"
    avgDew_entity = f"sensor.ogb_avgdewpoint_{room.lower()}"

    _LOGGER.info(f"🔍 {room} UPDATE SENSORS: VPD={vpd_value}, Temp={temp_value}, Hum={hum_value}, Dew={dew_value}")

    try:
        # Überprüfe, ob der Wert gültig ist
        new_vpd_value = (
            vpd_value if vpd_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.info(f"🔍 {room} Calling update_sensor for {vpd_entity} with value {new_vpd_value}")
        # Rufe den Service auf
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": vpd_entity, "value": new_vpd_value},
            blocking=True,
        )
        _LOGGER.info(f"✅ {room} VPD service call completed")
        
        new_temp_value = (
            temp_value if temp_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.info(f"🔍 {room} Calling update_sensor for {avgTemp_entity} with value {new_temp_value}")
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": avgTemp_entity, "value": new_temp_value},
            blocking=True,
        )
        _LOGGER.info(f"✅ {room} AvgTemp service call completed")
        
        new_hum_value = (
            hum_value if hum_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.info(f"🔍 {room} Calling update_sensor for {avgHum_entity} with value {new_hum_value}")
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": avgHum_entity, "value": new_hum_value},
            blocking=True,
        )
        _LOGGER.info(f"✅ {room} AvgHum service call completed")
        
        new_dew_value = (
            dew_value if dew_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.info(f"🔍 {room} Calling update_sensor for {avgDew_entity} with value {new_dew_value}")
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": avgDew_entity, "value": new_dew_value},
            blocking=True,
        )
        _LOGGER.info(f"✅ {room} AvgDew service call completed")
        _LOGGER.debug(
            f"Sensor '{vpd_entity}' updated via service with value: {vpd_entity}"
        )
    except Exception as e:
        _LOGGER.info(f"Failed to update sensor '{vpd_entity}' via service: {e}")


async def _update_specific_sensor(entity, room, value, hass):

    entity_id = f"sensor.{entity}{room.lower()}"
    try:
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": entity_id, "value": value},
            blocking=True,
        )
    except Exception as e:
        _LOGGER.info(f"Failed to update sensor '{entity_id}' via service: {e}")


async def _update_specific_number(entity, room, value, hass):

    entity_id = f"number.{entity}{room.lower()}"
    try:
        await hass.services.async_call(
            domain="number",
            service="set_value",
            service_data={"entity_id": entity_id, "value": float(value)},
            blocking=True,
        )
    except Exception as e:
        _LOGGER.info(f"Failed to update Number '{entity_id}' via service: {e}")

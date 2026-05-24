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

    _LOGGER.debug(f"🔍 {room} UPDATE SENSORS: VPD={vpd_value}, Temp={temp_value}, Hum={hum_value}, Dew={dew_value}")

    try:
        # Überprüfe, ob der Wert gültig ist
        new_vpd_value = (
            vpd_value if vpd_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.debug(f"🔍 {room} Calling update_sensor for {vpd_entity} with value {new_vpd_value}")
        # Rufe den Service auf
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": vpd_entity, "value": new_vpd_value},
            blocking=True,
        )
        _LOGGER.debug(f"✅ {room} VPD service call completed")
        
        new_temp_value = (
            temp_value if temp_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.debug(f"🔍 {room} Calling update_sensor for {avgTemp_entity} with value {new_temp_value}")
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": avgTemp_entity, "value": new_temp_value},
            blocking=True,
        )
        _LOGGER.debug(f"✅ {room} AvgTemp service call completed")
        
        new_hum_value = (
            hum_value if hum_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.debug(f"🔍 {room} Calling update_sensor for {avgHum_entity} with value {new_hum_value}")
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": avgHum_entity, "value": new_hum_value},
            blocking=True,
        )
        _LOGGER.debug(f"✅ {room} AvgHum service call completed")
        
        new_dew_value = (
            dew_value if dew_value not in (None, "unknown", "unbekannt") else 0.0
        )
        _LOGGER.debug(f"🔍 {room} Calling update_sensor for {avgDew_entity} with value {new_dew_value}")
        await hass.services.async_call(
            domain="opengrowbox",
            service="update_sensor",
            service_data={"entity_id": avgDew_entity, "value": new_dew_value},
            blocking=True,
        )
        _LOGGER.debug(f"✅ {room} AvgDew service call completed")
        _LOGGER.debug(
            f"Sensor '{vpd_entity}' updated via service with value: {vpd_entity}"
        )
    except Exception as e:
        _LOGGER.debug(f"Failed to update sensor '{vpd_entity}' via service: {e}")

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
        _LOGGER.debug(f"Failed to update sensor '{entity_id}' via service: {e}")

async def _update_specific_select(entity, room, value, hass):

    entity_id = f"select.{entity}_{room.lower()}"
    try:
        await hass.services.async_call(
            domain="select",
            service="select_option",
            service_data={"entity_id": entity_id, "option": value},
            blocking=True,
        )
    except Exception as e:
        _LOGGER.debug(f"Failed to update select '{entity_id}' via service: {e}")

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
        _LOGGER.debug(f"Failed to update Number '{entity_id}' via service: {e}")

async def update_entity(entity: str, value, room: str, hass) -> bool:
    """Update any HA entity by detecting its type from the prefix.

    Args:
        entity:  Full entity_id base like "select.ogb_co2_control"
                 (room suffix is appended automatically as _{room.lower()})
        room:    Room identifier, e.g. "Dev Room" or "dev_room"
        value:   New value – type must match the domain
        hass:    Home Assistant instance

    Returns:
        True on success, False on failure.
    """
    if "." not in entity:
        _LOGGER.error(f"update_entity: no domain prefix in '{entity}' — skipping")
        return False

    # Normalize room: lowercase + spaces → underscores
    room_normalized = room.lower().replace(" ", "_")
    
    domain = entity.split(".")[0]
    base   = entity.split(".", 1)[1]          # e.g. "ogb_co2_control"
    full_entity_id = f"{domain}.{base}_{room_normalized}"

    try:
        if domain == "sensor":
            await hass.services.async_call(
                domain="opengrowbox",
                service="update_sensor",
                service_data={"entity_id": full_entity_id, "value": value},
                blocking=True,
            )

        elif domain == "select":
            await hass.services.async_call(
                domain="select",
                service="select_option",
                service_data={"entity_id": full_entity_id, "option": str(value)},
                blocking=True,
            )

        elif domain in ("number", "input_number"):
            await hass.services.async_call(
                domain=domain,
                service="set_value",
                service_data={"entity_id": full_entity_id, "value": float(value)},
                blocking=True,
            )

        elif domain == "input_boolean":
            svc = "turn_on" if value else "turn_off"
            await hass.services.async_call(
                domain="input_boolean",
                service=svc,
                service_data={"entity_id": full_entity_id},
                blocking=True,
            )

        elif domain == "input_text":
            await hass.services.async_call(
                domain="input_text",
                service="set_value",
                service_data={"entity_id": full_entity_id, "value": str(value)},
                blocking=True,
            )

        elif domain == "time":
            # value must be a string "HH:MM:SS" or a datetime.time object
            time_str = value.strftime("%H:%M:%S") if hasattr(value, "strftime") else str(value)
            await hass.services.async_call(
                domain="time",
                service="set_value",
                service_data={"entity_id": full_entity_id, "time": time_str},
                blocking=True,
            )

        elif domain == "input_datetime":
            from datetime import date, datetime, time as dt_time
            service_data = {"entity_id": full_entity_id}
            if isinstance(value, datetime):
                service_data["datetime"] = value.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(value, date):
                service_data["date"] = value.strftime("%Y-%m-%d")
            elif isinstance(value, dt_time):
                service_data["time"] = value.strftime("%H:%M:%S")
            else:
                service_data["datetime"] = str(value)
            await hass.services.async_call(
                domain="input_datetime",
                service="set_datetime",
                service_data=service_data,
                blocking=True,
            )

        else:
            _LOGGER.warning(
                f"update_entity: unknown domain '{domain}' for '{full_entity_id}', skipping"
            )
            return False

        _LOGGER.debug(f"update_entity: ✓ {full_entity_id} = {value!r}")
        return True

    except Exception as e:
        _LOGGER.warning(f"update_entity: ✗ '{full_entity_id}' failed: {e}")
        return False
import logging
_LOGGER = logging.getLogger(__name__)

async def update_ownDeviceLists(room,device_info_list):
    """
    Extrahiert entity_ids aus device_info_list und schreibt sie in die *_device_select_{room}-Entities.
    """
    # Alle entity_ids sammeln
    deviceList = []
    for device in device_info_list:
        for entity in device.get("entities", []):
            entity_id = entity.get("entity_id")
            if entity_id and entity_id not in deviceList:
                deviceList.append(entity_id)

    _LOGGER.debug(f"[{room}] deviceList: {deviceList}")

    # Deine festen Ziel-Entities mit Room-Namen
    ownLightDevice_entity        = f"select.ogb_light_device_select_{room.lower()}"
    ownClimateDevice_entity      = f"ogb_climate_device_select_{room.lower()}"
    ownHumidiferDevice_entity    = f"ogb_humidifer_device_select_{room.lower()}"
    ownDehumidiferDevice_entity  = f"ogb_dehumidifer_device_select_{room.lower()}"
    ownExhaustDevice_entity      = f"ogb_exhaust_device_select_{room.lower()}"
    ownIntakeDevice_entity      = f"ogb_intake_device_select_{room.lower()}"
    ownVentilationDevice_entity  = f"ogb_vents_device_select_{room.lower()}"
    ownHeaterDevice_entity       = f"ogb_heater_device_select_{room.lower()}"
    ownCoolerDevice_entity       = f"ogb_cooler_device_select_{room.lower()}"
    ownco2PumpDevice_entity      = f"ogb_co2_device_select_{room.lower()}"
    ownwaterPumpDevice_entity    = f"ogb_waterpump_device_select_{room.lower()}"

    try:
        async def set_value(entity_id, value):
            safe_value = value if value else []
            await self.hass.services.async_call(
                domain="opengrowbox",
                service="add_select_options",
                service_data={
                    "entity_id": entity_id,
                    "value": safe_value
                },
                blocking=True
            )
            _LOGGER.info(f"Updated {entity_id} to {safe_value}")

        # An jede Entity die gesammelte Liste schreiben
        await set_value(ownLightDevice_entity, deviceList)
        await set_value(ownClimateDevice_entity, deviceList)
        await set_value(ownHumidiferDevice_entity, deviceList)
        await set_value(ownDehumidiferDevice_entity, deviceList)
        await set_value(ownExhaustDevice_entity, deviceList)
        await set_value(ownIntakeDevice_entity, deviceList)
        await set_value(ownVentilationDevice_entity, deviceList)
        await set_value(ownHeaterDevice_entity, deviceList)
        await set_value(ownCoolerDevice_entity, deviceList)
        await set_value(ownco2PumpDevice_entity, deviceList)
        await set_value(ownwaterPumpDevice_entity, deviceList)

    except Exception as e:
        _LOGGER.error(f"Failed to update device select options for room {self.room}: {e}")

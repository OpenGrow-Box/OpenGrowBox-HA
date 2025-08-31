import logging
from datetime import datetime,timedelta

_LOGGER = logging.getLogger(__name__)

async def update_light_state(lightOnTime,lightOffTime,isLightNowON,room):
    """
    Aktualisiere den Status von `lightOn`, basierend auf den Lichtzeiten.
    """

    try:
        if not lightOnTime or not lightOffTime:
            _LOGGER.debug("Lichtzeiten fehlen. Bitte sicherstellen, dass 'lightOnTime' und 'lightOffTime' gesetzt sind.")
            return None
        if lightOnTime == "" or lightOffTime == "":
            _LOGGER.debug("Lichtzeiten fehlen. Bitte sicherstellen, dass 'lightOnTime' und 'lightOffTime' gesetzt sind.")
            return None

        # Konvertiere Zeitstrings in `time`-Objekte
        light_on_time = datetime.strptime(lightOnTime, "%H:%M:%S").time()
        light_off_time = datetime.strptime(lightOffTime, "%H:%M:%S").time()

        # Hole die aktuelle Zeit
        current_time = datetime.now().time()

        # Prüfe, ob die aktuelle Zeit im Bereich liegt
        if light_on_time < light_off_time:
            # Normaler Zyklus (z. B. 08:00 bis 20:00)
            is_light_on = light_on_time <= current_time < light_off_time
        else:
            # Über Mitternacht (z. B. 20:00 bis 08:00)
            is_light_on = current_time >= light_on_time or current_time < light_off_time

        # Aktualisiere den Status im DataStore
        current_status = isLightNowON
        #_LOGGER.warn(f"Prüfung Licht Zeiten für {room} CurrentState:{current_status} NeededState:{is_light_on}") 
        if current_status != is_light_on:
            _LOGGER.warn(f"LightStateChagned in {room} From {current_status} To:{is_light_on}")    
            return is_light_on
    except Exception as e:
        _LOGGER.error(f"{room} Fehler beim Aktualisieren des Lichtstatus: {e}")       


def hours_between(start_str, stop_str):
    """
    Berechnet die Stunden zwischen zwei Zeiten (HH:MM:SS),
    auch wenn der Zeitraum über Mitternacht geht.
    """
    fmt = "%H:%M:%S"
    start = datetime.strptime(start_str, fmt)
    stop = datetime.strptime(stop_str, fmt)

    if stop <= start:
        stop += timedelta(days=1)

    diff = stop - start
    return diff.total_seconds() / 3600  # Stunden als float
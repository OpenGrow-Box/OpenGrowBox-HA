import logging
from datetime import datetime, timedelta

_LOGGER = logging.getLogger(__name__)


async def update_light_state(lightOnTime, lightOffTime, isLightNowON, room):
    """
    Update the status of `lightOn` based on the light times.
    """

    try:
        if not lightOnTime or not lightOffTime:
            _LOGGER.debug(
                "Light times missing. Please ensure 'lightOnTime' and 'lightOffTime' are set."
            )
            return None
        if lightOnTime == "" or lightOffTime == "":
            _LOGGER.debug(
                "Light times missing. Please ensure 'lightOnTime' and 'lightOffTime' are set."
            )
            return None

        # Convert time strings to `time` objects
        light_on_time = datetime.strptime(lightOnTime, "%H:%M:%S").time()
        light_off_time = datetime.strptime(lightOffTime, "%H:%M:%S").time()

        # Get the current time
        current_time = datetime.now().time()

        # Check whether the current time is within the range
        if light_on_time < light_off_time:
            # Normal cycle (e.g. 08:00 to 20:00)
            is_light_on = light_on_time <= current_time < light_off_time
        else:
            # Crossing midnight (e.g. 20:00 to 08:00)
            is_light_on = current_time >= light_on_time or current_time < light_off_time

        # Update the status in the DataStore
        current_status = isLightNowON
        # _LOGGER.warn(f"Light time check for {room} CurrentState:{current_status} NeededState:{is_light_on}")
        if current_status != is_light_on:
            _LOGGER.warn(
                f"Light state changed in {room} from {current_status} to {is_light_on}"
            )
            return is_light_on
    except Exception as e:
        _LOGGER.error(f"{room} Error updating light state: {e}")


def hours_between(start_str, stop_str):
    """
    Calculate the hours between two times (HH:MM:SS),
    even if the period crosses midnight.
    """
    fmt = "%H:%M:%S"
    start = datetime.strptime(start_str, fmt)
    stop = datetime.strptime(stop_str, fmt)

    if stop <= start:
        stop += timedelta(days=1)

    diff = stop - start
    return diff.total_seconds() / 3600  # hours as float

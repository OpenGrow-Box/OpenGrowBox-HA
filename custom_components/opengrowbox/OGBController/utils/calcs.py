import math
import logging
import asyncio

_LOGGER = logging.getLogger(__name__)

# Berechne Durchschnittswert aus einer Liste (asynchron)
def calculate_avg_value(data=[]):
    total = 0
    count = 0

    for entry in data:
        # Prüfe, ob der Eintrag ein Dictionary ist
        if not isinstance(entry, dict):
            _LOGGER.warn(f"Ignoring non-dictionary entry: {entry}")
            continue

        # Extrahiere den 'value'-Wert
        value = entry.get("value")
        if value is None:
            _LOGGER.warn(f"Ignoring None value for entry: {entry}")
            continue

        try:
            # Konvertiere den Wert in float
            value = float(value)
            total += value
            count += 1
        except ValueError:
            _LOGGER.warn(f"Ignoring non-numeric value '{value}' for entry: {entry}")
        except Exception as e:
            _LOGGER.error(f"Unexpected error for entry {entry}: {e}")

    if count == 0:
        return "unavailable"

    avg_value = total / count
    return round(avg_value, 2)

# Berechne aktuellen VPD (asynchron)
def calculate_current_vpd(temp, humidity, leaf_offset):
    try:
        temp = float(temp)
        humidity = float(humidity)
        leaf_temp = temp - float(leaf_offset)
    except (ValueError, TypeError):
        return None

    if temp is None or humidity is None or leaf_temp is None:
        return None

    sdp_luft = 0.6108 * math.exp((17.27 * temp) / (temp + 237.3))
    sdp_blatt = 0.6108 * math.exp((17.27 * leaf_temp) / (leaf_temp + 237.3))
    adp = (humidity / 100) * sdp_luft
    vpd = sdp_blatt - adp

    _LOGGER.debug(f"VPD-Calculation got {round(vpd, 2)}")
    return round(vpd, 2)

# Berechne perfekten VPD (asynchron)
def calculate_perfect_vpd(vpd_range, tolerance_percent):

    if not isinstance(vpd_range, (list, tuple)) or len(vpd_range) != 2:
        raise ValueError("vpd_range must be a list or tuple with exactly two numbers.")

    try:
        vpd_min = float(vpd_range[0])
        vpd_max = float(vpd_range[1])
        tolerance_percent = float(tolerance_percent)
    except (ValueError, TypeError):
        raise ValueError("Invalid inputs for vpd_range or tolerance_percent.")

    average_vpd = (vpd_min + vpd_max) / 2
    tolerance = (tolerance_percent / 100) * average_vpd
    
    return {
        "perfection": round(average_vpd, 3),
        "perfect_min": round(average_vpd - tolerance, 3),
        "perfect_max": round(average_vpd + tolerance, 3),
    }

# Berechne Taupunkt (asynchron)
def calculate_dew_point(temp, humidity):
    try:
        temp = float(temp)
        humidity = float(humidity)
    except (ValueError, TypeError):
        return "unavailable"

    a = 17.27
    b = 237.7

    gamma = (a * temp) / (b + temp) + math.log(humidity / 100)
    dew_point = (b * gamma) / (a - gamma)

    return round(dew_point, 2)

# Berechne DewPointVPD (asynchron)
async def calc_dew_vpd(air_temp, dew_point):
    try:
        air_temp = float(air_temp)
        dew_point = float(dew_point)
    except (ValueError, TypeError):
        return {
            "dewpoint_vpd": None,
            "vapor_pressure_actual": None,
            "vapor_pressure_saturation": None,
        }

    sdp_luft = 0.6108 * math.exp((17.27 * air_temp) / (air_temp + 237.3))
    adp = 0.6108 * math.exp((17.27 * dew_point) / (dew_point + 237.3))
    dew_vpd = sdp_luft - adp

    vapor_pressure_actual = 6.11 * (10 ** ((7.5 * dew_point) / (237.3 + dew_point)))
    vapor_pressure_saturation = 6.11 * (10 ** ((7.5 * air_temp) / (237.3 + air_temp)))

    return {
        "dewpoint_vpd": round(dew_vpd, 3),
        "vapor_pressure_actual": round(vapor_pressure_actual, 2),
        "vapor_pressure_saturation": round(vapor_pressure_saturation, 2),
    }

# Berechne DewPointVPD (Based on Dewpoint/TEMP)
def calc_dew_vpd(air_temp, dew_point):
    try:
        air_temp = float(air_temp)
        dew_point = float(dew_point)
    except (ValueError, TypeError):
        return {
            "dewpoint_vpd": None,
            "vapor_pressure_actual": None,
            "vapor_pressure_saturation": None,
        }

    sdp_luft = 0.6108 * math.exp((17.27 * air_temp) / (air_temp + 237.3))
    adp = 0.6108 * math.exp((17.27 * dew_point) / (dew_point + 237.3))
    dew_vpd = sdp_luft - adp

    vapor_pressure_actual = 6.11 * (10 ** ((7.5 * dew_point) / (237.3 + dew_point)))
    vapor_pressure_saturation = 6.11 * (10 ** ((7.5 * air_temp) / (237.3 + air_temp)))

    return {
        "dewpoint_vpd": round(dew_vpd, 3),
        "vapor_pressure_actual": round(vapor_pressure_actual, 2),
        "vapor_pressure_saturation": round(vapor_pressure_saturation, 2),
    }

# Berechne Dry5Days VPD (Based on TEMP/HUM/VPD)
def calc_Dry5Days_vpd(temp, humidity, leaf_offset=0):
    try:
        temp = float(temp)
        humidity = float(humidity)
        leaf_temp = temp - float(leaf_offset)
    except (ValueError, TypeError):
        return None

    sdp_luft = 0.6108 * math.exp((17.27 * temp) / (temp + 237.3))
    sdp_blatt = 0.6108 * math.exp((17.27 * leaf_temp) / (leaf_temp + 237.3))
    adp = (humidity / 100) * sdp_luft
    vpd = sdp_blatt - adp

    return round(vpd, 2)

def calc_light_to_ppfd_dli(value, unit="lux", hours=18, area_m2=1.0, led_type="fullspektrum_grow"):
    """
    Convert Lux or Lumen to PPFD (µmol/m²/s) and DLI (mol/m²/d) for Grow LEDs.
    
    Optimized for cannabis and vegetable growing with realistic conversion factors.

    :param value: Light measurement (Lux or Lumen)
    :param unit: "lux" or "lumen"
    :param hours: Photoperiod in hours (default 8h)
    :param area_m2: Area in m² if unit is lumen (default 1.0)
    :param led_type: LED type - affects conversion factor
    :return: (ppfd, dli) - PPFD in µmol/m²/s, DLI in mol/m²/d
    
    Available led_types:
    - "fullspektrum_grow": Vollspektrum Grow LEDs (factor 15) - RECOMMENDED
    - "quantum_board": Samsung LM301B/H Quantum Boards (factor 16)
    - "red_blue_grow": Red/Blue Grow LEDs (factor 12)
    - "high_end_grow": High-End Grow LEDs (factor 18)
    - "cob_grow": COB Grow LEDs (factor 20)
    - "hps_equivalent": LED as HPS replacement (factor 15)
    - "burple": Old "Burple" LEDs (factor 12)
    - "white_led": Standard white LEDs (factor 54) - NOT for growing
    """

    # Wenn None oder leer, Standardwert nutzen
    if value is None or value == "":
        value = default_value

    # Immer versuchen, value in float umzuwandeln
    try:
        value = float(value)
    except (ValueError, TypeError):
        value = float(default_value)

    # Umrechnungsfaktoren für verschiedene LED-Typen
    conversion_factors = {
        "fullspektrum_grow": 15,
        "quantum_board": 16,
        "red_blue_grow": 12,
        "high_end_grow": 18,
        "cob_grow": 20,
        "hps_equivalent": 15,
        "burple": 12,
        "white_led": 54
    }

    # Input validation
    if led_type not in conversion_factors:
        available_types = ", ".join(conversion_factors.keys())
        raise ValueError(f"led_type must be one of: {available_types}")

    if unit.lower() == "lumen":
        if area_m2 <= 0:
            raise ValueError("area_m2 must be positive when using lumen")
        lux = value / area_m2
    elif unit.lower() == "lux":
        lux = value
    else:
        raise ValueError("unit must be 'lux' or 'lumen'")

    if hours <= 0:
        raise ValueError("hours must be positive")

    if value < 0:
        value = 0  # negative Werte automatisch auf 0 setzen

    # Umrechnung basierend auf LED-Typ
    factor = conversion_factors[led_type]
    ppfd = lux / factor

    # DLI berechnung
    dli = ppfd * 3600 * hours / 1_000_000

    return round(ppfd), round(dli, 1)
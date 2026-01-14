import asyncio
import logging
from datetime import datetime

from ...utils.calcs import calculate_perfect_vpd
from ...utils.sensorUpdater import _update_specific_sensor

_LOGGER = logging.getLogger(__name__)


class OGBConfigurationManager:
    """Manages all configuration updates and settings for OpenGrowBox."""

    def __init__(self, data_store, event_manager, room, hass):
        """Initialize the configuration manager.

        Args:
            data_store: Reference to the data store
            event_manager: Reference to the event manager
            room: Room identifier
            hass: Home Assistant instance
        """
        self.data_store = data_store
        self.event_manager = event_manager
        self.room = room
        self.hass = hass
        
        # Initialization flag - suppresses event emissions during initial config load
        # Set to True after first_start() is called
        self._is_initialized = False

        # VPD intervals mapping
        self.vpd_intervals = {
            "LIVE": 0,
            "1MIN": 60,
            "5MIN": 300,
            "10MIN": 600,
            "15MIN": 900,
            "30MIN": 1800,
            "1H": 3600,
        }
    
    def mark_initialized(self):
        """Mark the configuration manager as initialized.
        
        After this is called, config changes will emit events.
        Called from first_start() after initial config loading.
        """
        self._is_initialized = True
        _LOGGER.info(f"{self.room}: Configuration manager initialized - events now enabled")

    def _is_unavailable_state(self, value) -> bool:
        """Check if a value represents an unavailable/unknown HA state.
        
        Args:
            value: The state value to check
            
        Returns:
            True if the value is unavailable/unknown, False otherwise
        """
        if value is None:
            return True
        str_value = str(value).lower().strip()
        return str_value in ("unavailable", "unknown", "none", "")

    def get_configuration_mapping(self):
        """Get the mapping of entity keys to configuration handlers."""
        return {
            # Basics
            f"ogb_vpd_determination_{self.room.lower()}": self._ogb_vpd_determination,
            f"ogb_maincontrol_{self.room.lower()}": self._update_control_option,
            f"ogb_notifications_{self.room.lower()}": self._update_notify_option,
            f"ogb_ai_learning_{self.room.lower()}": self._update_ai_learning,
            f"ogb_vpdtolerance_{self.room.lower()}": self._update_vpd_tolerance,
            f"ogb_plantstage_{self.room.lower()}": self._update_plant_stage,
            f"ogb_planttype_{self.room.lower()}": self._update_plant_type,
            f"ogb_tentmode_{self.room.lower()}": self._update_tent_mode,
            f"ogb_leaftemp_offset_{self.room.lower()}": self._update_leaf_temp_offset,
            f"ogb_vpdtarget_{self.room.lower()}": self._update_vpd_target,
            f"ogb_vpd_devicedampening_{self.room.lower()}": self._update_vpd_device_dampening,
            # Light Times
            f"ogb_lightontime_{self.room.lower()}": self._update_light_on_time,
            f"ogb_lightofftime_{self.room.lower()}": self._update_light_off_time,
            f"ogb_sunrisetime_{self.room.lower()}": self._update_sunrise_time,
            f"ogb_sunsettime_{self.room.lower()}": self._update_sunset_time,
            # Light Calculation Settings
            f"ogb_lightledtype_{self.room.lower()}": self._update_light_led_type,
            f"ogb_luxtoppfdfactor_{self.room.lower()}": self._update_lux_to_ppfd_factor,
            # Control Settings
            f"ogb_lightcontrol_{self.room.lower()}": self._update_ogb_light_control,
            f"ogb_holdvpdnight_{self.room.lower()}": self._update_vpd_night_hold_control,
            f"ogb_vpdlightcontrol_{self.room.lower()}": self._update_vpd_light_control,
            f"ogb_light_controltype_{self.room.lower()}": self._update_light_control_type,
            # CO2 Settings
            f"ogb_co2_control_{self.room.lower()}": self._update_co2_control,
            f"ogb_co2targetvalue_{self.room.lower()}": self._update_co2_target_value,
            f"ogb_co2minvalue_{self.room.lower()}": self._update_co2_min_value,
            f"ogb_co2maxvalue_{self.room.lower()}": self._update_co2_max_value,
            # Weights
            f"ogb_ownweights_{self.room.lower()}": self._update_own_weights_control,
            f"ogb_temperatureweight_{self.room.lower()}": self._update_temperature_weight,
            f"ogb_humidityweight_{self.room.lower()}": self._update_humidity_weight,
            # Plant Dates
            f"ogb_breederbloomdays_{self.room.lower()}": self._update_breeder_bloom_days_value,
            f"ogb_growstartdate_{self.room.lower()}": self._update_grow_start_dates_value,
            f"ogb_bloomswitchdate_{self.room.lower()}": self._update_bloom_switch_date_value,
            # Drying
            f"ogb_dryingmodes_{self.room.lower()}": self._update_drying_mode,
            # MINMAX
            f"ogb_minmax_control_{self.room.lower()}": self._update_min_max_control,
            f"ogb_mintemp_{self.room.lower()}": self._update_min_temp,
            f"ogb_minhum_{self.room.lower()}": self._update_min_humidity,
            f"ogb_maxtemp_{self.room.lower()}": self._update_max_temp,
            f"ogb_maxhum_{self.room.lower()}": self._update_max_humidity,
            # Hydro
            f"ogb_hydro_mode_{self.room.lower()}": self._update_hydro_mode,
            f"ogb_hydro_cycle_{self.room.lower()}": self._update_hydro_mode_cycle,
            f"ogb_hydropumpduration_{self.room.lower()}": self._update_hydro_duration,
            f"ogb_hydropumpintervall_{self.room.lower()}": self._update_hydro_intervall,
            f"ogb_hydro_retrive_{self.room.lower()}": self._update_retrieve_mode,
            f"ogb_hydroretriveduration_{self.room.lower()}": self._update_hydro_retrieve_duration,
            f"ogb_hydroretriveintervall_{self.room.lower()}": self._update_hydro_retrieve_intervall,
            # Feed
            f"ogb_feed_plan_{self.room.lower()}": self._update_feed_mode,
            f"ogb_feed_ph_target_{self.room.lower()}": self._update_feed_ph_target,
            f"ogb_feed_ec_target_{self.room.lower()}": self._update_feed_ec_target,
            f"ogb_feed_nutrient_a_{self.room.lower()}": self._update_feed_nutrient_a_ml,
            f"ogb_feed_nutrient_b_{self.room.lower()}": self._update_feed_nutrient_b_ml,
            f"ogb_feed_nutrient_c_{self.room.lower()}": self._update_feed_nutrient_c_ml,
            f"ogb_feed_nutrient_w_{self.room.lower()}": self._update_feed_nutrient_w_ml,
            f"ogb_feed_nutrient_x_{self.room.lower()}": self._update_feed_nutrient_x_ml,
            f"ogb_feed_nutrient_y_{self.room.lower()}": self._update_feed_nutrient_y_ml,
            f"ogb_feed_nutrient_ph_{self.room.lower()}": self._update_feed_nutrient_ph_ml,
            # Ambient/Outdoor Features
            f"ogb_ambientcontrol_{self.room.lower()}": self._update_ambient_control,
            # Devices
            f"ogb_light_minmax_{self.room.lower()}": self._device_self_min_max,
            f"ogb_light_volt_min_{self.room.lower()}": self._device_min_max_setter,
            f"ogb_light_volt_max_{self.room.lower()}": self._device_min_max_setter,
            # Exhaust
            f"ogb_exhaust_minmax_{self.room.lower()}": self._device_self_min_max,
            f"ogb_exhaust_duty_min_{self.room.lower()}": self._device_min_max_setter,
            f"ogb_exhaust_duty_max_{self.room.lower()}": self._device_min_max_setter,
            # Intake
            f"ogb_intake_minmax_{self.room.lower()}": self._device_self_min_max,
            f"ogb_intake_duty_min_{self.room.lower()}": self._device_min_max_setter,
            f"ogb_intake_duty_max_{self.room.lower()}": self._device_min_max_setter,
            # Vents
            f"ogb_ventilation_minmax_{self.room.lower()}": self._device_self_min_max,
            f"ogb_ventilation_duty_min_{self.room.lower()}": self._device_min_max_setter,
            f"ogb_ventilation_duty_max_{self.room.lower()}": self._device_min_max_setter,
            # Device Selects
            f"ogb_device_labelident_{self.room.lower()}": self._device_from_label,
            # WorkMode
            f"ogb_workmode_{self.room.lower()}": self._update_work_mode_control,
            # Strain Data
            f"ogb_strainname_{self.room.lower()}": self._update_strain_name,
            # Area
            f"ogb_grow_area_m2_{self.room.lower()}": self._update_grow_area,
            # Medium
            f"ogb_mediumtype_{self.room.lower()}": self._update_medium_type,
            f"ogb_multi_mediumctrl_{self.room.lower()}": self._update_multi_medium_control,
            # Crop Steering - note: entity names are lowercase with underscores
            f"ogb_cropsteering_mode_{self.room.lower()}": self._crop_steering_mode,
            f"ogb_cropsteering_phases_{self.room.lower()}": self._crop_steering_phase,
            # Crop Steering Parameters - Shot Intervall
            f"ogb_cropsteering_p0_shot_intervall_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_shot_intervall_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_shot_intervall_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_shot_intervall_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - Shot Duration
            f"ogb_cropsteering_p0_shot_duration_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_shot_duration_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_shot_duration_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_shot_duration_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - Shot Sum
            f"ogb_cropsteering_p0_shot_sum_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_shot_sum_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_shot_sum_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_shot_sum_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - EC Target
            f"ogb_cropsteering_p0_ec_target_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_ec_target_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_ec_target_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_ec_target_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - EC Dryback
            f"ogb_cropsteering_p0_ec_dryback_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_ec_dryback_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_ec_dryback_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_ec_dryback_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - Moisture Dryback
            f"ogb_cropsteering_p0_moisture_dryback_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_moisture_dryback_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_moisture_dryback_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_moisture_dryback_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - Max EC
            f"ogb_cropsteering_p0_maxec_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_maxec_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_maxec_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_maxec_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - Min EC
            f"ogb_cropsteering_p0_minec_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_minec_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_minec_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_minec_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - VWC Target
            f"ogb_cropsteering_p0_vwc_target_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_vwc_target_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_vwc_target_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_vwc_target_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - VWC Max
            f"ogb_cropsteering_p0_vwc_max_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_vwc_max_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_vwc_max_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_vwc_max_{self.room.lower()}": self._crop_steering_sets,
            # Crop Steering Parameters - VWC Min
            f"ogb_cropsteering_p0_vwc_min_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p1_vwc_min_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p2_vwc_min_{self.room.lower()}": self._crop_steering_sets,
            f"ogb_cropsteering_p3_vwc_min_{self.room.lower()}": self._crop_steering_sets,
            
            # Special Lights - Far Red
            f"ogb_light_farred_enabled_{self.room.lower()}": self._update_farred_enabled,
            f"ogb_light_farred_mode_{self.room.lower()}": self._update_farred_mode,
            f"ogb_light_farred_start_duration_{self.room.lower()}": self._update_farred_start_duration,
            f"ogb_light_farred_end_duration_{self.room.lower()}": self._update_farred_end_duration,
            f"ogb_light_farred_intensity_{self.room.lower()}": self._update_farred_intensity,
            
            # Special Lights - UV
            f"ogb_light_uv_enabled_{self.room.lower()}": self._update_uv_enabled,
            f"ogb_light_uv_mode_{self.room.lower()}": self._update_uv_mode,
            f"ogb_light_uv_delay_start_{self.room.lower()}": self._update_uv_delay_start,
            f"ogb_light_uv_stop_before_end_{self.room.lower()}": self._update_uv_stop_before_end,
            f"ogb_light_uv_max_duration_{self.room.lower()}": self._update_uv_max_duration,
            f"ogb_light_uv_intensity_{self.room.lower()}": self._update_uv_intensity,
            
            # Special Lights - Spectrum (Blue)
            f"ogb_light_blue_enabled_{self.room.lower()}": self._update_blue_enabled,
            f"ogb_light_blue_mode_{self.room.lower()}": self._update_blue_mode,
            f"ogb_light_blue_morning_boost_{self.room.lower()}": self._update_blue_morning_boost,
            f"ogb_light_blue_evening_reduce_{self.room.lower()}": self._update_blue_evening_reduce,
            f"ogb_light_blue_transition_{self.room.lower()}": self._update_blue_transition,
            
            # Special Lights - Spectrum (Red)
            f"ogb_light_red_enabled_{self.room.lower()}": self._update_red_enabled,
            f"ogb_light_red_mode_{self.room.lower()}": self._update_red_mode,
            f"ogb_light_red_morning_reduce_{self.room.lower()}": self._update_red_morning_reduce,
            f"ogb_light_red_evening_boost_{self.room.lower()}": self._update_red_evening_boost,
            f"ogb_light_red_transition_{self.room.lower()}": self._update_red_transition,
        }

    def handle_configuration_update(self, entity_key, data):
        """Handle configuration updates by routing to appropriate handlers.
        
        Entity keys come in with domain prefix (e.g., 'select.ogb_cropsteering_mode_veggitent')
        but the mapping uses bare keys (e.g., 'ogb_cropsteering_mode_veggitent').
        We strip the domain prefix before lookup.
        """
        actions = self.get_configuration_mapping()
        
        # Strip domain prefix (select., number., switch., text., time., date., etc.)
        # Entity keys from HA come as "domain.entity_id" but our mapping uses just "entity_id"
        original_key = entity_key
        if "." in entity_key:
            entity_key = entity_key.split(".", 1)[1]
        
        action = actions.get(entity_key)
        
        # Debug: Log all incoming entity keys for troubleshooting
        if "crop" in entity_key.lower() or "steering" in entity_key.lower():
            _LOGGER.debug(f"OGB-Manager {self.room}: CropSteering entity update: {original_key} -> {entity_key}")
            _LOGGER.debug(f"OGB-Manager {self.room}: Action found: {action is not None}")
        
        if action:
            _LOGGER.debug(f"OGB-Manager {self.room}: Routing {entity_key} to handler")
            asyncio.create_task(action(data))
            return True
        
        # Dynamic handler for CropSteering parameters (number entities)
        # Matches: ogb_cropsteering_p0_vwc_max_veggitent, ogb_cropsteering_p1_shot_ec_veggitent, etc.
        has_cropsteering = "cropsteering" in entity_key.lower()
        has_phase = any(f"_p{i}_" in entity_key.lower() for i in range(4))
        is_cs_param = has_cropsteering and has_phase
        
        _LOGGER.debug(f"OGB-Manager {self.room}: CS param check: key={entity_key}, has_cropsteering={has_cropsteering}, has_phase={has_phase}, is_cs={is_cs_param}")
        
        if is_cs_param:
            _LOGGER.debug(f"OGB-Manager {self.room}: ✅ Dynamic CropSteering parameter MATCHED: {entity_key}")
            _LOGGER.debug(f"OGB-Manager {self.room}: ✅ Data newState: {getattr(data, 'newState', 'N/A')}")
            asyncio.create_task(self._crop_steering_sets(data, entity_key))
            return True
        
        # Only log if it's a relevant entity we might care about
        if "ogb_" in entity_key.lower():
            _LOGGER.debug(f"OGB-Manager {self.room}: No action found for {entity_key} (original: {original_key}).")
        return False

    # Core control methods
    async def _ogb_vpd_determination(self, data):
        """Update VPD determination mode."""
        value = data.newState[0]
        current_main_control = self.data_store.get("vpdDetermination")
        if current_main_control != value:
            self.data_store.set("vpdDetermination", value)
            await self.event_manager.emit("VPDDeterminationChange", value)

    async def _update_control_option(self, data):
        """Update main control option."""
        value = data.newState[0]
        current_main_control = self.data_store.get("mainControl")
        if current_main_control != value:
            self.data_store.set("mainControl", value)
            await self.event_manager.emit("mainControlChange", value)
            await self.event_manager.emit(
                "PremiumChange",
                {"currentValue": value, "lastValue": current_main_control},
            )

    async def _update_notify_option(self, data):
        """Update notification option."""
        value = data.newState[0]
        current_state = self.data_store.get("notifyControl")
        self.data_store.set("notifyControl", value)
        if value == "Disabled":
            self.event_manager.change_notify_set(False)
        elif value == "Enabled":
            self.event_manager.change_notify_set(True)

    async def _update_ai_learning(self, data):
        """Update AI learning control."""
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.aiLearning")
        )
        if current_value != value:
            self.data_store.setDeep(
                "controlOptions.aiLearning", self._string_to_bool(value)
            )

    async def _update_vpd_tolerance(self, data):
        """Update VPD tolerance."""
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("vpd.tolerance")
        if current_value != value:
            self.data_store.setDeep("vpd.tolerance", value)

    # Plant stage and type methods
    async def _update_plant_stage(self, data):
        """Update plant stage."""
        value = data.newState[0]
        current_stage = self.data_store.get("plantStage")
        if current_stage != value:
            self.data_store.set("plantStage", value)

            own_weights_active = self.data_store.getDeep("controlOptions.ownWeights")
            if not own_weights_active and (
                value in ["MidFlower", "LateFlower"]
                or current_stage in ["MidFlower", "LateFlower"]
            ):
                await _update_specific_sensor(
                    "ogb_humidityweight_", self.room, float(1.25), self.hass
                )

            await self._plant_stage_to_vpd()
            await self.event_manager.emit("PlantStageChange", value)

    async def _update_plant_type(self, data):
        """Update plant type."""
        value = data.newState[0]
        current_light_plan = self.data_store.get("plantType")
        if current_light_plan != value:
            self.data_store.set("plantType", value)
            await self.event_manager.emit("LightPlanChange", value)
            _LOGGER.debug(f"Light Plan changed to {value}")

    async def _update_tent_mode(self, data):
        """Update tent mode."""
        value = data.newState[0]
        current_mode = self.data_store.get("tentMode")

        if isinstance(data, dict) and "newState" in data:  # OGBInitData
            self.data_store.set("tentMode", value)
        elif hasattr(data, "newState"):  # OGBEventPublication
            if current_mode != value:
                # Create proper mode publication for mode manager
                from ...data.OGBDataClasses.OGBPublications import OGBModeRunPublication
                tent_mode_pub = OGBModeRunPublication(currentMode=value)
                self.data_store.set("tentMode", value)
                #await self.event_manager.emit("selectActionMode", tent_mode_pub)

                # Premium mode remapping - handled by premium manager
                await self.event_manager.emit("PremiumModeChange", value)
        else:
            _LOGGER.error(
                f"Unknown tent-mode check your Select Options: {type(data)} - Data: {data}"
            )

    async def _update_leaf_temp_offset(self, data):
        """Update leaf temperature offset."""
        value = data.newState[0]
        current_stage = self.data_store.getDeep("tentData.leafTempOffset")

        if isinstance(data, dict):  # OGBInitData
            self.data_store.setDeep("tentData.leafTempOffset", value)
        elif hasattr(data, "newState"):  # OGBEventPublication
            if current_stage != value:
                self.data_store.setDeep("tentData.leafTempOffset", value)
                await self.event_manager.emit("VPDCreation", value)
        else:
            _LOGGER.error(f"Unknown datatype: {type(data)} - Data: {data}")

    async def _update_vpd_target(self, data):
        """Update targeted VPD value."""
        value = float(data.newState[0])
        current_value = self.data_store.getDeep("vpd.targeted")

        if current_value != value:
            _LOGGER.info(f"{self.room}: Update Target VPD to {value}")
            self.data_store.setDeep("vpd.targeted", value)

            tolerance_percent = float(self.data_store.getDeep("vpd.tolerance") or 0)
            tolerance_value = value * (tolerance_percent / 100)

            min_vpd = value - tolerance_value
            max_vpd = value + tolerance_value

            await _update_specific_sensor(
                "ogb_current_vpd_target_", self.room, value, self.hass
            )
            await _update_specific_sensor(
                "ogb_current_vpd_target_min_", self.room, min_vpd, self.hass
            )
            await _update_specific_sensor(
                "ogb_current_vpd_target_max_", self.room, max_vpd, self.hass
            )

    # Light configuration methods
    async def _update_light_on_time(self, data):
        """Update light on time."""
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("isPlantDay.lightOnTime")
        if current_value != value:
            self.data_store.setDeep("isPlantDay.lightOnTime", value)
            await self.event_manager.emit("LightTimeChanges", True)

    async def _update_light_off_time(self, data):
        """Update light off time."""
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("isPlantDay.lightOffTime")
        if current_value != value:
            self.data_store.setDeep("isPlantDay.lightOffTime", value)
            await self.event_manager.emit("LightTimeChanges", True)

    async def _update_sunrise_time(self, data):
        """Update sunrise time."""
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("isPlantDay.sunRiseTime")
        if current_value != value:
            self.data_store.setDeep("isPlantDay.sunRiseTime", value)
            await self.event_manager.emit("SunRiseTimeUpdates", value)

    async def _update_sunset_time(self, data):
        """Update sunset time."""
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("isPlantDay.sunSetTime")
        if current_value != value:
            self.data_store.setDeep("isPlantDay.sunSetTime", value)
            await self.event_manager.emit("SunSetTimeUpdates", value)

    async def _update_light_led_type(self, data):
        """Update light LED type for PPFD/DLI calculation."""
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("Light.ledType")
        if current_value != value:
            self.data_store.setDeep("Light.ledType", value)
            _LOGGER.info(f"{self.room}: LED type updated to '{value}'")

    async def _update_lux_to_ppfd_factor(self, data):
        """Update Lux to PPFD conversion factor."""
        value = data.newState[0]
        if value is None:
            return
        try:
            factor = float(value)
            current_value = self.data_store.getDeep("Light.luxToPPFDFactor")
            if current_value != factor:
                self.data_store.setDeep("Light.luxToPPFDFactor", factor)
                _LOGGER.info(f"{self.room}: Lux to PPFD factor updated to {factor}")
        except (ValueError, TypeError):
            _LOGGER.error(f"{self.room}: Invalid Lux to PPFD factor value: {value}")

    # Helper methods
    def _string_to_bool(self, string_to_bool):
        """Convert string to boolean."""
        if string_to_bool == "YES":
            return True
        if string_to_bool == "NO":
            return False
        return string_to_bool

    async def _plant_stage_to_vpd(self):
        """Update VPD values based on plant stage."""
        plant_stage = self.data_store.get("plantStage")
        stage_values = self.data_store.getDeep(f"plantStages.{plant_stage}")
        own_control_values = self.data_store.getDeep("controlOptions.minMaxControl")

        if not stage_values:
            _LOGGER.error(
                f"{self.room}: No data found for plant stage '{plant_stage}'."
            )
            return

        if own_control_values:
            _LOGGER.error(
                f"{self.room}: No adjustment possible for plant stage with own MinMax active"
            )
            return

        try:
            vpd_range = stage_values["vpdRange"]
            max_temp = stage_values["maxTemp"]
            min_temp = stage_values["minTemp"]
            max_humidity = stage_values["maxHumidity"]
            min_humidity = stage_values["minHumidity"]

            tolerance = self.data_store.getDeep("vpd.tolerance")
            perfections = calculate_perfect_vpd(vpd_range, tolerance)

            perfect_vpd = perfections["perfection"]
            perfect_vpd_min = perfections["perfect_min"]
            perfect_vpd_max = perfections["perfect_max"]

            await _update_specific_sensor(
                "ogb_current_vpd_target_", self.room, perfect_vpd, self.hass
            )
            await _update_specific_sensor(
                "ogb_current_vpd_target_min_", self.room, perfect_vpd_min, self.hass
            )
            await _update_specific_sensor(
                "ogb_current_vpd_target_max_", self.room, perfect_vpd_max, self.hass
            )

            self.data_store.setDeep("vpd.range", vpd_range)

            min_max_active = self._string_to_bool(
                self.data_store.getDeep("controlOptions.minMaxControl")
            )

            if min_max_active == False:
                self.data_store.setDeep("tentData.maxTemp", max_temp)
                self.data_store.setDeep("tentData.minTemp", min_temp)
                self.data_store.setDeep("tentData.maxHumidity", max_humidity)
                self.data_store.setDeep("tentData.minHumidity", min_humidity)

                self.data_store.setDeep("vpd.perfection", perfect_vpd)
                self.data_store.setDeep("vpd.perfectMin", perfect_vpd_min)
                self.data_store.setDeep("vpd.perfectMax", perfect_vpd_max)

                # Would call update_min_max_sensors here
                await self.event_manager.emit("PlantStageChange", plant_stage)
                _LOGGER.debug(
                    f"{self.room}: Plant stage '{plant_stage}' successfully transferred to VPD data."
                )
            else:
                self.data_store.setDeep("vpd.perfection", perfect_vpd)
                self.data_store.setDeep("vpd.perfectMin", perfect_vpd_min)
                self.data_store.setDeep("vpd.perfectMax", perfect_vpd_max)
                await self.event_manager.emit("PlantStageChange", plant_stage)

        except KeyError as e:
            _LOGGER.error(f"{self.room}: Missing key in plant stage data '{e}'")
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error processing plant stage data: {e}")

    # Additional configuration methods
    async def _update_vpd_device_dampening(self, data):
        """
        Update VPD device dampening
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.vpdDeviceDampening")
        )
        if current_value != value:
            self.data_store.setDeep(
                "controlOptions.vpdDeviceDampening", self._string_to_bool(value)
            )

    async def _update_ogb_light_control(self, data):
        """
        Update OGB light control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.lightbyOGBControl")
        )
        if current_value != value:
            self.data_store.setDeep(
                "controlOptions.lightbyOGBControl", self._string_to_bool(value)
            )
            await self.event_manager.emit(
                "updateControlModes", self._string_to_bool(value)
            )

    async def _update_vpd_night_hold_control(self, data):
        """
        Update VPD night hold control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.nightVPDHold")
        )
        if current_value != value:
            self.data_store.setDeep(
                "controlOptions.nightVPDHold", self._string_to_bool(value)
            )
            await self.event_manager.emit(
                "updateControlModes", self._string_to_bool(value)
            )

    async def _update_vpd_light_control(self, data):
        """
        Update VPD light control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.vpdLightControl")
        )
        self.data_store.setDeep(
            "controlOptions.vpdLightControl", self._string_to_bool(value)
        )
        await self.event_manager.emit("updateControlModes", self._string_to_bool(value))
        await self.event_manager.emit("VPDLightControl", self._string_to_bool(value))

    async def _update_co2_control(self, data):
        """
        Update CO2 control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.co2Control")
        )
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update CO2 control to {value}")
            self.data_store.setDeep(
                "controlOptions.co2Control", self._string_to_bool(value)
            )

    async def _update_co2_target_value(self, data):
        """
        Update CO2 target value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.co2ppm.target")
        if float(current_value) != value:
            _LOGGER.info(f"{self.room}: Update CO2 target value to {value}")
            self.data_store.setDeep("controlOptionData.co2ppm.target", float(value))

    async def _update_co2_min_value(self, data):
        """
        Update CO2 min value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.co2ppm.minPPM")
        if float(current_value) != value:
            _LOGGER.info(f"{self.room}: Update CO2 min value to {value}")
            self.data_store.setDeep("controlOptionData.co2ppm.minPPM", float(value))

    async def _update_co2_max_value(self, data):
        """
        Update CO2 max value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.co2ppm.maxPPM")
        if float(current_value) != value:
            _LOGGER.info(f"{self.room}: Update CO2 max value to {value}")
            self.data_store.setDeep("controlOptionData.co2ppm.maxPPM", float(value))

    async def _update_own_weights_control(self, data):
        """
        Update own weights control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.ownWeights")
        )
        if current_value != value:
            self.data_store.setDeep(
                "controlOptions.ownWeights", self._string_to_bool(value)
            )

    async def _update_temperature_weight(self, data):
        """
        Update temperature weight
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.weights.temp")
        if float(current_value) != value:
            self.data_store.setDeep("controlOptionData.weights.temp", float(value))

    async def _update_humidity_weight(self, data):
        """
        Update humidity weight
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.weights.hum")
        if float(current_value) != value:
            self.data_store.setDeep("controlOptionData.weights.hum", float(value))

    async def _update_breeder_bloom_days_value(self, data):
        """
        Update breeder bloom days value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("plantDates.breederbloomdays")
        if int(float(current_value)) != value:
            _LOGGER.info(f"{self.room}: Update breeder bloom days to {value}")
            self.data_store.setDeep("plantDates.breederbloomdays", int(float(value)))
            await self.event_manager.emit("PlantTimeChange", int(float(value)))
            # Would call _update_plant_dates here

    async def _update_grow_start_dates_value(self, data):
        """
        Update grow start date value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("plantDates.growstartdate")
        if current_value != value:
            _LOGGER.info(f"{self.room}: Update grow start to {value}")
            self.data_store.setDeep("plantDates.growstartdate", value)
            await self.event_manager.emit("PlantTimeChange", value)
            # Would call _update_plant_dates here

    async def _update_bloom_switch_date_value(self, data):
        """
        Update bloom switch date value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("plantDates.bloomswitchdate")
        if current_value != value:
            self.data_store.setDeep("plantDates.bloomswitchdate", value)
            await self.event_manager.emit("PlantTimeChange", value)
            # Would call _update_plant_dates here

    async def _update_drying_mode(self, data):
        """
        Update drying mode
        """
        value = data.newState[0]
        current_mode = self.data_store.getDeep("drying.currentDryMode")
        if current_mode != value:
            self.data_store.setDeep("drying.currentDryMode", value)

    async def _update_min_max_control(self, data):
        """
        Update min max control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.minMaxControl")
        )
        if current_value != value:
            bool_value = self._string_to_bool(value)
            self.data_store.setDeep("controlOptions.minMaxControl", bool_value)
            if bool_value == False:
                # Would call update_min_max_sensors here
                pass

    async def _update_min_temp(self, data):
        """
        Update min temperature
        """
        min_max_control = self._string_to_bool(
            self.data_store.getDeep("controlOptions.minMaxControl")
        )
        if min_max_control == False:
            return
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.minmax.minTemp")
        if float(current_value) != value:
            _LOGGER.info(f"{self.room}: Update min temp to {value}")
            self.data_store.setDeep("controlOptionData.minmax.minTemp", float(value))
            self.data_store.setDeep("tentData.minTemp", float(value))

    async def _update_min_humidity(self, data):
        """
        Update min humidity
        """
        min_max_control = self._string_to_bool(
            self.data_store.getDeep("controlOptions.minMaxControl")
        )
        if min_max_control == False:
            return
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.minmax.minHum")
        if float(current_value) != value:
            _LOGGER.info(f"{self.room}: Update min humidity to {value}")
            self.data_store.setDeep("controlOptionData.minmax.minHum", float(value))
            self.data_store.setDeep("tentData.minHumidity", float(value))

    async def _update_max_temp(self, data):
        """
        Update max temperature
        """
        min_max_control = self._string_to_bool(
            self.data_store.getDeep("controlOptions.minMaxControl")
        )
        if min_max_control == False:
            return
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.minmax.maxTemp")
        if float(current_value) != value:
            _LOGGER.info(f"{self.room}: Update max temp to {value}")
            self.data_store.setDeep("controlOptionData.minmax.maxTemp", float(value))
            self.data_store.setDeep("tentData.maxTemp", float(value))

    async def _update_max_humidity(self, data):
        """
        Update max humidity
        """
        min_max_control = self._string_to_bool(
            self.data_store.getDeep("controlOptions.minMaxControl")
        )
        if min_max_control == False:
            return
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.minmax.maxHum")
        if float(current_value) != value:
            _LOGGER.info(f"{self.room}: Update max humidity to {value}")
            self.data_store.setDeep("controlOptionData.minmax.maxHum", float(value))
            self.data_store.setDeep("tentData.maxHumidity", float(value))

    # Hydroponics methods
    async def _update_hydro_mode(self, data):
        """
        Update hydro mode
        """
        control_option = self.data_store.get("mainControl")
        if control_option not in ["HomeAssistant", "Premium"]:
            return

        value = data.newState[0]
        if value == "OFF":
            _LOGGER.info(f"{self.room}: Deactivate hydro mode")
            self.data_store.setDeep("Hydro.Active", False)
            self.data_store.setDeep("Hydro.Mode", value)
            # Only emit if initialized (after first_start)
            if self._is_initialized:
                await self.event_manager.emit("HydroModeChange", value)
        else:
            _LOGGER.info(f"{self.room}: Update hydro mode to {value}")
            self.data_store.setDeep("Hydro.Active", True)
            self.data_store.setDeep("Hydro.Mode", value)
            # Only emit if initialized (after first_start)
            if self._is_initialized:
                await self.event_manager.emit("HydroModeChange", value)

    async def _update_hydro_mode_cycle(self, data):
        """
        Update hydro cycle
        """
        value = data.newState[0]
        current_value = self._string_to_bool(self.data_store.getDeep("Hydro.Cycle"))
        if current_value != value:
            self.data_store.setDeep("Hydro.Cycle", self._string_to_bool(value))
            # Only emit if initialized (after first_start)
            if self._is_initialized:
                await self.event_manager.emit("HydroModeChange", value)

    async def _update_hydro_duration(self, data):
        """
        Update hydro duration with validation
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Duration")

        try:
            validated_value = float(value) if value is not None else 30.0
            if validated_value <= 0:
                validated_value = 30.0
        except (ValueError, TypeError):
            validated_value = 30.0
            _LOGGER.error(
                f"{self.room}: Invalid duration value '{value}', using default 30s"
            )

        if current_value != validated_value:
            self.data_store.setDeep("Hydro.Duration", validated_value)
            # Only emit if initialized (after first_start)
            if self._is_initialized:
                await self.event_manager.emit("HydroModeChange", validated_value)

    async def _update_hydro_intervall(self, data):
        """
        Update hydro interval with validation
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Intervall")

        try:
            validated_value = float(value) if value is not None else 60.0
            if validated_value <= 0:
                validated_value = 60.0
        except (ValueError, TypeError):
            validated_value = 60.0
            _LOGGER.error(
                f"{self.room}: Invalid interval value '{value}', using default 60s"
            )

        if current_value != validated_value:
            self.data_store.setDeep("Hydro.Intervall", validated_value)
            # Only emit if initialized (after first_start)
            if self._is_initialized:
                await self.event_manager.emit("HydroModeChange", validated_value)

    # Feeding methods
    async def _update_feed_mode(self, data):
        """
        Update feed mode
        """
        control_option = self.data_store.get("mainControl")
        if control_option not in ["HomeAssistant", "Premium"]:
            return

        value = data.newState[0]
        if value == "Disabled":
            self.data_store.setDeep("Hydro.FeedModeActive", False)
            self.data_store.setDeep("Hydro.FeedMode", value)
            await self.event_manager.emit("FeedModeChange", value)
        else:
            self.data_store.setDeep("Hydro.FeedModeActive", True)
            self.data_store.setDeep("Hydro.FeedMode", value)
            await self.event_manager.emit("FeedModeChange", value)

    async def _update_feed_ph_target(self, data):
        """
        Update feed pH target
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.PH_Target")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.PH_Target", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "ph_target", "value": new_value}
            )

    async def _update_feed_ec_target(self, data):
        """
        Update feed EC target
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.EC_Target")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.EC_Target", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "ec_target", "value": new_value}
            )

    async def _update_feed_nutrient_a_ml(self, data):
        """
        Update feed nutrient A ml
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Nut_A_ml")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nut_A_ml", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "a_ml", "value": new_value}
            )

    async def _update_feed_nutrient_b_ml(self, data):
        """
        Update feed nutrient B ml
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Nut_B_ml")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nut_B_ml", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "b_ml", "value": new_value}
            )

    async def _update_feed_nutrient_c_ml(self, data):
        """
        Update feed nutrient C ml
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Nut_C_ml")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nut_C_ml", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "c_ml", "value": new_value}
            )

    async def _update_feed_nutrient_ph_ml(self, data):
        """
        Update feed nutrient pH ml
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Nut_PH_ml")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nut_PH_ml", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "ph_ml", "value": new_value}
            )

    # Device configuration methods
    async def _device_self_min_max(self, data):
        """Update device min/max activation flags."""
        value = self._string_to_bool(data.newState[0])
        name = data.Name.lower()

        # Set activation flags based on device type
        if "exhaust" in name:
            self.data_store.setDeep("DeviceMinMax.Exhaust.active", value)
            _LOGGER.info(f"{self.room}: Exhaust min/max control set to {value}")
        elif "intake" in name:
            self.data_store.setDeep("DeviceMinMax.Intake.active", value)
            _LOGGER.info(f"{self.room}: Intake min/max control set to {value}")
        elif "ventilation" in name:
            self.data_store.setDeep("DeviceMinMax.Ventilation.active", value)
            _LOGGER.info(f"{self.room}: Ventilation min/max control set to {value}")
        elif "light" in name:
            self.data_store.setDeep("DeviceMinMax.Light.active", value)
            _LOGGER.info(f"{self.room}: Light min/max control set to {value}")
        else:
            _LOGGER.error(f"{self.room}: Unknown device type for min/max control: {name}")

        # Emit event for device manager
        await self.event_manager.emit("SetMinMax", data)

    async def _device_min_max_setter(self, data):
        """Update device min/max settings for voltage and duty cycle limits."""
        value = data.newState[0]
        name = data.Name.lower()

        # Set min/max values based on device type and parameter
        if "exhaust" in name:
            if "min" in name:
                self.data_store.setDeep("DeviceMinMax.Exhaust.minDuty", float(value))
                _LOGGER.info(f"{self.room}: Set exhaust min duty = {value}")
            elif "max" in name:
                self.data_store.setDeep("DeviceMinMax.Exhaust.maxDuty", float(value))
                _LOGGER.info(f"{self.room}: Set exhaust max duty = {value}")

        elif "intake" in name:
            if "min" in name:
                self.data_store.setDeep("DeviceMinMax.Intake.minDuty", float(value))
                _LOGGER.info(f"{self.room}: Set intake min duty = {value}")
            elif "max" in name:
                self.data_store.setDeep("DeviceMinMax.Intake.maxDuty", float(value))
                _LOGGER.info(f"{self.room}: Set intake max duty = {value}")

        elif "ventilation" in name:
            if "min" in name:
                self.data_store.setDeep("DeviceMinMax.Ventilation.minDuty", float(value))
                _LOGGER.info(f"{self.room}: Set ventilation min duty = {value}")
            elif "max" in name:
                self.data_store.setDeep("DeviceMinMax.Ventilation.maxDuty", float(value))
                _LOGGER.info(f"{self.room}: Set ventilation max duty = {value}")

        elif "light" in name:
            if "min" in name:
                self.data_store.setDeep("DeviceMinMax.Light.minVoltage", float(value))
                _LOGGER.info(f"{self.room}: Set light min voltage = {value}")
            elif "max" in name:
                self.data_store.setDeep("DeviceMinMax.Light.maxVoltage", float(value))
                _LOGGER.info(f"{self.room}: Set light max voltage = {value}")

        else:
            _LOGGER.error(f"{self.room}: Unknown device limit control: {name}")
            return

        # Emit event for device manager
        await self.event_manager.emit("SetMinMax", data)

    async def _device_from_label(self, data):
        """Update device label identification setting."""
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("DeviceLabelIdent")
        )
        if current_value != value:
            bool_value = self._string_to_bool(value)
            self.data_store.setDeep("DeviceLabelIdent", bool_value)
            _LOGGER.info(f"{self.room}: Device label identification set to {bool_value}")

    async def _update_work_mode_control(self, data):
        """Update work mode control."""
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.workMode")
        )
        if current_value != value:
            bool_value = self._string_to_bool(value)
            self.data_store.setDeep("controlOptions.workMode", bool_value)
            await self.event_manager.emit("WorkModeChange", bool_value)
            _LOGGER.info(f"{self.room}: Work mode updated to {bool_value}")

    async def _update_strain_name(self, data):
        """Update strain name."""
        # Implementation would go here
        pass

    async def _update_grow_area(self, data):
        """Update grow area."""
        # Implementation would go here
        pass

    async def _update_medium_type(self, data):
        """Update medium type - emits MediumChange event to create/update mediums.
        
        NOTE: This is called during managerInit AFTER LoadDataStore and MediumManager.init()
        have already run. So the MediumManager can properly sync (keeping existing plant data)
        instead of creating new empty mediums.
        """
        value = data.newState[0]
        _LOGGER.info(f"{self.room}: _update_medium_type called with value: '{value}'")
        
        # Skip invalid HA states (unavailable, unknown, etc.)
        if self._is_unavailable_state(value):
            _LOGGER.info(f"{self.room}: Skipping invalid medium type state: '{value}'")
            return
        
        _LOGGER.info(f"{self.room}: Medium type changed to: {value} - emitting MediumChange")
        
        # CRITICAL: Include room in event data so MediumManager can filter by room
        await self.event_manager.emit("MediumChange", {
            "room": self.room,
            "medium_type": str(value).strip()
        })
        _LOGGER.info(f"{self.room}: MediumChange event emitted with room filter")

    async def _update_multi_medium_control(self, data):
        """Update multi medium control setting."""
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.multiMediumCtrl")
        )
        if current_value != value:
            bool_value = self._string_to_bool(value)
            self.data_store.setDeep("controlOptions.multiMediumCtrl", bool_value)
            _LOGGER.info(f"{self.room}: Multi medium control set to {bool_value}")

    async def _update_ambient_control(self, data):
        """
        Update ambient control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.ambientControl")
        )
        if current_value != value:
            self.data_store.setDeep(
                "controlOptions.ambientControl", self._string_to_bool(value)
            )

    async def _update_light_control_type(self, data):
        """
        Update light control type
        """
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("controlOptions.lightControlType")
        if current_value != value:
            self.data_store.setDeep("controlOptions.lightControlType", value)
            _LOGGER.info(f"{self.room}: Light control type updated to '{value}'")

    async def _update_retrieve_mode(self, data):
        """
        Update retrieve mode
        """
        control_option = self.data_store.get("mainControl")
        if control_option not in ["HomeAssistant", "Premium"]:
            return

        value = self._string_to_bool(data.newState[0])
        if value == True:
            self.data_store.setDeep("Hydro.Retrieve", True)
            self.data_store.setDeep("Hydro.R_Active", True)
            await self.event_manager.emit("HydroModeRetrieveChange", value)
        else:
            self.data_store.setDeep("Hydro.Retrieve", False)
            self.data_store.setDeep("Hydro.R_Active", False)
            await self.event_manager.emit("HydroModeRetrieveChange", value)

    async def _update_hydro_retrieve_duration(self, data):
        """
        Update hydro retrieve duration
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.R_Duration")
        if current_value != value:
            self.data_store.setDeep("Hydro.R_Duration", value)
            await self.event_manager.emit("HydroModeRetriveChange", value)

    async def _update_hydro_retrieve_intervall(self, data):
        """
        Update hydro retrieve interval
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.R_Intervall")
        if current_value != value:
            self.data_store.setDeep("Hydro.R_Intervall", value)
            await self.event_manager.emit("HydroModeRetriveChange", value)

    async def _update_feed_nutrient_w_ml(self, data):
        """
        Update feed nutrient W ml
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Nut_W_ml")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nut_W_ml", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "w_ml", "value": new_value}
            )

    async def _update_feed_nutrient_x_ml(self, data):
        """
        Update feed nutrient X ml
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Nut_X_ml")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nut_X_ml", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "x_ml", "value": new_value}
            )

    async def _update_feed_nutrient_y_ml(self, data):
        """
        Update feed nutrient Y ml
        """
        new_value = data.newState[0]
        current_value = self.data_store.getDeep("Hydro.Nut_Y_ml")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nut_Y_ml", new_value)
            await self.event_manager.emit(
                "FeedModeValueChange", {"type": "y_ml", "value": new_value}
            )

    # Crop Steering configuration methods
    async def _crop_steering_mode(self, data):
        """
        Update CropSteering active mode setting.
        
        NOTE: This only stores the user's selection.
        The actual activation/deactivation is handled by OGBCastManager
        when the user changes Hydro.Mode to "Crop-Steering".
        """
        value = data.newState[0]
        _LOGGER.debug(f"{self.room}: _crop_steering_mode called with value: {value}")
        
        # Store the user's mode selection
        self.data_store.setDeep("CropSteering.ActiveMode", value)
        _LOGGER.debug(f"{self.room}: CropSteering.ActiveMode set to: {value}")
        
        # Emit event - CastManager/CSManager will check if it should actually run
        await self.event_manager.emit("CropSteeringChanges", data)
        _LOGGER.debug(f"{self.room}: CropSteeringChanges event emitted for mode: {value}")

    async def _crop_steering_phase(self, data):
        """Update CropSteering phase selector.
        
        IMPORTANT: Always store the phase, not just in Manual mode!
        User may set phase BEFORE switching to Manual mode.
        The phase is stored as-is (e.g., 'P1') - code that reads it should handle case.
        """
        value = data.newState[0]
        # Store lowercase for consistency
        phase_lower = value.lower() if value else "p0"
        self.data_store.setDeep("CropSteering.CropPhase", phase_lower)
        _LOGGER.debug(f"{self.room}: Crop Steering phase changed to {phase_lower} (from {value})")

    async def _crop_steering_sets(self, data, entity_key=None):
        """Dynamic setter for all Crop Steering parameters.
        
        This stores user-configured values for CropSteering phases.
        Values are stored at: CropSteering.Substrate.{phase}.{parameter}
        """
        value = data.newState[0]
        # Use entity_key if provided, otherwise fall back to data.Name
        name = (entity_key or getattr(data, 'Name', '') or '').lower()
        
        _LOGGER.debug(f"🌱 {self.room}: _crop_steering_sets CALLED - entity_key='{entity_key}', data.Name='{getattr(data, 'Name', 'N/A')}', value={value}")
        
        # Extract phase from parameter name (p0, p1, p2, p3)
        phase = None
        for p in ["p0", "p1", "p2", "p3"]:
            if f"_{p}_" in name:
                phase = p
                break
        
        if not phase:
            _LOGGER.error(f"❌ {self.room}: No Phase Found in name: {name}")
            return
        
        # Crop steering parameter mapping - expanded to match original repo
        # This maps entity name patterns to dataStore paths
        cs_parameter_mapping = {
            # EC parameters
            "shot_ec": ("Substrate", "Shot_EC"),
            "ec_target": ("Substrate", "EC_Target"),
            "ec_dryback": ("Substrate", "EC_Dryback"),
            "maxec": ("Substrate", "Max_EC"),
            "minec": ("Substrate", "Min_EC"),

            # VWC/Moisture parameters
            "vwc_target": ("Substrate", "VWC_Target"),
            "vwc_max": ("Substrate", "VWC_Max"),
            "vwc_min": ("Substrate", "VWC_Min"),
            "moisture_dryback": ("Substrate", "Moisture_Dryback"),

            # Irrigation parameters
            "shot_intervall": ("Substrate", "Shot_Intervall"),
            "shot_duration": ("Substrate", "Shot_Duration_Sec"),
            "shot_sum": ("Substrate", "Shot_Sum"),

            # Dryback parameters
            "dryback_target": ("Substrate", "Dryback_Target_Percent"),
            "dryback_duration": ("Substrate", "Dryback_Duration_Hours"),

            # Frequency parameters
            "irrigation_frequency": ("Substrate", "Irrigation_Frequency"),
        }
        
        _LOGGER.debug(f"🌱 {self.room}: CS parameter lookup - name='{name}', phase='{phase}', value={value}")
        
        for param_key, (soil_path, sub_key) in cs_parameter_mapping.items():
            if param_key in name:
                path = f"CropSteering.{soil_path}.{phase}.{sub_key}"
                self.data_store.setDeep(path, value)
                _LOGGER.debug(f"🌱 {self.room}: ✅ STORED {path} = {value}")
                
                # Verify it was actually stored
                verify = self.data_store.getDeep(path)
                _LOGGER.debug(f"🌱 {self.room}: ✅ VERIFY {path} = {verify}")
                return
        
        _LOGGER.error(f"⚠️ {self.room}: NO MATCH found for crop steering parameter: {name}")

    def get_configuration_info(self):
        """Get current configuration information."""
        return {
            "main_control": self.data_store.get("mainControl"),
            "plant_stage": self.data_store.get("plantStage"),
            "plant_type": self.data_store.get("plantType"),
            "tent_mode": self.data_store.get("tentMode"),
            "vpd_determination": self.data_store.get("vpdDetermination"),
            "notify_control": self.data_store.get("notifyControl"),
        }

    # ============================================================
    # SPECIAL LIGHTS CONFIGURATION METHODS
    # ============================================================
    
    # --- Far Red Light Settings ---
    async def _update_farred_enabled(self, data):
        """Update Far Red light enabled state."""
        value = self._string_to_bool(data.newState[0])
        current = self._string_to_bool(self.data_store.getDeep("specialLights.farRed.enabled"))
        if current != value:
            self.data_store.setDeep("specialLights.farRed.enabled", value)
            await self.event_manager.emit("FarRedSettingsUpdate", {"enabled": value})
            _LOGGER.info(f"{self.room}: Far Red enabled = {value}")

    async def _update_farred_mode(self, data):
        """Update Far Red light mode (Schedule, Always On, Always Off, Manual)."""
        value = data.newState[0]
        valid_modes = ["Schedule", "Always On", "Always Off", "Manual"]
        if value not in valid_modes:
            _LOGGER.debug(f"{self.room}: Invalid Far Red mode '{value}', ignoring")
            return
        current = self.data_store.getDeep("specialLights.farRed.mode")
        if current != value:
            self.data_store.setDeep("specialLights.farRed.mode", value)
            await self.event_manager.emit("FarRedSettingsUpdate", {"mode": value})
            _LOGGER.info(f"{self.room}: Far Red mode = {value}")

    async def _update_farred_start_duration(self, data):
        """Update Far Red start duration (minutes at start of light cycle)."""
        value = int(float(data.newState[0]))
        current = self.data_store.getDeep("specialLights.farRed.startDurationMinutes")
        if current != value:
            self.data_store.setDeep("specialLights.farRed.startDurationMinutes", value)
            await self.event_manager.emit("FarRedSettingsUpdate", {"startDurationMinutes": value})
            _LOGGER.info(f"{self.room}: Far Red start duration = {value} min")

    async def _update_farred_end_duration(self, data):
        """Update Far Red end duration (minutes at end of light cycle)."""
        value = int(float(data.newState[0]))
        current = self.data_store.getDeep("specialLights.farRed.endDurationMinutes")
        if current != value:
            self.data_store.setDeep("specialLights.farRed.endDurationMinutes", value)
            await self.event_manager.emit("FarRedSettingsUpdate", {"endDurationMinutes": value})
            _LOGGER.info(f"{self.room}: Far Red end duration = {value} min")

    async def _update_farred_intensity(self, data):
        """Update Far Red intensity (0-100%)."""
        value = int(float(data.newState[0]))
        value = max(0, min(100, value))  # Clamp to 0-100
        current = self.data_store.getDeep("specialLights.farRed.intensity")
        if current != value:
            self.data_store.setDeep("specialLights.farRed.intensity", value)
            await self.event_manager.emit("FarRedSettingsUpdate", {"intensity": value})
            _LOGGER.info(f"{self.room}: Far Red intensity = {value}%")

    # --- UV Light Settings ---
    async def _update_uv_enabled(self, data):
        """Update UV light enabled state."""
        value = self._string_to_bool(data.newState[0])
        current = self._string_to_bool(self.data_store.getDeep("specialLights.uv.enabled"))
        if current != value:
            self.data_store.setDeep("specialLights.uv.enabled", value)
            await self.event_manager.emit("UVSettingsUpdate", {"enabled": value})
            _LOGGER.info(f"{self.room}: UV enabled = {value}")

    async def _update_uv_mode(self, data):
        """Update UV light mode (Schedule, Always On, Always Off, Manual)."""
        value = data.newState[0]
        valid_modes = ["Schedule", "Always On", "Always Off", "Manual"]
        if value not in valid_modes:
            _LOGGER.debug(f"{self.room}: Invalid UV mode '{value}', ignoring")
            return
        current = self.data_store.getDeep("specialLights.uv.mode")
        if current != value:
            self.data_store.setDeep("specialLights.uv.mode", value)
            await self.event_manager.emit("UVSettingsUpdate", {"mode": value})
            _LOGGER.info(f"{self.room}: UV mode = {value}")

    async def _update_uv_delay_start(self, data):
        """Update UV delay after light start (minutes)."""
        value = int(float(data.newState[0]))
        current = self.data_store.getDeep("specialLights.uv.delayAfterStartMinutes")
        if current != value:
            self.data_store.setDeep("specialLights.uv.delayAfterStartMinutes", value)
            await self.event_manager.emit("UVSettingsUpdate", {"delayAfterStartMinutes": value})
            _LOGGER.info(f"{self.room}: UV delay after start = {value} min")

    async def _update_uv_stop_before_end(self, data):
        """Update UV stop before light end (minutes)."""
        value = int(float(data.newState[0]))
        current = self.data_store.getDeep("specialLights.uv.stopBeforeEndMinutes")
        if current != value:
            self.data_store.setDeep("specialLights.uv.stopBeforeEndMinutes", value)
            await self.event_manager.emit("UVSettingsUpdate", {"stopBeforeEndMinutes": value})
            _LOGGER.info(f"{self.room}: UV stop before end = {value} min")

    async def _update_uv_max_duration(self, data):
        """Update UV max duration per day (hours)."""
        value = int(float(data.newState[0]))
        current = self.data_store.getDeep("specialLights.uv.maxDurationHours")
        if current != value:
            self.data_store.setDeep("specialLights.uv.maxDurationHours", value)
            await self.event_manager.emit("UVSettingsUpdate", {"maxDurationHours": value})
            _LOGGER.info(f"{self.room}: UV max duration = {value} hours")

    async def _update_uv_intensity(self, data):
        """Update UV intensity (0-100%)."""
        value = int(float(data.newState[0]))
        value = max(0, min(100, value))  # Clamp to 0-100
        current = self.data_store.getDeep("specialLights.uv.intensity")
        if current != value:
            self.data_store.setDeep("specialLights.uv.intensity", value)
            await self.event_manager.emit("UVSettingsUpdate", {"intensity": value})
            _LOGGER.info(f"{self.room}: UV intensity = {value}%")

    # --- Blue Spectrum Light Settings ---
    async def _update_blue_enabled(self, data):
        """Update Blue spectrum light enabled state."""
        value = self._string_to_bool(data.newState[0])
        current = self._string_to_bool(self.data_store.getDeep("specialLights.spectrum.blue.enabled"))
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.blue.enabled", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"blue": {"enabled": value}})
            _LOGGER.info(f"{self.room}: Blue spectrum enabled = {value}")

    async def _update_blue_mode(self, data):
        """Update Blue spectrum light mode (Schedule, Always On, Always Off, Manual)."""
        value = data.newState[0]
        valid_modes = ["Schedule", "Always On", "Always Off", "Manual"]
        if value not in valid_modes:
            _LOGGER.debug(f"{self.room}: Invalid Blue mode '{value}', ignoring")
            return
        current = self.data_store.getDeep("specialLights.spectrum.blue.mode")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.blue.mode", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"blue": {"mode": value}})
            _LOGGER.info(f"{self.room}: Blue spectrum mode = {value}")

    async def _update_blue_morning_boost(self, data):
        """Update Blue morning boost percentage."""
        value = int(float(data.newState[0]))
        value = max(0, min(100, value))
        current = self.data_store.getDeep("specialLights.spectrum.blue.morningBoostPercent")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.blue.morningBoostPercent", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"blue": {"morningBoostPercent": value}})
            _LOGGER.info(f"{self.room}: Blue morning boost = {value}%")

    async def _update_blue_evening_reduce(self, data):
        """Update Blue evening reduce percentage."""
        value = int(float(data.newState[0]))
        value = max(0, min(100, value))
        current = self.data_store.getDeep("specialLights.spectrum.blue.eveningReducePercent")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.blue.eveningReducePercent", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"blue": {"eveningReducePercent": value}})
            _LOGGER.info(f"{self.room}: Blue evening reduce = {value}%")

    async def _update_blue_transition(self, data):
        """Update Blue transition duration (minutes)."""
        value = int(float(data.newState[0]))
        current = self.data_store.getDeep("specialLights.spectrum.blue.transitionMinutes")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.blue.transitionMinutes", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"blue": {"transitionMinutes": value}})
            _LOGGER.info(f"{self.room}: Blue transition = {value} min")

    # --- Red Spectrum Light Settings ---
    async def _update_red_enabled(self, data):
        """Update Red spectrum light enabled state."""
        value = self._string_to_bool(data.newState[0])
        current = self._string_to_bool(self.data_store.getDeep("specialLights.spectrum.red.enabled"))
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.red.enabled", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"red": {"enabled": value}})
            _LOGGER.info(f"{self.room}: Red spectrum enabled = {value}")

    async def _update_red_mode(self, data):
        """Update Red spectrum light mode (Schedule, Always On, Always Off, Manual)."""
        value = data.newState[0]
        valid_modes = ["Schedule", "Always On", "Always Off", "Manual"]
        if value not in valid_modes:
            _LOGGER.debug(f"{self.room}: Invalid Red mode '{value}', ignoring")
            return
        current = self.data_store.getDeep("specialLights.spectrum.red.mode")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.red.mode", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"red": {"mode": value}})
            _LOGGER.info(f"{self.room}: Red spectrum mode = {value}")

    async def _update_red_morning_reduce(self, data):
        """Update Red morning reduce percentage."""
        value = int(float(data.newState[0]))
        value = max(0, min(100, value))
        current = self.data_store.getDeep("specialLights.spectrum.red.morningReducePercent")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.red.morningReducePercent", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"red": {"morningReducePercent": value}})
            _LOGGER.info(f"{self.room}: Red morning reduce = {value}%")

    async def _update_red_evening_boost(self, data):
        """Update Red evening boost percentage."""
        value = int(float(data.newState[0]))
        value = max(0, min(100, value))
        current = self.data_store.getDeep("specialLights.spectrum.red.eveningBoostPercent")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.red.eveningBoostPercent", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"red": {"eveningBoostPercent": value}})
            _LOGGER.info(f"{self.room}: Red evening boost = {value}%")

    async def _update_red_transition(self, data):
        """Update Red transition duration (minutes)."""
        value = int(float(data.newState[0]))
        current = self.data_store.getDeep("specialLights.spectrum.red.transitionMinutes")
        if current != value:
            self.data_store.setDeep("specialLights.spectrum.red.transitionMinutes", value)
            await self.event_manager.emit("SpectrumSettingsUpdate", {"red": {"transitionMinutes": value}})
            _LOGGER.info(f"{self.room}: Red transition = {value} min")

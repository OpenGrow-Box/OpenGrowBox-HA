import asyncio
import logging
from datetime import datetime

from ...utils.calcs import calculate_perfect_vpd
from ...utils.sensorUpdater import _update_specific_number, _update_specific_sensor

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

    def _coerce_float(self, value, *, context: str = "value") -> float | None:
        """Convert HA-style values to float without raising during init."""
        if self._is_unavailable_state(value):
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.warning(
                f"{self.room}: Ignoring non-numeric {context}: {value}"
            )
            return None

    def get_configuration_mapping(self):
        """Get the mapping of entity keys to configuration handlers."""
        return {
            # Basics
            f"ogb_vpd_determination_{self.room.lower()}": self._ogb_vpd_determination,
            f"ogb_maincontrol_{self.room.lower()}": self._update_control_option,
            f"ogb_notifications_{self.room.lower()}": self._update_notify_option,
            f"ogb_vpdtolerance_{self.room.lower()}": self._update_vpd_tolerance,
            f"ogb_plantstage_{self.room.lower()}": self._update_plant_stage,
            f"ogb_planttype_{self.room.lower()}": self._update_plant_type,
            f"ogb_plantspecies_{self.room.lower()}": self._update_plant_species,
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
            # Reservoir Levels
            f"ogb_feed_reservoir_min_{self.room.lower()}": self._update_reservoir_min_level,
            f"ogb_feed_reservoir_max_{self.room.lower()}": self._update_reservoir_max_level,
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
            # WorkMode
            f"ogb_workmode_{self.room.lower()}": self._update_work_mode_control,
            # Strain Data
            f"ogb_strainname_{self.room.lower()}": self._update_strain_name,
            # Area
            f"ogb_grow_area_m2_{self.room.lower()}": self._update_grow_area,
            f"ogb_reservoir_volume_l_{self.room.lower()}": self._update_reservoir_volume,
            # Pump Flow Rates
            f"ogb_pump_flowrate_a_{self.room.lower()}": self._update_pump_flowrate_a,
            f"ogb_pump_flowrate_b_{self.room.lower()}": self._update_pump_flowrate_b,
            f"ogb_pump_flowrate_c_{self.room.lower()}": self._update_pump_flowrate_c,
            f"ogb_pump_flowrate_w_{self.room.lower()}": self._update_pump_flowrate_w,
            f"ogb_pump_flowrate_ph_down_{self.room.lower()}": self._update_pump_flowrate_ph_down,
            f"ogb_pump_flowrate_ph_up_{self.room.lower()}": self._update_pump_flowrate_ph_up,
            f"ogb_pump_flowrate_x_{self.room.lower()}": self._update_pump_flowrate_x,
            f"ogb_pump_flowrate_y_{self.room.lower()}": self._update_pump_flowrate_y,
            # Nutrient Concentrations
            f"ogb_nutrient_concentration_a_{self.room.lower()}": self._update_nutrient_concentration_a,
            f"ogb_nutrient_concentration_b_{self.room.lower()}": self._update_nutrient_concentration_b,
            f"ogb_nutrient_concentration_c_{self.room.lower()}": self._update_nutrient_concentration_c,
            f"ogb_nutrient_concentration_ph_down_{self.room.lower()}": self._update_nutrient_concentration_ph_down,
            f"ogb_nutrient_concentration_x_{self.room.lower()}": self._update_nutrient_concentration_x,
            f"ogb_nutrient_concentration_y_{self.room.lower()}": self._update_nutrient_concentration_y,
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

            f"ogb_soilmoisturemin_{self.room.lower()}": self._soil_moisture_threshold_sets,
            f"ogb_soilmoisturemax_{self.room.lower()}": self._soil_moisture_threshold_sets,
           
            # Special Lights - Far Red
            f"ogb_light_farred_enabled_{self.room.lower()}": self._update_farred_enabled,
            f"ogb_light_farred_mode_{self.room.lower()}": self._update_farred_mode,
            f"ogb_light_farred_start_duration_{self.room.lower()}": self._update_farred_start_duration,
            f"ogb_light_farred_end_duration_{self.room.lower()}": self._update_farred_end_duration,
            f"ogb_light_farred_intensity_{self.room.lower()}": self._update_farred_intensity,
            f"ogb_light_farred_smart_start_{self.room.lower()}": self._update_farred_smart_start,
            f"ogb_light_farred_smart_end_{self.room.lower()}": self._update_farred_smart_end,
            
            # Special Lights - UV
            f"ogb_light_uv_enabled_{self.room.lower()}": self._update_uv_enabled,
            f"ogb_light_uv_mode_{self.room.lower()}": self._update_uv_mode,
            f"ogb_light_uv_delay_start_{self.room.lower()}": self._update_uv_delay_start,
            f"ogb_light_uv_stop_before_end_{self.room.lower()}": self._update_uv_stop_before_end,
            f"ogb_light_uv_max_duration_{self.room.lower()}": self._update_uv_max_duration,
            f"ogb_light_uv_intensity_{self.room.lower()}": self._update_uv_intensity,
            f"ogb_light_uv_midday_start_{self.room.lower()}": self._update_uv_midday_start,
            f"ogb_light_uv_midday_end_{self.room.lower()}": self._update_uv_midday_end,
            
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

    async def _update_vpd_tolerance(self, data):
        """Update VPD tolerance."""
        value = data.newState[0]
        if value is None:
            return
        current_value = self.data_store.getDeep("vpd.tolerance")
        if current_value != value:
            self.data_store.setDeep("vpd.tolerance", value)

            try:
                new_tolerance = float(value)
            except (TypeError, ValueError):
                _LOGGER.warning(f"{self.room}: Invalid VPD tolerance value: {value}")
                return

            targeted = self.data_store.getDeep("vpd.targeted")
            if targeted is not None:
                try:
                    targeted_value = float(targeted)
                except (TypeError, ValueError):
                    _LOGGER.warning(f"{self.room}: Invalid targeted VPD value: {targeted}")
                    return

                tol_val = targeted_value * (new_tolerance / 100)
                targeted_min = round(targeted_value - tol_val, 2)
                targeted_max = round(targeted_value + tol_val, 2)
                self.data_store.setDeep("vpd.targetedMin", targeted_min)
                self.data_store.setDeep("vpd.targetedMax", targeted_max)

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
                await _update_specific_number(
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

    async def _update_plant_species(self, data):
        """Update plant species and reload plant stages with species-specific VPD values."""
        value = data.newState[0]
        current_species = self.data_store.get("plantSpecies")
        
        if current_species != value:
            self.data_store.set("plantSpecies", value)
            
            # Import plant species functions
            from ...data.OGBParams.OGBPlants import get_full_plant_stages, get_plant_species_stages, is_valid_species
            
            # Validate species
            if not is_valid_species(value):
                _LOGGER.warning(f"{self.room}: Invalid plant species '{value}', using default")
                value = "Cannabis"
            
            # Get plant stages for this species
            plant_stages = get_full_plant_stages(value)
            new_stages_list = get_plant_species_stages(value)
            
            # Update the plant stages in data store
            self.data_store.set("plantStages", plant_stages)
            
            # Update PlantStage select options to match species stages
            await self._update_plant_stage_select_options(new_stages_list)
            
            # Emit event to trigger VPD recalculation with new stage values
            await self.event_manager.emit("PlantSpeciesChange", value)
            await self.event_manager.emit("PlantStageChange", self.data_store.get("plantStage"))
            
            _LOGGER.info(f"{self.room}: Plant species changed to '{value}', updated plant stages")

    async def _update_plant_stage_select_options(self, new_stages: list):
        """Update PlantStage select entity options based on species stages.
        
        Args:
            new_stages: List of available stages for the current species
        """
        try:
            # Build entity_id for the PlantStage select
            entity_id = f"select.ogb_plantstage_{self.room.lower()}"
            
            # Use the opengrowbox.set_select_options service if available
            # or directly manipulate the select entity
            service_name = "set_select_options"
            
            # Check if service exists
            if self.hass.services.has_service("opengrowbox", service_name):
                # Use service to update options
                await self.hass.services.async_call(
                    domain="opengrowbox",
                    service=service_name,
                    service_data={
                        "entity_id": entity_id,
                        "options": new_stages
                    },
                    blocking=True
                )
                _LOGGER.debug(f"{self.room}: Called {service_name} service for {entity_id}")
            else:
                # Fallback: Direct entity manipulation via hass.data
                select_entity = None
                
                if hasattr(self.hass, 'data') and 'opengrowbox' in self.hass.data:
                    domain_data = self.hass.data['opengrowbox']
                    if isinstance(domain_data, dict) and 'selects' in domain_data:
                        # Search for the PlantStage select entity for this room
                        target_name = f"OGB_PlantStage_{self.room}"
                        for entity in domain_data['selects']:
                            if hasattr(entity, '_name') and entity._name == target_name:
                                select_entity = entity
                                break
                            # Alternative: check entity_id
                            if hasattr(entity, 'entity_id') and f"ogb_plantstage_{self.room.lower()}" in entity.entity_id:
                                select_entity = entity
                                break
                
                if select_entity:
                    current_stage = select_entity._attr_current_option
                    
                    # Update options to match species stages
                    select_entity._attr_options = new_stages
                    
                    # Check if current stage is still valid
                    if current_stage not in new_stages:
                        # Set to first available stage
                        new_stage = new_stages[0] if new_stages else "Germination"
                        select_entity._attr_current_option = new_stage
                        self.data_store.set("plantStage", new_stage)
                        _LOGGER.info(
                            f"{self.room}: PlantStage changed from '{current_stage}' to '{new_stage}' "
                            f"(not available in new species)"
                        )
                    
                    # Notify Home Assistant of the change
                    if hasattr(select_entity, 'async_write_ha_state'):
                        select_entity.async_write_ha_state()
                    
                    _LOGGER.debug(f"{self.room}: Updated PlantStage select options: {new_stages}")
                else:
                    _LOGGER.warning(
                        f"{self.room}: Could not find PlantStage select entity. "
                        f"Options will update on next restart."
                    )
        except Exception as e:
            _LOGGER.error(f"{self.room}: Error updating PlantStage select options: {e}")

    async def _update_tent_mode(self, data):
        """Update tent mode."""
        value = data.newState[0]
        current_mode = self.data_store.get("tentMode")

        if isinstance(data, dict) and "newState" in data:  # OGBInitData
            self.data_store.set("tentMode", value)
            # Emit event to activate mode - needed for proper initialization
            # Emit even during init so mode manager can properly set the mode
            from ...data.OGBDataClasses.OGBPublications import OGBModeRunPublication
            tent_mode_pub = OGBModeRunPublication(currentMode=value)
            await self.event_manager.emit("selectActionMode", tent_mode_pub)
            await self.event_manager.emit("PremiumModeChange", value)
            _LOGGER.info(f"🔄 {self.room}: Tent mode set to '{value}' during initialization")
        elif hasattr(data, "newState"):  # OGBEventPublication
            if current_mode != value:
                # Create proper mode publication for mode manager
                from ...data.OGBDataClasses.OGBPublications import OGBModeRunPublication
                tent_mode_pub = OGBModeRunPublication(currentMode=value)
                self.data_store.set("tentMode", value)
                await self.event_manager.emit("selectActionMode", tent_mode_pub)
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
        # Validate data exists before conversion
        if not data or not hasattr(data, 'newState') or not data.newState or len(data.newState) == 0:
            _LOGGER.error(f"{self.room}: Invalid data for VPD target update")
            return
        
        try:
            value = float(data.newState[0])
        except (ValueError, TypeError) as e:
            _LOGGER.error(f"{self.room}: Failed to convert VPD target value: {data.newState[0]} - {e}")
            return
        
        current_value = self.data_store.getDeep("vpd.targeted")
        current_min = self.data_store.getDeep("vpd.targetedMin")
        current_max = self.data_store.getDeep("vpd.targetedMax")

        if current_value != value or current_min is None or current_max is None:
            _LOGGER.info(f"{self.room}: Update Target VPD to {value}")
            self.data_store.setDeep("vpd.targeted", value)

            tolerance_percent = float(self.data_store.getDeep("vpd.tolerance") or 0)
            tolerance_value = value * (tolerance_percent / 100)

            min_vpd = round(value - tolerance_value, 2)
            max_vpd = round(value + tolerance_value, 2)

            self.data_store.setDeep("vpd.targetedMin", min_vpd)
            self.data_store.setDeep("vpd.targetedMax", max_vpd)

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
        min_max_active = self._string_to_bool(
            self.data_store.getDeep("controlOptions.minMaxControl")
        )

        if not stage_values:
            _LOGGER.error(
                f"{self.room}: No data found for plant stage '{plant_stage}'."
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

            # Always update VPD targets (these are NOT the same as Light MinMax)
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

            # Only update Temp/Humidity targets if minMaxControl is NOT active
            # (Light MinMax is handled separately in Light.py)
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
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
            _LOGGER.info(f"{self.room}: Update CO2 target value to {value}")
            self.data_store.setDeep("controlOptionData.co2ppm.target", float(value))

    async def _update_co2_min_value(self, data):
        """
        Update CO2 min value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.co2ppm.minPPM")
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
            _LOGGER.info(f"{self.room}: Update CO2 min value to {value}")
            self.data_store.setDeep("controlOptionData.co2ppm.minPPM", float(value))

    async def _update_co2_max_value(self, data):
        """
        Update CO2 max value
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.co2ppm.maxPPM")
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
            _LOGGER.info(f"{self.room}: Update CO2 max value to {value}")
            self.data_store.setDeep("controlOptionData.co2ppm.maxPPM", float(value))

    async def _update_ambient_control(self, data):
        """
        Update own weights control
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.ambientControl")
        )
        if current_value != value:
            self.data_store.setDeep("controlOptions.ambientControl", self._string_to_bool(value))

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
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
            self.data_store.setDeep("controlOptionData.weights.temp", float(value))

    async def _update_humidity_weight(self, data):
        """
        Update humidity weight
        """
        value = data.newState[0]
        current_value = self.data_store.getDeep("controlOptionData.weights.hum")
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
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
        Update min max control - emits events for both enable and disable
        """
        value = data.newState[0]
        current_value = self._string_to_bool(
            self.data_store.getDeep("controlOptions.minMaxControl")
        )
        if current_value != value:
            bool_value = self._string_to_bool(value)
            self.data_store.setDeep("controlOptions.minMaxControl", bool_value)

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
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
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
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
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
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
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
        if current_value is None:
            current_value = 0
        if float(current_value) != float(value):
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

    async def _update_reservoir_min_level(self, data):
        """
        Update reservoir minimum level threshold (trigger for auto-fill)
        """
        new_value = self._coerce_float(data.newState[0], context="reservoir_min_level")
        if new_value is None:
            _LOGGER.warning(f"{self.room}: Invalid reservoir min level value, skipping update")
            return
            
        current_value = self.data_store.getDeep("Hydro.ReservoirMinLevel")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.ReservoirMinLevel", new_value)
            _LOGGER.info(f"{self.room}: Reservoir min level updated to {new_value}%")
            await self.event_manager.emit(
                "ReservoirLevelChange", {"type": "min_level", "value": new_value}
            )

    async def _update_reservoir_max_level(self, data):
        """
        Update reservoir maximum level threshold (target for auto-fill)
        """
        new_value = self._coerce_float(data.newState[0], context="reservoir_max_level")
        if new_value is None:
            _LOGGER.warning(f"{self.room}: Invalid reservoir max level value, skipping update")
            return
            
        current_value = self.data_store.getDeep("Hydro.ReservoirMaxLevel")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.ReservoirMaxLevel", new_value)
            _LOGGER.info(f"{self.room}: Reservoir max level updated to {new_value}%")
            await self.event_manager.emit(
                "ReservoirLevelChange", {"type": "max_level", "value": new_value}
            )

    # Device configuration methods
    async def _device_self_min_max(self, data):
        """Update device min/max activation flags."""
        value = self._string_to_bool(data.newState[0])
        name = data.Name.lower()
        device_type = None
        active_bool = value

        # Determine device type from entity name
        if "exhaust" in name:
            device_type = "Exhaust"
            active_path = "DeviceMinMax.Exhaust.active"
        elif "intake" in name:
            device_type = "Intake"
            active_path = "DeviceMinMax.Intake.active"
        elif "ventilation" in name:
            device_type = "Ventilation"
            active_path = "DeviceMinMax.Ventilation.active"
        elif "light" in name:
            device_type = "Light"
            active_path = "DeviceMinMax.Light.active"
        else:
            _LOGGER.error(f"{self.room}: Unknown device type for min/max control: {name}")
            return

        # Check current value and update if changed
        current_value = self.data_store.getDeep(active_path)
        if current_value != active_bool:
            _LOGGER.info(f"{self.room}: Updating {device_type} min/max active from {current_value} to {active_bool}")
            self.data_store.setDeep(active_path, active_bool)
        else:
            _LOGGER.info(f"{self.room}: {device_type} min/max active already set to {active_bool}")

        # Ensure the active flag is set in the data store
        self.data_store.setDeep(f"DeviceMinMax.{device_type}.active", active_bool)
        _LOGGER.info(f"{self.room}: {device_type} min/max active set to {active_bool}")

        # Emit event with correct device type
        if not active_bool:
            _LOGGER.info(f"{self.room}: Emitting MinMaxControlDisabled for {device_type}")
            await self.event_manager.emit("MinMaxControlDisabled", {"deviceType": device_type})
        else:
            _LOGGER.info(f"{self.room}: Emitting MinMaxControlEnabled for {device_type}")
            await self.event_manager.emit("MinMaxControlEnabled", {"deviceType": device_type})

    async def _device_min_max_setter(self, data):
        """Update device min/max settings for voltage and duty cycle limits."""
        value = data.newState[0]
        name = data.Name.lower()

        min_path = None
        max_path = None
        device_type = None

        if "exhaust" in name:
            device_type = "Exhaust"
            min_path = "DeviceMinMax.Exhaust.minDuty"
            max_path = "DeviceMinMax.Exhaust.maxDuty"
        elif "intake" in name:
            device_type = "Intake"
            min_path = "DeviceMinMax.Intake.minDuty"
            max_path = "DeviceMinMax.Intake.maxDuty"
        elif "ventilation" in name:
            device_type = "Ventilation"
            min_path = "DeviceMinMax.Ventilation.minDuty"
            max_path = "DeviceMinMax.Ventilation.maxDuty"
        elif "light" in name:
            device_type = "Light"
            min_path = "DeviceMinMax.Light.minVoltage"
            max_path = "DeviceMinMax.Light.maxVoltage"
        else:
            _LOGGER.error(f"{self.room}: Unknown device limit control: {name}")
            return

        # Ensure min/max active flag is set when setting values
        self.data_store.setDeep(f"DeviceMinMax.{device_type}.active", True)
        _LOGGER.info(f"{self.room}: Auto-activated {device_type} min/max control")

        numeric_value = self._coerce_float(value, context=name)
        if numeric_value is None:
            _LOGGER.warning(
                f"{self.room}: Skipping {device_type} min/max update for invalid init value '{value}'"
            )
            return

        if "min" in name:
            self.data_store.setDeep(min_path, numeric_value)
            _LOGGER.info(f"{self.room}: Set {device_type.lower()} min {('duty' if device_type != 'Light' else 'voltage')} = {value}")
        elif "max" in name:
            self.data_store.setDeep(max_path, numeric_value)
            _LOGGER.info(f"{self.room}: Set {device_type.lower()} max {('duty' if device_type != 'Light' else 'voltage')} = {value}")

        # Get current values and validate
        min_val = self._coerce_float(
            self.data_store.getDeep(min_path), context=f"{device_type} min"
        )
        max_val = self._coerce_float(
            self.data_store.getDeep(max_path), context=f"{device_type} max"
        )

        if min_val is not None:
            self.data_store.setDeep(min_path, min_val)
        if max_val is not None:
            self.data_store.setDeep(max_path, max_val)

        if min_val is not None and max_val is not None and min_val >= max_val:
            adjustment = 10
            _LOGGER.debug(
                f"{self.room}: Invalid {device_type} min/max: min={min_val} >= max={max_val}. "
                f"Adjusting max to min+{adjustment}"
            )
            self.data_store.setDeep(max_path, min_val + adjustment)

        # Emit event to update devices
        _LOGGER.info(f"{self.room}: Emitting SetDeviceMinMax for {device_type}")
        await self.event_manager.emit("SetDeviceMinMax", device_type)

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
        """Update grow area in m²."""
        new_value = self._coerce_float(data.newState[0], context="grow_area_m2")
        if new_value is None:
            _LOGGER.info(f"{self.room}: Skipping invalid grow area state")
            return

        current_value = self.data_store.get("growAreaM2")

        if current_value != new_value:
            self.data_store.set("growAreaM2", new_value)
            _LOGGER.info(f"{self.room}: Grow area updated to {new_value} m²")

    async def _update_reservoir_volume(self, data):
        """Update reservoir volume in liters."""
        new_value = self._coerce_float(data.newState[0], context="reservoir_volume_l")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid reservoir volume state (must be > 0)")
            return

        current_value = self.data_store.getDeep("Hydro.ReservoirVolume")

        if current_value != new_value:
            self.data_store.setDeep("Hydro.ReservoirVolume", new_value)
            _LOGGER.info(f"{self.room}: Reservoir volume updated to {new_value} L")

    async def _update_pump_flowrate_a(self, data):
        """Update pump A flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_a")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump A flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_A")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_A", new_value)
            _LOGGER.info(f"{self.room}: Pump A flow rate updated to {new_value} ml/min")

    async def _update_pump_flowrate_b(self, data):
        """Update pump B flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_b")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump B flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_B")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_B", new_value)
            _LOGGER.info(f"{self.room}: Pump B flow rate updated to {new_value} ml/min")

    async def _update_pump_flowrate_c(self, data):
        """Update pump C flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_c")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump C flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_C")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_C", new_value)
            _LOGGER.info(f"{self.room}: Pump C flow rate updated to {new_value} ml/min")

    async def _update_pump_flowrate_w(self, data):
        """Update pump W flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_w")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump W flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_W")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_W", new_value)
            _LOGGER.info(f"{self.room}: Pump W flow rate updated to {new_value} ml/min")

    async def _update_pump_flowrate_ph_down(self, data):
        """Update pump PH-Down flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_ph_down")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump PH-Down flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_PH_Down")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_PH_Down", new_value)
            _LOGGER.info(f"{self.room}: Pump PH-Down flow rate updated to {new_value} ml/min")

    async def _update_pump_flowrate_ph_up(self, data):
        """Update pump PH+ flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_ph_up")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump PH+ flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_PH_Up")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_PH_Up", new_value)
            _LOGGER.info(f"{self.room}: Pump PH+ flow rate updated to {new_value} ml/min")

    async def _update_pump_flowrate_x(self, data):
        """Update pump X flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_x")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump X flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_X")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_X", new_value)
            _LOGGER.info(f"{self.room}: Pump X flow rate updated to {new_value} ml/min")

    async def _update_pump_flowrate_y(self, data):
        """Update pump Y flow rate (ml/min)"""
        new_value = self._coerce_float(data.newState[0], context="pump_flowrate_y")
        if new_value is None or new_value <= 0:
            _LOGGER.info(f"{self.room}: Skipping invalid pump Y flow rate")
            return
        
        current_value = self.data_store.getDeep("Hydro.Pump_FlowRate_Y")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Pump_FlowRate_Y", new_value)
            _LOGGER.info(f"{self.room}: Pump Y flow rate updated to {new_value} ml/min")

    async def _update_nutrient_concentration_a(self, data):
        """Update nutrient A concentration (ml/L)"""
        new_value = self._coerce_float(data.newState[0], context="nutrient_concentration_a")
        if new_value is None or new_value < 0:
            _LOGGER.info(f"{self.room}: Skipping invalid nutrient A concentration")
            return
        
        current_value = self.data_store.getDeep("Hydro.Nutrient_Concentration_A")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nutrient_Concentration_A", new_value)
            await self.event_manager.emit("FeedModeValueChange", {"type": "concentration_a", "value": new_value})
            _LOGGER.info(f"{self.room}: Nutrient A concentration updated to {new_value} ml/L")

    async def _update_nutrient_concentration_b(self, data):
        """Update nutrient B concentration (ml/L)"""
        new_value = self._coerce_float(data.newState[0], context="nutrient_concentration_b")
        if new_value is None or new_value < 0:
            _LOGGER.info(f"{self.room}: Skipping invalid nutrient B concentration")
            return
        
        current_value = self.data_store.getDeep("Hydro.Nutrient_Concentration_B")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nutrient_Concentration_B", new_value)
            await self.event_manager.emit("FeedModeValueChange", {"type": "concentration_b", "value": new_value})
            _LOGGER.info(f"{self.room}: Nutrient B concentration updated to {new_value} ml/L")

    async def _update_nutrient_concentration_c(self, data):
        """Update nutrient C concentration (ml/L)"""
        new_value = self._coerce_float(data.newState[0], context="nutrient_concentration_c")
        if new_value is None or new_value < 0:
            _LOGGER.info(f"{self.room}: Skipping invalid nutrient C concentration")
            return
        
        current_value = self.data_store.getDeep("Hydro.Nutrient_Concentration_C")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nutrient_Concentration_C", new_value)
            await self.event_manager.emit("FeedModeValueChange", {"type": "concentration_c", "value": new_value})
            _LOGGER.info(f"{self.room}: Nutrient C concentration updated to {new_value} ml/L")

    async def _update_nutrient_concentration_ph_down(self, data):
        """Update PH- concentration (ml/L)"""
        new_value = self._coerce_float(data.newState[0], context="nutrient_concentration_ph_down")
        if new_value is None or new_value < 0:
            _LOGGER.info(f"{self.room}: Skipping invalid PH- concentration")
            return
        
        current_value = self.data_store.getDeep("Hydro.Nutrient_Concentration_PH_Down")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nutrient_Concentration_PH_Down", new_value)
            await self.event_manager.emit("FeedModeValueChange", {"type": "concentration_ph_down", "value": new_value})
            _LOGGER.info(f"{self.room}: PH- concentration updated to {new_value} ml/L")

    async def _update_nutrient_concentration_x(self, data):
        """Update nutrient X concentration (ml/L)"""
        new_value = self._coerce_float(data.newState[0], context="nutrient_concentration_x")
        if new_value is None or new_value < 0:
            _LOGGER.info(f"{self.room}: Skipping invalid nutrient X concentration")
            return
        
        current_value = self.data_store.getDeep("Hydro.Nutrient_Concentration_X")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nutrient_Concentration_X", new_value)
            await self.event_manager.emit("FeedModeValueChange", {"type": "concentration_x", "value": new_value})
            _LOGGER.info(f"{self.room}: Nutrient X concentration updated to {new_value} ml/L")

    async def _update_nutrient_concentration_y(self, data):
        """Update nutrient Y concentration (ml/L)"""
        new_value = self._coerce_float(data.newState[0], context="nutrient_concentration_y")
        if new_value is None or new_value < 0:
            _LOGGER.info(f"{self.room}: Skipping invalid nutrient Y concentration")
            return
        
        current_value = self.data_store.getDeep("Hydro.Nutrient_Concentration_Y")
        if current_value != new_value:
            self.data_store.setDeep("Hydro.Nutrient_Concentration_Y", new_value)
            await self.event_manager.emit("FeedModeValueChange", {"type": "concentration_y", "value": new_value})
            _LOGGER.info(f"{self.room}: Nutrient Y concentration updated to {new_value} ml/L")

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

    async def _soil_moisture_threshold_sets(self, data):
        """Update global soil moisture thresholds and sync them to all mediums."""
        value = self._coerce_float(
            data.newState[0] if getattr(data, "newState", None) else None,
            context="soil moisture threshold",
        )
        if value is None:
            return

        entity_name = (getattr(data, "Name", "") or "").lower()
        room_suffix = f"_{self.room.lower()}"
        is_min = f"ogb_soilmoisturemin{room_suffix}" in entity_name
        is_max = f"ogb_soilmoisturemax{room_suffix}" in entity_name

        if not (is_min or is_max):
            _LOGGER.debug(
                f"{self.room}: Ignoring soil moisture update for unknown entity '{entity_name}'"
            )
            return

        min_path = "Hydro.PlantWatering.soilMoistureMin"
        max_path = "Hydro.PlantWatering.soilMoistureMax"

        current_min = self._coerce_float(
            self.data_store.getDeep(min_path), context="soil moisture min"
        )
        current_max = self._coerce_float(
            self.data_store.getDeep(max_path), context="soil moisture max"
        )

        if current_min is None:
            current_min = 50.0
        if current_max is None:
            current_max = 50.0

        new_min = value if is_min else current_min
        new_max = value if is_max else current_max

        if new_min > new_max:
            if is_min:
                new_max = new_min
            else:
                new_min = new_max

        self.data_store.setDeep(min_path, new_min)
        self.data_store.setDeep(max_path, new_max)

        grow_mediums = self.data_store.get("growMediums") or []
        medium_count = len(grow_mediums)

        for medium_index in range(medium_count):
            await self.event_manager.emit(
                "UpdateMediumPlantDates",
                {
                    "room": self.room,
                    "medium_index": medium_index,
                    "moisture_min": new_min,
                    "moisture_max": new_max,
                },
                haEvent=True,
            )

        _LOGGER.info(
            f"{self.room}: Soil moisture thresholds updated - min={new_min}, max={new_max}, mediums={medium_count}"
        )

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

    async def _update_farred_smart_start(self, data):
        """Enable/disable smart Far Red start (15 min before main lights)."""
        value = self._string_to_bool(data.newState[0])
        current = self.data_store.getDeep("specialLights.farRed.smartStartEnabled")
        if current != value:
            self.data_store.setDeep("specialLights.farRed.smartStartEnabled", value)
            await self.event_manager.emit("FarRedSettingsUpdate", {"smartStartEnabled": value})
            _LOGGER.info(f"{self.room}: Far Red smart start = {value}")

    async def _update_farred_smart_end(self, data):
        """Enable/disable smart Far Red end (15 min after main lights)."""
        value = self._string_to_bool(data.newState[0])
        current = self.data_store.getDeep("specialLights.farRed.smartEndEnabled")
        if current != value:
            self.data_store.setDeep("specialLights.farRed.smartEndEnabled", value)
            await self.event_manager.emit("FarRedSettingsUpdate", {"smartEndEnabled": value})
            _LOGGER.info(f"{self.room}: Far Red smart end = {value}")

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

    async def _update_uv_midday_start(self, data):
        """Set UV midday start time (preset options)."""
        value = data.newState[0]
        valid_times = ["11:00", "11:30", "12:00", "12:30", "13:00"]
        if value not in valid_times:
            _LOGGER.debug(f"{self.room}: Invalid UV midday start time '{value}', ignoring")
            return
        current = self.data_store.getDeep("specialLights.uv.middayStartTime")
        if current != value:
            self.data_store.setDeep("specialLights.uv.middayStartTime", value)
            await self.event_manager.emit("UVSettingsUpdate", {"middayStartTime": value})
            _LOGGER.info(f"{self.room}: UV midday start = {value}")

    async def _update_uv_midday_end(self, data):
        """Set UV midday end time (preset options)."""
        value = data.newState[0]
        valid_times = ["13:00", "13:30", "14:00", "14:30", "15:00"]
        if value not in valid_times:
            _LOGGER.debug(f"{self.room}: Invalid UV midday end time '{value}', ignoring")
            return
        current = self.data_store.getDeep("specialLights.uv.middayEndTime")
        if current != value:
            self.data_store.setDeep("specialLights.uv.middayEndTime", value)
            await self.event_manager.emit("UVSettingsUpdate", {"middayEndTime": value})
            _LOGGER.info(f"{self.room}: UV midday end = {value}")

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

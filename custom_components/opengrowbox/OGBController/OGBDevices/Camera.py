import logging
import asyncio
import os
import subprocess
import base64
import io
import zipfile
from datetime import datetime, timedelta, timezone, time
from .Device import Device

# Home Assistant imports for scheduling
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
 
_LOGGER = logging.getLogger(__name__)


class Camera(Device):
    def __init__(
        self,
        deviceName,
        deviceData,
        eventManager,
        dataStore,
        deviceType,
        inRoom,
        hass=None,
        deviceLabel="EMPTY",
        allLabels=[],
    ):
        super().__init__(
            deviceName,
            deviceData,
            eventManager,
            dataStore,
            deviceType,
            inRoom,
            hass,
            deviceLabel,
            allLabels,
        )
        
        # Store device data for camera access
        self.deviceData = deviceData
        
        # Initialize camera state
        self.last_image = None
        self.last_capture_time = None
        
        # Timelapse State
        self.tl_active = False
        self.tl_start_time = None
        self.tl_end_time = None  # End time for duration check
        self.tl_image_count = 0
        self._timelapse_unsub = None  # Stores HA timer unsubscribe callback
        self._timelapse_start_unsub = None  # Stores start timer unsubscribe callback (async_track_point_in_time)
        
        # CamConfig Object
        self.ogb_cam_conf = self.dataStore.get("plantsView")

        # HA availability flag for consistent validation
        if hass is None:
            _LOGGER.warning(f"{deviceName}: Camera initialized without hass instance - some features may not work")
            self.hass_available = False
        else:
            self.hass_available = True

        ## Events Register
        self.event_manager.on("TakeImage", self.takeImage)
        self.event_manager.on("StartTL", self.startTL)
        
        # Register HA event listeners for timelapse
        if self.hass:
            self.hass.bus.async_listen("opengrowbox_get_timelapse_config", self._handle_get_timelapse_config)
            self.hass.bus.async_listen("opengrowbox_save_timelapse_config", self._handle_save_timelapse_config)
            self.hass.bus.async_listen("opengrowbox_generate_timelapse", self._handle_generate_timelapse)
            self.hass.bus.async_listen("opengrowbox_get_timelapse_status", self._handle_get_timelapse_status)
            self.hass.bus.async_listen("opengrowbox_start_timelapse", self._handle_start_timelapse)
            self.hass.bus.async_listen("opengrowbox_stop_timelapse", self._handle_stop_timelapse)
            # Register HA event listeners for daily photo operations
            self.hass.bus.async_listen("opengrowbox_get_daily_photos", self._handle_get_daily_photos)
            self.hass.bus.async_listen("opengrowbox_get_daily_photo", self._handle_get_daily_photo)
            self.hass.bus.async_listen("opengrowbox_delete_daily_photo", self._handle_delete_daily_photo)
            self.hass.bus.async_listen("opengrowbox_delete_all_daily", self._handle_delete_all_daily)
            self.hass.bus.async_listen("opengrowbox_download_daily_zip", self._handle_download_daily_zip)
            # Register HA event listeners for timelapse deletion
            self.hass.bus.async_listen("opengrowbox_delete_all_timelapse", self._handle_delete_all_timelapse)
            self.hass.bus.async_listen("opengrowbox_delete_all_timelapse_output", self._handle_delete_all_timelapse_output)
        
        # Timelapse generation state
        self.tl_generation_active = False
        self.tl_generation_progress = 0
        self.tl_generation_status = "idle"
        self.tl_generation_task = None  # Track the background task for cleanup

        # Daily snapshot scheduling state
        self._daily_snapshot_unsub = None

        # Rate limiting for timelapse generation
        self._generation_lock = asyncio.Lock()
        self._last_generation_time = None
        self._generation_cooldown = 5.0  # seconds

        # Initialize camera after setup
        asyncio.create_task(self.init())

    def deviceInit(self, entitys):
        """Minimal initialization for camera - stores entity in options."""
        # Store camera entities
        self.camera_entities = entitys if isinstance(entitys, list) else [entitys]
        
        # Store camera entity in options (like other devices)
        if self.camera_entities:
            for entity in self.camera_entities:
                if isinstance(entity, dict) and entity.get("entity_id", "").startswith("camera."):
                    self.options.append(entity)
        
        # Set initialization flags directly
        self.initialization = True
        self.isInitialized = True
        
        # Use logging like parent class does for consistency
        logging.warning(f"Device: {self.deviceName} Initialization done {self}")
    
    @property
    def camera_entity_id(self):
        """Get the camera entity_id for frontend communication."""
        if hasattr(self, 'camera_entities') and self.camera_entities:
            for entity in self.camera_entities:
                if isinstance(entity, dict):
                    entity_id = entity.get("entity_id", "")
                    if entity_id.startswith("camera."):
                        return entity_id
        return self.deviceName  # Fallback to device name

    async def init(self):
        """Initialize camera device."""
        try:
            # Use Home Assistant config path like OGBDSManager does
            if self.hass:
                base_path = self.hass.config.path("ogb_data")
            else:
                base_path = "/config/ogb_data"
            
            storage_path = os.path.join(base_path, f"{self.inRoom}_img", self.deviceName)
            
            try:
                os.makedirs(storage_path, exist_ok=True)
                _LOGGER.info(f"{self.deviceName}: Created storage directory: {storage_path}")
            except Exception as mkdir_err:
                _LOGGER.warning(f"{self.deviceName}: Could not create storage directory: {mkdir_err}")
                # Fallback to /tmp if not writable
                storage_path = f"/tmp/ogb_data/{self.inRoom}_img/{self.deviceName}"
                os.makedirs(storage_path, exist_ok=True)
                _LOGGER.info(f"{self.deviceName}: Using fallback storage: {storage_path}")
            
            self.camera_storage_path = storage_path

            # Create daily/ subdirectory for daily snapshots
            daily_path = os.path.join(storage_path, "daily")
            try:
                os.makedirs(daily_path, exist_ok=True)
                _LOGGER.info(f"{self.deviceName}: Created daily snapshot directory: {daily_path}")
            except Exception as daily_mkdir_err:
                _LOGGER.warning(f"{self.deviceName}: Could not create daily directory: {daily_mkdir_err}")

            # Create timelapse/ subdirectory for timelapse recordings
            timelapse_path = os.path.join(storage_path, "timelapse")
            try:
                os.makedirs(timelapse_path, exist_ok=True)
                _LOGGER.info(f"{self.deviceName}: Created timelapse directory: {timelapse_path}")
            except Exception as tl_mkdir_err:
                _LOGGER.warning(f"{self.deviceName}: Could not create timelapse directory: {tl_mkdir_err}")

            # Ensure plantsView exists in dataStore for timelapse config
            plants_view = self.dataStore.get("plantsView")
            if not plants_view:
                _LOGGER.warning(f"{self.deviceName}: plantsView not found in dataStore, creating default")
                plants_view = {
                    "isTimeLapseActive": False,
                    "TimeLapseIntervall": "",
                    "StartDate": "",
                    "EndDate": "",
                    "OutPutFormat": "",
                    "tl_image_count": 0,
                    "daily_snapshot_enabled": False,
                    "daily_snapshot_time": "09:00",
                }
                self.dataStore.set("plantsView", plants_view)
            else:
                _LOGGER.info(f"{self.deviceName}: Loaded plantsView from dataStore: {plants_view}")

                # Validate date formats - only accept UTC ISO format (YYYY-MM-DDTHH:MM:SSZ)
                # Invalid dates are cleared to prevent parsing errors
                date_fields_validated = True
                for date_field in ["StartDate", "EndDate"]:
                    date_str = plants_view.get(date_field, "")

                    if not date_str:
                        continue  # Skip empty strings

                    # Check if already in UTC ISO format (ends with 'Z' or has timezone info)
                    if date_str.endswith('Z') or '+' in date_str:
                        try:
                            # Try to parse to verify it's valid
                            datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        except ValueError:
                            # Invalid format - clear it
                            _LOGGER.warning(
                                f"{self.deviceName}: Invalid date format '{date_str}' for {date_field}. "
                                f"Expected UTC ISO (YYYY-MM-DDTHH:MM:SSZ). Clearing field."
                            )
                            plants_view[date_field] = ""
                            date_fields_validated = False
                    else:
                        # Old format detected (datetime-local without timezone) - clear it
                        _LOGGER.warning(
                            f"{self.deviceName}: Old date format '{date_str}' for {date_field}. "
                            f"Expected UTC ISO (YYYY-MM-DDTHH:MM:SSZ). Clearing field."
                        )
                        plants_view[date_field] = ""
                        date_fields_validated = False

                # Save cleaned data back if any changes were made
                if not date_fields_validated:
                    self.dataStore.set("plantsView", plants_view)

                # Check if timelapse was active before restart - resume if needed
                if plants_view.get("isTimeLapseActive", False):
                    start_str = plants_view.get("StartDate", "")
                    end_str = plants_view.get("EndDate", "")

                    # Check if we have valid dates
                    if not start_str or not end_str:
                        # Cannot resume without dates - mark as inactive
                        _LOGGER.warning(
                            f"{self.deviceName}: Timelapse was active but StartDate/EndDate missing - marking inactive"
                        )
                        plants_view["isTimeLapseActive"] = False
                        self.dataStore.set("plantsView", plants_view)
                    else:
                        # Parse dates to check validity (UTC ISO format expected)
                        try:
                            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')) if start_str else None
                            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')) if end_str else None
                        except ValueError:
                            start_dt = None
                            end_dt = None

                        if not start_dt or not end_dt:
                            # Invalid dates - mark as inactive
                            _LOGGER.warning(
                                f"{self.deviceName}: Timelapse was active but dates are invalid - marking inactive"
                            )
                            plants_view["isTimeLapseActive"] = False
                            self.dataStore.set("plantsView", plants_view)
                        else:
                            now = dt_util.now()

                            if end_dt <= now:
                                # End time already passed - mark as completed
                                _LOGGER.info(
                                    f"{self.deviceName}: Timelapse end time passed during restart - marking complete"
                                )
                                plants_view["isTimeLapseActive"] = False
                                self.dataStore.set("plantsView", plants_view)
                            else:
                                # Resume - startTL will figure out immediate vs scheduled
                                _LOGGER.warning(
                                    f"{self.deviceName}: Timelapse was active before restart - resuming"
                                )
                                # Load existing count from plantsView
                                self.tl_image_count = int(plants_view.get("tl_image_count", 0))
                                await self.startTL(resume=True)

            # Schedule daily snapshot if enabled
            if plants_view.get("daily_snapshot_enabled", False):
                await self._schedule_daily_snapshot()
                _LOGGER.info(f"{self.deviceName}: Daily snapshots enabled and scheduled")

            _LOGGER.info(f"{self.deviceName}: Camera initialized (storage: {storage_path})")

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Camera initialization failed: {e}")

    def _validate_storage_path(self, subdirectory="daily"):
        """Validate and return a safe storage path.

        Args:
            subdirectory: Subdirectory to validate (e.g., "daily", "timelapse")

        Returns:
            tuple: (validated_path, storage_base_path)

        Raises:
            ValueError: If path traversal attempt detected.
        """
        storage_path = getattr(self, 'camera_storage_path',
                              f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
        target_path = os.path.join(storage_path, subdirectory)

        # Path validation: resolve to absolute path and check for traversal
        target_path_resolved = os.path.realpath(target_path)
        storage_path_resolved = os.path.realpath(storage_path)

        if not target_path_resolved.startswith(storage_path_resolved):
            raise ValueError(f"Path traversal attempt detected: {target_path}")

        return target_path, storage_path

    async def _start_capturing(self, start_dt, end_dt):
        """Start the interval-based capture scheduler.

        Args:
            start_dt: timezone-aware datetime for when timelapse started
            end_dt: timezone-aware datetime for when timelapse should end
        """
        plants_view = self.dataStore.get("plantsView") or {}
        interval_sec = int(plants_view.get("TimeLapseIntervall", "30") or "30")

        # Store the dates
        self.tl_start_time = start_dt
        self.tl_end_time = end_dt
        self.tl_active = True

        # Take the first image immediately when timelapse starts
        # This ensures we capture at the exact start time, not waiting for first interval
        try:
            # Check plant day (Light logic) before capturing
            is_plant_day = self.dataStore.get("isPlantDay")
            if is_plant_day:
                # Capture Image
                await self.takeImage()

                # Save Image with current timestamp
                if hasattr(self, 'last_image') and self.last_image:
                    # Use timelapse subdirectory
                    storage_base = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
                    image_path = os.path.join(storage_base, "timelapse")

                    # ISO FILENAME FORMAT: {device_name}_YYYYMMDD_HHMMSS.jpg
                    # Uses ISO 8601 date format with underscore separator (filesystem-safe)
                    # Timestamp in LOCAL time for human-readable filenames
                    now_local = dt_util.as_local(dt_util.now())
                    timestamp_str = now_local.strftime("%Y%m%d_%H%M%S")
                    filename = f"{self.deviceName}_{timestamp_str}.jpg"
                    full_path = os.path.join(image_path, filename)

                    await self.saveImage(full_path)
                    self.tl_image_count += 1

                    # Persist updated count to plantsView
                    plants_view = self.dataStore.get("plantsView") or {}
                    plants_view["tl_image_count"] = self.tl_image_count
                    self.dataStore.set("plantsView", plants_view)

                    _LOGGER.info(f"{self.deviceName}: Captured first timelapse image immediately at start")
            else:
                _LOGGER.debug(f"{self.deviceName}: Skipped initial capture - isPlantDay is False (light off)")
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to capture initial timelapse image: {e}")

        # Start the interval scheduler
        self._timelapse_unsub = async_track_time_interval(
            self.hass,
            self._timelapse_callback,
            timedelta(seconds=interval_sec)
        )

        _LOGGER.info(
            f"{self.deviceName}: Timelapse capture started "
            f"(interval: {interval_sec}s, end: {dt_util.as_local(end_dt).isoformat()})"
        )

        # Emit recording started event
        await self.event_manager.emit("CameraRecordingStatus", {
            "room": self.inRoom,
            "camera_entity": self.camera_entity_id,
            "is_recording": True,
            "image_count": self.tl_image_count,
            "start_time": start_dt.isoformat(),
        }, haEvent=True)

    async def _schedule_timelapse_start(self, start_dt, end_dt):
        """Schedule a delayed start for timelapse using async_track_point_in_time.

        Args:
            start_dt: timezone-aware datetime for when to start capturing
            end_dt: timezone-aware datetime for when timelapse should end
        """
        # Store the dates
        self.tl_start_time = start_dt
        self.tl_end_time = end_dt
        self.tl_active = True

        # Schedule the start callback
        # Note: Must use run_coroutine_threadsafe since callback runs in thread executor
        # and _start_capturing is async
        def start_callback(now):
            loop = self.hass.loop
            asyncio.run_coroutine_threadsafe(self._start_capturing(start_dt, end_dt), loop)

        self._timelapse_start_unsub = async_track_point_in_time(
            self.hass,
            start_callback,
            start_dt
        )

        _LOGGER.info(
            f"{self.deviceName}: Timelapse scheduled to start at {dt_util.as_local(start_dt).isoformat()} "
            f"(current time: {dt_util.now().isoformat()})"
        )

        # Emit scheduled event
        await self.event_manager.emit("CameraRecordingStatus", {
            "room": self.inRoom,
            "camera_entity": self.camera_entity_id,
            "is_recording": False,
            "is_scheduled": True,
            "scheduled_start": start_dt.isoformat(),
            "scheduled_end": end_dt.isoformat(),
        }, haEvent=True)

    async def startTL(self, resume=False):
        """Start timelapse capture using StartDate/EndDate from plantsView.

        Dates must be in UTC ISO format (YYYY-MM-DDTHH:MM:SSZ).

        If StartDate <= now: Start capturing immediately.
        If StartDate > now: Schedule start for that time.
        """
        try:
            # Clean up any existing timer
            self._stop_timelapse_internal_timer()

            # Get timelapse configuration from plantsView
            plants_view = self.dataStore.get("plantsView") or {}
            start_str = plants_view.get("StartDate", "")
            end_str = plants_view.get("EndDate", "")

            # Parse dates (UTC ISO format expected)
            try:
                start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')) if start_str else None
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')) if end_str else None
            except ValueError:
                start_dt = None
                end_dt = None

            # Validate dates
            if not start_dt or not end_dt:
                _LOGGER.error(
                    f"{self.deviceName}: Invalid StartDate or EndDate - cannot start timelapse "
                    f"(StartDate: '{start_str}', EndDate: '{end_str}')"
                )
                # Emit error event to frontend
                await self.event_manager.emit("TimelapseError", {
                    "device": self.deviceName,
                    "reason": "invalid_datetime",
                    "message": "Start date and end date must be valid ISO datetime strings"
                }, haEvent=True)
                return

            # Reset image count if not resuming
            if not resume:
                self.tl_image_count = 0
                plants_view["tl_image_count"] = 0

            # Update plantsView
            plants_view["isTimeLapseActive"] = True
            self.dataStore.set("plantsView", plants_view)

            # Get current time
            now = dt_util.now()

            # Decide: start immediately or schedule for later?
            if start_dt <= now:
                # Start capturing immediately
                _LOGGER.info(
                    f"{self.deviceName}: Timelapse starting immediately "
                    f"(StartDate {dt_util.as_local(start_dt).isoformat()} is in the past or now)"
                )
                await self._start_capturing(start_dt, end_dt)
            else:
                # Schedule start for later
                _LOGGER.info(
                    f"{self.deviceName}: Timelapse scheduled for future start "
                    f"(StartDate: {dt_util.as_local(start_dt).isoformat()})"
                )
                await self._schedule_timelapse_start(start_dt, end_dt)

            # Trigger state save to persist isTimeLapseActive flag
            if not resume:
                asyncio.create_task(self.event_manager.emit(
                    "SaveState",
                    {"source": "Camera", "device": self.deviceName, "action": "start_recording"}
                ))

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to start timelapse: {e}")
            self.tl_active = False

    async def _timelapse_callback(self, now):
        """Callback triggered by HA scheduler for timelapse photos."""
        try:
            # 1. Check if we should stop (Duration exceeded)
            # Note: tl_end_time is in UTC (from ISO format), now is timezone-aware
            # Compare directly in UTC for consistent behavior
            now_utc = now.astimezone(timezone.utc)
            # Also get local time for filename (human-readable)
            now_local = dt_util.as_local(now)

            if self.tl_end_time and now_utc > self.tl_end_time:
                _LOGGER.info(f"{self.deviceName}: Timelapse duration exceeded")
                await self._stop_timelapse_and_notify()
                return

            # 2. Check plant day (Light logic)
            is_plant_day = self.dataStore.get("isPlantDay")
            if not is_plant_day:
                _LOGGER.debug(f"{self.deviceName}: Skipping capture - isPlantDay is False (light off)")
                return

            # 3. Capture Image
            await self.takeImage()
            
            # 4. Save Image
            if hasattr(self, 'last_image') and self.last_image:
                # Use timelapse subdirectory
                storage_base = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
                image_path = os.path.join(storage_base, "timelapse")
                
                # ISO FILENAME FORMAT: {device_name}_YYYYMMDD_HHMMSS.jpg
                # Uses ISO 8601 date format with underscore separator (filesystem-safe)
                # Timestamp in LOCAL time for human-readable filenames
                timestamp_str = now_local.strftime("%Y%m%d_%H%M%S")
                filename = f"{self.deviceName}_{timestamp_str}.jpg"
                full_path = os.path.join(image_path, filename)
                
                await self.saveImage(full_path)
                self.tl_image_count += 1

                # Persist updated count to plantsView
                plants_view = self.dataStore.get("plantsView") or {}
                plants_view["tl_image_count"] = self.tl_image_count
                self.dataStore.set("plantsView", plants_view)

                # Emit status
                await self.event_manager.emit("CameraRecordingStatus", {
                        "room": self.inRoom,
                        "camera_entity": self.camera_entity_id,
                        "is_recording": True,
                        "image_count": self.tl_image_count,
                        "start_time": self.tl_start_time.isoformat() if self.tl_start_time else None,
                    }, haEvent=True)
                
                # Trigger state save occasionally? 
                # Doing this every frame might be heavy for SD cards if interval is short.
                # Kept from original code:
                asyncio.create_task(self.event_manager.emit("SaveState", {"source": "Camera", "device": self.deviceName}))

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Timelapse callback error: {e}")

    def _stop_timelapse_internal_timer(self):
        """Cancel all timelapse-related timers (interval and start delay)."""
        # Cancel interval scheduler
        if self._timelapse_unsub is not None:
            self._timelapse_unsub()
            self._timelapse_unsub = None

        # Cancel start scheduler (if waiting for StartDate)
        if self._timelapse_start_unsub is not None:
            self._timelapse_start_unsub()
            self._timelapse_start_unsub = None

    async def _stop_timelapse_and_notify(self):
        """Stops timelapse, cleans up, and notifies."""
        self._stop_timelapse_internal_timer()
        self.tl_active = False
        
        # Calculate duration
        duration = 0
        if self.tl_start_time:
             duration = (dt_util.now() - self.tl_start_time).total_seconds()

        # Update Config
        plants_view = self.dataStore.get("plantsView") or {}
        plants_view["isTimeLapseActive"] = False
        self.dataStore.set("plantsView", plants_view)

        # Emit recording status stopped (for frontend)
        await self.event_manager.emit("CameraRecordingStatus", {
            "room": self.inRoom,
            "camera_entity": self.camera_entity_id,
            "is_recording": False,
            "image_count": self.tl_image_count,
            "start_time": None,
        }, haEvent=True)

        await self.event_manager.emit("TimelapseCompleted", {
            "device": self.deviceName,
            "total_images": self.tl_image_count,
            "duration": duration
        }, haEvent=True)
        
        asyncio.create_task(self.event_manager.emit("SaveState", {"source": "Camera", "device": self.deviceName, "action": "stop_recording"}))


    async def takeImage(self):
        """Handle TakeImage event from OGB system - capture from HA camera entity."""
        try:
            # Get camera entity_id from stored entities
            camera_entity_id = None
            if hasattr(self, 'camera_entities') and self.camera_entities:
                for entity in self.camera_entities:
                    if isinstance(entity, dict):
                        entity_id = entity.get("entity_id", "")
                        if entity_id.startswith("camera."):
                            camera_entity_id = entity_id
                            break
            
            if not camera_entity_id:
                _LOGGER.error(f"{self.deviceName}: No camera entity found")
                return None
            
            # Use HA camera proxy service to get image
            if self.hass:
                try:
                    # Get image from HA camera proxy
                    image_data = await self._get_ha_camera_image(camera_entity_id)
                    
                    if image_data:
                        # Store image data
                        self.last_image = image_data
                        self.last_capture_time = dt_util.now()
                        
                        # Emit image captured event for WebSocket transmission
                        await self.event_manager.emit("CameraImageCaptured", {
                            "device": self.deviceName,
                            "timestamp": self.last_capture_time.isoformat(),
                            "image_data": image_data,
                            "camera_entity": camera_entity_id,
                            "deviceType": self.deviceType
                        }, haEvent=True)
                        
                        _LOGGER.info(f"{self.deviceName}: Image captured successfully from {camera_entity_id}")
                        return image_data
                    else:
                        _LOGGER.warning(f"{self.deviceName}: No image data from camera {camera_entity_id}")
                        return None
                        
                except Exception as ha_err:
                    _LOGGER.error(f"{self.deviceName}: HA camera capture error: {ha_err}")
                    return None
            else:
                _LOGGER.error(f"{self.deviceName}: No HA instance available")
                return None
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to capture image: {e}")
            # Emit error event
            await self.event_manager.emit("CameraError", {
                "device": self.deviceName,
                "error": str(e),
                "timestamp": dt_util.now().isoformat()
            }, haEvent=True)
            return None
    
    async def _get_ha_camera_image(self, entity_id):
        """Get image from HA camera entity directly via component API."""
        try:
            if not self.hass:
                _LOGGER.error(f"{self.deviceName}: No HA instance available")
                return None
            
            # Get camera component and entity directly from HA
            from homeassistant.components.camera import async_get_image
            
            _LOGGER.debug(f"{self.deviceName}: Fetching image from {entity_id} via HA API")
            
            # Use HA's internal async_get_image function
            # This bypasses HTTP and uses internal API with proper auth
            image = await async_get_image(self.hass, entity_id)
            
            if image and image.content:
                # Convert bytes to base64
                image_base64 = base64.b64encode(image.content).decode('utf-8')
                _LOGGER.debug(f"{self.deviceName}: Successfully captured image from {entity_id} ({len(image.content)} bytes)")
                return image_base64
            else:
                _LOGGER.warning(f"{self.deviceName}: No image content from {entity_id}")
                return None
                        
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error fetching HA camera image: {e}")
            return None

    def _sync_save_image(self, path, image_data):
        """Synchronous image save - called via executor."""
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        # Handle different image formats
        if isinstance(image_data, str):
            # Base64 encoded image
            binary_data = base64.b64decode(image_data)
            with open(path, 'wb') as f:
                f.write(binary_data)
        else:
            # Binary image data
            with open(path, 'wb') as f:
                f.write(image_data)
    
    async def saveImage(self, path):
        """Save image data to specified path."""
        try:
            if hasattr(self, 'last_image') and self.last_image:
                # Run sync file operation in executor to avoid blocking
                await self.hass.async_add_executor_job(
                    self._sync_save_image, path, self.last_image
                )
                _LOGGER.debug(f"{self.deviceName}: Image saved to {path}")
            else:
                _LOGGER.warning(f"{self.deviceName}: No image data to save")
                
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to save image to {path}: {e}")

    # ============================================================================
    # Daily Snapshot Scheduling
    # ============================================================================

    async def _capture_daily_snapshot(self):
        """Capture daily snapshot with 3-retry exponential backoff.

        Retry delays: 5s, 15s, 30s
        Reuses _get_ha_camera_image() for actual capture.
        Emits ogb_camera_capture_failed on final failure.

        Returns:
            str: Base64-encoded image data on success, None on failure.
        """
        retry_delays = [5, 15, 30]  # seconds between retries
        camera_entity_id = self.camera_entity_id

        for attempt, delay in enumerate(retry_delays):
            try:
                _LOGGER.debug(
                    f"{self.deviceName}: Daily snapshot capture attempt {attempt + 1}/3"
                )

                # Use _get_ha_camera_image for actual capture
                image_data = await self._get_ha_camera_image(camera_entity_id)

                if image_data:
                    _LOGGER.info(
                        f"{self.deviceName}: Daily snapshot captured successfully "
                        f"(attempt {attempt + 1})"
                    )
                    return image_data
                else:
                    _LOGGER.warning(
                        f"{self.deviceName}: Daily snapshot attempt {attempt + 1} "
                        f"returned no image data"
                    )

            except Exception as e:
                _LOGGER.warning(
                    f"{self.deviceName}: Daily snapshot attempt {attempt + 1} failed: {e}"
                )

            # If not the last attempt, wait before retry
            if attempt < len(retry_delays) - 1:
                _LOGGER.info(
                    f"{self.deviceName}: Retrying daily snapshot in {delay} seconds..."
                )
                await asyncio.sleep(delay)

        # All retries failed
        _LOGGER.error(
            f"{self.deviceName}: Daily snapshot failed after {len(retry_delays)} attempts"
        )

        # Emit failure event
        await self.event_manager.emit(
            "ogb_camera_capture_failed",
            {
                "device": self.deviceName,
                "room": self.inRoom,
                "camera_entity": camera_entity_id,
                "error": f"Failed after {len(retry_delays)} retry attempts",
                "retry_count": len(retry_delays),
            },
            haEvent=True,
        )

        return None

    async def _schedule_daily_snapshot(self):
        """Schedule daily snapshot using async_track_point_in_time().

        Uses HA's dt_util.now() for proper timezone/DST handling.
        Calculates next capture time and schedules callback.
        If the target time has already passed today, schedules for tomorrow.
        """
        try:
            # Get daily snapshot config from plantsView
            plants_view = self.dataStore.get("plantsView") or {}
            enabled = plants_view.get("daily_snapshot_enabled", False)
            config_time = plants_view.get("daily_snapshot_time", "09:00")

            # Cancel any existing scheduled snapshot
            if self._daily_snapshot_unsub is not None:
                self._daily_snapshot_unsub()
                self._daily_snapshot_unsub = None
                _LOGGER.debug(f"{self.deviceName}: Cancelled previous daily snapshot schedule")

            # Only schedule if enabled
            if not enabled:
                _LOGGER.debug(f"{self.deviceName}: Daily snapshots disabled, not scheduling")
                return

            # Parse target time (format: "HH:MM")
            try:
                target_time = time.fromisoformat(config_time)
            except ValueError as e:
                _LOGGER.error(f"{self.deviceName}: Invalid daily_snapshot_time format '{config_time}': {e}")
                return

            # Get current time with HA timezone handling
            now = dt_util.now()

            # Calculate next capture time
            next_capture = now.replace(
                hour=target_time.hour,
                minute=target_time.minute,
                second=0,
                microsecond=0
            )

            # If time has already passed today, schedule for tomorrow
            if next_capture <= now:
                next_capture += timedelta(days=1)

            # Schedule the callback using HA's event system
            self._daily_snapshot_unsub = async_track_point_in_time(
                self.hass,
                self._daily_snapshot_callback,
                next_capture
            )

            _LOGGER.debug(
                f"{self.deviceName}: Scheduled daily snapshot for {next_capture.isoformat()} "
                f"(target: {config_time}, room: {self.inRoom})"
            )

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to schedule daily snapshot: {e}")

    async def _save_daily_photo(self, image_data):
        """Save daily snapshot photo with YYYY-MM-DD_HHMMSS.jpg filename format.

        Args:
            image_data: Base64-encoded image data to save.

        Returns:
            dict: Result with keys:
                - success (bool): True if saved or already exists
                - filename (str): Saved filename
                - path (str): Full path to saved file
                - date (str): Date prefix (YYYY-MM-DD)
                - reason (str): "saved", "already_exists", or error message

        Raises:
            ValueError: If path traversal attempt detected.
        """
        try:
            # Get and validate storage path using helper method
            try:
                daily_path, storage_path = self._validate_storage_path("daily")
                daily_path_resolved = os.path.realpath(daily_path)
                storage_path_resolved = os.path.realpath(storage_path)
            except ValueError as e:
                _LOGGER.error(f"{self.deviceName}: Path validation failed: {e}")
                return {
                    "success": False,
                    "reason": f"Path validation failed: {e}",
                    "filename": None,
                    "path": None,
                    "date": None,
                }

            # Ensure daily directory exists
            try:
                os.makedirs(daily_path, exist_ok=True)
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to create daily directory: {e}")
                return {
                    "success": False,
                    "reason": f"Failed to create daily directory: {e}",
                    "filename": None,
                    "path": None,
                    "date": None,
                }

            # Generate filename with timestamp (YYYY-MM-DD_HHMMSS.jpg format)
            timestamp = dt_util.now().strftime("%Y-%m-%d_%H%M%S")
            filename = f"{timestamp}.jpg"
            full_path = os.path.join(daily_path, filename)

            # Additional path validation on final file path
            full_path_resolved = os.path.realpath(full_path)
            if not full_path_resolved.startswith(daily_path_resolved):
                raise ValueError(f"Path traversal attempt detected: {full_path}")

            # Check if we already have a snapshot for today (to avoid duplicates)
            today_prefix = dt_util.now().strftime("%Y-%m-%d")

            # Use asyncio.to_thread for blocking file I/O
            def _check_existing():
                if not os.path.exists(daily_path):
                    return []
                return [
                    f for f in os.listdir(daily_path)
                    if f.startswith(today_prefix) and f.endswith(".jpg")
                ]

            existing_photos = await asyncio.to_thread(_check_existing)

            if existing_photos:
                _LOGGER.info(
                    f"{self.deviceName}: Daily snapshot already exists for today ({today_prefix}), skipping save"
                )
                return {
                    "success": True,
                    "filename": existing_photos[0],
                    "path": os.path.join(daily_path, existing_photos[0]),
                    "date": today_prefix,
                    "reason": "already_exists",
                }

            # Decode base64 image data to bytes for file write
            def _write_image():
                binary_data = base64.b64decode(image_data)
                with open(full_path, 'wb') as f:
                    f.write(binary_data)
                return full_path

            # Use asyncio.to_thread for blocking file write
            saved_path = await asyncio.to_thread(_write_image)

            _LOGGER.info(f"{self.deviceName}: Daily snapshot saved: {saved_path}")

            return {
                "success": True,
                "filename": filename,
                "path": saved_path,
                "date": today_prefix,
                "reason": "saved",
            }

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to save daily photo: {e}")
            return {
                "success": False,
                "reason": str(e),
                "filename": None,
                "path": None,
                "date": None,
            }

    async def _daily_snapshot_callback(self, *args):
        """Callback triggered when scheduled daily snapshot time arrives.

        Captures an image, saves it to the daily/ subdirectory,
        emits a success event, and reschedules for the next day.
        """
        try:
            _LOGGER.info(f"{self.deviceName}: Daily snapshot triggered")

            # Get storage path
            storage_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            daily_path = os.path.join(storage_path, "daily")

            # Ensure daily directory exists
            try:
                os.makedirs(daily_path, exist_ok=True)
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to create daily directory: {e}")
                # Emit failure event
                await self.event_manager.emit("ogb_camera_capture_failed", {
                    "device": self.deviceName,
                    "room": self.inRoom,
                    "error": f"Failed to create daily directory: {e}",
                    "retry_count": 0,
                }, haEvent=True)
                # Reschedule for next day anyway
                await self._schedule_daily_snapshot()
                return

            # Capture image from camera with retry logic
            image_data = await self._capture_daily_snapshot()

            if image_data:
                # Store image data and timestamp (like takeImage does)
                self.last_image = image_data
                self.last_capture_time = dt_util.now()

                # Save using the new _save_daily_photo method
                result = await self._save_daily_photo(image_data)

                if result["success"]:
                    if result["reason"] == "already_exists":
                        # Emit info event about existing photo
                        await self.event_manager.emit("ogb_camera_daily_photo_exists", {
                            "device": self.deviceName,
                            "room": self.inRoom,
                            "date": result["date"],
                            "existing_file": result["filename"],
                        }, haEvent=True)
                    else:
                        # Emit success event for frontend
                        await self.event_manager.emit("ogb_camera_daily_photo_captured", {
                            "device": self.deviceName,
                            "room": self.inRoom,
                            "camera_entity": self.camera_entity_id,
                            "date": result["date"],
                            "filename": result["filename"],
                            "path": result["path"],
                            "timestamp": dt_util.now().isoformat(),
                        }, haEvent=True)
                else:
                    _LOGGER.warning(f"{self.deviceName}: Failed to save daily photo: {result['reason']}")
                    # Emit failure event
                    await self.event_manager.emit("ogb_camera_capture_failed", {
                        "device": self.deviceName,
                        "room": self.inRoom,
                        "error": result["reason"],
                        "retry_count": 0,
                    }, haEvent=True)
            else:
                # _capture_daily_snapshot already emitted ogb_camera_capture_failed
                _LOGGER.warning(f"{self.deviceName}: Failed to capture daily snapshot after all retries")

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Daily snapshot callback error: {e}")
            # Emit failure event
            await self.event_manager.emit("ogb_camera_capture_failed", {
                "device": self.deviceName,
                "room": self.inRoom,
                "error": str(e),
                "retry_count": 0,
            }, haEvent=True)

        finally:
            # Always reschedule for next day
            await self._schedule_daily_snapshot()

    # ============================================================================
    # Timelapse Event Handlers (HA Event Bus)
    # ============================================================================

    async def _handle_get_timelapse_config(self, event):
        """Handle opengrowbox_get_timelapse_config event from frontend."""
        _LOGGER.info(f"{self.deviceName}: timelapse Event {event}")
        try:
            event_data = event.data
            device_name = event_data.get("device_name")

            # Only respond if this event is for this camera
            # Note: Currently responding to all cameras - device filtering disabled

            # Get current timelapse config from plantsView
            plants_view = self.dataStore.get("plantsView") or {}
            tl_config = {
                "isTimeLapseActive": plants_view.get("isTimeLapseActive", False),
                "TimeLapseIntervall": plants_view.get("TimeLapseIntervall", "30"),
                "StartDate": plants_view.get("StartDate", ""),
                "EndDate": plants_view.get("EndDate", ""),
                "OutPutFormat": plants_view.get("OutPutFormat", "mp4"),
            }
            # Use timelapse subdirectory for storage
            storage_base = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            timelapse_path = os.path.join(storage_base, "timelapse")

            # List available timelapse folders (run in executor to avoid blocking)
            available_timelapses = []
            try:
                if self.hass and os.path.exists(timelapse_path):
                    # Run sync listdir in executor
                    def _list_timelapses():
                        result = []
                        # Count images directly in timelapse directory
                        if os.path.exists(timelapse_path):
                            image_count = len([f for f in os.listdir(timelapse_path) if f.endswith(('.jpg', '.jpeg', '.png'))])
                            if image_count > 0:
                                result.append({
                                    "folder": "timelapse",
                                    "path": timelapse_path,
                                    "image_count": image_count
                                })
                        return result

                    available_timelapses = await self.hass.async_add_executor_job(_list_timelapses)
            except Exception as e:
                _LOGGER.warning(f"{self.deviceName}: Error listing timelapse folders: {e}")
            
            # Get camera entity_id for frontend matching
            camera_entity_id = None
            if hasattr(self, 'camera_entities') and self.camera_entities:
                for entity in self.camera_entities:
                    if isinstance(entity, dict):
                        entity_id = entity.get("entity_id", "")
                        if entity_id.startswith("camera."):
                            camera_entity_id = entity_id
                            break
            
            # Use persisted isTimeLapseActive from plantsView, not just in-memory tl_active
            is_recording_active = plants_view.get("isTimeLapseActive", False) or self.tl_active
            
            config_response = {
                "device_name": camera_entity_id or self.deviceName,
                "storage_path": timelapse_path,
                "current_config": {
                    "interval": tl_config.get("TimeLapseIntervall", "30"),
                    "duration": tl_config.get("duration", 3600),
                    "image_path": tl_config.get("image_path", timelapse_path),
                    "StartDate": tl_config.get("StartDate", ""),
                    "EndDate": tl_config.get("EndDate", ""),
                    "OutPutFormat": tl_config.get("OutPutFormat", "mp4"),
                    "daily_snapshot_enabled": plants_view.get("daily_snapshot_enabled", False),
                    "daily_snapshot_time": plants_view.get("daily_snapshot_time", "09:00"),
                },
                "available_timelapses": available_timelapses,
                "tl_active": is_recording_active,
                "tl_start_time": self.tl_start_time.isoformat() if self.tl_start_time else None,
                "tl_image_count": self.tl_image_count,
            }
            
            # Emit response event
            await self.event_manager.emit("TimelapseConfigResponse", config_response, haEvent=True)
            _LOGGER.info(f"{self.deviceName}: Sent timelapse config")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling get timelapse config: {e}")

    async def _handle_save_timelapse_config(self, event):
        """Handle opengrowbox_save_timelapse_config event from frontend."""
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            
            _LOGGER.debug(f"{self.deviceName}: RECEIVED save_timelapse_config event from {device_name}")
            _LOGGER.debug(f"{self.deviceName}: Event data: {event_data}")
            
            # Only respond if this event is for this camera
            if device_name != self.camera_entity_id:
                _LOGGER.warning(f"{self.deviceName}: Ignoring event - device mismatch (expected {self.camera_entity_id}, got {device_name})")
                return
            
            # Get new config from event
            new_config = event_data.get("config", {})
            _LOGGER.debug(f"{self.deviceName}: New config received: {new_config}")
            
            # Update plantsView in dataStore
            plants_view = self.dataStore.get("plantsView") or {}
            plants_view.update({
                "isTimeLapseActive": new_config.get("isTimeLapseActive", False),
                "TimeLapseIntervall": str(new_config.get("interval", "30")),
                "StartDate": new_config.get("startDate", ""),
                "EndDate": new_config.get("endDate", ""),
                "OutPutFormat": new_config.get("format", "mp4"),
                "daily_snapshot_enabled": new_config.get("daily_snapshot_enabled", False),
                "daily_snapshot_time": new_config.get("daily_snapshot_time", "09:00"),
            })
            self.dataStore.set("plantsView", plants_view)

            # Update daily snapshot scheduling if settings changed
            daily_enabled = plants_view.get("daily_snapshot_enabled", False)
            if daily_enabled:
                await self._schedule_daily_snapshot()
                _LOGGER.info(f"{self.deviceName}: Daily snapshots rescheduled with time {plants_view.get('daily_snapshot_time', '09:00')}")
            else:
                # Cancel existing schedule if disabled
                if self._daily_snapshot_unsub is not None:
                    self._daily_snapshot_unsub()
                    self._daily_snapshot_unsub = None
                    _LOGGER.info(f"{self.deviceName}: Daily snapshots disabled, schedule cancelled")

            # Emit success event
            await self.event_manager.emit("TimelapseConfigSaved", {
                "device_name": self.camera_entity_id,
                "config": plants_view,
                "success": True,
            }, haEvent=True)
            
            _LOGGER.debug(f"{self.deviceName}: Timelapse config saved to plantsView")
            
            # Trigger state save to persist changes
            _LOGGER.debug(f"{self.deviceName}: Triggering SaveState event to persist changes")
            asyncio.create_task(self.event_manager.emit("SaveState", {"source": "Camera", "device": self.deviceName}))
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling save timelapse config: {e}")
            # Emit error event
            await self.event_manager.emit("TimelapseConfigSaved", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)

    async def _handle_generate_timelapse(self, event):
        """Handle opengrowbox_generate_timelapse event from frontend."""
        try:
            event_data = event.data
            device_name = event_data.get("device_name")

            # Only respond if this event is for this camera
            if device_name != self.camera_entity_id:
                return

            # Rate limiting check
            async with self._generation_lock:
                now = dt_util.now()
                if self._last_generation_time:
                    time_since_last = (now - self._last_generation_time).total_seconds()
                    if time_since_last < self._generation_cooldown:
                        _LOGGER.warning(
                            f"{self.deviceName}: Generation request too soon "
                            f"({time_since_last:.1f}s), ignoring"
                        )
                        await self.event_manager.emit("TimelapseGenerationStarted", {
                            "device_name": self.camera_entity_id,
                            "error": "Rate limited - too soon",
                            "success": False,
                        }, haEvent=True)
                        return

                # Check if generation already active
                if self.tl_generation_active:
                    _LOGGER.warning(f"{self.deviceName}: Timelapse generation already in progress")
                    await self.event_manager.emit("TimelapseGenerationStarted", {
                        "device_name": self.camera_entity_id,
                        "error": "Generation already in progress",
                        "success": False,
                    }, haEvent=True)
                    return

                self._last_generation_time = now

            # Get parameters
            start_date = event_data.get("start_date")
            end_date = event_data.get("end_date")
            interval = event_data.get("interval", 30)  # seconds between frames
            output_format = event_data.get("format", "mp4")

            # Start generation in background task and store reference for cleanup
            self.tl_generation_task = asyncio.create_task(
                self._generate_timelapse_video(start_date, end_date, interval, output_format)
            )

            # Emit started event
            await self.event_manager.emit("TimelapseGenerationStarted", {
                "device_name": self.camera_entity_id,
                "start_date": start_date,
                "end_date": end_date,
                "format": output_format,
            }, haEvent=True)

            _LOGGER.info(f"{self.deviceName}: Timelapse generation started")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling generate timelapse: {e}")

    async def _handle_get_timelapse_status(self, event):
        """Handle opengrowbox_get_timelapse_status event from frontend."""
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            
            # Only respond if this event is for this camera
            if device_name != self.camera_entity_id:
                return
            
            # Emit current status
            await self.event_manager.emit("TimelapseStatusResponse", {
                "device_name": self.camera_entity_id,
                "tl_active": self.tl_active,
                "tl_start_time": self.tl_start_time.isoformat() if self.tl_start_time else None,
                "tl_image_count": self.tl_image_count,
                "generation_active": getattr(self, 'tl_generation_active', False),
                "generation_progress": getattr(self, 'tl_generation_progress', 0),
                "generation_status": getattr(self, 'tl_generation_status', 'idle'),
            }, haEvent=True)
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling get timelapse status: {e}")

    async def _handle_start_timelapse(self, event):
        """Handle opengrowbox_start_timelapse event from frontend."""
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            
            # Only respond if this event is for this camera
            if device_name != self.camera_entity_id:
                return
            
            # Get interval from event or use default
            interval = event_data.get("interval", 30)
            
            # Update config temporarily (startTL reads from dataStore)
            plants_view = self.dataStore.get("plantsView") or {}
            plants_view["TimeLapseIntervall"] = str(interval)
            self.dataStore.set("plantsView", plants_view)

            # Start timelapse recording
            await self.startTL()

            _LOGGER.info(f"{self.deviceName}: Timelapse start command processed via event (interval: {interval}s)")

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling start timelapse: {e}")

    async def _handle_stop_timelapse(self, event):
        """Handle opengrowbox_stop_timelapse event from frontend."""
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            
            # Only respond if this event is for this camera
            if device_name != self.camera_entity_id:
                return
            
            # Stop timelapse recording using helper
            await self._stop_timelapse_and_notify()
            
            _LOGGER.info(f"{self.deviceName}: Timelapse recording stopped via event")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling stop timelapse: {e}")

    async def _generate_timelapse_video(self, start_date, end_date, interval, output_format):
        """Generate timelapse video from stored images.

        Args:
            start_date: Start date in UTC ISO format (YYYY-MM-DDTHH:MM:SSZ)
            end_date: End date in UTC ISO format (YYYY-MM-DDTHH:MM:SSZ)
            interval: Seconds between frames
            output_format: Output video format ('mp4' or 'webm')
        """
        try:
            self.tl_generation_active = True
            self.tl_generation_status = "scanning"
            self.tl_generation_progress = 0

            # Use timelapse subdirectory for storage
            storage_base = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            timelapse_path = os.path.join(storage_base, "timelapse")

            # Parse dates (UTC ISO format expected: YYYY-MM-DDTHH:MM:SSZ)
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00')) if start_date else None
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00')) if end_date else None

            # Find all images in date range
            all_images = []
            # Scan timelapse directory for images
            for root, dirs, files in os.walk(timelapse_path):
                for file in files:
                    if file.endswith(('.jpg', '.jpeg', '.png')):
                        file_path = os.path.join(root, file)
                        file_stat = os.stat(file_path)

                        # Create timezone-aware datetime in UTC to match start_dt/end_dt                                                                                                 
                        file_mtime = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)                                                                                         

                        # Check if within date range
                        if start_dt and file_mtime < start_dt:
                            continue
                        if end_dt and file_mtime > end_dt:
                            continue
                        
                        all_images.append({
                            "path": file_path,
                            "mtime": file_mtime,
                            "filename": file,
                        })
            
            # Sort by modification time
            all_images.sort(key=lambda x: x["mtime"])
            
            if len(all_images) == 0:
                _LOGGER.warning(f"{self.deviceName}: No images found for timelapse generation")
                self.tl_generation_status = "error"
                self.tl_generation_active = False
                await self.event_manager.emit("TimelapseGenerationComplete", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "No images found in date range",
                }, haEvent=True)
                return
            
            # Filter by interval
            filtered_images = [all_images[0]]  # Always include first
            last_time = all_images[0]["mtime"]
            
            for img in all_images[1:]:
                time_diff = (img["mtime"] - last_time).total_seconds()
                if time_diff >= interval:
                    filtered_images.append(img)
                    last_time = img["mtime"]
            
            _LOGGER.info(f"{self.deviceName}: Selected {len(filtered_images)} images for timelapse")
            
            # Create output directory in www folder for frontend access via /local/
            if self.hass:
                www_path = self.hass.config.path("www", "ogb_data", f"{self.inRoom}_img", "timelapse_output")
            else:
                www_path = f"/config/www/ogb_data/{self.inRoom}_img/timelapse_output"
            os.makedirs(www_path, exist_ok=True)
            
            timestamp = dt_util.now().strftime("%Y%m%d_%H%M%S")
            
            if output_format == "zip":
                # Create ZIP of images
                import zipfile
                zip_path = os.path.join(www_path, f"timelapse_{self.deviceName}_{timestamp}.zip")
                
                self.tl_generation_status = "creating_zip"
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for i, img in enumerate(filtered_images):
                        # Use original filename to preserve timestamp information
                        arcname = img["filename"]
                        zipf.write(img["path"], arcname)
                        
                        # Update progress
                        self.tl_generation_progress = int((i / len(filtered_images)) * 100)
                        
                        # Emit progress every 10%
                        if i % max(1, len(filtered_images) // 10) == 0:
                            await self.event_manager.emit("TimelapseGenerationProgress", {
                                "device_name": self.camera_entity_id,
                                "progress": self.tl_generation_progress,
                                "status": self.tl_generation_status,
                            }, haEvent=True)
                
                output_path = zip_path
                
            else:
                # Create MP4 video using ffmpeg
                output_path = os.path.join(www_path, f"timelapse_{self.deviceName}_{timestamp}.mp4")
                
                # Create temporary file list for ffmpeg
                list_file = os.path.join(www_path, f"input_list_{timestamp}.txt")
                with open(list_file, 'w') as f:
                    for img in filtered_images:
                        f.write(f"file '{img['path']}'\n")
                        f.write(f"duration {interval}\n")
                    # Last frame needs duration too
                    f.write(f"file '{filtered_images[-1]['path']}'\n")
                
                self.tl_generation_status = "encoding_video"
                
                # Run ffmpeg
                cmd = [
                    "ffmpeg",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", list_file,
                    "-vf", "fps=30,format=yuv420p",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-movflags", "+faststart",
                    "-y",
                    output_path,
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                
                stdout, stderr = await process.communicate()
                
                # Clean up list file
                try:
                    os.remove(list_file)
                    _LOGGER.debug(f"{self.deviceName}: Cleaned up temporary list file: {list_file}")
                except OSError as e:
                    _LOGGER.warning(f"{self.deviceName}: Failed to remove temporary file {list_file}: {e}")
                
                if process.returncode != 0:
                    raise Exception(f"ffmpeg failed: {stderr.decode()}")
            
            # Success
            self.tl_generation_status = "complete"
            self.tl_generation_progress = 100
            
            await self.event_manager.emit("TimelapseGenerationComplete", {
                "device_name": self.camera_entity_id,
                "success": True,
                "output_path": output_path,
                "format": output_format,
                "frame_count": len(filtered_images),
                "download_url": f"/local/ogb_data/{self.inRoom}_img/timelapse_output/{os.path.basename(output_path)}",
            }, haEvent=True)
            
            _LOGGER.info(f"{self.deviceName}: Timelapse generation complete: {output_path}")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Timelapse generation failed: {e}")
            self.tl_generation_status = "error"
            await self.event_manager.emit("TimelapseGenerationComplete", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)
        finally:
            self.tl_generation_active = False

    # ============================================================================
    # Daily Photo Event Handlers (HA Event Bus)
    # ============================================================================

    async def _handle_get_daily_photos(self, event):
        """Handle opengrowbox_get_daily_photos event from frontend.

        Scans the daily/ folder and returns a list of available daily photos
        sorted newest first. Each photo entry contains date and filename.

        Response event: DailyPhotosResponse
        """
        try:
            event_data = event.data
            device_name = event_data.get("device_name")

            # Get storage path
            storage_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            daily_path = os.path.join(storage_path, "daily")

            # List daily photos (run in executor to avoid blocking)
            daily_photos = []
            try:
                if self.hass and os.path.exists(daily_path):
                    # Run sync file operations in executor
                    def _list_daily_photos():
                        result = []
                        if not os.path.exists(daily_path):
                            return result

                        for filename in os.listdir(daily_path):
                            if filename.endswith(('.jpg', '.jpeg', '.png')):
                                # Extract date from filename (format: YYYY-MM-DD_HHMMSS.jpg)
                                date_part = filename.split('_')[0] if '_' in filename else filename
                                file_path = os.path.join(daily_path, filename)

                                # Get file modification time for accurate sorting
                                try:
                                    file_stat = os.stat(file_path)
                                    mtime = file_stat.st_mtime
                                except Exception:
                                    mtime = 0

                                result.append({
                                    "date": date_part,
                                    "filename": filename,
                                    "mtime": mtime,
                                })
                        return result

                    daily_photos = await self.hass.async_add_executor_job(_list_daily_photos)

                    # Sort by modification time (newest first)
                    daily_photos.sort(key=lambda x: x["mtime"], reverse=True)

                    # Remove mtime from response (internal use only)
                    for photo in daily_photos:
                        del photo["mtime"]

            except Exception as e:
                _LOGGER.warning(f"{self.deviceName}: Error listing daily photos: {e}")

            # Get camera entity_id for frontend matching
            camera_entity_id = self.camera_entity_id

            # Emit response event
            await self.event_manager.emit("DailyPhotosResponse", {
                "camera_entity": camera_entity_id,
                "photos": daily_photos,
                "storage_path": daily_path,
                "count": len(daily_photos),
            }, haEvent=True)

            _LOGGER.info(f"{self.deviceName}: Sent daily photos list (count: {len(daily_photos)})")

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling get daily photos: {e}")

    async def _handle_get_daily_photo(self, event):
        """Handle opengrowbox_get_daily_photo event from frontend.

        Reads a daily photo file by date and returns it base64-encoded.

        Args:
            event: HA event with data containing:
                - device_name: Camera device identifier
                - date: Date string (YYYY-MM-DD format)

        Response event: DailyPhotoResponse with base64-encoded image data.
        """
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            date_str = event_data.get("date")

            # Validate date parameter
            if not date_str:
                _LOGGER.error(f"{self.deviceName}: No date provided in get_daily_photo event")
                await self.event_manager.emit("DailyPhotoResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "No date provided",
                }, haEvent=True)
                return

            # Get and validate storage path using helper method
            try:
                daily_path, storage_path = self._validate_storage_path("daily")
                daily_path_resolved = os.path.realpath(daily_path)
                storage_path_resolved = os.path.realpath(storage_path)
            except ValueError as e:
                _LOGGER.error(f"{self.deviceName}: Path validation failed: {e}")
                await self.event_manager.emit("DailyPhotoResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Invalid storage path",
                }, haEvent=True)
                return

            # Find photo file for the requested date
            # Files are named: YYYY-MM-DD_HHMMSS.jpg
            photo_filename = None
            photo_path = None

            try:
                if self.hass and os.path.exists(daily_path):
                    # Run sync file operations in executor
                    def _find_photo_by_date():
                        if not os.path.exists(daily_path):
                            return None, None

                        for filename in os.listdir(daily_path):
                            if filename.endswith(('.jpg', '.jpeg', '.png')):
                                # Check if filename starts with the requested date
                                if filename.startswith(date_str):
                                    file_path = os.path.join(daily_path, filename)

                                    # Additional path validation on final file path
                                    file_path_resolved = os.path.realpath(file_path)
                                    if not file_path_resolved.startswith(daily_path_resolved):
                                        _LOGGER.warning(f"{self.deviceName}: Path traversal attempt detected for {filename}")
                                        continue

                                    return filename, file_path

                        return None, None

                    photo_filename, photo_path = await self.hass.async_add_executor_job(_find_photo_by_date)

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Error finding daily photo: {e}")

            # Check if photo was found
            if not photo_path or not os.path.exists(photo_path):
                _LOGGER.warning(f"{self.deviceName}: No photo found for date {date_str}")
                await self.event_manager.emit("DailyPhotoResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": f"No photo found for date {date_str}",
                    "date": date_str,
                }, haEvent=True)
                return

            # Read and base64 encode the photo file
            def _read_and_encode_photo():
                try:
                    with open(photo_path, 'rb') as f:
                        image_data = f.read()
                    # Encode to base64
                    image_base64 = base64.b64encode(image_data).decode('utf-8')
                    return image_base64
                except Exception as e:
                    _LOGGER.error(f"{self.deviceName}: Error reading photo file: {e}")
                    raise

            try:
                image_base64 = await self.hass.async_add_executor_job(_read_and_encode_photo)

                # Emit success response with base64-encoded image
                await self.event_manager.emit("DailyPhotoResponse", {
                    "camera_entity": self.camera_entity_id,
                    "success": True,
                    "date": date_str,
                    "filename": photo_filename,
                    "image_data": image_base64,
                    "timestamp": dt_util.now().isoformat(),
                }, haEvent=True)

                _LOGGER.info(f"{self.deviceName}: Sent daily photo for {date_str} ({photo_filename})")

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to read/encode photo: {e}")
                await self.event_manager.emit("DailyPhotoResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": f"Failed to read photo: {str(e)}",
                    "date": date_str,
                }, haEvent=True)

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling get daily photo: {e}")
            await self.event_manager.emit("DailyPhotoResponse", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)

    async def _handle_delete_daily_photo(self, event):
        """Handle opengrowbox_delete_daily_photo event from frontend.

        Deletes a single daily photo file by date.

        Args:
            event: HA event with data containing:
                - device_name: Camera device identifier
                - date: Date string (YYYY-MM-DD format)

        Emits:
            - ogb_camera_photo_deleted: On successful deletion
            - DailyPhotoDeletedResponse: Success/error response
        """
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            date_str = event_data.get("date")

            # Validate date parameter
            if not date_str:
                _LOGGER.error(f"{self.deviceName}: No date provided in delete_daily_photo event")
                await self.event_manager.emit("DailyPhotoDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "No date provided",
                }, haEvent=True)
                return

            # Validate date format (YYYY-MM-DD)
            try:
                # Attempt to parse date to validate format
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                _LOGGER.error(f"{self.deviceName}: Invalid date format: {date_str}")
                await self.event_manager.emit("DailyPhotoDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Invalid date format (expected YYYY-MM-DD)",
                }, haEvent=True)
                return

            # Get and validate storage path using helper method
            try:
                daily_path, storage_path = self._validate_storage_path("daily")
                daily_path_resolved = os.path.realpath(daily_path)
                storage_path_resolved = os.path.realpath(storage_path)
            except ValueError as e:
                _LOGGER.error(f"{self.deviceName}: Path validation failed: {e}")
                await self.event_manager.emit("DailyPhotoDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Invalid storage path",
                }, haEvent=True)
                return

            # Find photo file for the requested date
            # Files are named: YYYY-MM-DD_HHMMSS.jpg
            photo_filename = None
            photo_path = None

            try:
                if self.hass and os.path.exists(daily_path):
                    # Run sync file operations in executor
                    def _find_photo_by_date():
                        if not os.path.exists(daily_path):
                            return None, None

                        for filename in os.listdir(daily_path):
                            if filename.endswith(('.jpg', '.jpeg', '.png')):
                                # Check if filename starts with the requested date
                                if filename.startswith(date_str):
                                    file_path = os.path.join(daily_path, filename)

                                    # Additional path validation on final file path
                                    file_path_resolved = os.path.realpath(file_path)
                                    if not file_path_resolved.startswith(daily_path_resolved):
                                        _LOGGER.warning(f"{self.deviceName}: Path traversal attempt detected for {filename}")
                                        continue

                                    return filename, file_path

                        return None, None

                    photo_filename, photo_path = await self.hass.async_add_executor_job(_find_photo_by_date)

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Error finding daily photo: {e}")

            # Check if photo was found
            if not photo_path or not os.path.exists(photo_path):
                _LOGGER.warning(f"{self.deviceName}: No photo found for date {date_str}")
                await self.event_manager.emit("DailyPhotoDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": f"No photo found for date {date_str}",
                    "date": date_str,
                }, haEvent=True)
                return

            # Delete the photo file
            def _delete_photo_file():
                try:
                    os.remove(photo_path)
                    return True
                except Exception as e:
                    _LOGGER.error(f"{self.deviceName}: Error deleting photo file: {e}")
                    raise

            try:
                await self.hass.async_add_executor_job(_delete_photo_file)

                # Emit photo deleted event for frontend listeners
                await self.event_manager.emit("ogb_camera_photo_deleted", {
                    "device": self.deviceName,
                    "room": self.inRoom,
                    "camera_entity": self.camera_entity_id,
                    "date": date_str,
                    "filename": photo_filename,
                    "timestamp": dt_util.now().isoformat(),
                }, haEvent=True)

                # Emit success response
                await self.event_manager.emit("DailyPhotoDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": True,
                    "date": date_str,
                    "filename": photo_filename,
                }, haEvent=True)

                _LOGGER.info(f"{self.deviceName}: Deleted daily photo for {date_str} ({photo_filename})")

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to delete photo: {e}")
                await self.event_manager.emit("DailyPhotoDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": f"Failed to delete photo: {str(e)}",
                    "date": date_str,
                }, haEvent=True)

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling delete daily photo: {e}")
            await self.event_manager.emit("DailyPhotoDeletedResponse", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)

    async def _handle_delete_all_daily(self, event):
        """Handle opengrowbox_delete_all_daily event from frontend.

        Deletes all daily photos for this camera from the daily/ folder.

        Args:
            event: HA event with data containing:
                - device_name: Camera device identifier

        Emits:
            - ogb_camera_all_daily_deleted: On successful deletion
            - DailyAllDeletedResponse: Success/error response with count
        """
        try:
            event_data = event.data
            device_name = event_data.get("device_name")

            # Get storage path and daily directory
            storage_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            daily_path = os.path.join(storage_path, "daily")

            # Path validation: resolve to absolute path and check for traversal
            daily_path_resolved = os.path.realpath(daily_path)
            storage_path_resolved = os.path.realpath(storage_path)

            if not daily_path_resolved.startswith(storage_path_resolved):
                _LOGGER.error(f"{self.deviceName}: Path traversal attempt detected in daily path")
                await self.event_manager.emit("DailyAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Invalid storage path",
                }, haEvent=True)
                return

            # Check if daily folder exists
            if not os.path.exists(daily_path):
                _LOGGER.warning(f"{self.deviceName}: Daily folder does not exist: {daily_path}")
                await self.event_manager.emit("DailyAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": True,
                    "deleted_count": 0,
                    "message": "Daily folder does not exist",
                }, haEvent=True)
                return

            # Delete all photo files
            def _delete_all_photos():
                deleted_count = 0
                try:
                    for filename in os.listdir(daily_path):
                        if filename.endswith(('.jpg', '.jpeg', '.png')):
                            file_path = os.path.join(daily_path, filename)

                            # Additional path validation on each file path
                            file_path_resolved = os.path.realpath(file_path)
                            if not file_path_resolved.startswith(daily_path_resolved):
                                _LOGGER.warning(f"{self.deviceName}: Path traversal attempt detected for {filename}")
                                continue

                            os.remove(file_path)
                            deleted_count += 1

                    return deleted_count
                except Exception as e:
                    _LOGGER.error(f"{self.deviceName}: Error deleting all daily photos: {e}")
                    raise

            try:
                deleted_count = await self.hass.async_add_executor_job(_delete_all_photos)

                # Emit all photos deleted event
                await self.event_manager.emit("ogb_camera_all_daily_deleted", {
                    "device": self.deviceName,
                    "room": self.inRoom,
                    "camera_entity": self.camera_entity_id,
                    "deleted_count": deleted_count,
                    "timestamp": dt_util.now().isoformat(),
                }, haEvent=True)

                # Emit success response
                await self.event_manager.emit("DailyAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": True,
                    "deleted_count": deleted_count,
                }, haEvent=True)

                _LOGGER.info(f"{self.deviceName}: Deleted all daily photos ({deleted_count} files)")

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to delete all daily photos: {e}")
                await self.event_manager.emit("DailyAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": str(e),
                }, haEvent=True)

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling delete all daily photos: {e}")
            await self.event_manager.emit("DailyAllDeletedResponse", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)

    async def _handle_download_daily_zip(self, event):
        """Handle opengrowbox_download_daily_zip event from frontend.

        Generates an in-memory ZIP file containing daily photos, optionally
        filtered by date range. Streams the ZIP as base64 via HA event.

        Args:
            event: HA event with data containing:
                - device_name: Camera device identifier
                - start_date: Optional start date string (YYYY-MM-DD format)
                - end_date: Optional end date string (YYYY-MM-DD format)

        Emits:
            - DailyZipResponse: Success/error response with base64-encoded ZIP data
        """
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            start_date = event_data.get("start_date")
            end_date = event_data.get("end_date")

            # Validate date formats if provided
            if start_date:
                try:
                    datetime.strptime(start_date, "%Y-%m-%d")
                except ValueError:
                    _LOGGER.error(f"{self.deviceName}: Invalid start_date format: {start_date}")
                    await self.event_manager.emit("DailyZipResponse", {
                        "device_name": self.camera_entity_id,
                        "success": False,
                        "error": "Invalid start_date format (expected YYYY-MM-DD)",
                    }, haEvent=True)
                    return

            if end_date:
                try:
                    datetime.strptime(end_date, "%Y-%m-%d")
                except ValueError:
                    _LOGGER.error(f"{self.deviceName}: Invalid end_date format: {end_date}")
                    await self.event_manager.emit("DailyZipResponse", {
                        "device_name": self.camera_entity_id,
                        "success": False,
                        "error": "Invalid end_date format (expected YYYY-MM-DD)",
                    }, haEvent=True)
                    return

            # Get storage path and daily directory
            storage_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            daily_path = os.path.join(storage_path, "daily")

            # Path validation: resolve to absolute path and check for traversal
            daily_path_resolved = os.path.realpath(daily_path)
            storage_path_resolved = os.path.realpath(storage_path)

            if not daily_path_resolved.startswith(storage_path_resolved):
                _LOGGER.error(f"{self.deviceName}: Path traversal attempt detected in daily path")
                await self.event_manager.emit("DailyZipResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Invalid storage path",
                }, haEvent=True)
                return

            # Check if daily folder exists
            if not os.path.exists(daily_path):
                _LOGGER.warning(f"{self.deviceName}: Daily folder does not exist: {daily_path}")
                await self.event_manager.emit("DailyZipResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Daily folder does not exist",
                }, haEvent=True)
                return

            # Collect and filter photos by date range
            def _collect_photos():
                photos = []
                if not os.path.exists(daily_path):
                    return photos

                for filename in os.listdir(daily_path):
                    if filename.endswith(('.jpg', '.jpeg', '.png')):
                        # Extract date from filename (format: YYYY-MM-DD_HHMMSS.jpg)
                        date_part = filename.split('_')[0] if '_' in filename else filename

                        # Filter by date range if provided
                        if start_date and date_part < start_date:
                            continue
                        if end_date and date_part > end_date:
                            continue

                        file_path = os.path.join(daily_path, filename)

                        # Path validation on each file
                        file_path_resolved = os.path.realpath(file_path)
                        if not file_path_resolved.startswith(daily_path_resolved):
                            _LOGGER.warning(f"{self.deviceName}: Path traversal attempt detected for {filename}")
                            continue

                        photos.append((filename, file_path))

                return photos

            try:
                photos = await self.hass.async_add_executor_job(_collect_photos)
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Error collecting photos: {e}")
                await self.event_manager.emit("DailyZipResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": f"Failed to collect photos: {str(e)}",
                }, haEvent=True)
                return

            if not photos:
                _LOGGER.warning(f"{self.deviceName}: No photos found for the specified date range")
                await self.event_manager.emit("DailyZipResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "No photos found for the specified date range",
                }, haEvent=True)
                return

            # Create in-memory ZIP file
            def _create_zip():
                zip_buffer = io.BytesIO()

                # Use ZIP_STORED for JPG (no recompression) - faster and no quality loss
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_STORED) as zipf:
                    for filename, file_path in photos:
                        # Read file and add to ZIP with original filename
                        with open(file_path, 'rb') as f:
                            file_data = f.read()
                        zipf.writestr(filename, file_data)

                # Get ZIP data
                zip_data = zip_buffer.getvalue()
                zip_buffer.close()
                return zip_data

            try:
                zip_data = await self.hass.async_add_executor_job(_create_zip)

                # Encode to base64 for transmission
                zip_base64 = base64.b64encode(zip_data).decode('utf-8')

                # Emit success response with base64-encoded ZIP
                await self.event_manager.emit("DailyZipResponse", {
                    "camera_entity": self.camera_entity_id,
                    "success": True,
                    "zip_data": zip_base64,
                    "photo_count": len(photos),
                    "start_date": start_date,
                    "end_date": end_date,
                    "timestamp": dt_util.now().isoformat(),
                }, haEvent=True)

                _LOGGER.info(
                    f"{self.deviceName}: Generated daily ZIP with {len(photos)} photos "
                    f"(range: {start_date or 'all'} to {end_date or 'all'}, "
                    f"size: {len(zip_data)} bytes)"
                )

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to create ZIP: {e}")
                await self.event_manager.emit("DailyZipResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": f"Failed to create ZIP: {str(e)}",
                }, haEvent=True)

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling download daily ZIP: {e}")
            await self.event_manager.emit("DailyZipResponse", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)

    async def _handle_delete_all_timelapse(self, event):
        """Handle opengrowbox_delete_all_timelapse event from frontend."""
        try:
            event_data = event.data
            device_name = event_data.get("device_name")

            # Get storage path and timelapse directory
            storage_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            timelapse_path = os.path.join(storage_path, "timelapse")

            # Path validation: resolve to absolute path and check for traversal
            timelapse_path_resolved = os.path.realpath(timelapse_path)
            storage_path_resolved = os.path.realpath(storage_path)

            if not timelapse_path_resolved.startswith(storage_path_resolved):
                _LOGGER.error(f"{self.deviceName}: Path traversal attempt detected in timelapse path")
                await self.event_manager.emit("TimelapseAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Invalid storage path",
                }, haEvent=True)
                return

            # Check if timelapse folder exists
            if not os.path.exists(timelapse_path):
                _LOGGER.warning(f"{self.deviceName}: Timelapse folder does not exist: {timelapse_path}")
                await self.event_manager.emit("TimelapseAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": True,
                    "deleted_count": 0,
                    "message": "Timelapse folder does not exist",
                }, haEvent=True)
                return

            # Delete all timelapse photo files
            def _delete_all_timelapse_photos():
                deleted_count = 0
                try:
                    for filename in os.listdir(timelapse_path):
                        if filename.endswith(('.jpg', '.jpeg', '.png')):
                            file_path = os.path.join(timelapse_path, filename)

                            # Additional path validation on each file path
                            file_path_resolved = os.path.realpath(file_path)
                            if not file_path_resolved.startswith(timelapse_path_resolved):
                                _LOGGER.warning(f"{self.deviceName}: Path traversal attempt detected for {filename}")
                                continue

                            os.remove(file_path)
                            deleted_count += 1

                    return deleted_count
                except Exception as e:
                    _LOGGER.error(f"{self.deviceName}: Error deleting all timelapse photos: {e}")
                    raise

            try:
                deleted_count = await self.hass.async_add_executor_job(_delete_all_timelapse_photos)

                # Emit all timelapse photos deleted event
                await self.event_manager.emit("ogb_camera_all_timelapse_deleted", {
                    "device": self.deviceName,
                    "room": self.inRoom,
                    "camera_entity": self.camera_entity_id,
                    "deleted_count": deleted_count,
                    "timestamp": dt_util.now().isoformat(),
                }, haEvent=True)

                # Emit success response
                await self.event_manager.emit("TimelapseAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": True,
                    "deleted_count": deleted_count,
                }, haEvent=True)

                _LOGGER.info(f"{self.deviceName}: Deleted all timelapse photos ({deleted_count} files)")

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to delete all timelapse photos: {e}")
                await self.event_manager.emit("TimelapseAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": str(e),
                }, haEvent=True)

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling delete all timelapse photos: {e}")
            await self.event_manager.emit("TimelapseAllDeletedResponse", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)

    async def _handle_delete_all_timelapse_output(self, event):
        """Handle opengrowbox_delete_all_timelapse_output event from frontend.

        Deletes all timelapse output files (MP4/ZIP) from the www output directory.

        Args:
            event: HA event with data containing:
                - device_name: Camera device identifier

        Emits:
            - ogb_camera_all_timelapse_output_deleted: On successful deletion
            - TimelapseOutputAllDeletedResponse: Success/error response with count
        """
        try:
            event_data = event.data
            device_name = event_data.get("device_name")

            # Get www output path
            www_path = self.hass.config.path("www", "ogb_data", f"{self.inRoom}_img", "timelapse_output")

            # Path validation: resolve to absolute path and check for traversal
            www_path_resolved = os.path.realpath(www_path)
            www_base_resolved = os.path.realpath(self.hass.config.path("www", "ogb_data"))

            if not www_path_resolved.startswith(www_base_resolved):
                _LOGGER.error(f"{self.deviceName}: Path traversal attempt detected in timelapse output path")
                await self.event_manager.emit("TimelapseOutputAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": "Invalid storage path",
                }, haEvent=True)
                return

            # Check if timelapse output folder exists
            if not os.path.exists(www_path):
                _LOGGER.warning(f"{self.deviceName}: Timelapse output folder does not exist: {www_path}")
                await self.event_manager.emit("TimelapseOutputAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": True,
                    "deleted_count": 0,
                    "message": "Timelapse output folder does not exist",
                }, haEvent=True)
                return

            # Delete all timelapse output files (MP4 and ZIP)
            def _delete_all_timelapse_outputs():
                deleted_count = 0
                try:
                    for filename in os.listdir(www_path):
                        if filename.endswith(('.mp4', '.zip')):
                            file_path = os.path.join(www_path, filename)

                            # Additional path validation on each file path
                            file_path_resolved = os.path.realpath(file_path)
                            if not file_path_resolved.startswith(www_path_resolved):
                                _LOGGER.warning(f"{self.deviceName}: Path traversal attempt detected for {filename}")
                                continue

                            os.remove(file_path)
                            deleted_count += 1

                    return deleted_count
                except Exception as e:
                    _LOGGER.error(f"{self.deviceName}: Error deleting all timelapse output files: {e}")
                    raise

            try:
                deleted_count = await self.hass.async_add_executor_job(_delete_all_timelapse_outputs)

                # Emit all timelapse output deleted event
                await self.event_manager.emit("ogb_camera_all_timelapse_output_deleted", {
                    "device": self.deviceName,
                    "room": self.inRoom,
                    "camera_entity": self.camera_entity_id,
                    "deleted_count": deleted_count,
                    "timestamp": dt_util.now().isoformat(),
                }, haEvent=True)

                # Emit success response
                await self.event_manager.emit("TimelapseOutputAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": True,
                    "deleted_count": deleted_count,
                }, haEvent=True)

                _LOGGER.info(f"{self.deviceName}: Deleted all timelapse output files ({deleted_count} files)")

            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Failed to delete all timelapse output files: {e}")
                await self.event_manager.emit("TimelapseOutputAllDeletedResponse", {
                    "device_name": self.camera_entity_id,
                    "success": False,
                    "error": str(e),
                }, haEvent=True)

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling delete all timelapse output: {e}")
            await self.event_manager.emit("TimelapseOutputAllDeletedResponse", {
                "device_name": self.camera_entity_id,
                "success": False,
                "error": str(e),
            }, haEvent=True)

    async def async_cleanup(self):
        """Cleanup when camera device is being removed or HA is stopping.

        Cancels all scheduled tasks including daily snapshots and background generation.
        """
        try:
            # Cancel daily snapshot schedule
            if self._daily_snapshot_unsub is not None:
                self._daily_snapshot_unsub()
                self._daily_snapshot_unsub = None
                _LOGGER.info(f"{self.deviceName}: Cancelled daily snapshot schedule")

            # Cancel background timelapse generation task if active
            if self.tl_generation_task and not self.tl_generation_task.done():
                self.tl_generation_task.cancel()
                try:
                    await self.tl_generation_task
                except asyncio.CancelledError:
                    _LOGGER.info(f"{self.deviceName}: Timelapse generation task cancelled during cleanup")
                self.tl_generation_task = None

            # Stop timelapse scheduler if active
            self._stop_timelapse_internal_timer()

            # Reset active flag
            if self.tl_active:
                self.tl_active = False
                _LOGGER.info(f"{self.deviceName}: Stopped timelapse during cleanup")

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error during cleanup: {e}")
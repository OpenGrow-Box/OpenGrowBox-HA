import logging
import asyncio
import os
import subprocess
import base64
from datetime import datetime, timedelta
from .Device import Device
 
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
        self.tl_active = False
        self.tl_start_time = None
        self.tl_image_count = 0
        
        # CamConfig Object
        self.ogb_cam_conf = self.dataStore.get("plantsView")

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
        
        # Timelapse generation state
        self.tl_generation_active = False
        self.tl_generation_progress = 0
        self.tl_generation_status = "idle"
        
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
                }
                self.dataStore.set("plantsView", plants_view)
            else:
                _LOGGER.info(f"{self.deviceName}: Loaded plantsView from dataStore: {plants_view}")
                
                # Check if timelapse was active before restart - resume if needed
                if plants_view.get("isTimeLapseActive", False):
                    _LOGGER.warning(f"{self.deviceName}: Timelapse was active before restart - resuming recording")
                    # Get interval from saved config
                    interval = int(plants_view.get("TimeLapseIntervall", "30") or "30")
                    # Start timelapse without updating plantsView (already correct)
                    self.tl_active = True
                    self.tl_start_time = datetime.now()
                    self.tl_image_count = 0
                    # Start in background
                    asyncio.create_task(self._run_timelapse(interval, 86400, storage_path))
            
            _LOGGER.info(f"{self.deviceName}: Camera initialized (storage: {storage_path})")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Camera initialization failed: {e}")
            
    async def _capture_rtsp_image(self, config):
        """Capture from RTSP stream."""
        try:
            import cv2
        except ImportError:
            _LOGGER.error(f"{self.deviceName}: OpenCV (cv2) not available for RTSP capture")
            return None
            
        try:
            rtsp_url = config.get("rtsp_url", "")
            if not rtsp_url:
                _LOGGER.error(f"{self.deviceName}: No RTSP URL configured")
                return None
            
            # OpenCV video capture from RTSP
            cap = cv2.VideoCapture(rtsp_url)
            
            # Wait for camera to connect
            await asyncio.sleep(2)
            
            if not cap.isOpened():
                _LOGGER.error(f"{self.deviceName}: Failed to connect to RTSP stream")
                cap.release()
                return None
            
            # Read single frame
            ret, frame = cap.read()
            cap.release()
            
            if ret and frame is not None:
                # Convert to base64
                import base64
                _, buffer = cv2.imencode('.jpg', frame)
                image_base64 = base64.b64encode(buffer).decode('utf-8')
                return image_base64
            else:
                _LOGGER.error(f"{self.deviceName}: Failed to capture frame from RTSP")
                return None
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: RTSP capture error: {e}")
            return None
            
    async def _capture_http_image(self, config):
        """Capture from HTTP camera."""
        try:
            import aiohttp
        except ImportError:
            _LOGGER.error(f"{self.deviceName}: aiohttp not available for HTTP capture")
            return None
            
        import base64
        
        try:
            http_url = config.get("http_url", "")
            snapshot_url = config.get("snapshot_url")
            
            # Use snapshot URL if provided, otherwise main URL
            capture_url = snapshot_url or f"{http_url}/snapshot"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(capture_url, timeout=10) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        
                        # Convert to base64
                        image_base64 = base64.b64encode(image_data).decode('utf-8')
                        return image_base64
                    else:
                        _LOGGER.error(f"{self.deviceName}: HTTP camera returned status {response.status}")
                        return None
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: HTTP capture error: {e}")
            return None
            
    async def _capture_usb_image(self, config):
        """Capture from USB camera."""
        import subprocess
        import base64
        
        try:
            # Common USB camera capture methods
            capture_methods = [
                ["fswebcam", "-r", "1"],
                ["ffmpeg", "-f", "v4l2", "-i", config.get("device", "/dev/video0"), "-vframes", "1", "-f", "image2pipe", "-vcodec", "mjpeg"],
                ["v4l2-ctl", "--device", config.get("device", "/dev/video0"), "--stream-mmap", "--stream-to", "-", "--frames", "1", "--format", "jpeg"]
            ]
            
            for cmd in capture_methods:
                try:
                    result = subprocess.run(cmd, capture_output=True, timeout=10)
                    if result.returncode == 0:
                        image_data = result.stdout
                        
                        # Check if we got valid image data
                        if image_data.startswith(b'\xff\xd8\xff\xe0'):  # JPEG header
                            image_base64 = base64.b64encode(image_data).decode('utf-8')
                            _LOGGER.info(f"{self.deviceName}: USB camera captured successfully")
                            return image_base64
                except subprocess.TimeoutExpired:
                    _LOGGER.warning(f"{self.deviceName}: Capture command timeout: {cmd[0]}")
                except Exception as e:
                    _LOGGER.debug(f"{self.deviceName}: Capture method failed: {cmd[0]} - {e}")
            
            _LOGGER.error(f"{self.deviceName}: All USB camera capture methods failed")
            return None
        
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: USB camera error: {e}")
            return None
            
    async def _run_timelapse(self, interval, duration, image_path):
        """Run timelapse capture loop."""
        if self.tl_start_time is None:
            _LOGGER.error(f"{self.deviceName}: Timelapse start time is None")
            return
            
        end_time = self.tl_start_time + timedelta(seconds=duration)
    
        while self.tl_active and datetime.now() < end_time:
            try:
                # Check if it's plant day (light is on) - only capture when light is on
                is_plant_day = self.dataStore.get("isPlantDay")
                if not is_plant_day:
                    _LOGGER.debug(f"{self.deviceName}: Skipping capture - isPlantDay is False (light off)")
                    await asyncio.sleep(interval)
                    continue
                
                # Capture image using main takeImage method
                await self.takeImage()
                
                # Save image if we have one
                if hasattr(self, 'last_image') and self.last_image:
                    filename = f"{self.deviceName}_{self.tl_image_count:05d}.jpg"
                    full_path = f"{image_path}/{filename}"
                    await self.saveImage(full_path)
                    self.tl_image_count += 1
                    
                    # Emit progress update every 10 images
                    if self.tl_image_count % 10 == 0:
                        await self.event_manager.emit("CameraRecordingStatus", {
                            "room": self.inRoom,
                            "camera_entity": self.camera_entity_id,
                            "is_recording": True,
                            "image_count": self.tl_image_count,
                            "start_time": self.tl_start_time.isoformat() if self.tl_start_time else None,
                        }, haEvent=True)
                
                # Wait for next interval
                await asyncio.sleep(interval)
            
            except asyncio.CancelledError:
                _LOGGER.info(f"{self.deviceName}: Timelapse cancelled")
                break
            except Exception as e:
                _LOGGER.error(f"{self.deviceName}: Timelapse error: {e}")
                await asyncio.sleep(interval)
        
        # Timelapse completed
        self.tl_active = False
        if self.tl_start_time is not None:
            duration = (datetime.now() - self.tl_start_time).total_seconds()
        else:
            duration = 0
            
        await self.event_manager.emit("TimelapseCompleted", {
            "device": self.deviceName,
            "total_images": self.tl_image_count,
            "duration": duration
        }, haEvent=True)

    async def startTL(self):
        """Start timelapse capture."""
        try:
            # Get timelapse configuration from plantsView
            plants_view = self.dataStore.get("plantsView") or {}
            interval = int(plants_view.get("TimeLapseIntervall", "30") or "30")  # seconds
            duration = 86400  # Default 24 hours
            image_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            
            # Initialize timelapse state
            self.tl_active = True
            self.tl_start_time = datetime.now()
            self.tl_image_count = 0
            
            # Update plantsView
            plants_view["isTimeLapseActive"] = True
            self.dataStore.set("plantsView", plants_view)
            
            _LOGGER.info(f"{self.deviceName}: Starting timelapse (interval: {interval}s, duration: {duration}s)")
            
            # Emit recording started event
            await self.event_manager.emit("CameraRecordingStatus", {
                "room": self.inRoom,
                "camera_entity": self.camera_entity_id,
                "is_recording": True,
                "image_count": self.tl_image_count,
                "start_time": self.tl_start_time.isoformat(),
            }, haEvent=True)
            
            # Start timelapse task
            asyncio.create_task(self._run_timelapse(interval, duration, image_path))
            
            # Trigger state save to persist isTimeLapseActive flag
            asyncio.create_task(self.event_manager.emit("SaveState", {"source": "Camera", "device": self.deviceName, "action": "start_recording"}))
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to start timelapse: {e}")

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
                        self.last_capture_time = datetime.now()
                        
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
                "timestamp": datetime.now().isoformat()
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
    # Timelapse Event Handlers (HA Event Bus)
    # ============================================================================

    async def _handle_get_timelapse_config(self, event):
        """Handle opengrowbox_get_timelapse_config event from frontend."""
        _LOGGER.error(f"{self.deviceName}: timelapse Event {event}")
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            
            # Only respond if this event is for this camera
            # if device_name not in self.deviceName:
            #     return
            
            # Get current timelapse config from plantsView
            plants_view = self.dataStore.get("plantsView") or {}
            tl_config = {
                "isTimeLapseActive": plants_view.get("isTimeLapseActive", False),
                "TimeLapseIntervall": plants_view.get("TimeLapseIntervall", "30"),
                "StartDate": plants_view.get("StartDate", ""),
                "EndDate": plants_view.get("EndDate", ""),
                "OutPutFormat": plants_view.get("OutPutFormat", "mp4"),
            }
            storage_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            
            # List available timelapse folders (run in executor to avoid blocking)
            available_timelapses = []
            try:
                if self.hass and os.path.exists(storage_path):
                    # Run sync listdir in executor
                    def _list_timelapses():
                        result = []
                        for folder in os.listdir(storage_path):
                            folder_path = os.path.join(storage_path, folder)
                            if os.path.isdir(folder_path):
                                # Count images in folder
                                image_count = len([f for f in os.listdir(folder_path) if f.endswith(('.jpg', '.jpeg', '.png'))])
                                if image_count > 0:
                                    result.append({
                                        "folder": folder,
                                        "path": folder_path,
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
                "storage_path": storage_path,
                "current_config": {
                    "interval": tl_config.get("TimeLapseIntervall", "30"),
                    "duration": tl_config.get("duration", 3600),
                    "image_path": tl_config.get("image_path", storage_path),
                    "StartDate": tl_config.get("StartDate", ""),
                    "EndDate": tl_config.get("EndDate", ""),
                    "OutPutFormat": tl_config.get("OutPutFormat", "mp4"),
                },
                "available_timelapses": available_timelapses,
                "tl_active": is_recording_active,
                "tl_start_time": self.tl_start_time.isoformat() if self.tl_start_time else None,
                "tl_image_count": self.tl_image_count,
            }
            
            # Emit response event
            await self.event_manager.emit("TimelapseConfigResponse", config_response, haEvent=True)
            _LOGGER.warning(f"{self.deviceName}: Sent timelapse config")
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling get timelapse config: {e}")

    async def _handle_save_timelapse_config(self, event):
        """Handle opengrowbox_save_timelapse_config event from frontend."""
        try:
            event_data = event.data
            device_name = event_data.get("device_name")
            
            _LOGGER.warning(f"{self.deviceName}: RECEIVED save_timelapse_config event from {device_name}")
            _LOGGER.warning(f"{self.deviceName}: Event data: {event_data}")
            
            # Only respond if this event is for this camera
            if device_name != self.camera_entity_id:
                _LOGGER.warning(f"{self.deviceName}: Ignoring event - device mismatch (expected {self.camera_entity_id}, got {device_name})")
                return
            
            # Get new config from event
            new_config = event_data.get("config", {})
            _LOGGER.warning(f"{self.deviceName}: New config received: {new_config}")
            
            # Update plantsView in dataStore
            plants_view = self.dataStore.get("plantsView") or {}
            plants_view.update({
                "isTimeLapseActive": new_config.get("isTimeLapseActive", False),
                "TimeLapseIntervall": str(new_config.get("interval", "30")),
                "StartDate": new_config.get("startDate", ""),
                "EndDate": new_config.get("endDate", ""),
                "OutPutFormat": new_config.get("format", "mp4"),
            })
            self.dataStore.set("plantsView", plants_view)
            
            # Emit success event
            await self.event_manager.emit("TimelapseConfigSaved", {
                "device_name": self.camera_entity_id,
                "config": plants_view,
                "success": True,
            }, haEvent=True)
            
            _LOGGER.warning(f"{self.deviceName}: Timelapse config saved to plantsView")
            
            # Trigger state save to persist changes
            _LOGGER.warning(f"{self.deviceName}: Triggering SaveState event to persist changes")
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
            
            # Get parameters
            start_date = event_data.get("start_date")
            end_date = event_data.get("end_date")
            interval = event_data.get("interval", 30)  # seconds between frames
            output_format = event_data.get("format", "mp4")
            
            # Start generation in background task
            asyncio.create_task(self._generate_timelapse_video(start_date, end_date, interval, output_format))
            
            # Emit started event
            await self.event_manager.emit("TimelapseGenerationStarted", {
                "device_name": self.camera_entity_id,
                "start_date": start_date,
                "end_date": end_date,
                "format": output_format,
            }, haEvent=True)
            
            _LOGGER.warning(f"{self.deviceName}: Timelapse generation started")
            
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
            duration = event_data.get("duration", 86400)  # Default 24 hours
            
            # Start timelapse recording
            await self.startTL()
            
            _LOGGER.warning(f"{self.deviceName}: Timelapse recording started via event (interval: {interval}s)")
            
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
            
            # Stop timelapse recording
            self.tl_active = False
            
            # Emit stopped event
            await self.event_manager.emit("TimelapseStopped", {
                "device_name": self.camera_entity_id,
                "total_images": self.tl_image_count,
                "duration": (datetime.now() - self.tl_start_time).total_seconds() if self.tl_start_time else 0,
            }, haEvent=True)
            
            _LOGGER.info(f"{self.deviceName}: Timelapse recording stopped via event")
            
            # Trigger state save to persist isTimeLapseActive flag
            asyncio.create_task(self.event_manager.emit("SaveState", {"source": "Camera", "device": self.deviceName, "action": "stop_recording"}))
            
        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Error handling stop timelapse: {e}")

    async def _generate_timelapse_video(self, start_date, end_date, interval, output_format):
        """Generate timelapse video from stored images."""
        try:
            self.tl_generation_active = True
            self.tl_generation_status = "scanning"
            self.tl_generation_progress = 0
            
            storage_path = getattr(self, 'camera_storage_path', f"/config/ogb_data/{self.inRoom}_img/{self.deviceName}")
            
            # Parse dates
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00')) if start_date else None
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00')) if end_date else None
            
            # Find all images in date range
            all_images = []
            for root, dirs, files in os.walk(storage_path):
                for file in files:
                    if file.endswith(('.jpg', '.jpeg', '.png')):
                        file_path = os.path.join(root, file)
                        file_stat = os.stat(file_path)
                        file_mtime = datetime.fromtimestamp(file_stat.st_mtime)
                        
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
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if output_format == "zip":
                # Create ZIP of images
                import zipfile
                zip_path = os.path.join(www_path, f"timelapse_{self.deviceName}_{timestamp}.zip")
                
                self.tl_generation_status = "creating_zip"
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for i, img in enumerate(filtered_images):
                        arcname = f"frame_{i:05d}.jpg"
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
                except:
                    pass
                
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

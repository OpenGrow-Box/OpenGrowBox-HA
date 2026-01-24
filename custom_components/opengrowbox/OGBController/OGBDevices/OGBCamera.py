"""
OGBCamera - Camera device class for OpenGrowBox

Implements camera device support for HLS streaming and still image fallback.
"""
import logging
from .Device import Device

_LOGGER = logging.getLogger(__name__)


class OGBCamera(Device):
    """
    Camera device for OpenGrowBox.

    Provides:
    - Camera entity binding and management
    - HLS stream URL retrieval
    - Still image URL fallback
    """

    def __init__(self, deviceName, deviceData, eventManager, dataStore, deviceType, inRoom, hass=None, deviceLabel="EMPTY", allLabels=[]):
        """Initialize camera device."""
        super().__init__(
            deviceName=deviceName,
            deviceData=deviceData,
            eventManager=eventManager,
            dataStore=dataStore,
            deviceType=deviceType,
            inRoom=inRoom,
            hass=hass,
            deviceLabel=deviceLabel,
            allLabels=allLabels
        )
        # Extract entity_id from cameras list (populated by base deviceInit)
        if self.cameras:
            self.entity_id = self.cameras[0].get("entity_id")
        else:
            self.entity_id = None
        self._stream_url = None
        self._image_url = None
        self.capabilities = {}

    def get_hls_url(self):
        """
        Get HLS stream URL for the camera.

        Returns:
            str: HLS stream URL or None if not available
        """
        if not self.entity_id:
            _LOGGER.warning(f"{self.deviceName}: No entity_id, cannot get stream URL")
            return None

        # Return cached URL if available
        if self._stream_url:
            return self._stream_url

        # HLS stream endpoint pattern in Home Assistant
        # The frontend will need to call the camera/stream WebSocket API
        # This is a placeholder for the URL pattern
        self._stream_url = f"/api/camera_proxy_stream/{self.entity_id}"
        _LOGGER.debug(f"{self.deviceName}: HLS stream URL: {self._stream_url}")
        return self._stream_url

    def get_image_url(self):
        """
        Get still image URL for the camera (fallback).

        Returns:
            str: Still image URL
        """
        if not self.entity_id:
            _LOGGER.warning(f"{self.deviceName}: No entity_id, cannot get image URL")
            return None

        if not self._image_url:
            # Camera proxy URL for still images
            self._image_url = f"/api/camera_proxy/{self.entity_id}"
            _LOGGER.debug(f"{self.deviceName}: Image URL: {self._image_url}")

        return self._image_url

    async def bind(self):
        """
        Bind camera entity and emit detection event.
        OGBDeviceManager already filtered by area, so no validation needed.
        """
        try:
            # Emit camera detected event
            await self.event_manager.emit('camera_detected', {
                'entity_id': self.entity_id,
                'device_name': self.deviceName,
                'room': self.inRoom,
                'friendly_name': self.deviceLabel
            })

            _LOGGER.info(f"{self.deviceName}: Camera bound successfully")
            return True

        except Exception as e:
            _LOGGER.error(f"{self.deviceName}: Failed to bind camera: {e}")
            return False

    def __repr__(self):
        """Compact representation for debugging."""
        return (
            f"OGBCamera(name='{self.deviceName}', room='{self.inRoom}', "
            f"entity_id='{self.entity_id}', initialized={self.isInitialized})"
        )

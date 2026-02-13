# Camera System - Live Viewing & Timelapse Recording

## Overview

The Camera System provides comprehensive camera integration for OpenGrowBox, enabling live video streaming, automated daily snapshots, and timelapse video generation for tracking plant growth over time. The system integrates with Home Assistant camera entities and provides both real-time viewing and historical image management.

## System Architecture

### Core Components

#### 1. Camera Device Class (`Camera.py`)
```python
class Camera(Device):
    """Camera device with timelapse and daily snapshot capabilities."""
```

**Features:**
- **Live Streaming**: HLS video streaming with fallback to still images
- **Timelapse Recording**: Configurable interval-based image capture
- **Daily Snapshots**: Scheduled daily photo capture at specified times
- **Video Generation**: MP4 video and ZIP archive creation from captured images
- **Storage Management**: Organized storage with daily/ and timelapse/ subdirectories

#### 2. Frontend Camera Card (`CameraCard.jsx`)
```jsx
const CameraCard = () => {
    // React component for camera interface
}
```

**Features:**
- **Multi-Tab Interface**: Live View, Daily View, Timelapse & Config
- **Real-Time Updates**: WebSocket event subscriptions
- **Progress Tracking**: Timelapse generation progress with visual feedback
- **Storage Management**: Download and delete operations for photos

## Camera Types and Capabilities

### Supported Camera Entities

| Camera Type | Description | Integration Method |
|-------------|-------------|-------------------|
| `camera.*` | Any Home Assistant camera entity | HA Camera Proxy API |
| Generic IP Camera | RTSP/HTTP streaming cameras | HA Camera Integration |
| USB Camera | Direct USB camera connection | HA Camera Integration |
| ESP32 Camera | ESP32-based camera modules | HA ESPHome/WebRTC |

### Camera Capabilities

| Capability | Description | Status |
|------------|-------------|---------|
| `canStream` | Live video streaming support | ✅ Supported |
| `canCapture` | Single image capture | ✅ Supported |
| `canTimelapse` | Timelapse recording | ✅ Supported |
| `canDailySnapshot` | Scheduled daily snapshots | ✅ Supported |
| `canGenerateVideo` | MP4 video generation | ✅ Supported |

## Live Camera Streaming

### Streaming Methods

#### 1. HLS Streaming (Primary)
- **Protocol**: HTTP Live Streaming (HLS)
- **Library**: hls.js for browser compatibility
- **Features**:
  - Adaptive bitrate streaming
  - Low latency (2-5 seconds)
  - Automatic reconnection
  - Buffer management

```javascript
// HLS initialization
const hls = new Hls({
    maxBufferLength: 30,
    maxMaxBufferLength: 60,
    enableWorker: true
});
hls.loadSource(streamResponse.url);
hls.attachMedia(videoRef.current);
```

#### 2. Still Image Fallback
- **Trigger**: HLS timeout (5 seconds) or error
- **Refresh Rate**: Every 5 seconds
- **Authentication**: Bearer token from Home Assistant
- **Format**: Base64-encoded blob URL

```python
# Backend image capture
async def _get_ha_camera_image(self, entity_id):
    from homeassistant.components.camera import async_get_image
    image = await async_get_image(self.hass, entity_id)
    image_base64 = base64.b64encode(image.content).decode('utf-8')
    return image_base64
```

### Stream Status Indicators

| Status | Color | Description |
|--------|--------|-------------|
| `streaming` | Green | Live stream active |
| `connecting` | Yellow | Establishing connection |
| `still` | Blue | Still image mode |
| `error` | Red | Connection failed |
| `idle` | Gray | Ready to connect |

## Timelapse Recording

### Recording Configuration

```python
# Timelapse configuration stored in plantsView
timelapse_config = {
    "isTimeLapseActive": False,
    "TimeLapseIntervall": "300",  # seconds
    "StartDate": "2026-01-12T12:00",
    "EndDate": "2026-01-15T12:00",
    "OutPutFormat": "mp4",  # or "zip"
    "tl_image_count": 0,
}
```

### Capture Intervals

| Interval | Recommended Use | Storage Impact |
|----------|------------------|-----------------|
| 30 seconds | Testing only | Very High |
| 60 seconds | Testing only | Very High |
| 300 seconds (5 min) | Short-term monitoring | High |
| 600 seconds (10 min) | Medium-term monitoring | Medium |
| 900 seconds (15 min) | Recommended | Medium |
| 1800 seconds (30 min) | Long-term monitoring | Low |
| 3600 seconds (1 hour) | Minimal monitoring | Very Low |

### Recording Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Timelapse Recording Flow                        │
└─────────────────────────────────────────────────────────────────────────────┘

1. Configuration
   ├── Set StartDate and EndDate
   ├── Set capture interval (seconds)
   └── Select output format (MP4/ZIP)

2. Start Recording
   ├── Check if StartDate <= now
   │   ├── Yes: Start capturing immediately
   │   └── No: Schedule start for StartDate
   ├── Register HA scheduler: async_track_time_interval()
   └── Set isTimeLapseActive = True

3. Image Capture Loop
   ├── Wait for interval (e.g., 300 seconds)
   ├── Check if isPlantDay (light on)
   │   ├── No: Skip capture
   │   └── Yes: Continue
   ├── Capture image from HA camera entity
   ├── Save to timelapse/ subdirectory
   │   └── Filename: {device_name}_YYYYMMDD_HHMMSS.jpg
   ├── Increment tl_image_count
   ├── Emit CameraRecordingStatus event
   └── Check if EndDate reached
       ├── No: Continue loop
       └── Yes: Stop recording

4. Stop Recording
   ├── Cancel HA scheduler
   ├── Set isTimeLapseActive = False
   ├── Emit TimelapseCompleted event
   └── Save state to dataStore
```

### Plant Day Integration

Timelapse recording respects the plant day/night cycle:

```python
# Check plant day status
is_plant_day = self.dataStore.get("isPlantDay")
if not is_plant_day:
    _LOGGER.debug(f"{self.deviceName}: Skipping capture - isPlantDay is False (light off)")
    return
```

**Behavior:**
- **Light ON**: Capture images at configured interval
- **Light OFF**: Skip capture, wait for next interval
- **Benefit**: Avoids capturing dark images, saves storage

## Daily Snapshots

### Snapshot Scheduling

```python
# Daily snapshot configuration
daily_config = {
    "daily_snapshot_enabled": False,
    "daily_snapshot_time": "09:00",  # HH:MM format
}
```

### Scheduling Mechanism

```python
# Schedule using HA's async_track_point_in_time()
self._daily_snapshot_unsub = async_track_point_in_time(
    self.hass,
    self._daily_snapshot_callback,
    next_capture  # datetime object
)
```

**Features:**
- **Timezone Aware**: Uses HA's configured timezone
- **DST Handling**: Automatic adjustment for daylight saving time
- **Auto-Reschedule**: Automatically schedules next day after capture
- **Duplicate Prevention**: Skips if snapshot already exists for today

### Capture with Retry Logic

Daily snapshots include automatic retry with exponential backoff:

```python
retry_delays = [5, 15, 30]  # seconds between retries

for attempt, delay in enumerate(retry_delays):
    image_data = await self._get_ha_camera_image(camera_entity_id)
    if image_data:
        return image_data  # Success
    await asyncio.sleep(delay)  # Wait before retry
```

**Retry Behavior:**
- **Attempt 1**: Immediate capture
- **Attempt 2**: Wait 5 seconds, retry
- **Attempt 3**: Wait 15 seconds, retry
- **Attempt 4**: Wait 30 seconds, retry
- **Failure**: Emit `ogb_camera_capture_failed` event

## Storage Structure

### Directory Layout

```
/config/ogb_data/
└── {room_name}_img/
    └── {camera_name}/
        ├── daily/                    # Daily snapshots
        │   ├── 2026-01-12_090000.jpg
        │   ├── 2026-01-13_090001.jpg
        │   └── 2026-01-14_090000.jpg
        ├── timelapse/               # Timelapse images
        │   ├── camera_20260112_120000.jpg
        │   ├── camera_20260112_120500.jpg
        │   └── camera_20260112_121000.jpg
        └── (no output files)        # Output files stored elsewhere
```

### Output Storage

Timelapse output files (MP4/ZIP) are stored in the Home Assistant www directory:

```
/config/www/ogb_data/
└── {room_name}_img/
    └── timelapse_output/
        ├── timelapse_camera_20260112_123456.mp4
        └── timelapse_camera_20260112_123456.zip
```

**Access URL:** `/local/ogb_data/{room_name}_img/timelapse_output/{filename}`

### Filename Conventions

| Type | Format | Example |
|------|---------|---------|
| Daily Snapshot | `YYYY-MM-DD_HHMMSS.jpg` | `2026-01-12_090000.jpg` |
| Timelapse Image | `{device_name}_YYYYMMDD_HHMMSS.jpg` | `camera_20260112_120000.jpg` |
| Timelapse Video | `timelapse_{device_name}_{timestamp}.mp4` | `timelapse_camera_1705067056.mp4` |
| Timelapse ZIP | `timelapse_{device_name}_{timestamp}.zip` | `timelapse_camera_1705067056.zip` |

## Video Generation

### MP4 Video Generation

Uses FFmpeg for video encoding:

```python
cmd = [
    "ffmpeg",
    "-f", "concat",
    "-safe", "0",
    "-i", list_file,  # Input file list
    "-vf", "fps=30,format=yuv420p",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "23",
    "-movflags", "+faststart",
    "-y",
    output_path,
]
```

**Encoding Parameters:**
- **Frame Rate**: 30 FPS
- **Codec**: H.264 (libx264)
- **Preset**: fast (encoding speed vs. compression)
- **CRF**: 23 (constant rate factor, 18-28 is typical range)
- **Pixel Format**: YUV 4:2:0 (maximum compatibility)

### ZIP Archive Generation

Creates ZIP archive without recompression:

```python
with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_STORED) as zipf:
    for filename, file_path in photos:
        with open(file_path, 'rb') as f:
            file_data = f.read()
        zipf.writestr(filename, file_data)
```

**Benefits:**
- **Faster**: No recompression needed
- **No Quality Loss**: Original images preserved
- **Smaller File Size**: For already-compressed JPEGs

### Progress Tracking

Generation progress is reported via events:

```python
# Emit progress every 10%
if i % max(1, len(filtered_images) // 10) == 0:
    await self.event_manager.emit("TimelapseGenerationProgress", {
        "device_name": self.camera_entity_id,
        "progress": self.tl_generation_progress,
        "status": self.tl_generation_status,
    }, haEvent=True)
```

**Progress States:**
- `idle`: No generation in progress
- `scanning`: Scanning for images
- `creating_zip`: Creating ZIP archive
- `encoding_video`: Encoding MP4 video
- `complete`: Generation finished successfully
- `error`: Generation failed

## Event System

### Home Assistant Events

| Event Name | Direction | Purpose |
|------------|-----------|---------|
| `opengrowbox_get_timelapse_config` | Frontend → Backend | Request timelapse configuration |
| `opengrowbox_save_timelapse_config` | Frontend → Backend | Save timelapse configuration |
| `opengrowbox_generate_timelapse` | Frontend → Backend | Generate timelapse video/ZIP |
| `opengrowbox_get_timelapse_status` | Frontend → Backend | Get recording status |
| `opengrowbox_start_timelapse` | Frontend → Backend | Start timelapse recording |
| `opengrowbox_stop_timelapse` | Frontend → Backend | Stop timelapse recording |
| `opengrowbox_get_daily_photos` | Frontend → Backend | List daily snapshots |
| `opengrowbox_get_daily_photo` | Frontend → Backend | Get single daily photo |
| `opengrowbox_delete_daily_photo` | Frontend → Backend | Delete single daily photo |
| `opengrowbox_delete_all_daily` | Frontend → Backend | Delete all daily photos |
| `opengrowbox_download_daily_zip` | Frontend → Backend | Download daily photos as ZIP |
| `opengrowbox_delete_all_timelapse` | Frontend → Backend | Delete all timelapse photos |
| `opengrowbox_delete_all_timelapse_output` | Frontend → Backend | Delete all timelapse output |

### Backend Events (Responses)

| Event Name | Direction | Purpose |
|------------|-----------|---------|
| `TimelapseConfigResponse` | Backend → Frontend | Timelapse configuration data |
| `TimelapseConfigSaved` | Backend → Frontend | Configuration save confirmation |
| `TimelapseGenerationStarted` | Backend → Frontend | Generation started notification |
| `TimelapseGenerationProgress` | Backend → Frontend | Progress updates |
| `TimelapseGenerationComplete` | Backend → Frontend | Generation complete with download URL |
| `CameraRecordingStatus` | Backend → Frontend | Recording status updates |
| `TimelapseCompleted` | Backend → Frontend | Recording finished |
| `DailyPhotosResponse` | Backend → Frontend | Daily photos list |
| `DailyPhotoResponse` | Backend → Frontend | Single photo data (base64) |
| `DailyZipResponse` | Backend → Frontend | ZIP download data (base64) |
| `ogb_camera_daily_photo_captured` | Backend → Frontend | New daily photo notification |
| `ogb_camera_daily_photo_exists` | Backend → Frontend | Photo already exists warning |
| `ogb_camera_photo_deleted` | Backend → Frontend | Photo deleted notification |
| `ogb_camera_all_daily_deleted` | Backend → Frontend | All photos deleted notification |
| `ogb_camera_capture_failed` | Backend → Frontend | Capture failure with retry info |
| `ogb_camera_all_timelapse_deleted` | Backend → Frontend | Timelapse photos deleted |
| `ogb_camera_all_timelapse_output_deleted` | Backend → Frontend | Output files deleted |

## Security and Path Validation

### Path Traversal Protection

All file operations include path validation:

```python
# Resolve to absolute path
daily_path_resolved = os.path.realpath(daily_path)
storage_path_resolved = os.path.realpath(storage_path)

# Check for traversal
if not daily_path_resolved.startswith(storage_path_resolved):
    raise ValueError(f"Path traversal attempt detected: {daily_path}")
```

**Protected Operations:**
- Daily photo save
- Daily photo delete
- ZIP file creation
- Timelapse output operations

### Authentication

Camera access requires Home Assistant authentication:

```javascript
// Frontend token retrieval
const getHaToken = () => {
    // Priority 1: HomeAssistantContext accessToken
    if (accessToken) return accessToken;
    
    // Priority 2: HASS entity state
    if (HASS && HASS.states['text.ogb_accesstoken']) {
        return HASS.states['text.ogb_accesstoken'].state;
    }
    
    // Priority 3: localStorage OAuth tokens
    const hassTokens = localStorage.getItem('hassTokens');
    if (hassTokens) {
        return JSON.parse(hassTokens).access_token;
    }
    
    return '';
};
```

## Performance Considerations

### Storage Impact

| Interval | Images/Day | Storage/Day (est.) | Storage/Month |
|----------|-------------|---------------------|---------------|
| 30 seconds | 2,880 | ~1.4 GB | ~42 GB |
| 300 seconds (5 min) | 288 | ~144 MB | ~4.3 GB |
| 900 seconds (15 min) | 96 | ~48 MB | ~1.4 GB |
| 3600 seconds (1 hour) | 24 | ~12 MB | ~360 MB |

**Assumptions:**
- 24-hour plant day
- Average image size: 500 KB
- 30-day month

### CPU Impact

**MP4 Generation:**
- **High CPU Usage**: FFmpeg encoding is CPU-intensive
- **Duration**: Depends on image count and hardware
- **Recommendation**: Use ZIP format for faster processing

**ZIP Generation:**
- **Low CPU Usage**: Simple file archiving
- **Duration**: Much faster than MP4
- **Recommendation**: Use when video encoding is not required

### Network Impact

**Live Streaming:**
- **Bandwidth**: 1-5 Mbps for HD streams
- **Latency**: 2-5 seconds for HLS
- **Recommendation**: Use wired connection for best performance

**Still Image Mode:**
- **Bandwidth**: ~500 KB per image
- **Refresh Rate**: Every 5 seconds
- **Bandwidth/Minute**: ~6 MB

## Troubleshooting

### Common Issues

#### HLS Stream Not Loading

**Symptoms:**
- Video player shows "Connecting..." indefinitely
- Falls back to still image mode

**Solutions:**
1. Check camera entity is available in HA
2. Verify camera supports streaming (not still-only)
3. Check network connectivity to camera
4. Review HA logs for camera errors

#### Timelapse Not Capturing

**Symptoms:**
- Recording status shows "Active" but no images
- `tl_image_count` not incrementing

**Solutions:**
1. Verify `isPlantDay` is True (lights on)
2. Check camera entity is accessible
3. Review HA logs for capture errors
4. Verify storage directory is writable

#### Daily Snapshot Failing

**Symptoms:**
- `ogb_camera_capture_failed` events
- No new daily photos

**Solutions:**
1. Check camera availability at scheduled time
2. Verify HA authentication token is valid
3. Review retry count in failure event
4. Check storage permissions

#### Video Generation Fails

**Symptoms:**
- `TimelapseGenerationComplete` with success: False
- Error message about FFmpeg

**Solutions:**
1. Verify FFmpeg is installed on HA host
2. Check sufficient disk space for output
3. Reduce image count (shorter date range)
4. Use ZIP format as alternative

### Debug Logging

Enable debug logging for camera operations:

```python
# In configuration.yaml
logger:
  default: warning
  logs:
    custom_components.opengrowbox.OGBController.OGBDevices.Camera: debug
```

---

## Camera System Summary

**Camera system implemented!** OpenGrowBox provides comprehensive camera integration.

**Camera Features:**
- ✅ **Live Streaming**: HLS video with still image fallback
- ✅ **Timelapse Recording**: Configurable interval-based capture
- ✅ **Daily Snapshots**: Scheduled daily photo capture
- ✅ **Video Generation**: MP4 and ZIP output formats
- ✅ **Storage Management**: Organized directory structure
- ✅ **Event System**: Real-time status updates
- ✅ **Security**: Path validation and authentication
- ✅ **Performance**: Optimized for various use cases

**Integration Points:**
- ✅ **Home Assistant**: Camera entity integration
- ✅ **DataStore**: Configuration persistence
- ✅ **EventManager**: Event-based communication
- ✅ **WebSocket**: Real-time frontend updates

**For camera setup instructions, see [Device Management Guide](device_management.md)**
**For API event details, see [Frontend Communication Guide](../technical_reference/FRONTEND_COMMUNICATION.md)**

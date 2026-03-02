# Camera Developer Guide - Technical Implementation

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Backend Implementation](#backend-implementation)
3. [Frontend Implementation](#frontend-implementation)
4. [Data Flow](#data-flow)
5. [Event System](#event-system)
6. [API Integration](#api-integration)
7. [Storage Management](#storage-management)
8. [Security Considerations](#security-considerations)

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        Camera System Architecture                        │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend     │    │  Home Assistant │    │   Backend       │
│  (React)       │◄──►│   (HA Core)    │◄──►│  (Python)       │
│                 │    │                 │    │                 │
│ - CameraCard    │    │ - Camera Proxy  │    │ - Camera.py    │
│ - HLS.js       │    │ - Event Bus     │    │ - FFmpeg        │
│ - WebSocket    │    │ - State Machine  │    │ - File I/O      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
       │                      │                      │
       │                      │                      │
       └──────────────────────┴──────────────────────┘
                    WebSocket Events
```

### Technology Stack

| Component | Technology | Purpose |
|----------|-----------|---------|
| **Backend** | Python 3.10+ | Device logic, HA integration |
| **Frontend** | React + Styled Components | User interface, state management |
| **Streaming** | HLS.js | Video streaming in browser |
| **Video Encoding** | FFmpeg | MP4 video generation |
| **Communication** | WebSocket + HA Events | Real-time updates |
| **Storage** | Filesystem | Image and video storage |

## Data Flow

### Timelapse Recording Flow

```
Frontend                    Backend                    HA System
   │                           │                          │
   │ 1. User clicks "Start"    │                          │
   ├──────────────────────────►│                          │
   │                           │ 2. Validate config      │
   │                           ├──────────────────────►│
   │                           │                          │
   │                           │ 3. Schedule timer       │
   │                           ├──────────────────────►│
   │                           │                          │
   │                           │ 4. Timer fires        │
   │                           │                          │ 5. Capture image
   │                           │◄───────────────────────┤
   │◄───────────────────────────┤                          │
   │ 6. Emit status event    │                          │
   │◄───────────────────────────┤                          │
   │                           │ 7. Save to disk        │
   │                           ├──────────────────────►│
   │                           │                          │
   │                           │ 8. Emit update event   │
   │                           ├──────────────────────►│
   │◄───────────────────────────┤                          │
   │ 9. Update UI state       │                          │
   │                           │                          │
   │                           │ 10. End time reached   │
   │                           ├──────────────────────►│
   │                           │ 11. Stop recording     │
   │                           │                          │
   │◄───────────────────────────┤                          │
   │ 12. Emit complete event │                          │
   │◄───────────────────────────┤                          │
   │ 13. Update UI           │                          │
```

### Daily Snapshot Flow

```
Frontend                    Backend                    HA System
   │                           │                          │
   │ 1. User enables daily    │                          │
   │    snapshot                │                          │
   ├──────────────────────────►│                          │
   │                           │ 2. Schedule next time   │
   │                           ├──────────────────────►│
   │                           │                          │
   │                           │ 3. Time reached        │
   │                           │                          │ 4. Capture with retry
   │                           │◄───────────────────────┤
   │◄───────────────────────────┤                          │
   │ 5. Save to disk        │                          │
   │                           ├──────────────────────►│
   │                           │                          │
   │ 6. Emit success event   │                          │
   │                           ├──────────────────────►│
   │◄───────────────────────────┤                          │
   │ 7. Reschedule for       │                          │
   │    next day               ├──────────────────────►│
   │                           │                          │
```

## Event System

### Event Naming Convention

**Request Events (Frontend → Backend):**
- Prefix: `opengrowbox_`
- Format: `opengrowbox_{action}`
- Examples:
  - `opengrowbox_get_timelapse_config`
  - `opengrowbox_save_timelapse_config`
  - `opengrowbox_start_timelapse`
  - `opengrowbox_delete_daily_photo`

**Response Events (Backend → Frontend):**
- No prefix (OGB internal events)
- Format: `{ActionName}Response` or `ogb_camera_{action}`
- Examples:
  - `TimelapseConfigResponse`
  - `TimelapseGenerationComplete`
  - `ogb_camera_daily_photo_captured`
  - `ogb_camera_capture_failed`

### Event Payload Structure

```typescript
// Timelapse Config Request
interface TimelapseConfigRequest {
  event_type: 'opengrowbox_save_timelapse_config';
  event_data: {
    device_name: string;        // Camera entity ID
    config: {
      interval: string;          // Capture interval in seconds
      startDate: string;        // ISO datetime
      endDate: string;          // ISO datetime
      format: 'mp4' | 'zip'; // Output format
      daily_snapshot_enabled: boolean;
      daily_snapshot_time: string; // HH:MM format
    };
  };
}

// Timelapse Config Response
interface TimelapseConfigResponse {
  device_name: string;
  current_config: {
    interval: string;
    StartDate: string;
    EndDate: string;
    OutPutFormat: string;
    daily_snapshot_enabled: boolean;
    daily_snapshot_time: string;
  };
  tl_active: boolean;
  tl_start_time: string | null;  // ISO datetime
  tl_image_count: number;
  available_timelapses: Array<{
    folder: string;
    path: string;
    image_count: number;
  }>;
}

// Recording Status Update
interface CameraRecordingStatus {
  room: string;
  camera_entity: string;
  is_recording: boolean;
  is_scheduled?: boolean;
  scheduled_start?: string;
  image_count: number;
  start_time: string | null;
}

// Generation Progress
interface TimelapseGenerationProgress {
  device_name: string;
  progress: number;  // 0-100
  status: 'scanning' | 'creating_zip' | 'encoding_video';
}

// Generation Complete
interface TimelapseGenerationComplete {
  device_name: string;
  success: boolean;
  output_path?: string;
  format?: string;
  frame_count?: number;
  download_url?: string;
  error?: string;
}
```

## API Integration

### Home Assistant Camera Proxy

**Endpoint:** `/api/camera_proxy/{entity_id}`

**Method:** GET

**Headers:**
```http
Authorization: Bearer {token}
```

**Response:**
- **Content-Type:** `image/jpeg` or camera-specific type
- **Body:** Raw image bytes

**Example:**
```javascript
const response = await fetch(`${baseUrl}/api/camera_proxy/camera.grow_room`, {
  headers: {
    'Authorization': `Bearer ${token}`
  }
});
const blob = await response.blob();
```

### WebSocket Camera Stream

**Request:**
```javascript
{
  type: 'camera/stream',
  entity_id: 'camera.grow_room'
}
```

**Response:**
```javascript
{
  url: 'http://homeassistant:8123/api/camera_proxy_stream/camera.grow_room?token=...'
}
```

**Usage:**
```javascript
const streamResponse = await connection.sendMessagePromise({
  type: 'camera/stream',
  entity_id: selectedCamera
});

if (streamResponse && streamResponse.url) {
  const hls = new Hls();
  hls.loadSource(streamResponse.url);
  hls.attachMedia(videoRef.current);
}
```

### HA Event Bus

**Firing Events:**
```javascript
await connection.sendMessagePromise({
  type: 'fire_event',
  event_type: 'opengrowbox_start_timelapse',
  event_data: {
    device_name: selectedCamera,
    interval: 300,
  },
});
```

**Subscribing to Events:**
```javascript
const unsubscribe = await connection.subscribeEvents(
  (event) => {
    const data = event.data;
    console.log('Received event:', data);
    // Handle event data
  },
  'TimelapseGenerationComplete'
);

// Cleanup
unsubscribe();
```

## Storage Management

### Directory Structure

```
/config/ogb_data/
└── {room_name}_img/
    └── {camera_name}/
        ├── daily/                    # Daily snapshots
        │   ├── 2026-01-12_090000.jpg
        │   ├── 2026-01-13_090001.jpg
        │   └── ...
        ├── timelapse/               # Timelapse source images
        │   ├── camera_20260112_120000.jpg
        │   ├── camera_20260112_120500.jpg
        │   └── ...
        └── (no output files)        # Output stored in www/
```

### Output File Location

```
/config/www/ogb_data/
└── {room_name}_img/
    └── timelapse_output/
        ├── timelapse_camera_20260112_123456.mp4
        └── timelapse_camera_20260112_123456.zip
```

**Access via:** `/local/ogb_data/{room_name}_img/timelapse_output/{filename}`

### File Naming Conventions

| Type | Format | Example |
|------|---------|---------|
| Daily Snapshot | `YYYY-MM-DD_HHMMSS.jpg` | `2026-01-12_090000.jpg` |
| Timelapse Image | `{device_name}_YYYYMMDD_HHMMSS.jpg` | `camera_20260112_120000.jpg` |
| Timelapse Video | `timelapse_{device_name}_{timestamp}.mp4` | `timelapse_camera_1705067056.mp4` |
| Timelapse ZIP | `timelapse_{device_name}_{timestamp}.zip` | `timelapse_camera_1705067056.zip` |

## Security Considerations

### Path Traversal Protection

All file operations validate paths:

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

### Data Validation

**Date Format Validation:**
```python
def _parse_local_datetime(self, date_string: str) -> Optional[datetime]:
    """Parse ISO datetime string as local time."""
    if not date_string:
        return None
    
    try:
        naive_dt = datetime.fromisoformat(date_string)
        now = dt_util.now()
        local_tz = now.tzinfo
        local_dt = naive_dt.replace(tzinfo=local_tz)
        return dt_util.as_local(local_dt)
    except (ValueError, AttributeError) as e:
        _LOGGER.error(f"Failed to parse datetime '{date_string}': {e}")
        return None
```

**Parameter Validation:**
```python
# Validate timelapse configuration
if not start_dt or not end_dt:
    await self._emit_error("invalid_datetime", "Invalid date format")
    return

# Validate interval
try:
    interval_sec = int(interval_str)
    if interval_sec < 30:
        raise ValueError("Interval must be at least 30 seconds")
except ValueError:
    await self._emit_error("invalid_interval", "Invalid interval format")
```

## Performance Optimization

### Async File Operations

Use `async_add_executor_job` for blocking I/O:

```python
# Bad: Blocking file operations
def _delete_all_photos():
    for filename in os.listdir(daily_path):
        os.remove(os.path.join(daily_path, filename))

# Good: Non-blocking file operations
deleted_count = await self.hass.async_add_executor_job(_delete_all_photos)
```

### Memory Management

Clean up blob URLs to prevent memory leaks:

```javascript
// Create blob URL
const blobUrl = URL.createObjectURL(blob);
setImageUrl(blobUrl);

// Cleanup on unmount
useEffect(() => {
  return () => {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
  };
}, []);
```

### Event Cleanup

Always unsubscribe from events on component unmount:

```javascript
useEffect(() => {
  const unsubscribe = await connection.subscribeEvents(
    (event) => { /* handle event */ },
    'TimelapseGenerationComplete'
  );
  
  return () => {
    if (unsubscribe) unsubscribe();
  };
}, []);
```

---

## Camera Developer Guide Summary

**Camera system documented!** Complete technical implementation reference.

**Backend Implementation:**
- ✅ **Camera Class**: Full Python class structure
- ✅ **Image Capture**: HA camera proxy integration
- ✅ **Timelapse Recording**: Interval-based capture with scheduling
- ✅ **Daily Snapshots**: Scheduled capture with retry logic
- ✅ **Video Generation**: FFmpeg MP4 and ZIP archive creation
- ✅ **Storage Management**: Path-validated file operations
- ✅ **Event System**: HA event bus integration

**Frontend Implementation:**
- ✅ **React Component**: Complete component structure
- ✅ **Live Streaming**: HLS.js integration with fallback
- ✅ **State Management**: React hooks for all camera features
- ✅ **Event Subscriptions**: WebSocket event handling
- ✅ **UI Components**: Styled components for camera interface
- ✅ **Error Handling**: User-friendly error messages

**API Integration:**
- ✅ **Camera Proxy**: HA camera proxy API
- ✅ **WebSocket**: Real-time camera streaming
- ✅ **Event Bus**: HA event fire/subscribe
- ✅ **Authentication**: Token-based access control

**For user-facing documentation, see [Camera User Guide](../user_guides/CAMERA_USER_GUIDE.md)**
**For system architecture, see [Architecture Guide](../getting_started/ARCHITECTURE.md)**
**For device management, see [Device Management Guide](../device_management/device_management.md)**

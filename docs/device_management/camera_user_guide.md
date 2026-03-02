# Camera System - User Guide

## Overview

The OpenGrowBox Camera System provides comprehensive grow room monitoring through live streaming, automatic daily snapshots, and timelapse video creation. This guide covers all features and how to use them effectively.

### What You Can Do

- **Live Streaming**: View your grow room in real-time through HLS video streaming with automatic fallback to still images
- **Daily Snapshots**: Automatically capture photos at scheduled times (daily or custom intervals)
- **Timelapse Videos**: Create time-lapse videos from interval-based image capture
- **Photo Management**: Browse, download, delete, and archive your grow photos

### System Architecture

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│   Frontend  │ ◄──────► │ Home Assistant│ ◄──────► │   Camera    │
│ CameraCard  │  Events  │    Event Bus  │  Events │  (Camera.py)│
└─────────────┘         └──────────────┘         └─────────────┘
                              │
                              ▼
                       ┌──────────────┐
                       │ Camera Proxy │
                       │ /api/camera_ │
                       │    proxy/    │
                       └──────────────┘
```

### Prerequisites

Before using the camera system, ensure you have:

1. **Camera Devices Set Up**: At least one camera entity configured in Home Assistant
2. **Camera Integration**: Cameras added to your OGB room configuration
3. **Storage Space**: Sufficient disk space for photos and videos (recommended: 1GB+ per active camera)
4. **FFmpeg Installed**: Required for timelapse video generation (usually included with HA)

### Quick Start Checklist

- [ ] Camera entity configured in Home Assistant
- [ ] Camera added to OGB room
- [ ] Camera visible in CameraCard
- [ ] Live stream working (or still image fallback)
- [ ] Storage directories created automatically

---

## Live Streaming

### Viewing Your Grow Room Live

The CameraCard provides real-time video streaming of your grow room using HLS (HTTP Live Streaming) technology with automatic fallback to still images.

#### How Streaming Works

1. **Primary Method**: The frontend requests an HLS stream from Home Assistant's camera proxy
2. **HLS.js Playback**: If your browser supports HLS.js, the stream plays smoothly
3. **Automatic Fallback**: If streaming fails, the system switches to authenticated still images
4. **Authentication**: All camera access uses your Home Assistant token for security

#### Accessing the Stream

1. Navigate to your OGB dashboard
2. Select the room with your camera
3. Locate the **CameraCard** component
4. The stream starts automatically when you select a camera

#### Camera Selection

If you have multiple cameras in a room:

1. Click the camera dropdown or navigation arrows
2. Select the desired camera from the list
3. The stream automatically switches to the selected camera

#### Stream States

You may see several states during stream initialization:

- **Idle**: No camera selected
- **Connecting**: Establishing connection to camera
- **Streaming**: Live video is playing
- **Still**: Showing still images (stream fallback mode)

#### Troubleshooting Streaming Issues

**Problem: "No Cameras Found"**

- **Cause**: No cameras configured for the current room
- **Solution**:
  1. Check HA Configuration → Devices & Services
  2. Verify camera entities exist
  3. Ensure camera area_id matches your room

**Problem: Stream Won't Start**

- **Cause**: Camera doesn't support HLS or network issues
- **Solution**: System automatically falls back to still images
  - Still images refresh every 30 seconds
  - Use authenticated fetch for security

**Problem: "Stream initialization failed"**

- **Cause**: Timeout waiting for HLS stream (5 second limit)
- **Solution**: Expected behavior - falls back to still images automatically

**Problem: Black Screen**

- **Cause**: Camera not providing data or auth issues
- **Solution**:
  1. Check browser console for errors
  2. Verify camera works in HA frontend
  3. Check camera permissions

**Chrome Reload "No Cameras Found" Bug**:

If you see "No Cameras Found" after reloading Chrome:
- This is a known race condition with HASS data loading
- The system now handles this gracefully - the camera list appears once HASS loads
- Simply wait 1-2 seconds for data to load

#### Supported Camera Types

The camera system works with most Home Assistant camera integrations:

- **Generic Camera**: Any camera entity in HA
- **RTSP Cameras**: Most IP cameras
- **MJPEG Cameras**: Automatic fallback to still images
- **WebRTC Cameras**: If supported by HA
- **USB Cameras**: Locally connected cameras

#### Performance Considerations

- **Bandwidth**: Live streaming uses approximately 1-3 Mbps per camera
- **CPU**: HLS.js decoding uses browser resources
- **Battery**: Mobile devices may drain faster with live streaming
- **Multiple Streams**: Viewing multiple camera streams multiplies resource usage

---

## Daily Snapshots

Daily snapshots automatically capture your grow at consistent intervals, perfect for tracking plant progress over time.

### Understanding Daily Snapshots

Daily snapshots are:
- **Automatic**: Captured at scheduled times
- **Persistent**: Stored indefinitely until deleted
- **Browsable**: Navigate through photos by date
- **Exportable**: Download individual photos or bulk ZIP archives

### Setting Up Automatic Daily Snapshots

#### Enabling Daily Snapshots

1. Open the CameraCard in your OGB dashboard
2. Click the **"Daily"** tab
3. Toggle **"Enable Daily Snapshots"** to ON
4. Set the **Snapshot Time** (default: 09:00)
5. The system automatically schedules daily captures

#### Scheduling Options

**Automatic Mode** (Recommended):
- Photos taken every 24 hours at the specified time
- Uses Home Assistant's scheduling system
- Survives HA restarts

**Manual Capture**:
- Click **"Take Photo Now"** in the Daily tab
- Immediate capture regardless of schedule
- Useful for impromptu documentation

#### Changing Snapshot Time

1. Open **Daily** tab in CameraCard
2. Update the **Snapshot Time** field
3. Changes take effect on the next scheduled capture
4. Existing photos are not affected

### Browsing Daily Photos

#### Viewing Photos by Date

1. Navigate to the **Daily** tab
2. Use left/right arrows to browse by date
3. The most recent photo displays automatically
4. Dates with photos show as active in the navigation

#### Photo List

The daily photo system displays:
- **Date**: When the photo was captured
- **Thumbnail**: Preview of the photo
- **Count**: Total number of stored photos

#### Loading Photos

- Photos load automatically when you select a date
- Large photos may take 1-2 seconds to load
- Previous blob URLs are automatically cleaned up to prevent memory issues

### Managing Daily Photos

#### Deleting Individual Photos

1. Browse to the photo you want to delete
2. Click the **Delete** (trash icon) button
3. Confirm the deletion
4. The photo is permanently removed from storage

#### Deleting All Daily Photos

**Warning**: This action cannot be undone.

1. Open the **Daily** tab
2. Click **"Delete All Photos"**
3. Confirm the deletion
4. All daily photos are permanently removed

#### Downloading Photos as ZIP

**Date Range Export**:

1. Open the **Daily** tab
2. Click **"Download ZIP"**
3. Select a date range (start date and end date)
4. Click **"Generate ZIP"**
5. The file downloads automatically when ready

**What's Included**:
- All daily photos within the date range
- Original quality JPEG files
- Organized by date in filename

**ZIP File Format**:
```
daily_photos_{camera}_{startdate}_{enddate}.zip
├── 2026-01-15.jpg
├── 2026-01-16.jpg
└── 2026-01-17.jpg
```

### Storage & Retention

#### Where Photos Are Stored

Daily snapshots are stored at:
```
/config/ogb_data/{room}_img/{camera}/daily/
├── 2026-01-15_09-00-00.jpg
├── 2026-01-16_09-00-00.jpg
└── 2026-01-17_09-00-00.jpg
```

#### File Naming Convention

```
YYYY-MM-DD_HH-MM-SS.jpg
```

Example: `2026-01-17_09-00-00.jpg`

#### Storage Management

**Automatic Storage Creation**:
- Directories are created automatically when you enable the camera
- No manual configuration required

**Disk Space Usage**:
- Typical photo: 500KB - 2MB
- 30 days of daily snapshots: ~15-60 MB
- Monitor available disk space regularly

**Manual Access**:
- Access photos directly via file system
- Location: `/config/ogb_data/` on your HA server
- Use Samba/SSH add-ons for external access

### Troubleshooting Daily Snapshots

**Problem: Photos Not Appearing**

- **Cause**: Scheduling issue or camera capture failure
- **Solution**:
  1. Check Settings → System → Logs for "Camera" errors
  2. Verify camera entity is working in HA
  3. Try manual capture to test
  4. Check available disk space

**Problem: Old Photos Missing**

- **Cause**: Manual deletion or storage corruption
- **Solution**: Photos deleted cannot be recovered
  - Implement regular backups if needed
  - Use ZIP download feature to archive important periods

**Problem: ZIP Download Fails**

- **Cause**: No photos in date range or generation error
- **Solution**:
  1. Verify date range contains photos
  2. Check logs for "zipfile" errors
  3. Ensure sufficient disk space for ZIP creation

---

## Timelapse Creation

Timelapses compress days or weeks of growth into minutes of video, perfect for documenting plant progress and creating sharing content.

### Understanding Timelapse Workflow

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Configure  │ -> │   Record     │ -> │   Generate   │
│   Settings   │    │   Images     │    │    Video     │
└──────────────┘    └──────────────┘    └──────────────┘
                          │                    │
                          ▼                    ▼
                   ┌──────────────┐    ┌──────────────┐
                   │Interval-based│    │ FFmpeg       │
                   │capture every│    │Processing    │
                   │ X seconds   │    │              │
                   └──────────────┘    └──────────────┘
```

### Configuring Timelapse Settings

#### Accessing Timelapse Configuration

1. Open CameraCard in OGB dashboard
2. Click the **"Timelapse"** tab
3. Configure the following settings:

#### Basic Settings

**Start Date**:
- When the timelapse should begin capturing
- Format: `YYYY-MM-DDTHH:mm` (local time)
- Example: `2026-01-15T08:00`

**End Date**:
- When the timelapse should stop capturing
- Must be after the start date
- Example: `2026-02-15T08:00`

**Interval** (seconds):
- How often to capture a photo
- Common intervals:
  - `60` - Every minute (detailed timelapse)
  - `300` - Every 5 minutes (balanced)
  - `600` - Every 10 minutes (storage-efficient)
  - `3600` - Every hour (long-term monitoring)

**Output Format**:
- Video format for the generated timelapse
- Supported formats:
  - `mp4` - Most compatible, recommended
  - `webm` - Web-optimized
  - `avi` - Legacy format

#### Advanced Options

**Daily Snapshot Integration**:
- Enable "Daily Snapshot" to capture at specific times daily
- Works alongside interval-based capture
- Useful for consistent lighting conditions

#### Saving Configuration

1. Click **"Save Configuration"** after setting all fields
2. Configuration persists across HA restarts
3. Can be modified while recording is active
4. Changes apply on next capture cycle

### Starting a Timelapse Recording

#### Automatic Start (Scheduled)

If you set a start date in the future:
1. Save your timelapse configuration
2. The system schedules the recording automatically
3. Capturing begins at the specified start time
4. Status shows "Scheduled" until start time

#### Immediate Start

To start capturing immediately:

1. Set start date to current time
2. Set desired end date
3. Click **"Start Recording"**
4. Capturing begins within one interval period

#### What Happens When You Start

1. **Backend Scheduling**:
   - HA schedules interval-based captures
   - Timer created based on your interval setting

2. **Storage Preparation**:
   - Directory created: `/config/ogb_data/{room}_img/{camera}/timelapse/`
   - Images stored with timestamp filenames

3. **Status Updates**:
   - Frontend shows "Recording" status
   - Image count updates in real-time
   - Start time logged

### Monitoring Recording Progress

#### Recording Status Display

While recording, the CameraCard shows:

- **Active Indicator**: "Recording" badge or icon
- **Image Count**: Number of photos captured so far
- **Start Time**: When recording began
- **Elapsed Time**: How long recording has been active

#### Real-time Updates

Status updates every few seconds via HA events:
- New image count
- Recording state changes
- Progress percentage

#### Expected Image Counts

Calculate expected images:
```
Total Hours × (3600 / Interval) = Total Images
```

Examples:
- 24 hours @ 300s interval = ~288 images
- 7 days @ 600s interval = ~1,008 images
- 30 days @ 3600s interval = ~720 images

### Stopping a Recording

#### Manual Stop

1. Click **"Stop Recording"** in the Timelapse tab
2. Confirmation dialog appears
3. Recording stops immediately
4. All captured images are preserved

#### Automatic Stop

Recording stops automatically when:
- End date/time is reached
- HA restarts (recording resumes if end time hasn't passed)

#### What Happens When You Stop

1. **Capture Cessation**:
   - Interval timer is cancelled
   - No more images captured
   - Existing images preserved

2. **Status Update**:
   - Status changes to "Stopped"
   - Final image count frozen
   - Ready for video generation

### Generating the Timelapse Video

#### Pre-Generation Checklist

Before generating:
- [ ] Recording is stopped
- [ ] Sufficient images captured (100+ recommended)
- [ ] Available disk space for video (typically 50-200 MB)
- [ ] FFmpeg installed (usually included with HA)

#### Generation Process

1. Click **"Generate Timelapse"** in the Timelapse tab
2. Progress bar appears showing completion percentage
3. Backend processes images with FFmpeg
4. Video file created in timelapse directory
5. Download starts automatically when complete

#### Generation Timeline

Typical generation times:
- 100 images @ 1080p: ~30-60 seconds
- 500 images @ 1080p: ~2-5 minutes
- 1000+ images @ 1080p: ~5-15 minutes

#### Progress Updates

Progress updates every 5% via HA events:
- 0%: Generation started
- 25%: Images being processed
- 50%: Encoding in progress
- 75%: Finalizing video
- 100%: Complete, downloading...

#### Video Generation Settings

The backend uses FFmpeg with these defaults:
- **Frame Rate**: 30 fps
- **Encoding**: H.264 (mp4) or VP9 (webm)
- **Quality**: High quality preset
- **Resolution**: Matches source images

### Downloading and Managing Videos

#### Automatic Download

When generation completes:
1. Video downloads automatically to your browser
2. Filename includes camera name and timestamp
3. Example: `timelapse_camera1_1705487123456.mp4`

#### Manual Download Access

Videos are stored at:
```
/config/ogb_data/{room}_img/{camera}/timelapse/output/
├── timelapse_2026-01-17.mp4
└── timelapse_2026-01-20.mp4
```

Access via:
- Samba add-on
- SSH/SCP
- File Editor add-on

#### Deleting Timelapse Data

**Delete All Raw Images**:
1. Click **"Delete All Timelapse Images"**
2. WARNING: Deletes all captured images
3. Generated videos are NOT deleted
4. Frees significant disk space

**Delete Output Videos**:
1. Click **"Delete All Timelapse Videos"**
2. WARNING: Deletes all generated videos
3. Raw images are preserved
4. Can regenerate videos from images

### Advanced Timelapse Features

#### Surviving HA Restarts

If HA restarts during recording:
1. System checks active timelapse status on startup
2. If end time hasn't passed, recording resumes
3. Image count preserved from dataStore
4. No images lost

#### Daily Snapshot Integration

Combine interval capture with daily snapshots:
1. Enable "Daily Snapshot" in timelapse config
2. Set daily snapshot time (e.g., 09:00)
3. Both systems capture independently
4. More comprehensive coverage

#### Multiple Timelapse Sessions

You can:
- Run multiple timelapse sessions sequentially
- Previous images preserved until deleted
- Generate multiple videos from same images
- Delete old sessions to free space

### Troubleshooting Timelapse Issues

**Problem: Recording Won't Start**

- **Cause**: Invalid dates or camera not ready
- **Solution**:
  1. Verify end date is after start date
  2. Check camera entity is working
  3. Review logs for "startTL" errors

**Problem: Low Image Count**

- **Cause**: Interval too long or short duration
- **Solution**:
  1. Reduce interval for more images
  2. Extend end date for longer recording
  3. Minimum 100 images recommended for good timelapses

**Problem: Generation Fails**

- **Cause**: FFmpeg missing or insufficient images
- **Solution**:
  1. Check FFmpeg is installed: `ffmpeg -version`
  2. Verify image directory has files
  3. Check logs for subprocess errors
  4. Ensure sufficient disk space

**Problem: Video Quality Poor**

- **Cause**: Low-quality source images or compression
- **Solution**:
  1. Check camera image quality settings
  2. Ensure good lighting during captures
  3. Adjust FFmpeg settings (requires code modification)

**Problem: "Generation Stuck at X%"**

- **Cause**: Large image set processing
- **Solution**:
  1. Be patient - large sets take time
  2. Check CPU usage in HA
  3. Monitor logs for progress updates
  4. If stuck > 30 minutes, restart HA

---

## Storage & Management

### Understanding Camera Storage Architecture

```
/config/ogb_data/
├── {room_name}_img/
│   ├── {camera_1}/
│   │   ├── daily/          # Daily snapshots
│   │   │   ├── 2026-01-15_09-00-00.jpg
│   │   │   └── 2026-01-16_09-00-00.jpg
│   │   └── timelapse/      # Timelapse data
│   │       ├── 2026-01-15_10-30-45.jpg
│   │       ├── 2026-01-15_10-35-45.jpg
│   │       └── output/     # Generated videos
│   │           └── timelapse_2026-01-20.mp4
│   └── {camera_2}/
│       └── ...
```

### Storage Space Planning

#### Estimated Usage Per Camera

**Daily Snapshots**:
- Per photo: 500KB - 2MB
- 30 days: ~15-60 MB
- 1 year: ~180-730 MB

**Timelapse Images** (depending on interval):
- 1 day @ 300s interval: ~15-60 MB
- 7 days @ 300s interval: ~100-420 MB
- 30 days @ 300s interval: ~450-1800 MB

**Generated Videos**:
- Typical timelapse: 50-200 MB
- Depends on duration and quality

#### Total Storage Recommendations

- **Minimal** (1 camera, 30 days): 500 MB
- **Typical** (2 cameras, 60 days): 2 GB
- **Extensive** (4 cameras, 90 days): 5 GB

### Accessing Files Outside OGB

#### Via Samba Add-On

1. Install Samba add-on in HA
2. Configure share for `/config`
3. Access from Windows/Mac/Linux
4. Navigate to `ogb_data/`

#### Via SSH/SCP

```bash
# Connect to HA
ssh homeassistant

# Navigate to camera storage
cd /config/ogb_data/{room}_img/{camera}/daily/

# Copy files via SCP from another machine
scp homeassistant@hassio:/config/ogb_data/growroom1_img/camera1/daily/*.jpg ./
```

#### Via File Editor Add-On

1. Install File Editor add-on
2. Navigate to `/config/ogb_data/`
3. Browse and download files directly

### Backup Strategies

#### Manual Backup

1. Stop timelapse recordings
2. Use Samba/SSH to copy files
3. Backup to external storage or cloud
4. Verify backup integrity

#### Automated Backup

Consider:
- Home Assistant Google Drive Backup
- Samba backup scripts
- External backup solutions
- Regular backup schedule (weekly recommended)

#### What to Backup

Essential files:
- **Daily photos**: `/config/ogb_data/{room}_img/{camera}/daily/`
- **Timelapse images**: `/config/ogb_data/{room}_img/{camera}/timelapse/`
- **Generated videos**: `/config/ogb_data/{room}_img/{camera}/timelapse/output/`
- **OGB Configuration**: `/config/.storage/opengrowbox/`

### Storage Cleanup

#### Safe to Delete

- Old daily snapshots (export important ones first)
- Completed timelapse raw images (after generating video)
- Old timelapse videos

#### NOT Safe to Delete

- Active timelapse images
- Configuration files
- System directories

#### Cleanup Strategies

1. **Regular Cleanup**: Delete photos older than X days
2. **Archive First**: Export to ZIP before deleting
3. **Selective Deletion**: Keep important milestones
4. **Monitor Space**: Check disk usage regularly

### Monitoring Storage Usage

#### Check Storage Space

**Via HA Terminal**:
```bash
df -h /config
```

**Via Samba**:
- Check folder properties
- Monitor free space

**Recommended Thresholds**:
- > 1 GB free: Healthy
- 500 MB - 1 GB: Monitor closely
- < 500 MB: Cleanup required

---

## Troubleshooting

### Common Issues and Solutions

#### Issue: "No Cameras Found" Message

**Symptoms**:
- CameraCard shows "No Cameras Found"
- Camera dropdown is empty

**Causes**:
1. No cameras configured for current room
2. HASS data not loaded yet (Chrome reload bug)
3. Camera entity doesn't exist in HA
4. Camera area_id doesn't match room

**Solutions**:
1. Wait 1-2 seconds for HASS to load (especially after Chrome reload)
2. Check HA → Configuration → Devices & Services
3. Verify camera entities exist: `camera.*`
4. Check camera area_id in HA device settings
5. Ensure camera is added to OGB room configuration

#### Issue: Live Stream Not Working

**Symptoms**:
- Black screen or error message
- "Stream initialization failed"
- Automatic fallback to still images

**Causes**:
1. Camera doesn't support HLS streaming
2. Network connectivity issues
3. HA camera proxy not responding
4. Authentication token expired

**Solutions**:
1. Expected behavior - falls back to still images automatically
2. Still images refresh every 30 seconds
3. Check camera works in HA frontend (Developer Tools → States)
4. Verify HA token is valid (logout/login to OGB)
5. Check browser console for errors (F12)

#### Issue: Daily Snapshots Not Capturing

**Symptoms**:
- No new photos appear
- Schedule doesn't trigger

**Causes**:
1. Daily snapshots not enabled
2. HA scheduling system not running
3. Camera capture failing
4. Storage permissions issue

**Solutions**:
1. Verify "Enable Daily Snapshots" is ON
2. Check Settings → System → Logs for "Camera" errors
3. Test manual capture to verify camera works
4. Verify storage directory exists: `/config/ogb_data/`
5. Check HA scheduler is running: HA restart

#### Issue: Timelapse Recording Not Starting

**Symptoms**:
- Click "Start" but nothing happens
- Status remains "Idle"

**Causes**:
1. End date is before start date
2. Camera not ready
3. Invalid configuration

**Solutions**:
1. Verify end date is after start date
2. Check browser console for errors
3. Review timelapse configuration
4. Check logs for "startTL" errors
5. Ensure sufficient disk space

#### Issue: Timelapse Generation Fails

**Symptoms**:
- Progress bar stops at X%
- "Generation failed" error
- No video downloads

**Causes**:
1. FFmpeg not installed
2. Insufficient images (< 10)
3. Disk space full
4. Image corruption

**Solutions**:
1. Install FFmpeg: Usually included with HA
2. Verify at least 10 images captured
3. Check available disk space
4. Review logs for subprocess errors
5. Try deleting corrupted images

#### Issue: Photos Not Loading

**Symptoms**:
- Blank photo display
- "Failed to load photo" error

**Causes**:
1. Large image loading slowly
2. Browser memory issues
3. Network timeout

**Solutions**:
1. Wait 1-2 seconds for loading
2. Refresh page to clear memory
3. Check browser console for errors
4. Verify photo file exists in storage

#### Issue: ZIP Download Fails

**Symptoms**:
- ZIP download doesn't start
- "Generation failed" error

**Causes**:
1. No photos in date range
2. Insufficient memory for ZIP creation
3. Disk space full

**Solutions**:
1. Verify date range contains photos
2. Check browser console for errors
3. Ensure sufficient disk space
4. Try smaller date range

### Error Messages Explained

#### "No authentication token available"

**Meaning**: HA token not found for camera access

**Solution**:
- Refresh OGB page
- Logout and login to OGB
- Check browser local storage for token

#### "Camera does not support HLS streaming"

**Meaning**: Camera entity doesn't provide video stream

**Solution**:
- Expected - falls back to still images automatically
- Still images work fine for monitoring

#### "Failed to fetch camera image: 401"

**Meaning**: Authentication failed

**Solution**:
- Re-login to OGB
- Verify HA token is valid
- Check camera permissions in HA

#### "Timelapse generation failed: subprocess error"

**Meaning**: FFmpeg processing failed

**Solution**:
- Check FFmpeg is installed: `ffmpeg -version`
- Review logs for FFmpeg error details
- Verify image files aren't corrupted
- Check disk space

### Getting Help

#### Collect Diagnostic Information

Before seeking help, gather:

1. **HA Logs**:
   - Settings → System → Logs
   - Filter for "opengrowbox" or "Camera"

2. **Browser Console**:
   - Press F12 in browser
   - Check Console tab for errors
   - Look for red error messages

3. **System Info**:
   - HA version
   - OGB version
   - Camera type/model
   - Browser and version

#### Where to Get Help

1. **OGB Documentation**: Check technical reference for developers
2. **GitHub Issues**: Search for similar issues
3. **Community Forums**: Home Assistant Community
4. **OGB Support**: Official support channels

#### Reporting Bugs

When reporting bugs, include:
- Steps to reproduce
- Expected vs actual behavior
- Error messages (screenshots)
- HA logs excerpt
- Browser console errors
- System configuration

---

## Best Practices

### Live Streaming

1. **Monitor Bandwidth**: Multiple streams consume significant bandwidth
2. **Use Mobile Responsibly**: Streaming drains battery faster
3. **Check Camera Placement**: Ensure good view of grow area
4. **Lighting Matters**: Good lighting improves stream quality

### Daily Snapshots

1. **Consistent Timing**: Set snapshot time when lights are ON
2. **Regular Backups**: Export ZIP archives monthly
3. **Monitor Storage**: Check disk space regularly
4. **Delete Old Photos**: Keep only important milestones

### Timelapse Creation

1. **Plan Duration**: Longer recordings create better timelapses
2. **Balance Interval**: 300-600 seconds works well for most grows
3. **Test First**: Try short timelapse before committing to long one
4. **Generate Before Deleting**: Always generate video before deleting raw images

### Storage Management

1. **Weekly Cleanup**: Delete unnecessary photos
2. **Monthly Archives**: Export ZIP backups of important periods
3. **Monitor Space**: Check disk usage weekly
4. **External Backup**: Consider cloud backup for important grow data

---

## FAQ

**Q: How many cameras can I use per room?**

A: No hard limit. Practical limit is 4-6 cameras per room due to UI space and bandwidth.

**Q: Can I use outdoor cameras?**

A: Yes, any HA camera entity works with OGB cameras.

**Q: What's the maximum timelapse duration?**

A: Limited by disk space. 30-60 days is practical for most setups.

**Q: Do timelapses impact HA performance?**

A: Minimal impact during recording. Generation uses CPU for 2-15 minutes.

**Q: Can I access camera photos from outside HA?**

A: Yes, via Samba, SSH, or File Editor add-on at `/config/ogb_data/`

**Q: What happens if HA restarts during timelapse?**

A: Recording resumes automatically if end time hasn't passed. No images lost.

**Q: Can I change interval while recording?**

A: Yes, but takes effect on next capture cycle. Existing images preserved.

**Q: How do I move camera storage to external drive?**

A: Symlink `/config/ogb_data/` to external drive. Not officially supported.

**Q: Can I use camera photos for machine learning?**

A: Yes, photos are standard JPEG files. Access via storage directory.

**Q: Do daily snapshots and timelapse work together?**

A: Yes, they're independent systems. Can use both simultaneously.

---

## Glossary

- **HLS**: HTTP Live Streaming - video streaming protocol
- **Blob URL**: Browser-specific URL for in-memory binary data
- **FFmpeg**: Command-line tool for video processing
- **Interval**: Time between timelapse photo captures (seconds)
- **HA Token**: Authentication token for Home Assistant API access
- **Camera Proxy**: HA service that provides camera stream access
- **Event Bus**: HA communication system for components

---

## Appendix: Keyboard Shortcuts

(If implemented in your UI)

- **Arrow Left/Right**: Browse previous/next daily photo
- **Ctrl+S**: Save timelapse configuration
- **Delete**: Delete current daily photo
- **Escape**: Close modals/dialogs

---

## Document Version

**Version**: 1.0
**Last Updated**: 2026-01-17
**For OGB Version**: Current development version

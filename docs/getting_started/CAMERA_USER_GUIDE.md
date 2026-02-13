# Camera User Guide - Viewing, Recording & Timelapse

## Table of Contents

1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Live Camera View](#live-camera-view)
4. [Daily Snapshots](#daily-snapshots)
5. [Timelapse Recording](#timelapse-recording)
6. [Storage Management](#storage-management)
7. [Troubleshooting](#troubleshooting)

## Overview

The Camera feature in OpenGrowBox allows you to:

- **View live camera feeds** from your grow room
- **Capture daily snapshots** for tracking plant growth
- **Create timelapse videos** showing plant development over time
- **Download and manage** your captured images and videos

### Camera Interface Tabs

The camera interface is organized into three main tabs:

| Tab | Purpose | Features |
|-----|---------|----------|
| **Live View** | Real-time camera streaming | HLS video, still image fallback, refresh controls |
| **Daily View** | Browse daily snapshots | Photo gallery, navigation, delete individual photos |
| **Timelapse & Config** | Configure and generate timelapses | Recording controls, date/time settings, video generation, storage management |

## Getting Started

### Prerequisites

Before using the camera feature, ensure you have:

1. **Home Assistant Camera Entity**
   - Add a camera entity to your Home Assistant instance
   - Assign the camera to your grow room (area)
   - Verify the camera is accessible in HA

2. **Supported Camera Types**
   - IP cameras with RTSP/HTTP streaming
   - USB cameras connected to your HA host
   - ESP32/ESPHome camera modules
   - Any HA-integrated camera entity

3. **Storage Space**
   - Ensure sufficient disk space for captured images
   - See [Storage Impact](#storage-impact) section for estimates

### Adding a Camera to Your Grow Room

1. Open Home Assistant
2. Navigate to **Settings** → **Devices & Services**
3. Add your camera integration (if not already added)
4. Go to **Settings** → **Areas**
5. Assign your camera entity to your grow room area
6. The camera will now appear in OpenGrowBox

### Selecting a Camera

If you have multiple cameras in your grow room:

1. Open the **Camera** card in OpenGrowBox
2. Use the camera selector dropdown (top-right corner)
3. Select the desired camera from the list
4. Your selection is saved for future visits

## Live Camera View

### Accessing Live View

1. Click on the **Camera** card in your dashboard
2. Ensure the **Live View** tab is selected
3. The camera feed will automatically connect

### Stream Status Indicators

The status indicator shows the current connection state:

| Status | Icon | Meaning |
|--------|--------|---------|
| 🟢 **Live** | Green dot | Video stream is active and playing |
| 🟡 **Connecting...** | Yellow dot | Establishing connection to camera |
| 🔵 **Still Image (5s)** | Blue dot | Using still image mode, refreshes every 5 seconds |
| 🔴 **Error** | Red dot | Connection failed, use Refresh button |

### Streaming vs. Still Image Mode

**HLS Streaming (Preferred):**
- Real-time video playback
- Low latency (2-5 seconds)
- Adaptive quality based on network
- Requires camera to support streaming

**Still Image Mode (Fallback):**
- Single image that refreshes every 5 seconds
- Used when streaming is unavailable
- Click **Refresh** to manually update the image
- Automatically falls back from streaming if connection fails

### Refreshing the Camera Feed

If the camera feed appears frozen or disconnected:

1. Click the **Refresh** button (top-right)
2. The system will attempt to reconnect to the camera
3. Status will update to show connection progress

### Troubleshooting Live View Issues

**Problem: Camera shows "No Cameras Found"**
- **Solution**: Add a camera entity to your grow room in Home Assistant

**Problem: Video player shows "Connecting..." indefinitely**
- **Solution**: 
  1. Check if camera entity is available in HA
  2. Verify camera supports streaming
  3. Check network connectivity
  4. Try refreshing the page

**Problem: Falls back to still image mode**
- **Solution**: This is normal if:
  - Camera doesn't support HLS streaming
  - Network conditions prevent streaming
  - Camera is still-image only
  - The still image mode is fully functional

## Daily Snapshots

### What Are Daily Snapshots?

Daily snapshots are automatically captured photos taken at a specific time each day. They provide a simple way to track plant growth day-by-day without the storage overhead of continuous timelapse recording.

### Enabling Daily Snapshots

1. Navigate to the **Timelapse & Config** tab
2. Find the **Daily Snapshot Settings** section
3. Toggle **Enable Daily Snapshots** to ON
4. Set the **Snapshot Time** (default: 09:00)
5. The system will automatically schedule the next snapshot

**Note:** Changes are saved automatically when you adjust settings.

### Setting Snapshot Time

Choose a time when your grow lights are ON for best results:

- **Recommended**: 1-2 hours after lights turn on
- **Format**: 24-hour format (HH:MM)
- **Examples**:
  - `09:00` - 9:00 AM
  - `14:30` - 2:30 PM
  - `20:00` - 8:00 PM

### Viewing Daily Snapshots

1. Click the **Daily View** tab
2. The most recent snapshot will be displayed
3. Use the navigation controls to browse:
   - **← Left Arrow**: View previous day's photo
   - **→ Right Arrow**: View next day's photo
4. The photo date is shown at the bottom of the image

### Daily Photo Information

When viewing a daily snapshot, you'll see:

- **Photo Date**: The date the photo was taken (YYYY-MM-DD format)
- **Photo Counter**: "X of Y photos" showing current position in gallery
- **Delete Button**: Remove this specific photo

### Deleting Individual Daily Photos

To delete a single daily snapshot:

1. Navigate to the **Daily View** tab
2. Select the photo you want to delete
3. Click the **Delete** button (trash icon) on the photo overlay
4. Confirm the deletion in the popup dialog
5. The photo will be removed from storage

**Warning:** This action cannot be undone.

### Daily Snapshot Capture Failures

If a daily snapshot fails to capture:

1. A notification will appear at the top of the camera card
2. The notification shows:
   - Error message
   - Retry attempt number (e.g., "Retry attempt 2 of 3")
3. Click **Retry Now** to immediately attempt another capture
4. Or click **Dismiss** to ignore the failure

**Automatic Retry:** The system automatically retries up to 3 times with delays of 5, 15, and 30 seconds.

## Timelapse Recording

### What Is a Timelapse?

A timelapse is a video created from a series of images captured at regular intervals over a period of time. It compresses hours or days of plant growth into a short video, making it easy to visualize development and changes.

### Timelapse vs. Daily Snapshots

| Feature | Timelapse | Daily Snapshots |
|---------|-----------|-----------------|
| **Capture Frequency** | Every X seconds/minutes | Once per day |
| **Storage Usage** | Higher (many images) | Lower (one image/day) |
| **Detail Level** | High (frequent captures) | Medium (daily captures) |
| **Best For** | Detailed growth tracking | Simple progress tracking |
| **Video Output** | Automatic MP4/ZIP generation | Manual download of individual photos |

### Starting a Timelapse Recording

1. Navigate to the **Timelapse & Config** tab
2. Configure your timelapse settings (see [Timelapse Configuration](#timelapse-configuration))
3. Click the **Start Recording** button
4. The status will change to "🔴 Recording Active"
5. Images will be captured at the configured interval

**Recording Status Display:**
- **Status**: "Recording Active" or "Recording Stopped"
- **Interval**: Shows capture frequency (e.g., "Capturing every 300s")
- **Image Count**: Number of images captured so far

### Stopping a Timelapse Recording

1. Navigate to the **Timelapse & Config** tab
2. Click the **Stop Recording** button
3. The status will change to "⚪ Recording Stopped"
4. All captured images are saved in the timelapse directory

**Note:** You can also set an **End Date & Time** to automatically stop recording.

### Timelapse Configuration

#### Start Date & Time

When recording should begin:

- **Immediate Start**: Set to current time or past time
- **Scheduled Start**: Set to a future date/time
- **Format**: Date and time picker (YYYY-MM-DD HH:MM)

**Behavior:**
- If start time is in the past: Recording starts immediately
- If start time is in the future: Recording is scheduled

#### End Date & Time

When recording should automatically stop:

- **Required**: Must be set for recording to work
- **Format**: Date and time picker (YYYY-MM-DD HH:MM)
- **Automatic Stop**: Recording stops when this time is reached

**Example:**
- Start: `2026-01-12 09:00`
- End: `2026-01-15 09:00`
- Duration: 3 days of recording

#### Capture Interval

How often to capture images:

| Interval | Description | Use Case |
|----------|-------------|----------|
| 30 seconds | Testing only | Short-term testing |
| 1 minute | Testing only | Short-term testing |
| 5 minutes | Short interval | Detailed tracking, high storage |
| 10 minutes | Medium interval | Good balance of detail and storage |
| **15 minutes** | **Recommended** | Best balance for most use cases |
| 30 minutes | Long interval | Lower storage, less detail |
| 1 hour | Very long interval | Minimal storage, basic tracking |
| 2 hours | Maximum interval | Long-term monitoring |

**Recommendations:**
- **Testing**: Use 30 seconds or 1 minute to verify setup
- **Production**: Use 10-15 minutes for optimal results
- **Storage Concerned**: Use 30 minutes or longer

#### Output Format

Choose how to receive your timelapse:

| Format | Description | Pros | Cons |
|--------|-------------|------|------|
| **MP4 Video** | Compressed video file | Easy to share, playable anywhere | Slow generation, CPU-intensive |
| **ZIP of Images** | Archive of raw images | Fast generation, no quality loss | Larger file size, requires video player for viewing |

**Performance Warning:** MP4 generation can be very slow depending on your hardware. Consider ZIP format for faster downloads.

### Generating a Timelapse

After capturing images, generate your timelapse:

1. Navigate to the **Timelapse & Config** tab
2. Ensure **Start Date** and **End Date** are set
3. Select your desired **Output Format** (MP4 or ZIP)
4. Click the **Download Timelapse** button
5. Progress will be displayed:

**Progress States:**
- **Generating Timelapse**: Video/ZIP is being created
- **Progress Bar**: Shows percentage complete
- **Complete!**: Download started automatically
- **Generation Failed**: Error message displayed

**Automatic Download:**
- When generation completes, the file downloads automatically
- Filename includes camera name and timestamp
- For MP4: `timelapse_camera_1705067056.mp4`
- For ZIP: `timelapse_camera_1705067056.zip`

### Plant Day Integration

Timelapse recording respects your grow light cycle:

- **Lights ON**: Images are captured at the configured interval
- **Lights OFF**: Capture is skipped until lights turn on
- **Benefit**: Avoids capturing dark images, saves storage

**Note:** This uses the `isPlantDay` setting from your grow configuration.

## Storage Management

### Viewing Storage Information

The **Storage Management** section shows:

- **Daily Photos Count**: Number of daily snapshots stored
- **Storage Location**: Path to stored images
- **File Size Estimates**: Based on image count

### Downloading Daily Photos as ZIP

To download all daily photos:

1. Navigate to the **Timelapse & Config** tab
2. Find the **Storage Management** section
3. Optionally set a **Date Range Filter**:
   - **From Date**: Start date for included photos
   - **To Date**: End date for included photos
4. Click **Download Daily as ZIP**
5. The ZIP file will download automatically

**Date Range Filter:**
- Leave blank to include ALL daily photos
- Set dates to filter photos within that range
- The hint shows how many photos will be included

### Deleting All Daily Photos

To remove all daily snapshots:

1. Navigate to the **Timelapse & Config** tab
2. Find the **Storage Management** section
3. Click **Delete All Daily Photos**
4. Confirm deletion in the popup dialog
5. All daily photos will be permanently removed

**Warning:** This action cannot be undone. Consider downloading a ZIP backup first.

### Deleting All Timelapse Photos

To remove all timelapse source images:

1. Navigate to the **Timelapse & Config** tab
2. Find the **Storage Management** section
3. Click **Delete All Timelapse Photos**
4. Confirm deletion in the popup dialog
5. All timelapse images will be permanently removed

**Note:** This does NOT delete generated MP4/ZIP files. Use "Delete All Timelapse Output" for that.

### Deleting All Timelapse Output Files

To remove generated videos and ZIPs:

1. Navigate to the **Timelapse & Config** tab
2. Find the **Storage Management** section
3. Click **Delete All Timelapse Output**
4. Confirm deletion in the popup dialog
5. All MP4 and ZIP files will be permanently removed

**Note:** This does NOT delete source timelapse images. Use "Delete All Timelapse Photos" for that.

## Troubleshooting

### Camera Not Showing Up

**Problem:** Camera card shows "No Cameras Found"

**Solutions:**
1. Verify camera entity exists in Home Assistant
2. Check camera is assigned to your grow room (area)
3. Ensure camera entity state is not "unavailable"
4. Refresh the OpenGrowBox page

### Live Stream Not Connecting

**Problem:** Status shows "Connecting..." indefinitely

**Solutions:**
1. Check if camera supports streaming (not still-only)
2. Verify network connectivity to camera
3. Check Home Assistant logs for camera errors
4. Try refreshing the camera card
5. System may fall back to still image mode (this is normal)

### Timelapse Not Capturing Images

**Problem:** Recording shows "Active" but image count not increasing

**Solutions:**
1. Verify grow lights are ON (isPlantDay = True)
2. Check camera entity is accessible
3. Review HA logs for capture errors
4. Verify storage directory is writable
5. Check interval is set correctly (not 0)

### Daily Snapshot Not Capturing

**Problem:** No new daily photos appear

**Solutions:**
1. Verify daily snapshots are enabled
2. Check snapshot time is set correctly
3. Ensure camera is available at scheduled time
4. Review HA logs for capture errors
5. Check for capture failure notifications

### Video Generation Fails

**Problem:** "Generation Failed" error when creating timelapse

**Solutions:**
1. Verify FFmpeg is installed on HA host
2. Check sufficient disk space for output file
3. Try a shorter date range (fewer images)
4. Use ZIP format as alternative
5. Review HA logs for FFmpeg errors

### Storage Space Issues

**Problem:** Disk space running low due to many images

**Solutions:**
1. Delete old daily photos or timelapse images
2. Increase capture interval (fewer images)
3. Use daily snapshots instead of continuous timelapse
4. Download and backup important images, then delete from system

### Performance Issues

**Problem:** System slow when generating timelapse

**Solutions:**
1. Use ZIP format instead of MP4 (faster)
2. Reduce number of images (shorter date range)
3. Generate during off-peak hours
4. Consider upgrading HA host hardware

## Best Practices

### For Optimal Results

1. **Use Appropriate Intervals**
   - Testing: 30 seconds - 1 minute
   - Production: 10-15 minutes
   - Storage-limited: 30 minutes or longer

2. **Schedule During Light Hours**
   - Set daily snapshots 1-2 hours after lights on
   - Timelapse automatically respects light cycle
   - Avoids capturing dark images

3. **Manage Storage Regularly**
   - Download and archive important timelapses
   - Delete old images regularly
   - Monitor disk space usage

4. **Choose Right Output Format**
   - MP4: For sharing and easy viewing
   - ZIP: For faster generation and raw image preservation

5. **Test Before Long Recordings**
   - Start with short test recordings (1-2 hours)
   - Verify images are capturing correctly
   - Check video generation works
   - Then start longer recordings

### Storage Planning

**Estimate your storage needs:**

| Recording Type | Duration | Interval | Est. Storage |
|--------------|----------|----------|----------------|
| Daily Snapshot | 1 month | 1/day | ~15 MB |
| Short Timelapse | 1 day | 15 min | ~48 MB |
| Medium Timelapse | 1 week | 15 min | ~336 MB |
| Long Timelapse | 1 month | 15 min | ~1.4 GB |

**Assumptions:**
- Average image size: 500 KB
- 24-hour plant day
- 30-day month

---

## Camera User Guide Summary

**Camera features documented!** OpenGrowBox provides comprehensive camera functionality.

**User Capabilities:**
- ✅ **Live Viewing**: Real-time camera streaming
- ✅ **Daily Snapshots**: Scheduled daily photo capture
- ✅ **Timelapse Recording**: Interval-based image capture
- ✅ **Video Generation**: MP4 and ZIP output formats
- ✅ **Storage Management**: Download and delete operations
- ✅ **Progress Tracking**: Real-time status updates
- ✅ **Error Handling**: Retry logic and notifications

**For technical implementation details, see [Camera System Guide](../device_management/CAMERA_SYSTEM.md)**
**For API event reference, see [Frontend Communication Guide](../technical_reference/FRONTEND_COMMUNICATION.md)**
**For general device management, see [Device Management Guide](../device_management/device_management.md)**

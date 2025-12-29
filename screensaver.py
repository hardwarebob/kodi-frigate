#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Frigate NVR Screensaver
Displays Frigate camera feeds using ffmpeg in a grid layout
"""

import sys
import os
import subprocess
import xbmc
import xbmcaddon
import xbmcgui

# Python 2/3 compatibility
if sys.version_info[0] >= 3:
    from xbmcvfs import translatePath
else:
    from xbmc import translatePath

# Add addon directory to path
addon = xbmcaddon.Addon('service.kodi.frigate')
addon_path = translatePath(addon.getAddonInfo('path'))
if isinstance(addon_path, bytes):
    addon_path = addon_path.decode('utf-8')
sys.path.insert(0, addon_path)

from frigate_client import FrigateClient


class FrigateScreensaver(xbmcgui.WindowXML):
    """Screensaver window displaying Frigate camera feeds using ffmpeg"""

    def __init__(self, *args, **kwargs):
        super(FrigateScreensaver, self).__init__(*args, **kwargs)
        self.addon = xbmcaddon.Addon('service.kodi.frigate')
        self.monitor = xbmc.Monitor()
        self.cameras = []
        self.camera_index = 0
        self.cycle_timer = 0
        self.cycle_interval = 10
        self.running = False
        self.cycle_thread = None
        self.initialized = False
        self.frigate_url = None
        self.num_cameras = 1
        self.ffmpeg_process = None
        self.player = xbmc.Player()
        self.pipe_path = None

    def onInit(self):
        """Initialize screensaver - called automatically by Kodi"""
        # Prevent multiple initialization
        if self.initialized:
            xbmc.log('[Frigate Screensaver] Already initialized, skipping', xbmc.LOGDEBUG)
            return

        self.initialized = True

        try:
            xbmc.log('[Frigate Screensaver] Initializing', xbmc.LOGINFO)

            # Load settings
            cycle_interval = int(self.addon.getSetting('screensaver_cycle_interval') or '10')
            screensaver_cameras = self.addon.getSetting('screensaver_cameras') or ''
            camera_count_setting = int(self.addon.getSetting('screensaver_camera_count') or '0')

            # Map setting to actual number of cameras (0=1, 1=2, 2=3, 3=4)
            self.num_cameras = camera_count_setting + 1

            self.frigate_url = self.addon.getSetting('frigate_url')
            frigate_username = self.addon.getSetting('frigate_username')
            frigate_password = self.addon.getSetting('frigate_password')

            if not self.frigate_url:
                xbmc.log('[Frigate Screensaver] Frigate URL not configured', xbmc.LOGWARNING)
                self.close()
                return

            # Get cameras from Frigate
            client = FrigateClient(self.frigate_url, frigate_username if frigate_username else None,
                                 frigate_password if frigate_password else None)
            all_cameras = client.get_cameras()

            if not all_cameras:
                xbmc.log('[Frigate Screensaver] No cameras available', xbmc.LOGWARNING)
                self.close()
                return

            # Filter cameras if specific ones are configured
            if screensaver_cameras:
                camera_filter = [cam.strip().lower() for cam in screensaver_cameras.split(',') if cam.strip()]
                self.cameras = [(name, info) for name, info in all_cameras.items()
                              if name.lower() in camera_filter and info.get('enabled', True)]
            else:
                self.cameras = [(name, info) for name, info in all_cameras.items()
                              if info.get('enabled', True)]

            if not self.cameras:
                xbmc.log('[Frigate Screensaver] No enabled cameras available', xbmc.LOGWARNING)
                self.close()
                return

            xbmc.log('[Frigate Screensaver] Found {} cameras, displaying {} at a time'.format(
                len(self.cameras), self.num_cameras), xbmc.LOGINFO)

            # Store cycle interval for timer
            self.cycle_interval = cycle_interval
            self.cycle_timer = cycle_interval

            # Start playing cameras
            if self.num_cameras == 1:
                # Single camera - play directly without ffmpeg
                xbmc.log('[Frigate Screensaver] Starting direct playback for single camera...', xbmc.LOGINFO)
                self._play_single_camera()
            else:
                # Multiple cameras - use ffmpeg to combine streams
                xbmc.log('[Frigate Screensaver] Starting ffmpeg playback for {} cameras...'.format(self.num_cameras), xbmc.LOGINFO)
                self._start_ffmpeg()

            # Start background thread for cycling and user activity detection
            xbmc.log('[Frigate Screensaver] Starting background thread...', xbmc.LOGINFO)
            import threading
            self.cycle_thread = threading.Thread(target=self._cycle_thread)
            self.cycle_thread.daemon = True
            self.running = True
            self.cycle_thread.start()
            xbmc.log('[Frigate Screensaver] Initialization complete', xbmc.LOGINFO)

        except Exception as e:
            xbmc.log('[Frigate Screensaver] Error during initialization: {}'.format(str(e)), xbmc.LOGERROR)
            import traceback
            xbmc.log('[Frigate Screensaver] Traceback: {}'.format(traceback.format_exc()), xbmc.LOGERROR)
            self.close()

    def _cycle_thread(self):
        """Background thread for cycling cameras and detecting user activity"""
        while self.running and not self.monitor.abortRequested():
            if self.monitor.waitForAbort(1):
                break

            # Check if user is active (idle time has reset to a low value)
            # When user interacts, idle time resets to 0 or near 0
            current_idle_time = xbmc.getGlobalIdleTime()
            if current_idle_time < 3:
                # User activity detected - exit screensaver
                xbmc.log('[Frigate Screensaver] User activity detected (idle time: {}s), exiting screensaver'.format(current_idle_time), xbmc.LOGINFO)
                self.running = False

                # Stop player and ffmpeg
                if self.player.isPlaying():
                    self.player.stop()
                self._stop_ffmpeg()

                # Close the screensaver window
                try:
                    self.close()
                except:
                    pass
                break

            # Cycle to next set of cameras
            self.cycle_timer -= 1
            if self.cycle_timer <= 0:
                self._cycle_cameras()
                self.cycle_timer = self.cycle_interval

    def _get_stream_url(self, camera_info):
        """Get stream URL for a camera, preferring RTSP"""
        # Prefer RTSP for high quality video
        stream_url = camera_info.get('rtsp_url', '')

        # Rewrite localhost/127.0.0.1 to use Frigate server hostname
        if stream_url and ('127.0.0.1' in stream_url or 'localhost' in stream_url):
            if sys.version_info[0] >= 3:
                from urllib.parse import urlparse
            else:
                from urlparse import urlparse
            try:
                parsed = urlparse(self.frigate_url)
                frigate_host = parsed.hostname
                if frigate_host:
                    stream_url = stream_url.replace('127.0.0.1', frigate_host)
                    stream_url = stream_url.replace('localhost', frigate_host)
                    xbmc.log('[Frigate Screensaver] Rewrote RTSP URL to use Frigate hostname: {}'.format(stream_url), xbmc.LOGDEBUG)
            except:
                pass

        # Fallback to MJPEG if RTSP not available
        if not stream_url:
            stream_url = camera_info.get('mjpeg_url', '')
            xbmc.log('[Frigate Screensaver] RTSP not available, using MJPEG fallback', xbmc.LOGDEBUG)

        return stream_url

    def _play_single_camera(self):
        """Play a single camera stream directly without ffmpeg"""
        if not self.cameras:
            return

        camera_name, camera_info = self.cameras[self.camera_index]
        stream_url = self._get_stream_url(camera_info)

        if not stream_url:
            xbmc.log('[Frigate Screensaver] No stream URL for camera: {}'.format(camera_name), xbmc.LOGWARNING)
            return

        xbmc.log('[Frigate Screensaver] Playing camera: {} - {}'.format(camera_name, stream_url), xbmc.LOGINFO)

        # Create list item for the stream
        list_item = xbmcgui.ListItem(path=stream_url)
        list_item.setProperty('IsPlayable', 'true')
        list_item.setProperty('IsInternetStream', 'true')
        list_item.setInfo('video', {'title': camera_name})

        # Set MIME type based on stream type
        if stream_url.startswith('rtsp://'):
            xbmc.log('[Frigate Screensaver] Stream type: RTSP', xbmc.LOGDEBUG)
        else:
            list_item.setContentLookup(False)
            list_item.setMimeType('video/x-motion-jpeg')
            xbmc.log('[Frigate Screensaver] Stream type: MJPEG', xbmc.LOGDEBUG)

        # Play the stream in fullscreen
        self.player.play(stream_url, list_item, windowed=False)

    def _start_ffmpeg(self):
        """Start ffmpeg to pipe camera streams to Kodi player"""
        # Stop any existing ffmpeg process
        self._stop_ffmpeg()

        # Get current set of cameras to display
        cameras_to_display = []
        for i in range(min(self.num_cameras, len(self.cameras))):
            cam_idx = (self.camera_index + i) % len(self.cameras)
            camera_name, camera_info = self.cameras[cam_idx]
            stream_url = self._get_stream_url(camera_info)

            if stream_url:
                cameras_to_display.append((camera_name, stream_url))
                xbmc.log('[Frigate Screensaver] Camera {} slot {}: {}'.format(camera_name, i, stream_url), xbmc.LOGINFO)

        if not cameras_to_display:
            xbmc.log('[Frigate Screensaver] No valid camera streams to display', xbmc.LOGWARNING)
            return

        # Calculate grid layout
        if self.num_cameras == 1:
            grid_cols, grid_rows = 1, 1
        elif self.num_cameras == 2:
            grid_cols, grid_rows = 2, 1
        elif self.num_cameras == 3:
            grid_cols, grid_rows = 3, 1
        elif self.num_cameras == 4:
            grid_cols, grid_rows = 2, 2
        else:
            grid_cols, grid_rows = 2, 2

        # Create named pipe for ffmpeg output in Kodi's temp directory
        import tempfile
        import stat
        if sys.version_info[0] >= 3:
            from xbmcvfs import translatePath
        else:
            from xbmc import translatePath

        # Get Kodi's temp directory
        kodi_temp = translatePath('special://temp/')
        if isinstance(kodi_temp, bytes):
            kodi_temp = kodi_temp.decode('utf-8')

        self.pipe_path = os.path.join(kodi_temp, 'frigate_screensaver.ts')
        self.log_path = os.path.join(kodi_temp, 'frigate-screensaver-ffmpeg.log')

        # Remove existing pipe if it exists
        if os.path.exists(self.pipe_path):
            try:
                os.remove(self.pipe_path)
            except:
                pass

        # Create named pipe
        try:
            os.mkfifo(self.pipe_path, 0o666)
            xbmc.log('[Frigate Screensaver] Created named pipe: {}'.format(self.pipe_path), xbmc.LOGINFO)
        except Exception as e:
            xbmc.log('[Frigate Screensaver] Failed to create named pipe: {}'.format(str(e)), xbmc.LOGERROR)
            return

        # Build ffmpeg command
        ffmpeg_cmd = ['ffmpeg', '-y', '-threads', '5']

        # Add input streams
        for camera_name, stream_url in cameras_to_display:
            ffmpeg_cmd.extend(['-i', stream_url])

        # Build filter complex for grid layout
        if len(cameras_to_display) == 1:
            # Single camera - just scale to fullscreen
            filter_complex = '[0:v]scale=1920x1080[out]'
        else:
            # Multiple cameras - create grid with xstack
            filter_parts = []
            for i in range(len(cameras_to_display)):
                # Scale each input to fit in grid cell
                cell_width = 1920 // grid_cols
                cell_height = 1080 // grid_rows
                filter_parts.append('[{}:v]scale={}:{}:force_original_aspect_ratio=decrease,pad={}:{}:(ow-iw)/2:(oh-ih)/2[v{}]'.format(
                    i, cell_width, cell_height, cell_width, cell_height, i))

            # Create xstack layout
            inputs = ''.join('[v{}]'.format(i) for i in range(len(cameras_to_display)))

            if grid_cols == 2 and grid_rows == 1:
                layout = '0_0|w0_0'
            elif grid_cols == 3 and grid_rows == 1:
                layout = '0_0|w0_0|w0+w1_0'
            elif grid_cols == 2 and grid_rows == 2:
                layout = '0_0|w0_0|0_h0|w0_h0'
            else:
                layout = '0_0|w0_0'

            filter_complex = ';'.join(filter_parts) + ';{}xstack=inputs={}:layout={}[out]'.format(
                inputs, len(cameras_to_display), layout)

        # Output to named pipe as MPEG-TS - will write to pipe_path directly
        ffmpeg_cmd.extend([
            '-filter_complex', filter_complex,
            '-map', '[out]',
            '-f', 'mpegts',
            '-codec:v', 'mpeg2video',
            '-b:v', '8M',
            '-maxrate', '8M',
            '-bufsize', '4M',
            '-r', '30',
            self.pipe_path
        ])

        xbmc.log('[Frigate Screensaver] Starting ffmpeg: {}'.format(' '.join(ffmpeg_cmd)), xbmc.LOGINFO)
        xbmc.log('[Frigate Screensaver] ffmpeg log will be written to: {}'.format(self.log_path), xbmc.LOGINFO)

        try:
            # Start Kodi player to read from the pipe first
            import threading
            threading.Thread(target=self._start_player).start()

            # Wait a moment for the player to start reading
            import time
            time.sleep(0.5)

            # Open log file for stderr
            log_file = open(self.log_path, 'w')

            # Now start ffmpeg without shell - pass command as list to avoid escaping issues
            self.ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stderr=log_file,
                stdout=subprocess.PIPE
            )

            # Close our file handle - ffmpeg now owns it
            log_file.close()

            xbmc.log('[Frigate Screensaver] ffmpeg process started with PID {}'.format(self.ffmpeg_process.pid), xbmc.LOGINFO)

        except Exception as e:
            xbmc.log('[Frigate Screensaver] Failed to start ffmpeg: {}'.format(str(e)), xbmc.LOGERROR)
            self.ffmpeg_process = None
            if os.path.exists(self.pipe_path):
                os.remove(self.pipe_path)

    def _start_player(self):
        """Start Kodi player to read from the pipe"""
        import time

        # Wait a moment for the pipe to be created
        max_wait = 5
        for i in range(max_wait * 10):
            if os.path.exists(self.pipe_path):
                break
            time.sleep(0.1)

        if not os.path.exists(self.pipe_path):
            xbmc.log('[Frigate Screensaver] Pipe does not exist after {}s, cannot start player'.format(max_wait), xbmc.LOGWARNING)
            return

        try:
            # Create list item for the pipe
            list_item = xbmcgui.ListItem(path=self.pipe_path)
            list_item.setProperty('IsPlayable', 'true')
            list_item.setContentLookup(False)

            # Play the pipe stream in fullscreen
            xbmc.log('[Frigate Screensaver] Starting Kodi player with pipe: {}'.format(self.pipe_path), xbmc.LOGINFO)
            self.player.play(self.pipe_path, list_item, windowed=False)

        except Exception as e:
            xbmc.log('[Frigate Screensaver] Failed to start player: {}'.format(str(e)), xbmc.LOGERROR)

    def _stop_ffmpeg(self):
        """Stop the ffmpeg process and clean up"""
        # Stop Kodi player
        if self.player and self.player.isPlaying():
            xbmc.log('[Frigate Screensaver] Stopping Kodi player', xbmc.LOGDEBUG)
            try:
                self.player.stop()
            except Exception as e:
                xbmc.log('[Frigate Screensaver] Error stopping player: {}'.format(str(e)), xbmc.LOGDEBUG)

        # Stop ffmpeg process
        if self.ffmpeg_process:
            xbmc.log('[Frigate Screensaver] Stopping ffmpeg process', xbmc.LOGDEBUG)
            try:
                # Kill immediately instead of waiting for graceful termination
                self.ffmpeg_process.kill()
                self.ffmpeg_process = None
            except Exception as e:
                xbmc.log('[Frigate Screensaver] Error stopping ffmpeg: {}'.format(str(e)), xbmc.LOGDEBUG)

        # Remove named pipe
        if self.pipe_path and os.path.exists(self.pipe_path):
            try:
                os.remove(self.pipe_path)
                self.pipe_path = None
            except Exception as e:
                # Silently ignore pipe removal errors
                pass

    def _cycle_cameras(self):
        """Cycle to next set of cameras"""
        if len(self.cameras) <= self.num_cameras:
            # Not enough cameras to cycle
            return

        self.camera_index = (self.camera_index + self.num_cameras) % len(self.cameras)
        xbmc.log('[Frigate Screensaver] Cycling to camera index {}'.format(self.camera_index), xbmc.LOGINFO)

        if self.num_cameras == 1:
            # Single camera - play directly
            self._play_single_camera()
        else:
            # Multiple cameras - restart ffmpeg with new cameras
            self._start_ffmpeg()

    def onAction(self, action):
        """Handle user actions"""
        # Stop background thread
        self.running = False
        if self.cycle_thread:
            self.cycle_thread.join(timeout=2)

        # Stop ffmpeg and player
        self._stop_ffmpeg()

        # Close screensaver on any action
        self.close()


# Entry point when run directly
if __name__ == '__main__':
    xbmc.log('[Frigate Screensaver] Starting screensaver', xbmc.LOGINFO)
    screensaver = FrigateScreensaver('screensaver-frigate.xml', addon.getAddonInfo('path'), 'default', '1080i')
    screensaver.doModal()

    # Cleanup on exit
    try:
        screensaver.running = False
        if screensaver.player.isPlaying():
            screensaver.player.stop()
        if screensaver.ffmpeg_process:
            screensaver.ffmpeg_process.kill()
    except:
        pass
    del screensaver
    xbmc.log('[Frigate Screensaver] Screensaver stopped', xbmc.LOGINFO)

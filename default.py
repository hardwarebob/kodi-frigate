#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# Frigate NVR Integration - Camera Display Script
# Displays camera overlay when triggered by Frigate detection events
#
# Can be called with camera parameter:
# RunScript(service.kodi.frigate,camera=front_door)
#

# Import the modules
import os, time, random, string, sys, platform
import xbmc, xbmcaddon, xbmcgui, xbmcvfs
import requests, subprocess
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from threading import Thread

try:
    from urllib.request import build_opener, HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, HTTPDigestAuthHandler, Request
except ImportError:
    from urllib2 import build_opener, HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, HTTPDigestAuthHandler, Request

if sys.version_info.major < 3:
    INFO = xbmc.LOGNOTICE
    from xbmc import translatePath
else:
    INFO = xbmc.LOGINFO
    from xbmcvfs import translatePath
DEBUG = xbmc.LOGDEBUG

# Import Frigate client
addon_path = translatePath(xbmcaddon.Addon().getAddonInfo('path'))
if isinstance(addon_path, bytes):
    addon_path = addon_path.decode('utf-8')
sys.path.insert(0, addon_path)

from frigate_client import FrigateClient

# Constants
ACTION_PREVIOUS_MENU = 10
ACTION_STOP = 13
ACTION_NAV_BACK = 92
ACTION_BACKSPACE = 110

# Set plugin variables
__addon__        = xbmcaddon.Addon()
__addon_id__     = __addon__.getAddonInfo('id')
__addon_path__   = __addon__.getAddonInfo('path')
__profile__      = translatePath(__addon__.getAddonInfo('profile'))
__icon__         = os.path.join(__addon_path__, 'icon.png')
__loading__      = os.path.join(__addon_path__, 'loading.gif')

# Get settings
SETTINGS = {
    'width':         int(float(__addon__.getSetting('width'))),
    'height':        int(float(__addon__.getSetting('height'))),
    'interval':      int(float(__addon__.getSetting('interval'))),
    'autoClose':     bool(__addon__.getSetting('autoClose') == 'true'),
    'duration':      int(float(__addon__.getSetting('duration')) * 1000),
    'padding':       int(float(__addon__.getSetting('padding'))),
    'animate':       bool(__addon__.getSetting('animate') == 'true'),
    'aspectRatio':   int(float(__addon__.getSetting('aspectRatio')))
    }

# Frigate settings
frigate_url = __addon__.getSetting('frigate_url')
frigate_username = __addon__.getSetting('frigate_username') if __addon__.getSetting('frigate_username') else None
frigate_password = __addon__.getSetting('frigate_password') if __addon__.getSetting('frigate_password') else None

ffmpeg_exec = 'ffmpeg.exe' if platform.system() == 'Windows' else 'ffmpeg'

# Utils
def log(message, loglevel=INFO):
    xbmc.log(msg='[{}] {}'.format(__addon_id__, message), level=loglevel)

# Parse command line arguments
camera_name = None

# Log all arguments for debugging
log('Script called with {} arguments: {}'.format(len(sys.argv), sys.argv), DEBUG)

if len(sys.argv) > 1:
    for i in range(1, len(sys.argv)):
        try:
            if '=' in sys.argv[i]:
                key, value = sys.argv[i].split('=', 1)
                if key == 'camera':
                    camera_name = value
                    log('Parsed camera name: {}'.format(camera_name), DEBUG)
                elif key == 'duration':
                    SETTINGS['duration'] = int(value)
                    log('Parsed duration: {}'.format(SETTINGS['duration']), DEBUG)
        except Exception as e:
            log('Error parsing argument {}: {}'.format(sys.argv[i], str(e)), DEBUG)
            continue

def which(pgm):
    for path in os.getenv('PATH').split(os.path.pathsep):
        p = os.path.join(path, pgm)
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None

# Classes
class CamPreviewDialog(xbmcgui.WindowDialog):
    def __init__(self, camera_name, camera_info):
        """
        Initialize camera preview dialog

        Args:
            camera_name (str): Name of the camera
            camera_info (dict): Camera information from Frigate
        """
        self.camera_name = camera_name
        self.camera_info = camera_info
        self.isRunning = False
        self.lock_file = os.path.join(__profile__, 'overlay_active_{}.lock'.format(camera_name))

        # Setup authentication
        passwd_mgr = HTTPPasswordMgrWithDefaultRealm()
        self.opener = build_opener()

        # Use snapshot URL from Frigate
        self.url = camera_info['snapshot_url']

        if frigate_username and frigate_password:
            passwd_mgr.add_password(None, frigate_url, frigate_username, frigate_password)
            self.opener.add_handler(HTTPBasicAuthHandler(passwd_mgr))
            self.opener.add_handler(HTTPDigestAuthHandler(passwd_mgr))

        # Create temp directory for snapshots
        randomname = ''.join([random.choice(string.ascii_letters + string.digits) for n in range(32)])
        self.tmpdir = os.path.join(__profile__, randomname)
        if not xbmcvfs.exists(self.tmpdir):
            xbmcvfs.mkdirs(self.tmpdir)

        # Calculate position (single camera, centered or positioned)
        x, y, w, h = self.calculate_position()

        # Create image control
        self.control = xbmcgui.ControlImage(x, y, w, h, __loading__, aspectRatio=SETTINGS['aspectRatio'])
        self.addControl(self.control)

        # Add animation if enabled
        if SETTINGS['animate']:
            self.control.setAnimations([
                ('WindowOpen', 'effect=slide start=%d time=1000 tween=cubic easing=in' % w,),
                ('WindowClose', 'effect=slide end=%d time=1000 tween=cubic easing=in' % w,)
            ])

    def calculate_position(self):
        """Calculate position for single camera display (right side of screen)"""
        WIDTH = 1280
        HEIGHT = 720

        w = SETTINGS['width']
        h = SETTINGS['height']
        p = SETTINGS['padding']

        # Position on right side of screen
        x = int(WIDTH - (w + p))
        y = int(p)

        return x, y, w, h

    def start(self):
        """Start the camera display"""
        # Create lock file to indicate overlay is active
        try:
            # Write timestamp to lock file
            with open(self.lock_file, 'w') as f:
                f.write(str(time.time()))
            log('Created lock file: {}'.format(self.lock_file), DEBUG)
        except Exception as e:
            log('Failed to create lock file: {}'.format(str(e)), DEBUG)

        self.show()
        self.isRunning = True

        # Start update thread
        Thread(target=self.update).start()

        # Auto-close timer with dynamic duration extension
        startTime = time.time()
        last_check = time.time()

        while not SETTINGS['autoClose'] or (time.time() - startTime) * 1000 <= SETTINGS['duration']:
            if not self.isRunning:
                break

            # Check lock file every second for duration updates
            current_time = time.time()
            if current_time - last_check >= 1.0:
                last_check = current_time
                try:
                    if os.path.exists(self.lock_file):
                        with open(self.lock_file, 'r') as f:
                            lock_time = float(f.read().strip())
                        # If lock file was updated recently (within last 2 seconds), extend the duration
                        if current_time - lock_time < 2:
                            startTime = current_time
                            log('Extended overlay duration for {}'.format(self.camera_name), DEBUG)
                except Exception as e:
                    log('Error checking lock file: {}'.format(str(e)), DEBUG)

            xbmc.sleep(500)

        self.isRunning = False
        self.close()
        self.cleanup()

    def update(self):
        """Update camera snapshots continuously"""
        request = Request(self.url)
        index = 1

        log('Starting camera update loop for {}'.format(self.camera_name), DEBUG)

        while self.isRunning:
            snapshot = os.path.join(self.tmpdir, 'snapshot_{:06d}.jpg'.format(index))
            index += 1

            try:
                # Fetch snapshot from Frigate
                imgData = self.opener.open(request, timeout=5).read()

                if imgData:
                    file = xbmcvfs.File(snapshot, 'wb')
                    file.write(bytearray(imgData))
                    file.close()

                    # Update display
                    self.control.setImage(snapshot, False)

            except Exception as e:
                log('Error fetching snapshot: {}'.format(str(e)), xbmc.LOGWARNING)

            # Wait before next update
            xbmc.sleep(SETTINGS['interval'])

    def cleanup(self):
        """Clean up temporary files"""
        try:
            # Remove lock file
            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)
                log('Removed lock file: {}'.format(self.lock_file), DEBUG)
        except Exception as e:
            log('Error removing lock file: {}'.format(str(e)), DEBUG)

        try:
            files = xbmcvfs.listdir(self.tmpdir)[1]
            for file in files:
                xbmcvfs.delete(os.path.join(self.tmpdir, file))
            xbmcvfs.rmdir(self.tmpdir)
        except Exception as e:
            log('Error during cleanup: {}'.format(str(e)), DEBUG)

    def onAction(self, action):
        """Handle user actions"""
        if action in (ACTION_PREVIOUS_MENU, ACTION_STOP, ACTION_BACKSPACE, ACTION_NAV_BACK):
            self.stop()

    def stop(self):
        """Stop the camera display"""
        self.isRunning = False


if __name__ == '__main__':
    if not camera_name:
        log('ERROR: No camera specified. Use camera=<name> parameter.', xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'Frigate',
            'No camera specified',
            xbmcgui.NOTIFICATION_ERROR,
            3000
        )
        sys.exit(1)

    log('Displaying camera: {}'.format(camera_name))

    # Check if overlay is already active for this camera
    lock_file = os.path.join(__profile__, 'overlay_active_{}.lock'.format(camera_name))

    if os.path.exists(lock_file):
        # Overlay already active - just update the timestamp to extend duration
        try:
            with open(lock_file, 'w') as f:
                f.write(str(time.time()))
            log('Overlay already active for {}, extended duration'.format(camera_name), xbmc.LOGINFO)
            sys.exit(0)
        except Exception as e:
            log('Failed to update lock file: {}'.format(str(e)), DEBUG)
            # Continue to create new overlay if we can't update the lock

    # Initialize Frigate client
    try:
        frigate_client = FrigateClient(frigate_url, frigate_username, frigate_password)
        cameras = frigate_client.get_cameras()

        if camera_name not in cameras:
            log('ERROR: Camera {} not found in Frigate'.format(camera_name), xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                'Frigate',
                'Camera {} not found'.format(camera_name),
                xbmcgui.NOTIFICATION_ERROR,
                3000
            )
            sys.exit(1)

        camera_info = cameras[camera_name]

        # Create and show camera preview
        camPreview = CamPreviewDialog(camera_name, camera_info)
        camPreview.start()

        del camPreview

    except Exception as e:
        log('ERROR: Failed to display camera: {}'.format(str(e)), xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'Frigate',
            'Failed to display camera',
            xbmcgui.NOTIFICATION_ERROR,
            3000
        )

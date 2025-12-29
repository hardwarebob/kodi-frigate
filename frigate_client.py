# -*- coding: utf-8 -*-
"""
Frigate NVR API Client
Handles communication with Frigate server to discover cameras and retrieve configuration
"""

import json
try:
    # Python 3
    from urllib.request import Request, urlopen, HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, HTTPDigestAuthHandler, build_opener
    from urllib.error import URLError, HTTPError
except ImportError:
    # Python 2
    from urllib2 import Request, urlopen, URLError, HTTPError, HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, HTTPDigestAuthHandler, build_opener

import xbmc


class FrigateClient:
    """Client for interacting with Frigate NVR API"""

    def __init__(self, base_url, username=None, password=None):
        """
        Initialize Frigate API client

        Args:
            base_url (str): Base URL of Frigate server (e.g., http://localhost:5000)
            username (str, optional): Username for authentication
            password (str, optional): Password for authentication
        """
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.opener = None

        # Setup authentication if credentials provided
        if username and password:
            password_mgr = HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(None, self.base_url, username, password)

            basic_handler = HTTPBasicAuthHandler(password_mgr)
            digest_handler = HTTPDigestAuthHandler(password_mgr)

            self.opener = build_opener(basic_handler, digest_handler)

    def _make_request(self, endpoint):
        """
        Make HTTP request to Frigate API

        Args:
            endpoint (str): API endpoint path (e.g., /api/config)

        Returns:
            dict: Parsed JSON response or None on error
        """
        url = self.base_url + endpoint

        try:
            xbmc.log('[Frigate] Making API request to: {0}'.format(url), xbmc.LOGDEBUG)

            request = Request(url)

            if self.opener:
                response = self.opener.open(request, timeout=10)
            else:
                response = urlopen(request, timeout=10)

            data = response.read()

            # Decode bytes to string if needed (Python 3)
            if isinstance(data, bytes):
                data = data.decode('utf-8')

            return json.loads(data)

        except HTTPError as e:
            xbmc.log('[Frigate] HTTP Error {0}: {1}'.format(e.code, e.reason), xbmc.LOGERROR)
            return None
        except URLError as e:
            xbmc.log('[Frigate] URL Error: {0}'.format(str(e.reason)), xbmc.LOGERROR)
            return None
        except Exception as e:
            xbmc.log('[Frigate] Error making request: {0}'.format(str(e)), xbmc.LOGERROR)
            return None

    def get_config(self):
        """
        Get Frigate configuration

        Returns:
            dict: Frigate configuration or None on error
        """
        return self._make_request('/api/config')

    def get_cameras(self):
        """
        Get list of cameras from Frigate

        Returns:
            dict: Dictionary of camera configurations keyed by camera name
                  Each camera dict contains:
                  - name: Camera name
                  - enabled: Whether camera is enabled
                  - mjpeg_url: MJPEG stream URL
                  - snapshot_url: Snapshot URL
                  - rtsp_url: RTSP stream URL (if available in config)
        """
        config = self.get_config()

        if not config or 'cameras' not in config:
            xbmc.log('[Frigate] Failed to get camera configuration', xbmc.LOGERROR)
            return {}

        cameras = {}

        for camera_name, camera_config in config['cameras'].items():
            # Check if camera is enabled
            enabled = camera_config.get('enabled', True)

            # Build camera info
            camera_info = {
                'name': camera_name,
                'enabled': enabled,
                'mjpeg_url': '{0}/api/{1}'.format(self.base_url, camera_name),
                'snapshot_url': '{0}/api/{1}/latest.jpg'.format(self.base_url, camera_name),
            }

            # Extract RTSP URL from camera inputs if available
            if 'ffmpeg' in camera_config and 'inputs' in camera_config['ffmpeg']:
                inputs = camera_config['ffmpeg']['inputs']
                if inputs and len(inputs) > 0:
                    # Get first input path (usually RTSP URL)
                    first_input = inputs[0]
                    if 'path' in first_input:
                        camera_info['rtsp_url'] = first_input['path']

            cameras[camera_name] = camera_info

            xbmc.log('[Frigate] Found camera: {0} (enabled={1})'.format(camera_name, enabled), xbmc.LOGDEBUG)

        return cameras

    def get_snapshot_url(self, camera_name):
        """
        Get snapshot URL for a specific camera

        Args:
            camera_name (str): Name of the camera

        Returns:
            str: Full URL to latest snapshot
        """
        return '{0}/api/{1}/latest.jpg'.format(self.base_url, camera_name)

    def get_mjpeg_url(self, camera_name):
        """
        Get MJPEG stream URL for a specific camera

        Args:
            camera_name (str): Name of the camera

        Returns:
            str: Full URL to MJPEG stream
        """
        return '{0}/api/{1}'.format(self.base_url, camera_name)

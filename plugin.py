#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Frigate NVR Video Plugin
Provides video plugin interface for browsing and viewing Frigate cameras
"""

import sys
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon

# Python 2/3 compatibility
if sys.version_info[0] >= 3:
    from urllib.parse import parse_qsl, urlencode
    from xbmcvfs import translatePath
else:
    from urlparse import parse_qsl
    from urllib import urlencode
    from xbmc import translatePath

# Add addon directory to path
addon = xbmcaddon.Addon()
addon_path = translatePath(addon.getAddonInfo('path'))
if isinstance(addon_path, bytes):
    addon_path = addon_path.decode('utf-8')
sys.path.insert(0, addon_path)

from frigate_client import FrigateClient

# Plugin handle
handle = int(sys.argv[1])


def build_url(query):
    """Build a plugin URL with query parameters"""
    base_url = sys.argv[0]
    return base_url + '?' + urlencode(query)


def list_cameras():
    """List all available Frigate cameras"""
    xbmcplugin.setContent(handle, 'videos')

    # Get Frigate settings
    frigate_url = addon.getSetting('frigate_url')
    frigate_username = addon.getSetting('frigate_username')
    frigate_password = addon.getSetting('frigate_password')

    if not frigate_url:
        xbmcgui.Dialog().notification(
            'Frigate NVR',
            'Please configure Frigate URL in settings',
            xbmcgui.NOTIFICATION_WARNING
        )
        xbmcplugin.endOfDirectory(handle, succeeded=False)
        return

    try:
        # Get cameras from Frigate
        client = FrigateClient(frigate_url, frigate_username, frigate_password)
        cameras = client.get_cameras()

        if not cameras:
            xbmcgui.Dialog().notification(
                'Frigate NVR',
                'No cameras found',
                xbmcgui.NOTIFICATION_INFO
            )
            xbmcplugin.endOfDirectory(handle, succeeded=False)
            return

        # Add a list item for each camera
        for camera_name, camera_info in sorted(cameras.items()):
            if not camera_info.get('enabled', True):
                continue

            # Create list item
            list_item = xbmcgui.ListItem(label=camera_name)
            list_item.setInfo('video', {
                'title': camera_name,
                'mediatype': 'video'
            })

            # Set thumbnail to latest snapshot
            if 'snapshot_url' in camera_info:
                list_item.setArt({'thumb': camera_info['snapshot_url']})

            # Set as playable
            list_item.setProperty('IsPlayable', 'true')

            # Build URL to play camera
            url = build_url({'action': 'play', 'camera': camera_name})

            # Add context menu to open in overlay
            list_item.addContextMenuItems([
                ('Open in Overlay', 'RunScript({},camera={})'.format(
                    addon.getAddonInfo('id'), camera_name))
            ])

            xbmcplugin.addDirectoryItem(handle, url, list_item, isFolder=False)

        xbmcplugin.endOfDirectory(handle, succeeded=True)

    except Exception as e:
        xbmc.log('Frigate plugin error: {}'.format(str(e)), xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'Frigate NVR',
            'Error loading cameras: {}'.format(str(e)),
            xbmcgui.NOTIFICATION_ERROR
        )
        xbmcplugin.endOfDirectory(handle, succeeded=False)


def play_camera(camera_name):
    """Play a camera's live stream"""
    # Get Frigate settings
    frigate_url = addon.getSetting('frigate_url')
    frigate_username = addon.getSetting('frigate_username')
    frigate_password = addon.getSetting('frigate_password')

    try:
        xbmc.log('[Frigate Plugin] Playing camera: {}'.format(camera_name), xbmc.LOGINFO)

        # Get camera info
        client = FrigateClient(frigate_url, frigate_username, frigate_password)
        cameras = client.get_cameras()

        if camera_name not in cameras:
            xbmc.log('[Frigate Plugin] Camera not found: {}'.format(camera_name), xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                'Frigate NVR',
                'Camera not found: {}'.format(camera_name),
                xbmcgui.NOTIFICATION_ERROR
            )
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
            return

        camera_info = cameras[camera_name]
        xbmc.log('[Frigate Plugin] Camera info: {}'.format(str(camera_info)), xbmc.LOGDEBUG)

        # Prefer RTSP stream, but rewrite localhost/127.0.0.1 to use Frigate server hostname
        stream_url = None

        if 'rtsp_url' in camera_info and camera_info['rtsp_url']:
            rtsp_url = camera_info['rtsp_url']

            # If RTSP URL uses localhost/127.0.0.1, replace with Frigate server hostname
            if '127.0.0.1' in rtsp_url or 'localhost' in rtsp_url:
                # Extract hostname/IP from Frigate URL
                # frigate_url format: http://hostname:port or http://hostname
                try:
                    if sys.version_info[0] >= 3:
                        from urllib.parse import urlparse
                    else:
                        from urlparse import urlparse

                    parsed = urlparse(frigate_url)
                    frigate_host = parsed.hostname

                    if frigate_host:
                        # Replace 127.0.0.1 or localhost with Frigate hostname
                        stream_url = rtsp_url.replace('127.0.0.1', frigate_host)
                        stream_url = stream_url.replace('localhost', frigate_host)
                        xbmc.log('[Frigate Plugin] Rewritten RTSP URL from {} to {}'.format(
                            rtsp_url, stream_url), xbmc.LOGINFO)
                    else:
                        stream_url = rtsp_url
                        xbmc.log('[Frigate Plugin] Could not parse Frigate hostname, using RTSP as-is: {}'.format(
                            stream_url), xbmc.LOGWARNING)
                except Exception as e:
                    stream_url = rtsp_url
                    xbmc.log('[Frigate Plugin] Error parsing URL: {}, using RTSP as-is: {}'.format(
                        str(e), stream_url), xbmc.LOGWARNING)
            else:
                stream_url = rtsp_url
                xbmc.log('[Frigate Plugin] Using RTSP stream: {}'.format(stream_url), xbmc.LOGINFO)

        # Fall back to MJPEG if no RTSP available
        if not stream_url:
            stream_url = camera_info.get('mjpeg_url', '')
            xbmc.log('[Frigate Plugin] Using MJPEG stream: {}'.format(stream_url), xbmc.LOGINFO)

        if not stream_url:
            xbmc.log('[Frigate Plugin] No stream URL available', xbmc.LOGERROR)
            xbmcgui.Dialog().notification(
                'Frigate NVR',
                'No stream URL available for camera',
                xbmcgui.NOTIFICATION_ERROR
            )
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
            return

        # Create playable item
        list_item = xbmcgui.ListItem(path=stream_url)
        list_item.setInfo('video', {'title': camera_name})

        # Set properties for streaming
        list_item.setProperty('IsPlayable', 'true')
        list_item.setProperty('IsInternetStream', 'true')

        # Set MIME type based on stream type
        if stream_url.startswith('rtsp://'):
            # Don't set MIME type for RTSP, let Kodi handle it
            xbmc.log('[Frigate Plugin] Stream type: RTSP', xbmc.LOGDEBUG)
        else:
            # MJPEG stream
            list_item.setContentLookup(False)
            list_item.setMimeType('video/x-motion-jpeg')
            xbmc.log('[Frigate Plugin] Stream type: MJPEG', xbmc.LOGDEBUG)

        # Set thumbnail
        if 'snapshot_url' in camera_info:
            list_item.setArt({'thumb': camera_info['snapshot_url']})

        xbmc.log('[Frigate Plugin] Resolving to URL: {}'.format(stream_url), xbmc.LOGINFO)

        # Play the stream
        xbmcplugin.setResolvedUrl(handle, True, list_item)

    except Exception as e:
        xbmc.log('[Frigate Plugin] Play error: {}'.format(str(e)), xbmc.LOGERROR)
        import traceback
        xbmc.log('[Frigate Plugin] Traceback: {}'.format(traceback.format_exc()), xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'Frigate NVR',
            'Error playing camera: {}'.format(str(e)),
            xbmcgui.NOTIFICATION_ERROR
        )
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())


def router(params):
    """Route to the appropriate function based on params"""
    if params:
        # Parse parameters
        params = dict(parse_qsl(params))
        action = params.get('action')

        if action == 'play':
            play_camera(params.get('camera'))
        else:
            list_cameras()
    else:
        # No parameters - show camera list
        list_cameras()


if __name__ == '__main__':
    # Get query string (everything after ?)
    params = sys.argv[2][1:] if len(sys.argv) > 2 else ''
    router(params)

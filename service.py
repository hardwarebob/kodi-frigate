# -*- coding: utf-8 -*-
"""
Frigate NVR Integration Service
Background service that listens for Frigate detection events via MQTT
and triggers camera overlay display when configured objects are detected
"""

import sys
import os
import xbmc
import xbmcaddon
import xbmcvfs

# Add addon directory to path to import our modules
addon = xbmcaddon.Addon()
addon_path = xbmcvfs.translatePath(addon.getAddonInfo('path'))
if isinstance(addon_path, bytes):
    addon_path = addon_path.decode('utf-8')
sys.path.insert(0, addon_path)

from frigate_client import FrigateClient
from mqtt_listener import FrigateMQTTListener


class FrigateService:
    """Main service that coordinates Frigate integration"""

    def __init__(self):
        self.addon = xbmcaddon.Addon()
        self.monitor = xbmc.Monitor()
        self.frigate_client = None
        self.mqtt_listener = None
        self.cameras = {}
        self.running = False

        xbmc.log('[Frigate] Service initialized', xbmc.LOGINFO)

    def load_settings(self):
        """Load settings from Kodi addon settings"""
        settings = {
            'frigate_url': self.addon.getSetting('frigate_url'),
            'frigate_username': self.addon.getSetting('frigate_username'),
            'frigate_password': self.addon.getSetting('frigate_password'),
            'mqtt_host': self.addon.getSetting('mqtt_host'),
            'mqtt_port': int(self.addon.getSetting('mqtt_port') or '1883'),
            'mqtt_username': self.addon.getSetting('mqtt_username'),
            'mqtt_password': self.addon.getSetting('mqtt_password'),
            'mqtt_topic_prefix': self.addon.getSetting('mqtt_topic_prefix') or 'frigate',
            'trigger_objects': self.addon.getSetting('trigger_objects') or 'person,car,dog,cat',
            'min_confidence': int(self.addon.getSetting('min_confidence') or '70'),
            'trigger_on_new_only': self.addon.getSetting('trigger_on_new_only') == 'true',
            'trigger_cameras': self.addon.getSetting('trigger_cameras') or '',
        }

        xbmc.log('[Frigate] Settings loaded: frigate_url={0}, mqtt_host={1}:{2}'.format(
            settings['frigate_url'], settings['mqtt_host'], settings['mqtt_port']), xbmc.LOGINFO)

        return settings

    def initialize_frigate_client(self, settings):
        """Initialize Frigate API client"""
        frigate_url = settings['frigate_url']
        frigate_username = settings['frigate_username'] if settings['frigate_username'] else None
        frigate_password = settings['frigate_password'] if settings['frigate_password'] else None

        self.frigate_client = FrigateClient(frigate_url, frigate_username, frigate_password)

        # Discover cameras
        xbmc.log('[Frigate] Discovering cameras from Frigate...', xbmc.LOGINFO)
        self.cameras = self.frigate_client.get_cameras()

        if self.cameras:
            xbmc.log('[Frigate] Found {0} cameras: {1}'.format(
                len(self.cameras), ', '.join(self.cameras.keys())), xbmc.LOGINFO)
        else:
            xbmc.log('[Frigate] WARNING: No cameras found or failed to connect to Frigate', xbmc.LOGWARNING)

    def initialize_mqtt_listener(self, settings):
        """Initialize MQTT listener"""
        mqtt_host = settings['mqtt_host']
        mqtt_port = settings['mqtt_port']
        mqtt_username = settings['mqtt_username'] if settings['mqtt_username'] else None
        mqtt_password = settings['mqtt_password'] if settings['mqtt_password'] else None
        mqtt_topic_prefix = settings['mqtt_topic_prefix']

        self.mqtt_listener = FrigateMQTTListener(
            mqtt_host, mqtt_port, mqtt_username, mqtt_password, mqtt_topic_prefix
        )

        # Set event callback
        self.mqtt_listener.set_event_callback(self.on_detection_event)

        # Set filters
        trigger_objects = [obj.strip() for obj in settings['trigger_objects'].split(',') if obj.strip()]
        trigger_cameras = [cam.strip() for cam in settings['trigger_cameras'].split(',') if cam.strip()]
        self.mqtt_listener.set_filters(
            trigger_objects,
            settings['min_confidence'],
            settings['trigger_on_new_only'],
            trigger_cameras
        )

        # Start listener
        if self.mqtt_listener.start():
            xbmc.log('[Frigate] MQTT listener started successfully', xbmc.LOGINFO)
            return True
        else:
            xbmc.log('[Frigate] Failed to start MQTT listener', xbmc.LOGERROR)
            return False

    def on_detection_event(self, camera_name, object_type, event_data):
        """
        Callback when detection event occurs

        Args:
            camera_name (str): Name of camera with detection
            object_type (str): Type of object detected (e.g., 'person')
            event_data (dict): Full event data from MQTT
        """
        xbmc.log('[Frigate] Detection event: camera={0}, object={1}'.format(
            camera_name, object_type), xbmc.LOGINFO)

        # Check if camera exists in our discovered cameras
        if camera_name not in self.cameras:
            xbmc.log('[Frigate] Camera {0} not found in discovered cameras, skipping'.format(
                camera_name), xbmc.LOGWARNING)
            return

        camera_info = self.cameras[camera_name]

        # Check if camera is enabled
        if not camera_info.get('enabled', True):
            xbmc.log('[Frigate] Camera {0} is disabled, skipping'.format(camera_name), xbmc.LOGDEBUG)
            return

        # Trigger camera display
        self.display_camera(camera_name)

    def display_camera(self, camera_name):
        """
        Display camera overlay

        Args:
            camera_name (str): Name of camera to display
        """
        try:
            # Build command to execute the display script with camera parameter
            addon_id = self.addon.getAddonInfo('id')

            # Use RunScript to execute default.py with camera parameter
            command = 'RunScript({0},camera={1})'.format(addon_id, camera_name)

            xbmc.log('[Frigate] Executing display command: {0}'.format(command), xbmc.LOGINFO)
            xbmc.executebuiltin(command)

        except Exception as e:
            xbmc.log('[Frigate] Error displaying camera: {0}'.format(str(e)), xbmc.LOGERROR)

    def start(self):
        """Start the Frigate service"""
        xbmc.log('[Frigate] Starting Frigate NVR Integration Service', xbmc.LOGINFO)

        # Load settings
        settings = self.load_settings()

        # Initialize Frigate client and discover cameras
        frigate_initialized = False
        try:
            self.initialize_frigate_client(settings)
            frigate_initialized = True
        except Exception as e:
            xbmc.log('[Frigate] WARNING: Failed to initialize Frigate client: {0}'.format(str(e)), xbmc.LOGWARNING)
            xbmc.log('[Frigate] Video plugin will not be available until Frigate is configured', xbmc.LOGWARNING)

        # Initialize and start MQTT listener (optional)
        mqtt_initialized = False
        if settings['mqtt_host']:
            try:
                if self.initialize_mqtt_listener(settings):
                    mqtt_initialized = True
                else:
                    xbmc.log('[Frigate] WARNING: Failed to start MQTT listener', xbmc.LOGWARNING)
                    xbmc.log('[Frigate] Automatic detection events will not be available', xbmc.LOGWARNING)
            except Exception as e:
                xbmc.log('[Frigate] WARNING: Failed to initialize MQTT listener: {0}'.format(str(e)), xbmc.LOGWARNING)
                xbmc.log('[Frigate] Automatic detection events will not be available', xbmc.LOGWARNING)
        else:
            xbmc.log('[Frigate] MQTT host not configured - automatic detection events disabled', xbmc.LOGINFO)
            xbmc.log('[Frigate] Video plugin will still be available for manual camera access', xbmc.LOGINFO)

        self.running = True
        self.last_settings = settings

        # Main service loop
        if mqtt_initialized:
            xbmc.log('[Frigate] Service running with MQTT enabled, waiting for detection events...', xbmc.LOGINFO)
        else:
            xbmc.log('[Frigate] Service running in manual-only mode (no MQTT)', xbmc.LOGINFO)

        while not self.monitor.abortRequested():
            # Wait for 10 seconds, but check for abort every second
            for _ in range(10):
                if self.monitor.waitForAbort(1):
                    xbmc.log('[Frigate] Abort requested, stopping service', xbmc.LOGINFO)
                    break

            # Exit if abort was requested
            if self.monitor.abortRequested():
                break

            # Check if settings have changed
            current_settings = self.load_settings()
            if self._settings_changed(self.last_settings, current_settings):
                xbmc.log('[Frigate] Settings changed, updating...', xbmc.LOGINFO)

                # Check what changed
                mqtt_connection_changed = self._mqtt_connection_changed(self.last_settings, current_settings)
                filter_settings_changed = self._filter_settings_changed(self.last_settings, current_settings)
                frigate_settings_changed = self._frigate_settings_changed(self.last_settings, current_settings)

                # Reinitialize Frigate client if URL changed
                if frigate_settings_changed:
                    xbmc.log('[Frigate] Frigate settings changed, reinitializing client...', xbmc.LOGINFO)
                    try:
                        self.initialize_frigate_client(current_settings)
                    except Exception as e:
                        xbmc.log('[Frigate] Failed to reinitialize Frigate client: {}'.format(str(e)), xbmc.LOGWARNING)

                # Handle MQTT changes
                if mqtt_connection_changed:
                    xbmc.log('[Frigate] MQTT connection settings changed, reconnecting...', xbmc.LOGINFO)
                    # Stop current MQTT listener
                    if self.mqtt_listener:
                        self.mqtt_listener.stop()
                        self.mqtt_listener = None

                    # Reinitialize MQTT if configured
                    if current_settings['mqtt_host']:
                        try:
                            self.initialize_mqtt_listener(current_settings)
                        except Exception as e:
                            xbmc.log('[Frigate] Failed to reinitialize MQTT: {}'.format(str(e)), xbmc.LOGWARNING)
                elif filter_settings_changed and self.mqtt_listener:
                    xbmc.log('[Frigate] Filter settings changed, updating filters...', xbmc.LOGINFO)
                    # Just update filters without reconnecting
                    trigger_objects = [obj.strip() for obj in current_settings['trigger_objects'].split(',') if obj.strip()]
                    trigger_cameras = [cam.strip() for cam in current_settings['trigger_cameras'].split(',') if cam.strip()]
                    self.mqtt_listener.set_filters(
                        trigger_objects,
                        current_settings['min_confidence'],
                        current_settings['trigger_on_new_only'],
                        trigger_cameras
                    )

                # Update last_settings
                self.last_settings = current_settings

            # Verify MQTT connection is still alive
            elif self.mqtt_listener and not self.mqtt_listener.is_connected():
                xbmc.log('[Frigate] MQTT connection lost, attempting to reconnect...', xbmc.LOGWARNING)

        # Cleanup
        self.stop()

    def _settings_changed(self, old_settings, new_settings):
        """Check if any relevant settings have changed"""
        # Check MQTT settings
        mqtt_keys = ['mqtt_host', 'mqtt_port', 'mqtt_username', 'mqtt_password', 'mqtt_topic_prefix']
        for key in mqtt_keys:
            if old_settings.get(key) != new_settings.get(key):
                xbmc.log('[Frigate] Setting changed: {}'.format(key), xbmc.LOGDEBUG)
                return True

        # Check filter settings
        filter_keys = ['trigger_objects', 'min_confidence', 'trigger_on_new_only', 'trigger_cameras']
        for key in filter_keys:
            if old_settings.get(key) != new_settings.get(key):
                xbmc.log('[Frigate] Setting changed: {}'.format(key), xbmc.LOGDEBUG)
                return True

        # Check Frigate settings
        frigate_keys = ['frigate_url', 'frigate_username', 'frigate_password']
        for key in frigate_keys:
            if old_settings.get(key) != new_settings.get(key):
                xbmc.log('[Frigate] Setting changed: {}'.format(key), xbmc.LOGDEBUG)
                return True

        return False

    def _mqtt_connection_changed(self, old_settings, new_settings):
        """Check if MQTT connection settings have changed"""
        mqtt_keys = ['mqtt_host', 'mqtt_port', 'mqtt_username', 'mqtt_password', 'mqtt_topic_prefix']
        for key in mqtt_keys:
            if old_settings.get(key) != new_settings.get(key):
                return True
        return False

    def _filter_settings_changed(self, old_settings, new_settings):
        """Check if filter settings have changed"""
        filter_keys = ['trigger_objects', 'min_confidence', 'trigger_on_new_only', 'trigger_cameras']
        for key in filter_keys:
            if old_settings.get(key) != new_settings.get(key):
                return True
        return False

    def _frigate_settings_changed(self, old_settings, new_settings):
        """Check if Frigate settings have changed"""
        frigate_keys = ['frigate_url', 'frigate_username', 'frigate_password']
        for key in frigate_keys:
            if old_settings.get(key) != new_settings.get(key):
                return True
        return False

    def stop(self):
        """Stop the Frigate service"""
        xbmc.log('[Frigate] Stopping Frigate NVR Integration Service', xbmc.LOGINFO)

        if self.mqtt_listener:
            self.mqtt_listener.stop()

        self.running = False


# Entry point for service
if __name__ == '__main__':
    service = FrigateService()
    service.start()

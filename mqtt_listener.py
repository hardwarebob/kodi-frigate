# -*- coding: utf-8 -*-
"""
MQTT Event Listener for Frigate
Subscribes to Frigate MQTT events and triggers camera display on detection
"""

import json
import threading
import sys
import os
import xbmc
import xbmcaddon
import xbmcvfs

# Add resources/lib to path for bundled paho library
addon_path = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('path'))
if isinstance(addon_path, bytes):
    addon_path = addon_path.decode('utf-8')
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

try:
    import paho.mqtt.client as mqtt
except ImportError:
    xbmc.log('[Frigate] ERROR: paho.mqtt module not found in resources/lib', xbmc.LOGERROR)
    mqtt = None


class FrigateMQTTListener:
    """Listens to Frigate MQTT events and triggers camera display"""

    def __init__(self, host, port, username=None, password=None, topic_prefix='frigate'):
        """
        Initialize MQTT listener

        Args:
            host (str): MQTT broker host
            port (int): MQTT broker port
            username (str, optional): MQTT username
            password (str, optional): MQTT password
            topic_prefix (str): Frigate MQTT topic prefix (default: 'frigate')
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix
        self.client = None
        self.connected = False
        self.event_callback = None
        self.stop_event = threading.Event()

        # Settings for filtering events
        self.trigger_objects = []
        self.min_confidence = 0
        self.trigger_on_new_only = True
        self.trigger_cameras = []

        if mqtt is None:
            xbmc.log('[Frigate] MQTT module not available, listener cannot start', xbmc.LOGERROR)

    def set_event_callback(self, callback):
        """
        Set callback function to be called when detection event occurs

        Args:
            callback: Function to call with (camera_name, object_type, event_data) args
        """
        self.event_callback = callback

    def set_filters(self, trigger_objects, min_confidence=0, trigger_on_new_only=True, trigger_cameras=None):
        """
        Set filters for which events should trigger the callback

        Args:
            trigger_objects (list): List of object types to trigger on (e.g., ['person', 'car'])
            min_confidence (int): Minimum confidence percentage (0-100)
            trigger_on_new_only (bool): Only trigger on 'new' events, not 'update' or 'end'
            trigger_cameras (list): List of camera names to trigger on (empty list = all cameras)
        """
        self.trigger_objects = [obj.strip().lower() for obj in trigger_objects]
        self.trigger_cameras = [cam.strip().lower() for cam in (trigger_cameras or [])]
        self.min_confidence = min_confidence / 100.0  # Convert percentage to 0-1 range
        self.trigger_on_new_only = trigger_on_new_only

        camera_list = 'all' if not self.trigger_cameras else ', '.join(self.trigger_cameras)
        xbmc.log('[Frigate] Event filters set: objects={0}, min_confidence={1}%, new_only={2}, cameras={3}'.format(
            self.trigger_objects, min_confidence, trigger_on_new_only, camera_list), xbmc.LOGINFO)

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker"""
        if rc == 0:
            self.connected = True
            xbmc.log('[Frigate] Connected to MQTT broker at {0}:{1}'.format(self.host, self.port), xbmc.LOGINFO)

            # Subscribe to Frigate events topic
            events_topic = '{0}/events'.format(self.topic_prefix)
            client.subscribe(events_topic)
            xbmc.log('[Frigate] Subscribed to topic: {0}'.format(events_topic), xbmc.LOGINFO)
        else:
            self.connected = False
            error_messages = {
                1: 'Connection refused - incorrect protocol version',
                2: 'Connection refused - invalid client identifier',
                3: 'Connection refused - server unavailable',
                4: 'Connection refused - bad username or password',
                5: 'Connection refused - not authorized'
            }
            error_msg = error_messages.get(rc, 'Unknown error')
            xbmc.log('[Frigate] MQTT connection failed: {0}'.format(error_msg), xbmc.LOGERROR)

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker"""
        self.connected = False
        if rc != 0:
            xbmc.log('[Frigate] Unexpected MQTT disconnection. Will auto-reconnect.', xbmc.LOGWARNING)
        else:
            xbmc.log('[Frigate] Disconnected from MQTT broker', xbmc.LOGINFO)

    def _on_message(self, client, userdata, msg):
        """Callback when MQTT message received"""
        try:
            # Parse JSON payload
            payload = json.loads(msg.payload.decode('utf-8'))

            # Extract event information
            event_type = payload.get('type')  # 'new', 'update', or 'end'

            # Get the 'after' object state (current state)
            after = payload.get('after', {})

            camera_name = after.get('camera')
            object_type = after.get('label', '').lower()
            confidence = after.get('score', 0)

            # Log event details
            xbmc.log('[Frigate] Received event: type={0}, camera={1}, object={2}, confidence={3:.0%}'.format(
                event_type, camera_name, object_type, confidence), xbmc.LOGDEBUG)

            # Apply filters
            should_trigger = True

            # Filter by event type (new only if configured)
            if self.trigger_on_new_only and event_type != 'new':
                should_trigger = False
                xbmc.log('[Frigate] Skipping non-new event type: {0}'.format(event_type), xbmc.LOGDEBUG)

            # Filter by camera
            if should_trigger and self.trigger_cameras:
                if camera_name.lower() not in self.trigger_cameras:
                    should_trigger = False
                    xbmc.log('[Frigate] Skipping camera: {0} (not in trigger list)'.format(camera_name), xbmc.LOGDEBUG)

            # Filter by object type
            if should_trigger and self.trigger_objects:
                if object_type not in self.trigger_objects:
                    should_trigger = False
                    xbmc.log('[Frigate] Skipping object type: {0} (not in trigger list)'.format(object_type), xbmc.LOGDEBUG)

            # Filter by confidence
            if should_trigger and confidence < self.min_confidence:
                should_trigger = False
                xbmc.log('[Frigate] Skipping low confidence: {0:.0%} < {1:.0%}'.format(
                    confidence, self.min_confidence), xbmc.LOGDEBUG)

            # Trigger callback if all filters passed
            if should_trigger and self.event_callback:
                xbmc.log('[Frigate] Triggering display for camera: {0}, object: {1}'.format(
                    camera_name, object_type), xbmc.LOGINFO)
                self.event_callback(camera_name, object_type, payload)

        except json.JSONDecodeError as e:
            xbmc.log('[Frigate] Failed to parse MQTT message: {0}'.format(str(e)), xbmc.LOGERROR)
        except Exception as e:
            xbmc.log('[Frigate] Error processing MQTT message: {0}'.format(str(e)), xbmc.LOGERROR)

    def start(self):
        """Start MQTT listener in background thread"""
        if mqtt is None:
            xbmc.log('[Frigate] Cannot start MQTT listener - paho.mqtt not available', xbmc.LOGERROR)
            return False

        try:
            # Create MQTT client
            self.client = mqtt.Client(client_id='kodi-frigate')

            # Set authentication if provided
            if self.username and self.password:
                self.client.username_pw_set(self.username, self.password)

            # Set callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            # Connect to broker
            xbmc.log('[Frigate] Connecting to MQTT broker at {0}:{1}'.format(self.host, self.port), xbmc.LOGINFO)
            self.client.connect(self.host, self.port, keepalive=60)

            # Start network loop in background thread
            self.client.loop_start()

            return True

        except Exception as e:
            xbmc.log('[Frigate] Failed to start MQTT listener: {0}'.format(str(e)), xbmc.LOGERROR)
            return False

    def stop(self):
        """Stop MQTT listener"""
        if self.client:
            xbmc.log('[Frigate] Stopping MQTT listener', xbmc.LOGINFO)
            self.client.loop_stop()
            self.client.disconnect()
            self.connected = False

    def is_connected(self):
        """Check if connected to MQTT broker"""
        return self.connected

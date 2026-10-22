"""MQTT link on the topics the SOMICS backend grants each device.

The broker ACL allows exactly: subscribe <topic_base>/device/<ID>/command and
publish <topic_base>/device/<ID>/status. Every outbound message (state, result,
event, presence) therefore goes to the status topic, tagged with "kind".
"""
import json
import logging
import threading
import time
import uuid

from .backend import classify, topics

LOG = logging.getLogger(__name__)


def make_client(cfg, check=False):
    import paho.mqtt.client as mqtt
    options = dict(client_id=("mira-check-" + uuid.uuid4().hex[:12]) if check
                   else "mira-player-" + cfg["device_id"],
                   clean_session=True, protocol=mqtt.MQTTv311, transport="websockets")
    if hasattr(mqtt, "CallbackAPIVersion"):
        options["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
    client = mqtt.Client(**options)
    client.username_pw_set(cfg["username"], cfg["password"])
    client.ws_set_options(path=cfg["ws_path"])
    if cfg["mqtt_tls"]:
        client.tls_set()
    client.reconnect_delay_set(min_delay=2, max_delay=60)
    client.max_queued_messages_set(100)
    return client


class Broker:
    def __init__(self, cfg, check=False, client=None):
        self.cfg, self.check = cfg, check
        self.client = client or make_client(cfg, check)
        self.controller = None
        self.ready, self.rejected = threading.Event(), threading.Event()
        self.connected, self.last_mid = False, None
        self.retry_at = float("inf")
        self.command_topic, self.status_topic = topics(cfg)
        self.client.on_connect, self.client.on_subscribe = self.on_connect, self.on_subscribe
        self.client.on_disconnect, self.client.on_message = self.on_disconnect, self.on_message

    def publish(self, kind, payload, retain=False):
        """Send one device message; `retain` is ignored because the backend counts
        every status message as a heartbeat, so a retained one would fake presence."""
        if self.check:
            return
        message = dict(payload, kind=kind, deviceId=self.cfg["device_id"])
        info = self.client.publish(self.status_topic, json.dumps(message, ensure_ascii=False), qos=1)
        if getattr(info, "rc", 0):
            LOG.warning("MQTT publish deferred/rejected rc=%s kind=%s", info.rc, kind)

    def subscribe(self):
        self.retry_at = time.monotonic() + 30
        rc, self.last_mid = self.client.subscribe(self.command_topic, 1)
        if rc:
            LOG.error("Subscribe call failed rc=%s", rc)

    def on_connect(self, client, userdata, flags, rc, *extra):
        self.ready.clear()
        self.connected = rc == 0
        if rc:
            self.rejected.set()
            LOG.error("MQTT connection rejected rc=%s", rc)
            return
        LOG.info("MQTT connected host=%s port=%s", self.cfg["host"], self.cfg["port"])
        self.rejected.clear()
        self.subscribe()

    def on_subscribe(self, client, userdata, mid, granted, *extra):
        if mid != self.last_mid:
            return
        if len(granted) != 1 or granted[0] >= 128:
            self.ready.clear()
            self.rejected.set()
            LOG.error("MQTT SUBACK rejected: %s", granted)
            return
        LOG.info("MQTT subscribed topic=%s", self.command_topic)
        self.rejected.clear()
        self.ready.set()
        self.retry_at = float("inf")
        self.publish("presence", dict(state="online"))
        if self.controller and not self.check:
            self.controller.submit("mqtt", True)

    def on_disconnect(self, client, userdata, rc, *extra):
        LOG.info("MQTT disconnected rc=%s", rc)
        self.connected = False
        self.ready.clear()
        if self.controller and not self.check:
            self.controller.submit("mqtt", False)

    def on_message(self, client, userdata, message):
        if self.check or not self.controller or message.topic != self.command_topic:
            return
        try:
            kind, payload = classify(message.payload, self.cfg)
        except ValueError as error:
            self.publish("event", dict(state="rejected", error=str(error)))
            return
        if kind:
            self.controller.submit(kind, payload, message.retain)
        else:
            LOG.info("Ignoring unsupported web command on %s", message.topic)

    def start(self):
        self.client.connect_async(self.cfg["host"], self.cfg["port"], keepalive=60)
        self.client.loop_start()

    def tick(self):
        if self.connected and not self.ready.is_set() and time.monotonic() >= self.retry_at:
            self.subscribe()

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()

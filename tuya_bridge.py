#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import logging
import signal
from typing import Any, Dict, List, Tuple

import paho.mqtt.client as mqtt
from tuya_connector import TuyaOpenAPI, TuyaOpenPulsar, TuyaCloudPulsarTopic, TUYA_LOGGER

# =========================
# Configuration (env vars)
# =========================
ACCESS_ID     = os.getenv("TUYA_ACCESS_ID", "")
ACCESS_KEY    = os.getenv("TUYA_ACCESS_KEY", "")
API_ENDPOINT  = os.getenv("TUYA_API_ENDPOINT", "")           # e.g. https://openapi.tuyaeu.com

MQ_ENDPOINT   = os.getenv("TUYA_MQ_ENDPOINT", "")            # e.g. wss://mqe.tuyaeu.com:8285/

BROKER_ADDR   = os.getenv("MQTT_HOST", "127.0.0.1")
BROKER_PORT   = int(os.getenv("MQTT_PORT", "1883"))
USERNAME      = os.getenv("MQTT_USERNAME", "")
PASSWORD      = os.getenv("MQTT_PASSWORD", "")

# Incoming commands (MQTT → Tuya)
COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "tuya/command")

# Outgoing events (Tuya → MQTT)
EVENT_TOPIC   = os.getenv("MQTT_EVENT_TOPIC",   "tuya/event")  # publish Tuya events here
ACK_TOPIC     = os.getenv("MQTT_ACK_TOPIC",     "tuya/ack")    # publish command results here

MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "tuya-bridge")
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))
LOG_LEVEL      = os.getenv("LOG_LEVEL", "INFO").upper()

# TLS (optional)
ENABLE_TLS     = os.getenv("MQTT_TLS", "false").lower() in ("1", "true", "yes")
TLS_INSECURE   = os.getenv("MQTT_TLS_INSECURE", "false").lower() in ("1", "true", "yes")

# Tuya Pulsar topic (PROD/TEST). Usually PROD.
PULSAR_TOPIC   = TuyaCloudPulsarTopic.PROD

# =========================
# Logging
# =========================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("tuya-mqtt-bridge")
TUYA_LOGGER.setLevel(logging.DEBUG if LOG_LEVEL == "DEBUG" else logging.INFO)

# =========================
# Tuya OpenAPI init
# =========================
if not all([ACCESS_ID, ACCESS_KEY, API_ENDPOINT]):
    logger.warning("Incomplete Tuya OpenAPI configuration (ACCESS_ID/ACCESS_KEY/API_ENDPOINT). "
                   "Command sending may fail.")

openapi = TuyaOpenAPI(API_ENDPOINT, ACCESS_ID, ACCESS_KEY)
try:
    openapi.connect()
    logger.info("Tuya OpenAPI: connected.")
except Exception as e:
    logger.exception("Tuya OpenAPI connect() failed: %s", e)

# =========================
# MQTT client init (paho-mqtt v2-compatible)
# =========================
# Use the modern callback API version when available (paho-mqtt 2.x),
# but stay compatible with older versions too.
cbv = getattr(mqtt, "CallbackAPIVersion", None)
client_kwargs = {"client_id": MQTT_CLIENT_ID, "clean_session": True}
if cbv:
    # Try common enum names across 2.x releases
    ver2 = getattr(cbv, "VERSION2", None) or getattr(cbv, "v5", None) or getattr(cbv, "V5", None)
    if ver2:
        client_kwargs["callback_api_version"] = ver2

client = mqtt.Client(**client_kwargs)

if USERNAME:
    client.username_pw_set(username=USERNAME, password=PASSWORD or None)

# Last Will: status topic
client.will_set(f"{ACK_TOPIC}/status", payload="offline", qos=1, retain=True)

if ENABLE_TLS:
    import ssl
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    if TLS_INSECURE:
        client.tls_insecure_set(True)  # only for testing!

# =========================
# Helper functions
# =========================
def parse_payload(payload_bytes: bytes) -> Dict[str, Any]:
    """Safe JSON parsing."""
    try:
        text = payload_bytes.decode("utf-8", "strict")
    except UnicodeDecodeError as e:
        raise ValueError(f"Message is not valid UTF-8: {e}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object (dict).")
    return data


def build_tuya_command(data: Dict[str, Any]) -> Tuple[str, Dict[str, List[Dict[str, Any]]]]:
    """
    Expected input:
      {"id":"<device_id>", "code":"<dp_code>", "value":<val>}
    or
      {"id":"<device_id>", "commands":[{"code":"..","value":..}, ...]}
    """
    if "id" not in data or not isinstance(data["id"], str) or not data["id"].strip():
        raise ValueError("Missing or invalid 'id' (Tuya device ID).")
    device_id = data["id"].strip()

    if "commands" in data:
        cmds = data["commands"]
        if not isinstance(cmds, list) or not cmds:
            raise ValueError("'commands' must be a non-empty list.")
        for idx, c in enumerate(cmds):
            if not isinstance(c, dict) or "code" not in c or "value" not in c:
                raise ValueError(f"'commands[{idx}]' is invalid: must contain 'code' and 'value'.")
        commands = cmds
    else:
        if "code" not in data or "value" not in data:
            raise ValueError("Either provide a 'commands' list, or 'code' and 'value'.")
        commands = [{"code": data["code"], "value": data["value"]}]

    return device_id, {"commands": commands}


def send_tuya_command(device_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Send Tuya command via OpenAPI."""
    path = f"/v1.0/iot-03/devices/{device_id}/commands"
    logger.debug("Tuya POST %s body=%s", path, body)
    try:
        resp = openapi.post(path, body)
        logger.debug("Tuya response: %s", resp)
        return resp or {}
    except Exception as e:
        logger.exception("Tuya POST failed: %s", e)
        return {"success": False, "msg": str(e)}


def mqtt_publish_safe(topic: str, payload: Any, qos: int = 1, retain: bool = False):
    """Safe publish even from other threads (e.g. Pulsar callback)."""
    try:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        elif not isinstance(payload, (str, bytes, bytearray)):
            payload = str(payload)

        # reconnect if disconnected
        if not client.is_connected():
            try:
                client.reconnect()
            except Exception as e:
                logger.warning("MQTT reconnect failed: %s", e)

        res = client.publish(topic, payload=payload, qos=qos, retain=retain)
        res.wait_for_publish(timeout=5)
    except Exception as e:
        logger.exception("MQTT publish failed (topic=%s): %s", topic, e)


# =========================
# MQTT callbacks (v2 signatures with optional properties)
# =========================
def on_connect(_client: mqtt.Client, _userdata, _flags, rc, properties=None):
    if rc == 0:
        logger.info("MQTT connected %s:%s", BROKER_ADDR, BROKER_PORT)
        try:
            _client.subscribe(COMMAND_TOPIC, qos=1)
            logger.info("Subscribed to command topic: %s", COMMAND_TOPIC)
            _client.publish(f"{ACK_TOPIC}/status", payload="online", qos=1, retain=True)
        except Exception as e:
            logger.exception("MQTT subscribe failed: %s", e)
    else:
        logger.error("MQTT connect failed rc=%s", rc)


def on_disconnect(_client: mqtt.Client, _userdata, rc, properties=None):
    if rc != 0:
        logger.warning("Unexpected MQTT disconnect (rc=%s). Reconnecting…", rc)
    else:
        logger.info("MQTT connection closed.")


def on_message(_client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage):
    logger.debug("MQTT message: topic=%s qos=%s retained=%s payload=%r",
                 msg.topic, msg.qos, msg.retain, msg.payload)

    # Process incoming command
    try:
        data = parse_payload(msg.payload)
        device_id, body = build_tuya_command(data)
    except Exception as e:
        logger.error("Invalid command: %s | payload=%r", e, msg.payload)
        # negative ack
        nack = {
            "success": False,
            "error": "invalid_command",
            "message": str(e),
            "payload": try_decode(msg.payload)
        }
        mqtt_publish_safe(f"{ACK_TOPIC}/error", nack, qos=1, retain=False)
        return

    resp = send_tuya_command(device_id, body)
    success = bool(resp.get("success"))
    ack_payload = {
        "device_id": device_id,
        "commands": body.get("commands", []),
        "success": success,
        "code": resp.get("code"),
        "msg": resp.get("msg") or resp.get("message"),
        "raw": resp,
    }
    topic = f"{ACK_TOPIC}/ok" if success else f"{ACK_TOPIC}/error"
    mqtt_publish_safe(topic, ack_payload, qos=1, retain=False)
    if success:
        logger.info("Command successful device=%s cmds=%s", device_id, body["commands"])
    else:
        logger.error("Command FAILED device=%s code=%s msg=%s",
                     device_id, resp.get("code"), resp.get("msg") or resp.get("message"))


def try_decode(b: bytes) -> Any:
    """Try to decode a bytes payload into JSON or UTF-8 string for logging/ACK."""
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        try:
            return b.decode("utf-8", "ignore")
        except Exception:
            return str(b)


# =========================
# Tuya OpenPulsar (ingress)
# =========================
if not all([ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT]):
    logger.warning("Incomplete Tuya MQ (Pulsar) configuration (ACCESS_ID/ACCESS_KEY/MQ_ENDPOINT). "
                   "Tuya→MQTT event forwarding will not work.")

open_pulsar = TuyaOpenPulsar(ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT, PULSAR_TOPIC)

def tuya_pulsar_listener(msg):
    """
    'msg' is usually a dict (event), but may vary by connector version.
    You can adjust the MQTT topic structure if you want to route by device_id.
    """
    try:
        # Try to extract device_id if present
        device_id = None
        if isinstance(msg, dict):
            # common keys: 'devId', 'deviceId', 'data', etc.
            device_id = msg.get("devId") or msg.get("deviceId") or msg.get("device_id")
        topic = f"{EVENT_TOPIC}" + (f"/{device_id}" if device_id else "")
        mqtt_publish_safe(topic, msg, qos=1, retain=False)
        logger.debug("Tuya→MQTT event published: %s", topic)
    except Exception as e:
        logger.exception("Pulsar listener failed: %s", e)

open_pulsar.add_message_listener(tuya_pulsar_listener)

# =========================
# Graceful shutdown
# =========================
running = True
def _handle_shutdown(signum, frame):
    """Set the 'running' flag to False on SIGINT/SIGTERM and exit the loop."""
    global running
    logger.info("Shutdown signal received (%s).", signum)
    running = False

signal.signal(signal.SIGINT,  _handle_shutdown)
signal.signal(signal.SIGTERM, _handle_shutdown)

# =========================
# Main
# =========================
def main():
    # MQTT connect + loop
    try:
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        client.reconnect_delay_set(min_delay=1, max_delay=120)
        client.connect(BROKER_ADDR, BROKER_PORT, keepalive=MQTT_KEEPALIVE)
        client.loop_start()
        # publish status online
        mqtt_publish_safe(f"{ACK_TOPIC}/status", "online", qos=1, retain=True)
    except Exception as e:
        logger.exception("MQTT connect failed: %s", e)

    # Start Tuya Pulsar
    try:
        open_pulsar.start()
        logger.info("Tuya OpenPulsar started.")
    except Exception as e:
        logger.exception("Tuya OpenPulsar start() failed: %s", e)

    # Wait for signal
    try:
        while running:
            # On non-Linux, replace with time.sleep(1) if signal.pause is unavailable
            signal.pause()
    except Exception as e:
        logger.exception("Runtime error: %s", e)
    finally:
        # shutdown sequence
        try:
            open_pulsar.stop()
        except Exception:
            pass
        try:
            mqtt_publish_safe(f"{ACK_TOPIC}/status", "offline", qos=1, retain=True)
        except Exception:
            pass
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        logger.info("Stopped.")

if __name__ == "__main__":
    main()

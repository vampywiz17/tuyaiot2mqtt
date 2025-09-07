#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import logging
import signal
import time
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
EVENT_TOPIC   = os.getenv("MQTT_EVENT_TOPIC",   "tuya/event")   # publish Tuya events here
ACK_TOPIC     = os.getenv("MQTT_ACK_TOPIC",     "tuya/ack")     # publish command results here

# API request/response topics
API_REQ_TOPIC = os.getenv("MQTT_API_REQ_TOPIC", "tuya/api/request")
API_RES_TOPIC = os.getenv("MQTT_API_RES_TOPIC", "tuya/api/response")

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
# Let the Tuya SDK be chatty on DEBUG
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
# MQTT client init (with modern callback API if available)
# =========================
cbv = getattr(mqtt, "CallbackAPIVersion", None)
client_kwargs = {"client_id": MQTT_CLIENT_ID, "clean_session": True}
if cbv:
    ver2 = getattr(cbv, "VERSION2", None) or getattr(cbv, "v5", None) or getattr(cbv, "V5", None)
    if ver2:
        client_kwargs["callback_api_version"] = ver2

client = mqtt.Client(**client_kwargs)

if USERNAME:
    client.username_pw_set(username=USERNAME, password=PASSWORD or None)

# Last Will: broadcast offline on ack/status
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
    """Strict JSON parser with clear error messages."""
    try:
        text = payload_bytes.decode("utf-8", "strict")
    except UnicodeDecodeError as e:
        raise ValueError(f"Payload is not valid UTF-8: {e}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Payload is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object (dict).")
    return data


def build_tuya_command(data: Dict[str, Any]) -> Tuple[str, Dict[str, List[Dict[str, Any]]]]:
    """
    Accepts:
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
                raise ValueError(f"'commands[{idx}]' must contain 'code' and 'value'.")
        commands = cmds
    else:
        if "code" not in data or "value" not in data:
            raise ValueError("Either provide a 'commands' list, or 'code' and 'value'.")
        commands = [{"code": data["code"], "value": data["value"]}]

    return device_id, {"commands": commands}


def send_tuya_command(device_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Send Tuya command via OpenAPI and return raw response dict."""
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
    """Publish safely (reconnect if needed) and log errors."""
    try:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        elif not isinstance(payload, (str, bytes, bytearray)):
            payload = str(payload)

        # reconnect if disconnected
        if not client.is_connected():
            try:
                client.reconnect()
                logger.warning("MQTT reconnect successful.")
            except Exception as e:
                logger.warning("MQTT reconnect failed: %s", e)

        res = client.publish(topic, payload=payload, qos=qos, retain=retain)
        res.wait_for_publish(timeout=5)
        logger.debug("MQTT published topic=%s qos=%s retain=%s", topic, qos, retain)
    except Exception as e:
        logger.exception("MQTT publish failed (topic=%s): %s", topic, e)


# --- Generic Tuya OpenAPI caller (passthrough) ---
def tuya_api_call(method: str, path: str, params: Dict[str, Any] | None = None, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Generic Tuya OpenAPI call (GET/POST/PUT/DELETE).
    """
    method = (method or "GET").upper()
    logger.debug("Tuya API call %s %s params=%s body=%s", method, path, params, body)
    try:
        if method == "GET":
            resp = openapi.get(path, params or {})
        elif method == "POST":
            resp = openapi.post(path, body or {})
        elif method == "PUT":
            resp = openapi.put(path, body or {})
        elif method == "DELETE":
            resp = openapi.delete(path, params or {})
        else:
            return {"success": False, "msg": f"Unsupported method: {method}"}
        return resp or {}
    except Exception as e:
        logger.exception("Tuya API call failed: %s %s", method, path)
        return {"success": False, "msg": str(e)}


def _normalize_spec_result_to_legacy(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize different Tuya spec formats to a legacy-friendly shape:
      {
        "category": "...",
        "functions": [{"dp_id": int|None, "code": str, "type": str, "values": str}, ...],
        "status":    [{"dp_id": int|None, "code": str, "type": str, "values": str}, ...]
      }
    Accepts:
      - v1.1 legacy style (dpId/dp_id present)
      - v1.0 singular spec style (id present -> mapped to dp_id)
    """
    def _one(items):
        out = []
        for it in items or []:
            dp_id = None
            # detect dp id in multiple styles
            if isinstance(it.get("dp_id"), int):
                dp_id = it["dp_id"]
            elif isinstance(it.get("dpId"), int):
                dp_id = it["dpId"]
            elif "id" in it:
                _id = it.get("id")
                if isinstance(_id, int):
                    dp_id = _id
                elif isinstance(_id, str) and _id.isdigit():
                    dp_id = int(_id)

            vals = it.get("values")
            if not isinstance(vals, str):
                vals = json.dumps(vals or {}, ensure_ascii=False)

            out.append({
                "dp_id": dp_id,
                "code": it.get("code"),
                "type": it.get("type"),
                "values": vals
            })
        return out

    return {
        "category": result.get("category"),
        "functions": _one(result.get("functions")),
        "status": _one(result.get("status")),
    }


# =========================
# API action handlers
# =========================
def handle_api_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience actions so you don't need to remember raw paths.
    Supported:
      - list_devices: cursor-based pagination (v1.3)
          inputs: page_size (int, default 100), last_row_key (optional)
                  optional filters: source_type, source_id, name, category, product_id, device_ids
      - device_status: id
      - device_functions: id
      - device_specifications: id   (DP meta with dp_id; legacy-first with modern fallback)
    """
    action = payload.get("action")

    if action == "list_devices":
        # v1.3 cursor-based pagination: use page_size + last_row_key (+ optional filters)
        page_size = int(payload.get("page_size", 100))
        last_row_key = payload.get("last_row_key")

        params: Dict[str, Any] = {"page_size": page_size}
        if last_row_key:
            params["last_row_key"] = last_row_key

        # Optional filters (per Tuya docs)
        for key in ("source_type", "source_id", "name", "category", "product_id", "device_ids"):
            val = payload.get(key)
            if val:
                params[key] = val

        return tuya_api_call("GET", "/v1.3/iot-03/devices", params=params)

    elif action == "device_status":
        dev_id = payload.get("id")
        if not dev_id:
            return {"success": False, "msg": "Missing 'id' for device_status"}
        return tuya_api_call("GET", f"/v1.0/iot-03/devices/{dev_id}/status")

    elif action == "device_functions":
        dev_id = payload.get("id")
        if not dev_id:
            return {"success": False, "msg": "Missing 'id' for device_functions"}
        return tuya_api_call("GET", f"/v1.0/iot-03/devices/{dev_id}/functions")

    elif action == "device_specifications":
        dev_id = payload.get("id")
        if not dev_id:
            return {"success": False, "msg": "Missing 'id' for device_specifications"}

        # 1) Legacy endpoint (v1.1) returns dpId
        logger.debug("Fetching legacy device specifications (v1.1)...")
        legacy = tuya_api_call("GET", f"/v1.1/devices/{dev_id}/specifications")
        if legacy and isinstance(legacy.get("result"), dict):
            try:
                norm = _normalize_spec_result_to_legacy(legacy["result"])
                # Use legacy result if any dp_id was found
                if any(x.get("dp_id") is not None for x in norm.get("functions", [])) or \
                   any(x.get("dp_id") is not None for x in norm.get("status", [])):
                    logger.debug("Using legacy specification result (dp_id present).")
                    return {"success": True, "result": norm}
            except Exception:
                logger.debug("Legacy normalization failed; falling back to modern endpoint.")

        # 2) Fallback to modern singular spec: map 'id' -> 'dp_id' when present
        logger.debug("Fetching modern device specification (v1.0 singular)...")
        modern = tuya_api_call("GET", f"/v1.0/iot-03/devices/{dev_id}/specification", params={"lang": "en"})
        if modern and isinstance(modern.get("result"), dict):
            norm = _normalize_spec_result_to_legacy(modern["result"])
            return {"success": True, "result": norm}

        return {"success": False, "msg": "Unable to fetch device specifications from either endpoint"}

    else:
        return {"success": False, "msg": f"Unsupported action: {action}"}


def handle_api_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Two modes:
      A) action-mode: {"action": "...", ...}
      B) passthrough-mode:
         {
           "method": "GET|POST|PUT|DELETE",
           "path": "/v1.3/iot-03/devices",
           "params": { ... },   # optional (GET/DELETE)
           "body":   { ... },   # optional (POST/PUT)
           "correlation_id": "..."
         }
    """
    if "action" in payload:
        return handle_api_action(payload)

    method = payload.get("method", "GET")
    path   = payload.get("path")
    params = payload.get("params") or {}
    body   = payload.get("body")   or {}

    if not path:
        return {"success": False, "msg": "Missing 'path' for passthrough mode"}

    return tuya_api_call(method, path, params=params, body=body)


# =========================
# MQTT callbacks (with verbose logging)
# =========================
def on_connect(_client: mqtt.Client, _userdata, _flags, rc, properties=None):
    if rc == 0:
        logger.info("MQTT connected %s:%s", BROKER_ADDR, BROKER_PORT)
        try:
            _client.subscribe(COMMAND_TOPIC, qos=1)
            _client.subscribe(API_REQ_TOPIC, qos=1)
            logger.info("Subscribed to command topic: %s", COMMAND_TOPIC)
            logger.info("Subscribed to API request topic: %s", API_REQ_TOPIC)
            _client.publish(f"{ACK_TOPIC}/status", payload="online", qos=1, retain=True)
            logger.info("Published online status to %s", f"{ACK_TOPIC}/status")
        except Exception as e:
            logger.exception("MQTT subscribe/publish failed: %s", e)
    else:
        logger.error("MQTT connect failed rc=%s", rc)


def on_disconnect(_client: mqtt.Client, _userdata, rc, properties=None):
    if rc != 0:
        logger.warning("Unexpected MQTT disconnect (rc=%s). Will attempt reconnect.", rc)
    else:
        logger.info("MQTT connection closed.")


def on_message(_client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage):
    logger.debug("MQTT message: topic=%s qos=%s retained=%s payload=%r",
                 msg.topic, msg.qos, msg.retain, msg.payload)

    # API requests on dedicated topic
    if msg.topic == API_REQ_TOPIC:
        try:
            req = parse_payload(msg.payload)
            corr = req.get("correlation_id")
            logger.info("API request received action=%s correlation_id=%s", req.get("action"), corr)
            resp = handle_api_request(req)
            out = {"success": bool(resp.get("success", True)), "response": resp}
            if corr is not None:
                out["correlation_id"] = corr
            mqtt_publish_safe(API_RES_TOPIC, out, qos=1, retain=False)
            logger.info("API response published topic=%s correlation_id=%s", API_RES_TOPIC, corr)
        except Exception as e:
            logger.exception("API request handling failed: %s", e)
            out = {"success": False, "error": "api_request_failed", "msg": str(e)}
            mqtt_publish_safe(API_RES_TOPIC, out, qos=1, retain=False)
        return

    # Device command path (MQTT → Tuya)
    try:
        data = parse_payload(msg.payload)
        device_id, body = build_tuya_command(data)
    except Exception as e:
        logger.error("Invalid command: %s | payload=%r", e, msg.payload)
        nack = {
            "success": False,
            "error": "invalid_command",
            "message": str(e),
            "payload": _try_decode(msg.payload)
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


def _try_decode(b: bytes) -> Any:
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
    """
    try:
        device_id = None
        if isinstance(msg, dict):
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
        logger.info("MQTT loop started.")
        mqtt_publish_safe(f"{ACK_TOPIC}/status", "online", qos=1, retain=True)
        logger.info("Bridge status set to online.")
    except Exception as e:
        logger.exception("MQTT connect/start failed: %s", e)

    # Start Tuya Pulsar
    try:
        open_pulsar.start()
        logger.info("Tuya OpenPulsar started.")
    except Exception as e:
        logger.exception("Tuya OpenPulsar start() failed: %s", e)

    # Wait for shutdown signal
    try:
        while running:
            time.sleep(0.5)  # use signal.pause() on Linux if you prefer
    except Exception as e:
        logger.exception("Runtime error: %s", e)
    finally:
        # shutdown sequence
        try:
            open_pulsar.stop()
            logger.info("Tuya OpenPulsar stopped.")
        except Exception:
            pass
        try:
            mqtt_publish_safe(f"{ACK_TOPIC}/status", "offline", qos=1, retain=True)
            logger.info("Bridge status set to offline.")
        except Exception:
            pass
        try:
            client.loop_stop()
            client.disconnect()
            logger.info("MQTT loop stopped and disconnected.")
        except Exception:
            pass
        logger.info("Stopped.")

if __name__ == "__main__":
    main()

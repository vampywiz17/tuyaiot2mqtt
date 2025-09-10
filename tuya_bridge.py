#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import logging
import signal
import time
import threading
import socket
from queue import Queue, Empty
from typing import Any, Dict, List, Tuple, Optional

import paho.mqtt.client as mqtt
from tuya_connector import TuyaOpenAPI, TuyaOpenPulsar, TuyaCloudPulsarTopic, TUYA_LOGGER

# ========= Fast JSON (orjson -> json) =========
try:
    import orjson
    def _dumps(obj: Any) -> str:
        return orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")
except Exception:
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

# ================= Configuration =================
ACCESS_ID      = os.getenv("TUYA_ACCESS_ID", "")
ACCESS_KEY     = os.getenv("TUYA_ACCESS_KEY", "")
API_ENDPOINT   = os.getenv("TUYA_API_ENDPOINT", "")
MQ_ENDPOINT    = os.getenv("TUYA_MQ_ENDPOINT", "")

BROKER_ADDR    = os.getenv("MQTT_HOST", "127.0.0.1")
BROKER_PORT    = int(os.getenv("MQTT_PORT", "1883"))
USERNAME       = os.getenv("MQTT_USERNAME", "")
PASSWORD       = os.getenv("MQTT_PASSWORD", "")

COMMAND_TOPIC  = os.getenv("MQTT_COMMAND_TOPIC", "tuya/command")
EVENT_TOPIC    = os.getenv("MQTT_EVENT_TOPIC",   "tuya/event")
ACK_TOPIC      = os.getenv("MQTT_ACK_TOPIC",     "tuya/ack")
API_REQ_TOPIC  = os.getenv("MQTT_API_REQ_TOPIC", "tuya/api/request")
API_RES_TOPIC  = os.getenv("MQTT_API_RES_TOPIC", "tuya/api/response")

MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "tuya-bridge")
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))
LOG_LEVEL      = os.getenv("LOG_LEVEL", "INFO").upper()
ENABLE_TLS     = os.getenv("MQTT_TLS", "false").lower() in ("1", "true", "yes")
TLS_INSECURE   = os.getenv("MQTT_TLS_INSECURE", "false").lower() in ("1", "true", "yes")

EVENT_QOS      = int(os.getenv("EVENT_QOS", "0"))
ACK_QOS        = int(os.getenv("ACK_QOS", "0"))
API_QOS        = int(os.getenv("API_QOS", "0"))

CMD_WORKERS    = max(1, int(os.getenv("CMD_WORKERS", "4")))
OUT_QUEUE_SIZE = int(os.getenv("OUT_QUEUE_SIZE", "2000"))
EVT_QUEUE_SIZE = int(os.getenv("EVT_QUEUE_SIZE", "4000"))
CMD_QUEUE_SIZE = int(os.getenv("CMD_QUEUE_SIZE", "1000"))

PULSAR_TOPIC   = TuyaCloudPulsarTopic.PROD

# ================= Logging =================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tuya-mqtt-bridge")
TUYA_LOGGER.setLevel(logging.DEBUG if LOG_LEVEL == "DEBUG" else logging.INFO)

# ================= Tuya OpenAPI =================
if not all([ACCESS_ID, ACCESS_KEY, API_ENDPOINT]):
    logger.warning("Incomplete Tuya OpenAPI configuration (ACCESS_ID/ACCESS_KEY/API_ENDPOINT).")

openapi = TuyaOpenAPI(API_ENDPOINT, ACCESS_ID, ACCESS_KEY)
try:
    openapi.connect()
    logger.info("Tuya OpenAPI: connected.")
except Exception as e:
    logger.exception("Tuya OpenAPI connect() failed: %s", e)

# ================= MQTT client =================
cbv = getattr(mqtt, "CallbackAPIVersion", None)
client_kwargs = {"client_id": MQTT_CLIENT_ID, "clean_session": False}
if cbv:
    v2 = getattr(cbv, "VERSION2", None) or getattr(cbv, "v5", None) or getattr(cbv, "V5", None)
    if v2:
        client_kwargs["callback_api_version"] = v2

client = mqtt.Client(**client_kwargs)

try:
    client.socket_options = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
except Exception:
    pass

if USERNAME:
    client.username_pw_set(USERNAME, PASSWORD or None)

client.will_set(f"{ACK_TOPIC}/status", payload="offline", qos=ACK_QOS, retain=True)

if ENABLE_TLS:
    import ssl
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    if TLS_INSECURE:
        client.tls_insecure_set(True)

client.reconnect_delay_set(min_delay=1, max_delay=60)
client.max_inflight_messages_set(40)
client.max_queued_messages_set(0)

# ================= State & Queues =================
mqtt_ready = False
stop_event = threading.Event()

_out_q:  Queue[Tuple[str, str, int, bool]]        = Queue(maxsize=OUT_QUEUE_SIZE)  # MQTT offline
_evt_q:  Queue[Any]                                = Queue(maxsize=EVT_QUEUE_SIZE)  # Tuya→MQTT
_cmd_q:  Queue[Tuple[str, Dict[str, Any], str]]    = Queue(maxsize=CMD_QUEUE_SIZE)  # Commands

# ================= Helpers =================
def _fast_dump(payload: Any) -> str:
    if isinstance(payload, (dict, list)):
        return _dumps(payload)
    if isinstance(payload, (bytes, bytearray)):
        try:
            return payload.decode("utf-8")
        except Exception:
            return str(payload)
    return str(payload)

def _gen_corr() -> str:
    return f"{int(time.time()*1000)}-{os.getpid()}-{threading.get_ident()}"

def _try_decode(b: bytes) -> Any:
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        try:
            return b.decode("utf-8", "ignore")
        except Exception:
            return str(b)

def parse_payload(raw: bytes) -> Dict[str, Any]:
    try:
        text = raw.decode("utf-8", "strict")
        data = json.loads(text)
    except Exception as e:
        raise ValueError(f"Invalid JSON/UTF8: {e}")
    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object (dict).")
    return data

def mqtt_publish(topic: str, payload: Any, qos: int = 0, retain: bool = False) -> None:
    msg = _fast_dump(payload)
    if not mqtt_ready or not client.is_connected():
        try:
            _out_q.put_nowait((topic, msg, qos, retain))
            if LOG_LEVEL == "DEBUG":
                logger.debug("Queued MQTT (offline) %s", topic)
        except Exception:
            logger.warning("MQTT offline queue full, dropping msg for %s", topic)
        return
    client.publish(topic, payload=msg, qos=qos, retain=retain)

# ================= Tuya API utilities =================
def build_tuya_command(data: Dict[str, Any]) -> Tuple[str, Dict[str, List[Dict[str, Any]]]]:
    dev_id = (data.get("id") or "").strip()
    if not dev_id:
        raise ValueError("Missing or invalid 'id' (Tuya device ID).")
    if "commands" in data:
        cmds = data["commands"]
        if not isinstance(cmds, list) or not cmds:
            raise ValueError("'commands' must be a non-empty list.")
        for idx, c in enumerate(cmds):
            if not isinstance(c, dict) or "code" not in c or "value" not in c:
                raise ValueError(f"'commands[{idx}]' must contain 'code' and 'value'.")
        return dev_id, {"commands": cmds}
    if "code" not in data or "value" not in data:
        raise ValueError("Either provide a 'commands' list, or 'code' and 'value'.")
    return dev_id, {"commands": [{"code": data["code"], "value": data["value"]}]}

def send_tuya_command(device_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return openapi.post(f"/v1.0/iot-03/devices/{device_id}/commands", body) or {}
    except Exception as e:
        logger.exception("Tuya POST failed: %s", e)
        return {"success": False, "msg": str(e)}

def tuya_api_call(method: str, path: str,
                  params: Optional[Dict[str, Any]] = None,
                  body:   Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m = (method or "GET").upper()
    try:
        if m == "GET":
            resp = openapi.get(path, params or {})
        elif m == "POST":
            resp = openapi.post(path, body or {})
        elif m == "PUT":
            resp = openapi.put(path, body or {})
        elif m == "DELETE":
            resp = openapi.delete(path, params or {})
        else:
            return {"success": False, "msg": f"Unsupported method: {m}"}
        return resp or {}
    except Exception as e:
        logger.exception("Tuya API call failed: %s %s", m, path)
        return {"success": False, "msg": str(e)}

# ================= High-level API actions =================
def _normalize_spec_result_to_legacy(result: Dict[str, Any]) -> Dict[str, Any]:
    def _one(items):
        out = []
        for it in items or []:
            dp_id = (it.get("dp_id") if isinstance(it.get("dp_id"), int)
                     else it.get("dpId") if isinstance(it.get("dpId"), int)
                     else (int(it["id"]) if str(it.get("id","")).isdigit() else None))
            vals = it.get("values")
            if not isinstance(vals, str):
                vals = _dumps(vals or {})
            out.append({"dp_id": dp_id, "code": it.get("code"), "type": it.get("type"), "values": vals})
        return out
    return {"category": result.get("category"),
            "functions": _one(result.get("functions")),
            "status":    _one(result.get("status"))}

def handle_api_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    act = payload.get("action")
    if act == "list_devices":
        params = {"page_size": int(payload.get("page_size", 100))}
        if payload.get("last_row_key"): params["last_row_key"] = payload["last_row_key"]
        for k in ("source_type","source_id","name","category","product_id","device_ids"):
            if payload.get(k): params[k] = payload[k]
        return tuya_api_call("GET", "/v1.3/iot-03/devices", params=params)

    if act == "device_status":
        dev_id = payload.get("id")
        if not dev_id: return {"success": False, "msg": "Missing 'id' for device_status"}
        return tuya_api_call("GET", f"/v1.0/iot-03/devices/{dev_id}/status")

    if act == "device_functions":
        dev_id = payload.get("id")
        if not dev_id: return {"success": False, "msg": "Missing 'id' for device_functions"}
        return tuya_api_call("GET", f"/v1.0/iot-03/devices/{dev_id}/functions")

    if act == "device_specifications":
        dev_id = payload.get("id")
        if not dev_id: return {"success": False, "msg": "Missing 'id' for device_specifications"}
        legacy = tuya_api_call("GET", f"/v1.1/devices/{dev_id}/specifications")
        if legacy and isinstance(legacy.get("result"), dict):
            try:
                norm = _normalize_spec_result_to_legacy(legacy["result"])
                if any(x.get("dp_id") is not None for x in norm["functions"]) or \
                   any(x.get("dp_id") is not None for x in norm["status"]):
                    return {"success": True, "result": norm}
            except Exception:
                pass
        modern = tuya_api_call("GET", f"/v1.0/iot-03/devices/{dev_id}/specification", params={"lang":"en"})
        if modern and isinstance(modern.get("result"), dict):
            return {"success": True, "result": _normalize_spec_result_to_legacy(modern["result"])}
        return {"success": False, "msg": "Unable to fetch device specifications from either endpoint"}

    return {"success": False, "msg": f"Unsupported action: {act}"}

def handle_api_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    if "action" in payload:
        return handle_api_action(payload)
    method = payload.get("method", "GET")
    path   = payload.get("path")
    if not path: return {"success": False, "msg": "Missing 'path' for passthrough mode"}
    return tuya_api_call(method, path, params=payload.get("params") or {}, body=payload.get("body") or {})

# ================= Workers =================
def command_worker():
    while not stop_event.is_set():
        try:
            device_id, body, corr = _cmd_q.get(timeout=0.5)
        except Empty:
            continue
        try:
            resp = send_tuya_command(device_id, body)
            success = bool(resp.get("success"))
            ack = {
                "device_id": device_id,
                "commands": body.get("commands", []),
                "success": success,
                "code": resp.get("code"),
                "msg": resp.get("msg") or resp.get("message"),
                "raw": resp,
                "correlation_id": corr,
            }
            topic = f"{ACK_TOPIC}/ok" if success else f"{ACK_TOPIC}/error"
            mqtt_publish(topic, ack, qos=ACK_QOS, retain=False)
        except Exception as e:
            mqtt_publish(f"{ACK_TOPIC}/error", {
                "device_id": device_id,
                "success": False,
                "error": "command_failed",
                "msg": str(e),
                "commands": body.get("commands", []),
                "correlation_id": corr,
            }, qos=ACK_QOS, retain=False)
        finally:
            _cmd_q.task_done()

def tuya_event_worker():
    while not stop_event.is_set():
        try:
            msg = _evt_q.get(timeout=0.5)
        except Empty:
            continue
        try:
            dev_id = msg.get("devId") or msg.get("deviceId") or msg.get("device_id") if isinstance(msg, dict) else None
            topic = f"{EVENT_TOPIC}" + (f"/{dev_id}" if dev_id else "")
            mqtt_publish(topic, msg, qos=EVENT_QOS, retain=False)
        except Exception as e:
            logger.exception("Pulsar event processing failed: %s", e)
        finally:
            _evt_q.task_done()

def pulsar_supervisor(_unused=None):
    global open_pulsar
    started = False

    while not stop_event.is_set():
        try:
            # első indulás vagy előző hiba után új példányt építünk
            if not started:
                # ha van régi példány, próbáljuk megállítani (csendben)
                try:
                    open_pulsar.stop()
                except Exception:
                    pass

                # új példány + listener
                open_pulsar = TuyaOpenPulsar(ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT, PULSAR_TOPIC)
                open_pulsar.add_message_listener(tuya_pulsar_listener)

                open_pulsar.start()
                started = True
                logger.info("Tuya OpenPulsar started.")

            # csak várunk a leállításra; nem állítjuk le percenként
            stop_event.wait(1.0)

        except Exception as e:
            # bármilyen hiba: próbáljuk leállítani, majd új példányt hozunk létre
            logger.warning("Pulsar error: %s -- recreating in 5s", e)
            try:
                open_pulsar.stop()
            except Exception:
                pass
            started = False
            # kis backoff, majd újrakezdjük a ciklust (új példányt építünk)
            if stop_event.wait(5.0):
                break

    # végső leállítás
    try:
        open_pulsar.stop()
    except Exception:
        pass

# ================= MQTT Callbacks =================
def on_connect(c: mqtt.Client, _u, _f, rc, properties=None):
    global mqtt_ready
    if rc != 0:
        logger.error("MQTT connect failed rc=%s", rc); return
    mqtt_ready = True
    try:
        c.subscribe(COMMAND_TOPIC, qos=max(ACK_QOS, EVENT_QOS))
        c.subscribe(API_REQ_TOPIC, qos=API_QOS)
        c.publish(f"{ACK_TOPIC}/status", payload="online", qos=ACK_QOS, retain=True)
    except Exception as e:
        logger.exception("MQTT subscribe/publish failed: %s", e)
    drained = 0
    while True:
        try:
            t, p, q, r = _out_q.get_nowait()
        except Empty:
            break
        c.publish(t, payload=p, qos=q, retain=r)
        drained += 1
    if drained:
        logger.info("Flushed %s queued MQTT messages.", drained)

def on_disconnect(_c, _u, rc, properties=None):
    global mqtt_ready
    mqtt_ready = False
    if rc != 0: logger.warning("Unexpected MQTT disconnect (rc=%s).", rc)

def on_subscribe(_c, _u, mid, granted_qos, properties=None):
    if LOG_LEVEL == "DEBUG":
        logger.debug("MQTT subscribed mid=%s qos=%s", mid, granted_qos)

def on_message(_c: mqtt.Client, _u, msg: mqtt.MQTTMessage):
    # API requests
    if msg.topic == API_REQ_TOPIC:
        try:
            req  = parse_payload(msg.payload)
            corr = req.get("correlation_id") or _gen_corr()
            resp = handle_api_request(req)
            mqtt_publish(API_RES_TOPIC, {"success": bool(resp.get("success", True)),
                                         "response": resp, "correlation_id": corr},
                         qos=API_QOS, retain=False)
        except Exception as e:
            mqtt_publish(API_RES_TOPIC, {"success": False, "error": "api_request_failed",
                                         "msg": str(e), "correlation_id": _gen_corr()},
                         qos=API_QOS, retain=False)
        return

    # Device commands
    try:
        data = parse_payload(msg.payload)
        device_id, body = build_tuya_command(data)
        corr = data.get("correlation_id") or _gen_corr()
        try:
            _cmd_q.put_nowait((device_id, body, corr))
        except Exception:
            mqtt_publish(f"{ACK_TOPIC}/error", {
                "device_id": device_id, "success": False, "error": "queue_full",
                "msg": "command queue is full", "correlation_id": corr
            }, qos=ACK_QOS, retain=False)
    except Exception as e:
        mqtt_publish(f"{ACK_TOPIC}/error", {
            "success": False, "error": "invalid_command", "message": str(e),
            "payload": _try_decode(msg.payload), "correlation_id": _gen_corr()
        }, qos=ACK_QOS, retain=False)

# ================= Tuya OpenPulsar (ingress) =================
if not all([ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT]):
    logger.warning("Incomplete Tuya MQ (Pulsar) configuration (ACCESS_ID/ACCESS_KEY/MQ_ENDPOINT).")

open_pulsar = TuyaOpenPulsar(ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT, PULSAR_TOPIC)

def tuya_pulsar_listener(msg):
    try:
        _evt_q.put_nowait(msg)
    except Exception:
        logger.warning("Tuya event queue full, dropping event")

open_pulsar.add_message_listener(tuya_pulsar_listener)

# ================= Shutdown handling =================
def _handle_shutdown(signum, _frame):
    logger.info("Shutdown signal received (%s).", signum)
    stop_event.set()

signal.signal(signal.SIGINT,  _handle_shutdown)
signal.signal(signal.SIGTERM, _handle_shutdown)

# ================= Main =================
def main():
    # MQTT
    try:
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        client.on_subscribe = on_subscribe

        client.connect(BROKER_ADDR, BROKER_PORT, keepalive=MQTT_KEEPALIVE)
        client.loop_start()
        logger.info("MQTT loop started.")
        mqtt_publish(f"{ACK_TOPIC}/status", "online", qos=ACK_QOS, retain=True)
    except Exception as e:
        logger.exception("MQTT connect/start failed: %s", e)

    # Workers
    for i in range(CMD_WORKERS):
        threading.Thread(target=command_worker, name=f"cmd-worker-{i+1}", daemon=True).start()
    threading.Thread(target=tuya_event_worker, name="tuya-event-worker", daemon=True).start()
    threading.Thread(target=pulsar_supervisor, args=(open_pulsar,), name="pulsar-supervisor", daemon=True).start()

    # Idle loop
    try:
        while not stop_event.wait(0.5):
            pass
    except Exception as e:
        logger.exception("Runtime error: %s", e)
    finally:
        try:
            mqtt_publish(f"{ACK_TOPIC}/status", "offline", qos=ACK_QOS, retain=True)
        except Exception:
            pass
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        try:
            open_pulsar.stop()
        except Exception:
            pass
        logger.info("Stopped.")

if __name__ == "__main__":
    main()

Changelog
=========

All notable changes to this project will be documented in this file.

## [0.0.0.3] – 2025-09-10
### Added
- Fast JSON serialization via `orjson` if available; fallback to `json` with compact separators. Introduced `_dumps()` helper.
- Central `stop_event` `threading.Event()` for cleaner shutdown of background workers.
- Parallel command processing using configurable `CMD_WORKERS` threads.
- Dedicated Tuya event worker thread: listener now only queues events, worker handles MQTT emits.
- Robust Pulsar supervisor: on errors, creates a new `TuyaOpenPulsar` instance (avoids “threads can only be started once” issues).
- Optional `TCP_NODELAY` on MQTT client to reduce Nagle-induced latency.

### Changed
- Simplified helpers: merged JSON dump, correlation generation, payload parse, and publish into concise, flat helper functions (`_fast_dump`, `_gen_corr`, `parse_payload()`, `mqtt_publish()`).
- MQTT publishing no longer blocks or manually reconnects. Offline messages are queued in `_out_q` and flushed on reconnect (in `on_connect()`).
- Persistent MQTT session (`clean_session=False`), with automatic resubscribe on connect.
- Added ability to configure QoS levels via environment variables (`EVENT_QOS`, `ACK_QOS`, `API_QOS`, defaulting to 0 for minimal latency).
- Simplified spec normalization in ` _normalize_spec_result_to_legacy()` with clearer value handling using `_dumps()`.
- Increased default MQTT inflight messages (40) and disabled built-in queue in favor of custom queue.

### Fixed
- Fixed deadlock issue in Pulsar handling: now recreates `TuyaOpenPulsar` instance upon failure instead of restarting existing thread.
- Ensured all MQTT callbacks (`on_connect`, `on_message`, etc.) are compatible with v5/v2 callback signatures (`properties=None` present).

### Performance
- Reduced blocking: removed `wait_for_publish()` and moved to worker-thread-based command execution.
- Lower latency on publishing and JSON handling.
- Tuned Paho settings: `max_inflight_messages_set(40)`, `max_queued_messages_set(0)`—custom queue used instead.
- More responsive shutdown: background threads use shorter timeouts for quicker termination.

### Configuration / Dev Changes
- Added optional environment variables:
  - `EVENT_QOS`, `ACK_QOS`, `API_QOS`
  - `CMD_WORKERS`, `OUT_QUEUE_SIZE`, `EVT_QUEUE_SIZE`, `CMD_QUEUE_SIZE`
- No changes required for existing configuration values (`ACCESS_ID`, `MQTT_*`, etc.).
- Backward compatible; behavior remains consistent unless optional vars are modified.

[0.0.2] — 2025-09-08
--------------------

### Added
- Cloud API over MQTT (request/response)
  - Request topic: tuya/api/request
  - Response topic: tuya/api/response

- Action helpers
  - list_devices — Tuya v1.3 cursor-based listing (supports page_size, last_row_key, and optional filters source_type, source_id, name, category, product_id, device_ids).
  - device_status — Current DP values of a device.
  - device_functions — Supported function codes of a device.
  - device_specifications — DP meta (code/type/values + dp_id).
    Uses legacy-first strategy to ensure DP IDs are available:
      1) GET /v1.1/devices/{device_id}/specifications (returns dpId)
      2) Fallback: GET /v1.0/iot-03/devices/{device_id}/specification (maps id → dp_id)
    The result is normalized to a consistent shape with dp_id, code, type, values (values is always a string).

- Passthrough mode (generic Tuya OpenAPI caller)
  Call any supported Tuya OpenAPI endpoint over MQTT without adding code changes.
  Supported methods: GET, POST, PUT, DELETE.
  Request shape:
    {
      "method": "GET|POST|PUT|DELETE",
      "path": "<Tuya API path, e.g. /v1.3/iot-03/devices>",
      "params": { ... },   // optional, for GET/DELETE
      "body":   { ... },   // optional, for POST/PUT
      "correlation_id": "optional-tracking-id"
    }



[0.0.1] — 2025-09-07
--------------------

### Added
- Two-way MQTT ↔ Tuya Cloud bridge
  - Commands (MQTT → Tuya OpenAPI)
    Send device commands to tuya/command:
    - Single command:
        { "id": "<device_id>", "code": "switch_1", "value": true }
    - Multiple commands:
        {
          "id": "<device_id>",
          "commands": [
            { "code": "switch_1", "value": true },
            { "code": "bright_value", "value": 500 }
          ]
        }
    - Acks on:
      - tuya/ack/ok (success)
      - tuya/ack/error (failure with details)
    - LWT status on tuya/ack/status (online/offline).

  - Events (Tuya Pulsar → MQTT)
    Tuya cloud events are forwarded to:
    - tuya/event
    - tuya/event/<device_id> when the device ID can be extracted.

- Resilient JSON parsing & error reporting
  Invalid or non-UTF-8 messages on the command topic produce a structured negative ack on tuya/ack/error with the original payload echoed when possible.

- Connection robustness
  - Auto reconnect for MQTT.
  - Clean logging with adjustable verbosity via LOG_LEVEL.
  - Optional TLS (with MQTT_TLS, MQTT_TLS_INSECURE).
  - paho-mqtt v2 API compatible callbacks.

### Environment variables (excerpt)
- Tuya Cloud: TUYA_ACCESS_ID, TUYA_ACCESS_KEY, TUYA_API_ENDPOINT, TUYA_MQ_ENDPOINT
- MQTT: MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD
- Topics: MQTT_COMMAND_TOPIC, MQTT_EVENT_TOPIC, MQTT_ACK_TOPIC
- Misc: MQTT_CLIENT_ID, MQTT_KEEPALIVE, LOG_LEVEL, MQTT_TLS, MQTT_TLS_INSECURE

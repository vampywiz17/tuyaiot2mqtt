Changelog
=========

All notable changes to this project will be documented in this file.

[0.0.2] — 2025-09-08
--------------------

### Added
- Tuya OpenAPI over MQTT (request/response)
  New MQTT topics for on-demand Tuya Cloud API calls:
  - Request topic: tuya/api/request
  - Response topic: tuya/api/response

- Action-based helpers
  - action: "list_devices" — Lists devices using the Tuya v1.3 cursor-based endpoint.
    Supports:
    - page_size (default 100)
    - last_row_key (cursor for next page)
    - optional filters: source_type, source_id, name, category, product_id, device_ids
    - Typical user scope (recommended):
      source_type: "tuyaUser", source_id: "<UID from Link Tuya App Account>"
    - Request example:
      {
        "action": "list_devices",
        "page_size": 50,
        "source_type": "tuyaUser",
        "source_id": "<YOUR_UID>",
        "correlation_id": "abc-123"
      }
    - Paginate next page (if has_more: true):
      {
        "action": "list_devices",
        "page_size": 50,
        "last_row_key": "<from-previous-response>",
        "source_type": "tuyaUser",
        "source_id": "<YOUR_UID>",
        "correlation_id": "abc-124"
      }

  - action: "device_status" — Gets DP status for a specific device.
    { "action": "device_status", "id": "<device_id>", "correlation_id": "chk-1" }

  - action: "device_functions" — Gets supported DP codes/functions for a device.
    { "action": "device_functions", "id": "<device_id>", "correlation_id": "fn-1" }

- Passthrough mode (generic API caller)
  Call any supported Tuya OpenAPI endpoint via MQTT without adding code:
    {
      "method": "GET",
      "path": "/v1.3/iot-03/devices",
      "params": { "page_size": 50 },
      "correlation_id": "pt-1"
    }
  Supported methods: GET, POST, PUT, DELETE.

- Correlation IDs
  Any correlation_id in the request is echoed back in the response to simplify flow matching in Node-RED/HA automations.

### Behavior
- API responses are published to tuya/api/response with the shape:
    {
      "success": true,
      "response": { /* Tuya API raw response */ },
      "correlation_id": "..."
    }
  On failure:
    {
      "success": false,
      "error": "api_request_failed",
      "msg": "error details",
      "correlation_id": "..."
    }

### Notes
- Device listing uses Tuya v1.3 (cursor pagination) — there is no page_no; use page_size + last_row_key.
- In many accounts, listing requires scope (e.g., source_type: "tuyaUser" + source_id: "<UID>").
- Existing command/event bridge functionality remains unchanged.

### Security
- Consider broker auth/ACLs for the new API request topic.
- Be mindful that some API responses may include sensitive fields (e.g., ip, lat, lon, local_key). Filter or handle appropriately downstream if needed.


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

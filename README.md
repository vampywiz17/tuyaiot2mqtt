# FOR TESTING! Command script version 2

# tuyaiot2mqtt

Bridge between the Tuya IoT Development Platform and a local MQTT broker.

This script connects your Tuya IoT project to a local MQTT broker.  
It forwards Tuya cloud device events into MQTT topics and also allows you to send commands from MQTT back to Tuya devices.

This is useful for catching and controlling features that are only exposed via the Tuya cloud (e.g. alarm system alerts, doorbell pushes) which cannot be accessed via the local Tuya APIs.

---

## Features

- **Two-way bridge**
  - MQTT → Tuya Cloud (send commands)
  - Tuya Cloud → MQTT (receive events)
- Error handling & logging
- Works in Docker (recommended)
- Configurable topics and credentials via environment variables

---

## Requirements

- A Tuya IoT account at [https://iot.tuya.com/](https://iot.tuya.com/) with a registered project.  
  Follow the setup guide: [Home Assistant Tuya integration](https://www.home-assistant.io/integrations/tuya/).
- Enable **Industry Project Client Service** and **Message Service** for your Tuya project.
- Access ID and Access Key from your Tuya project.
- API and MQ endpoints for your region (see below).

---

## Endpoints

### API endpoints

| Location | API_ENDPOINT                  |
|----------|-------------------------------|
| China    | https://openapi.tuyacn.com    |
| America  | https://openapi.tuyaus.com    |
| Europe   | https://openapi.tuyaeu.com    |
| India    | https://openapi.tuyain.com    |

### MQ endpoints

| Location | MQ_ENDPOINT                    |
|----------|--------------------------------|
| China    | wss://mqe.tuyacn.com:8285/     |
| America  | wss://mqe.tuyaus.com:8285/     |
| Europe   | wss://mqe.tuyaeu.com:8285/     |
| India    | wss://mqe.tuyain.com:8285/     |

---

## MQTT Topics

- **Command topic** (default: `tuya/command`)  
  Send JSON commands here, which will be forwarded to Tuya OpenAPI.

- **Acknowledgements** (default: `tuya/ack/...`)  
  - `tuya/ack/ok` – command succeeded  
  - `tuya/ack/error` – command failed (with error details)

- **Events** (default: `tuya/event/...`)  
  Device state updates and Pulsar events forwarded from Tuya.  
  Typically published as `tuya/event/<deviceId>`.

---

## Example Command Payloads

**Single command:**

```json
{
  "id": "<device_ID>",
  "code": "switch_1",
  "value": true
}
```

***Multiple commands:***

```json
{
  "id": "<DEVICE_ID>",
  "commands": [
    { "code": "switch_1", "value": true },
    { "code": "bright_value", "value": 500 }
  ]
}
```
## Supported API Payloads

***List devices***

```json
{
  "action": "list_devices",
  "page_size": 50,
  "source_type": "tuyaUser",
  "source_id": "<YOUR_UID>",
  "correlation_id": "list-1"
}
```

***Device status***

```json
{
  "action": "device_status",
  "id": "<DEVICE_ID>",
  "correlation_id": "status-1"
}
```

***Device specifications***

```json
{
  "action": "device_specifications",
  "id": "<DEVICE_ID>",
  "correlation_id": "spec-1"
}
```

***Passthrough mode example***

```json
  {
    "method": "GET",
    "path": "/v1.0/iot-03/devices/<DEVICE_ID>/status",
    "correlation_id": "pt-2"
  }
```


***Notes:***

- `id` is the Tuya **device ID** (from the IoT portal).
- `code` and `value` come from the **Device Debugging** page in the IoT portal.
- Use lowercase `true`/`false` for boolean values (JSON standard).
- You found UID with Tuya/Smart home app pair page on Development portal

---

## Usage with Home Assistant

This script publishes raw JSON payloads to MQTT.  
They are **not directly usable** as Home Assistant devices.  
Instead, you can:

- Use **MQTT sensor/switch templates**: https://www.home-assistant.io/integrations/sensor.mqtt/  
- Or use **Node-RED** flows to process the messages.

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/vampywiz17/tuyaiot2mqtt.git
cd tuyaiot2mqtt
```

### 2. Build Docker image

```bash
docker build -t tuyaiot2mqtt .
```

### 3. Run with Docker Compose

Edit the provided `docker-compose.yml` and set your environment variables:

```yaml
services:
  tuya-bridge:
    image: tuyaiot2mqtt:latest
    restart: unless-stopped
    environment:
      TUYA_ACCESS_ID: "<your-tuya-access-id>"
      TUYA_ACCESS_KEY: "<your-tuya-access-key>"
      TUYA_API_ENDPOINT: "https://openapi.tuyaeu.com"
      TUYA_MQ_ENDPOINT: "wss://mqe.tuyaeu.com:8285/"
      MQTT_HOST: "127.0.0.1"
      MQTT_PORT: "1883"
      MQTT_USERNAME: ""
      MQTT_PASSWORD: ""
      MQTT_CLIENT_ID: "tuya-bridge"
      MQTT_KEEPALIVE: "60"
      MQTT_COMMAND_TOPIC: "tuya/command"
      MQTT_EVENT_TOPIC: "tuya/event"
      MQTT_ACK_TOPIC: "tuya/ack"
      LOG_LEVEL: "INFO"
      MQTT_TLS: "false"
      MQTT_TLS_INSECURE: "false"
```

Then start it:

```bash
docker compose up -d
```

---

## Notes & Limitations

- This depends on the Tuya cloud. If Tuya servers are unavailable, the bridge cannot function.
- This is **not a replacement** for the official Tuya or LocalTuya integrations.
- Best suited for catching special events that don’t propagate locally.

---

## Status

Experimental / for testing. Feedback and contributions welcome!

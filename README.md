# tuyaiot2mqtt
Connect Tuya IoT Development Platform to local MQTT server

This little script connect Tuya IoT Platform to local MQTT server. In this method is possible to catch specific messages that Tuya devices only send to cloud and not possible to hande it with local tuya API (like alarm system alert, doorbell push, etc...)

This method is build a MQTT connection between Tuya and your computer. It is the most "deeper" connection that possible to use it with regual user. So it really fast. But of course, it depend the Tuya servers avability!

Attention!

- This script not able to control tuya devices! My goal is only catch "cloud only" messages, I control all devices with [tuya-local](https://github.com/make-all/tuya-local) integration.
- It only work that you register https://iot.tuya.com/ website and create a project! Follow this rule to do this: https://www.home-assistant.io/integrations/tuya/
- To this script work, need to add "Industry Project Client Service" to your project and enable "Message Service"
- This script send formated json to local MQTT, but this unsuitable to send it to Home Assistant or other Smart Home system directly! Need to deal with it. The simplest way, that use Node-RED!
- If Tuya servers are not available, of course this script is not working! 

# INSTALL

dependencies:

- Python 3
- Pip3
- tuya-connector-python
- paho-mqtt

Install Python 3 and modules, and clone this repo. (install modules with `sudo pip3 install --user` argument, if you want to use it systemd service!)

Edit tuyaiot2mqtt.py, fill out the "config" section.

Run this commands:

`chmod +x tuyaiot2mqtt.py`

`./tuyaiot2mqtt.py`

Done!

# Run as systemd service

install tuyaiot2mqtt.py

`sudo install -D -m 764 iot2mqtt.py /opt/tuyaiot2mqtt/tuyaiot2mqtt.py`

create a new systemd service:

`sudo vi /etc/systemd/system/tuyaiot2mqtt.service`

copy this:

```
[Unit]
Description=tuyaiot2mqtt service
After=multi-user.target
[Service]
Type=simple
Restart=always
ExecStart=/usr/bin/python3 /opt/tuyaiot2mqtt/tuyaiot2mqtt.py
[Install]
WantedBy=multi-user.target
```


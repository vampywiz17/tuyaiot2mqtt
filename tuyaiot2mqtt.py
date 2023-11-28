#!/usr/bin/env python3

import paho.mqtt.client as mqtt
from tuya_connector import TUYA_LOGGER, TuyaOpenPulsar, TuyaCloudPulsarTopic

# Start config

ACCESS_ID = "" # Tuya IoT ID
ACCESS_KEY = "" # Tuya IoT key
BROKER_ADDRESS = "" # Local mqtt server address
BROKER_PORT = "" # Leave it blank, if use default
USERNAME = "" # Leave it blank, if not use authentication
PASSWORD = "" # Leave it blank, if not use authentication
TOPIC = ""  # mqtt topic name
MQ_ENDPOINT = "" # Tuya MQTT endpoint. See available regions in README
CLIENT_NAME = "" # Client name

# End config

open_pulsar = TuyaOpenPulsar(
    ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT, TuyaCloudPulsarTopic.PROD
)

client = mqtt.Client(CLIENT_NAME)
client.username_pw_set(username=USERNAME,password=PASSWORD)

open_pulsar.add_message_listener(lambda msg: (client.connect(BROKER_ADDRESS, BROKER_PORT), client.publish(TOPIC,msg)))

# Start Message Queue
open_pulsar.start()

input()
# Stop Message Queue
open_pulsar.stop()

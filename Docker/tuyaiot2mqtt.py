#!/usr/bin/env python3

import paho.mqtt.client as mqtt
from tuya_connector import TUYA_LOGGER, TuyaOpenPulsar, TuyaCloudPulsarTopic
import os

# Start config

ACCESS_ID = os.environ['ACCESS_ID']
ACCESS_KEY = os.environ['ACCESS_KEY']
BROKER_ADDRESS = os.environ['BROKER_ADDRESS']
BROKER_PORT = os.environ['BROKER_PORT']
USERNAME = os.environ['USERNAME']
PASSWORD = os.environ['PASSWORD']
TOPIC = os.environ['TOPIC']
MQ_ENDPOINT = os.environ['MQ_ENDPOINT']
CLIENT_NAME = os.environ['CLIENT_NAME']

# End config

open_pulsar = TuyaOpenPulsar(
    ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT, TuyaCloudPulsarTopic.PROD
)

BROKER_PORT_INT = int(BROKER_PORT)

client = mqtt.Client(CLIENT_NAME)
client.username_pw_set(username=USERNAME,password=PASSWORD)

open_pulsar.add_message_listener(lambda msg: (client.connect(BROKER_ADDRESS, BROKER_PORT_INT), client.publish(TOPIC,msg)))

# Start Message Queue
open_pulsar.start()

input()
# Stop Message Queue
open_pulsar.stop()

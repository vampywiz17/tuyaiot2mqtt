import paho.mqtt.client as mqtt
import subprocess
import logging
import json
from tuya_connector import TuyaOpenAPI, TUYA_LOGGER

# Set up cloud connection parameters
ACCESS_ID = "" # Tuya IoT ID
ACCESS_KEY = "" # Tuya IoT key
BROKER_ADDRESS = "" # Local mqtt server address
BROKER_PORT =  # Leave it blank, if use default
USERNAME = "" # Leave it blank, if not use authentication
PASSWORD = "" # Leave it blank, if not use authentication
SET_TOPIC = ""  # mqtt topic name
API_ENDPOINT = "" # API endpoint address

# Enable debug log
TUYA_LOGGER.setLevel(logging.DEBUG)

# Init OpenAPI and connect
openapi = TuyaOpenAPI(API_ENDPOINT, ACCESS_ID, ACCESS_KEY)
openapi.connect()

# Command send
def on_message(client, userdata, msg, ):
    command = eval(str(msg.payload.decode("utf-8","ignore")))
    json_dump = json.dumps(command, ensure_ascii=False)
    json_data = json.loads(json_dump)
    commands = {'commands': [command]}
    openapi.post('/v1.0/iot-03/devices/{}/commands'.format(json_data["id"]), commands)

# Init mqtt and connect
client = mqtt.Client()
client.username_pw_set(username=USERNAME,password=PASSWORD)
client.connect(BROKER_ADDRESS, BROKER_PORT)
client.subscribe( SET_TOPIC , qos=0)

# Trigger mqtt message
client.on_message = on_message
client.loop_forever()
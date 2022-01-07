import paho.mqtt.client as mqtt
from tuya_connector import TUYA_LOGGER, TuyaOpenPulsar, TuyaCloudPulsarTopic

# Start config

ACCESS_ID = "" # Tuya IoT ID
ACCESS_KEY = "" # Tuya IoT key
BROKER_ADDRESS = "" # Local mqtt server address
TOPIC = ""  # mqtt topic name
MQ_ENDPOINT = "wss://mqe.tuyaeu.com:8285/" # Tuya MQTT endpoint. Only need to change, if you use different region, not EU
CLIENT_NAME = "tuya_iot_client" # Client name. Not need to change, by default

# End config

open_pulsar = TuyaOpenPulsar(
    ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT, TuyaCloudPulsarTopic.PROD
)

client = mqtt.Client(CLIENT_NAME)

open_pulsar.add_message_listener(lambda msg: (client.connect(BROKER_ADDRESS), client.publish(TOPIC,msg)))

# Start Message Queue
open_pulsar.start()

input()
# Stop Message Queue
open_pulsar.stop()

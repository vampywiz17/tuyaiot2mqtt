import paho.mqtt.client as mqtt
import logging
from tuya_connector import TUYA_LOGGER, TuyaOpenPulsar, TuyaCloudPulsarTopic
 
# Konfig szakasz
 
ACCESS_ID = "" #Tuya IoT ID
ACCESS_KEY = "" #Tuya IoT key
BROKER_ADDRESS = "" #Saját mqtt szerver cím
TOPIC = ""  #Cél topic név
MQ_ENDPOINT = "wss://mqe.tuyaeu.com:8285/" #Tuya MQTT endpoint. Csak akkor kell megváltoztatni, ha nem EU régióban vagy regisztrálva!
CLIENT_NAME = "tuya_iot_client" #A kliens neve. Alapértelmezetten nem kell módosítani
 
#Eddig a pontig tart a konfig szakasz!!
 
client = mqtt.Client(CLIENT_NAME)
client.connect(BROKER_ADDRESS)
client.subscribe(TOPIC)
 
 
open_pulsar = TuyaOpenPulsar(
    ACCESS_ID, ACCESS_KEY, MQ_ENDPOINT, TuyaCloudPulsarTopic.PROD
)
 
open_pulsar.add_message_listener(lambda msg: client.publish("tuya_iot_topic",msg))
 
# Start Message Queue
open_pulsar.start()
 
input()
# Stop Message Queue
open_pulsar.stop()
 
client.loop_forever()

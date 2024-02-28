import os

from dotenv import load_dotenv
if os.path.exists('env/mqtt.env'):
    load_dotenv('env/mqtt.env', verbose=True, override=True)

MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))

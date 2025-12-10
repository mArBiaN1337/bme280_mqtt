import ujson # type: ignore
import machine
import bme280
import gc
import network
import urequests # type: ignore
import socket
import machine
import time  
from boot import Boot
from umqttsimple import MQTTClient
import ubinascii
# pyright: reportMissingImports=false

FILE_NAME = "bme_data.json"
MQTT_CONFIG = "config.json"

class BMELogger:
    def __init__(self):

        self.boot = Boot()

        self.get_mqtt_config()
        self.get_wifi_config()
        
        self.onboard_led = self.boot.onboard_led
        self.onboard_led.value(0)

        self.wtd = machine.WDT(timeout=20000)

        self.i2c = machine.I2C(0, scl=machine.Pin(22), sda=machine.Pin(21), freq=400000)
        self.bme = bme280.BME280(i2c=self.i2c)

        self.i2c = machine.I2C(scl=machine.Pin(22), sda=machine.Pin(21))
        self.bme = bme280.BME280(i2c=self.i2c)

    def sync_time_http(self):       
        url = "http://worldtimeapi.org/api/timezone/Europe/Amsterdam"
        try:
            # 1. Get JSON from the API
            response = urequests.get(url)
            data = response.json()
            response.close()
            
            # 2. Parse the datetime string (e.g., "2025-11-25T13:45:30.123456+01:00")
            # We manually parse the string to avoid Epoch (1970 vs 2000) math confusion
            dt_str = data['datetime']
            
            year = int(dt_str[0:4])
            month = int(dt_str[5:7])
            day = int(dt_str[8:10])
            hour = int(dt_str[11:13])
            minute = int(dt_str[14:16])
            second = int(dt_str[17:19])
            
            # We also need the weekday for the ESP32 RTC (0=Mon, 6=Sun)
            # The API gives strictly 1=Mon, 7=Sun, so we subtract 1
            weekday = data['day_of_week'] - 1 
            if weekday < 0: weekday = 6 

            # 3. Set the internal hardware clock (RTC)
            # Format: (year, month, day, weekday, hour, minute, second, subseconds)
            rtc = machine.RTC()
            rtc.datetime((year, month, day, weekday, hour, minute, second, 0))
                    
        except Exception as e:
            raise
    
    def get_mqtt_config(self):

        with open(MQTT_CONFIG, 'r') as f:
            config = ujson.load(f)

        self.LAST_MSG = 0
        self.MQTT_CLIENT_ID = ubinascii.hexlify(machine.unique_id())
        self.MQTT_SERVER = config["mqtt"]["broker_ip"]
        self.MQTT_USER = config["mqtt"]["username"]
        self.MQTT_PASSWORD = config["mqtt"]["password"]
        self.TOPIC_PUB = config["mqtt"]["topic_pub"].encode('utf-8')
        self.MSG_INTERVAL = config["mqtt"]["msg_interval"]
        self.QOS = config["mqtt"]["qos"]

    def get_wifi_config(self):
        
        with open('config.json', 'r') as f:
            config = ujson.load(f)
            
        self._net_ssid = config["network"]["ssid"]
        self._net_pass = config["network"]["password"]

    def connect_wifi(self):

        try:
            station = network.WLAN(network.STA_IF)
            if station.isconnected():
                self.ip = station.ifconfig()[0]
                return
            else:
                station.active(True)
                station.connect(self._net_ssid, self._net_pass)
                while not station.isconnected():
                    pass

                self.ip = station.ifconfig()[0]
                self.blink_onboard_led()

        except Exception as e:
            print('Failed to connect to WiFi:', e)
            raise

    def blink_onboard_led(self, times=3, interval=0.2):
        for _ in range(times):
            self.onboard_led.value(1)
            time.sleep(interval)
            self.onboard_led.value(0)
            time.sleep(interval)

    def collect_sensor_data(self):
        temp, press, hum = self.bme.values

        # strip units
        temp = temp.replace("C", "").strip()
        press = press.replace("hPa", "").strip()
        hum = hum.replace("%", "").strip()

        return (temp, press, hum)
    
    @staticmethod
    def get_weekday(weekday_number):
        weekdays = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        return weekdays[weekday_number]
    
    @staticmethod
    def get_month(month_number):
        months = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        return months[month_number - 1]

    @staticmethod
    def build_timestamp():
        timestamp = time.localtime()

        weekday = bme_logger.get_weekday(timestamp[6])
        day_number = timestamp[2]
        month = bme_logger.get_month(timestamp[1])
        year_number = timestamp[0]
        hour = timestamp[3]
        minute = timestamp[4]
        second = timestamp[5]

        timestamp = "{} {:02} {} {:04} {:02}-{:02}-{:02}".format(
            weekday,
            day_number,
            month,
            year_number,
            hour,
            minute,
            second,
        )

        return timestamp
    
    def build_json(self):

        temp, press, hum = self.collect_sensor_data()
        # format it like this: strftime("%A %d %B %Y %H-%M-%S")
        timestamp = bme_logger.build_timestamp()

        data = {
                "device_id": "ESP32-Marbian",
                "temperature": temp,     
                "pressure": press,
                "humidity": hum,
                "timestamp": timestamp
                }

        with open(FILE_NAME, "w") as f:
            ujson.dump(data, f)  # .dump() writes directly to the file

        time.sleep(0.1)
       
    def create_socket(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(False)

        self.sock.bind((self.ip, self.port))
        self.sock.listen(5)

        self.blink_onboard_led(times=3, interval=0.1)

    def mqtt_callback(self, topic, msg):
        print(f"Received message on topic {topic}: {msg}")

    def connect_mqtt(self):
        try:
            self.client = MQTTClient(self.MQTT_CLIENT_ID, self.MQTT_SERVER, user=self.MQTT_USER, password=self.MQTT_PASSWORD)
            self.client.connect()
            self.blink_onboard_led()

            return self.client 
        
        except Exception as e:
            raise

    def restart_reconnect(self):
        print('Failed to connect to MQTT broker. Reconnecting...')
        time.sleep(2)
        machine.reset()

if __name__ == "__main__":
    bme_logger = None
    mqtt_client = None
    try:
        bme_logger = BMELogger()
        bme_logger.connect_wifi()
        bme_logger.sync_time_http()

        try:
            mqtt_client = bme_logger.connect_mqtt()
        except OSError:
            bme_logger.restart_reconnect()

        while True:

            bme_logger.wtd.feed()
            try:
                mqtt_client.check_msg()
                if (time.time() - bme_logger.LAST_MSG) > bme_logger.MSG_INTERVAL:
                    
                    bme_logger.build_json()
                    with open(FILE_NAME, "rb") as f:
                        json_data = f.read()
                    
                    mqtt_client.publish(bme_logger.TOPIC_PUB, json_data)
                    bme_logger.LAST_MSG = time.time()
                    bme_logger.blink_onboard_led(times=1, interval=0.1)
            
            except OSError:
                bme_logger.restart_reconnect()

            gc.collect()
            time.sleep(1)

    except KeyboardInterrupt:
        print("BME Logger stopped")
        

    finally:
        if mqtt_client:
            mqtt_client.disconnect()

        if bme_logger:
            bme_logger.blink_onboard_led(times=10, interval=0.1) 
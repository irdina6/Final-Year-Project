import time
from datetime import datetime, timezone

import smbus2
import bme280
from influxdb import InfluxDBClient

# ===============================
# INFLUXDB v1 SETTINGS
# ===============================
USER = "insert_user"
PASSWORD = "insert_password"
DBNAME = "insert_dbname"
HOST = "insert_host"
PORT_DB = 8086

db = InfluxDBClient(host=HOST, port=PORT_DB, username=USER, password=PASSWORD, database=DBNAME)

NODE_ID = "pi3_node1"  # change if you want

# ===============================
# I2C BUS
# ===============================
bus = smbus2.SMBus(1)

# ===============================
# BME280 SETTINGS
# ===============================
BME280_ADDRESS = 0x76  # sometimes 0x77
calibration_params = bme280.load_calibration_params(bus, BME280_ADDRESS)

# ===============================
# BH1750 SETTINGS
# ===============================
BH1750_ADDRESS = 0x23  # sometimes 0x5C
CONTINUOUS_HIGH_RES_MODE = 0x10

def read_light_lux() -> float:
    data = bus.read_i2c_block_data(BH1750_ADDRESS, CONTINUOUS_HIGH_RES_MODE, 2)
    raw = (data[0] << 8) | data[1]
    return float(raw / 1.2)

def write_influx(temp_c: float, humidity: float, pressure_hpa: float, lux: float) -> bool:
    point = [{
        "measurement": "environment",
        "tags": {"nodeId": NODE_ID},
        "time": datetime.now(timezone.utc).isoformat(),
        "fields": {
            "temperature_c": float(temp_c),
            "humidity_pct": float(humidity),
            "pressure_hpa": float(pressure_hpa),
            "lux": float(lux),
        }
    }]
    return db.write_points(point)

def main():
    print("🌡️💡 BME280 + BH1750 → InfluxDB started (Ctrl+C to stop)")
    print(f"InfluxDB DB: {DBNAME} | measurement: environment | tag nodeId={NODE_ID}\n")

    # Put BH1750 into continuous mode
    try:
        bus.write_byte(BH1750_ADDRESS, CONTINUOUS_HIGH_RES_MODE)
        time.sleep(0.2)
    except Exception as e:
        print("⚠️ BH1750 init warning:", e)

    while True:
        try:
            bme = bme280.sample(bus, BME280_ADDRESS, calibration_params)
            lux = read_light_lux()

            temp_c = bme.temperature
            hum = bme.humidity
            pres = bme.pressure

            print(
                f"Temp: {temp_c:.2f} °C | Humidity: {hum:.2f} % | "
                f"Pressure: {pres:.2f} hPa | Lux: {lux:.2f}"
            )

            ok = write_influx(temp_c, hum, pres, lux)
            if not ok:
                print("❌ Influx write_points returned False")

            time.sleep(2)

        except KeyboardInterrupt:
            print("\n🛑 Stopped.")
            break
        except Exception as e:
            print("❌ Error:", e)
            time.sleep(2)

if __name__ == "__main__":
    main()

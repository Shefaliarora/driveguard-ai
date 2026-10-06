"""
DriveGuard AI - Vehicle Telemetry Simulator
Trigger: EventBridge schedule (e.g. rate(30 seconds))
Publishes fake CAN-bus-style telemetry for each VIN in VehicleFleet
to AWS IoT Core topic: vehicle/{vin}/telemetry

Roughly 1 in 8 publishes is a deliberately injected anomaly, so you
always have fresh incidents to show on camera.
"""

import json
import os
import random
import time
import boto3

FLEET_TABLE = os.environ.get("FLEET_TABLE", "VehicleFleet")
IOT_ENDPOINT = os.environ["IOT_ENDPOINT"]  # e.g. abc123-ats.iot.us-east-1.amazonaws.com

dynamodb = boto3.resource("dynamodb")
fleet_table = dynamodb.Table(FLEET_TABLE)
iot_data = boto3.client("iot-data", endpoint_url=f"https://{IOT_ENDPOINT}")

UNTRUSTED_OTA_SOURCES = ["unknown-update-mirror.net", "freefirmware-cdn.ru", "ota-mirror-77.cn"]


def get_fleet():
    resp = fleet_table.scan()
    return resp.get("Items", [])


def normal_reading(prev_speed):
    return {
        "speed": max(0, prev_speed + random.randint(-5, 5)),
        "rpm": random.randint(800, 3000),
        "battery_voltage": round(random.uniform(12.1, 12.8), 1),
        "dtc_codes": [],
        "failed_auth_attempts": 0,
    }


def inject_anomaly(reading, vehicle):
    anomaly_type = random.choice(["speed_spike", "dtc_spike", "auth_brute_force", "untrusted_ota"])

    if anomaly_type == "speed_spike":
        reading["prev_speed"] = reading["speed"]
        reading["speed"] = reading["speed"] + random.randint(80, 120)
        reading["dtc_codes"] = ["U0100", "P0A80"]
    elif anomaly_type == "dtc_spike":
        reading["dtc_codes"] = ["P0128", "P0A80", "U0100", "P0300"]
    elif anomaly_type == "auth_brute_force":
        reading["failed_auth_attempts"] = random.randint(4, 8)
    elif anomaly_type == "untrusted_ota":
        reading["ota_source"] = random.choice(UNTRUSTED_OTA_SOURCES)

    return reading, anomaly_type


def handler(event, context):
    fleet = get_fleet()
    published = []

    for vehicle in fleet:
        vin = vehicle["vin"]
        prev_speed = random.randint(20, 70)
        reading = normal_reading(prev_speed)
        reading.setdefault("prev_speed", prev_speed)
        reading.setdefault("ota_source", vehicle.get("trusted_ota_sources", ["unknown"])[0])

        anomaly_type = None
        if random.random() < 0.125:  # ~1 in 8
            reading, anomaly_type = inject_anomaly(reading, vehicle)

        payload = {
            "vin": vin,
            "timestamp": int(time.time()),
            "gps": {"lat": round(random.uniform(-60, 60), 4), "lon": round(random.uniform(-150, 150), 4)},
            **reading,
        }

        topic = f"vehicle/{vin}/telemetry"
        iot_data.publish(topic=topic, qos=0, payload=json.dumps(payload))
        published.append({"vin": vin, "topic": topic, "anomaly_injected": anomaly_type})

    return {"statusCode": 200, "body": json.dumps({"published": published})}

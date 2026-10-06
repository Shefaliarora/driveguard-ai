"""
DriveGuard AI - Anomaly Detector
Trigger: Kinesis Data Stream (vehicle-telemetry-stream)
Applies simple rule-based thresholds to each telemetry record.
On a match, writes a new item to the Incidents DynamoDB table
(status: NEW) which fires the Incident Analyst Lambda via a
DynamoDB Stream.

Swap this logic for Amazon Lookout for Metrics if you want a
managed-ML anomaly detection story instead of rules.
"""

import base64
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

INCIDENTS_TABLE = os.environ.get("INCIDENTS_TABLE", "Incidents")
FLEET_TABLE = os.environ.get("FLEET_TABLE", "VehicleFleet")

dynamodb = boto3.resource("dynamodb")
incidents_table = dynamodb.Table(INCIDENTS_TABLE)
fleet_table = dynamodb.Table(FLEET_TABLE)

SPEED_DELTA_THRESHOLD = 60
DTC_COUNT_THRESHOLD = 2
FAILED_AUTH_THRESHOLD = 3


def trusted_ota_sources(vin):
    resp = fleet_table.get_item(Key={"vin": vin})
    item = resp.get("Item", {})
    return set(item.get("trusted_ota_sources", []))


def evaluate(reading):
    speed = reading.get("speed", 0)
    prev_speed = reading.get("prev_speed", speed)
    dtc_codes = reading.get("dtc_codes", [])
    failed_auth = reading.get("failed_auth_attempts", 0)
    ota_source = reading.get("ota_source")

    if abs(speed - prev_speed) >= SPEED_DELTA_THRESHOLD:
        return "SPEED_DELTA_EXCEEDED"
    if len(dtc_codes) > DTC_COUNT_THRESHOLD:
        return "DTC_THRESHOLD"
    if failed_auth >= FAILED_AUTH_THRESHOLD:
        return "FAILED_AUTH_THRESHOLD"
    if ota_source and ota_source not in trusted_ota_sources(reading.get("vin")):
        return "UNTRUSTED_OTA_SOURCE"
    if len(dtc_codes) == 1:
        return "DTC_THRESHOLD"  # low-severity, still worth a triage pass
    return None


def handler(event, context):
    created = []

    for record in event["Records"]:
        payload = base64.b64decode(record["kinesis"]["data"])
        reading = json.loads(payload)

        triggered_rule = evaluate(reading)
        if not triggered_rule:
            continue

        incident_id = f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        item = {
            "incident_id": incident_id,
            "vin": reading.get("vin"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "NEW",
            "raw_signal": reading,
            "triggered_rule": triggered_rule,
        }
        incidents_table.put_item(Item=item)
        created.append(incident_id)

    return {"statusCode": 200, "body": json.dumps({"incidents_created": created})}

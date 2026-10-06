"""
DriveGuard AI - Read API
Trigger: API Gateway (GET /incidents)
Returns the most recent triaged incidents for the SOC dashboard,
newest first. CORS headers included so the S3/CloudFront-hosted
frontend can call this directly from the browser.
"""

import json
import os
import boto3
from boto3.dynamodb.conditions import Attr

INCIDENTS_TABLE = os.environ.get("INCIDENTS_TABLE", "Incidents")
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "50"))

dynamodb = boto3.resource("dynamodb")
incidents_table = dynamodb.Table(INCIDENTS_TABLE)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}


def handler(event, context):
    resp = incidents_table.scan(FilterExpression=Attr("status").eq("TRIAGED"))
    items = resp.get("Items", [])

    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    items = items[:MAX_ITEMS]

    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps(items, default=str),
    }

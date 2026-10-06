"""
DriveGuard AI - Incident Analyst (the GenAI centerpiece)
Trigger: DynamoDB Stream on Incidents table, filtered to INSERT
events where status == "NEW"

Calls Amazon Bedrock (Claude) to triage the raw anomaly into a
severity rating, likely cause, plain-English explanation, and a
recommended action. Updates the Incidents record with the result.
If severity is Critical, publishes to an SNS topic.
"""

import json
import os
import boto3
from boto3.dynamodb.types import TypeDeserializer

INCIDENTS_TABLE = os.environ.get("INCIDENTS_TABLE", "Incidents")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6-v1:0")

dynamodb = boto3.resource("dynamodb")
incidents_table = dynamodb.Table(INCIDENTS_TABLE)
bedrock = boto3.client("bedrock-runtime")
sns = boto3.client("sns")
deserializer = TypeDeserializer()

SYSTEM_PROMPT = """You are a SOC analyst for connected-vehicle cybersecurity.
Given a raw telemetry anomaly from a vehicle, respond with ONLY a JSON object
(no markdown, no commentary) with exactly these fields:
{
  "severity": "Low" | "Medium" | "High" | "Critical",
  "likely_cause": "short label, e.g. CAN bus injection, GPS spoofing, unauthorized OTA, sensor fault",
  "explanation": "2-3 plain-English sentences a non-technical manager could understand",
  "recommended_action": "one concrete next step for the SOC team"
}"""


def deserialize_dynamo_item(dynamo_item):
    return {k: deserializer.deserialize(v) for k, v in dynamo_item.items()}


def call_bedrock(raw_signal, triggered_rule):
    user_message = (
        f"Triggered rule: {triggered_rule}\n"
        f"Raw telemetry: {json.dumps(raw_signal, default=str)}"
    )

    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0.2},
    )

    text = response["output"]["message"]["content"][0]["text"]
    return json.loads(text)


def handler(event, context):
    updated = []

    for record in event["Records"]:
        if record["eventName"] != "INSERT":
            continue

        new_image = record["dynamodb"]["NewImage"]
        item = deserialize_dynamo_item(new_image)

        if item.get("status") != "NEW":
            continue

        triage = call_bedrock(item["raw_signal"], item["triggered_rule"])

        incidents_table.update_item(
            Key={"incident_id": item["incident_id"]},
            UpdateExpression=(
                "SET #s = :status, severity = :sev, likely_cause = :cause, "
                "explanation = :expl, recommended_action = :action"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "TRIAGED",
                ":sev": triage["severity"],
                ":cause": triage["likely_cause"],
                ":expl": triage["explanation"],
                ":action": triage["recommended_action"],
            },
        )
        updated.append(item["incident_id"])

        if triage["severity"] == "Critical" and SNS_TOPIC_ARN:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"CRITICAL: {item['vin']} - {triage['likely_cause']}",
                Message=(
                    f"Incident: {item['incident_id']}\n"
                    f"VIN: {item['vin']}\n"
                    f"Cause: {triage['likely_cause']}\n\n"
                    f"{triage['explanation']}\n\n"
                    f"Recommended action: {triage['recommended_action']}"
                ),
            )

    return {"statusCode": 200, "body": json.dumps({"triaged": updated})}

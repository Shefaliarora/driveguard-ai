# DriveGuard AI — Real-Time Automotive Cybersecurity Copilot on AWS

**The hook for your video:** Connected cars stream thousands of signals a minute (CAN-bus diagnostics, OTA update status, GPS, access attempts). A SOC analyst can't read raw telemetry fast enough to tell "bad sensor" from "active attack." This demo builds an AI copilot that detects anomalies in simulated vehicle telemetry and uses Amazon Bedrock to turn them into a plain-English, triaged incident report in seconds.

## Business scenario

An OEM's Security Operations Center monitors a fleet for signs of compromise — CAN-bus injection, GPS spoofing, unauthorized ECU access, malicious OTA pushes. Today that's rule-based alerting with no context, so analysts drown in noise. **DriveGuard AI** adds a GenAI triage layer: every anomaly gets an automatic severity rating, a human-readable explanation, and a recommended action — before a human ever looks at it.

## Architecture

```
Vehicle Simulator (Lambda, scheduled)
        │  publishes JSON telemetry
        ▼
   AWS IoT Core  ──(IoT Rule)──▶  Kinesis Data Stream
        │                              │
        │                              ▼
        │                    Lambda: Anomaly Detector
        │                    (rule-based scoring, or
        │                     Lookout for Metrics)
        │                              │ anomaly found
        │                              ▼
        │                    DynamoDB: Incidents (raw)
        │                              │
        │                              ▼
        │                    Lambda: Incident Analyst
        │                    → Amazon Bedrock (Claude)
        │                    "You are an automotive SOC
        │                     analyst. Triage this signal."
        │                              │ severity + explanation
        │                              ▼
        │                    DynamoDB: Incidents (enriched)
        │                              │
        │                              ▼
        │                 API Gateway + Lambda (read API)
        │                              │
        │                              ▼
        │                 S3 + CloudFront: SOC Dashboard
        └──── Critical severity ──▶ SNS → email/Slack
```

**Services used:** AWS IoT Core, Kinesis Data Streams, Lambda, Amazon Bedrock (Claude), DynamoDB, API Gateway, S3 + CloudFront, SNS. All serverless — no idle cost between demos, and it's a single AWS account, no real vehicle hardware needed.

A full AWS-icon version of this diagram is included as `architecture-diagram.png`.

## What's in this package
| File | Purpose |
|---|---|
| `architecture-diagram.png` | The diagram above, drawn with official AWS service icons |
| `sample_data_vehiclefleet.json` | 8 fake VINs to seed the `VehicleFleet` table |
| `sample_data_incidents.json` | 4 example triaged incidents (High/Critical/Low) to seed `Incidents` or use as a fallback if Bedrock is slow on camera |
| `lambda_simulator.py` | Publishes fake telemetry to IoT Core, injects anomalies ~1 in 8 runs |
| `lambda_anomaly_detector.py` | Kinesis-triggered rule-based detector, writes to `Incidents` |
| `lambda_incident_analyst.py` | DynamoDB-Stream-triggered, calls Bedrock, writes severity/explanation back |
| `lambda_read_api.py` | API Gateway handler that serves triaged incidents to the dashboard |

## Prerequisites
- AWS account with Bedrock model access enabled for a Claude model (Bedrock → Model access).
- Comfortable with Lambda, IAM roles, and S3 static hosting (you've taught all of these).
- ~3–4 hours to build end-to-end; budget a separate pass for polish before recording.

## Step-by-step build (console)

### Part A — Tables and fleet
1. **DynamoDB → Create table** `VehicleFleet`, partition key `vin` (String). Open the table → Explore items → Create item → paste each record from `sample_data_vehiclefleet.json` one at a time (8 items).
2. **DynamoDB → Create table** `Incidents`, partition key `incident_id` (String). On the table, enable **DynamoDB Streams** (view type: New image) — needed in Part C.
3. **IAM → Create role** `driveguard-lambda-role`, trusted entity Lambda, attach `AWSLambdaBasicExecutionRole` plus inline permissions for: `dynamodb:*` on both tables, `iot:Publish`, `kinesis:GetRecords/GetShardIterator/DescribeStream/ListStreams`, `bedrock:InvokeModel`/`bedrock:Converse`, `sns:Publish`, `dynamodb:GetStreamRecords`/`DescribeStream`/`GetShardIterator`/`ListStreams` (DynamoDB Streams).
4. **Bedrock → Model access** → request access to a Claude model (e.g. Claude Sonnet) if not already enabled. Note the model ID for later.

### Part B — Ingest and simulate
5. **IoT Core → Settings** → copy your account's IoT **endpoint** (you'll need it as a Lambda env var).
6. **Kinesis → Create data stream** `vehicle-telemetry-stream`, on-demand capacity mode.
7. **IoT Core → Message routing → Rules → Create rule**: SQL `SELECT * FROM 'vehicle/+/telemetry'`, action = send to Kinesis stream `vehicle-telemetry-stream`, use the IAM role IoT Core offers to create.
8. **Lambda → Create function** `driveguard-simulator`, Python 3.12, attach `driveguard-lambda-role`. Paste in `lambda_simulator.py`. Environment variables: `IOT_ENDPOINT` (from step 5), `FLEET_TABLE=VehicleFleet`.
9. **EventBridge → Create rule**, schedule expression `rate(30 seconds)`, target = `driveguard-simulator` Lambda. This is your "fleet heartbeat" for the demo.

### Part C — Detect, then triage with Bedrock
10. **Lambda → Create function** `driveguard-anomaly-detector`, same role. Paste `lambda_anomaly_detector.py`. Env vars: `INCIDENTS_TABLE=Incidents`, `FLEET_TABLE=VehicleFleet`. Add trigger: Kinesis, stream = `vehicle-telemetry-stream`, batch size 10.
11. **Lambda → Create function** `driveguard-incident-analyst`, same role. Paste `lambda_incident_analyst.py`. Env vars: `INCIDENTS_TABLE=Incidents`, `BEDROCK_MODEL_ID` (your model ID from step 4), `SNS_TOPIC_ARN` (create an **SNS** topic `driveguard-critical-alerts` first, subscribe your email, confirm the subscription). Add trigger: DynamoDB, table = `Incidents`, batch size 1, starting position = Latest.

### Part D — Dashboard
12. **Lambda → Create function** `driveguard-read-api`, same role. Paste `lambda_read_api.py`. Env var: `INCIDENTS_TABLE=Incidents`.
13. **API Gateway → Create REST API** → resource `/incidents`, method `GET`, integration = Lambda `driveguard-read-api`. Enable CORS on the resource. Deploy to a stage (e.g. `prod`) and copy the invoke URL.
14. **Frontend:** a single static `index.html` with a `fetch()` call to your `/incidents` URL every 5 seconds — render each item as a card with a severity-colored badge (Critical=red, High=orange, Medium=yellow, Low=gray), the `explanation`, and `recommended_action`. Keep it to one file, no build step.
15. **S3 → Create bucket**, enable **static website hosting**, upload `index.html`, make the bucket/object public (or front it with **CloudFront** for a clean HTTPS URL — nicer for recording).

### Dry run before recording
Manually invoke `driveguard-simulator` 3–4 times from the Lambda console ("Test" with an empty event) to generate traffic quickly, then check the `Incidents` table fills in and `status` flips `NEW → TRIAGED` within a few seconds. If Bedrock is rate-limited or slow mid-recording, you can fall back to pre-loading `sample_data_incidents.json` directly into the table as a safety net.

## Recording the demo
1. Open the dashboard, let it sit on "all clear" (or showing only Low-severity items).
2. Manually invoke the simulator Lambda on camera (or just wait ~30s for the EventBridge schedule) so an anomaly fires — narrate the raw signal as it appears in the Lambda logs or DynamoDB console.
3. Switch to the dashboard: watch the incident enrich itself in real time — raw alert → Bedrock-generated severity, explanation, and recommended action, color-coded.
4. Trigger a Critical case (failed-auth or untrusted-OTA anomaly) and show the SNS email landing in your inbox.
5. Close on the architecture diagram (`architecture-diagram.png`) and the one-line business value: "SOC analysts go from raw telemetry to a triaged, explained incident in seconds, not hours."

## LinkedIn framing
Lead with the business pain (alert fatigue in connected-vehicle SOCs), not the service list. Name the services in a caption line, not the hook. As an AWS Community Builder, tagging **#AWSCommunityBuilders** and mentioning Bedrock + Claude ties your AWS and Claude Partner Network angles together naturally.

---
*Cost note: everything here is serverless and pay-per-use — a few demo runs will cost cents. Remember to tear down IoT Rules, Kinesis stream, and CloudFront after recording if you won't reuse them.*

# PotchPulse: Smart Water Infrastructure Monitoring (SWIM)

A real-time streaming data platform on **Microsoft Azure** that monitors a city's water network. Simulated pressure sensors, a maintenance truck and citizen social-media reports stream through **Event Hubs** into **Stream Analytics**, are stored in **Cosmos DB**, are enriched with **AI sentiment analysis** by an **Azure Function**, and are visualised live in **Power BI** (DirectQuery).

The scenario is based on real news of pipe bursts and water outages in Potchefstroom, South Africa.

![Architecture](docs/images/architecture.png)

## What it does

1. **Simulation** ([`generator/potch_generator.py`](generator/potch_generator.py)). Four cooperating threads share one in-memory "city state":
   - **IoT sensors:** 15 pressure sensors across 5 suburbs. A pressure drop flags a `POSSIBLE_INCIDENT` with a physics-based confidence score (capped at 0.70). A hidden "ground truth" decides whether the pipe is actually broken, so false alarms happen too.
   - **Social media:** citizens report an incident 0–10 times, choosing random negative, neutral or positive messages. A reported incident becomes `REPORTED_INCIDENT`.
   - **Fleet:** a municipal truck is dispatched to the highest-confidence incident, drives there, and then either fixes the pipe or records a false alarm (`incident_resolution`).
   - **DB sync:** reads back the confidence scores the Azure Function updated, closing the feedback loop for dispatch decisions.
2. **Ingestion:** three Event Hubs (`iot`, `social`, `fleet`), all events carrying timestamps for correct event-time ordering.
3. **Stream processing** ([`stream-analytics/query.sql`](stream-analytics/query.sql)). One Stream Analytics job:
   - sends social reports to the AI bridge function;
   - upserts sensor telemetry and fleet positions;
   - routes `incident_resolution` events to incident history;
   - merges sensors and trucks into one map-ready container (CTE `CombinedMapData`).
4. **AI enrichment** ([`function/function_app.py`](function/function_app.py)). An HTTP-triggered Azure Function calls the **Azure AI Language** sentiment API for each report:
   - it raises the sensor's confidence score for negative reports and lowers it for positive ones;
   - it counts the reports;
   - it stores each report with its sentiment.
5. **Storage:** Cosmos DB (NoSQL), with six containers whose partition keys are chosen per access pattern: `SensorMaster`, `SensorTelemetry`, `Fleet`, `IncidentHistory`, `SocialMediaReports` and `UnifiedLocations`.
6. **Analytics:** a three-page Power BI report over Cosmos DB with DirectQuery ([`powerbi/`](powerbi/)):
   - a live command-centre map;
   - incident trends over time;
   - a correlation of citizen reports with incident outcomes.

## Dashboard

| Command centre (live map) | Incident tracking | Citizen report correlation |
|---|---|---|
| ![Command centre](docs/images/dashboard-command-center.png) | ![Incident tracking](docs/images/dashboard-incident-tracking.png) | ![Citizen reports](docs/images/dashboard-citizen-reports.png) |

## Tech stack

| Layer | Azure service / technology |
|---|---|
| Data generation | Python 3 (threads), `azure-eventhub`, `azure-cosmos`; ran on an Azure VM |
| Ingestion | Azure Event Hubs (3 hubs) |
| Stream processing | Azure Stream Analytics (SQL-like query, event-time with `TIMESTAMP BY`) |
| AI | Azure AI Language (Microsoft Foundry) sentiment analysis, called from an Azure Function (Python, HTTP trigger) |
| Storage | Azure Cosmos DB for NoSQL |
| Visualisation | Power BI Desktop, DirectQuery via the Cosmos DB v2 connector |

## Repository layout

```text
generator/            simulator (potch_generator.py), sensor master data, historical-data generator
  data/               generated historical incidents and social reports (for backfilling the dashboards)
function/             Azure Function "AI bridge" (sentiment -> confidence score)
stream-analytics/     Stream Analytics job query
powerbi/              Power BI report (.pbix)
docs/                 full project write-up (REPORT.md) and images
```

## Running it

Running it needs your own Azure resources. The original ones were on an Azure for Students subscription in Italy North and no longer exist.

1. **Create the resources:**
   - an Event Hubs namespace with hubs `evh-potchiot-…`, `evh-potchsocial-…` and `evh-potchfleet-…`;
   - a Cosmos DB for NoSQL account;
   - an Azure AI Language resource;
   - a Function App (Python);
   - a Stream Analytics job.
2. **Function:** deploy [`function/`](function/) (for example with the VS Code Azure Functions extension). Copy `local.settings.example.json` to `local.settings.json` for local runs, and set the same values as application settings in Azure:
   - `AI_ENDPOINT`, `AI_KEY`;
   - `COSMOS_URI`, `COSMOS_KEY`, `COSMOS_DB`.
3. **Stream Analytics:**
   - Add the inputs `iot-stream`, `social-stream` and `fleet-stream` (Event Hubs).
   - Add the outputs `out-aibridge` (Azure Function), `out-cosmos-sensortelemetry`, `out-cosmos-fleet`, `out-cosmos-incidenthistory` and `out-cosmos-unifiedlocations` (Cosmos DB). The partition keys are listed in [docs/REPORT.md](docs/REPORT.md#schema-design).
   - Paste [`stream-analytics/query.sql`](stream-analytics/query.sql) and start the job.
4. **Generator:**

   ```bash
   cd generator
   pip install -r requirements.txt
   cp .env.example .env        # then replace the placeholder values with your own
   python potch_generator.py
   ```

   On first run it creates the Cosmos DB database and containers.
5. **Power BI:** open [`powerbi/PotchPulse-Dashboard.pbix`](powerbi/) and point the Cosmos DB connection at your account.

Secrets are read from environment variables or a local `.env` file only. Both `.env` and `local.settings.json` are git-ignored. The keys in the `.example` files are random placeholders in the right format, not real credentials.

## Documentation

[docs/REPORT.md](docs/REPORT.md) is the full write-up. It covers:

- the problem definition and data-generation realism;
- the UML architecture;
- the Cosmos DB schema and partition-key choices;
- the Event Hubs design;
- the Stream Analytics query, inputs and outputs;
- the Power BI insights;
- observability, performance and cost.

Built for ITRI613 (Databases I).

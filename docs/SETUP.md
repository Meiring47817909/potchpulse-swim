# Setup guide

This guide rebuilds PotchPulse SWIM in your own Azure subscription, from an empty resource group to a live Power BI dashboard. Every step uses the Azure portal. Expect about 1–2 hours the first time.

> **Order matters.** The generator creates the Cosmos DB database and containers, and a Stream Analytics job with Cosmos DB outputs fails to start if those containers don't exist yet. Follow the steps in order.

## What you will create

Put everything in **one resource group and one region**. The original used Italy North because an Azure for Students policy limited the available regions. Any region that offers all five services works.

| # | Resource | Example name | Tier / plan | Used for |
|---|---|---|---|---|
| 1 | Resource group | `rg-potchpulse-dev` | – | Holds everything; deleting it cleans up |
| 2 | Azure Cosmos DB for NoSQL account | `cosmos-potchpulse-dev` | **Serverless** (recommended) | Database `PotchSmartCity` with 6 containers |
| 3 | Event Hubs namespace + 3 event hubs | `evhns-potchpulse-dev` | Standard, 1 throughput unit | Ingests IoT, social and fleet events |
| 4 | Azure AI Language resource | `lang-potchpulse-dev` | Free (F0) | Sentiment analysis of citizen reports |
| 5 | Function App (Python) + its Storage account | `func-potchpulse-aibridge` | Flex Consumption or Consumption, Linux, Python 3.11 | The "AI bridge" between Stream Analytics and AI Language |
| 6 | Stream Analytics job | `asa-potchpulse-dev` | Smallest streaming unit size | Routes and transforms the streams into Cosmos DB and the function |

You also need these on your computer:

- Python 3.10+
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local) (or VS Code with the Azure Functions extension)
- [Power BI Desktop](https://powerbi.microsoft.com/desktop/) (Windows)

> **Cost:** Stream Analytics is billed per streaming-unit hour **while the job is running**, and it was the most expensive part of the original deployment. Stop the job when you aren't demoing, and delete the resource group when you're done (step 10).

---

## 1. Resource group

Portal → **Resource groups** → **Create**. Pick your subscription, a name (e.g. `rg-potchpulse-dev`) and the region you'll use for everything.

## 2. Cosmos DB

1. Portal → **Create a resource** → **Azure Cosmos DB** → **Azure Cosmos DB for NoSQL**.
2. Use the resource group and region from step 1, and set **Capacity mode** to **Serverless**.

   *Why serverless:* the generator creates six containers, and with provisioned throughput each one gets its own 400 RU/s. That is more than the free tier covers.
3. When it's deployed, open **Settings → Keys** and note the **URI** and **PRIMARY KEY**.

Don't create the database or containers yourself; the generator does that in step 4. For reference, these are the containers it creates:

| Container | Partition key | Written by |
|---|---|---|
| `SensorMaster` | `/suburb` | Generator (static sensor list, seeded once) |
| `SensorTelemetry` | `/id` | Stream Analytics (`out-cosmos-sensortelemetry`) and the function (confidence score) |
| `Fleet` | `/id` | Stream Analytics (`out-cosmos-fleet`) |
| `IncidentHistory` | `/sensor_id` | Stream Analytics (`out-cosmos-incidenthistory`), history loader |
| `SocialMediaReports` | `/suburb` | The function, history loader |
| `UnifiedLocations` | `/id` | Stream Analytics (`out-cosmos-unifiedlocations`), feeds the Power BI map |

## 3. Event Hubs

1. Portal → **Create a resource** → **Event Hubs** → create a **namespace** (tier **Standard**, 1 throughput unit) in your resource group and region.
2. In the namespace → **Event Hubs** → **+ Event Hub**, create three hubs. **Partition count 1** and **retention 1 hour** are enough; these match the original.
   - `evh-potchiot-dev-italynorth`
   - `evh-potchsocial-dev-italynorth`
   - `evh-potchfleet-dev-italynorth`

   You can use other names. If you do, set `HUB_IOT`, `HUB_SOCIAL` and `HUB_FLEET` in `.env` to match.
3. Namespace → **Settings → Shared access policies** → **+ Add**, and create two policies:
   - `PythonGeneratorKey` with **Send** only. Copy its **Connection string–primary key**; the generator uses it.
   - `asa-listen` with **Listen** only, for Stream Analytics in step 7.

## 4. Configure the generator and initialise Cosmos DB

```bash
cd generator
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Edit `.env` and replace the placeholder values:

| Variable | Value |
|---|---|
| `EVENTHUB_CONNECTION_STR` | Connection string of the `PythonGeneratorKey` policy (step 3) |
| `HUB_IOT`, `HUB_SOCIAL`, `HUB_FLEET` | Your event hub names, if you changed them |
| `COSMOS_URI`, `COSMOS_KEY` | From Cosmos DB → Keys (step 2) |
| `COSMOS_DB` | Keep `PotchSmartCity` |

Then create the database, the six containers and the 15 sensors, without sending any events:

```bash
python potch_generator.py --init-only
```

In Cosmos DB → **Data Explorer**, `PotchSmartCity` should now have six containers. `SensorMaster` and `SensorTelemetry` should each hold 15 items.

**Optional, for the trend pages in Power BI:** load about six weeks of historical incidents and citizen reports (225 incidents and 525 reports, dated April–May 2026).

```bash
python load_history.py
```

To generate a fresh random history first, run `python generate_history.py`; it overwrites the files in `data/`.

## 5. Azure AI Language

1. Portal → **Create a resource** → search **Language service** → **Create**. Skip the custom features and select **Continue to create your resource**.
2. Use the same resource group and region, and pricing tier **Free F0** (5,000 text records a month).
3. Open **Resource Management → Keys and Endpoint** and note **KEY 1** and the **Endpoint** (`https://<name>.cognitiveservices.azure.com/`).

## 6. Function App (AI bridge)

The function is an HTTP-triggered Python function, `AnalyzePotchSentiment`, in [`function/function_app.py`](../function/function_app.py). For each batch of citizen reports it:

- calls AI Language for the sentiment;
- saves the report in `SocialMediaReports`;
- adjusts the sensor's `confidence_score` in `SensorTelemetry` (+0.10 negative, +0.05 neutral, −0.05 positive) and increments `social_reports_count`.

### 6a. Create it

Portal → **Create a resource** → **Function App**:

- **Hosting plan:** Flex Consumption (or Consumption)
- **Runtime stack:** Python, version **3.11**
- **Operating system:** Linux
- **Region:** the same one as everything else

The wizard also creates the Storage account that every Function App needs. Keep the defaults for the rest.

### 6b. Application settings

Function App → **Settings → Environment variables** → **App settings**. Add these settings, then **Apply**:

| Name | Value |
|---|---|
| `AI_ENDPOINT` | Language endpoint (step 5), e.g. `https://lang-potchpulse-dev.cognitiveservices.azure.com` |
| `AI_KEY` | Language KEY 1 |
| `COSMOS_URI` | Cosmos DB URI |
| `COSMOS_KEY` | Cosmos DB primary key |
| `COSMOS_DB` | `PotchSmartCity` |

### 6c. Deploy

```bash
cd function
func azure functionapp publish <your-function-app-name>
```

You can deploy from VS Code instead: Azure Functions extension → **Deploy to Function App**. Afterwards, `AnalyzePotchSentiment` should appear under the Function App's **Functions** list.

### 6d. Test it (optional, recommended)

In the portal, open the function → **Get function URL** (default key), then send a test report:

```bash
curl -X POST "<function-url>" -H "Content-Type: application/json" \
  -d '[{"report_id":"test-1","report_time":"2026-01-01T00:00:00Z","suburb":"The Bult","sensor_id":"SENS-BULT-01","citizen_report":"Huge pipe burst, the whole street is flooded!"}]'
```

The response should include `"ai_sentiment": "negative"`, and `SENS-BULT-01` in `SensorTelemetry` should have a higher `confidence_score` and `social_reports_count`. Delete the `test-1` item from `SocialMediaReports` afterwards if you like.

**Running it locally** instead: copy `local.settings.example.json` to `local.settings.json`, fill in the same values, and run `func start`. The URL is then `http://localhost:7071/api/AnalyzePotchSentiment`, and it needs Azurite for `AzureWebJobsStorage`.

## 7. Stream Analytics job

1. Portal → **Create a resource** → **Stream Analytics job**, in the same resource group and region, with the smallest streaming unit size. Hosting environment: **Cloud**.
2. **Job topology → Inputs → + Add input → Event Hub**. Create three inputs. **The alias names must match exactly**, because the query uses them:

   | Input alias | Event hub |
   |---|---|
   | `iot-stream` | `evh-potchiot-…` |
   | `social-stream` | `evh-potchsocial-…` |
   | `fleet-stream` | `evh-potchfleet-…` |

   For each input:
   - choose your namespace, and consumer group **Use existing → `$Default`**;
   - set authentication to **Connection string**, with policy **Use existing → `asa-listen`**;
   - set the serialization to **JSON** and the encoding to **UTF-8**.
3. **Job topology → Outputs**. Create five outputs.

   For each **Cosmos DB** output:
   - choose your account and database `PotchSmartCity`;
   - set authentication to **Connection string**;
   - **set Document id to `id`**, so the outputs upsert (update in place) rather than add duplicates.

   | Output alias | Type | Container / function |
   |---|---|---|
   | `out-aibridge` | Azure Function | Function App from step 6 → `AnalyzePotchSentiment` |
   | `out-cosmos-sensortelemetry` | Cosmos DB | `SensorTelemetry` |
   | `out-cosmos-fleet` | Cosmos DB | `Fleet` |
   | `out-cosmos-incidenthistory` | Cosmos DB | `IncidentHistory` |
   | `out-cosmos-unifiedlocations` | Cosmos DB | `UnifiedLocations` |

   For `out-aibridge`, the job fetches the function key itself and you can keep the default batch settings. If your Function App isn't listed, choose **Provide Azure Function settings manually** and enter the app name, the function name `AnalyzePotchSentiment` and the default function key.
4. **Job topology → Query.** Paste the whole of [`stream-analytics/query.sql`](../stream-analytics/query.sql) and **Save query**. The `WITH CombinedMapData` clause must stay at the top.
5. Optional: start the generator (step 8) for a minute, then use **Test query** to check that each input produces rows.
6. **Overview → Start job → Now.**

## 8. Run the simulation

```bash
cd generator
python potch_generator.py        # Ctrl+C to stop
```

The console shows sensor readings, incidents, social reports and the truck moving. The original ran it around the clock on a small Azure Linux VM. Your own computer is fine for a demo.

Check the data is flowing:

- **Stream Analytics → Overview / Metrics:** input and output events rise, and there are no runtime errors.
- **Function App → the function → Monitor / Logs:** invocations appear, with log lines such as `Updated sensor … confidence to …`.
- **Cosmos DB → Data Explorer:**
  - `UnifiedLocations` has 15 sensors plus `TRUCK-01` and `JB-Marks`;
  - `Fleet` updates every 5 seconds;
  - `SocialMediaReports` and `IncidentHistory` grow as incidents happen and get resolved.

## 9. Power BI

1. Open [`powerbi/PotchPulse-Dashboard.pbix`](../powerbi/) in Power BI Desktop.
2. **Home → Transform data → Data source settings**. Select the Cosmos DB source → **Change Source**, and enter your account endpoint (the Cosmos DB URI).
3. **Edit Permissions → Credentials → Edit**, and enter your Cosmos DB **account key**.
4. Click **Apply changes**, then **Refresh**. The report uses **DirectQuery** through the **Azure Cosmos DB v2** connector, so the visuals read live data.

To build your own report instead: **Get data → Azure Cosmos DB v2** → endpoint → **DirectQuery** → pick the database and containers.

## 10. Stop and clean up

- **Stop the Stream Analytics job** whenever you aren't using it; it costs money while it runs.
- To remove everything: Portal → your resource group → **Delete resource group**.
- If you rotate or delete keys, update `.env`, the Function App settings, the Stream Analytics inputs and outputs, and the Power BI credentials.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `KeyError: 'EVENTHUB_CONNECTION_STR'` (or `COSMOS_URI`) | `.env` is missing or not in `generator/`, or `python-dotenv` isn't installed. Run `pip install -r requirements.txt`, or set the variables in your shell. |
| Stream Analytics fails to start or shows a Cosmos DB output error | The containers don't exist yet: run `python potch_generator.py --init-only`. Also check the database name and the Document id `id`. |
| Duplicate documents instead of updates | A Cosmos DB output has no Document id. Set it to `id` (step 7.3). |
| Stream Analytics input deserialization errors | The input serialization isn't JSON, or the alias names don't match the query. |
| Every report's sentiment is `neutral` | The function couldn't reach AI Language and fell back to neutral. Check `AI_ENDPOINT` and `AI_KEY`, and look for `AI Sentiment call failed` in the function logs. |
| Function logs show `Sensor … not found in DB` | `SensorTelemetry` wasn't initialised. Run the generator with `--init-only`. |
| Function returns 500 `Bridge Error` | An app setting is missing or wrong (step 6b), or `SocialMediaReports` doesn't exist. |
| `out-aibridge` errors in Stream Analytics | The function isn't deployed, the key changed, or the function is failing. Check its logs. |
| Power BI map is empty | `UnifiedLocations` is empty, which means the job isn't running or the generator isn't sending. Check step 8. |

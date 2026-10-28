-- Note: Azure Stream Analytics requires the WITH clause to be the FIRST statement.
WITH CombinedMapData AS (
    -- Send Sensors to the Unified Map Container
    SELECT
        CAST(id AS nvarchar(max)) AS id,
        CAST('Sensor' AS nvarchar(max)) AS Category,
        CAST(address AS nvarchar(max)) AS location_name,
        CAST(
            CASE 
                WHEN status = 'POSSIBLE_INCIDENT' THEN 'Sensor - Possible Incident'
                WHEN status = 'REPORTED_INCIDENT' THEN 'Sensor - Reported Incident'
                WHEN status = 'OK' THEN 'Sensor - Normal'
                WHEN status = 'FIXING' THEN 'Sensor - Fixing'
                ELSE CONCAT('Sensor - ', CAST(status AS nvarchar(max)))
            END 
        AS nvarchar(max)) AS Legend_Category,
        CAST(suburb AS nvarchar(max)) AS suburb,
        CAST(latest_pressure AS float) AS latest_pressure,
        CAST(lat AS float) AS lat,
        CAST(lon AS float) AS lon,
        CAST(
            CASE 
                WHEN status = 'POSSIBLE_INCIDENT' THEN 'Possible Incident'
                WHEN status = 'REPORTED_INCIDENT' THEN 'Reported Incident'
                WHEN status = 'OK' THEN 'Normal'
                WHEN status = 'FIXING' THEN 'Fixing'
                ELSE CAST(status AS nvarchar(max))
            END 
        AS nvarchar(max)) AS status,
        CAST(last_updated AS datetime) AS last_updated
    FROM [iot-stream] TIMESTAMP BY last_updated

    UNION

    -- Send Fleet to the Unified Map Container
    SELECT
        CAST(id AS nvarchar(max)) AS id,
        CAST('Fleet' AS nvarchar(max)) AS Category,
        CAST(id AS nvarchar(max)) AS location_name,
        CAST(
            CASE 
                WHEN status = 'DISPATCHED' THEN 'Fleet - Dispatched'
                WHEN status = 'IDLE' THEN 'Fleet - Idle'
                WHEN status = 'BASE' THEN 'Fleet - Base'
                WHEN status = 'ON_SITE' THEN 'Fleet - On Site'
                WHEN status = 'FIXING' THEN 'Fleet - Fixing'
                ELSE CONCAT('Fleet - ', CAST(status AS nvarchar(max)))
            END 
        AS nvarchar(max)) AS Legend_Category,
        CAST(NULL AS nvarchar(max)) AS suburb,
        CAST(NULL AS float) AS latest_pressure,
        CAST(lat AS float) + 0.00015 AS lat,
        CAST(lon AS float) + 0.00015 AS lon,
        CAST(
            CASE 
                WHEN status = 'DISPATCHED' THEN 'Dispatched'
                WHEN status = 'IDLE' THEN 'Idle'
                WHEN status = 'BASE' THEN 'Base'
                WHEN status = 'ON_SITE' THEN 'On Site'
                WHEN status = 'FIXING' THEN 'Fixing'
                ELSE CAST(status AS nvarchar(max))
            END 
        AS nvarchar(max)) AS status,
        CAST(last_updated AS datetime) AS last_updated
    FROM [fleet-stream] TIMESTAMP BY last_updated
    WHERE event_type = 'gps_update'
)

-- 1. THE AI BRIDGE ROUTE (Social Media)
-- This routes raw social complaints to the Python Function for Sentiment Analysis, which then updates Cosmos DB confidence scores.
SELECT 
    report_id,
    timestamp AS report_time,
    suburb,
    sensor_id,   -- Link to the exact sensor experiencing the possible incident
    text AS citizen_report
INTO [out-aibridge] 
FROM [social-stream] TIMESTAMP BY timestamp

-- 2. THE IOT SENSOR ROUTE
-- Takes the stateful sensor payloads from the generator and upserts them directly to the SensorTelemetry container 
-- Make sure the output in Azure is configured to use 'id' as Document ID and '/id' as Partition Key
SELECT
    id,
    last_updated,
    latest_pressure,
    status,
    confidence_score,
    social_reports_count
INTO [out-cosmos-sensortelemetry]
FROM [iot-stream] TIMESTAMP BY last_updated

-- 3. THE FLEET ROUTE
-- Takes the fleet position payloads and upserts them directly to the Fleet container
-- Make sure the output in Azure is configured to use 'id' as Document ID and '/id' as Partition Key
SELECT
    id,
    status,
    lat,
    lon,
    target_sensor_id,
    last_updated
INTO [out-cosmos-fleet]
FROM [fleet-stream] TIMESTAMP BY last_updated
WHERE event_type = 'gps_update'

-- 4. THE UNIFIED MAP ROUTE (Power BI DirectQuery)
SELECT *
INTO [out-cosmos-unifiedlocations]
FROM CombinedMapData

-- 5. THE INCIDENT HISTORY ROUTE
-- Takes incident resolution reports from the Fleet Event Hub and appends them to IncidentHistory
-- Make sure the output in Azure is configured to use 'id' as Document ID and '/sensor_id' as Partition Key
SELECT
    id,
    sensor_id,
    recorded_pressure,
    final_social_reports,
    incident_type,
    outcome,
    timestamp,
    date_only
INTO [out-cosmos-incidenthistory]
FROM [fleet-stream] TIMESTAMP BY last_updated
WHERE event_type = 'incident_resolution'
import azure.functions as func
import json
import os
import requests
import logging
from azure.cosmos import CosmosClient, exceptions

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="AnalyzePotchSentiment")
def AnalyzePotchSentiment(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Potch Pulse Bridge: Processing incoming social media reports.')

    try:
        events = req.get_json()
        if not isinstance(events, list):
            events = [events]

        # 1. Setup Clients
        ai_key = os.environ.get("AI_KEY")
        ai_endpoint = os.environ.get("AI_ENDPOINT", "").rstrip("/")
        
        cosmos_uri = os.environ.get("COSMOS_URI")
        cosmos_key = os.environ.get("COSMOS_KEY")
        db_name = os.environ.get("COSMOS_DB", "PotchSmartCity")
        
        cosmos_client = CosmosClient(cosmos_uri, cosmos_key)
        database = cosmos_client.get_database_client(db_name)
        
        # Connect to both containers
        social_container = database.get_container_client("SocialMediaReports")
        sensors_container = database.get_container_client("SensorTelemetry")

        processed_reports = []

        for event in events:
            report_text = event.get('citizen_report', '')
            suburb_str = event.get('suburb', 'unknown_suburb')
            sensor_id = event.get('sensor_id')
            report_id = event.get('report_id')
            report_time = event.get('report_time')

            # 2. Call the AI Service
            url = f"{ai_endpoint}/language/:analyze-text?api-version=2023-04-01"
            payload = {
                "kind": "SentimentAnalysis",
                "parameters": {"modelVersion": "latest"},
                "analysisInput": {
                    "documents": [{"id": "1", "language": "en", "text": report_text}]
                }
            }
            headers = {"Ocp-Apim-Subscription-Key": ai_key, "Content-Type": "application/json"}
            
            try:
                response = requests.post(url, headers=headers, json=payload)
                response.raise_for_status()
                sentiment = response.json()['results']['documents'][0]['sentiment']
            except Exception as e:
                logging.error(f"AI Sentiment call failed: {e}")
                sentiment = "neutral" # Fallback
            
            # 3. Save to SocialMediaReports Container
            social_doc = {
                "id": report_id,
                "suburb": suburb_str,
                "sensor_id": sensor_id,
                "text": report_text,
                "ai_sentiment": sentiment,
                "report_time": report_time
            }
            social_container.upsert_item(social_doc)
            processed_reports.append(social_doc)
            
            # 4. Integrate with Sensor / Incident state in SensorTelemetry container
            if sensor_id and suburb_str:
                try:
                    # Read the corresponding sensor state (partition key is id for SensorTelemetry)
                    sensor_item = sensors_container.read_item(item=sensor_id, partition_key=sensor_id)
                    
                    # Update confidence matching
                    if sentiment == "negative":
                        # E.g., Negative sentiment implies a +10% confidence boost
                        confidence_boost = 0.10
                    elif sentiment == "positive":
                        # E.g., positive sentiment might drop confidence
                        confidence_boost = -0.05
                    else:
                        confidence_boost = 0.05
                    
                    current_confidence = sensor_item.get('confidence_score', 0.5)
                    new_confidence = min(1.0, max(0.0, current_confidence + confidence_boost))
                    
                    # Increment the reports tracker
                    sensor_item['confidence_score'] = round(new_confidence, 2)
                    sensor_item['social_reports_count'] = sensor_item.get('social_reports_count', 0) + 1
                    
                    # Persist the enriched confidence back to Cosmos so the Fleet/PowerBI sees it immediately
                    sensors_container.upsert_item(sensor_item)
                    logging.info(f"Updated sensor {sensor_id} in {suburb_str} confidence to {new_confidence}")
                    
                except exceptions.CosmosResourceNotFoundError:
                    logging.warning(f"Sensor {sensor_id} not found in DB. Could not update confidence.")
                except Exception as e:
                    logging.error(f"Failed to update sensor state: {e}")

        return func.HttpResponse(json.dumps(processed_reports), status_code=200, mimetype="application/json")

    except Exception as e:
        logging.error(f"Bridge Error: {str(e)}")
        return func.HttpResponse(f"Bridge Error: {str(e)}", status_code=500)
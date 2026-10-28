import json
import uuid
import random
from datetime import datetime, timedelta

# Existing Sensor List
INITIAL_SENSORS = [
    {"id": "SENS-BULT-01", "suburb": "The Bult", "address": "100 Steve Biko St"},
    {"id": "SENS-BULT-02", "suburb": "The Bult", "address": "25 Hoffman St"},
    {"id": "SENS-BULT-03", "suburb": "The Bult", "address": "88 Meyer St"},
    {"id": "SENS-CENT-01", "suburb": "Potch Central", "address": "15 Wandrag Ln"},
    {"id": "SENS-CENT-02", "suburb": "Potch Central", "address": "55 Nelson Mandela Dr"},
    {"id": "SENS-CENT-03", "suburb": "Potch Central", "address": "14 Gouws St"},
    {"id": "SENS-DASS-01", "suburb": "Dassierand", "address": "32 Eleazer Rd"},
    {"id": "SENS-DASS-02", "suburb": "Dassierand", "address": "11 Alpha St"},
    {"id": "SENS-DASS-03", "suburb": "Dassierand", "address": "20 Beta St"},
    {"id": "SENS-GRIM-01", "suburb": "Grimbeek Park", "address": "5 Grimbeek Ave"},
    {"id": "SENS-GRIM-02", "suburb": "Grimbeek Park", "address": "12 Willow St"},
    {"id": "SENS-GRIM-03", "suburb": "Grimbeek Park", "address": "40 Mooi River Dr"},
    {"id": "SENS-BP-01", "suburb": "Baillie Park", "address": "12 Parys Ave"},
    {"id": "SENS-BP-02", "suburb": "Baillie Park", "address": "8 Rocher St"},
    {"id": "SENS-BP-03", "suburb": "Baillie Park", "address": "23 Bremner St"},
]

def generate_historical_data():
    start_date = datetime(2026, 4, 1, 0, 0, 0)
    end_date = datetime(2026, 5, 12, 23, 59, 59)
    total_days = (end_date - start_date).days

    incident_history = []
    social_media_reports = []

    # Scale incidents based on the number of days (approx 5-6 per day)
    num_incidents = int(total_days * 5.5)

    for _ in range(num_incidents):
        # Pick a random time in the month
        random_seconds = random.randint(0, int(total_days * 24 * 3600))
        incident_time = start_date + timedelta(seconds=random_seconds)
        
        # Pick a random sensor
        sensor = random.choice(INITIAL_SENSORS)
        
        # Determine outcome
        is_true_burst = random.random() < 0.7
        outcome = "Burst fixed" if is_true_burst else "False alarm"
        
        pressure = random.uniform(5, 15) if is_true_burst else random.uniform(12, 20)
        
        # Make the data realistic: True bursts generate more social media reports!
        if is_true_burst:
            reports_count = random.choices([0, 1, 2, 3, 5, 8], weights=[5, 15, 25, 30, 15, 10])[0]
        else:
            reports_count = random.choices([0, 1, 2], weights=[75, 20, 5])[0]
        
        incident_id = str(uuid.uuid4())
        
        incident_history.append({
            "id": incident_id,
            "sensor_id": sensor["id"],
            "recorded_pressure": round(pressure, 2),
            "final_social_reports": reports_count,
            "incident_type": "Reported Incident" if reports_count > 0 else "Possible Incident",
            "outcome": outcome,
            "timestamp": incident_time.isoformat() + "Z",
            "date_only": incident_time.strftime("%Y-%m-%d")
        })
        
        # Generate the social media reports leading up to this incident
        for _ in range(reports_count):
            report_time = incident_time - timedelta(minutes=random.randint(5, 120))
            
            sentiment = random.choices(["negative", "neutral", "positive"], weights=[80, 15, 5])[0]
            
            if sentiment == "negative":
                text = f"Water issues at {sensor['address']}! Please fix it."
            elif sentiment == "neutral":
                text = f"Is there a leak at {sensor['address']}?"
            else:
                text = f"Looks like they are working on the pipes near {sensor['address']}."
                
            social_media_reports.append({
                "id": str(uuid.uuid4()),
                "suburb": sensor["suburb"],
                "sensor_id": sensor["id"],
                "text": text,
                "ai_sentiment": sentiment,
                "report_time": report_time.isoformat() + "Z"
            })

    # Sort chronologically
    incident_history.sort(key=lambda x: x["timestamp"])
    social_media_reports.sort(key=lambda x: x["report_time"])

    # Write to files
    with open("historical_incident_history.json", "w") as f:
        json.dump(incident_history, f, indent=4)
        
    with open("historical_social_media.json", "w") as f:
        json.dump(social_media_reports, f, indent=4)
        
    print(f"Generated {len(incident_history)} Incident History records.")
    print(f"Generated {len(social_media_reports)} Social Media Reports.")

if __name__ == "__main__":
    generate_historical_data()

import json
import time
import random
import uuid
import threading
import math
import os
from datetime import datetime, timezone
from azure.eventhub import EventHubProducerClient, EventData
from azure.cosmos import CosmosClient, PartitionKey, exceptions

# --- AZURE CONFIGURATION ---
# Secrets come from environment variables (see .env.example); never hard-code keys.
EH_CONNECTION_STR = os.environ["EVENTHUB_CONNECTION_STR"]  # Event Hubs namespace connection string (send policy)
HUB_IOT = os.environ.get("HUB_IOT", "evh-potchiot-dev-italynorth")
HUB_SOCIAL = os.environ.get("HUB_SOCIAL", "evh-potchsocial-dev-italynorth")
HUB_FLEET = os.environ.get("HUB_FLEET", "evh-potchfleet-dev-italynorth")

COSMOS_URI = os.environ["COSMOS_URI"]
COSMOS_KEY = os.environ["COSMOS_KEY"]
DB_NAME = os.environ.get("COSMOS_DB", "PotchSmartCity")

MUNICIPALITY_BASE = {"lat": -26.7145, "lon": 27.0970} # Starting point for fleet

INITIAL_SENSORS = [
    # Bult
    {"id": "SENS-BULT-01", "lat": -26.6980, "lon": 27.0945, "suburb": "The Bult", "address": "100 Steve Biko St"},
    {"id": "SENS-BULT-02", "lat": -26.6955, "lon": 27.0910, "suburb": "The Bult", "address": "25 Hoffman St"},
    {"id": "SENS-BULT-03", "lat": -26.7001, "lon": 27.0955, "suburb": "The Bult", "address": "88 Meyer St"},
    
    # Potch Central
    {"id": "SENS-CENT-01", "lat": -26.7180, "lon": 27.0920, "suburb": "Potch Central", "address": "15 Wandrag Ln"},
    {"id": "SENS-CENT-02", "lat": -26.7168, "lon": 27.0940, "suburb": "Potch Central", "address": "55 Nelson Mandela Dr"},
    {"id": "SENS-CENT-03", "lat": -26.7140, "lon": 27.0915, "suburb": "Potch Central", "address": "14 Gouws St"},
    
    # Dassierand
    {"id": "SENS-DASS-01", "lat": -26.7025, "lon": 27.0855, "suburb": "Dassierand", "address": "32 Eleazer Rd"},
    {"id": "SENS-DASS-02", "lat": -26.7035, "lon": 27.0850, "suburb": "Dassierand", "address": "11 Alpha St"},
    {"id": "SENS-DASS-03", "lat": -26.7050, "lon": 27.0860, "suburb": "Dassierand", "address": "20 Beta St"},
    
    # Grimbeek Park
    {"id": "SENS-GRIM-01", "lat": -26.7210, "lon": 27.1120, "suburb": "Grimbeek Park", "address": "5 Grimbeek Ave"},
    {"id": "SENS-GRIM-02", "lat": -26.7235, "lon": 27.1155, "suburb": "Grimbeek Park", "address": "12 Willow St"},
    {"id": "SENS-GRIM-03", "lat": -26.7250, "lon": 27.1130, "suburb": "Grimbeek Park", "address": "40 Mooi River Dr"},
    
    # Baillie Park
    {"id": "SENS-BP-01", "lat": -26.7105, "lon": 27.1085, "suburb": "Baillie Park", "address": "12 Parys Ave"},
    {"id": "SENS-BP-02", "lat": -26.7090, "lon": 27.1050, "suburb": "Baillie Park", "address": "8 Rocher St"},
    {"id": "SENS-BP-03", "lat": -26.7115, "lon": 27.1040, "suburb": "Baillie Park", "address": "23 Bremner St"},
]

# --- SHARED MEMORY ---
CITY_STATE = {
    "sensors": {}, # sensor_id -> { document + ground_truth flags }
    "fleet": {
        "id": "TRUCK-01",
        "status": "IDLE", # IDLE, DISPATCHED, ON_SITE, FIXING
        "lat": MUNICIPALITY_BASE["lat"],
        "lon": MUNICIPALITY_BASE["lon"],
        "target_sensor_id": None
    },
    "lock": threading.Lock()
}

# ---------------------------------------------------------
# COSMOS DB INITIALIZATION
# ---------------------------------------------------------
print("Initializing Cosmos DB Clients...")
client = CosmosClient(COSMOS_URI, COSMOS_KEY)
db = client.create_database_if_not_exists(id=DB_NAME)

sensor_master_container = db.create_container_if_not_exists(
    id="SensorMaster", partition_key=PartitionKey(path="/suburb")
)
sensor_telemetry_container = db.create_container_if_not_exists(
    id="SensorTelemetry", partition_key=PartitionKey(path="/id")
)
fleet_container = db.create_container_if_not_exists(
    id="Fleet", partition_key=PartitionKey(path="/id")
)
db.create_container_if_not_exists(
    id="SocialMediaReports", partition_key=PartitionKey(path="/suburb")
)
incident_history_container = db.create_container_if_not_exists(
    id="IncidentHistory", partition_key=PartitionKey(path="/sensor_id")
)
db.create_container_if_not_exists(
    id="UnifiedLocations", partition_key=PartitionKey(path="/id")
)

# Initialize SensorMaster (Static Data)
print("Reading existing SensorMaster from Cosmos DB...")
master_items = list(sensor_master_container.query_items(
    query="SELECT * FROM c", enable_cross_partition_query=True
))

if not master_items:
    print("No SensorMaster found in Cosmos DB! Auto-seeding 15 initial sensors...")
    for s_info in INITIAL_SENSORS:
        master_doc = {
            "id": s_info["id"],
            "lat": s_info["lat"],
            "lon": s_info["lon"],
            "suburb": s_info["suburb"],
            "address": s_info["address"]
        }
        sensor_master_container.upsert_item(master_doc)
        master_items.append(master_doc)
    print("Auto-seeding SensorMaster complete.")

sensor_items = []
for m_doc in master_items:
    try:
        t_doc = sensor_telemetry_container.read_item(item=m_doc["id"], partition_key=m_doc["id"])
    except exceptions.CosmosResourceNotFoundError:
        t_doc = {
            "id": m_doc["id"],
            "status": "OK",
            "latest_pressure": random.uniform(45, 55),
            "confidence_score": 0.0,
            "social_reports_count": 0,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        sensor_telemetry_container.upsert_item(t_doc)
    
    full_doc = {**m_doc, **t_doc}
    sensor_items.append(full_doc)

for doc in sensor_items:
    sensor_id = doc["id"]
    is_active = doc.get("status") in ["POSSIBLE_INCIDENT", "FIXING"]
    
    # Keep ground truth hidden from DB
    CITY_STATE["sensors"][sensor_id] = {
        "doc": doc,
        "actually_broken": is_active and random.random() < 0.8, # Guess truth state if restarting during incident
        "is_active_incident": is_active,
        "target_reports": 0,
        "reports_generated": doc.get("social_reports_count", 0)
    }

print(f"Loaded {len(CITY_STATE['sensors'])} sensors. Starting Simulation Threads...")

# ---------------------------------------------------------
# THREAD 1: IOT SENSORS
# ---------------------------------------------------------
def run_iot():
    eh_client = EventHubProducerClient.from_connection_string(EH_CONNECTION_STR, eventhub_name=HUB_IOT)
    with eh_client:
        while True:
            with CITY_STATE["lock"]:
                if not CITY_STATE["sensors"]:
                    continue
                # Pick a random sensor
                sensor_id = random.choice(list(CITY_STATE["sensors"].keys()))
                sensor_data = CITY_STATE["sensors"][sensor_id]
                doc = sensor_data["doc"]

                if not sensor_data["is_active_incident"]:
                    # 3% chance of anomaly
                    is_anomaly = random.random() < 0.03
                    if is_anomaly:
                        pressure = random.uniform(5, 20)
                        doc["latest_pressure"] = pressure
                        doc["status"] = "POSSIBLE_INCIDENT"
                        
                        # Dynamically scale confidence based on how low the pressure is!
                        # Cap physics-only confidence at ~0.70 so social media has 0.30 room to grow!
                        calculated_conf = 0.75 - (doc["latest_pressure"] / 60.0) * 0.50
                        doc["confidence_score"] = round(min(0.70, max(0.40, calculated_conf)), 2)
                        doc["social_reports_count"] = 0
                        
                        # Tie truth to pressure drop (Analytical Threshold modeling for Power BI)
                        if pressure < 12:
                            sensor_data["actually_broken"] = random.random() < 0.95
                        elif pressure < 17:
                            sensor_data["actually_broken"] = random.random() < 0.60
                        else:
                            sensor_data["actually_broken"] = random.random() < 0.20
                            
                        sensor_data["is_active_incident"] = True
                        
                        # Cap social media reports
                        target = random.choices([0, 1, 2, 3, 4, 5, 8, 10], weights=[35, 15, 15, 15, 5, 5, 5, 5])[0]
                        sensor_data["target_reports"] = target
                        sensor_data["reports_generated"] = 0
                        
                        print(f"🚨 [IoT Alert] Anomaly at {doc['address']}! (Ground truth broken: {sensor_data['actually_broken']}, Target Reports: {target}, Confidence: {doc.get('confidence_score', 0.2)})")
                    else:
                        doc["latest_pressure"] = random.uniform(45, 55)

                doc["last_updated"] = datetime.now(timezone.utc).isoformat()
                
                # Push state to Event Hub instead of direct Cosmos SDK
                batch = eh_client.create_batch(partition_key=doc["suburb"])
                batch.add(EventData(json.dumps(doc)))
                eh_client.send_batch(batch)

            time.sleep(3) # Small sleep, loops frequently

# ---------------------------------------------------------
# THREAD 2: SOCIAL MEDIA
# ---------------------------------------------------------
def run_social():
    eh_client = EventHubProducerClient.from_connection_string(EH_CONNECTION_STR, eventhub_name=HUB_SOCIAL)
    eh_iot_client = EventHubProducerClient.from_connection_string(EH_CONNECTION_STR, eventhub_name=HUB_IOT)
    with eh_client, eh_iot_client:
        while True:
            with CITY_STATE["lock"]:
                active_incidents = [s for s in CITY_STATE["sensors"].values() if s["is_active_incident"] and s["doc"]["status"] != "FIXING"]
            
            if active_incidents:
                # Target one of the incidents to complain about
                target = random.choice(active_incidents)
                doc = target["doc"]
                
                # Check if we hit the cap for this incident
                if target.get("reports_generated", 0) < target.get("target_reports", 0):
                    if random.random() < 0.40: # Slow down the frequency slightly
                        target["reports_generated"] += 1
                        
                        # Dynamic Message Generation mapped to expected AI Sentiment
                        msg_type = random.choices(["NEGATIVE", "NEUTRAL", "POSITIVE"], weights=[70, 20, 10])[0]
                        addr = doc['address']
                        
                        if msg_type == "NEGATIVE":
                            msg = random.choice([
                                f"Huge water issues at {addr}! What is going on??",
                                f"Absolute disaster, the pipe at {addr} is gushing water everywhere!",
                                f"No water pressure at all in my house near {addr}!! Fix this ASAP."
                            ])
                        elif msg_type == "NEUTRAL":
                            msg = random.choice([
                                f"Just noticed some water pooling near {addr}. Municipality?",
                                f"Are they doing maintenance at {addr}? Water is acting weird.",
                                f"Reporting a possible minor leak near {addr}."
                            ])
                        else:
                            msg = random.choice([
                                f"Nevermind, the water at {addr} was just a puddle from a sprinkler.",
                                f"Everything looks fine at {addr} now, thanks!",
                                f"Pressure is finally back to normal near {addr}. What a relief."
                            ])
                        
                        payload = {
                            "report_id": str(uuid.uuid4()),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "suburb": doc["suburb"],
                            "sensor_id": doc["id"],
                            "text": msg
                        }
                        
                        batch = eh_client.create_batch(partition_key=doc["suburb"])
                        batch.add(EventData(json.dumps(payload)))
                        eh_client.send_batch(batch)
                        
                        # Upgrade the incident status so it appears differently on the map!
                        if CITY_STATE["sensors"][doc["id"]]["doc"]["status"] == "POSSIBLE_INCIDENT":
                            CITY_STATE["sensors"][doc["id"]]["doc"]["status"] = "REPORTED_INCIDENT"
                            
                            # Minor instant boost (0.05) to break fleet ties, leaving room for Azure Sentiment
                            new_conf = doc.get("confidence_score", 0.5) + 0.05
                            CITY_STATE["sensors"][doc["id"]]["doc"]["confidence_score"] = round(min(0.99, new_conf), 2)
                            
                            # Instantly update the IoT stream so the map turns RED immediately!
                            doc["last_updated"] = datetime.now(timezone.utc).isoformat()
                            batch_iot = eh_iot_client.create_batch(partition_key=doc["suburb"])
                            batch_iot.add(EventData(json.dumps(doc)))
                            eh_iot_client.send_batch(batch_iot)
                            
                        print(f"📱 [Social] Citizen reported an issue at {doc['address']}. (Target Reports: {target.get('target_reports')}, Confidence: {doc.get('confidence_score', 0.2)})")
            
            time.sleep(8)

# ---------------------------------------------------------
# THREAD 3: DB SYNC
# ---------------------------------------------------------
def run_db_sync():
    """ 
    Periodically fetches the sensor documents to capture the updated `confidence_score` 
    that the external AI Function App edits in the background!
    """
    while True:
        with CITY_STATE["lock"]:
            active_ids = [k for k, v in CITY_STATE["sensors"].items() if v["is_active_incident"]]
        
        for sid in active_ids:
            with CITY_STATE["lock"]:
                suburb = CITY_STATE["sensors"][sid]["doc"]["suburb"]
            try:
                db_item = sensor_telemetry_container.read_item(item=sid, partition_key=sid)
                with CITY_STATE["lock"]:
                    CITY_STATE["sensors"][sid]["doc"]["confidence_score"] = db_item.get("confidence_score", 0.0)
                    CITY_STATE["sensors"][sid]["doc"]["social_reports_count"] = db_item.get("social_reports_count", 0)
            except Exception as e:
                pass # Silent pass if document doesn't exist yet
                
        time.sleep(10)

# ---------------------------------------------------------
# THREAD 4: FLEET DISPATCH & NAVIGATION
# ---------------------------------------------------------
def move_towards(curr_lat, curr_lon, target_lat, target_lon, step=0.0017):
    """ Simple linear interpolation for movement """
    d_lat = target_lat - curr_lat
    d_lon = target_lon - curr_lon
    dist = math.hypot(d_lat, d_lon)
    
    if dist < step:
        return target_lat, target_lon, True # Arrived
    
    ratio = step / dist
    return curr_lat + (d_lat * ratio), curr_lon + (d_lon * ratio), False

def run_fleet():
    eh_fleet_client = EventHubProducerClient.from_connection_string(EH_CONNECTION_STR, eventhub_name=HUB_FLEET)
    eh_iot_client = EventHubProducerClient.from_connection_string(EH_CONNECTION_STR, eventhub_name=HUB_IOT)
    
    with eh_fleet_client, eh_iot_client:
        while True:
            with CITY_STATE["lock"]:
                f_state = CITY_STATE["fleet"]
                
                # --- 1. IDLE: Look for high priority incident ---
                if f_state["status"] == "IDLE":
                    best_target_id = None
                    best_score = -1.0
                    best_distance = float('inf')
                    
                    curr_lat, curr_lon = f_state["lat"], f_state["lon"]
                    
                    for sid, sdata in CITY_STATE["sensors"].items():
                        doc = sdata["doc"]
                        if sdata["is_active_incident"] and doc["status"] in ["POSSIBLE_INCIDENT", "REPORTED_INCIDENT"]:
                            
                            # Priority 1 is purely Confidence Score now
                            score = doc.get("confidence_score", 0.0)
                                
                            dist = math.hypot(doc["lat"] - curr_lat, doc["lon"] - curr_lon)
                            
                            # Evaluation
                            if score > best_score:
                                best_score = score
                                best_distance = dist
                                best_target_id = sid
                            elif score == best_score:
                                # TIE-BREAKER: Shortest Distance
                                if dist < best_distance:
                                    best_distance = dist
                                    best_target_id = sid
                                
                    if best_target_id:
                        f_state["status"] = "DISPATCHED"
                        f_state["target_sensor_id"] = best_target_id
                        actual_conf = CITY_STATE["sensors"][best_target_id]["doc"].get("confidence_score", 0.0)
                        print(f"🚛 [Fleet] Dispatched to {best_target_id} (Confidence: {actual_conf})")
                    else:
                        # Return to Municipality Base if no active incidents!
                        new_lat, new_lon, arrived = move_towards(f_state["lat"], f_state["lon"], MUNICIPALITY_BASE["lat"], MUNICIPALITY_BASE["lon"])
                        f_state["lat"] = new_lat
                        f_state["lon"] = new_lon
                
                # --- 2. DISPATCHED: Move towards target ---
                elif f_state["status"] == "DISPATCHED":
                    target_doc = CITY_STATE["sensors"][f_state["target_sensor_id"]]["doc"]
                    new_lat, new_lon, arrived = move_towards(f_state["lat"], f_state["lon"], target_doc["lat"], target_doc["lon"])
                    f_state["lat"] = new_lat
                    f_state["lon"] = new_lon
                    
                    if arrived:
                        f_state["status"] = "ON_SITE"
                        print(f"🚛 [Fleet] Arrived at {f_state['target_sensor_id']}. Checking true state...")
                
                # --- 3. ON_SITE: Verify Ground Truth ---
                elif f_state["status"] == "ON_SITE":
                    sid = f_state["target_sensor_id"]
                    sdata = CITY_STATE["sensors"][sid]
                    
                    if sdata["actually_broken"]:
                        print(f"🛠️ [Fleet] Confirmed burst at {sid}! Beginning repairs...")
                        f_state["status"] = "FIXING"
                        sdata["doc"]["status"] = "FIXING"
                        
                        # Send updated sensor doc to IOT event hub
                        sdata["doc"]["last_updated"] = datetime.now(timezone.utc).isoformat()
                        batch = eh_iot_client.create_batch(partition_key=sdata["doc"]["suburb"])
                        batch.add(EventData(json.dumps(sdata["doc"])))
                        eh_iot_client.send_batch(batch)
                    else:
                        print(f"❌ [Fleet] False alarm at {sid}! No issue found.")
                        
                        # Log Incident History for Power BI before resetting
                        try:
                            incident_doc = {
                                "id": str(uuid.uuid4()),
                                "sensor_id": sid,
                                "recorded_pressure": sdata["doc"]["latest_pressure"],
                                "final_social_reports": sdata["doc"]["social_reports_count"],
                                "incident_type": "Reported Incident" if sdata["doc"]["social_reports_count"] > 0 else "Possible Incident",
                                "outcome": "False alarm",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "date_only": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                "event_type": "incident_resolution",
                                "last_updated": datetime.now(timezone.utc).isoformat()
                            }
                            batch = eh_fleet_client.create_batch(partition_key=sid)
                            batch.add(EventData(json.dumps(incident_doc)))
                            eh_fleet_client.send_batch(batch)
                        except Exception as e:
                            print(f"⚠️ Failed to write to incident history: {e}")
                            
                        sdata["doc"]["status"] = "OK"
                        sdata["doc"]["confidence_score"] = 0.0
                        sdata["is_active_incident"] = False
                        
                        sdata["doc"]["last_updated"] = datetime.now(timezone.utc).isoformat()
                        batch = eh_iot_client.create_batch(partition_key=sdata["doc"]["suburb"])
                        batch.add(EventData(json.dumps(sdata["doc"])))
                        eh_iot_client.send_batch(batch)
                        
                        f_state["status"] = "IDLE"
                        f_state["target_sensor_id"] = None
                        
                # --- 4. FIXING: Perform repairs ---
                elif f_state["status"] == "FIXING":
                    sid = f_state["target_sensor_id"]
                    sdata = CITY_STATE["sensors"][sid]
                    
                    if random.random() < 0.25:
                        print(f"✅ [Fleet] Issue resolved at {sid}. Returning to duties.")
                        
                        # Log Incident History for Power BI before resetting
                        try:
                            incident_doc = {
                                "id": str(uuid.uuid4()),
                                "sensor_id": sid,
                                "recorded_pressure": sdata["doc"]["latest_pressure"],
                                "final_social_reports": sdata["doc"]["social_reports_count"],
                                "incident_type": "Reported Incident" if sdata["doc"]["social_reports_count"] > 0 else "Possible Incident",
                                "outcome": "Burst fixed",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "date_only": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                "event_type": "incident_resolution",
                                "last_updated": datetime.now(timezone.utc).isoformat()
                            }
                            batch = eh_fleet_client.create_batch(partition_key=sid)
                            batch.add(EventData(json.dumps(incident_doc)))
                            eh_fleet_client.send_batch(batch)
                        except Exception as e:
                            print(f"⚠️ Failed to write to incident history: {e}")
                            
                        sdata["doc"]["status"] = "OK"
                        sdata["doc"]["confidence_score"] = 0.0
                        sdata["is_active_incident"] = False
                        
                        sdata["doc"]["last_updated"] = datetime.now(timezone.utc).isoformat()
                        batch = eh_iot_client.create_batch(partition_key=sdata["doc"]["suburb"])
                        batch.add(EventData(json.dumps(sdata["doc"])))
                        eh_iot_client.send_batch(batch)
                        
                        f_state["status"] = "IDLE"
                        f_state["target_sensor_id"] = None
                
                # Save Fleet position to Fleet Event Hub constantly
                fleet_doc = {
                    "id": f_state["id"],
                    "status": f_state["status"],
                    "lat": f_state["lat"],
                    "lon": f_state["lon"],
                    "target_sensor_id": f_state["target_sensor_id"],
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "event_type": "gps_update"
                }
                batch = eh_fleet_client.create_batch(partition_key=f_state["id"])
                batch.add(EventData(json.dumps(fleet_doc)))
                
                # Ping the Municipality Base as well, so it shows up on the map!
                base_doc = {
                    "id": "JB-Marks",
                    "status": "BASE",
                    "lat": MUNICIPALITY_BASE["lat"],
                    "lon": MUNICIPALITY_BASE["lon"],
                    "target_sensor_id": None,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "event_type": "gps_update"
                }
                batch.add(EventData(json.dumps(base_doc)))
                eh_fleet_client.send_batch(batch)
                
            time.sleep(5) # 5 second movement ticks

if __name__ == "__main__":
    t_iot = threading.Thread(target=run_iot, daemon=True)
    t_soc = threading.Thread(target=run_social, daemon=True)
    t_sync = threading.Thread(target=run_db_sync, daemon=True)
    t_fleet = threading.Thread(target=run_fleet, daemon=True)
    
    t_iot.start(); t_soc.start(); t_sync.start(); t_fleet.start()
    
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
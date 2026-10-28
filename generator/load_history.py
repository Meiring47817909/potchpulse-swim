"""Backfill Cosmos DB with the generated historical data so the Power BI trend pages have history.

Loads data/historical_incident_history.json -> IncidentHistory (partition key /sensor_id)
and   data/historical_social_media.json     -> SocialMediaReports (partition key /suburb).

Uses the same settings as potch_generator.py (COSMOS_URI, COSMOS_KEY, COSMOS_DB from the
environment or generator/.env). Safe to re-run: documents are upserted by id.

    python load_history.py
"""
import json
import os

from azure.cosmos import CosmosClient, PartitionKey

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from dotenv import load_dotenv  # optional: load settings from generator/.env
    load_dotenv(os.path.join(HERE, ".env"))
except ImportError:
    pass

COSMOS_URI = os.environ["COSMOS_URI"]
COSMOS_KEY = os.environ["COSMOS_KEY"]
DB_NAME = os.environ.get("COSMOS_DB", "PotchSmartCity")

# file -> (container, partition key path); partition keys match potch_generator.py
TARGETS = [
    ("historical_incident_history.json", "IncidentHistory", "/sensor_id"),
    ("historical_social_media.json", "SocialMediaReports", "/suburb"),
]


def main():
    db = CosmosClient(COSMOS_URI, COSMOS_KEY).create_database_if_not_exists(id=DB_NAME)
    for file_name, container_name, pk_path in TARGETS:
        with open(os.path.join(HERE, "data", file_name), encoding="utf-8") as f:
            docs = json.load(f)
        container = db.create_container_if_not_exists(
            id=container_name, partition_key=PartitionKey(path=pk_path)
        )
        for i, doc in enumerate(docs, 1):
            container.upsert_item(doc)
            if i % 100 == 0:
                print(f"  {container_name}: {i}/{len(docs)}")
        print(f"Loaded {len(docs)} documents into {container_name}.")


if __name__ == "__main__":
    main()

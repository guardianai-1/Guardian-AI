# security_db_setup.py
from pymongo import MongoClient, ASCENDING, DESCENDING
from security_config import MONGO_URI, DB_NAME

import json
import os
from pymongo.errors import CollectionInvalid

SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "schema")


def _load_schema(file_name: str):
    path = os.path.join(SCHEMA_DIR, file_name)
    with open(path, "r") as f:
        return json.load(f)


def _ensure_indexes(collection, x_indexes: list):
    for spec in x_indexes or []:
        keys = spec.get("keys")
        options = spec.get("options", {})
        if not keys:
            # Skip notes or entries without key specs
            continue
        if isinstance(keys, dict):
            # Handle 2dsphere special case
            items = []
            for k, v in keys.items():
                if isinstance(v, str) and v.lower() == "2dsphere":
                    items.append((k, "2dsphere"))
                else:
                    items.append((k, v))
        else:
            # Unexpected format; skip
            continue
        try:
            collection.create_index(items, **options)
        except Exception as e:
            print(f"  - WARNING: Failed to create index {keys}: {e}")


def _apply_collection_schema(db, schema_json: dict):
    name = schema_json.get("collection")
    if not name:
        return
    validator = schema_json.get("validator")
    validationLevel = schema_json.get("validationLevel", "moderate")
    validationAction = schema_json.get("validationAction", "warn")

    existing_collections = db.list_collection_names()
    if name not in existing_collections:
        print(f"Creating collection '{name}' with validator...")
        try:
            db.create_collection(name, validator=validator, validationLevel=validationLevel, validationAction=validationAction)
        except CollectionInvalid:
            # Race or already exists without validator; continue to collMod
            pass
    else:
        print(f"Updating validator for existing collection '{name}'...")
        try:
            db.command({
                "collMod": name,
                "validator": validator or {},
                "validationLevel": validationLevel,
                "validationAction": validationAction,
            })
        except Exception as e:
            print(f"  - WARNING: collMod failed for {name}: {e}")

    # Ensure indexes from schema metadata
    _ensure_indexes(db[name], schema_json.get("x-indexes", []))


def setup_database():
    """
    Connects to the database and creates all necessary collections and indexes.
    This script should be run once during initial deployment.
    """
    print("Connecting to MongoDB...")
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    print(f"Connected to database: '{DB_NAME}'")

    # Apply schema validators and indexes from JSON files
    print("Applying collection schemas and indexes from /schema ...")
    for file in [
        "tracked_subjects.schema.json",
        "events.schema.json",
        "vlm_logs.schema.json",
        "cameras.schema.json",
    ]:
        try:
            schema_def = _load_schema(file)
            _apply_collection_schema(db, schema_def)
            print(f"  - Applied schema for {schema_def.get('collection')}")
        except FileNotFoundError:
            print(f"  - WARNING: Schema file not found: {file}")
        except json.JSONDecodeError as e:
            print(f"  - WARNING: Invalid JSON in {file}: {e}")

    print("\nAdditional actions required (if using Atlas Vector Search):")
    print("  - Create Vector Search index 'reid_vector_index' on TrackedSubjects.reid_vector")
    print("  - Create Vector Search index 'vlm_log_index' on VlmLogs.description_embedding")

    print("\nDatabase setup complete. Collections, validators, and indexes are ready.")
    client.close()


if __name__ == "__main__":
    setup_database()
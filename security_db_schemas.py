# security_db_schemas.py

# Note: While MongoDB is schemaless, this file defines the intended structure
# for our application data, serving as essential documentation.

TRACKED_SUBJECTS_SCHEMA = {
    "_id": "ObjectId",
    "tracking_id": "string (e.g., 'person_169...')", # INDEXED, UNIQUE
    "current_status": "string ('normal', 'suspicious', 'threat_cleared')",
    "current_camera_id": "ObjectId",
    "representative_thumbnail_url": "string (URL to image)",
    "reid_vector": "array[float] (e.g., [0.11, -0.23, ...])" # REQUIRES VECTOR INDEX
}

EVENTS_SCHEMA = {
    "_id": "ObjectId",
    "start_time": "ISODate",
    "end_time": "ISODate (null while active)",
    "status": "string ('active', 'ended_cleared', 'ended_escalated')", # INDEXED
    "start_camera_id": "ObjectId",
    "involved_cameras": "array[ObjectId]",
    "participant_tracking_ids": "array[string]", # INDEXED (Multikey)
    "final_summary": "string (optional)"
}

VLM_LOGS_SCHEMA = {
    "_id": "ObjectId",
    "event_id": "ObjectId", # INDEXED
    "timestamp": "ISODate", # INDEXED (part of compound index, and TTL index)
    "camera_id": "ObjectId",
    "frame_image_url": "string (URL to image)",
    "collective_description": "string",
    "description_embedding": "array[float]", # REQUIRES VECTOR INDEX
    "subjects_in_log": "array[string]" # INDEXED (Multikey)
}

CAMERAS_SCHEMA = {
    "_id": "ObjectId",
    "name": "string (e.g., 'Lobby - Main Entrance')", # INDEXED
    "stream_url": "string",
    "location": {
        "type": "Point",
        "coordinates": "[longitude, latitude]"
    }
}
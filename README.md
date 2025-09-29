# Video Analytics App – Changes and Setup

This README documents the updates made and how to run the app and database setup.

What changed
- security_threat_detection.py
  - Added a runnable __main__ entrypoint that initializes queues and starts threat_detection_process.
  - Added dynamic video source resolution:
    - CLI arg 1 (e.g., security_threat_detection.py 0 or /path/to/video.mp4)
    - THREAT_VIDEO_SOURCE environment variable
    - Fallback to webcam index 0
  - Keeps the YOLO tracking loop running with an on-screen preview (show=True).

- schema/ (new MongoDB JSON Schemas)
  - schema/tracked_subjects.schema.json: Validation for TrackedSubjects + unique index on tracking_id + guidance for Atlas Vector Search on reid_vector.
  - schema/events.schema.json: Validation for Events + indexes on status and participant_tracking_ids.
  - schema/vlm_logs.schema.json: Validation for VlmLogs + compound and TTL indexes + guidance for Vector Search on description_embedding.
  - schema/cameras.schema.json: Validation for Cameras + unique name index + optional 2dsphere geospatial index.

- security_db_setup.py
  - Now loads JSON schema files from schema/ and applies them via create_collection/collMod.
  - Ensures recommended indexes declared in the x-indexes block are created.
  - Prints guidance to create Atlas Vector Search indexes manually (if using Atlas Vector Search).

How to run the app (Python 3.12.3 with uv)
- Default webcam (index 0):
  - ./scripts/run_with_uv.sh
- Specific camera index:
  - ./scripts/run_with_uv.sh security_threat_detection.py 1
- Video file:
  - THREAT_VIDEO_SOURCE=/absolute/path/to/video.mp4 ./scripts/run_with_uv.sh

MongoDB setup
1) Configure environment
- Copy .env.example to .env and update values:
  - MONGO_URI (required)
  - DB_NAME (optional; defaults to video_analytics_db)

2) Apply collection validators and indexes
- uv run -p 3.12.3 python security_db_setup.py
- This will:
  - Create/update collections with JSON Schema validation
  - Create indexes defined in x-indexes blocks
  - Remind you to create Atlas Vector Search indexes (manual step)

Vector Search (manual in Atlas)
- TrackedSubjects.reid_vector → index name: reid_vector_index
- VlmLogs.description_embedding → index name: vlm_log_index

Files touched
- security_threat_detection.py (entrypoint + video source handling)
- security_db_setup.py (schema loader + indexes)
- schema/*.schema.json (new)

Environment variables
- MONGO_URI: MongoDB connection string
- DB_NAME: Database name (default: video_analytics_db)
- THREAT_VIDEO_SOURCE: Video input (int camera index or absolute file path)

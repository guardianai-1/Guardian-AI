# MongoDB Schemas, Rationale, and App Integration

This document explains why each collection exists, what every field means, how they relate to each other, and how the runtime writes/reads them.

- security_threat_detection.py (YOLOv8 pose-based, with __main__ entrypoint)
- security_threat_detection1.py (MediaPipe-based variant)
Both integrate with the DB layer through security_db_writer.py and security_db_queries.py.

Why this schema
- Event-centric model: Suspicious behavior is captured as an Event with a lifecycle (start → enrich → end).
- Separation of concerns:
  - TrackedSubjects stores person-level state and re-identification (ReID) vector.
  - Events stores timelines and participants.
  - VlmLogs stores periodic multimodal analyses tied to an event.
  - Cameras registers streams and optional geo metadata.
- Performance and search:
  - Strict indexes on lookups used by the app (status, participants, event timeline).
  - Vector-ready fields (reid_vector, description_embedding) for Atlas Vector Search.
  - Optional TTL on logs to manage storage growth.
- Evolvability: JSON Schema validationLevel is set to "moderate" so we can iterate quickly without hard failures while still catching shape drift.

Collections and fields (and why)

1) Cameras
Purpose: Registry of camera streams used by detection.
Fields:
- _id: ObjectId. Primary key.
- name: string (unique). Human-friendly camera name. Index supports fast name lookup.
- stream_url: string. RTSP/HTTP file path or device-specific source.
- location: GeoJSON Point { type: "Point", coordinates: [lon, lat] }. Optional; enables mapping and geo queries.
- created_at, updated_at: date. Optional bookkeeping.
Indexes:
- Unique index on name.
- 2dsphere on location (if used).
App integration:
- security_threat_detection.py currently uses a placeholder camera_id (e.g., 'cam_01'). Recommended: create a Cameras doc and pass its ObjectId; or relax types to string if you prefer string IDs.

2) TrackedSubjects
Purpose: One document per recognized person across frames, with current status and optional ReID vector.
Fields:
- _id: ObjectId. Primary key.
- tracking_id: string (unique). App-level stable ID (e.g., person_ab12cd34). Used widely in Events/VlmLogs.
- current_status: string enum ['normal','suspicious','threat_cleared']. Current classification.
- current_camera_id: ObjectId|null. Where the subject was last seen.
- representative_thumbnail_url: string|null. Optional thumbnail for UI.
- reid_vector: array[float]|null. Embedding for re-identification. Requires Vector Search index in Atlas to query by similarity.
- created_at, updated_at: date|null. Optional bookkeeping.
Indexes:
- Unique index on tracking_id.
- Vector Search (manual in Atlas) on reid_vector.
App integration:
- security_threat_detection.py enqueues create_new_subject when first seeing a YOLO track and update_subject_status on pose changes.

3) Events
Purpose: Lifecycle record for suspicious activity sessions.
Fields:
- _id: ObjectId. Pre-generated in the detector to correlate all writes during the session.
- start_time: date. When event started.
- end_time: date|null. When event ended.
- status: string enum ['active','ended_cleared','ended_escalated']. Used for dashboards and filtering.
- start_camera_id: ObjectId. Camera where event began.
- involved_cameras: array[ObjectId]|null. Cameras that later joined (multi-cam setups).
- participant_tracking_ids: array[string]. The app-level person IDs involved. Multikey index for fast queries.
- final_summary: string|null. Human/VLM-produced summary at close.
- created_at, updated_at: date|null. Optional bookkeeping.
Indexes:
- status, participant_tracking_ids.
App integration:
- The detector enqueues create_event when suspicious subjects appear, add_participant_to_event as new people join, and end_event on resolution.

4) VlmLogs
Purpose: Time-stamped analysis entries (e.g., every 5s) that enrich an Event with descriptions and subjects present.
Fields:
- _id: ObjectId. Primary key.
- event_id: ObjectId. Foreign key to Events._id.
- timestamp: date. Log time (indexed, supports TTL).
- camera_id: ObjectId|null. Which camera produced the frame.
- frame_image_url: string|null. Optional frame capture URL (e.g., to S3 or local storage).
- collective_description: string. VLM-produced description or summary for that slice.
- description_embedding: array[float]|null. Vector for semantic search on logs.
- subjects_in_log: array[string]. Who was present in this log entry.
- created_at: date|null. Optional bookkeeping.
Indexes:
- Compound (event_id, timestamp) for timelines.
- subjects_in_log for "show all logs with X".
- TTL on timestamp (e.g., 90 days) to auto-purge older logs (optional).
- Vector Search (manual in Atlas) on description_embedding.
App integration:
- The detector enqueues VLM tasks periodically while an Event is active; a worker writes VlmLogs via add_vlm_log.

How the app writes to MongoDB
- security_threat_detection.py detects people, tracks pose, and pushes tasks to db_writer_queue (create_new_subject, update_subject_status, create_event, add_participant_to_event, end_event).
- security_db_writer.py consumes that queue and calls security_db_queries.DatabaseManager methods.
- security_db_queries.py performs the actual inserts/updates on TrackedSubjects, Events, and VlmLogs.

Type alignment note (ObjectId vs string)
- The code currently passes 'cam_01' as camera_id/start_camera_id. Schemas expect ObjectId for cameras. Because validationLevel is "moderate", writes will succeed but you may see validation warnings.
Recommended:
1) Insert each camera in Cameras; capture its _id; pass that ObjectId to create_event/create_new_subject.
2) Or, switch those fields to strings in the schemas if you prefer string IDs.

Applying schemas and indexes
- Automated (preferred): uv run -p 3.12.3 python security_db_setup.py
  - Reads schema/*.schema.json
  - Applies validators via createCollection/collMod
  - Creates indexes declared in x-indexes
  - Prints reminders for Atlas Vector Search indexes
- Manual (mongosh) examples are still valid if you prefer shell-based setup.

Vector Search (Atlas)
- TrackedSubjects.reid_vector → create index reid_vector_index
- VlmLogs.description_embedding → create index vlm_log_index
- Choose dimensions and similarity metric (e.g., cosine) based on your embedding model.

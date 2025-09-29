# System Architecture and Deployment Guide

Overview
- This application performs real‑time video threat detection using a pose model (YOLOv8 pose) and maintains an event‑centric record in MongoDB. It is process‑oriented and message‑driven via in‑memory queues.
- Key modules:
  - security_threat_detection.py: detector/runtime loop, event lifecycle, queue producer
  - security_db_writer.py: database writer process (queue consumer)
  - security_worker.py: background AI worker for VLM analysis (queue consumer/producer)
  - security_db_queries.py: MongoDB data access
  - security_db_setup.py: applies JSON schema validation and indexes from schema/
  - schema/*.schema.json: JSON Schemas and index recommendations for MongoDB
  - scripts/run_with_uv.sh: ensures Python 3.12.3, venv, dependencies; launches entrypoint

Runtime data flow (logical)

  +--------------------+            +----------------------------+
  |  Video Sources     |            |  Configuration (.env)      |
  |  (Webcam/RTSP/MP4) |            |  - THREAT_VIDEO_SOURCE     |
  +----------+---------+            |  - THREAT_MODEL_PATH       |
             |                      |  - THREAT_IMG_SIZE/CONF    |
             v                      |  - MONGO_URI / DB_NAME     |
  +-----------------------------+   +-------------+--------------+
  | security_threat_detection.py|                 |
  |  - YOLOv8 pose inference    |<----------------+
  |  - Pose heuristic (on-ground)|
  |  - Track subjects           |
  |  - Manage single active     |
  |    Event lifecycle          |
  |  - Enqueue DB + VLM tasks   |
  +------+----------------------+ 
         | db_writer_queue (tasks)
         | vlm_task_queue (tasks)
         v                         vlm_result_queue (results)
  +-------------------+            ^
  | security_db_writer|            |
  |  - Consumes DB    |            |
  |    tasks and calls|            |
  |    security_db_   |            |
  |    queries        |            |
  +---------+---------+            |
            |                      |
            v                      |
      +-----------+                |
      | MongoDB   |                |
      | (Schemas: |                |
      | Cameras,  |                |
      | Tracked-  |                |
      | Subjects, |                |
      | Events,   |                |
      | VlmLogs)  |                |
      +-----------+                |
                                    
                         +--------------------+
                         | security_worker.py |
                         |  - Consumes VLM    |
                         |    tasks, analyzes |
                         |    frames (e.g. LLM|
                         |    vision), writes |
                         |    VlmLogs via DB  |
                         |    tasks, posts    |
                         |    summaries to    |
                         |    vlm_result_queue|
                         +--------------------+

Entity model (MongoDB)
- Cameras: registry of streams (name, stream_url, optional location). Unique index on name; optional 2dsphere index for location.
- TrackedSubjects: one per person across frames (tracking_id unique, status, optional camera ref, optional reid_vector for vector search).
- Events: event lifecycle and participants (status, start/end times, camera IDs, participant_tracking_ids, final_summary).
- VlmLogs: periodic analysis tied to Event (timestamp, camera_id, collective_description, subjects_in_log, optional embedding for vector search). TTL index optional for auto‑prune.
- JSON Schemas and recommended indexes live under schema/ and are applied by security_db_setup.py.

Sequence (event lifecycle)
1) Detector identifies people and evaluates on‑ground pose. When a subject holds the pose for POSE_CONFIRMATION_SEC, it transitions to suspicious.
2) If at least one suspicious subject exists and no active event is present, the detector creates an Event (pre‑generated _id) and adds the first participant.
3) Every VLM_INTERVAL_SEC while active, the detector sends a VLM analysis task with a base64 frame snapshot and participant IDs.
4) The AI worker processes the task, generates a collective_description (and optionally an embedding), and writes a VlmLogs entry; results can be sent back on vlm_result_queue for UI/logging.
5) When no subjects remain suspicious, the detector ends the Event and writes final status/summary if available.

Process topology
- Single‑host (default): All processes (detector, DB writer, AI worker) run on one machine using multiprocessing queues.
- Multi‑camera: Run one detector process per camera source. All detector processes share the same DB writer and AI worker pool.
- Distributed (optional): Replace in‑memory queues with a message bus (e.g., Redis, RabbitMQ, or Kafka) to allow cross‑host scaling.

Deployment blueprint

Local development (current):
- ./scripts/run_with_uv.sh uses Python 3.12.3, sets up .venv, installs requirements, and runs security_threat_detection.py.
- MongoDB connection via MONGO_URI from .env; schemas applied with: uv run -p 3.12.3 python security_db_setup.py.

Containerized (suggested):
- Services:
  - detector: image with model weights and camera access (one replica per camera).
  - db-writer: single consumer service for DB writes.
  - ai-worker: N replicas to handle VLM analysis throughput.
  - mongodb: managed service (e.g., Atlas) or self‑hosted (stateful).
- Networking: internal network between services; outbound to MongoDB.
- Secrets: inject MONGO_URI, DB_NAME, THREAT_* via env/secret store.
- Storage: mount model files (e.g., yolov8*-pose.pt) as read‑only; optional volume for logs/frames.
- GPU (optional): enable if using heavy models; otherwise CPU works for smaller models.

Scaling
- Throughput scales with:
  - detector replicas (more cameras)
  - ai-worker replicas (more concurrent VLM tasks)
  - MongoDB cluster tier and indexes (query/write throughput)
- Backpressure: Increase VLM interval or batch requests if AI is the bottleneck.

Observability
- Logs from each process; centralize via container logs or a log aggregator.
- Health probes: liveness/readiness endpoints (add a lightweight HTTP health server if desired).
- Metrics: FPS, queue depth, event counts, VLM latency (can be exported via stdout or a metrics server).

Configuration (.env)
- MONGO_URI (required): MongoDB connection string.
- DB_NAME (optional): defaults to video_analytics_db.
- THREAT_VIDEO_SOURCE: camera index or absolute file path.
- THREAT_MODEL_PATH: pose model path; defaults to yolov8s-pose.pt.
- THREAT_IMG_SIZE, THREAT_CONF: inference parameters.

Notes
- Cameras fields are defined as ObjectId in schemas; the detector currently uses a placeholder camera_id string (e.g., 'cam_01'). Either pass real Camera ObjectIds from the Cameras collection or relax the schema to string types for camera fields.
- Vector search (Atlas): create reid_vector_index on TrackedSubjects.reid_vector and vlm_log_index on VlmLogs.description_embedding if you use similarity queries.

# security_db_queries.py
from pymongo import MongoClient
from bson import ObjectId
import datetime

class DatabaseManager:
    """
    A class to handle all database operations for the video analytics application.
    """
    def __init__(self, uri, db_name):
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
        self.subjects = self.db.TrackedSubjects
        self.events = self.db.Events
        self.vlm_logs = self.db.VlmLogs
        self.cameras = self.db.Cameras

    # --- Subject (Re-ID) Queries ---
    def find_subject_by_vector(self, vector, k=1):
        """Finds the most similar subject using vector search."""
        # This requires a Vector Search index named 'reid_vector_index' in Atlas
        pipeline = [
            {
                "$search": {
                    "index": "reid_vector_index",
                    "knnBeta": {
                        "vector": vector,
                        "path": "reid_vector",
                        "k": k
                    }
                }
            },
            {"$limit": k}
        ]
        results = list(self.subjects.aggregate(pipeline))
        return results[0] if results else None

    def create_new_subject(self, tracking_id, reid_vector, camera_id):
        """Creates a new tracked subject."""
        doc = {
            "tracking_id": tracking_id,
            "current_status": "normal",
            "current_camera_id": camera_id,
            "representative_thumbnail_url": None, # Can be updated later
            "reid_vector": reid_vector
        }
        return self.subjects.insert_one(doc).inserted_id

    def update_subject_status(self, tracking_id, status, camera_id=None):
        """Updates the status and location of a subject."""
        update_doc = {"$set": {"current_status": status}}
        if camera_id:
            update_doc["$set"]["current_camera_id"] = camera_id
        return self.subjects.update_one({"tracking_id": tracking_id}, update_doc)

    # --- Event Queries ---

    def create_event(self, event_id, start_camera_id, participant_tracking_id):
        """Starts a new event log using a pre-generated ObjectId."""
        doc = {
            "_id": event_id, # <-- Use the passed ID
            "start_time": datetime.datetime.utcnow(),
            "end_time": None,
            "status": "active",
            "start_camera_id": start_camera_id,
            "involved_cameras": [start_camera_id],
            "participant_tracking_ids": [participant_tracking_id]
        }
        # Use insert_one, it will use the _id from the doc if present
        self.events.insert_one(doc)
        return event_id


    def add_participant_to_event(self, event_id, tracking_id):
        """Adds a new person to an ongoing event's participant list."""
        return self.events.update_one(
            {"_id": event_id},
            {"$addToSet": {"participant_tracking_ids": tracking_id}}
        )

    def end_event(self, event_id, final_status="ended_cleared", summary=""):
        """Marks an event as ended."""
        return self.events.update_one(
            {"_id": event_id},
            {"$set": {
                "status": final_status,
                "end_time": datetime.datetime.utcnow(),
                "final_summary": summary
            }}
        )

    # --- VLM Log Queries ---
    def add_vlm_log(self, event_id, camera_id, description, embedding, subjects):
        """Adds a new VLM log entry."""
        doc = {
            "event_id": event_id,
            "timestamp": datetime.datetime.utcnow(),
            "camera_id": camera_id,
            "frame_image_url": None, # Can be updated later
            "collective_description": description,
            "description_embedding": embedding,
            "subjects_in_log": subjects
        }
        return self.vlm_logs.insert_one(doc).inserted_id

    # --- Complex Analytical Queries ---
    def get_person_involvement_details(self, tracking_id):
        """
        Finds all events a person was involved in and the exact start/end
        timestamps of their involvement in each.
        """
        pipeline = [
            {"$match": {"subjects_in_log": tracking_id}},
            {"$group": {
                "_id": "$event_id",
                "person_involvement_start": {"$min": "$timestamp"},
                "person_involvement_end": {"$max": "$timestamp"}
            }},
            {"$lookup": {
                "from": "Events",
                "localField": "_id",
                "foreignField": "_id",
                "as": "event_details"
            }},
            {"$project": {
                "_id": 0,
                "event_id": "$_id",
                "person_involvement_start": 1,
                "person_involvement_end": 1,
                "event_details": {"$arrayElemAt": ["$event_details", 0]}
            }},
            {"$sort": {"person_involvement_start": -1}}
        ]
        return list(self.vlm_logs.aggregate(pipeline))

    def semantic_search_logs(self, query_vector, k=5):
        """Performs semantic search on VLM descriptions."""
        # This requires a Vector Search index named 'vlm_log_index' in Atlas
        pipeline = [
            {
                "$search": {
                    "index": "vlm_log_index",
                    "knnBeta": {
                        "vector": query_vector,
                        "path": "description_embedding",
                        "k": k
                    }
                }
            },
            {"$project": {
                "score": {"$meta": "searchScore"},
                "timestamp": 1,
                "collective_description": 1,
                "event_id": 1
            }},
            {"$limit": k}
        ]
        return list(self.vlm_logs.aggregate(pipeline))

    def close(self):
        self.client.close()
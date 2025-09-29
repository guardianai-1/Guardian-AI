# security_threat_detection.py
import cv2
import time
import multiprocessing
import os
import uuid
import base64
from bson import ObjectId
from ultralytics import YOLO
import numpy as np
import sys  # Added for CLI arg video source override

# --- YOLO Configuration ---
MODEL_PATH = os.getenv('THREAT_MODEL_PATH', 'yolov8s-pose.pt') # Path to your YOLOv8 pose model
IMG_SIZE = int(os.getenv('THREAT_IMG_SIZE', '768'))
DET_CONF = float(os.getenv('THREAT_CONF', '0.15'))

# --- YOLO Keypoint Indices (COCO format) ---
# We only need the torso keypoints for our heuristic
LEFT_SHOULDER, RIGHT_SHOULDER = 5, 6
LEFT_HIP, RIGHT_HIP = 11, 12

# --- Pose Detection Helper (YOLO Version) ---
def is_person_on_ground_yolo(keypoints, frame_height, ground_threshold_percent=0.55):
    """
    A simple heuristic to check if a person is on the ground using YOLO keypoints.
    Checks if the average torso position is in the bottom X% of the frame.
    
    keypoints: A tensor of keypoints for a single person from YOLO results.
               Format: [keypoint_index, [x, y, confidence]]
    """
    # Extract Y coordinates and confidence for major torso parts
    ls_y, ls_conf = keypoints[LEFT_SHOULDER][1], keypoints[LEFT_SHOULDER][2]
    rs_y, rs_conf = keypoints[RIGHT_SHOULDER][1], keypoints[RIGHT_SHOULDER][2]
    lh_y, lh_conf = keypoints[LEFT_HIP][1], keypoints[LEFT_HIP][2]
    rh_y, rh_conf = keypoints[RIGHT_HIP][1], keypoints[RIGHT_HIP][2]

    # Collect Y-coordinates of valid points (confidence > 0.5)
    valid_points_y = []
    if ls_conf > 0.5: valid_points_y.append(ls_y)
    if rs_conf > 0.5: valid_points_y.append(rs_y)
    if lh_conf > 0.5: valid_points_y.append(lh_y)
    if rh_conf > 0.5: valid_points_y.append(rh_y)

    # We need at least two points to make a reasonable guess
    if len(valid_points_y) < 2:
        return False

    # Calculate average Y position and the pixel threshold for the ground
    avg_torso_y = sum(valid_points_y) / len(valid_points_y)
    ground_threshold_pixels = frame_height * ground_threshold_percent
    
    # Return True if the average torso position is below the threshold
    return avg_torso_y > ground_threshold_pixels

# --- Main Process Function ---
def threat_detection_process(start_interaction_event: multiprocessing.Event,
                            db_writer_queue: multiprocessing.Queue,
                            vlm_task_queue: multiprocessing.Queue,
                            vlm_result_queue: multiprocessing.Queue):
    
    print(f"[ThreatDetection PID: {os.getpid()}] Process started.")

    # --- State Management ---
    # Key: YOLO's temporary track_id, Value: dict of our persistent state
    tracked_subjects = {} 
    
    # This dictionary holds the state of the single, system-wide event.
    active_event = {
        'id': None,
        'status': 'inactive',
        'last_vlm_trigger_time': 0,
        'participants': set()
    }

    # --- Configuration ---
    POSE_CONFIRMATION_SEC = 1.0  # How long a pose must be held
    VLM_INTERVAL_SEC = 5.0      # Interval for VLM analysis

    # --- Video Source Resolution (env / CLI / fallback) ---
    chosen_source = None
    # Priority 1: CLI arg (if running via __main__ wrapper)
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        try:
            chosen_source = int(arg)
        except ValueError:
            chosen_source = arg
    # Priority 2: Environment variable
    if chosen_source is None:
        env_source = os.getenv("THREAT_VIDEO_SOURCE")
        if env_source:
            try:
                chosen_source = int(env_source)
            except ValueError:
                chosen_source = env_source
    # Fallback: webcam index 0
    if chosen_source is None:
        chosen_source = 0
    # If it is a string path but does not exist, fallback to 0
    if isinstance(chosen_source, str) and not os.path.exists(chosen_source):
        print(f"[ThreatDetection] Provided source '{chosen_source}' not found. Falling back to webcam 0.")
        chosen_source = 0
    print(f"[ThreatDetection] Using video source: {chosen_source}")

    # --- Model Loading ---
    print(f"[ThreatDetection] Loading YOLO model: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    print("[ThreatDetection] YOLO model loaded successfully.")

    try:
        # --- Main Detection Loop using YOLO's streaming tracker ---
        # The model handles video capture internally. `source=0` is the default webcam.
        # `stream=True` creates a generator for memory-efficient processing.
        # `classes=[0]` tells YOLO to only detect and track 'person' class.
        results_generator = model.track(source=chosen_source, conf=DET_CONF, stream=True, show=True, persist=True, classes=[0], tracker="my_botsort_reid.yaml", imgsz=IMG_SIZE)

        for results in results_generator:
            current_time = time.time()
            frame = results.orig_img
            frame_height, _, _ = frame.shape

            # --- 1. Process Tracking Results ---
            # Check if any persons were tracked in this frame
            if results.boxes.id is None:
                # No detections in this frame; let the tracker handle occlusions via track_buffer (BoT-SORT)
                # Keep tracked_subjects as-is to preserve mapping across brief lapses
                continue
                
            yolo_track_ids = results.boxes.id.int().cpu().tolist()
            all_keypoints = results.keypoints.cpu()
            current_track_ids = set(yolo_track_ids)

            # --- 2. Update Subject States based on Pose ---
            for i, yolo_track_id in enumerate(yolo_track_ids):
                keypoints_for_person = all_keypoints[i].data.squeeze()
                
                # Add new subjects if not already tracked
                if yolo_track_id not in tracked_subjects:
                    persistent_id = f"person_{uuid.uuid4().hex[:8]}"
                    tracked_subjects[yolo_track_id] = {
                        'tracking_id': persistent_id, # Our system's permanent ID
                        'status': 'normal', # normal, pending, suspicious
                        'pose_start_time': None,
                        'is_confirmed_suspicious': False
                    }
                    print(f"[NEW SUBJECT] YOLO ID {yolo_track_id} assigned persistent ID {persistent_id}")
                    # Create the subject record in the DB
                    db_writer_queue.put({
                        'action': 'create_new_subject',
                        'payload': {
                            'tracking_id': persistent_id,
                            'reid_vector': [], # No vector for now
                            'camera_id': 'cam_01'
                        }
                    })

                subject = tracked_subjects[yolo_track_id]
                
                # Check for the suspicious pose
                if is_person_on_ground_yolo(keypoints_for_person, frame_height):
                    if subject['status'] == 'normal':
                        subject['status'] = 'pending'
                        subject['pose_start_time'] = current_time
                    elif subject['status'] == 'pending' and current_time - subject['pose_start_time'] >= POSE_CONFIRMATION_SEC:
                        if not subject.get('is_confirmed_suspicious', False):
                            print(f"CONFIRMED SUSPICIOUS: {subject['tracking_id']} (YOLO ID: {yolo_track_id})")
                            subject['status'] = 'suspicious'
                            subject['is_confirmed_suspicious'] = True
                            # Update status in DB
                            db_writer_queue.put({
                                'action': 'update_subject_status',
                                'payload': {'tracking_id': subject['tracking_id'], 'status': 'suspicious'}
                            })
                else: # Person is NOT on the ground
                    if subject['status'] != 'normal':
                        print(f"POSE NORMAL: {subject['tracking_id']} (YOLO ID: {yolo_track_id})")
                        subject['status'] = 'normal'
                        subject['pose_start_time'] = None
                        subject['is_confirmed_suspicious'] = False
                        # Update status in DB
                        db_writer_queue.put({
                            'action': 'update_subject_status',
                            'payload': {'tracking_id': subject['tracking_id'], 'status': 'normal'}
                        })

            # --- 3. Clean up lost tracks ---
            lost_track_ids = set(tracked_subjects.keys()) - current_track_ids
            for lost_id in lost_track_ids:
                print(f"[TRACK LOST] Person with YOLO ID: {lost_id} has left the scene.")
                del tracked_subjects[lost_id]

            # --- 4. Manage the Global Event Lifecycle (This logic is unchanged) ---
            suspicious_subjects = [s for s in tracked_subjects.values() if s['status'] == 'suspicious']

            # START a new event
            if suspicious_subjects and active_event['status'] == 'inactive':
                print("EVENT START: At least one subject is suspicious.")
                active_event['status'] = 'active'
                active_event['id'] = ObjectId()
                
                first_participant_id = suspicious_subjects[0]['tracking_id']
                active_event['participants'].add(first_participant_id)

                db_writer_queue.put({
                    'action': 'create_event',
                    'payload': {'event_id': active_event['id'], 'start_camera_id': 'cam_01', 'participant_tracking_id': first_participant_id}
                })
                active_event['last_vlm_trigger_time'] = 0

            # END an existing event
            if not suspicious_subjects and active_event['status'] == 'active':
                print(f"EVENT END: No more suspicious subjects. Closing event {active_event['id']}.")
                db_writer_queue.put({
                    'action': 'end_event',
                    'payload': {'event_id': active_event['id']}
                })
                active_event['status'] = 'inactive'
                active_event['id'] = None
                active_event['participants'].clear()

            # CONTINUOUS VLM ANALYSIS
            if active_event['status'] == 'active' and current_time - active_event['last_vlm_trigger_time'] >= VLM_INTERVAL_SEC:
                print(f"VLM TRIGGER: 5-second interval for event {active_event['id']}.")
                
                current_participant_ids = {s['tracking_id'] for s in suspicious_subjects}
                new_participants = current_participant_ids - active_event['participants']

                for tracking_id in new_participants:
                    db_writer_queue.put({
                        'action': 'add_participant_to_event',
                        'payload': {'event_id': active_event['id'], 'tracking_id': tracking_id}
                    })
                    active_event['participants'].add(tracking_id)

                _, buffer = cv2.imencode('.jpg', frame)
                jpg_as_text = base64.b64encode(buffer).decode('utf-8')

                vlm_task_queue.put({
                    'task': 'analyze_threat',
                    'payload': {
                        'event_id': active_event['id'],
                        'subjects': [{'tracking_id': s['tracking_id']} for s in suspicious_subjects],
                        'base64_frame': jpg_as_text
                    }
                })
                active_event['last_vlm_trigger_time'] = current_time

    except KeyboardInterrupt:
        pass
    finally:
        # No cap.release() needed as YOLO's generator handles stream closure
        print(f"[ThreatDetection PID: {os.getpid()}] Process stopped. Cleaning up.")

if __name__ == "__main__":
    multiprocessing.set_start_method('spawn', force=True)
    start_event = multiprocessing.Event()
    db_writer_queue = multiprocessing.Queue()
    vlm_task_queue = multiprocessing.Queue()
    vlm_result_queue = multiprocessing.Queue()

    # Simple stub consumer so db queue does not block/grow unbounded
    import threading
    def _db_stub():
        while True:
            item = db_writer_queue.get()
            if item == "__STOP__":
                break
            print(f"[DB-STUB] {item}")
    threading.Thread(target=_db_stub, daemon=True).start()

    print("[MAIN] Starting threat_detection_process. Set THREAT_VIDEO_SOURCE or pass a CLI arg to change input.")
    threat_detection_process(start_event, db_writer_queue, vlm_task_queue, vlm_result_queue)

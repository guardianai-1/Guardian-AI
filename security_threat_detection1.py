# security_threat_detection.py
import cv2
import mediapipe as mp
import time
import multiprocessing
import os
import uuid
import base64
from bson import ObjectId # <-- Add this import

# --- MediaPipe Setup ---
BaseOptions = mp.tasks.BaseOptions
VisionRunningMode = mp.tasks.vision.RunningMode

# For gestures (hands) - used in ATTRACT state
GestureRecognizer = mp.tasks.vision.GestureRecognizer
GestureRecognizerOptions = mp.tasks.vision.GestureRecognizerOptions

# For body detection - used in ATTRACT state
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions

# For detailed hand tracking - used in INTERACTIVE state
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
HandLandmark = mp.solutions.hands.HandLandmark # For landmark indices

# --- Pose Detection Helper ---
def is_person_on_ground(landmarks, frame_height):
    """
    A simple heuristic to check if a person is on the ground.
    Checks if key landmarks (shoulders, hips) are in the bottom X% of the frame.
    """
    if not landmarks:
        return False
    
    # Get Y coordinates of major body parts
    left_shoulder_y = landmarks[mp.solutions.pose.PoseLandmark.LEFT_SHOULDER.value].y
    right_shoulder_y = landmarks[mp.solutions.pose.PoseLandmark.RIGHT_SHOULDER.value].y
    left_hip_y = landmarks[mp.solutions.pose.PoseLandmark.LEFT_HIP.value].y
    right_hip_y = landmarks[mp.solutions.pose.PoseLandmark.RIGHT_HIP.value].y
    
    # Average Y position of the torso
    avg_torso_y = (left_shoulder_y + right_shoulder_y + left_hip_y + right_hip_y) / 4
    
    # If the average torso position is in the bottom 25% of the frame,
    # we consider them "on the ground". This is a simple but effective heuristic.
    # Note: Y values from MediaPipe are normalized (0.0 at top, 1.0 at bottom).
    return avg_torso_y > 0.75

# --- Main Process Function ---
def threat_detection_process(start_interaction_event: multiprocessing.Event,
                            db_writer_queue: multiprocessing.Queue,
                            vlm_task_queue: multiprocessing.Queue,
                            vlm_result_queue: multiprocessing.Queue):
    
    print(f"[ThreatDetection PID: {os.getpid()}] Process started.")

    # --- State Management ---
    # This dictionary holds the state for every person currently visible.
    # Key: person_index (from pose detection), Value: dict of state
    tracked_subjects = {} 
    
    # This dictionary holds the state of the single, system-wide event.
    active_event = {
        'id': None,
        'status': 'inactive',
        'last_vlm_trigger_time': 0,
        'participants': set() # <-- Add a set to track participants
    }

    # --- Configuration ---
    POSE_CONFIRMATION_SEC = 1.0  # How long a pose must be held
    VLM_INTERVAL_SEC = 5.0      # Interval for VLM analysis

    # --- Model Loading & Camera Setup (as before) ---
    # ... (load pose_recognizer, open cap) ...
    pose_model_path = '/home/zero/iris/pose_landmarker.task'
    pose_options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=pose_model_path),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_poses=6 # Track up to 6 people
    )
    pose_recognizer = mp.tasks.vision.PoseLandmarker.create_from_options(pose_options)
    cap = cv2.VideoCapture(0)
    # ...

    try:
        while True:
            success, frame = cap.read()
            if not success: continue
            frame_height, frame_width, _ = frame.shape
            
            # --- 1. Detect Poses ---
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            pose_results = pose_recognizer.detect(mp_image)

            current_time = time.time()
            detected_person_indices = set(range(len(pose_results.pose_landmarks)))

            # --- 2. Update Subject States based on Pose ---
            for person_idx in detected_person_indices:
                landmarks = pose_results.pose_landmarks[person_idx]
                
                # Add new subjects if not already tracked
                if person_idx not in tracked_subjects:
                    tracked_subjects[person_idx] = {
                        'tracking_id': f"person_{uuid.uuid4().hex[:8]}",
                        'status': 'normal', # normal, pending, suspicious
                        'pose_start_time': None
                    }
                    # Create the subject record in the DB
                    db_writer_queue.put({
                        'action': 'create_new_subject',
                        'payload': {
                            'tracking_id': tracked_subjects[person_idx]['tracking_id'],
                            'reid_vector': [], # No vector for now
                            'camera_id': 'cam_01'
                        }
                    })

                subject = tracked_subjects[person_idx]
                
                # Check for the suspicious pose
                if is_person_on_ground(landmarks, frame_height):
                    if subject['status'] == 'normal':
                        subject['status'] = 'pending'
                        subject['pose_start_time'] = current_time
                    elif subject['status'] == 'pending' and current_time - subject['pose_start_time'] >= POSE_CONFIRMATION_SEC:
                        if subject.get('is_confirmed_suspicious', False) is False:
                            print(f"CONFIRMED SUSPICIOUS: {subject['tracking_id']}")
                            subject['status'] = 'suspicious'
                            subject['is_confirmed_suspicious'] = True
                            # Update status in DB
                            db_writer_queue.put({
                                'action': 'update_subject_status',
                                'payload': {'tracking_id': subject['tracking_id'], 'status': 'suspicious'}
                            })
                else: # Person is NOT on the ground
                    if subject['status'] != 'normal':
                        print(f"POSE NORMAL: {subject['tracking_id']}")
                        subject['status'] = 'normal'
                        subject['pose_start_time'] = None
                        subject['is_confirmed_suspicious'] = False
                        # Update status in DB
                        db_writer_queue.put({
                            'action': 'update_subject_status',
                            'payload': {'tracking_id': subject['tracking_id'], 'status': 'normal'}
                        })

            # --- 3. Manage the Global Event Lifecycle ---
            suspicious_subjects = [s for s in tracked_subjects.values() if s['status'] == 'suspicious']

            # --- START a new event ---
            if suspicious_subjects and active_event['status'] == 'inactive':
                print("EVENT START: At least one subject is suspicious.")
                active_event['status'] = 'active'
                # Generate the ObjectId here, this will be the one true ID
                active_event['id'] = ObjectId() # <-- Generate a BSON ObjectId
                
                first_participant_id = suspicious_subjects[0]['tracking_id']
                active_event['participants'].add(first_participant_id)

                db_writer_queue.put({
                    'action': 'create_event',
                    # Pass the pre-generated ID in the payload
                    'payload': {'event_id': active_event['id'], 'start_camera_id': 'cam_01', 'participant_tracking_id': first_participant_id}
                })
                # Force an immediate VLM trigger
                active_event['last_vlm_trigger_time'] = 0

            # --- END an existing event ---
            if not suspicious_subjects and active_event['status'] == 'active':
                print(f"EVENT END: No more suspicious subjects. Closing event {active_event['id']}.")
                db_writer_queue.put({
                    'action': 'end_event',
                    'payload': {'event_id': active_event['id']}
                })
                active_event['status'] = 'inactive'
                active_event['id'] = None

            # --- CONTINUOUS VLM ANALYSIS (5-second interval) ---
            if active_event['status'] == 'active' and current_time - active_event['last_vlm_trigger_time'] >= VLM_INTERVAL_SEC:
                print(f"VLM TRIGGER: 5-second interval for event {active_event['id']}.")
                
                # Add any new suspicious participants to the event record
                current_participant_ids = {s['tracking_id'] for s in suspicious_subjects}
                new_participants = current_participant_ids - active_event['participants']

                for tracking_id in new_participants:
                    db_writer_queue.put({
                        'action': 'add_participant_to_event',
                        'payload': {'event_id': active_event['id'], 'tracking_id': tracking_id}
                    })
                    active_event['participants'].add(tracking_id)

                # Encode the current frame
                _, buffer = cv2.imencode('.jpg', frame)
                jpg_as_text = base64.b64encode(buffer).decode('utf-8')

                # Send the analysis task
                vlm_task_queue.put({
                    'task': 'analyze_threat',
                    'payload': {
                        'event_id': active_event['id'],
                        'subjects': [{'tracking_id': s['tracking_id']} for s in suspicious_subjects],
                        'base64_frame': jpg_as_text # <-- Pass the relevant frame
                    }
                })
                active_event['last_vlm_trigger_time'] = current_time


    except KeyboardInterrupt:
        pass
    finally:
        print(f"[ThreatDetection PID: {os.getpid()}] Cleaning up.")
        cap.release()

        
# ...existing code...

if __name__ == "__main__":
    import threading
    multiprocessing.set_start_method("spawn", force=True)  # macOS-safe

    start_event = multiprocessing.Event()
    db_writer_queue = multiprocessing.Queue()
    vlm_task_queue = multiprocessing.Queue()
    vlm_result_queue = multiprocessing.Queue()

    # Stub consumers so queues don’t block
    def _db_consumer():
        while True:
            item = db_writer_queue.get()
            if item == "__STOP__":
                break
            print(f"[DB-STUB] {item}")

    def _vlm_worker():
        while True:
            task = vlm_task_queue.get()
            if task == "__STOP__":
                break
            # Simulate analysis result
            vlm_result_queue.put({
                "event_id": task["payload"]["event_id"],
                "summary": "ok"
            })

    threading.Thread(target=_db_consumer, daemon=True).start()
    threading.Thread(target=_vlm_worker, daemon=True).start()

    try:
        threat_detection_process(
            start_event, db_writer_queue, vlm_task_queue, vlm_result_queue
        )
    except KeyboardInterrupt:
        pass
    finally:
        db_writer_queue.put("__STOP__")
        vlm_task_queue.put("__STOP__")
        print("[MAIN] Exiting.")
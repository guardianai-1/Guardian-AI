# yolo_threat_detector_standalone_streaming.py

import cv2
from ultralytics import YOLO
import time
import uuid
import numpy as np
import os
import sys

# --- Configuration ---
def _resolve_video_source():
    # CLI arg takes precedence: python script.py [source]
    # Accept integer index (e.g., 0) or file path/URL.
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        try:
            return int(arg)
        except ValueError:
            return arg
    # Environment variable fallback
    env = os.getenv("VIDEO_SOURCE")
    if env:
        try:
            return int(env)
        except ValueError:
            return env
    # Default to webcam 0
    return 0

VIDEO_SOURCE = _resolve_video_source()
MODEL_VARIANT = 'yolov8n-pose.pt'
POSE_CONFIRMATION_SEC = 1.0
GROUND_THRESHOLD_PERCENT = 0.55

# --- Keypoint Indices ---
LEFT_SHOULDER, RIGHT_SHOULDER = 5, 6
LEFT_HIP, RIGHT_HIP = 11, 12

# --- Pose Detection Helper (No changes needed) ---
def is_person_on_ground_yolo(keypoints, frame_height):
    ls_y, ls_conf = keypoints[LEFT_SHOULDER][1], keypoints[LEFT_SHOULDER][2]
    rs_y, rs_conf = keypoints[RIGHT_SHOULDER][1], keypoints[RIGHT_SHOULDER][2]
    lh_y, lh_conf = keypoints[LEFT_HIP][1], keypoints[LEFT_HIP][2]
    rh_y, rh_conf = keypoints[RIGHT_HIP][1], keypoints[RIGHT_HIP][2]

    valid_points = []
    if ls_conf > 0.5: valid_points.append(ls_y)
    if rs_conf > 0.5: valid_points.append(rs_y)
    if lh_conf > 0.5: valid_points.append(lh_y)
    if rh_conf > 0.5: valid_points.append(rh_y)

    if len(valid_points) < 2:
        return False

    avg_torso_y = sum(valid_points) / len(valid_points)
    ground_threshold_pixels = frame_height * GROUND_THRESHOLD_PERCENT
    return avg_torso_y > ground_threshold_pixels

# --- Main Detection and Logic Function ---
def run_threat_detection():
    print("--- Starting YOLOv8 Threat Detection Test ---")
    
    print(f"Loading model: {MODEL_VARIANT}...")
    model = YOLO(MODEL_VARIANT)
    print("Model loaded successfully.")

    # --- State Management (No changes needed) ---
    tracked_subjects = {} 
    active_event = {'id': None, 'status': 'inactive', 'participants': set()}

    print("Processing video... Press 'q' to quit.")
    
    # --- NEW: Use YOLO's streaming mode ---
    # The model will handle video reading internally.
    # `stream=True` makes it a generator, yielding results frame by frame.
    results_generator = model.track(source=VIDEO_SOURCE, conf=0.2, stream=True, show=True, persist=True, classes=[0], tracker="my_botsort_reid.yaml")#, imgsz=768)

    for results in results_generator:
        # `results` is a single frame's result object
        current_time = time.time()
        
        # Get the original frame and annotated frame
        frame = results.orig_img
        annotated_frame = results.plot() # This has the default YOLO annotations
        
        frame_height, frame_width, _ = frame.shape

        # --- 2. Process Tracking Results (Logic is mostly the same) ---
        if results.boxes.id is not None:
            track_ids = results.boxes.id.int().cpu().tolist()
            boxes = results.boxes.xyxy.cpu()
            #all_keypoints = results.keypoints.cpu()
            
            # --- 3. Update Subject States based on Pose ---
        """
            for i, track_id in enumerate(track_ids):
                keypoints_for_person = all_keypoints[i].data.squeeze()
                if track_id not in tracked_subjects:
                    tracked_subjects[track_id] = {
                        'tracking_id': track_id,
                        'status': 'normal',
                        'pose_start_time': None,
                        'is_confirmed_suspicious': False
                    }
                    print(f"[NEW SUBJECT] Detected Person with Tracking ID: {track_id}")

                subject = tracked_subjects[track_id]
                
                if is_person_on_ground_yolo(keypoints_for_person, frame_height):
                    if subject['status'] == 'normal':
                        subject['status'] = 'pending'
                        subject['pose_start_time'] = current_time
                    elif subject['status'] == 'pending' and current_time - subject['pose_start_time'] >= POSE_CONFIRMATION_SEC:
                        if not subject['is_confirmed_suspicious']:
                            subject['status'] = 'suspicious'
                            subject['is_confirmed_suspicious'] = True
                            print(f"🔥🔥🔥 [CONFIRMED SUSPICIOUS] Tracking ID: {track_id} is on the ground! 🔥🔥🔥")
                else:
                    if subject['status'] != 'normal':
                        print(f"✅ [POSE NORMAL] Tracking ID: {track_id} is now normal.")
                        subject['status'] = 'normal'
                        subject['pose_start_time'] = None
                        subject['is_confirmed_suspicious'] = False
            
            # --- 4. Clean up lost tracks ---
            current_track_ids = set(track_ids)
            lost_track_ids = set(tracked_subjects.keys()) - current_track_ids
            for lost_id in lost_track_ids:
                print(f"[TRACK LOST] Person with Tracking ID: {lost_id} has left the scene.")
                del tracked_subjects[lost_id]
            
        # --- 5. Manage the Global Event Lifecycle (No changes needed) ---
        suspicious_subjects = [s for s in tracked_subjects.values() if s['status'] == 'suspicious']
        if suspicious_subjects and active_event['status'] == 'inactive':
            active_event['status'] = 'active'
            active_event['id'] = f"event_{uuid.uuid4().hex[:8]}"
            active_event['participants'] = {s['tracking_id'] for s in suspicious_subjects}
            print(f"\n🚨🚨🚨 [EVENT START] Event '{active_event['id']}' triggered. 🚨🚨🚨\n")
        elif not suspicious_subjects and active_event['status'] == 'active':
            print(f"\n✅ [EVENT END] Closing event '{active_event['id']}'.\n")
            active_event['status'] = 'inactive'
            active_event['id'] = None
            active_event['participants'].clear()
        elif suspicious_subjects and active_event['status'] == 'active':
            current_participants = {s['tracking_id'] for s in suspicious_subjects}
            new_participants = current_participants - active_event['participants']
            if new_participants:
                for pid in new_participants:
                    print(f"➕ [EVENT UPDATE] Person {pid} has joined event '{active_event['id']}'.")
                    active_event['participants'].add(pid)

        # --- 6. Add Custom Visualization (No changes needed) ---
        # We draw on top of the `annotated_frame` from `results.plot()`
        
        if results.boxes.id is not None:
            for subject in tracked_subjects.values():
                try:
                    idx = track_ids.index(subject['tracking_id'])
                    box = boxes[idx]
                    x1, y1, _, _ = map(int, box)
                    status = subject['status'].upper()
                    color = {'normal': (0, 255, 0), 'pending': (0, 255, 255), 'suspicious': (0, 0, 255)}[subject['status']]
                    cv2.putText(annotated_frame, f"ID: {subject['tracking_id']} - {status}", 
                                (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                except ValueError:
                    pass
        
        # Display the annotated frame
        cv2.imshow("YOLOv8 Threat Detection", annotated_frame)

        if cv2.waitKey(1) & 0xFF == ord("q"): # Use waitKey(1) for video
            break
        """

    # --- Cleanup ---
    cv2.destroyAllWindows()
    print("--- Detection finished. ---")

if __name__ == "__main__":
    run_threat_detection()

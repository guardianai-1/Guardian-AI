# security_worker.py (MODIFICATIONS)

import multiprocessing
import time
import os
from queue import Empty
import base64
import io
from dotenv import load_dotenv
import mss
from queue import Full
from PIL import Image
import wave # + ADD THIS STANDARD LIBRARY
import numpy as np # + ADD THIS FOR GETTING SAMPLE WIDTH

# --- NEW: AI and Utility Imports for OpenAI ---
from openai import OpenAI

load_dotenv()

# --- Helper Function to encode images ---
def encode_image_to_base64(image):
    """Encodes a PIL Image to a base64 string."""
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

# --- NEW: OpenAI Inference Functions ---

# --- NEW: VLM Analysis Function ---
def run_vlm_analysis(client, payload, db_writer_queue, vlm_result_queue):
    """
    Simulates running VLM on a threat, logging the result, and suggesting actions.
    """
    print(f"[BackgroundWorker] Received VLM task for event {payload['event_id']}")

    event_id = payload['event_id']
    subjects_in_log = [s['tracking_id'] for s in payload['subjects']]
    
    # 1. Generate a description and embedding
    prompt = f"Analyze the following subjects: {', '.join(subjects_in_log)}. What are each of them doing? Keep it extremely short."
    embedding = np.random.rand(256).tolist() # Dummy embedding

    try:

        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            try:
                sct_img = sct.grab(monitor)
                pil_image = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                latest_frame = pil_image
            except Full:
                # This is fine, just means the worker is busy. We drop the frame.
                pass
            except Exception as e:
                print(f"[LocalManager] Error in screen capture: {e}")
        
        print("[BackgroundWorker] Streaming LLM response to db queue...")
        
        base64_image = encode_image_to_base64(latest_frame)

        description = client.chat.completions.create(
            #model="gpt-4-turbo",
            model="gpt-4o", # gpt-4o is faster and cheaper
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{payload['base64_frame']}"
                            },
                        },
                    ],
                }
            ],
            stream=False,
        )
        description = description.choices[0].message.content.strip()
        print("[BackgroundWorker] LLM text output:", description)

        db_writer_queue.put({
            'action': 'add_vlm_log',
            'payload': {
                'event_id': event_id,
                'camera_id': 'cam_01',
                'description': description,
                'embedding': embedding,
                'subjects': subjects_in_log
            }
        })
        print(f"[BackgroundWorker] Sent VLM log for event {event_id} to DB writer.")
    except Exception as e:
        print(f"[BackgroundWorker] ERROR: {e}")

    
    # 3. VLM DOESNT CLEAR ANYONE YET!!
    #Simulate a decision. Maybe the VLM decides the threat is over.
    #if np.random.rand() > 0.5: # 50% chance of clearing the flag
    #    tracking_id_to_clear = subjects_in_log[0]
    #    print(f"[BackgroundWorker] VLM suggests clearing flag for {tracking_id_to_clear}.")
    #    vlm_result_queue.put({
    #        'action': 'suggest_clear_flag',
    #        'tracking_id': tracking_id_to_clear
    #    })


# --- MODIFIED: The Main Worker Process Function ---
def background_ai_worker(task_queue: multiprocessing.Queue,
                         stt_audio_queue: multiprocessing.Queue,
                         result_queue: multiprocessing.Queue,
                         conversation_active_event: multiprocessing.Event,
                         screen_frame_queue: multiprocessing.Queue,
                         audio_queue: multiprocessing.Queue,
                         start_interaction_event: multiprocessing.Event,
                         audio_interrupt_event: multiprocessing.Event,
                         # --- NEW ARGUMENTS ---
                         db_writer_queue: multiprocessing.Queue,
                         vlm_task_queue: multiprocessing.Queue,
                         vlm_result_queue: multiprocessing.Queue):

    print(f"[BackgroundWorker PID: {os.getpid()}] Process started.")

    # --- OpenAI Client Setup (Done ONCE) ---
    try:
        client = OpenAI() # The client automatically uses the OPENAI_API_KEY env var
        print("[BackgroundWorker] OpenAI client initialized successfully.")
    except Exception as e:
        print(f"[BackgroundWorker] FATAL: Could not initialize OpenAI client. Exiting. Error: {e}")
        return

    while True:
        current_time = time.time()
        try:
            task = task_queue.get_nowait()
            print(f"[BackgroundWorker] Received task: {task['task']}")

            if task['task'] == 'shutdown':
                print("[BackgroundWorker] Shutdown signal received.")
                break
        except Empty:
            pass
        # --- NEW: Check for VLM tasks ---
        try:
            vlm_task = vlm_task_queue.get_nowait()
            if vlm_task is None: # Shutdown signal
                break
            
            if vlm_task.get('task') == 'analyze_threat':
                run_vlm_analysis(client, vlm_task['payload'], db_writer_queue, vlm_result_queue)
        except Empty:
            pass # No VLM task, continue

        
        time.sleep(0.2)

    print(f"[BackgroundWorker PID: {os.getpid()}] Shutting down.")
# security_db_writer.py
import multiprocessing
import os
from queue import Empty

# Assume db_queries.py and config.py are in the same directory or accessible
from security_db_queries import DatabaseManager
from security_config import MONGO_URI, DB_NAME

def db_writer_process(db_queue: multiprocessing.Queue):
    """
    A dedicated process to handle all database write operations.
    It consumes tasks from a queue and executes them.
    """
    print(f"[DB_Writer PID: {os.getpid()}] Process started.")
    
    try:
        db_manager = DatabaseManager(MONGO_URI, DB_NAME)
        print("[DB_Writer] DatabaseManager initialized successfully.")
    except Exception as e:
        print(f"[DB_Writer] FATAL: Could not initialize DatabaseManager. Exiting. Error: {e}")
        return

    while True:
        try:
            # Block until a task is available
            task = db_queue.get()

            if task is None or task.get('action') == 'shutdown':
                print("[DB_Writer] Shutdown signal received.")
                break

            action = task.get('action')
            payload = task.get('payload', {})
            
            # --- Route tasks to the appropriate DB method ---
            if action == 'create_event':
                db_manager.create_event(**payload)
            elif action == 'add_participant_to_event':
                db_manager.add_participant_to_event(**payload)
            elif action == 'end_event':
                db_manager.end_event(**payload)
            elif action == 'add_vlm_log':
                db_manager.add_vlm_log(**payload)
            elif action == 'create_new_subject':
                db_manager.create_new_subject(**payload)
            elif action == 'update_subject_status':
                db_manager.update_subject_status(**payload)
            else:
                print(f"[DB_Writer] WARNING: Unknown action received: {action}")

        except Empty:
            # This part of the loop is not strictly needed with a blocking get(),
            # but it's good practice if you switch to a non-blocking get.
            continue
        except Exception as e:
            print(f"[DB_Writer] ERROR processing task '{task}': {e}")

    db_manager.close()
    print(f"[DB_Writer PID: {os.getpid()}] Shutting down.")
# security_orchestrator.py (MODIFICATIONS)
import multiprocessing
import asyncio
import time
import os

# Import the entrypoint functions from our other scripts
from security_threat_detection import threat_detection_process
from security_worker import background_ai_worker
from audio_manager_prod import audio_manager_entrypoint
from  listener_prod import local_agent_entrypoint

from security_db_writer import db_writer_process


INTERACTIVE_MODE_DURATION_SECONDS = 600


async def main():
    # ...
    # 1. Create communication channels
    task_queue = multiprocessing.Queue()
    result_queue = multiprocessing.Queue()
    
    ### NEW QUEUES ###
    db_writer_queue = multiprocessing.Queue()
    vlm_task_queue = multiprocessing.Queue()
    vlm_result_queue = multiprocessing.Queue()
    ### END NEW ###

    conversation_active_event = multiprocessing.Event()
    start_interaction_event = multiprocessing.Event()
    screen_frame_queue = multiprocessing.Queue(maxsize=5)
    stt_audio_queue = multiprocessing.Queue() # + CREATE THE NEW QUEUE
    # +++ ADD NEW CHANNELS FOR AUDIO +++
    audio_queue = multiprocessing.Queue()
    audio_interrupt_event = multiprocessing.Event()

    # 2. Define the processes
    processes = {
        "threat_detection": multiprocessing.Process(
            target=threat_detection_process,
            ### MODIFIED: Pass new queues ###
            args=(start_interaction_event, db_writer_queue, vlm_task_queue, vlm_result_queue)
        ),
        "background": multiprocessing.Process(
            target=background_ai_worker,
            ### MODIFIED: Pass new queues ###
            args=(task_queue, stt_audio_queue, result_queue, conversation_active_event, 
                  screen_frame_queue, audio_queue, start_interaction_event, audio_interrupt_event,
                  db_writer_queue, vlm_task_queue, vlm_result_queue)
        ),
        ### NEW PROCESS ###
        "db_writer": multiprocessing.Process(
            target=db_writer_process,
            args=(db_writer_queue,)
        ),
        "local_agent": multiprocessing.Process(
            target=local_agent_entrypoint,
            # ~ MODIFY: Pass the new audio interrupt event
            args=(conversation_active_event, audio_interrupt_event, stt_audio_queue) # + PASS IT HERE)
        ),
        # + ADD THE NEW AUDIO MANAGER PROCESS
        "audio": multiprocessing.Process(
            target=audio_manager_entrypoint,
            args=(audio_queue, audio_interrupt_event)
        )
    }

    try:
        # 3. Start the persistent processes
        processes["threat_detection"].start()
        processes["background"].start()
        processes["audio"].start()
        processes["db_writer"].start() ### NEW ###
        processes["local_agent"].start()

        print("Orchestrator: Threat Detection, Background, Audio, Local Agent, and DB_Writer processes started.")

        await asyncio.sleep(INTERACTIVE_MODE_DURATION_SECONDS)

    except KeyboardInterrupt:
        print("\nOrchestrator: Keyboard interrupt received. Shutting down.")
    # ...
    finally:
        print("\nOrchestrator: Cleaning up all processes...")
        task_queue.put({'task': 'shutdown'})
        audio_queue.put(None)
        db_writer_queue.put({'action': 'shutdown'}) ### NEW ###
        vlm_task_queue.put(None) ### NEW ###

        for name, p in processes.items():
            if p.is_alive():
                print(f"Orchestrator: Terminating process '{name}' (PID: {p.pid})...")
                p.terminate()
                p.join(timeout=5)
                if p.is_alive():
                    print(f"Orchestrator: Process '{name}' did not terminate gracefully, killing.")
                    p.kill()

    print("Orchestrator: Shutdown complete.")

if __name__ == "__main__":
    multiprocessing.set_start_method('spawn', force=True)
    asyncio.run(main())
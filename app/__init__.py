from flask import Flask
import os
from collections import deque
import threading
import time

def create_app():
    app = Flask(__name__)
    
    # Set secret key for session management
    app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
    
    # Initialize job queue and metadata storage
    app.job_queue = deque()
    app.jobs_metadata = {}
    
    # Register blueprints
    from app.routes import main
    app.register_blueprint(main)
    
    def background_worker():
        """Background worker to process jobs from the queue."""
        while True:
            if app.job_queue:
                # Get the next job from the queue
                job_id = app.job_queue.popleft()
                job = app.jobs_metadata.get(job_id)
                
                if job:
                    # Initialize logs list
                    job['logs'] = []
                    
                    # Update status to Running
                    job['status'] = 'Running'
                    job['start_time'] = time.time()
                    job['logs'].append(f"[{job_id}] Job started")
                    
                    # Simulate job execution with logs
                    for i in range(1, 6):  # 5 steps
                        time.sleep(2)  # Simulate work
                        log_message = f"[{job_id}] Step {i} complete"
                        job['logs'].append(log_message)
                    
                    # Update status to Completed
                    job['status'] = 'Completed'
                    job['end_time'] = time.time()
                    job['logs'].append(f"[{job_id}] Job completed successfully")
            
            # Sleep briefly to prevent CPU overuse
            time.sleep(1)
    
    # Start the background worker thread
    worker_thread = threading.Thread(target=background_worker, daemon=True)
    worker_thread.start()
    
    return app 
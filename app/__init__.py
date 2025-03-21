from flask import Flask
import os
from collections import deque
import threading
import time
import subprocess
from datetime import datetime

def check_docker_running():
    """Check if Docker daemon is running."""
    try:
        subprocess.run(['docker', 'info'], capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False

def detect_framework(job_path):
    """Detect the ML framework used in the project based on dependencies."""
    # Check for requirements.txt
    req_path = os.path.join(job_path, 'requirements.txt')
    if os.path.exists(req_path):
        with open(req_path, 'r') as f:
            content = f.read().lower()
            if 'torch' in content or 'pytorch' in content:
                return 'pytorch'
            if 'tensorflow' in content:
                return 'tensorflow'
    
    # Check for environment.yml
    env_path = os.path.join(job_path, 'environment.yml')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            content = f.read().lower()
            if 'torch' in content or 'pytorch' in content:
                return 'pytorch'
            if 'tensorflow' in content:
                return 'tensorflow'
    
    # Default to Python if no specific framework is detected
    return 'python'

def get_base_image(framework):
    """Get the appropriate Docker base image for the framework."""
    images = {
        'pytorch': 'pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime',
        'tensorflow': 'tensorflow/tensorflow:2.14.0-gpu',
        'python': 'python:3.10-slim'
    }
    return images.get(framework, 'python:3.10-slim')

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
    
    def run_docker_container(job_id, workspace_dir):
        """Run the job in a Docker container and stream logs."""
        # First check if Docker is running
        if not check_docker_running():
            return False, "Docker daemon is not running. Please start Docker and try again."
        
        # Store original Windows path for local operations
        original_workspace_dir = os.path.abspath(workspace_dir)
        
        # Convert path for Docker only when needed
        docker_workspace_dir = original_workspace_dir
        if os.name == 'nt':  # Windows
            docker_workspace_dir = original_workspace_dir.replace('\\', '/')
            # Convert drive letter format (e.g., C:/path) to Docker format (/c/path)
            if docker_workspace_dir[1] == ':':
                docker_workspace_dir = '/' + docker_workspace_dir[0].lower() + docker_workspace_dir[2:]

        print(f"Original workspace directory: {original_workspace_dir}")
        print(f"Docker workspace directory: {docker_workspace_dir}")
        
        # List all files in the workspace directory using original path
        try:
            files = os.listdir(original_workspace_dir)
            print(f"Files in workspace: {files}")
        except FileNotFoundError:
            return False, f"Workspace directory not found: {original_workspace_dir}"
        
        # Detect framework and get appropriate base image
        framework = detect_framework(original_workspace_dir)
        base_image = get_base_image(framework)
        app.jobs_metadata[job_id]['framework'] = framework
        
        # Check for run.sh or main.py using original path
        if os.path.exists(os.path.join(original_workspace_dir, 'run.sh')):
            cmd = ['docker', 'run', '--rm', '--gpus', 'all',
                   '-v', f'{docker_workspace_dir}:/app',
                   '-w', '/app',
                   base_image,
                   'bash', '-c', 'chmod +x run.sh && stdbuf -o0 ./run.sh']
        elif os.path.exists(os.path.join(original_workspace_dir, 'main.py')):
            # Check if requirements.txt exists
            if os.path.exists(os.path.join(original_workspace_dir, 'requirements.txt')):
                # Install requirements and run the script
                cmd = ['docker', 'run', '--rm', '--gpus', 'all',
                       '-v', f'{docker_workspace_dir}:/app',
                       '-w', '/app',
                       base_image,
                       'bash', '-c', 'pip install -r requirements.txt && stdbuf -o0 python3 -u main.py']
            else:
                # Run the script without installing requirements
                cmd = ['docker', 'run', '--rm', '--gpus', 'all',
                       '-v', f'{docker_workspace_dir}:/app',
                       '-w', '/app',
                       base_image,
                       'bash', '-c', 'stdbuf -o0 python3 -u main.py']
        else:
            return False, "No run.sh or main.py found in repository"
        
        try:
            print(f"Starting Docker container with command: {' '.join(cmd)}")
            
            # First, check if we need to pull the image
            inspect_process = subprocess.Popen(
                ['docker', 'inspect', base_image],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            inspect_process.wait()
            
            if inspect_process.returncode != 0:
                # Image doesn't exist locally, pull it
                app.jobs_metadata[job_id]['logs'].append(f"Pulling Docker image: {base_image}")
                pull_process = subprocess.Popen(
                    ['docker', 'pull', base_image],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                    universal_newlines=True
                )
                
                # Capture pull logs in real-time
                while True:
                    line = pull_process.stdout.readline()
                    if not line and pull_process.poll() is not None:
                        break
                    if line:
                        log_line = line.strip()
                        app.jobs_metadata[job_id]['logs'].append(log_line)
                        print(f"Added pull log line for job {job_id}: {log_line}")
        
            # Start the Docker container with a timeout
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                universal_newlines=True
            )
            
            print(f"Initial logs array length: {len(app.jobs_metadata[job_id]['logs'])}")
            
            # Read output line by line
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    # Append log line to job metadata
                    log_line = line.strip()
                    app.jobs_metadata[job_id]['logs'].append(log_line)
                    print(f"Added log line for job {job_id}: {log_line}")
                    print(f"Current logs array length: {len(app.jobs_metadata[job_id]['logs'])}")
            
            # Get the exit code
            exit_code = process.poll()
            app.jobs_metadata[job_id]['exit_code'] = exit_code
            print(f"Job {job_id} completed with exit code {exit_code}")
            print(f"Final logs array length: {len(app.jobs_metadata[job_id]['logs'])}")
            
            return exit_code == 0, f"Job completed with exit code {exit_code}"
            
        except subprocess.TimeoutExpired:
            process.kill()
            return False, "Job timed out after 300 seconds"
        except subprocess.CalledProcessError as e:
            error_msg = f"Docker command failed: {str(e)}"
            if "docker daemon is not running" in str(e).lower():
                error_msg = "Docker daemon is not running. Please start Docker and try again."
            return False, error_msg
        except Exception as e:
            return False, f"Error running Docker container: {str(e)}"
    
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
                    
                    # Get workspace directory
                    workspace_dir = os.path.join('workspace', job_id)
                    
                    # Run the job in Docker
                    success, message = run_docker_container(job_id, workspace_dir)
                    
                    # Update status based on execution result
                    if success:
                        job['status'] = 'Completed'
                        job['logs'].append(f"[{job_id}] Job completed successfully")
                    else:
                        job['status'] = 'Failed'
                        job['logs'].append(f"[{job_id}] Job failed: {message}")
                    
                    job['end_time'] = time.time()
            
            # Sleep briefly to prevent CPU overuse
            time.sleep(1)
    
    # Start the background worker thread
    worker_thread = threading.Thread(target=background_worker, daemon=True)
    worker_thread.start()
    
    return app 
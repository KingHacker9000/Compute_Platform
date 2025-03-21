from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
from collections import deque
import threading
import time
import subprocess
import shutil
import tempfile
import zipfile
import uuid
import traceback

# Configuration
JOB_EXPIRATION_MINUTES = 120  # Time after which jobs are deleted

db = SQLAlchemy()

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
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Initialize extensions
    db.init_app(app)
    
    # Initialize job tracking
    app.jobs_metadata = {}
    app.job_queue = []
    
    # Register blueprints
    from app.routes import main
    app.register_blueprint(main)
    
    # Start background worker
    worker_thread = threading.Thread(target=background_worker, args=(app,), daemon=True)
    worker_thread.start()
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_jobs, args=(app,), daemon=True)
    cleanup_thread.start()
    
    return app

def remove_readonly(func, path, _):
    """Clear the readonly bit and reattempt the removal."""
    try:
        # Make the file writable
        os.chmod(path, 0o777)
        func(path)
    except Exception as e:
        print(f"Error in remove_readonly: {str(e)}")
        raise

def remove_directory(directory):
    """Remove a directory and all its contents, handling read-only files."""
    try:
        if os.path.exists(directory):
            print(f"Attempting to remove directory: {directory}")
            shutil.rmtree(directory, onerror=remove_readonly)
            print(f"Successfully removed directory: {directory}")
            return True
    except Exception as e:
        print(f"Error removing directory {directory}: {str(e)}")
        return False

def cleanup_old_jobs(app):
    """Background thread to clean up old jobs and their files."""
    print("Cleanup thread started")
    while True:
        try:
            current_time = datetime.now()
            jobs_to_remove = []
            
            print(f"Checking for old jobs at {current_time}")
            print(f"Current jobs in metadata: {list(app.jobs_metadata.keys())}")
            
            # Find jobs older than JOB_EXPIRATION_MINUTES
            for job_id, job in app.jobs_metadata.items():
                job_time = datetime.fromisoformat(job['timestamp'])
                age = current_time - job_time
                print(f"Job {job_id} age: {age.total_seconds() / 60:.2f} minutes")
                
                if age > timedelta(minutes=JOB_EXPIRATION_MINUTES):
                    print(f"Job {job_id} is {age.total_seconds() / 60:.2f} minutes old, marking for removal")
                    jobs_to_remove.append(job_id)
            
            # Remove old jobs and their files
            for job_id in jobs_to_remove:
                print(f"\nProcessing cleanup for job {job_id}")
                
                # Remove job metadata
                if job_id in app.jobs_metadata:
                    del app.jobs_metadata[job_id]
                    print(f"Removed metadata for job {job_id}")
                
                # Remove job from queue if present
                if job_id in app.job_queue:
                    app.job_queue.remove(job_id)
                    print(f"Removed job {job_id} from queue")
                
                # Remove workspace directory
                workspace_dir = os.path.join('workspace', job_id)
                print(f"Checking workspace directory: {workspace_dir}")
                
                if os.path.exists(workspace_dir):
                    # Try to remove the directory with retries
                    max_retries = 3
                    for attempt in range(max_retries):
                        if remove_directory(workspace_dir):
                            break
                        print(f"Retry {attempt + 1}/{max_retries} for removing directory {workspace_dir}")
                        time.sleep(1)  # Wait a second before retrying
                else:
                    print(f"Workspace directory {workspace_dir} does not exist")
                
                print(f"Completed cleanup for job {job_id}")
            
            print(f"\nCleanup cycle completed. Sleeping for 5 minutes...")
            time.sleep(300)  # 5 minutes
            
        except Exception as e:
            print(f"Error in cleanup thread: {str(e)}")
            print("Stack trace:", traceback.format_exc())
            time.sleep(60)  # Wait a minute before retrying on error

def background_worker(app):
    """Background thread to process jobs."""
    while True:
        try:
            if app.job_queue:
                job_id = app.job_queue.pop(0)
                job = app.jobs_metadata.get(job_id)
                
                if job:
                    print(f"Processing job {job_id}")
                    job['status'] = 'Running'
                    job['start_time'] = datetime.now().timestamp()
                    
                    # Get workspace directory
                    workspace_dir = os.path.join('workspace', job_id)
                    
                    # Run the job in Docker
                    success, message = run_docker_container(app, job_id, workspace_dir)
                    
                    # Update job status
                    job['status'] = 'Completed' if success else 'Failed'
                    job['end_time'] = datetime.now().timestamp()
                    job['logs'].append(f"Job {'completed' if success else 'failed'}: {message}")
                    
                    print(f"Job {job_id} {'completed' if success else 'failed'}")
            
            time.sleep(1)  # Check queue every second
            
        except Exception as e:
            print(f"Error in background worker: {str(e)}")
            time.sleep(5)  # Wait 5 seconds before retrying on error

def run_docker_container(app, job_id, workspace_dir):
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
    
    # Create container name
    container_name = f"job_{job_id}"
    
    # Check for run.sh or main.py using original path
    if os.path.exists(os.path.join(original_workspace_dir, 'run.sh')):
        cmd = ['docker', 'run', '--name', container_name, '--gpus', 'all',
               '-v', f'{docker_workspace_dir}:/app',
               '-w', '/app',
               base_image,
               'bash', '-c', 'chmod +x run.sh && stdbuf -o0 ./run.sh']
    elif os.path.exists(os.path.join(original_workspace_dir, 'main.py')):
        # Check if requirements.txt exists
        if os.path.exists(os.path.join(original_workspace_dir, 'requirements.txt')):
            # Install requirements and run the script
            cmd = ['docker', 'run', '--name', container_name, '--gpus', 'all',
                   '-v', f'{docker_workspace_dir}:/app',
                   '-w', '/app',
                   base_image,
                   'bash', '-c', 'pip install -r requirements.txt && stdbuf -o0 python3 -u main.py']
        else:
            # Run the script without installing requirements
            cmd = ['docker', 'run', '--name', container_name, '--gpus', 'all',
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
            
            # Stream pull logs
            while True:
                line = pull_process.stdout.readline()
                if not line and pull_process.poll() is not None:
                    break
                if line:
                    app.jobs_metadata[job_id]['logs'].append(line.strip())
            
            pull_process.wait()
            if pull_process.returncode != 0:
                return False, f"Failed to pull Docker image: {base_image}"
        
        # Run the container
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
        
        # Stream container logs
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                app.jobs_metadata[job_id]['logs'].append(line.strip())
        
        # Get the exit code
        exit_code = process.wait()
        
        # Remove the container
        try:
            subprocess.run(['docker', 'rm', container_name], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to remove container {container_name}: {e.stderr}")
        
        return exit_code == 0, f"Container exited with code {exit_code}"
        
    except Exception as e:
        # Try to remove the container if it exists
        try:
            subprocess.run(['docker', 'rm', container_name], check=True)
        except subprocess.CalledProcessError:
            pass
        return False, f"Error running Docker container: {str(e)}" 
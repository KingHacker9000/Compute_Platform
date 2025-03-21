from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
from collections import deque
import threading
import time
import subprocess
import shutil
import traceback
import socket
from concurrent.futures import ThreadPoolExecutor

# Configuration
JOB_EXPIRATION_MINUTES = 120  # Time after which jobs are deleted
MAX_CONCURRENT_JOBS = 3  # Maximum number of jobs that can run simultaneously

db = SQLAlchemy()
job_semaphore = threading.Semaphore(MAX_CONCURRENT_JOBS)

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

def process_job(app, job_id):
    """Process a single job with resource management."""
    with job_semaphore:
        try:
            job = app.jobs_metadata.get(job_id)
            if not job:
                return
            
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
            
        except Exception as e:
            print(f"Error processing job {job_id}: {str(e)}")
            if job:
                job['status'] = 'Failed'
                job['end_time'] = datetime.now().timestamp()
                job['logs'].append(f"Job failed with error: {str(e)}")

def background_worker(app):
    """Background thread to manage job queue and parallel execution."""
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS) as executor:
        while True:
            try:
                # Process all queued jobs that can be started
                while app.job_queue and job_semaphore._value > 0:
                    job_id = app.job_queue.pop(0)
                    # Submit the job to the thread pool
                    executor.submit(process_job, app, job_id)
                
                time.sleep(1)  # Check queue every second
                
            except Exception as e:
                print(f"Error in background worker: {str(e)}")
                time.sleep(5)  # Wait 5 seconds before retrying on error

def find_available_port(start_port=9000):
    """Find an available port starting from start_port."""
    max_attempts = 100  # Prevent infinite loop
    current_port = start_port
    
    for _ in range(max_attempts):
        try:
            # Try to bind to the port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', current_port))
                return current_port
        except OSError:
            # Port is in use, try Docker port check as well
            try:
                # Check if port is used by Docker
                result = subprocess.run(['docker', 'ps', '--format', '{{.Ports}}'], 
                                     capture_output=True, text=True, check=True)
                if str(current_port) not in result.stdout:
                    # Port might be available for Docker even if not for direct binding
                    return current_port
            except subprocess.CalledProcessError:
                pass  # Ignore Docker check errors
            
            # Try next port
            current_port += 1
    
    raise RuntimeError(f"Could not find an available port after {max_attempts} attempts")

def run_docker_container(app, job_id, workspace_dir):
    """Run the job in a Docker container and stream logs."""
    # First check if Docker is running
    if not check_docker_running():
        return False, "Docker daemon is not running. Please start Docker."
    
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
    
    # Check if this is a web app
    is_web = app.jobs_metadata[job_id].get('is_web', False)
    container_port = app.jobs_metadata[job_id].get('container_port', 8000)
    
    # Build Docker command
    cmd = ['docker', 'run', '--name', container_name, '--gpus', 'all',
           '-v', f'{docker_workspace_dir}:/app',
           '-w', '/app']
    
    # Add port mapping for web apps
    if is_web:
        max_port_attempts = 5
        host_port = None
        last_error = None
        
        for attempt in range(max_port_attempts):
            try:
                host_port = find_available_port(9000 + attempt * 100)  # Try ports in different ranges
                # Test Docker port availability explicitly
                test_cmd = ['docker', 'run', '--rm', '-p', f'{host_port}:{container_port}', 'alpine', 'true']
                subprocess.run(test_cmd, check=True, capture_output=True)
                break  # Port is available
            except (RuntimeError, subprocess.CalledProcessError) as e:
                last_error = str(e)
                continue
        
        if host_port is None:
            return False, f"Could not find available port after {max_port_attempts} attempts. Last error: {last_error}"
        
        app.jobs_metadata[job_id]['host_port'] = host_port
        # Always use port mapping for web apps, regardless of OS
        cmd.extend(['-p', f'{host_port}:{container_port}'])
        # Add environment variables for the web app
        cmd.extend(['-e', f'PORT={container_port}',
                   '-e', f'FLASK_RUN_PORT={container_port}',
                   '-e', f'FLASK_RUN_HOST=0.0.0.0',
                   '-e', 'HOST=0.0.0.0'])  # Ensure app binds to all interfaces
    
    # Add base image and command
    cmd.append(base_image)
    
    # Check for run.sh or main.py using original path
    if os.path.exists(os.path.join(original_workspace_dir, 'run.sh')):
        # For web apps, ensure the app binds to 0.0.0.0
        if is_web:
            cmd.extend(['bash', '-c', 'tr -d "\\r" < /app/run.sh > /app/run_unix.sh && chmod +x /app/run_unix.sh && (export HOST=0.0.0.0; export PORT=' + str(container_port) + '; export FLASK_RUN_HOST=0.0.0.0; export FLASK_RUN_PORT=' + str(container_port) + '; /app/run_unix.sh)'])
        else:
            cmd.extend(['bash', '-c', 'tr -d "\\r" < /app/run.sh > /app/run_unix.sh && chmod +x /app/run_unix.sh && /app/run_unix.sh'])
    elif os.path.exists(os.path.join(original_workspace_dir, 'main.py')):
        # Check if requirements.txt exists
        if os.path.exists(os.path.join(original_workspace_dir, 'requirements.txt')):
            # Install requirements and run the script
            if is_web:
                cmd.extend(['bash', '-c', 'tr -d "\\r" < /app/requirements.txt > /app/requirements_unix.txt && pip install -r /app/requirements_unix.txt && (export HOST=0.0.0.0; export PORT=' + str(container_port) + '; export FLASK_RUN_HOST=0.0.0.0; export FLASK_RUN_PORT=' + str(container_port) + '; python3 -u main.py)'])
            else:
                cmd.extend(['bash', '-c', 'tr -d "\\r" < /app/requirements.txt > /app/requirements_unix.txt && pip install -r /app/requirements_unix.txt && python3 -u main.py'])
        else:
            # Run the script without installing requirements
            if is_web:
                cmd.extend(['bash', '-c', '(export HOST=0.0.0.0; export PORT=' + str(container_port) + '; export FLASK_RUN_HOST=0.0.0.0; export FLASK_RUN_PORT=' + str(container_port) + '; python3 -u main.py)'])
            else:
                cmd.extend(['bash', '-c', 'python3 -u main.py'])
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
        
        # For web apps, we don't remove the container immediately
        if not is_web:
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
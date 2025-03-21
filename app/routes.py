from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
import re
import subprocess
from urllib.parse import urlparse
import os
import uuid
from datetime import datetime
from git import Repo

main = Blueprint('main', __name__)

def is_valid_github_url(url):
    """Validate if the URL is a valid GitHub repository URL."""
    # Check if URL starts with https://github.com/
    if not url.startswith('https://github.com/'):
        return False
    
    # Check if URL ends with .git or matches GitHub repo pattern
    if not (url.endswith('.git') or re.match(r'https://github\.com/[^/]+/[^/]+/?$', url)):
        return False
    
    return True

def clone_repository(repo_url, branch, job_id):
    """Clone the repository into the workspace directory."""
    workspace_dir = os.path.join('workspace', str(job_id))
    os.makedirs(workspace_dir, exist_ok=True)
    
    try:
        # Clone the repository
        Repo.clone_from(repo_url, workspace_dir, branch=branch)
        return True
    except Exception as e:
        flash(f'Error cloning repository: {str(e)}', 'error')
        # Clean up the workspace directory on error
        import shutil
        if os.path.exists(workspace_dir):
            shutil.rmtree(workspace_dir)
        return False

@main.route('/')
def index():
    return render_template('index.html')

@main.route('/submit', methods=['GET', 'POST'])
def submit():
    if request.method == 'POST':
        repo_url = request.form.get('repo_url', '').strip()
        branch = request.form.get('branch', 'main').strip()
        
        if not repo_url:
            flash('Repository URL is required.', 'error')
            return redirect(url_for('main.submit'))
        
        if not is_valid_github_url(repo_url):
            flash('Please enter a valid GitHub repository URL.', 'error')
            return redirect(url_for('main.submit'))
        
        # Optional: Check if repository is reachable
        try:
            subprocess.run(['git', 'ls-remote', repo_url], 
                         capture_output=True, 
                         text=True, 
                         timeout=5)
        except subprocess.TimeoutExpired:
            flash('Repository validation timed out. Please check the URL and try again.', 'error')
            return redirect(url_for('main.submit'))
        except subprocess.CalledProcessError:
            flash('Could not validate repository. Please check if the repository exists and is accessible.', 'error')
            return redirect(url_for('main.submit'))
        
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        # Clone the repository
        if not clone_repository(repo_url, branch, job_id):
            return redirect(url_for('main.submit'))
        
        # Store job metadata
        current_app.jobs_metadata[job_id] = {
            'id': job_id,
            'repo_url': repo_url,
            'branch': branch,
            'status': 'Queued',
            'timestamp': datetime.now().isoformat()
        }
        
        # Add job to queue
        current_app.job_queue.append(job_id)
        
        flash('Repository submitted successfully!', 'success')
        return redirect(url_for('main.job_status', job_id=job_id))
    
    return render_template('submit.html')

@main.route('/job/<job_id>')
def job_status(job_id):
    job = current_app.jobs_metadata.get(str(job_id))
    if not job:
        flash('Job not found.', 'error')
        return redirect(url_for('main.index'))
    
    # If it's an AJAX request, return JSON
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'status': job['status'],
            'start_time': job.get('start_time'),
            'end_time': job.get('end_time')
        })
    
    # Create a copy of the job data for display
    display_job = job.copy()
    
    # Format timestamps if they exist
    if 'start_time' in display_job:
        display_job['start_time'] = datetime.fromtimestamp(display_job['start_time']).isoformat()
    if 'end_time' in display_job:
        display_job['end_time'] = datetime.fromtimestamp(display_job['end_time']).isoformat()
    
    return render_template('job_status.html', job=display_job)

@main.route('/logs/<job_id>')
def get_logs(job_id):
    job = current_app.jobs_metadata.get(str(job_id))
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    # Return logs if they exist, otherwise empty list
    logs = job.get('logs', [])
    return jsonify({'logs': logs}) 
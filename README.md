# AI Computer Platform

A powerful web-based platform for running AI workloads. This platform enables users to submit GitHub repositories, run AI models inside Docker containers with AMD GPU support, and view real-time logs. It also supports hosting live web demos from Flask, Django, or Gunicorn apps at clean URLs.

## ✨ Features

- ✅ **GitHub Repo Submission**: Easy submission of repositories with automatic validation
- ✅ **Auto Cloning & Validation**: Automatic repository cloning and dependency validation
- ✅ **Job Queuing & Execution**: Parallel job execution with resource management
- ✅ **Real-time Log Streaming**: Live updates of job execution progress
- ✅ **AMD GPU Support**: Full ROCm integration for AMD GPUs in WSL2
- ✅ **Smart Docker Image Selection**: Automatic detection of PyTorch, TensorFlow, or Python requirements
- ✅ **Web App Hosting**: Automatic hosting of web applications at `/site/<job_id>`
- ✅ **Downloadable Artifacts**: Easy access to trained models and outputs
- ✅ **Admin Dashboard**: Monitor and manage running jobs (Work in Progress)

## 🚀 Getting Started

### Prerequisites

- Windows 10/11 with WSL2 (Ubuntu)
- AMD GPU with ROCm drivers installed
- Docker with GPU support configured in WSL2
- Python 3.10 or higher
- Git

### Installation

1. Clone the repository:
```bash
git clone https://github.com/your-org/ai-compute-platform.git
cd ai-compute-platform
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Linux/WSL
# or
.\venv\Scripts\activate  # On Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure ROCm support in WSL2:
```bash
# Inside WSL2
sudo apt install rocm-smi rock-dkms
```

5. Verify GPU access:
```bash
rocminfo
rocm-smi
```

## 🏃‍♂️ Running the Platform

Start the Flask server:
```bash
python run.py
```

Visit `http://localhost:5000` in your browser.

## 📁 Project Structure

```
workspace/         → Job repositories and outputs
app/
  ├─ __init__.py   → App initialization and config
  ├─ routes.py     → Flask route handlers
  ├─ templates/    → HTML templates
  ├─ static/       → Static assets (CSS, JS)
  └─ utils.py      → Helper functions
run.py             → Application entry point
requirements.txt   → Python dependencies
```

## 🧑‍💻 Usage Guide

1. **Submit a Job**
   - Navigate to `/submit`
   - Enter your GitHub repository URL
   - Optionally specify a branch (defaults to main)

2. **Configure Your Repository**
   - Include a `run.sh` script or `main.py` file
   - Optionally add a `job.yaml` for configuration

3. **Track Progress**
   - Monitor your job at `/job/<job_id>`
   - View real-time logs and status updates

4. **Access Web Apps**
   - If your job is a web application, access it at `/site/<job_id>`
   - Ports are automatically managed

5. **Download Results**
   - Once complete, use the download button to get your results
   - Models are saved in the `models/` directory

## 📝 Job Configuration

Example `job.yaml`:
```yaml
web: true          # Is this a web application?
port: 8000         # Port for web apps (default: 8000)
gpu: true          # Requires GPU access
```

## 🔧 Technical Details

### GPU Support
- AMD GPUs are supported through ROCm in WSL2
- Automatic detection of PyTorch/TensorFlow requirements
- Dynamic GPU resource allocation

### Container Management
- Parallel job execution (default: 3 concurrent jobs)
- Automatic cleanup after completion
- Resource limits and timeouts

### Web App Support
- Automatic port assignment and management
- Reverse proxy for clean URLs
- Environment variable injection for proper binding

## 🧹 Cleanup & Maintenance

- Jobs automatically expire after 120 minutes
- Containers and workspace directories are cleaned up
- Failed jobs are properly logged and removed

## 👨‍💻 Development Notes

- Built with Flask and Python 3.10
- Uses Docker for containerization
- Supports AMD GPUs through ROCm stack
- Real-time log streaming via server-sent events
- Parallel job execution with resource management

## 📄 License

MIT License

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 🐛 Known Issues

- Web apps must bind to 0.0.0.0 to be accessible
- GPU support requires proper ROCm setup in WSL2
- Container cleanup may need manual intervention if jobs crash

## 📞 Support

For support, please open an issue on the GitHub repository. 
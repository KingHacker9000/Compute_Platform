from app import create_app

app = create_app()

if __name__ == '__main__':
    # Get the local IP address
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print(f"\nServer starting...")
    print(f"Local access: http://localhost:5000")
    print(f"Network access: http://{local_ip}:5000")
    print(f"Press Ctrl+C to stop the server\n")
    
    # Run the app on all network interfaces
    app.run(host='0.0.0.0', port=5000) 
    #app.run(host='0.0.0.0', port=5000, debug=True) 
#!/usr/bin/env python3
"""Road Discovery Web Server.

Runs the live web application on port 8000, enabling:
- Real-time address / location entry directly on the webpage
- Instant OSMnx road network extraction & curvature ranking
- 1-click Google Maps tour dispatch generation
- Direct GPX / KML downloads
- Herdr-styled driving guide
"""

import sys
import os
import socket
import subprocess
import signal

# Ensure repo root is on python path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def is_port_in_use(port: int) -> bool:
    """Check if a network port is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port_if_static_http_server(port: int = 8000):
    """If an old 'python -m http.server 8000' is running, terminate it to reclaim port 8000."""
    try:
        res = subprocess.run(
            ["lsof", "-t", f"-i:{port}"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = [p.strip() for p in res.stdout.strip().split("\n") if p.strip()]
        for pid in pids:
            # Check process command
            ps_res = subprocess.run(
                ["ps", "-p", pid, "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
            )
            cmd = ps_res.stdout.strip()
            # If it's http.server or python, terminate it so our full-stack API can bind
            if "http.server" in cmd or "uvicorn" in cmd:
                print(f"[Server] Freeing port {port} (terminating old static process PID {pid}: {cmd[:50]}...)")
                os.kill(int(pid), signal.SIGTERM)
    except Exception as e:
        print(f"[Server] Note: Could not auto-free port {port}: {e}")


def main():
    target_port = 8000

    # If port 8000 is occupied by static http.server, free it
    if is_port_in_use(target_port):
        free_port_if_static_http_server(target_port)

    # If still in use, fallback to 8080 or next available
    port = target_port
    if is_port_in_use(port):
        for candidate in [8080, 8001, 8002, 5000]:
            if not is_port_in_use(candidate):
                port = candidate
                break

    print("=" * 72)
    print(" 🏎️  ROAD DISCOVERY ENGINE - LIVE WEB SERVER")
    print("=" * 72)
    print(f" Web Map & Address Search:  http://localhost:{port}")
    print(f" How to Drive Guide:        http://localhost:{port}/navigation_guide.html")
    print(f" Discover API:              http://localhost:{port}/api/discover")
    print("=" * 72)
    print(" Enter any address, town, or landmark on the webpage to discover routes.\n")

    try:
        import uvicorn
        from road_discovery.api import app
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    except ImportError:
        print("[Error] Uvicorn is required. Run: pip install uvicorn fastapi", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

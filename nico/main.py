"""
Entry point for the `nico` CLI command.
Starts the local Flask server and opens the browser automatically.
Analogous to Duplicati: no persistent daemon, runs on demand.

Port strategy:
  1. If preferred port is free → use it.
  2. If preferred port is busy and NiCo is already running there → just open browser.
  3. If preferred port is busy with something else → find the next free port.
"""

import socket
import threading
import time
import urllib.request
import urllib.error
import webbrowser

from nico.server import create_app

PREFERRED_PORT = 8421
HOST = "127.0.0.1"


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) != 0


def _nico_already_running(port: int) -> bool:
    """Check if a NiCo instance is already listening on this port."""
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}/api/status", timeout=1
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _find_free_port(start: int) -> int:
    port = start
    while not _is_port_free(port):
        port += 1
    return port


def _open_browser(port: int):
    time.sleep(0.9)
    webbrowser.open(f"http://{HOST}:{port}")


def _get_csrf_token(port: int) -> str | None:
    """Fetch the CSRF token from the running NiCo instance's HTML page."""
    import re as _re
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/", timeout=2) as resp:
            html = resp.read().decode("utf-8")
        m = _re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        return m.group(1) if m else None
    except Exception:
        return None


def _shutdown_running_instance(port: int) -> bool:
    """Send shutdown request to a running NiCo instance. Returns True if successful."""
    token = _get_csrf_token(port)
    if not token:
        return False
    try:
        req = urllib.request.Request(
            f"http://{HOST}:{port}/api/shutdown",
            data=b"",
            method="POST",
            headers={"X-CSRF-Token": token},
        )
        with urllib.request.urlopen(req, timeout=2):
            pass
        return True
    except Exception:
        return False


def main():
    print("NiCo – NixOS Configurator")

    if _is_port_free(PREFERRED_PORT):
        port = PREFERRED_PORT
    elif _nico_already_running(PREFERRED_PORT):
        print(f"NiCo läuft bereits auf Port {PREFERRED_PORT}.")
        webbrowser.open(f"http://{HOST}:{PREFERRED_PORT}")
        return
    else:
        port = _find_free_port(PREFERRED_PORT + 1)
        print(f"Port {PREFERRED_PORT} belegt, verwende Port {port}.")

    print(f"Server läuft auf http://{HOST}:{port}")
    print("Beenden mit Ctrl+C.")

    t = threading.Thread(target=_open_browser, args=(port,), daemon=True)
    t.start()

    app = create_app()
    app.run(host=HOST, port=port, debug=False)


if __name__ == "__main__":
    main()

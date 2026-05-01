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


def _load_theme_css() -> str:
    import tomllib
    from pathlib import Path
    from nico import config_manager
    themes_dir = Path(__file__).parent / "static" / "themes"
    default = "catppuccin-mocha"
    theme = config_manager.get_app_settings().get("theme", default)
    toml_path = themes_dir / theme / "theme.toml"
    if not toml_path.exists():
        toml_path = themes_dir / default / "theme.toml"
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        css_vars = "\n".join(f"  --{k}: {v};" for k, v in data.get("vars", {}).items())
        return f":root {{\n{css_vars}\n}}"
    except Exception:
        return ""


def _serve_already_running_page(preferred_port: int) -> None:
    """Open a browser page offering restart when NiCo is already running."""
    import http.server

    mini_port = _find_free_port(preferred_port + 1)
    theme_css = _load_theme_css()

    _HTML = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>NiCo</title>
  <style>
THEME_CSS
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--sans, sans-serif);
      background: var(--bg);
      color: var(--text);
      display: flex;
      justify-content: center;
      align-items: center;
      height: 100vh;
    }
    .card {
      background: var(--bg-card, var(--surface0));
      border: 1px solid var(--border, var(--surface1));
      border-radius: 10px;
      padding: 2rem 2.5rem;
      text-align: center;
      max-width: 420px;
      width: 90%;
    }
    .logo { font-size: 2rem; font-weight: 700; color: var(--accent); margin-bottom: 0.25rem; }
    h2 { font-size: 1rem; font-weight: 400; color: var(--text2, var(--subtext1)); margin-bottom: 1.25rem; }
    p { color: var(--text2, var(--subtext1)); line-height: 1.6; margin-bottom: 1.75rem; }
    button {
      padding: 0.55rem 1.8rem;
      background: var(--accent);
      border: none;
      border-radius: 5px;
      color: var(--bg);
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity .15s;
    }
    button:hover { opacity: 0.85; }
    button:disabled { opacity: 0.45; cursor: default; }
    #msg { margin-top: 1rem; font-size: 0.82rem; color: var(--text-muted, var(--overlay0)); min-height: 1.2em; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">NiCo</div>
    <h2>NixOS Configurator</h2>
    <p>L&auml;uft bereits in einem anderen Fenster.<br>
       Dieses Tab schlie&szlig;en &ndash; oder neu starten:</p>
    <button id="btn" onclick="doRestart()">Neu starten</button>
    <div id="msg"></div>
  </div>
  <script>
    async function doRestart() {
      const btn = document.getElementById('btn');
      const msg = document.getElementById('msg');
      btn.disabled = true;
      btn.textContent = 'Wird neu gestartet\u2026';
      msg.textContent = 'Alte Instanz wird beendet\u2026';
      try { await fetch('http://127.0.0.1:MINI_PORT/restart', {method:'POST'}); } catch {}
      msg.textContent = 'Warte auf NiCo\u2026';
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 300));
        try {
          const r = await fetch('http://127.0.0.1:MINI_PORT/status', {cache:'no-store'});
          const j = await r.json();
          if (j.running) { window.location.href = 'http://127.0.0.1:PREF_PORT/'; return; }
        } catch {}
      }
      msg.textContent = 'Timeout \u2013 bitte manuell \xf6ffnen.';
    }
  </script>
</body>
</html>"""

    html = (_HTML
            .replace("THEME_CSS", theme_css)
            .replace("MINI_PORT", str(mini_port))
            .replace("PREF_PORT", str(preferred_port)))

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/status":
                running = False
                try:
                    with urllib.request.urlopen(
                        f"http://{HOST}:{preferred_port}/api/status", timeout=1
                    ) as r:
                        running = r.status == 200
                except Exception:
                    pass
                body = f'{{"running":{str(running).lower()}}}'.encode()
                self._reply(200, "application/json", body)
            else:
                self._reply(200, "text/html; charset=utf-8", html.encode())

        def do_POST(self):
            if self.path == "/restart":
                _shutdown_running_instance(preferred_port)
                for _ in range(50):
                    time.sleep(0.1)
                    if _is_port_free(preferred_port):
                        break

                def _start_flask():
                    print(f"Server läuft auf http://{HOST}:{preferred_port}")
                    print("Beenden mit Ctrl+C.")
                    create_app().run(host=HOST, port=preferred_port, debug=False)

                threading.Thread(target=_start_flask, daemon=False).start()
                self._reply(200, "application/json", b'{"ok":true}')

        def _reply(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer((HOST, mini_port), _Handler)
    threading.Thread(
        target=lambda: (time.sleep(30), srv.shutdown()), daemon=True
    ).start()
    webbrowser.open(f"http://{HOST}:{mini_port}/")
    srv.serve_forever()


def main():
    print("NiCo – NixOS Configurator")

    if _is_port_free(PREFERRED_PORT):
        port = PREFERRED_PORT
    elif _nico_already_running(PREFERRED_PORT):
        _serve_already_running_page(PREFERRED_PORT)
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

"""
Restarting NiCo from its own interface (Martin, 2026-11-25).

Offered after an update and in the quit dialog. The running NiCo starts a
new one and then exits; the new one waits until the port is free and takes
the same port, so the open browser tab only has to reload.

Which program is started:
- Running from the Nix store (nix profile, systemPackages, nix run): the
  `nico` command on PATH. After an update that is the new version – the
  profile has been switched, while this process still runs from the old
  store path. Without a `nico` on PATH (plain `nix run`) the same program
  again.
- Running from a source tree (start.sh): the same interpreter with
  `-m nico.main`, same working directory and environment, so the nix-shell
  around it is kept.

Known edge: started with `nix run` while a `nico` is also installed, a
restart switches to the installed one. Both are NiCo; telling them apart
would need the old profile state, which an update has just replaced.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Read by main.py of the new process: take this port once it is free, and do
# not open a browser – the old tab reloads itself.
ENV_PORT = "NICO_RESTART_PORT"


def restart_command() -> list[str]:
    here = Path(__file__).resolve()
    if str(here).startswith("/nix/store/"):
        exe = shutil.which("nico")
        if exe:
            return [exe]
    return [sys.executable, "-m", "nico.main"]


def spawn(port: int) -> None:
    """Start the new NiCo, detached: it has to outlive this process, and a
    closed terminal must not take it down (own session). Output goes where
    this process's output goes. Raises OSError when it cannot be started."""
    env = dict(os.environ)
    env[ENV_PORT] = str(port)
    subprocess.Popen(restart_command(), env=env, cwd=os.getcwd(),
                     stdin=subprocess.DEVNULL, start_new_session=True)

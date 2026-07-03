"""supervisor.py - windowless supervisor for the always-on FinanceTracker servers.

Run by the Windows Task Scheduler task "FinanceTracker" under pythonw.exe
(GUI subsystem: no console window can ever appear). Keeps BOTH local servers
alive so Tailscale Serve can expose them to the phone:

  - FastAPI backend on BACKEND_HOST:BACKEND_PORT   (tailscale serve :8443 -> 8010)
  - static PWA (frontend/dist) on 127.0.0.1:4173   (tailscale serve :443  -> 4173)

Every CHECK_INTERVAL seconds it probes each port and relaunches whichever
server is not listening. Children are spawned with CREATE_NO_WINDOW, so they
are invisible too. Server output goes to logs/backend.log and logs/web.log
(logs/ is gitignored and never leaves this machine).

No secrets here - only BACKEND_HOST and BACKEND_PORT are read from .env so
uvicorn binds correctly; everything sensitive is loaded inside the Python app.

Also runnable in a normal console for testing:
  venv\\Scripts\\python.exe service\\supervisor.py
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    from service import backup
except ImportError:  # run directly as a script: service/ is sys.path[0]
    try:
        import backup
    except Exception:  # noqa: BLE001 — a broken backup module must never stop the supervisor
        backup = None  # type: ignore[assignment]
except Exception:  # noqa: BLE001 — a broken backup module must never stop the supervisor
    backup = None  # type: ignore[assignment]

REPO = Path(__file__).resolve().parent.parent
PYTHON = REPO / "venv" / "Scripts" / "python.exe"
DIST = REPO / "frontend" / "dist"
LOGS = REPO / "logs"

WEB_HOST = "127.0.0.1"
WEB_PORT = 4173
CHECK_INTERVAL = 15
MAX_LOG_BYTES = 5 * 1024 * 1024

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def read_backend_bind() -> tuple[str, int]:
    """Read only BACKEND_HOST / BACKEND_PORT from .env; defaults otherwise."""
    host, port = "0.0.0.0", 8010
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key == "BACKEND_HOST" and value:
                host = value
            elif key == "BACKEND_PORT" and value.isdigit():
                port = int(value)
    return host, port


def is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def open_log(name: str):
    LOGS.mkdir(exist_ok=True)
    path = LOGS / name
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.unlink()
    return open(path, "a", encoding="utf-8", buffering=1)


def note(log, message: str) -> None:
    log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def spawn(args: list[str], log_name: str) -> subprocess.Popen:
    log = open_log(log_name)
    return subprocess.Popen(
        args,
        cwd=REPO,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def main() -> None:
    backend_host, backend_port = read_backend_bind()
    sup_log = open_log("supervisor.log")
    note(sup_log, f"supervisor started (backend {backend_host}:{backend_port}, web {WEB_HOST}:{WEB_PORT})")

    children: dict[str, subprocess.Popen] = {}
    servers = {
        "backend": (
            backend_port,
            [str(PYTHON), "-m", "uvicorn", "backend.app:app",
             "--host", backend_host, "--port", str(backend_port)],
            "backend.log",
        ),
        "web": (
            WEB_PORT,
            [str(PYTHON), "-m", "http.server", str(WEB_PORT),
             "--bind", WEB_HOST, "--directory", str(DIST)],
            "web.log",
        ),
    }

    while True:
        for name, (port, args, log_name) in servers.items():
            child = children.get(name)
            if child is not None and child.poll() is not None:
                note(sup_log, f"{name} exited with code {child.returncode}")
                children.pop(name)
            if not is_listening(port):
                note(sup_log, f"{name} not listening on {port}; starting")
                children[name] = spawn(args, log_name)
                time.sleep(3)
        try:
            if backup is not None:
                message = backup.run_backup_if_due(REPO)
                if message:
                    note(sup_log, message)
        except Exception as exc:  # backups must never break the supervisor loop
            note(sup_log, f"backup failed: {exc!r}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

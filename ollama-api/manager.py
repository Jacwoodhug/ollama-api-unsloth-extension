"""
Persistent manager server for the Ollama API proxy.
Runs on port 11435 (configurable via MANAGER_PORT env var).
Manages the lifecycle of main.py (the proxy) as a subprocess.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from config import read_proxy_settings, write_proxy_settings  # noqa: E402

app = FastAPI(title="Ollama Proxy Manager", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()
_proxy_process: subprocess.Popen | None = None


def _read_proxy_stdout(proc: subprocess.Popen) -> None:
    """Daemon thread: relay proxy stdout with [PROXY] prefix."""
    try:
        for line in iter(proc.stdout.readline, b""):
            print(f"[PROXY] {line.decode('utf-8', errors='replace').rstrip()}", flush=True)
    except Exception:
        pass


def _start_proxy_locked() -> None:
    """Start the proxy subprocess. Must be called with _lock held."""
    global _proxy_process
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "main.py")],
        cwd=str(HERE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _proxy_process = proc
    t = threading.Thread(target=_read_proxy_stdout, args=(proc,), daemon=True)
    t.start()


def _is_running() -> bool:
    """Return True if the proxy process is alive."""
    return _proxy_process is not None and _proxy_process.poll() is None


@app.on_event("startup")
def _auto_start_proxy() -> None:
    with _lock:
        if not _is_running():
            _start_proxy_locked()
    print("[MANAGER] Proxy auto-started on startup.", flush=True)


@app.get("/status")
def get_status():
    settings = read_proxy_settings()
    with _lock:
        running = _is_running()
        pid = _proxy_process.pid if running else None
    return {
        "running": running,
        "pid": pid,
        "port": settings.get("proxy_port", 11434),
        "settings": settings,
    }


@app.post("/start")
def start_proxy():
    with _lock:
        if _is_running():
            return {"status": "already_running", "pid": _proxy_process.pid}
        _start_proxy_locked()
        pid = _proxy_process.pid
    return {"status": "started", "pid": pid}


@app.post("/stop")
def stop_proxy():
    global _proxy_process
    with _lock:
        if not _is_running():
            return {"status": "not_running"}
        _proxy_process.terminate()
        try:
            _proxy_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proxy_process.kill()
        _proxy_process = None
    return {"status": "stopped"}


@app.get("/settings")
def get_settings():
    return read_proxy_settings()


@app.put("/settings")
def put_settings(data: dict):
    allowed_keys = {"unsloth_base_url", "unsloth_api_key", "model_context_length", "proxy_host", "proxy_port"}
    unknown = set(data.keys()) - allowed_keys
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings keys: {unknown}")
    current = read_proxy_settings()
    current.update(data)
    write_proxy_settings(current)
    return current


@app.get("/plugin.js")
def serve_plugin():
    plugin_path = HERE / "plugin.js"
    if not plugin_path.exists():
        raise HTTPException(status_code=404, detail="plugin.js not found")
    content = plugin_path.read_text(encoding="utf-8")
    return Response(content=content, media_type="application/javascript")


if __name__ == "__main__":
    port = int(os.getenv("MANAGER_PORT", "11435"))
    print(f"[MANAGER] Starting on port {port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

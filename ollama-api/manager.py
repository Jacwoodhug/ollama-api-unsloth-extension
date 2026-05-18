"""
Persistent manager server for the Ollama API proxy.
Runs on port 11435 (configurable via MANAGER_PORT env var).
Manages the lifecycle of main.py (the proxy) as a subprocess.
"""

import atexit
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import model_scanner

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from config import (  # noqa: E402
    read_proxy_settings, write_proxy_settings,
    read_model_settings, write_model_settings,
)

app = FastAPI(title="Ollama Proxy Manager", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()
_proxy_process: subprocess.Popen | None = None


def _stop_proxy() -> None:
    """Terminate the proxy subprocess gracefully, then force-kill if needed."""
    global _proxy_process
    proc = _proxy_process
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
    _proxy_process = None


# On POSIX: register atexit + SIGTERM handler so the proxy is cleaned up when
# the manager exits. Guarded to non-Windows because on Windows the console
# process-group interaction with VS Code's integrated terminal causes it to
# crash; the PS1 launch script handles cleanup via port-scan instead.
if os.name != "nt":
    atexit.register(_stop_proxy)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda _sig, _frame: sys.exit(0))


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


def _sanitize_settings(settings: dict) -> dict:
    """Return a copy of settings with unsloth_api_key replaced by a length-masked string."""
    out = dict(settings)
    raw_key = out.pop("unsloth_api_key", "")
    out["unsloth_api_key"] = "*" * len(raw_key) if raw_key else ""
    return out


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
        "settings": _sanitize_settings(settings),
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
    return _sanitize_settings(read_proxy_settings())


@app.put("/settings")
def put_settings(data: dict):
    allowed_keys = {
        "unsloth_base_url", "unsloth_api_key", "model_context_length",
        "proxy_host", "proxy_port", "open_browser_on_startup",
        "model_directory", "auto_switch_model", "model_configs",
    }
    unknown = set(data.keys()) - allowed_keys
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings keys: {unknown}")

    # model_configs lives in its own file — extract and save it separately.
    if "model_configs" in data:
        write_model_settings(data.pop("model_configs"))

    # Pop key now so needs_restart accounts for it without merging it blindly.
    incoming_key = data.pop("unsloth_api_key", None)
    key_changing = bool(incoming_key)

    restart_keys = {
        "unsloth_base_url", "unsloth_api_key", "model_context_length",
        "proxy_host", "proxy_port", "open_browser_on_startup",
        "model_directory", "auto_switch_model",
    }
    needs_restart = key_changing or bool(set(data.keys()) & restart_keys)

    current = read_proxy_settings()

    if key_changing:
        current["unsloth_api_key"] = incoming_key
    elif not current.get("unsloth_api_key"):
        raise HTTPException(
            status_code=422,
            detail="unsloth_api_key is required when no key is stored yet",
        )

    current.update(data)
    write_proxy_settings(current)

    if needs_restart:
        with _lock:
            if _is_running():
                _proxy_process.terminate()
                try:
                    _proxy_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _proxy_process.kill()
            _start_proxy_locked()

    return _sanitize_settings(current)


@app.get("/models")
def get_models():
    """Return scanned model list with per-model config overlaid."""
    model_configs = read_model_settings()
    models = model_scanner.scan_models()
    result = []
    new_configs: dict = {}
    for m in models:
        entry = dict(m)
        # Check quant-specific config first (e.g. "gemma-4-E2B-it-GGUF:BF16"),
        # then fall back to bare base name (e.g. "gemma-4-E2B-it-GGUF").
        cfg = model_configs.get(m["name"]) or model_configs.get(m["name"].split(":")[0]) or {}
        entry["context_length"] = cfg.get("context_length", "")
        entry["extra_args"] = cfg.get("extra_args", "")
        entry["hidden"] = bool(cfg.get("hidden", False))
        default_caps = ["completion", "tools"]
        if m.get("is_vision"):
            default_caps.append("vision")
        entry["capabilities"] = cfg.get("capabilities") or default_caps

        # Auto-populate model_settings.json for models with no existing config entry.
        has_own_config = m["name"] in model_configs or m["name"].split(":")[0] in model_configs
        if not has_own_config:
            new_configs[m["name"]] = {
                "context_length": 0,
                "extra_args": "",
                "capabilities": entry["capabilities"],
                "hidden": False,
            }

        result.append(entry)

    if new_configs:
        model_configs.update(new_configs)
        write_model_settings(model_configs)

    return {"models": result}


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

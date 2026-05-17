#!/usr/bin/env bash
set -uo pipefail

ROOT="$(dirname "$(realpath "$0")")"
PYTHON="$ROOT/studio/unsloth_studio/bin/python"
INDEX="$(find "$ROOT/studio/unsloth_studio/lib" -path "*/studio/frontend/dist/index.html" 2>/dev/null | head -1)"
PLUGIN_TAG='<script src="http://localhost:11435/plugin.js" defer></script>'

# 1. Install proxy requirements into studio Python (once; skips if already satisfied)
if ! "$PYTHON" -c "import fastapi, httpx, uvicorn, dotenv" 2>/dev/null; then
    echo "[SETUP] Installing ollama-api dependencies..."
    "$PYTHON" -m pip install -r "$ROOT/ollama-api/requirements.txt" --quiet
fi

# 2. Inject plugin script tag into Studio index.html (idempotent)
if [[ -f "$INDEX" ]] && ! grep -q 'localhost:11435/plugin\.js' "$INDEX"; then
    "$PYTHON" -c "
content = open('$INDEX', encoding='utf-8').read()
tag = '<script src=\"http://localhost:11435/plugin.js\" defer></script>'
open('$INDEX', 'w', encoding='utf-8').write(content.replace('</body>', tag + '\n</body>', 1))
"
    echo "[SETUP] Injected Ollama proxy plugin into Studio WebUI"
fi

# 3. Open browser after server starts (if enabled in settings)
SETTINGS="$ROOT/ollama-api/settings.json"
if [[ -f "$SETTINGS" ]]; then
    OPEN_BROWSER=$("$PYTHON" -c "import json; print(json.load(open('$SETTINGS')).get('open_browser_on_startup', False))" 2>/dev/null || echo "False")
    if [[ "$OPEN_BROWSER" == "True" ]]; then
        (sleep 5 && (xdg-open 'http://127.0.0.1:8888' 2>/dev/null || open 'http://127.0.0.1:8888' 2>/dev/null || true)) &
    fi
fi

# 4. Start manager (which auto-starts proxy) and unsloth as background processes
"$PYTHON" "$ROOT/ollama-api/manager.py" &
MANAGER_PID=$!

"$ROOT/studio/unsloth_studio/bin/unsloth" studio -p 8888 -H 0.0.0.0 2>&1 | \
    while IFS= read -r line; do echo "[UNSLOTH] $line"; done &
UNSLOTH_PID=$!

# 5. Cleanup on exit
cleanup() {
    echo "[SETUP] Stopping manager and proxy..."
    PROXY_PORT=11434
    SETTINGS="$ROOT/ollama-api/settings.json"
    if [[ -f "$SETTINGS" ]]; then
        PROXY_PORT=$("$PYTHON" -c "import json; print(json.load(open('$SETTINGS')).get('proxy_port', 11434))" 2>/dev/null || echo 11434)
    fi
    for port in 8888 11435 "$PROXY_PORT"; do
        pid=$(lsof -ti tcp:"$port" 2>/dev/null || true)
        [[ -n "$pid" ]] && kill -9 $pid 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for unsloth to exit
wait "$UNSLOTH_PID" 2>/dev/null || true

#!/usr/bin/env bash
set -uo pipefail

ROOT="$(dirname "$(realpath "$0")")"
PYTHON="$ROOT/studio/unsloth_studio/bin/python"
INDEX="$(find "$ROOT/studio/unsloth_studio/lib" -path "*/studio/frontend/dist/index.html" 2>/dev/null | head -1)"
# Bootstrap that loads plugin.js from whichever host the user is browsing from,
# so it works for both localhost and Tailscale / remote access.
PLUGIN_TAG="<script>(function(){var s=document.createElement('script');s.src=location.protocol+'//'+location.hostname+':11435/plugin.js';s.defer=true;document.head.appendChild(s);})();</script>"
OLD_PLUGIN_TAG='<script src="http://localhost:11435/plugin.js" defer></script>'

# 1. Install proxy requirements into studio Python (once; skips if already satisfied)
if ! "$PYTHON" -c "import fastapi, httpx, uvicorn, dotenv" 2>/dev/null; then
    echo "[SETUP] Installing ollama-api dependencies..."
    "$PYTHON" -m pip install -r "$ROOT/ollama-api/requirements.txt" --quiet
fi

# 2. Inject plugin script tag into Studio index.html (idempotent)
if [[ -f "$INDEX" ]]; then
    if grep -qF "$OLD_PLUGIN_TAG" "$INDEX"; then
        # Migrate old hardcoded-localhost tag to the new dynamic bootstrap
        "$PYTHON" -c "
import sys
content = open('$INDEX', encoding='utf-8').read()
old = '<script src=\"http://localhost:11435/plugin.js\" defer></script>'
new = \"$PLUGIN_TAG\"
open('$INDEX', 'w', encoding='utf-8').write(content.replace(old, new, 1))
"
        echo "[SETUP] Updated Ollama proxy plugin (dynamic hostname)"
    elif ! grep -q '11435/plugin\.js' "$INDEX"; then
        "$PYTHON" -c "
content = open('$INDEX', encoding='utf-8').read()
tag = \"$PLUGIN_TAG\"
open('$INDEX', 'w', encoding='utf-8').write(content.replace('</body>', tag + '\n</body>', 1))
"
        echo "[SETUP] Injected Ollama proxy plugin into Studio WebUI"
    fi
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

    # Ask the manager to exit gracefully — its atexit handler will terminate the proxy.
    if kill -0 "$MANAGER_PID" 2>/dev/null; then
        kill "$MANAGER_PID" 2>/dev/null || true
        # Wait up to 3 seconds for the manager (and its proxy child) to exit.
        DEADLINE=$((SECONDS + 3))
        while [[ $SECONDS -lt $DEADLINE ]]; do
            kill -0 "$MANAGER_PID" 2>/dev/null || break
            sleep 0.2
        done
    fi

    # Also signal unsloth directly so it doesn't linger.
    if kill -0 "$UNSLOTH_PID" 2>/dev/null; then
        kill "$UNSLOTH_PID" 2>/dev/null || true
    fi

    # Fall back: force-kill anything still listening on the relevant ports.
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

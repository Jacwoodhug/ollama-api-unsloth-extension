#!/usr/bin/env bash
set -uo pipefail

ROOT="$(dirname "$(realpath "$0")")"
PYTHON="$ROOT/studio/unsloth_studio/bin/python"
INDEX="$(find "$ROOT/studio/unsloth_studio/lib" -path "*/studio/frontend/dist/index.html" 2>/dev/null | head -1)"

# 1. Remove injected plugin tag from Studio WebUI
if [[ -z "$INDEX" || ! -f "$INDEX" ]]; then
    echo "[UNINSTALL] index.html not found — skipping WebUI cleanup"
elif ! grep -q '11435/plugin\.js' "$INDEX"; then
    echo "[UNINSTALL] Plugin tag not found in index.html — skipping"
else
    "$PYTHON" -c "
lines = open('$INDEX', encoding='utf-8').readlines()
lines = [l for l in lines if '11435/plugin.js' not in l]
open('$INDEX', 'w', encoding='utf-8').write(''.join(lines))
"
    echo "[UNINSTALL] Removed Ollama proxy plugin from Studio WebUI"
fi

# 2. Uninstall proxy requirements from studio Python
if [[ -f "$ROOT/ollama-api/requirements.txt" ]]; then
    echo "[UNINSTALL] Removing ollama-api dependencies..."
    "$PYTHON" -m pip uninstall -y -r "$ROOT/ollama-api/requirements.txt" --quiet
    echo "[UNINSTALL] Removed ollama-api dependencies"
fi

# 3. Remove ollama-api folder
if [[ -d "$ROOT/ollama-api" ]]; then
    rm -rf "$ROOT/ollama-api"
    echo "[UNINSTALL] Removed ollama-api/"
fi

# 4. Remove launch scripts and the other uninstall script
for file in launch-unsloth.ps1 launch-unsloth.sh uninstall.ps1; do
    if [[ -f "$ROOT/$file" ]]; then
        rm -f "$ROOT/$file"
        echo "[UNINSTALL] Removed $file"
    fi
done

echo "[UNINSTALL] Done."

# Self-delete
rm -f "$(realpath "$0")"

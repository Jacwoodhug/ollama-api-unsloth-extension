# Ollama API Unsloth Extension

This extension for Unsloth Studio exposes a fully **Ollama-compatible API** on port `11434` (configurable), so any client that uses the Ollama API (VS Code Copilot, Open WebUI, Continue, etc.) can use your locally-running Unsloth models without any client-side changes.

A persistent manager server and in-browser plugin let you control the proxy and edit its settings directly from the Studio sidebar.

---

## How It Works

This extension creates a proxy server running on the Ollama API port allowing Unsloth to be used with tools that do not support the OpenAI API that Unsloth uses.

```
Any Ollama-API-compatible client
(VS Code Copilot, Open WebUI, Continue…)
      │  speaks Ollama API protocol (port 11434)
      ▼
ollama-api/main.py  ← translates Ollama ↔ OpenAI-compatible format
      │  speaks OpenAI-compatible API (port 8888)
      ▼
Unsloth Studio (your local model)
```

`launch-unsloth.ps1` starts everything:
- **Manager** (`manager.py`) on port `11435` — manages the proxy lifecycle and serves the UI plugin
- **Proxy** (`main.py`) on port `11434` — auto-started by the manager
- **Unsloth Studio** on port `8888`
- Injects an **"Ollama Proxy" button** into the Studio sidebar on first launch

---

## Prerequisites

- Windows 10/11 or Linux
- [Unsloth Studio](https://unsloth.ai) installed with its bundled Python environment inside `studio/unsloth_studio/`
- [llama.cpp](https://github.com/ggerganov/llama.cpp) server binary at `llama.cpp/llama-server` (Linux) or `llama.cpp/llama-server.exe` (Windows)
- Linux only: `lsof` available (pre-installed on most distros)

---

## Installation

1. **Clone this repo** into the same folder that contains your `studio/` and `llama.cpp/` directories:
   ```powershell
   git clone <repo-url> .
   ```

2. **Launch** — everything else is automatic:

   **Windows:**
   ```powershell
   .\launch-unsloth.ps1
   ```

   **Linux:**
   ```bash
   chmod +x launch-unsloth.sh
   ./launch-unsloth.sh
   ```

   On first run this will:
   - Install proxy dependencies (`fastapi`, `httpx`, `uvicorn`, `python-dotenv`) into Unsloth's Python environment
   - Inject the Ollama Proxy plugin into the Studio WebUI
   - Start the manager, proxy, and Unsloth Studio
   - Open `http://127.0.0.1:8888` in your browser after 5 seconds

---

## Configuration

Settings are stored in `ollama-api/settings.json` (auto-created with defaults on first run, gitignored):

| Key | Default | Description |
|-----|---------|-------------|
| `unsloth_base_url` | `http://localhost:8888` | Unsloth Studio URL |
| `unsloth_api_key` | *(empty)* | API key shown in Studio |
| `model_context_length` | `32768` | Context window to report to Ollama clients |
| `proxy_host` | `0.0.0.0` | Interface for the proxy to bind to |
| `proxy_port` | `11434` | Port the Ollama-compatible proxy listens on |

**Priority chain:** `.env` file > `settings.json` > hardcoded defaults.

### Editing Settings

- **In-browser:** Click **Ollama Proxy** in the Studio sidebar → edit fields → **Save**
- **Manually:** Edit `ollama-api/settings.json` directly, then restart the proxy (Stop → Start in the modal)

### Finding Your API Key

Open Unsloth Studio → Settings — the API key is displayed there. Copy it into the **API Key** field in the Ollama Proxy modal.

### Context Length

Set `model_context_length` to match the context window of the model you have loaded in Unsloth. If this is too low, tools like VS Code Copilot will compact conversations prematurely.

---

## Connecting an Ollama Client

Point any Ollama-compatible client at `http://localhost:11434`. Examples:

**VS Code (settings.json):**
```json
"github.copilot.chat.ollama.endpoint": "http://localhost:11434"
```

**curl:**
```bash
curl http://localhost:11434/api/tags
```

---

## Stopping

Press `Ctrl+C` in the terminal running the launch script. It will kill all three processes (proxy, manager, Unsloth Studio) and clean up.

---

## Uninstalling

The only persistent change made outside this repo is the `<script>` tag injected into the Studio WebUI. To remove it:

**Windows:**
```powershell
.\uninstall.ps1
```

**Linux:**
```bash
chmod +x uninstall.sh
./uninstall.sh
```

Afterward you can delete the cloned files. The Studio and llama.cpp directories are left untouched.

---

## Project Structure

```
~/.unsloth/                 # Unsloth root (pre-existing)
├── studio/                 # Unsloth Studio (pre-existing)
├── llama.cpp/              # llama.cpp binaries (pre-existing)
├── launch-unsloth.ps1      # ← this repo: entry point (Windows)
├── launch-unsloth.sh       # ← this repo: entry point (Linux)
├── uninstall.ps1           # ← this repo: removes WebUI injection (Windows)
├── uninstall.sh            # ← this repo: removes WebUI injection (Linux)
└── ollama-api/             # ← this repo: proxy + manager + plugin
```

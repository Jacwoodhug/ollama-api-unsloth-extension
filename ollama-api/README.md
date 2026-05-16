# Ollama-to-Unsloth Proxy

A FastAPI proxy server that translates Ollama API requests to Unsloth's OpenAI-compatible API.

## Features

- **Ollama-compatible API** on port `11434`
- **Unsloth backend** integration via OpenAI-compatible endpoints
- **Streaming support** for real-time responses
- **Environment-based configuration**

## Requirements

- Python 3.10+
- `uv` (Python package manager)

## Installation

1. Clone this repository
2. Run `run.bat` (Windows) or manually:
   ```bash
   uv venv .venv
   uv pip install -r requirements.txt
   ```

## Configuration

You can configure the proxy using either a `.env` file or VS Code user settings. The proxy checks configuration in this priority order:

1. **`.env` file** (highest priority)
2. **VS Code user settings** (if `.env` is absent or values are missing)
3. **Default values** (lowest priority)

### Option 1: `.env` File

Create a `.env` file in the project root:

```env
UNSLOTH_BASE_URL=http://localhost:8888
UNSLOTH_API_KEY=your_api_key_here
PROXY_HOST=0.0.0.0
PROXY_PORT=11434
MODEL_CONTEXT_LENGTH=65536
```

### Option 2: VS Code User Settings

If you prefer not to store sensitive information in a `.env` file, you can use VS Code user settings instead.

#### Initialize VS Code Settings

Run the initialization script to set up VS Code user settings:

```bash
python init_settings.py
```

Or double-click `init_settings.bat` on Windows.

This will prompt you for your API key and context length, then save them to your VS Code user settings (`%APPDATA%\Code\User\settings.json` on Windows).

#### Manual Configuration

You can also manually edit your VS Code user settings (`Ctrl+,` → click the `{}` icon → "Open user settings") and add:

```json
{
    "unsloth.baseUrl": "http://localhost:8888",
    "unsloth.apiKey": "your_api_key_here",
    "unsloth.contextLength": 65536
}
```

> **Note**: `UNSLOTH_BASE_URL`, `UNSLOTH_API_KEY`, and `MODEL_CONTEXT_LENGTH` can be configured via VS Code settings or `.env`. The other settings (`PROXY_HOST`, `PROXY_PORT`) must be configured via `.env` only.

## Usage

Run `run.bat` to start the proxy server. The server will be available at `http://localhost:11434`.

## API Coverage

The proxy implements Ollama-compatible endpoints including:
- `/v1/chat/completions`
- `/v1/models`
- `/health`

See `PLAN.md` for full endpoint mapping.

## Hard-coded Values

The following values are hard-coded and not configurable via environment variables:

- **Ollama version** (`/api/version`): `0.24.0`
- **Model capabilities**: `["completion", "tools"]`

If your Ollama client checks for a specific version or capability set, you may need to update these values in `main.py` and `translate.py` respectively.

## Important: Context Length Configuration

### Why Context Length Matters

When using this proxy with **VS Code Copilot**, the context length reported by the proxy determines how much context Copilot will use before triggering "Conversation Compact". If the context length is too low, Copilot will compact conversations prematurely, reducing effective context.

### Setting Context Length in `.env`

Set `MODEL_CONTEXT_LENGTH` in your `.env` file to match your model's actual context window:

```env
MODEL_CONTEXT_LENGTH=65536
```

### Loading the Model in Unsloth

The proxy uses your configured `MODEL_CONTEXT_LENGTH` value (from `.env` or VS Code settings). Load any model you want in Unsloth — the proxy doesn't automatically detect the model's context window. You need to manually set `MODEL_CONTEXT_LENGTH` to match your model's actual context size.

For example, if you load a model with 65536 context in Unsloth but leave `MODEL_CONTEXT_LENGTH` at the default 32768, the proxy will report 32768 to Copilot (not the actual 65536).

### Architecture-Specific Context Length

The proxy automatically adds architecture-specific context length keys (e.g., `qwen35.context_length`) to the `/api/show` response. This ensures VS Code Copilot correctly reads the context length instead of falling back to the default 32768.

If you're using a custom model architecture, ensure `_guess_details()` in `translate.py` correctly identifies it so the appropriate context length key is generated.
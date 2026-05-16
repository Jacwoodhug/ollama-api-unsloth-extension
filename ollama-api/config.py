"""
Configuration module for the Ollama API proxy.
Priority chain: .env > settings.json > hardcoded defaults.
"""

import json
import os
import tempfile
from pathlib import Path

SETTINGS_PATH: Path = Path(__file__).parent / "settings.json"

_DEFAULTS: dict = {
    "unsloth_base_url": "http://localhost:8888",
    "unsloth_api_key": "",
    "model_context_length": 32768,
    "proxy_host": "0.0.0.0",
    "proxy_port": 11434,
}

_KEY_TO_ENV: dict[str, str] = {
    "unsloth_base_url": "UNSLOTH_BASE_URL",
    "unsloth_api_key": "UNSLOTH_API_KEY",
    "model_context_length": "MODEL_CONTEXT_LENGTH",
    "proxy_host": "PROXY_HOST",
    "proxy_port": "PROXY_PORT",
}


def read_proxy_settings() -> dict:
    """Load settings.json; create it with defaults if it does not exist."""
    if not SETTINGS_PATH.exists():
        write_proxy_settings(dict(_DEFAULTS))
        return dict(_DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def write_proxy_settings(data: dict) -> None:
    """Atomically write data to settings.json."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=SETTINGS_PATH.parent, suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, SETTINGS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_config_value(key: str, default: str = "") -> str:
    """
    Get a configuration value.
    Priority: .env env var > settings.json > default.

    key: settings.json key name (e.g. 'unsloth_base_url')
    """
    env_var = _KEY_TO_ENV.get(key)
    if env_var:
        env_value = os.getenv(env_var)
        if env_value is not None:
            return env_value

    settings = read_proxy_settings()
    if key in settings:
        return str(settings[key])

    return default


def get_int_config_value(key: str, default: int) -> int:
    """Get an integer configuration value (same priority chain as get_config_value)."""
    value = get_config_value(key, str(default))
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

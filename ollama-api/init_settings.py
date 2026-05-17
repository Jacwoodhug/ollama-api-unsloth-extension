#!/usr/bin/env python3
"""
Initialize ollama-api/settings.json for Unsloth proxy configuration.
Only adds keys that are not already present (skips existing keys).
"""

import sys
from config import read_proxy_settings, write_proxy_settings, SETTINGS_PATH

_DEFAULTS = {
    "unsloth_base_url": "http://127.0.0.1:8888",
    "unsloth_api_key": "",
    "model_context_length": 32768,
    "proxy_host": "0.0.0.0",
    "proxy_port": 11434,
}


def main():
    base_url_input = input(
        "Enter Unsloth base URL (or press Enter for default http://127.0.0.1:8888): "
    ).strip()
    api_key_input = input(
        "Enter your Unsloth API key (or press Enter to skip): "
    ).strip()
    context_length_input = input(
        "Enter context length (or press Enter for default 32768): "
    ).strip()

    overrides: dict = {}
    if base_url_input:
        overrides["unsloth_base_url"] = base_url_input
    if api_key_input:
        overrides["unsloth_api_key"] = api_key_input
    if context_length_input:
        try:
            overrides["model_context_length"] = int(context_length_input)
        except ValueError:
            print("Invalid context length, skipping.")

    existing = read_proxy_settings()
    result: dict[str, str] = {}

    merged = dict(_DEFAULTS)
    merged.update(existing)

    for key, value in overrides.items():
        if key in existing:
            result[key] = "skipped"
        else:
            merged[key] = value
            result[key] = "added"

    write_proxy_settings(merged)

    print(f"\nSettings saved to: {SETTINGS_PATH}")
    for key, status in result.items():
        print(f"  {key}: {status}")


if __name__ == "__main__":
    main()

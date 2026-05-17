"""
Fetches the available model list from the Unsloth Studio API
(GET /api/models/local) and caches the result for 30 seconds.
Replaces the old filesystem scanner.
"""

import time

import httpx

from config import get_config_value

# Name fragments that indicate a vision-capable model (case-insensitive).
_VISION_KEYWORDS = {
    "llava", "vision", "-vl", "vl-", "qwen2vl", "paligemma",
    "idefics", "internvl", "cogvlm", "moondream", "minicpm-v",
}

_CACHE_TTL = 30.0  # seconds
_cache: tuple[float, list[dict]] | None = None  # (timestamp, results)
_variants_cache: dict[str, tuple[float, dict]] = {}  # repo_id → (timestamp, info)


def _fetch_gguf_info(
    client: httpx.Client, base_url: str, headers: dict, repo_id: str
) -> dict:
    """Return GGUF repo info from the variants API, with 30 s caching.

    Returns {"variants": [{quant, filename, size_bytes}, ...], "has_vision": bool}
    """
    global _variants_cache
    now = time.monotonic()
    cached = _variants_cache.get(repo_id)
    if cached is not None and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    try:
        r = client.get(
            f"{base_url}/api/models/gguf-variants",
            headers=headers,
            params={"repo_id": repo_id},
        )
        if not r.is_success:
            info: dict = {"variants": [], "has_vision": False}
            _variants_cache[repo_id] = (now, info)
            return info
        data = r.json()
        variants = [
            {
                "quant": v["quant"],
                "filename": v["filename"],
                "size_bytes": v.get("size_bytes", 0),
            }
            for v in data.get("variants", [])
            if v.get("downloaded")
        ]
        info = {"variants": variants, "has_vision": bool(data.get("has_vision", False))}
    except Exception:
        info = {"variants": [], "has_vision": False}

    _variants_cache[repo_id] = (now, info)
    return info


def scan_models() -> list[dict]:
    """Return only *downloaded* models, cross-referencing three Studio endpoints.

    1. GET /api/models/cached-models  → repo_ids of downloaded transformer models
    2. GET /api/models/cached-gguf    → repo_ids of downloaded GGUF models
    3. GET /api/models/local          → full list (path, display_name, …)

    Only entries from /local whose model_id appears in the cached sets are kept.
    Results are cached for 30 s.

    Each returned dict has:
        name        – display name / Ollama model ID  (e.g. "gemma-4-E2B-it")
        model_id    – HuggingFace repo ID             (e.g. "unsloth/gemma-4-E2B-it")
        path        – absolute path as reported by Studio
        format      – "gguf" | "transformers"
        is_gguf     – bool
        is_vision   – bool (name-heuristic)
        is_audio    – bool (always False)
        size_bytes  – int  (from cached endpoint where available, else 0)
        mtime       – float (Unix timestamp from updated_at)
    """
    global _cache
    now = time.monotonic()
    if _cache is not None and (now - _cache[0]) < _CACHE_TTL:
        return _cache[1]

    base_url = get_config_value("unsloth_base_url", "http://127.0.0.1:8888")
    api_key = get_config_value("unsloth_api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        with httpx.Client(timeout=10.0) as client:
            r_local = client.get(f"{base_url}/api/models/local", headers=headers)
            r_local.raise_for_status()

            r_tf = client.get(f"{base_url}/api/models/cached-models", headers=headers)
            r_gguf = client.get(f"{base_url}/api/models/cached-gguf", headers=headers)

            local_data = r_local.json()

            # Build set of downloaded repo_ids from both cached endpoints.
            downloaded: set[str] = set()
            size_map: dict[str, int] = {}
            if r_tf.is_success:
                for entry in r_tf.json().get("cached", []):
                    rid = entry.get("repo_id", "")
                    if rid:
                        downloaded.add(rid)
                        size_map[rid] = entry.get("size_bytes", 0)
            if r_gguf.is_success:
                for entry in r_gguf.json().get("cached", []):
                    rid = entry.get("repo_id", "")
                    if rid:
                        downloaded.add(rid)
                        size_map[rid] = entry.get("size_bytes", 0)

            results = []
            for m in local_data.get("models", []):
                model_id = m.get("model_id") or m.get("id", "")
                if model_id not in downloaded:
                    continue
                base_entry = _map_model(m)
                base_entry["size_bytes"] = size_map.get(model_id, 0)

                if base_entry["is_gguf"]:
                    info = _fetch_gguf_info(client, base_url, headers, model_id)
                    # Use authoritative has_vision from the API, not the name heuristic.
                    base_entry["is_vision"] = info["has_vision"]
                    if info["variants"]:
                        display_name = base_entry["name"]
                        for v in info["variants"]:
                            entry = dict(base_entry)
                            entry["name"] = f"{display_name}:{v['quant']}"
                            entry["quant"] = v["quant"]
                            entry["quant_filename"] = v["filename"]
                            entry["size_bytes"] = v["size_bytes"]
                            results.append(entry)
                        continue

                results.append(base_entry)

    except Exception:
        return _cache[1] if _cache is not None else []

    results.sort(key=lambda m: m["name"].lower())
    _cache = (now, results)
    return results


def _map_model(m: dict) -> dict:
    display_name: str = m.get("display_name") or m.get("id", "")
    name_lower = display_name.lower()
    is_gguf = "-gguf" in name_lower
    is_vision = any(kw in name_lower for kw in _VISION_KEYWORDS)
    return {
        "name": display_name,
        "model_id": m.get("model_id") or m.get("id", ""),
        "path": m.get("path", ""),
        "format": "gguf" if is_gguf else "transformers",
        "is_gguf": is_gguf,
        "is_vision": is_vision,
        "is_audio": False,
        "size_bytes": 0,
        "mtime": m.get("updated_at", 0.0),
        "quant": "",
        "quant_filename": "",
    }

"""
Pure translation module: converts between Ollama and OpenAI API shapes.
No network calls, no FastAPI/httpx imports. Standard library only.
"""

import json
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unix_to_iso(ts: Optional[int]) -> str:
    """Convert a Unix timestamp (int) to an ISO 8601 string with Z suffix."""
    if ts is None:
        return _now_iso()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _map_finish_reason(reason: Optional[str]) -> str:
    mapping = {"stop": "stop", "length": "length", "tool_calls": "tool_calls"}
    return mapping.get(reason or "", "stop")


def _map_options(options: dict, out: dict) -> None:
    """Map Ollama options fields into an OpenAI-compatible dict in-place."""
    option_map = {
        "temperature": "temperature",
        "top_p": "top_p",
        "seed": "seed",
        "stop": "stop",
        "num_predict": "max_tokens",
    }
    for ollama_key, oai_key in option_map.items():
        if ollama_key in options:
            out[oai_key] = options[ollama_key]
    # top_k is dropped (not in OpenAI spec)


# ---------------------------------------------------------------------------
# Request translators (Ollama → OpenAI)
# ---------------------------------------------------------------------------

def ollama_chat_to_openai(body: dict) -> dict:
    """Translate an Ollama /api/chat request body to OpenAI /v1/chat/completions."""
    out: dict = {}

    out["model"] = body["model"]
    out["messages"] = body.get("messages", [])
    out["stream"] = body.get("stream", False)

    # tools passthrough (formats are compatible)
    if "tools" in body:
        out["tools"] = body["tools"]

    # format → response_format
    fmt = body.get("format")
    if fmt == "json":
        out["response_format"] = {"type": "json_object"}
    elif isinstance(fmt, dict):
        out["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": fmt,
                "strict": True,
            },
        }

    # options mapping
    _map_options(body.get("options", {}), out)

    # think and all other unknown top-level fields are dropped
    return out


def ollama_generate_to_openai(body: dict) -> dict:
    """Translate an Ollama /api/generate request body to OpenAI /v1/chat/completions."""
    out: dict = {}

    out["model"] = body["model"]
    out["stream"] = body.get("stream", False)

    messages = []

    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": system})

    prompt = body.get("prompt", "")
    images = body.get("images", [])
    if images:
        content = [{"type": "text", "text": prompt}]
        for image in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image}"},
            })
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})

    out["messages"] = messages

    _map_options(body.get("options", {}), out)

    return out


def v1_completions_to_openai_chat(body: dict) -> dict:
    """Translate an OpenAI /v1/completions request to /v1/chat/completions."""
    out: dict = {
        "model": body["model"],
        "messages": [{"role": "user", "content": body.get("prompt", "")}],
    }

    for key in ("stream", "temperature", "top_p", "max_tokens", "stop", "seed"):
        if key in body:
            out[key] = body[key]

    return out


# ---------------------------------------------------------------------------
# Response translators (OpenAI → Ollama, non-streaming)
# ---------------------------------------------------------------------------

def openai_to_ollama_chat_response(oai: dict, model: str) -> dict:
    """Translate a non-streaming OpenAI chat completions response to Ollama /api/chat."""
    choice = oai.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = oai.get("usage", {})

    out_message: dict = {
        "role": message.get("role", "assistant"),
        "content": message.get("content") or "",
    }

    tool_calls = message.get("tool_calls")
    if tool_calls:
        converted = []
        for tc in tool_calls:
            func = tc.get("function", {})
            arguments = func.get("arguments", "{}")
            try:
                parsed_args = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                parsed_args = {}
            converted.append({
                "function": {
                    "name": func.get("name", ""),
                    "arguments": parsed_args,
                }
            })
        out_message["tool_calls"] = converted

    return {
        "model": model,
        "created_at": _unix_to_iso(oai.get("created")),
        "message": out_message,
        "done": True,
        "done_reason": _map_finish_reason(choice.get("finish_reason")),
        "total_duration": 0,
        "load_duration": 0,
        "eval_duration": 0,
        "prompt_eval_count": usage.get("prompt_tokens", 0),
        "eval_count": usage.get("completion_tokens", 0),
    }


def openai_to_ollama_generate_response(oai: dict, model: str) -> dict:
    """Translate a non-streaming OpenAI response to Ollama /api/generate."""
    choice = oai.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = oai.get("usage", {})

    return {
        "model": model,
        "created_at": _unix_to_iso(oai.get("created")),
        "response": message.get("content") or "",
        "done": True,
        "done_reason": _map_finish_reason(choice.get("finish_reason")),
        "total_duration": 0,
        "load_duration": 0,
        "eval_duration": 0,
        "prompt_eval_count": usage.get("prompt_tokens", 0),
        "eval_count": usage.get("completion_tokens", 0),
    }


# ---------------------------------------------------------------------------
# Streaming chunk translators (OpenAI SSE → Ollama NDJSON)
# ---------------------------------------------------------------------------

def openai_chunk_to_ollama_chat_chunk(chunk: dict, model: str) -> Optional[dict]:
    """Translate a parsed OpenAI SSE chunk to an Ollama /api/chat NDJSON chunk."""
    choices = chunk.get("choices")
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")
    content = delta.get("content") or ""

    # Handle streaming tool call deltas
    tool_call_deltas = delta.get("tool_calls")

    if finish_reason is None:
        out: dict = {
            "model": model,
            "created_at": _now_iso(),
            "message": {"role": "assistant", "content": content},
            "done": False,
        }
        return out

    # Final chunk
    usage = chunk.get("usage", {})
    return {
        "model": model,
        "created_at": _now_iso(),
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": _map_finish_reason(finish_reason),
        "total_duration": 0,
        "load_duration": 0,
        "eval_duration": 0,
        "prompt_eval_count": usage.get("prompt_tokens", 0) if usage else 0,
        "eval_count": usage.get("completion_tokens", 0) if usage else 0,
    }


def openai_chunk_to_ollama_generate_chunk(chunk: dict, model: str) -> Optional[dict]:
    """Translate a parsed OpenAI SSE chunk to an Ollama /api/generate NDJSON chunk."""
    choices = chunk.get("choices")
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")
    content = delta.get("content") or ""

    if finish_reason is None:
        return {
            "model": model,
            "created_at": _now_iso(),
            "response": content,
            "done": False,
        }

    usage = chunk.get("usage", {})
    return {
        "model": model,
        "created_at": _now_iso(),
        "response": "",
        "done": True,
        "done_reason": _map_finish_reason(finish_reason),
        "total_duration": 0,
        "load_duration": 0,
        "eval_duration": 0,
        "prompt_eval_count": usage.get("prompt_tokens", 0) if usage else 0,
        "eval_count": usage.get("completion_tokens", 0) if usage else 0,
    }


# ---------------------------------------------------------------------------
# Streaming tool call accumulator
# ---------------------------------------------------------------------------

def assemble_tool_calls(accumulated: list) -> list:
    """
    Convert accumulated OpenAI streaming tool call deltas to Ollama-format tool calls.

    Each element of `accumulated` is a partial tool_call dict as streamed, with
    `id`, `type`, and `function` (containing `name` and `arguments` as a string
    that has been concatenated across chunks).
    """
    result = []
    for tc in accumulated:
        func = tc.get("function", {})
        arguments_str = func.get("arguments", "")
        try:
            parsed_args = json.loads(arguments_str)
        except (json.JSONDecodeError, TypeError):
            parsed_args = {}
        result.append({
            "function": {
                "name": func.get("name", ""),
                "arguments": parsed_args,
            }
        })
    return result


# ---------------------------------------------------------------------------
# Model list translators
# ---------------------------------------------------------------------------

def _guess_details(model_id: str, fmt: str = "") -> dict:
    """Infer Ollama-style model details from an OpenAI model ID string."""
    name = model_id.lower()
    if "qwen3" in name:
        family = "qwen35"
    elif "qwen2" in name or "qwen" in name:
        family = "qwen2"
    elif "llama" in name:
        family = "llama"
    elif "gemma4" in name:
        family = "gemma4"
    elif "gemma" in name:
        family = "gemma"
    elif "mistral" in name or "mixtral" in name:
        family = "mistral"
    elif "phi" in name:
        family = "phi3"
    elif "deepseek" in name:
        family = "deepseek"
    elif "gemini" in name:
        family = "gemini"
    else:
        family = ""
    # Prefer the explicit quant tag suffix (e.g. "gemma-4-E2B-it-GGUF:UD-Q4_K_XL")
    # over guessing from the model ID string, since the suffix is unambiguous.
    if ":" in model_id:
        quant = model_id.split(":", 1)[1]
    else:
        quant = ""
        for q in ["Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q4_K_M", "Q4_K_S",
                  "Q4_0", "Q3_K_M", "Q2_K", "IQ4_XS", "IQ3_M", "F16", "BF16", "F32"]:
            if q.upper() in model_id.upper():
                quant = q
                break
    resolved_fmt = fmt if fmt else ("gguf" if "gguf" in name else "")
    return {
        "parent_model": "",
        "format": resolved_fmt,
        "family": family,
        "families": [family] if family else [],
        "parameter_size": "",
        "quantization_level": quant,
    }


def openai_models_to_ollama_tags(oai: dict) -> dict:
    """Convert OpenAI /v1/models response to Ollama /api/tags response."""
    models = []
    for m in oai.get("data", []):
        model_id = m.get("id", "")
        models.append({
            "name": model_id,
            "model": model_id,
            "modified_at": _unix_to_iso(m.get("created")),
            "size": 0,
            "digest": "",
            "details": _guess_details(model_id),
        })
    return {"models": models}


def openai_models_to_ollama_ps(oai: dict) -> dict:
    """Convert OpenAI /v1/models response to Ollama /api/ps response."""
    models = []
    for m in oai.get("data", []):
        model_id = m.get("id", "")
        models.append({
            "name": model_id,
            "model": model_id,
            "modified_at": _unix_to_iso(m.get("created")),
            "size": 0,
            "digest": "",
            "details": _guess_details(model_id),
            "expires_at": "",
            "size_vram": 0,
        })
    return {"models": models}


def openai_models_to_ollama_show(oai: dict, name: str, context_length: int = 32768, capabilities: Optional[list] = None) -> Optional[dict]:
    """Find a model by name and return an Ollama /api/show response, or None."""
    # Strip Ollama tag suffix (e.g. "llama3:latest" -> "llama3") for fallback matching
    base_name = name.split(":")[0]
    for m in oai.get("data", []):
        model_id = m.get("id", "")
        if model_id == name or model_id == base_name:
            details = _guess_details(model_id)
            arch = details.get("family", "")
            model_info: dict = {"llm.context_length": context_length}
            if arch:
                model_info["general.architecture"] = arch
                model_info[f"{arch}.context_length"] = context_length
            return {
                "modelfile": "",
                "parameters": "",
                "template": "",
                "details": details,
                "model_info": model_info,
                "capabilities": capabilities if capabilities is not None else ["completion", "tools"],
                "name": name,
            }
    return None


def _path_mtime_iso(mtime: float) -> str:
    """Convert a file mtime (float) to an ISO 8601 string with Z suffix."""
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_models_to_ollama_tags(local_models: list[dict]) -> dict:
    """Convert scanned local model dicts to Ollama /api/tags format."""
    models = []
    for m in local_models:
        name = m["name"]
        details = _guess_details(name, fmt=m.get("format", ""))
        models.append({
            "name": name,
            "model": name,
            "modified_at": _path_mtime_iso(m.get("mtime", 0.0)),
            "size": m.get("size_bytes", 0),
            "digest": "",
            "details": details,
        })
    return {"models": models}


def local_model_to_ollama_show(local_model: dict, name: str, context_length: int, capabilities: Optional[list] = None) -> dict:
    """Build /api/show response for a locally found but not-yet-loaded model."""
    fmt = local_model.get("format", "")
    details = _guess_details(name, fmt=fmt)
    arch = details.get("family", "")
    model_info: dict = {"llm.context_length": context_length}
    if arch:
        model_info["general.architecture"] = arch
        model_info[f"{arch}.context_length"] = context_length
    if capabilities is None:
        capabilities = ["completion", "tools"]
        if local_model.get("is_vision"):
            capabilities.append("vision")
    return {
        "modelfile": "",
        "parameters": "",
        "template": "",
        "details": details,
        "model_info": model_info,
        "capabilities": capabilities,
        "name": name,
    }

"""
FastAPI proxy: Ollama-compatible API → Unsloth (OpenAI-compatible) backend.
"""

import glob as _glob
import json
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

import model_scanner
import translate
from config import get_config_value, get_int_config_value, read_proxy_settings, read_model_settings

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

UNSLOTH_BASE_URL: str = get_config_value("unsloth_base_url", "http://127.0.0.1:8888")
UNSLOTH_API_KEY: str = get_config_value("unsloth_api_key", "")
PROXY_HOST: str = get_config_value("proxy_host", "0.0.0.0")
PROXY_PORT: str = get_config_value("proxy_port", "11434")
MODEL_CONTEXT_LENGTH: int = get_int_config_value("model_context_length", 32768)
AUTO_SWITCH_MODEL: bool = get_config_value("auto_switch_model", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Model directory helpers
# ---------------------------------------------------------------------------

def scan_model_directory() -> list[dict]:
    """Fetch the model list from Studio's /api/models/local."""
    return model_scanner.scan_models()


def find_local_model(model_name: str) -> dict | None:
    """Find a model by display name or full HF model_id.

    Checks exact name first (supports quant-suffixed names like
    'gemma-4-E2B-it-GGUF:BF16'), then falls back to the base name
    (strip everything after ':') for bare model requests.
    """
    clean_base = model_name.split(":")[0]
    fallback: dict | None = None
    for m in scan_model_directory():
        if m["name"] == model_name:
            return m
        if fallback is None and (m["name"] == clean_base or m.get("model_id") == clean_base):
            fallback = m
    return fallback


def _resolve_quant_path(repo_path: str, filename: str) -> str:
    """Return the full path to a specific .gguf file in the HF cache snapshot dir."""
    pattern = os.path.join(repo_path, "snapshots", "*", filename)
    matches = _glob.glob(pattern)
    return matches[0] if matches else repo_path


def _get_model_cfg(model_name: str) -> dict:
    """Return model config dict, checking quant-specific key first then base name."""
    configs = read_model_settings()
    return configs.get(model_name) or configs.get(model_name.split(":")[0]) or {}


def get_model_context(model_name: str) -> int:
    """Return per-model context length, falling back to global MODEL_CONTEXT_LENGTH."""
    return _get_model_cfg(model_name).get("context_length") or MODEL_CONTEXT_LENGTH


async def ensure_model_loaded(client: httpx.AsyncClient, model_name: str) -> None:
    """If model_name is not currently loaded in Studio, load it (and replace current)."""
    local_model = find_local_model(model_name)
    if local_model is None:
        return  # not a local model — pass through, Studio will error if invalid

    # Check currently loaded model and active quant via inference/status.
    try:
        status_resp = await client.get("/api/inference/status")
        if status_resp.is_success:
            st = status_resp.json()
            if local_model["model_id"] in st.get("loaded", []):
                wanted_quant = local_model.get("quant", "")
                active_quant = st.get("gguf_variant", "")
                if not wanted_quant or wanted_quant == active_quant:
                    return  # already loaded with the right quant
    except Exception:
        pass

    cfg = _get_model_cfg(model_name)
    context = cfg.get("context_length") or MODEL_CONTEXT_LENGTH
    extra_args_str = cfg.get("extra_args", "")
    extra_args = extra_args_str.split() if extra_args_str.strip() else None

    model_path = local_model["path"]
    quant_filename = local_model.get("quant_filename", "")
    if quant_filename:
        model_path = _resolve_quant_path(model_path, quant_filename)

    load_req: dict = {"model_path": model_path, "max_seq_length": context}
    if extra_args and local_model["is_gguf"]:
        load_req["llama_extra_args"] = extra_args

    try:
        await client.post("/v1/load", json=load_req, timeout=300.0)
    except Exception:
        pass  # let the subsequent chat request surface the error naturally


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        base_url=UNSLOTH_BASE_URL,
        headers={"Authorization": f"Bearer {UNSLOTH_API_KEY}"},
        timeout=300.0,
    )
    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

async def sse_to_ndjson_chat(
    response: httpx.Response, model: str
) -> AsyncGenerator[bytes, None]:
    """Convert OpenAI SSE stream to Ollama /api/chat NDJSON, accumulating tool calls."""
    accumulated_tool_calls: dict[int, dict] = {}

    async for line in response.aiter_lines():
        if not line:
            continue
        if not line.startswith("data: "):
            continue
        content = line[len("data: "):]
        if content == "[DONE]":
            break

        try:
            chunk = json.loads(content)
        except json.JSONDecodeError:
            continue

        # Accumulate tool call deltas before translating
        finish_reason = None
        choices = chunk.get("choices", [])
        if choices:
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            delta = choice.get("delta", {})
            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                if idx not in accumulated_tool_calls:
                    accumulated_tool_calls[idx] = {
                        "id": tc_delta.get("id", ""),
                        "type": tc_delta.get("type", "function"),
                        "function": {"name": "", "arguments": ""},
                    }
                acc = accumulated_tool_calls[idx]
                func_delta = tc_delta.get("function", {})
                if func_delta.get("name"):
                    acc["function"]["name"] += func_delta["name"]
                if func_delta.get("arguments"):
                    acc["function"]["arguments"] += func_delta["arguments"]

        result = translate.openai_chunk_to_ollama_chat_chunk(chunk, model)
        if result is None:
            continue

        # On final tool_calls chunk, inject assembled tool calls
        if finish_reason == "tool_calls" and accumulated_tool_calls:
            ordered = [accumulated_tool_calls[k] for k in sorted(accumulated_tool_calls)]
            result["message"]["tool_calls"] = translate.assemble_tool_calls(ordered)

        yield (json.dumps(result) + "\n").encode()


async def sse_to_ndjson_generate(
    response: httpx.Response, model: str
) -> AsyncGenerator[bytes, None]:
    """Convert OpenAI SSE stream to Ollama /api/generate NDJSON."""
    async for line in response.aiter_lines():
        if not line:
            continue
        if not line.startswith("data: "):
            continue
        content = line[len("data: "):]
        if content == "[DONE]":
            break

        try:
            chunk = json.loads(content)
        except json.JSONDecodeError:
            continue

        result = translate.openai_chunk_to_ollama_generate_chunk(chunk, model)
        if result is not None:
            yield (json.dumps(result) + "\n").encode()


async def sse_passthrough(response: httpx.Response) -> AsyncGenerator[bytes, None]:
    """Pass SSE lines through unchanged as bytes."""
    async for line in response.aiter_lines():
        yield (line + "\n").encode()


async def chat_sse_to_completions_sse(
    response: httpx.Response,
) -> AsyncGenerator[bytes, None]:
    """Convert chat/completions SSE chunks to completions SSE chunks.

    Rewrites delta.content -> text and object 'chat.completion.chunk' ->
    'text_completion' so /v1/completions clients get the schema they expect.
    """
    async for line in response.aiter_lines():
        if not line:
            yield b"\n"
            continue
        if not line.startswith("data: "):
            yield (line + "\n").encode()
            continue
        content = line[len("data: "):]
        if content == "[DONE]":
            yield b"data: [DONE]\n\n"
            break
        try:
            chunk = json.loads(content)
        except json.JSONDecodeError:
            yield (line + "\n").encode()
            continue
        # Convert chat chunk shape to completions chunk shape
        choices = chunk.get("choices", [])
        new_choices = []
        for c in choices:
            delta = c.get("delta", {})
            new_choices.append({
                "index": c.get("index", 0),
                "text": delta.get("content") or "",
                "finish_reason": c.get("finish_reason"),
            })
        out = {
            "id": chunk.get("id", ""),
            "object": "text_completion",
            "created": chunk.get("created", 0),
            "model": chunk.get("model", ""),
            "choices": new_choices,
        }
        yield f"data: {json.dumps(out)}\n\n".encode()


# ---------------------------------------------------------------------------
# Non-streaming proxy helper
# ---------------------------------------------------------------------------

async def _proxy_get(client: httpx.AsyncClient, path: str) -> httpx.Response:
    try:
        response = await client.get(path)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


async def _proxy_post(
    client: httpx.AsyncClient, path: str, **kwargs
) -> httpx.Response:
    try:
        response = await client.post(path, **kwargs)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Ollama API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    model: str = body.get("model", "")
    stream: bool = body.get("stream", True)
    oai_request = translate.ollama_chat_to_openai(body)
    oai_request["stream"] = stream
    client: httpx.AsyncClient = request.app.state.client
    if AUTO_SWITCH_MODEL:
        await ensure_model_loaded(client, model)

    if stream:
        async def _stream() -> AsyncGenerator[bytes, None]:
            try:
                async with client.stream(
                    "POST", "/v1/chat/completions", json=oai_request
                ) as response:
                    async for chunk in sse_to_ndjson_chat(response, model):
                        yield chunk
            except httpx.HTTPStatusError as exc:
                yield (json.dumps({"error": f"upstream error {exc.response.status_code}"}) + "\n").encode()
            except httpx.RequestError as exc:
                yield (json.dumps({"error": f"upstream connection error: {exc}"}) + "\n").encode()

        return StreamingResponse(_stream(), media_type="application/x-ndjson")

    response = await _proxy_post(client, "/v1/chat/completions", json=oai_request)
    return translate.openai_to_ollama_chat_response(response.json(), model)


@app.post("/api/generate")
async def api_generate(request: Request):
    body = await request.json()
    model: str = body.get("model", "")
    stream: bool = body.get("stream", True)
    oai_request = translate.ollama_generate_to_openai(body)
    oai_request["stream"] = stream
    client: httpx.AsyncClient = request.app.state.client
    if AUTO_SWITCH_MODEL:
        await ensure_model_loaded(client, model)

    if stream:
        async def _stream() -> AsyncGenerator[bytes, None]:
            try:
                async with client.stream(
                    "POST", "/v1/chat/completions", json=oai_request
                ) as response:
                    async for chunk in sse_to_ndjson_generate(response, model):
                        yield chunk
            except httpx.HTTPStatusError as exc:
                yield (json.dumps({"error": f"upstream error {exc.response.status_code}"}) + "\n").encode()
            except httpx.RequestError as exc:
                yield (json.dumps({"error": f"upstream connection error: {exc}"}) + "\n").encode()

        return StreamingResponse(_stream(), media_type="application/x-ndjson")

    response = await _proxy_post(client, "/v1/chat/completions", json=oai_request)
    return translate.openai_to_ollama_generate_response(response.json(), model)


@app.get("/api/tags")
async def api_tags(request: Request):
    if AUTO_SWITCH_MODEL:
        configs = read_model_settings()
        models = [m for m in scan_model_directory() if not configs.get(m["name"], {}).get("hidden")]
        return translate.local_models_to_ollama_tags(models)
    client: httpx.AsyncClient = request.app.state.client
    response = await _proxy_get(client, "/v1/models")
    return translate.openai_models_to_ollama_tags(response.json())


@app.get("/api/ps")
async def api_ps(request: Request):
    client: httpx.AsyncClient = request.app.state.client
    response = await _proxy_get(client, "/v1/models")
    return translate.openai_models_to_ollama_ps(response.json())


@app.post("/api/show")
async def api_show(request: Request):
    body = await request.json()
    name: str = body.get("model") or body.get("name", "")
    context = get_model_context(name)
    clean = name.split(":")[0]
    cfg = _get_model_cfg(name)
    caps_override: list | None = cfg.get("capabilities") or None
    client: httpx.AsyncClient = request.app.state.client
    try:
        response = await _proxy_get(client, "/v1/models")
        result = translate.openai_models_to_ollama_show(response.json(), name, context, caps_override)
    except Exception:
        result = None
    if result is None:
        local = find_local_model(name)
        if local:
            result = translate.local_model_to_ollama_show(local, name, context, caps_override)
    if result is None:
        return JSONResponse({"error": "model not found"}, status_code=404)
    return result


@app.get("/api/version")
async def api_version():
    return {"version": "0.24.0"}


@app.post("/api/pull")
async def api_pull():
    async def _pull_stream() -> AsyncGenerator[bytes, None]:
        yield json.dumps({"status": "pulling manifest"}).encode() + b"\n"
        yield json.dumps({"status": "success"}).encode() + b"\n"

    return StreamingResponse(_pull_stream(), media_type="application/x-ndjson")


@app.delete("/api/delete")
async def api_delete():
    return Response(status_code=200)


@app.post("/api/copy")
async def api_copy():
    return JSONResponse({"error": "not implemented"}, status_code=501)


@app.post("/api/push")
async def api_push():
    return JSONResponse({"error": "not implemented"}, status_code=501)


@app.post("/api/create")
async def api_create():
    return JSONResponse({"error": "not implemented"}, status_code=501)


@app.post("/api/embed")
async def api_embed(request: Request):
    body = await request.json()
    model: str = body.get("model", "")
    inputs = body.get("input") or body.get("prompt", "")
    if isinstance(inputs, str):
        inputs = [inputs]
    zero_vec = [0.0] * 1536
    return {
        "model": model,
        "embeddings": [zero_vec for _ in inputs],
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": 0,
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible pass-through endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/embeddings")
async def v1_embeddings(request: Request):
    body = await request.json()
    model: str = body.get("model", "")
    inputs = body.get("input", "")
    if isinstance(inputs, str):
        inputs = [inputs]
    zero_vec = [0.0] * 1536
    data = [
        {"object": "embedding", "embedding": zero_vec, "index": i}
        for i, _ in enumerate(inputs)
    ]
    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/chat/completions")
async def v1_chat_completions(request: Request):
    raw_body = await request.body()
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        parsed = {}
    stream: bool = parsed.get("stream", False)
    client: httpx.AsyncClient = request.app.state.client
    if AUTO_SWITCH_MODEL:
        await ensure_model_loaded(client, parsed.get("model", ""))

    if stream:
        async def _stream() -> AsyncGenerator[bytes, None]:
            try:
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    content=raw_body,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    async for chunk in sse_passthrough(response):
                        yield chunk
            except httpx.HTTPStatusError as exc:
                yield f"data: {json.dumps({'error': f'upstream error {exc.response.status_code}'})}\n\n".encode()
            except httpx.RequestError as exc:
                yield f"data: {json.dumps({'error': f'upstream connection error: {str(exc)}'})}\n\n".encode()

        return StreamingResponse(_stream(), media_type="text/event-stream")

    response = await _proxy_post(
        client,
        "/v1/chat/completions",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type="application/json",
    )


@app.get("/v1/models")
async def v1_models(request: Request):
    client: httpx.AsyncClient = request.app.state.client
    response = await _proxy_get(client, "/v1/models")
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type="application/json",
    )


@app.post("/v1/completions")
async def v1_completions(request: Request):
    body = await request.json()
    stream: bool = body.get("stream", False)
    oai_chat_request = translate.v1_completions_to_openai_chat(body)
    oai_chat_request["stream"] = stream
    client: httpx.AsyncClient = request.app.state.client

    if stream:
        async def _stream() -> AsyncGenerator[bytes, None]:
            try:
                async with client.stream(
                    "POST", "/v1/chat/completions", json=oai_chat_request
                ) as response:
                    async for chunk in chat_sse_to_completions_sse(response):
                        yield chunk
            except httpx.HTTPStatusError as exc:
                yield f"data: {json.dumps({'error': f'upstream error {exc.response.status_code}'})}\n\n".encode()
            except httpx.RequestError as exc:
                yield f"data: {json.dumps({'error': f'upstream connection error: {str(exc)}'})}\n\n".encode()

        return StreamingResponse(_stream(), media_type="text/event-stream")

    response = await _proxy_post(client, "/v1/chat/completions", json=oai_chat_request)
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=PROXY_HOST, port=int(PROXY_PORT), reload=False)

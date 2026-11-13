"""Async client for LM Studio's local OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from urllib.parse import urlparse

import httpx

from aai.core.config import settings

logger = logging.getLogger(__name__)


class LMStudioError(RuntimeError):
    code = "lm_studio_request_error"


class LMStudioConnectionError(LMStudioError):
    code = "lm_studio_connection_error"


class LMStudioRequestError(LMStudioError):
    code = "lm_studio_request_error"


class LMStudioModelMissing(LMStudioError):
    code = "lm_studio_model_missing"


class LMStudioTimeout(LMStudioError):
    code = "lm_studio_timeout"


class LMStudioInvalidResponse(LMStudioError):
    code = "lm_studio_response_parse_error"


class LMStudioEmptyResponse(LMStudioError):
    code = "lm_studio_empty_response"


_REQUEST_LOCK = asyncio.Lock()


def _base_url() -> str:
    url = settings.lm_studio_base_url
    parsed = urlparse(url)
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise LMStudioConnectionError("LM Studio must use a loopback URL without credentials; external providers are not supported.")
    return url


def _public_endpoint() -> str:
    parsed = urlparse(settings.lm_studio_base_url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return "configured local LM Studio endpoint"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    return f"{parsed.scheme}://{host}{port}{parsed.path.rstrip('/')}"


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.lm_studio_timeout_seconds, connect=5.0)


async def _list_models(client: httpx.AsyncClient, *, job_id: str | None = None) -> list[str]:
    started = perf_counter()
    try:
        response = await client.get(f"{_base_url()}/models")
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException as error:
        logger.warning("LM_STUDIO_TIMEOUT stage=model_discovery job_id=%s duration_ms=%.1f exception_class=%s",
                       job_id or "none", (perf_counter() - started) * 1000, type(error).__name__)
        raise LMStudioTimeout("LM Studio did not respond before the configured timeout.") from error
    except ValueError as error:
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=model_discovery job_id=%s cause=%s",
                       job_id or "none", type(error).__name__)
        raise LMStudioInvalidResponse("LM Studio returned invalid JSON from its models endpoint.") from error
    except httpx.ConnectError as error:
        logger.warning("LM_STUDIO_CONNECTION_ERROR stage=model_discovery job_id=%s cause=%s",
                       job_id or "none", type(error).__name__)
        raise LMStudioConnectionError("Could not connect to the local LM Studio server.") from error
    except httpx.HTTPStatusError as error:
        logger.warning("LM_STUDIO_REQUEST_ERROR stage=model_discovery job_id=%s http_status=%s",
                       job_id or "none", error.response.status_code)
        raise LMStudioRequestError("LM Studio rejected the model discovery request.") from error
    except httpx.RequestError as error:
        logger.warning("LM_STUDIO_REQUEST_ERROR stage=model_discovery job_id=%s cause=%s",
                       job_id or "none", type(error).__name__)
        raise LMStudioRequestError("LM Studio model discovery request failed.") from error

    entries = body.get("data") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=model_discovery response_shape=invalid")
        raise LMStudioInvalidResponse("LM Studio returned a malformed models response; expected a data list.")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in entries):
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=model_discovery model_entry=invalid")
        raise LMStudioInvalidResponse("LM Studio returned a model entry without a valid ID.")
    return [item["id"] for item in entries]


async def _fetch_model_ids() -> list[str]:
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        return await _list_models(client)


async def _chat_completion(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    job_id: str | None = None,
) -> str:
    payload = {
        "model": settings.lm_studio_model,
        "messages": messages,
        "stream": False,
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    started = perf_counter()
    try:
        response = await client.post(f"{_base_url()}/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException as error:
        logger.warning("LM_STUDIO_TIMEOUT stage=chat_completion job_id=%s duration_ms=%.1f exception_class=%s",
                       job_id or "none", (perf_counter() - started) * 1000, type(error).__name__)
        raise LMStudioTimeout("LM Studio inference timed out. Retry or increase LM_STUDIO_TIMEOUT_SECONDS.") from error
    except ValueError as error:
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=chat_completion job_id=%s cause=%s",
                       job_id or "none", type(error).__name__)
        raise LMStudioInvalidResponse("LM Studio returned invalid JSON from its chat endpoint.") from error
    except httpx.ConnectError as error:
        logger.warning("LM_STUDIO_CONNECTION_ERROR stage=chat_completion job_id=%s cause=%s",
                       job_id or "none", type(error).__name__)
        raise LMStudioConnectionError("Could not connect to the local LM Studio chat endpoint.") from error
    except httpx.HTTPStatusError as error:
        logger.warning("LM_STUDIO_REQUEST_ERROR stage=chat_completion job_id=%s http_status=%s",
                       job_id or "none", error.response.status_code)
        raise LMStudioRequestError("LM Studio rejected the chat-completion request.") from error
    except httpx.RequestError as error:
        logger.warning("LM_STUDIO_REQUEST_ERROR stage=chat_completion job_id=%s cause=%s",
                       job_id or "none", type(error).__name__)
        raise LMStudioRequestError("LM Studio chat-completion request failed.") from error

    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=chat_completion job_id=%s response_shape=invalid", job_id or "none")
        raise LMStudioInvalidResponse("LM Studio returned a malformed chat-completion response.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=chat_completion job_id=%s message_shape=invalid", job_id or "none")
        raise LMStudioInvalidResponse("LM Studio returned a malformed assistant message.")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=chat_completion job_id=%s content_type=%s",
                       job_id or "none", type(content).__name__)
        raise LMStudioInvalidResponse("LM Studio returned non-text assistant content for a text-only request.")
    if content is None or not content.strip():
        has_reasoning = bool(message.get("reasoning_content"))
        logger.warning("LM_STUDIO_EMPTY_RESPONSE stage=chat_completion job_id=%s reasoning_only=%s",
                       job_id or "none", has_reasoning)
        raise LMStudioEmptyResponse("LM Studio returned no final text in choices[0].message.content.")
    if choices[0].get("finish_reason") == "length":
        logger.warning("LM_STUDIO_RESPONSE_PARSE_ERROR stage=chat_completion job_id=%s finish_reason=length", job_id or "none")
        raise LMStudioInvalidResponse("LM Studio truncated the chat response at the token limit.")
    return content


async def _probe_inference() -> None:
    async with _REQUEST_LOCK:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            await _chat_completion(
                client,
                [{"role": "user", "content": "Reply with the single word READY."}],
                max_tokens=5,
            )


async def get_status() -> dict:
    model = settings.lm_studio_model
    try:
        model_ids = await _fetch_model_ids()
    except LMStudioError as error:
        return {
            "provider": "lm_studio", "endpoint": _public_endpoint(), "model": model, "connected": False,
            "model_available": False, "inference_ready": False, "status": "unavailable", "message": str(error),
        }
    if model not in model_ids:
        return {
            "provider": "lm_studio", "endpoint": _public_endpoint(), "model": model, "connected": True,
            "model_available": False, "inference_ready": False, "status": "model_missing",
            "message": f"LM Studio is reachable, but the exact model ID {model} was not returned by /v1/models.",
        }

    try:
        await _probe_inference()
    except LMStudioError as error:
        return {
            "provider": "lm_studio", "endpoint": _public_endpoint(), "model": model, "connected": True,
            "model_available": True, "inference_ready": False, "status": "inference_unready",
            "message": f"The model is listed by LM Studio, but an inference probe failed: {error}",
        }
    return {
        "provider": "lm_studio", "endpoint": _public_endpoint(), "model": model, "connected": True,
        "model_available": True, "inference_ready": True, "status": "ready",
        "message": "LM Studio returned the configured model and completed a chat inference probe.",
    }


async def chat_json(system_prompt: str, user_prompt: str, *, max_tokens: int, job_id: str | None = None) -> str:
    timeout = _timeout()
    async with _REQUEST_LOCK:
        async with httpx.AsyncClient(timeout=timeout) as client:
            model_ids = await _list_models(client, job_id=job_id)
            if settings.lm_studio_model not in model_ids:
                raise LMStudioModelMissing(
                    f"The exact configured model ID {settings.lm_studio_model} is not available from LM Studio /v1/models."
                )
            return await _chat_completion(
                client,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                job_id=job_id,
            )

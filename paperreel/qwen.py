"""The local model, through Ollama's HTTP API.

Everything this studio says in words comes from one model running on this machine: the
script, the board edits a conversation asks for, the caption, and -- because the model has
vision -- the judgement of whether a still that came back actually belongs in the same reel
as the others.

This replaced the Antigravity CLI (`agy`), and the reason is the one number that used to
shape the whole design: agy's image tool allowed roughly five generations per five-hour
window, and its agent turns billed against the same plan quota. Nothing here is metered, so
the pipeline is free to spend turns on things that were previously too expensive to
attempt -- reviewing its own script before handing it over, and looking at every still it
asked for.

Three shapes of call, all on `/api/chat`:

    text(messages)                   -> str          the caption, and anything conversational
    structured(messages, schema)     -> dict         the script; Ollama constrains the decode
    chat(messages, tools=TOOLS)      -> message      one turn of a tool loop

Vision is the same call with `images` on a message; `encode` turns a path into what that
field wants. No new dependency: httpx is already in every entry point's inline deps.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Iterable

import httpx

from . import config


class OllamaError(RuntimeError):
    """The model was reached but could not answer. The message is user-facing."""


class OllamaUnavailable(OllamaError):
    """Nothing is listening, or the model is not pulled. Both are the user's to fix."""


# Connect fast so a missing Ollama is reported rather than waited on, but read slowly: a
# 36B model answering a long board digest on a laptop is minutes, not seconds, and a
# timeout here throws away work that was nearly done.
def _timeout(read: float | None = None) -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=read or config.QWEN_TIMEOUT,
                         # Images go up as base64 in the request body, so writes are not
                         # instant either -- two 768x1344 PNGs is a few megabytes of JSON.
                         write=60.0, pool=5.0)


def _post(path: str, body: dict, *, read: float | None = None) -> dict:
    try:
        response = httpx.post(f"{config.OLLAMA_URL}{path}", json=body, timeout=_timeout(read))
    except httpx.ConnectError as gone:
        raise OllamaUnavailable(
            f"no Ollama at {config.OLLAMA_URL}. Start it (`ollama serve`, or open the app) "
            f"and make sure `{config.QWEN_MODEL}` is pulled -- `make qwen` does that."
        ) from gone
    except httpx.TimeoutException as slow:
        raise OllamaError(
            f"{config.QWEN_MODEL} did not answer within {config.QWEN_TIMEOUT:.0f}s. Raise "
            "PAPERREEL_QWEN_TIMEOUT, or use a smaller model."
        ) from slow
    if response.status_code >= 400:
        raise OllamaError(f"Ollama returned {response.status_code}: {response.text[-600:]}")
    payload = response.json()
    # Ollama sometimes reports a model-level failure inside a 200, so both have to be checked.
    if isinstance(payload, dict) and payload.get("error"):
        raise OllamaError(str(payload["error"]))
    return payload


def health() -> dict | None:
    """What Ollama says about itself, or None when it cannot be used.

    Deliberately swallows every transport error, the same way `papercut.health` does: a
    studio session that is only editing text never needs the model, so "not running" is an
    ordinary state to report in the UI rather than a fault to raise on.
    """
    try:
        response = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=config.QWEN_PROBE_TIMEOUT)
        response.raise_for_status()
        names = [str(m.get("name", "")) for m in response.json().get("models", [])]
    except Exception:  # noqa: BLE001 -- unreachable, wrong service, malformed reply
        return None
    return {"url": config.OLLAMA_URL, "model": config.QWEN_MODEL,
            "ready": _has_model(names, config.QWEN_MODEL), "models": names}


def available() -> bool:
    reported = health()
    return bool(reported and reported["ready"])


def _has_model(names: Iterable[str], wanted: str) -> bool:
    """Is `wanted` pulled? `qwen3.6` and `qwen3.6:latest` are the same model to Ollama."""
    tags = set(names)
    return wanted in tags or f"{wanted}:latest" in tags


def encode(path: Path) -> str:
    """One image, as the `images` field wants it: raw base64, no data: prefix."""
    return base64.b64encode(path.read_bytes()).decode()


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         schema: dict | None = None, think: bool = False,
         temperature: float | None = None, model: str | None = None) -> dict:
    """One turn. Returns the assistant message, including `tool_calls` when there are any.

    `think` is passed explicitly rather than left to the model's default, because the
    default for a reasoning model is on and the reasoning is not free: the same board edit
    that takes 0.9s with thinking off takes 14s with it on, for a tool call that was
    already unambiguous. Planning is the exception and asks for it (see config.PLAN_THINK).
    """
    body: dict[str, Any] = {
        "model": model or config.QWEN_MODEL,
        "messages": messages,
        "stream": False,
        "think": think,
        # The weights are ~23 GiB and cost about four seconds to load. A studio session is
        # dozens of short turns, so holding the model resident between them is the
        # difference between an edit landing instantly and every one of them stalling.
        "keep_alive": config.QWEN_KEEP_ALIVE,
        "options": {
            # Ollama defaults the window to a few thousand tokens, which silently truncates
            # an eight-beat board digest plus its transcript -- and a truncated prompt does
            # not fail, it just answers about a board that is missing its last scenes.
            "num_ctx": config.QWEN_NUM_CTX,
            "temperature": config.QWEN_TEMPERATURE if temperature is None else temperature,
        },
    }
    if tools:
        body["tools"] = tools
    if schema is not None:
        body["format"] = schema
    try:
        payload = _post("/api/chat", body)
    except OllamaError as refused:
        # A model without the thinking capability rejects the key outright. Retrying without
        # it keeps PAPERREEL_QWEN_MODEL pointable at any local model rather than only at a
        # reasoning one.
        if "think" not in str(refused).lower():
            raise
        body.pop("think")
        payload = _post("/api/chat", body)
    return payload.get("message") or {}


def text(messages: list[dict], *, think: bool = False,
         temperature: float | None = None, model: str | None = None) -> str:
    """A plain answer, for the places where prose is the product."""
    message = chat(messages, think=think, temperature=temperature, model=model)
    body = str(message.get("content") or "").strip()
    if not body:
        raise OllamaError(f"{config.QWEN_MODEL} answered with nothing at all.")
    return body


def structured(messages: list[dict], schema: dict, *, think: bool = False,
               temperature: float | None = None, model: str | None = None) -> dict:
    """A JSON object matching `schema`.

    Ollama constrains the decode to the schema, so this is not prompt-and-hope -- but the
    model can still return an empty body when its whole answer went into a thinking block,
    and it still occasionally wraps the object in a markdown fence. Both are recoverable and
    neither is worth failing a turn over.
    """
    message = chat(messages, schema=schema, think=think, temperature=temperature, model=model)
    body = str(message.get("content") or "").strip()
    if not body:
        thought = str(message.get("thinking") or "").strip()
        raise OllamaError(
            f"{config.QWEN_MODEL} returned no JSON"
            + (f", only reasoning: {thought[-400:]}" if thought else ".")
        )
    try:
        parsed = json.loads(_unfenced(body))
    except json.JSONDecodeError as bad:
        raise OllamaError(f"{config.QWEN_MODEL} did not return JSON ({bad.msg}): {body[:400]}")
    if not isinstance(parsed, dict):
        raise OllamaError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _unfenced(body: str) -> str:
    """The JSON object inside whatever the model wrapped it in."""
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        body = body.rsplit("```", 1)[0]
    start, end = body.find("{"), body.rfind("}")
    return body[start:end + 1] if 0 <= start < end else body


def tool(name: str, description: str, properties: dict,
         required: list[str] | None = None) -> dict:
    """One entry for the `tools` array.

    Descriptions are not decoration here. Given a bare "Edit one beat" the model spent its
    whole turn reasoning about what the `action` parameter wanted -- reading the field name
    as a verb, because that is what "action" means in every other tool it has ever seen.
    Told what the field IS, it answers in one call. So every parameter says what it holds,
    in the vocabulary the rest of the prompt uses.
    """
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


def calls_of(message: dict) -> list[tuple[str, dict]]:
    """The (name, arguments) pairs in a tool-calling reply, in the order they were asked for.

    Arguments arrive as an object, but a model that has decided to emit a string instead is
    not worth losing the turn over -- the caller validates every field anyway.
    """
    found: list[tuple[str, dict]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        if name:
            found.append((name, arguments if isinstance(arguments, dict) else {}))
    return found


def answered(message: dict, tool_results: list[tuple[str, str]]) -> list[dict]:
    """The two messages a completed tool round adds to the transcript.

    The assistant turn goes back verbatim -- Ollama matches the results to the calls it
    holds -- followed by one tool message per call, named, so a round with several calls
    cannot be read out of order.
    """
    return [message] + [
        {"role": "tool", "tool_name": name, "content": content}
        for name, content in tool_results
    ]

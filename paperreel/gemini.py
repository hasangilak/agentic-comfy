"""The language model, through Google's Gemini API.

Everything this studio says in words comes from one model: the script, the board edits a
conversation asks for, the storyboard panels, the caption, and -- because the model has
vision -- the judgement of whether a still that came back actually belongs in the same reel
as the others.

This replaced `qwen3.6` on Ollama, which replaced the Antigravity CLI before it. The local
model was free and slow; this one is metered and fast, and both facts matter. Free is what
made the two self-review passes affordable in the first place (see `config.PLAN_REVIEW` /
`config.STILL_REVIEW`) -- they survive the move because Gemini's flash tier prices a review
turn at a fraction of one Gemini *image*, which the same pipeline already spends without
hesitating. What does NOT survive is the old assumption that a turn costs nothing at all: a
call added here has a price, and the test to apply is whether it is worth one.

Three shapes of call, all on `models/<model>:generateContent`:

    text(messages)                   -> str          the caption, and anything conversational
    structured(messages, schema)     -> dict         the script; the decode is schema-constrained
    chat(messages, tools=TOOLS)      -> message      one turn of a tool loop

Callers speak the Ollama message vocabulary -- `{"role": "system"|"user"|"assistant"|"tool",
"content": str, "images": [base64]}` -- and this module translates it into Gemini `contents`.
That is deliberate: the vocabulary is what every prompt in `agent.py`, `planner.py`,
`stills.py`, `pictures.py`, `staging.py` and `panels.py` is written in, and the transport is
the one place a change of provider should be visible.

No SDK: httpx is already in every entry point's inline deps, and the whole surface used here
is one POST.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx

from . import config, llm


class GeminiError(llm.LLMError):
    """The model was reached but could not answer. The message is user-facing.

    The base class is `llm.LLMError` rather than `RuntimeError` so a caller written against the
    protocol rather than against this module catches the same failures. `LLMError` is itself a
    `RuntimeError`, so every `except gemini.GeminiError` written before `llm.py` existed still
    catches exactly what it always did.
    """


class GeminiUnavailable(GeminiError, llm.LLMUnavailable):
    """No API key, or nothing answering. Both are the user's to fix."""


# Connect fast so an unreachable API is reported rather than waited on, but read slowly: a
# script review with the whole authoring brief in it is a minute of generation, and a timeout
# here throws away work that was nearly done.
def _timeout(read: float | None = None) -> httpx.Timeout:
    return httpx.Timeout(connect=10.0, read=read or config.LLM_TIMEOUT,
                         # Images go up as base64 in the request body, so writes are not
                         # instant either -- two 768x1344 PNGs is a few megabytes of JSON.
                         write=120.0, pool=5.0)


def _key() -> str:
    if not config.GOOGLE_API_KEY:
        raise GeminiUnavailable(
            "no Google API key. Put X-GOOG-API-KEY=... in .env (the same key the image "
            "server uses), or export GEMINI_API_KEY."
        )
    return config.GOOGLE_API_KEY


def _post(model: str, body: dict, *, read: float | None = None) -> dict:
    url = f"{config.GEMINI_API_URL}/models/{model}:generateContent"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.post(url, json=body, timeout=_timeout(read),
                                  headers={"x-goog-api-key": _key(),
                                           "content-type": "application/json"})
        except httpx.ConnectError as gone:
            raise GeminiUnavailable(
                f"could not reach {config.GEMINI_API_URL} -- check the network."
            ) from gone
        except httpx.TimeoutException as slow:
            raise GeminiError(
                f"{model} did not answer within {config.LLM_TIMEOUT:.0f}s. Raise "
                "PAPERREEL_LLM_TIMEOUT, or use a faster model."
            ) from slow
        if response.status_code == 429 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        if response.status_code >= 400:
            last_error = GeminiError(_fault(model, response))
            if response.status_code == 429 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise last_error
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            raise GeminiError(str(payload["error"].get("message") or payload["error"]))
        return payload
    if last_error is not None:
        raise last_error
    raise GeminiError(f"{model} did not answer after retries")


def _fault(model: str, response: httpx.Response) -> str:
    """What went wrong, in the words the API used rather than a status code.

    429 is worth naming: it is the one failure the user can act on immediately, and it is
    also the one that will arrive in bursts, because a stills pass is a dozen turns in a row.
    """
    detail = response.text[-600:]
    try:
        detail = str(response.json()["error"]["message"])
    except Exception:  # noqa: BLE001 -- a non-JSON error body is still worth showing
        pass
    if response.status_code == 429:
        return f"Gemini is rate-limiting {model} (429): {detail}"
    if response.status_code in (401, 403):
        return f"Gemini refused the API key ({response.status_code}): {detail}"
    return f"Gemini returned {response.status_code} for {model}: {detail}"


# The probe is cached because /api/status is polled: the studio asks after every settled job,
# and a request per poll is a round trip nobody reads. A failure is cached too -- otherwise a
# machine that is simply offline pays the timeout on every poll, which stalls the whole status
# route. Sixty seconds is short enough that a key fixed in .env shows up while the user is
# still looking at the sidebar.
_PROBED: tuple[float, dict | None] = (0.0, None)


def health() -> dict | None:
    """What the API says about the configured model, or None when it cannot be used.

    Deliberately swallows every transport error, the same way `papercut.health` does: a
    studio session that is only editing text never needs the model, so "not configured" is
    an ordinary state to report in the UI rather than a fault to raise on.
    """
    global _PROBED
    checked, cached = _PROBED
    if checked and time.monotonic() - checked < config.LLM_PROBE_CACHE:
        return cached
    if not config.GOOGLE_API_KEY:
        # Not cached: this one is answered from a variable, so it costs nothing to re-read.
        return None
    reported: dict | None = None
    try:
        response = httpx.get(
            f"{config.GEMINI_API_URL}/models/{config.TEXT_MODEL}",
            headers={"x-goog-api-key": config.GOOGLE_API_KEY},
            timeout=config.LLM_PROBE_TIMEOUT,
        )
        response.raise_for_status()
        named = str(response.json().get("name") or "").split("/")[-1]
        reported = {"url": config.GEMINI_API_URL, "model": config.TEXT_MODEL,
                    "ready": True, "models": [named or config.TEXT_MODEL]}
    except Exception:  # noqa: BLE001 -- offline, bad key, wrong model name
        reported = None
    _PROBED = (time.monotonic(), reported)
    return reported


def available() -> bool:
    reported = health()
    return bool(reported and reported["ready"])


def encode(path: Path) -> str:
    """One image, as `inlineData` wants it: raw base64, no data: prefix.

    Downscaled on the way out to a 1024 px long edge. A still is a 768x1344 PNG of about
    1.5 MB and base64 makes it 2 MB of request body, of which nothing is lost by shrinking
    it: Gemini resamples an inline image onto its own tile grid regardless, so full-size
    bytes were never reaching the model as detail.

    What this did NOT do is make the call faster, and the measurement is worth recording
    because it looks like it should have. A two-still review took 82.8s at full size and
    82.0s at 1024 px, and single-image calls took the same 81s -- the round trip was flat in
    payload size, so whatever costs that minute is not the upload. Keep the downscale for the
    request size; do not quote it as a latency fix.

    Pillow is in every entry point's inline deps, but the fallback is the original bytes
    rather than an exception: a picture that cannot be re-encoded is still a picture the
    model can look at, and refusing to review it would be the worse answer.
    """
    raw = path.read_bytes()
    if not config.LLM_IMAGE_EDGE:
        return base64.b64encode(raw).decode()
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(raw)) as picture:
            if max(picture.size) <= config.LLM_IMAGE_EDGE:
                return base64.b64encode(raw).decode()
            small = picture.convert("RGB")
            small.thumbnail((config.LLM_IMAGE_EDGE, config.LLM_IMAGE_EDGE))
            buffer = BytesIO()
            small.save(buffer, format="JPEG", quality=88)
        return base64.b64encode(buffer.getvalue()).decode()
    except Exception:  # noqa: BLE001 -- no Pillow, or a format it cannot open
        return base64.b64encode(raw).decode()


def _mime(encoded: str) -> str:
    """The type of an already-encoded image, from its first bytes.

    Sniffed rather than taken from the filename because `encode` only ever gets a path and
    the callers hand it both: a still is a JPEG the image server wrote, a reference picture
    is a PNG, and Gemini rejects an inline part whose declared type is wrong.
    """
    head = encoded[:16]
    if head.startswith("/9j/"):
        return "image/jpeg"
    if head.startswith("R0lGOD"):
        return "image/gif"
    if head.startswith("UklGR"):
        return "image/webp"
    return "image/png"


def _image_parts(message: dict) -> list[dict]:
    return [{"inlineData": {"mimeType": _mime(image), "data": image}}
            for image in message.get("images") or []]


def _response_parts(message: dict, results: list[str]) -> list[dict]:
    """One `functionResponse` per call the assistant made, matched by position.

    Gemini pairs a response to its call by `id` when there is one, so the ids the model
    minted are carried straight back through. Position is the fallback, and it is safe here
    because `answered()` builds the result list from the same call list in the same order.
    """
    parts = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        response: dict[str, Any] = {
            "name": str(function.get("name") or ""),
            "response": {"result": results[index] if index < len(results) else ""},
        }
        if call.get("id"):
            response["id"] = call["id"]
        parts.append({"functionResponse": response})
    return parts


def _contents(messages: list[dict]) -> tuple[list[dict], str]:
    """The Ollama-shaped transcript as Gemini `contents`, plus the system instruction.

    Two conversions are load-bearing:

    - An assistant turn goes back as the *parts the model returned*, kept on the message as
      `_parts`. Gemini 3 signs its reasoning (`thoughtSignature`) and validates that
      signature on the next turn of a tool loop, so a reconstructed text-only assistant turn
      breaks the round trip that `agent.turn` depends on.
    - Consecutive tool results are merged into one user turn. They are the answers to one
      round of calls, and sending them as separate turns reads to the model as several
      exchanges that never happened.
    """
    system: list[str] = []
    contents: list[dict] = []
    for message in messages:
        role = message.get("role")
        body = str(message.get("content") or "")
        if role == "system":
            if body:
                system.append(body)
            continue
        if role == "assistant":
            parts = message.get("_parts") or ([{"text": body}] if body else [])
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue
        if role == "tool":
            # Already assembled by `answered`; the merge happens there so the ids stay with
            # the assistant turn that minted them.
            parts = message.get("_parts") or []
            if parts:
                contents.append({"role": "user", "parts": parts})
            continue
        parts = ([{"text": body}] if body else []) + _image_parts(message)
        if parts:
            contents.append({"role": "user", "parts": parts})
    return contents, "\n\n".join(system)


def _thinking(think: bool) -> dict:
    """Reasoning effort, passed explicitly on every call rather than left to the default.

    The measurement that set this on the local model still holds in shape if not in seconds:
    reasoning is not free, and an unambiguous board edit does not need any. Here it is also
    money -- thought tokens are billed as output -- so everything is `minimal` except the
    planning pair, which asks for it (see `config.PLAN_THINK`).
    """
    return {"thinkingLevel": "high" if think else "minimal"}


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         schema: dict | None = None, think: bool = False,
         temperature: float | None = None, model: str | None = None) -> dict:
    """One turn. Returns the assistant message, including `tool_calls` when there are any.

    The return is Ollama-shaped -- `{"role", "content", "tool_calls"}` -- because every
    caller was written against that shape, with the raw Gemini parts kept alongside under
    `_parts` for the tool loop to hand back verbatim.
    """
    contents, system = _contents(messages)
    generation: dict[str, Any] = {
        "temperature": config.LLM_TEMPERATURE if temperature is None else temperature,
        "thinkingConfig": _thinking(think),
    }
    if schema is not None:
        generation["responseMimeType"] = "application/json"
        # `responseJsonSchema` takes the schema as written; the older `responseSchema` is a
        # cut-down dialect that drops the keywords these schemas use.
        generation["responseJsonSchema"] = schema
    body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [{"functionDeclarations": tools}]
    payload = _post(model or config.TEXT_MODEL, body)
    return _message(payload, model or config.TEXT_MODEL)


def _message(payload: dict, model: str) -> dict:
    """The first candidate, in the shape the callers read.

    A candidate with no parts at all is not an empty answer -- it is a refusal, a safety
    stop, or a generation that hit the output cap mid-object. Each of those is worth saying
    out loud, because the alternative is a caller reporting "the model said nothing".
    """
    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = ((payload.get("promptFeedback") or {}).get("blockReason") or "").strip()
        raise GeminiError(f"{model} returned no answer"
                          + (f" ({blocked})" if blocked else "."))
    candidate = candidates[0]
    parts = ((candidate.get("content") or {}).get("parts")) or []
    spoken = "".join(str(part.get("text") or "") for part in parts
                     if not part.get("thought"))
    calls = []
    for part in parts:
        call = part.get("functionCall")
        if not call:
            continue
        calls.append({"id": call.get("id"),
                      "function": {"name": call.get("name") or "",
                                   "arguments": call.get("args") or {}}})
    reason = str(candidate.get("finishReason") or "")
    if not spoken.strip() and not calls and reason not in ("STOP", ""):
        raise GeminiError(f"{model} stopped without answering ({reason}).")
    return {"role": "assistant", "content": spoken, "tool_calls": calls, "_parts": parts}


def text(messages: list[dict], *, think: bool = False,
         temperature: float | None = None, model: str | None = None) -> str:
    """A plain answer, for the places where prose is the product."""
    message = chat(messages, think=think, temperature=temperature, model=model)
    body = str(message.get("content") or "").strip()
    if not body:
        raise GeminiError(f"{model or config.TEXT_MODEL} answered with nothing at all.")
    return body


def structured(messages: list[dict], schema: dict, *, think: bool = False,
               temperature: float | None = None, model: str | None = None) -> dict:
    """A JSON object matching `schema`.

    The decode is constrained to the schema, so this is not prompt-and-hope -- but a model
    can still stop on the output cap partway through an object, and it still occasionally
    wraps the object in a markdown fence. The fence is recoverable and not worth failing a
    turn over; a truncated object is not, and says so.
    """
    message = chat(messages, schema=schema, think=think,
                   temperature=temperature, model=model)
    named = model or config.TEXT_MODEL
    body = str(message.get("content") or "").strip()
    if not body:
        raise GeminiError(f"{named} returned no JSON.")
    try:
        parsed = json.loads(_unfenced(body))
    except json.JSONDecodeError as bad:
        raise GeminiError(f"{named} did not return JSON ({bad.msg}): {body[:400]}")
    if not isinstance(parsed, dict):
        raise GeminiError(f"expected a JSON object, got {type(parsed).__name__}")
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
    """One entry for the `functionDeclarations` array.

    Descriptions are not decoration here. Given a bare "Edit one beat" a model spends its
    whole turn reasoning about what the `action` parameter wanted -- reading the field name
    as a verb, because that is what "action" means in every other tool it has ever seen.
    Told what the field IS, it answers in one call. So every parameter says what it holds,
    in the vocabulary the rest of the prompt uses.
    """
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {key: _declarable(field) for key, field in properties.items()},
            "required": required or [],
        },
    }


def _declarable(field: dict) -> dict:
    """One parameter, in the dialect a function declaration is allowed to use.

    Gemini's declaration schema takes `enum` on strings only, and answers a numeric one with
    a 400 naming the index -- which is how `{"type": "number", "enum": [5, 10]}` on the beat
    length took out the whole tool loop. The values are not dropped, because they are the
    constraint the caller is relying on: they move into the description, which is where a
    model reads them anyway (see the note on descriptions above).
    """
    if not isinstance(field, dict) or "enum" not in field:
        return field
    if field.get("type") == "string":
        return field
    allowed = ", ".join(str(value) for value in field["enum"])
    spoken = str(field.get("description") or "").rstrip()
    kept = {key: value for key, value in field.items() if key != "enum"}
    kept["description"] = (f"{spoken} " if spoken else "") + f"One of: {allowed}."
    return kept


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

    The assistant turn goes back verbatim -- signed reasoning and all, which Gemini checks --
    followed by ONE message carrying every result, because a round of parallel calls is
    answered in a single turn. The results are matched to the calls by position and then by
    the id the model minted, so a round with several calls cannot be read out of order.
    """
    return [message, {"role": "tool",
                      "_parts": _response_parts(message, [body for _, body in tool_results])}]

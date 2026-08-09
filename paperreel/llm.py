"""What this studio needs from a language model, as a protocol rather than a module name.

Everything that is words in this pipeline goes through one transport, and until now that
transport was named at every call site: `gemini.structured(...)`, `gemini.tool(...)`. That was
right while there was one of them and `gemini.py`'s own docstring says so -- "the transport is
the one place a change of provider should be visible". This module is that sentence made
checkable: the surface is written down, `gemini` is registered against it, and a second
provider is a file rather than a search-and-replace across six modules.

The protocol is deliberately `gemini.py`'s existing surface, not an idealised one. Two
consequences worth knowing before adding a provider:

- **`tool()` is on the protocol.** Building a function declaration looks like a caller's job,
  but it is not: Gemini answers a numeric `enum` with a 400 and `gemini._declarable` folds the
  values into the description instead. That is a per-provider dialect fix, so whoever knows the
  dialect owns the builder.
- **`chat()` returns the assistant message with the provider's own parts attached.** Gemini 3
  signs its reasoning and validates that signature on the next turn, so `answered()` hands the
  message back verbatim. A provider that reconstructs the turn from text breaks any tool loop
  built on it; a provider with nothing to sign can carry an empty `_parts` and lose nothing.

No provider is required to be a class. A module satisfies a protocol of plain functions, which
is why `gemini.py` needed no adapter and no rename to be the first implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from . import config


class LLMError(RuntimeError):
    """The model was reached but could not answer. The message is user-facing.

    `gemini.GeminiError` is this, so the nine `except gemini.GeminiError` sites written before
    this module existed keep catching exactly what they always did.
    """


class LLMUnavailable(LLMError):
    """No credential, or nothing answering. Both are the user's to fix."""


@runtime_checkable
class LLM(Protocol):
    """The transport, in the vocabulary every prompt in this package is already written in.

    Callers speak `{"role": "system"|"user"|"assistant"|"tool", "content": str,
    "images": [base64]}` -- the Ollama shape -- and a provider translates. That vocabulary is
    load-bearing rather than historical: every prompt in `agent.py`, `planner.py`, `stills.py`,
    `pictures.py`, `staging.py` and `panels.py` is written in it, and rewriting them to a new
    provider's shape is exactly the change this protocol exists to avoid.
    """

    def chat(self, messages: list[dict], *, tools: list[dict] | None = ...,
             schema: dict | None = ..., think: bool = ...,
             temperature: float | None = ..., model: str | None = ...) -> dict:
        """One turn. Returns the assistant message, `tool_calls` included when there are any."""

    def text(self, messages: list[dict], *, think: bool = ...,
             temperature: float | None = ..., model: str | None = ...) -> str:
        """A plain answer, for the places where prose is the product."""

    def structured(self, messages: list[dict], schema: dict, *, think: bool = ...,
                   temperature: float | None = ..., model: str | None = ...) -> dict:
        """A JSON object matching `schema`, constrained at decode where the API allows it."""

    def tool(self, name: str, description: str, properties: dict,
             required: list[str] | None = ...) -> dict:
        """One function declaration, in whatever dialect this provider's declarations use."""

    def calls_of(self, message: dict) -> list[tuple[str, dict]]:
        """The (name, arguments) pairs in a tool-calling reply, in the order asked for."""

    def answered(self, message: dict, tool_results: list[tuple[str, str]]) -> list[dict]:
        """The messages one completed tool round adds to the transcript."""

    def encode(self, path: Path) -> str:
        """One image as the API wants it inline: raw base64, no data: prefix."""

    def health(self) -> dict | None:
        """What the API says about the configured model, or None when it cannot be used."""

    def available(self) -> bool:
        """Whether a call would have any chance of succeeding."""


# Registered lazily rather than at import, because a provider module imports `config` and
# `config` is imported by this one. The cycle is real: `gemini.py` does `from . import config`
# and `GeminiError` subclasses `LLMError` from here, so importing `gemini` at module level
# would make `llm` unimportable on its own.
_PROVIDERS = ("gemini",)


def provider(name: str | None = None) -> LLM:
    """The transport, by name. One is registered; this function is what the seam is for.

    An unknown name says what IS registered rather than raising a KeyError, because the value
    arrives from an environment variable and a typo in `.env` is the likeliest way to get here.
    """
    wanted = (name or config.LLM_PROVIDER).strip().lower()
    if wanted == "gemini":
        from . import gemini

        return gemini
    raise LLMUnavailable(
        f"no language provider called {wanted!r}. Registered: "
        f"{', '.join(_PROVIDERS)}. Set PAPERREEL_LLM_PROVIDER to one of those."
    )

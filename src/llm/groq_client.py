"""K14 — the Groq client.

One key, one loader, read from config and nowhere else (TC-4, NFR-2). The key is
never logged, never printed, and never included in an error message: the errors
here name the variable to set, not its value, so a stack trace in a demo is safe
to show.

Failures collapse to one typed :class:`LLMUnavailable` after the configured
retries, because architecture.md §10 wants a readable message rather than a
traceback at the pipeline boundary (NFR-4).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from src.config import Settings, load

#: What a client must implement. Deliberately structural so the pipeline and the
#: tests depend on the two-method surface, not on Groq.
LLMMessage = Dict[str, str]


class LLMUnavailable(RuntimeError):
    """The LLM could not be used: no key, a timeout, or retries exhausted."""


def _redact(text: str, key: Optional[str]) -> str:
    """Remove the API key from any text that might reach a log or the UI."""
    if not key or not text:
        return text
    return text.replace(key, "<redacted>")


class GroqClient:
    """Thin wrapper over the Groq chat-completions API (AD-1, no LangChain)."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or load()
        if not self.settings.groq_api_key:
            # Names the variable and the file. Never echoes the key, because here
            # there is no key; but the wording is deliberate for the case where a
            # key is present and something else fails.
            raise LLMUnavailable(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key "
                "there; it is read from config only and never logged."
            )
        if not self.settings.groq_model:
            raise LLMUnavailable(
                "GROQ_MODEL is not set. Add a Groq model id to .env, for example "
                "llama-3.3-70b-versatile."
            )
        try:
            from groq import Groq
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise LLMUnavailable(
                "the 'groq' package is not installed. Run: pip install groq"
            ) from exc
        self._client = Groq(
            api_key=self.settings.groq_api_key,
            timeout=float(self.settings.groq_timeout_s),
            max_retries=0,  # retries are ours, so the bound is explicit (NFR-4)
        )

    def chat(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant's text for ``messages``.

        Temperature is the configured value, which defaults to 0.0, the lowest
        the API supports (NFR-3). Retries follow ``GROQ_MAX_RETRIES``.
        """
        payload: List[LLMMessage] = [dict(m) for m in messages]
        attempts = int(self.settings.groq_max_retries) + 1
        last: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.settings.groq_model,
                    messages=payload,
                    temperature=self.settings.groq_temperature,
                    max_tokens=self.settings.groq_max_tokens,
                )
                return self._extract_text(response)
            except Exception as exc:  # noqa: BLE001 - the SDK raises a wide family
                last = exc
                if attempt < attempts:
                    # Linear backoff. Short, because the point is to ride out a
                    # rate limit in a demo, not to wait it out indefinitely.
                    time.sleep(min(2.0, 0.5 * attempt))

        detail = _redact(f"{type(last).__name__}: {last}", self.settings.groq_api_key)
        raise LLMUnavailable(
            f"Groq request failed after {attempts} attempt(s) using model "
            f"{self.settings.groq_model!r}: {detail}"
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMUnavailable("Groq returned a response with no choices")
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", None)
        if text is None or not str(text).strip():
            raise LLMUnavailable("Groq returned an empty message")
        return str(text).strip()


class ScriptedLLMClient:
    """Returns canned responses in order, for tests and offline runs.

    The architecture's testing strategy (§13) calls for the whole query pipeline
    to be testable with no API key and no network; this is how. It also lets a
    test reproduce a specific failure, such as a model that mangles the URL, and
    confirm the citation still comes from metadata (AD-3).
    """

    def __init__(
        self,
        responses: Optional[Sequence[Any]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.responses: List[Any] = list(responses or [])
        self.error = error
        self.calls: List[List[LLMMessage]] = []

    def chat(self, messages: Sequence[LLMMessage]) -> str:
        self.calls.append([dict(m) for m in messages])
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise LLMUnavailable("ScriptedLLMClient has no responses left to return")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return str(value)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_messages(self) -> List[LLMMessage]:
        return self.calls[-1] if self.calls else []

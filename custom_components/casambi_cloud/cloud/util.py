"""Pure helpers: recursive secret redaction and bounded backoff with jitter."""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any

REDACTED = "**REDACTED**"

# Keys whose values must never appear in logs or diagnostics, matched
# case-insensitively and on substrings so variants such as
# "sessionId", "X-Casambi-Session" or "user_password" are all caught.
SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "password",
    "session",
    "api_key",
    "apikey",
    "x-casambi-key",
    "token",
    "cookie",
    "authorization",
    "secret",
    "email",
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def redact(data: Any, *, extra_values: Iterable[str] = ()) -> Any:
    """Recursively redact sensitive keys and known secret values.

    ``extra_values`` allows callers to pass literal secrets (session ids,
    network ids, addresses) so any occurrence of those strings inside
    values is masked as well, regardless of the key it appears under.
    """
    secrets = [value for value in extra_values if value]

    def _scrub_text(text: str) -> str:
        for secret in secrets:
            if secret and secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: (REDACTED if is_sensitive_key(str(key)) else _walk(value))
                for key, value in node.items()
            }
        if isinstance(node, (list, tuple)):
            walked = [_walk(item) for item in node]
            return walked if isinstance(node, list) else tuple(walked)
        if isinstance(node, str):
            return _scrub_text(node)
        return node

    return _walk(data)


def redact_message(message: str, secrets: Iterable[str]) -> str:
    """Mask any literal secret occurrences inside a free-form message."""
    for secret in secrets:
        if secret:
            message = message.replace(secret, REDACTED)
    return message


class ExponentialBackoff:
    """Bounded exponential backoff with full jitter.

    ``next_delay`` returns ``uniform(initial, current)`` and doubles the
    ceiling up to ``maximum``. ``reset`` restores the initial state after a
    successful (re)connect so short blips recover quickly while sustained
    outages back off toward the ceiling instead of hammering the Cloud.
    """

    def __init__(
        self,
        *,
        initial: float = 5.0,
        maximum: float = 300.0,
        factor: float = 2.0,
        rng: random.Random | None = None,
    ) -> None:
        self._initial = max(0.5, float(initial))
        self._maximum = max(self._initial, float(maximum))
        self._factor = max(1.1, float(factor))
        self._current = self._initial
        self._rng = rng or random.Random()
        self.attempts = 0

    def reset(self) -> None:
        self._current = self._initial
        self.attempts = 0

    def next_delay(self) -> float:
        self.attempts += 1
        delay = self._rng.uniform(self._initial, self._current)
        self._current = min(self._maximum, self._current * self._factor)
        return delay

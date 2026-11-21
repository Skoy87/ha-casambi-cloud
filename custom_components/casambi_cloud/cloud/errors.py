"""Exceptions for the vendored Casambi Cloud API client."""

from __future__ import annotations


class CasambiCloudError(Exception):
    """Base error for all Casambi Cloud client failures."""


class CasambiAuthenticationError(CasambiCloudError):
    """Credentials or API key were rejected (HTTP 401/403 on login)."""


class CasambiSessionExpiredError(CasambiCloudError):
    """A previously valid session is no longer accepted (HTTP 410)."""


class CasambiRateLimitError(CasambiCloudError):
    """The Casambi Cloud reported quota limits exceeded (HTTP 429)."""


class CasambiCommunicationError(CasambiCloudError):
    """Transport level failure: DNS, TCP, TLS, timeouts, HTTP 5xx."""


class CasambiProtocolError(CasambiCloudError):
    """The Cloud answered with a payload the client cannot interpret."""


class CasambiNotConnectedError(CasambiCloudError):
    """A command was attempted while the required wire is not running."""


class CasambiCommandError(CasambiCloudError):
    """The Cloud rejected a control command (wireStatus error)."""

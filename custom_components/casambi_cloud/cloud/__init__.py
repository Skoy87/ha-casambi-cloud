"""Vendored asynchronous client for the Casambi Cloud API.

This package is self-contained (aiohttp only) so the integration has no
pip requirements to install on Home Assistant OS. See ``const.py`` for the
primary sources each protocol detail was verified against.
"""

from . import commands
from .client import CasambiCloudClient
from .errors import (
    CasambiAuthenticationError,
    CasambiCloudError,
    CasambiCommandError,
    CasambiCommunicationError,
    CasambiNotConnectedError,
    CasambiProtocolError,
    CasambiRateLimitError,
    CasambiSessionExpiredError,
)
from .models import CasambiNetwork, CasambiScene, CasambiUnit

__all__ = [
    "CasambiAuthenticationError",
    "CasambiCloudClient",
    "CasambiCloudError",
    "CasambiCommandError",
    "CasambiCommunicationError",
    "CasambiNetwork",
    "CasambiNotConnectedError",
    "CasambiProtocolError",
    "CasambiRateLimitError",
    "CasambiScene",
    "CasambiSessionExpiredError",
    "CasambiUnit",
    "commands",
]

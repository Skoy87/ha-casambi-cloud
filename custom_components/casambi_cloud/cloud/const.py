"""Constants for the vendored Casambi Cloud API client.

Protocol facts in this module were derived from:
- The official Casambi developer documentation (https://developer.casambi.com/),
  in particular the WebSocket "open", "controlUnit", "controlScene", "ping"
  message formats, the "wireStatus" response semantics and the 5 minute
  inactivity timeout.
- The aiocasambi 0.285 source shipped with this integration's handoff
  package, used by the previous working versions of this integration.
"""

from __future__ import annotations

from typing import Final

REST_BASE_URL: Final = "https://door.casambi.com/v1"
WEBSOCKET_URL: Final = "wss://door.casambi.com/v1/bridge/"

HEADER_API_KEY: Final = "X-Casambi-Key"
HEADER_SESSION: Final = "X-Casambi-Session"

# WebSocket message methods (client -> server).
WS_METHOD_OPEN: Final = "open"
WS_METHOD_CLOSE: Final = "close"
WS_METHOD_PING: Final = "ping"
WS_METHOD_CONTROL_UNIT: Final = "controlUnit"
WS_METHOD_CONTROL_SCENE: Final = "controlScene"

# WebSocket event methods (server -> client).
WS_EVENT_UNIT_CHANGED: Final = "unitChanged"
WS_EVENT_PEER_CHANGED: Final = "peerChanged"
WS_EVENT_NETWORK_UPDATED: Final = "networkUpdated"

# Client type used in the OPEN message; 1 = FRONTEND per the official docs.
WS_CLIENT_TYPE_FRONTEND: Final = 1

# The server closes a WebSocket after 5 minutes without traffic
# (developer.casambi.com, "Keeping connection alive"). Ping well before that.
WS_KEEPALIVE_INTERVAL: Final = 240.0

# wireStatus value confirming a successful OPEN.
WIRE_STATUS_OK: Final = "openWireSucceed"

# Substrings that mark a wireStatus as an authentication/session problem.
# The documented failure statuses relate to API key authentication,
# API key authorization and invalid sessions; matching on fragments keeps
# classification robust against exact-token drift.
_WIRE_STATUS_AUTH_MARKERS: Final = ("session", "authenticate", "authorize", "key")


def wire_status_is_auth_error(status: str) -> bool:
    """Return True if a non-OK wireStatus looks like an auth/session problem."""
    lowered = status.lower()
    return any(marker in lowered for marker in _WIRE_STATUS_AUTH_MARKERS)


# REST status codes, per the official documentation and aiocasambi consts.
STATUS_UNAUTHORIZED: Final = 401
STATUS_FORBIDDEN: Final = 403
STATUS_NOT_FOUND: Final = 404
STATUS_INVALID_SESSION: Final = 410
STATUS_RATE_LIMITED: Final = 429

REASONS_BY_STATUS_CODE: Final[dict[int, str]] = {
    200: "request OK",
    400: "bad request, given parameters invalid",
    401: "unauthorized, invalid API key or credentials",
    403: "API not enabled or session creation attempted too soon after failure",
    404: "requested data not found",
    405: "method not allowed",
    410: "invalid session",
    416: "retrieval interval is too long",
    429: "quota limits exceeded",
    500: "server error",
}

# Control types seen in unit control payloads (REST state and unitChanged).
CONTROL_DIMMER: Final = "Dimmer"
CONTROL_CCT: Final = "CCT"
CONTROL_COLOR: Final = "Color"
CONTROL_WHITE: Final = "White"
CONTROL_VERTICAL: Final = "Vertical"
CONTROL_OVERHEAT: Final = "Overheat"
CONTROL_RGB: Final = "RGB"
CONTROL_COLORSOURCE: Final = "Colorsource"
CONTROL_COLOR_TEMPERATURE: Final = "ColorTemperature"

# Unit "type" values that are treated as lights.
LIGHT_UNIT_TYPES: Final = ("luminaire",)

# Connection states for a wire.
WIRE_STATE_DISCONNECTED: Final = "disconnected"
WIRE_STATE_CONNECTING: Final = "connecting"
WIRE_STATE_RUNNING: Final = "running"
WIRE_STATE_STOPPED: Final = "stopped"

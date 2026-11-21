"""Constants for the Casambi Cloud integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "casambi_cloud"

# --- config entry data keys (identical to the legacy integration) -----------
# Preserving these lets existing entries keep working without re-entry of
# credentials; async_migrate_entry only relocates tunables into options.
CONF_USER_PASSWORD: Final = "user_password"
CONF_NETWORK_PASSWORD: Final = "network_password"
CONF_NETWORK_TIMEOUT: Final = "network_timeout"  # legacy tunable (data)

# --- option keys --------------------------------------------------------------
OPT_SCAN_INTERVAL: Final = "scan_interval"
OPT_STARTUP_TIMEOUT: Final = "startup_timeout"
OPT_COMMAND_TIMEOUT: Final = "command_timeout"
OPT_RECONNECT_INITIAL_DELAY: Final = "reconnect_initial_delay"
OPT_RECONNECT_MAX_DELAY: Final = "reconnect_max_delay"
OPT_FAILURE_THRESHOLD: Final = "failure_threshold"
OPT_ENABLE_NOTIFICATIONS: Final = "enable_persistent_notifications"
OPT_INCLUDE_HIDDEN_SCENES: Final = "include_hidden_scenes"
OPT_STRUCTURE_REFRESH_INTERVAL: Final = "structure_refresh_interval"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 3600

DEFAULT_STARTUP_TIMEOUT: Final = 90
MIN_STARTUP_TIMEOUT: Final = 30
MAX_STARTUP_TIMEOUT: Final = 300

DEFAULT_COMMAND_TIMEOUT: Final = 10
MIN_COMMAND_TIMEOUT: Final = 5
MAX_COMMAND_TIMEOUT: Final = 60

DEFAULT_RECONNECT_INITIAL_DELAY: Final = 5
MIN_RECONNECT_INITIAL_DELAY: Final = 1
MAX_RECONNECT_INITIAL_DELAY: Final = 60

DEFAULT_RECONNECT_MAX_DELAY: Final = 300
MIN_RECONNECT_MAX_DELAY: Final = 30
MAX_RECONNECT_MAX_DELAY: Final = 1800

DEFAULT_FAILURE_THRESHOLD: Final = 3
MIN_FAILURE_THRESHOLD: Final = 1
MAX_FAILURE_THRESHOLD: Final = 20

DEFAULT_ENABLE_NOTIFICATIONS: Final = True
DEFAULT_INCLUDE_HIDDEN_SCENES: Final = False

DEFAULT_STRUCTURE_REFRESH_INTERVAL: Final = 3600
MIN_STRUCTURE_REFRESH_INTERVAL: Final = 300
MAX_STRUCTURE_REFRESH_INTERVAL: Final = 86400

# --- alerting -------------------------------------------------------------------
NOTIFICATION_ID_PROBLEM: Final = "casambi_cloud_problem"
NOTIFICATION_ID_RECOVERED: Final = "casambi_cloud_recovered"
ISSUE_CONNECTION_LOST: Final = "connection_lost"
ISSUE_GATEWAY_OFFLINE: Final = "gateway_offline"

# How many consecutive poll cycles the gateway may be reported offline
# before the gateway Repairs issue is raised.
GATEWAY_OFFLINE_GRACE_CYCLES: Final = 2

# --- legacy service (kept for backwards compatible automations) ----------------
SERVICE_LIGHT_TURN_ON: Final = "light_turn_on"
ATTR_SERV_BRIGHTNESS: Final = "brightness"
ATTR_SERV_DISTRIBUTION: Final = "distribution"

# Error categories surfaced by the diagnostic "last error" sensor.
ERROR_CATEGORY_AUTH: Final = "authentication"
ERROR_CATEGORY_SESSION: Final = "session_expired"
ERROR_CATEGORY_RATE_LIMIT: Final = "rate_limited"
ERROR_CATEGORY_CONNECTION: Final = "connection"
ERROR_CATEGORY_TIMEOUT: Final = "timeout"
ERROR_CATEGORY_PROTOCOL: Final = "protocol"
ERROR_CATEGORY_COMMAND: Final = "command_rejected"
ERROR_CATEGORY_UNKNOWN: Final = "unknown"

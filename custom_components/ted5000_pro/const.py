"""Constants for the TED5000 Pro integration."""

DOMAIN = "ted5000_pro"
MANUFACTURER = "The Energy Detective"
MODEL = "TED 5000"

DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 10          # seconds; the gateway updates about every second
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 300

CONF_SCAN_INTERVAL_SECONDS = "scan_interval_seconds"
CONF_CREATE_CIRCUIT_ENERGY = "create_circuit_energy"

# Phantom (standby) load tracking
CONF_PHANTOM_DAYS = "phantom_days"
CONF_PHANTOM_START = "phantom_window_start"
CONF_PHANTOM_END = "phantom_window_end"
DEFAULT_PHANTOM_DAYS = 7
MIN_PHANTOM_DAYS = 1
MAX_PHANTOM_DAYS = 90
DEFAULT_PHANTOM_START = "01:00"
DEFAULT_PHANTOM_END = "05:00"

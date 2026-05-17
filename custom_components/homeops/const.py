"""Constants for the HomeOps integration."""

DOMAIN = "homeops"

# Config entry keys
CONF_SERVER_URL = "homeops_url"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"

# Defaults
DEFAULT_SCAN_INTERVAL = 5  # minutes
DEFAULT_SERVER_URL = "http://192.168.30.x:4070"

# Coordinator data keys
DATA_COUNTERS = "counters"
DATA_PICK_MORNING = "pick_morning"
DATA_PICK_EVENING = "pick_evening"
DATA_PICK_WEEKEND = "pick_weekend"

# HA device identifier
DEVICE_ID = "homeops_maintenance"

# Sensor / binary sensor keys (unique_id suffixes)
SENSOR_MAINT_PICK_MORNING = "maint_pick_morning"
SENSOR_MAINT_PICK_EVENING = "maint_pick_evening"
SENSOR_MAINT_PICK_WEEKEND = "maint_pick_weekend"
SENSOR_MAINT_OVERDUE_COUNT = "maint_overdue_count"
BINARY_SENSOR_MAINT_DUE_TODAY = "maint_due_today"

# API paths
API_HEALTH = "/health"
API_MAINT_COUNTERS = "/api/maintenance/counters"
API_MAINT_COMPLETIONS = "/api/maintenance/completions"
API_MAINT_PICK = "/api/maintenance/pick"
API_MAINT_SNOOZE = "/api/maintenance/counters/{id}/snooze"

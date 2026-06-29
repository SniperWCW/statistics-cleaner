"""Constants for the Statistics Cleaner integration."""

from __future__ import annotations

DOMAIN = "statistics_cleaner"
TITLE = "Statistics Cleaner"

CONF_ENTITY_ID = "entity_id"
CONF_THRESHOLD = "threshold"
CONF_WINDOW_HOURS = "window_hours"
CONF_NAME = "name"
CONF_DB_PATH = "db_path"

DEFAULT_NAME = "Statistics Cleaner"
DEFAULT_THRESHOLD = 0.15
DEFAULT_WINDOW_HOURS = 24
DEFAULT_DB_PATH = "home-assistant_v2.db"

SERVICE_SCAN_OUTLIERS = "scan_outliers"
SERVICE_APPLY_PREVIEW = "apply_preview"

ATTR_ENTRY_ID = "entry_id"
ATTR_PREVIEW = "preview"
ATTR_SCAN_RESULT = "scan_result"
ATTR_BACKUP_PATH = "backup_path"

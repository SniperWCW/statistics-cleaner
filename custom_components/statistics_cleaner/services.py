"""Service handling for Statistics Cleaner."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .cleaner import StatisticsCleaner
from .const import (
    ATTR_BACKUP_PATH,
    ATTR_ENTRY_ID,
    ATTR_SCAN_RESULT,
    CONF_DB_PATH,
    CONF_ENTITY_ID,
    CONF_THRESHOLD,
    CONF_WINDOW_HOURS,
    DEFAULT_DB_PATH,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_HOURS,
    DOMAIN,
    SERVICE_APPLY_PREVIEW,
    SERVICE_SCAN_OUTLIERS,
)

_LOGGER = logging.getLogger(__name__)

SCAN_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(CONF_ENTITY_ID): cv.entity_id,
        vol.Optional(CONF_DB_PATH): cv.string,
        vol.Optional(CONF_THRESHOLD): vol.Coerce(float),
        vol.Optional(CONF_WINDOW_HOURS): vol.Coerce(int),
    }
)

APPLY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(CONF_ENTITY_ID): cv.entity_id,
    }
)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SCAN_OUTLIERS):
        return

    cleaner = StatisticsCleaner(Path(hass.config.path(".storage", DOMAIN)))
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["cleaner"] = cleaner

    async def handle_scan(call: ServiceCall) -> ServiceResponse:
        entry = _resolve_entry(hass, call.data.get(ATTR_ENTRY_ID))
        entity_id = (
            call.data.get(CONF_ENTITY_ID)
            or entry.options.get(CONF_ENTITY_ID)
            or entry.data.get(CONF_ENTITY_ID)
        )
        database_path = _resolve_database_path(hass, entry, call.data.get(CONF_DB_PATH))
        threshold = _resolve_number_option(
            entry,
            call.data.get(CONF_THRESHOLD),
            CONF_THRESHOLD,
            DEFAULT_THRESHOLD,
        )
        window_hours = int(
            _resolve_number_option(
                entry,
                call.data.get(CONF_WINDOW_HOURS),
                CONF_WINDOW_HOURS,
                DEFAULT_WINDOW_HOURS,
            )
        )

        if not entity_id:
            raise ServiceValidationError("An entity_id is required.")

        result = await cleaner.scan(
            entry_id=entry.entry_id,
            entity_id=entity_id,
            threshold=threshold,
            window_hours=window_hours,
            database_path=database_path,
        )
        _LOGGER.info(
            "Scan completed for %s with %s candidate(s)",
            entity_id,
            len(result.candidates),
        )
        return {ATTR_SCAN_RESULT: result.as_dict()}

    async def handle_apply(call: ServiceCall) -> ServiceResponse:
        entry = _resolve_entry(
            hass,
            call.data.get(ATTR_ENTRY_ID),
            call.data.get(CONF_ENTITY_ID),
        )
        result = await cleaner.apply_preview(entry_id=entry.entry_id)
        _LOGGER.info(
            "Apply preview requested for %s, created backup at %s",
            entry.entry_id,
            result.backup_path,
        )
        return {
            ATTR_ENTRY_ID: entry.entry_id,
            ATTR_BACKUP_PATH: result.backup_path,
            "applied_count": result.applied_count,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCAN_OUTLIERS,
        handle_scan,
        schema=SCAN_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_PREVIEW,
        handle_apply,
        schema=APPLY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _resolve_entry(
    hass: HomeAssistant,
    entry_id: str | None,
    entity_id: str | None = None,
):
    """Resolve a config entry for the integration."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No Statistics Cleaner config entries are available.")

    if entry_id is None and entity_id is None:
        return entries[0]

    if entry_id is not None:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry

    if entity_id is not None:
        matching_entries = [
            entry
            for entry in entries
            if entry.options.get(CONF_ENTITY_ID) == entity_id
            or entry.data.get(CONF_ENTITY_ID) == entity_id
        ]
        if len(matching_entries) == 1:
            return matching_entries[0]
        if len(matching_entries) > 1:
            raise ServiceValidationError(
                f"Multiple config entries found for entity_id: {entity_id}. "
                "Use entry_id explicitly."
            )
        raise ServiceValidationError(f"Unknown entity_id: {entity_id}")

    raise ServiceValidationError(f"Unknown entry_id: {entry_id}")


def _resolve_database_path(hass: HomeAssistant, entry, override_path: str | None) -> Path:
    """Resolve the configured SQLite database path."""
    configured_path = (
        override_path
        or entry.options.get(CONF_DB_PATH)
        or entry.data.get(CONF_DB_PATH)
        or DEFAULT_DB_PATH
    )
    database_path = Path(configured_path)
    if not database_path.is_absolute():
        database_path = Path(hass.config.path(configured_path))
    return database_path


def _resolve_number_option(entry, override_value, key: str, fallback):
    """Resolve a numeric option from service data, options, or entry data."""
    if override_value is not None:
        return override_value
    if key in entry.options:
        return entry.options[key]
    if key in entry.data:
        return entry.data[key]
    return fallback

"""HomeOps Home Assistant Integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HomeOpsApiError, HomeOpsClient
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SERVER_URL,
    DATA_COUNTERS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_COMPLETE_ITEM,
    SERVICE_INCREMENT_COUNTER,
    SERVICE_LOG_VACUUM_MISSION,
    SERVICE_POST_CONDITION_SIGNAL,
    SERVICE_POST_VACUUM_ZONE_SIGNAL,
    SERVICE_SNOOZE_ITEM,
)
from .coordinator import HomeOpsCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

# ── Service schemas ────────────────────────────────────────────────────────────

SCHEMA_COMPLETE_ITEM = vol.Schema(
    {
        vol.Required("item_code"): cv.string,
    }
)

SCHEMA_SNOOZE_ITEM = vol.Schema(
    {
        vol.Required("item_code"): cv.string,
        vol.Optional("days", default=7): vol.All(int, vol.Range(min=1, max=365)),
    }
)

SCHEMA_POST_CONDITION_SIGNAL = vol.Schema(
    {
        # Restrict to catalog code format — prevents path traversal in URL construction.
        vol.Required("code"): vol.All(cv.string, vol.Match(r"^[a-z0-9_]{1,64}$")),
        vol.Required("urgency"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Required("source"): cv.string,
        vol.Optional("reason"): cv.string,
        # min=0.1 — zero-TTL would expire the signal on arrival (silent no-op).
        vol.Optional("valid_for_hours"): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
    }
)

SCHEMA_POST_VACUUM_ZONE_SIGNAL = vol.Schema(
    {
        vol.Required("zone_label"): cv.string,
        vol.Required("unit_name"): cv.string,
        vol.Optional("source"): cv.string,
        vol.Required("signal_type"): cv.string,
        vol.Optional("context"): dict,
        vol.Optional("notes"): cv.string,
    }
)

SCHEMA_INCREMENT_COUNTER = vol.Schema(
    {
        vol.Required("code"): vol.All(cv.string, vol.Match(r"^[a-z0-9_]{1,64}$")),
    }
)

SCHEMA_LOG_VACUUM_MISSION = vol.Schema(
    {
        vol.Required("ha_entity_id"): cv.string,
        vol.Optional("error_code", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("duration_min"): vol.All(int, vol.Range(min=0)),
        vol.Optional("started_at"): vol.All(int, vol.Range(min=0)),
        vol.Optional("stuck_count"): vol.All(int, vol.Range(min=0)),
        vol.Optional("panics_count"): vol.All(int, vol.Range(min=0)),
        vol.Optional("plan_err"): cv.string,
        vol.Optional("initiator"): cv.string,
        vol.Optional("raw_state_snapshot"): dict,
        # Roborock-only
        vol.Optional("clean_status"): cv.string,
        vol.Optional("roborock_error"): vol.All(int, vol.Range(min=0)),
        vol.Optional("roborock_error_desc"): cv.string,
    }
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_counter(counters: list[dict], item_code: str) -> dict:
    """Locate a counter by its catalog code field or slugified label.

    Counter objects expose a ``code`` field when the API returns catalog metadata
    inline.  If the field is absent we fall back to matching on the slugified
    label (same logic button.py used for unique-id generation).
    """
    import re

    def _slug(s: str) -> str:
        return re.sub(r"[^a-z0-9_]", "", s.lower().replace(" ", "_"))

    for counter in counters:
        if counter.get("code") == item_code:
            return counter
        if _slug(counter.get("label", "")) == item_code:
            return counter

    raise ServiceValidationError(
        f"No HomeOps maintenance item found with item_code='{item_code}'. "
        "Check available counters via the HomeOps sensor attributes."
    )


# ── Setup / teardown ──────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HomeOps from a config entry."""
    session = async_get_clientsession(hass)
    client = HomeOpsClient(
        session,
        entry.data[CONF_SERVER_URL],
        entry.data[CONF_API_KEY],
    )

    coordinator = HomeOpsCoordinator(
        hass,
        client,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Register services (idempotent — only register once across all entries) ──
    if not hass.services.has_service(DOMAIN, SERVICE_COMPLETE_ITEM):
        async def handle_complete_item(call: ServiceCall) -> None:
            """Handle homeops.complete_item service call."""
            item_code: str = call.data["item_code"]

            # Resolve item_code → catalog_id using the first loaded coordinator.
            # All entries share the same catalog, so any coordinator works.
            coordinator_ref: HomeOpsCoordinator = next(
                iter(hass.data[DOMAIN].values())
            )["coordinator"]

            counters: list[dict] = coordinator_ref.data.get(DATA_COUNTERS, []) if coordinator_ref.data else []
            counter = _find_counter(counters, item_code)
            catalog_id: int = counter["catalog_id"]

            try:
                await coordinator_ref.client.complete_item(catalog_id)
            except HomeOpsApiError as err:
                raise HomeAssistantError(
                    f"HomeOps complete_item failed for '{item_code}' "
                    f"(catalog_id={catalog_id}): {err}"
                ) from err

            await coordinator_ref.async_request_refresh()
            _LOGGER.info("homeops.complete_item: completed '%s' (catalog_id=%d)", item_code, catalog_id)

        hass.services.async_register(
            DOMAIN,
            SERVICE_COMPLETE_ITEM,
            handle_complete_item,
            schema=SCHEMA_COMPLETE_ITEM,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SNOOZE_ITEM):
        async def handle_snooze_item(call: ServiceCall) -> None:
            """Handle homeops.snooze_item service call."""
            item_code: str = call.data["item_code"]
            days: int = call.data["days"]

            coordinator_ref: HomeOpsCoordinator = next(
                iter(hass.data[DOMAIN].values())
            )["coordinator"]

            counters: list[dict] = coordinator_ref.data.get(DATA_COUNTERS, []) if coordinator_ref.data else []
            counter = _find_counter(counters, item_code)
            counter_id: int = counter["id"]

            try:
                await coordinator_ref.client.snooze_item(counter_id, days=days)
            except HomeOpsApiError as err:
                raise HomeAssistantError(
                    f"HomeOps snooze_item failed for '{item_code}' "
                    f"(counter_id={counter_id}, days={days}): {err}"
                ) from err

            await coordinator_ref.async_request_refresh()
            _LOGGER.info("homeops.snooze_item: snoozed '%s' (counter_id=%d) by %d days", item_code, counter_id, days)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SNOOZE_ITEM,
            handle_snooze_item,
            schema=SCHEMA_SNOOZE_ITEM,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_POST_CONDITION_SIGNAL):
        async def handle_post_condition_signal(call: ServiceCall) -> None:
            """Handle homeops.post_condition_signal service call."""
            code: str          = call.data["code"]
            urgency: float     = call.data["urgency"]
            source: str        = call.data["source"]
            reason: str | None = call.data.get("reason")
            valid_for_hours    = call.data.get("valid_for_hours")

            entry_data = next(iter(hass.data[DOMAIN].values()))
            client_ref: HomeOpsClient = entry_data["client"]
            coordinator_ref: HomeOpsCoordinator = entry_data["coordinator"]

            try:
                await client_ref.post_condition_signal(
                    code=code,
                    urgency=urgency,
                    source=source,
                    reason=reason,
                    valid_for_hours=valid_for_hours,
                )
            except HomeOpsApiError as err:
                raise HomeAssistantError(
                    f"HomeOps post_condition_signal failed for '{code}': {err}"
                ) from err

            await coordinator_ref.async_request_refresh()
            _LOGGER.info(
                "homeops.post_condition_signal: code=%s urgency=%.3f source=%s",
                code, urgency, source,
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_POST_CONDITION_SIGNAL,
            handle_post_condition_signal,
            schema=SCHEMA_POST_CONDITION_SIGNAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_POST_VACUUM_ZONE_SIGNAL):
        async def handle_post_vacuum_zone_signal(call: ServiceCall) -> None:
            """Handle homeops.post_vacuum_zone_signal service call."""
            zone_label: str       = call.data["zone_label"]
            unit_name: str        = call.data["unit_name"]
            source: str | None    = call.data.get("source")
            signal_type: str      = call.data["signal_type"]
            context: dict | None  = call.data.get("context")
            notes: str | None     = call.data.get("notes")

            entry_data = next(iter(hass.data[DOMAIN].values()))
            client_ref: HomeOpsClient = entry_data["client"]

            payload: dict = dict(
                zone_label=zone_label,
                unit_name=unit_name,
                signal_type=signal_type,
            )
            if source is not None:
                payload["source"] = source
            if context is not None:
                payload["context"] = context
            if notes is not None:
                payload["notes"] = notes

            try:
                await client_ref.post_vacuum_zone_signal(**payload)
            except HomeOpsApiError as err:
                raise HomeAssistantError(
                    f"HomeOps post_vacuum_zone_signal failed for zone '{zone_label}': {err}"
                ) from err

            _LOGGER.info(
                "homeops.post_vacuum_zone_signal: zone=%s unit=%s source=%s signal_type=%s",
                zone_label, unit_name, source, signal_type,
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_POST_VACUUM_ZONE_SIGNAL,
            handle_post_vacuum_zone_signal,
            schema=SCHEMA_POST_VACUUM_ZONE_SIGNAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_INCREMENT_COUNTER):
        async def handle_increment_counter(call: ServiceCall) -> None:
            """Handle homeops.increment_maintenance_counter service call."""
            code: str = call.data["code"]

            entry_data = next(iter(hass.data[DOMAIN].values()))
            client_ref: HomeOpsClient = entry_data["client"]
            coordinator_ref: HomeOpsCoordinator = entry_data["coordinator"]

            try:
                await client_ref.increment_counter_by_code(code)
            except HomeOpsApiError as err:
                raise HomeAssistantError(
                    f"HomeOps increment_maintenance_counter failed for '{code}': {err}"
                ) from err

            await coordinator_ref.async_request_refresh()
            _LOGGER.info("homeops.increment_maintenance_counter: incremented '%s'", code)

        hass.services.async_register(
            DOMAIN,
            SERVICE_INCREMENT_COUNTER,
            handle_increment_counter,
            schema=SCHEMA_INCREMENT_COUNTER,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_LOG_VACUUM_MISSION):
        async def handle_log_vacuum_mission(call: ServiceCall) -> None:
            """Handle homeops.log_vacuum_mission service call."""
            entry_data = next(iter(hass.data[DOMAIN].values()))
            client_ref: HomeOpsClient = entry_data["client"]

            try:
                await client_ref.log_vacuum_mission(
                    ha_entity_id=call.data["ha_entity_id"],
                    error_code=call.data.get("error_code", 0),
                    duration_min=call.data.get("duration_min"),
                    started_at=call.data.get("started_at"),
                    stuck_count=call.data.get("stuck_count"),
                    panics_count=call.data.get("panics_count"),
                    plan_err=call.data.get("plan_err"),
                    initiator=call.data.get("initiator"),
                    raw_state_snapshot=call.data.get("raw_state_snapshot"),
                    clean_status=call.data.get("clean_status"),
                    roborock_error=call.data.get("roborock_error"),
                    roborock_error_desc=call.data.get("roborock_error_desc"),
                )
            except HomeOpsApiError as err:
                raise HomeAssistantError(
                    f"HomeOps log_vacuum_mission failed for '{call.data['ha_entity_id']}': {err}"
                ) from err

            _LOGGER.info(
                "homeops.log_vacuum_mission: logged mission for %s (error_code=%d, duration_min=%s)",
                call.data["ha_entity_id"],
                call.data.get("error_code", 0),
                call.data.get("duration_min"),
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_LOG_VACUUM_MISSION,
            handle_log_vacuum_mission,
            schema=SCHEMA_LOG_VACUUM_MISSION,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a HomeOps config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    # Remove services only when the last config entry is unloaded.
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_COMPLETE_ITEM)
        hass.services.async_remove(DOMAIN, SERVICE_SNOOZE_ITEM)
        hass.services.async_remove(DOMAIN, SERVICE_POST_CONDITION_SIGNAL)
        hass.services.async_remove(DOMAIN, SERVICE_POST_VACUUM_ZONE_SIGNAL)
        hass.services.async_remove(DOMAIN, SERVICE_INCREMENT_COUNTER)
        hass.services.async_remove(DOMAIN, SERVICE_LOG_VACUUM_MISSION)

    return unloaded

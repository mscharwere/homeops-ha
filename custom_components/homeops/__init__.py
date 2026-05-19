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

    return unloaded

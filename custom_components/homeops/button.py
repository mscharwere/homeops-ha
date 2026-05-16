"""Button platform for HomeOps — Complete and Snooze actions per catalog item."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HomeOpsApiError, HomeOpsClient
from .const import DATA_COUNTERS, DEVICE_ID, DOMAIN
from .coordinator import HomeOpsCoordinator

_LOGGER = logging.getLogger(__name__)

# Slug-safe label: lowercase, spaces→underscores, strip non-alphanum
def _slugify(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]", "", label.lower().replace(" ", "_"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HomeOps button entities — one complete + one snooze per catalog item."""
    coordinator: HomeOpsCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client: HomeOpsClient = hass.data[DOMAIN][entry.entry_id]["client"]
    base_url = coordinator.client._base_url

    counters: list[dict] = coordinator.data.get(DATA_COUNTERS, [])

    entities: list[ButtonEntity] = []
    seen_catalog_ids: set[int] = set()

    for counter in counters:
        catalog_id: int | None = counter.get("catalog_id")
        counter_id: int | None = counter.get("id")
        label: str = counter.get("label", f"item_{catalog_id}")

        if catalog_id is None or counter_id is None:
            continue
        if catalog_id in seen_catalog_ids:
            continue
        seen_catalog_ids.add(catalog_id)

        slug = _slugify(label)
        entities.append(
            HomeOpsCompleteButton(coordinator, client, catalog_id, counter_id, label, slug, base_url)
        )
        entities.append(
            HomeOpsSnoozeButton(coordinator, client, catalog_id, counter_id, label, slug, base_url)
        )

    async_add_entities(entities)


def _device_info(base_url: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, DEVICE_ID)},
        name="HomeOps Maintenance",
        manufacturer="HomeOps",
        model="Maintenance",
        configuration_url=base_url,
    )


class HomeOpsCompleteButton(CoordinatorEntity[HomeOpsCoordinator], ButtonEntity):
    """Button: log a completion for a maintenance catalog item."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:check-circle-outline"

    def __init__(
        self,
        coordinator: HomeOpsCoordinator,
        client: HomeOpsClient,
        catalog_id: int,
        counter_id: int,
        label: str,
        slug: str,
        base_url: str,
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._catalog_id = catalog_id
        self._counter_id = counter_id
        self._attr_name = f"Complete: {label}"
        self._attr_unique_id = f"homeops_maint_complete_{slug}_{catalog_id}"
        self._attr_device_info = _device_info(base_url)

    async def async_press(self) -> None:
        """Handle button press — POST /api/maintenance/completions."""
        try:
            await self._client.complete_item(self._catalog_id)
        except HomeOpsApiError as err:
            raise HomeAssistantError(
                f"HomeOps complete failed for catalog_id={self._catalog_id}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()


class HomeOpsSnoozeButton(CoordinatorEntity[HomeOpsCoordinator], ButtonEntity):
    """Button: snooze a maintenance item by 7 days."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-plus-outline"

    def __init__(
        self,
        coordinator: HomeOpsCoordinator,
        client: HomeOpsClient,
        catalog_id: int,
        counter_id: int,
        label: str,
        slug: str,
        base_url: str,
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._catalog_id = catalog_id
        self._counter_id = counter_id
        self._attr_name = f"Snooze: {label}"
        self._attr_unique_id = f"homeops_maint_snooze_{slug}_{catalog_id}"
        self._attr_device_info = _device_info(base_url)

    async def async_press(self) -> None:
        """Handle button press — POST /api/maintenance/counters/:id/snooze."""
        try:
            await self._client.snooze_item(self._counter_id, days=7)
        except HomeOpsApiError as err:
            raise HomeAssistantError(
                f"HomeOps snooze failed for counter_id={self._counter_id}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()

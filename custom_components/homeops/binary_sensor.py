"""Binary sensor platform for HomeOps — Maintenance domain."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BINARY_SENSOR_MAINT_DUE_TODAY,
    DATA_COUNTERS,
    DEVICE_ID,
    DOMAIN,
)
from .coordinator import HomeOpsCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HomeOps binary sensor entities."""
    coordinator: HomeOpsCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    base_url = coordinator.client._base_url
    async_add_entities([HomeOpsMaintDueTodaySensor(coordinator, base_url)])


class HomeOpsMaintDueTodaySensor(
    CoordinatorEntity[HomeOpsCoordinator], BinarySensorEntity
):
    """Binary sensor: ON when at least one maintenance item is overdue."""

    _attr_has_entity_name = True
    _attr_name = "Maintenance Due Today"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: HomeOpsCoordinator, base_url: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"homeops_{BINARY_SENSOR_MAINT_DUE_TODAY}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DEVICE_ID)},
            name="HomeOps Maintenance",
            manufacturer="HomeOps",
            model="Maintenance",
            configuration_url=base_url,
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        counters: list[dict] = self.coordinator.data.get(DATA_COUNTERS, [])
        return any(c.get("status") == "overdue" for c in counters)

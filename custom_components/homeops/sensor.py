"""Sensor platform for HomeOps — Maintenance domain picks and overdue count."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COUNTERS,
    DATA_PICK_EVENING,
    DATA_PICK_MORNING,
    DATA_PICK_WEEKEND,
    DEVICE_ID,
    DOMAIN,
    SENSOR_MAINT_OVERDUE_COUNT,
    SENSOR_MAINT_PICK_EVENING,
    SENSOR_MAINT_PICK_MORNING,
    SENSOR_MAINT_PICK_WEEKEND,
)
from .coordinator import HomeOpsCoordinator


# ── Pick sensors ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class HomeOpsPickSensorDescription(SensorEntityDescription):
    """Describes a maintenance pick sensor."""
    data_key: str


PICK_SENSOR_DESCRIPTIONS: tuple[HomeOpsPickSensorDescription, ...] = (
    HomeOpsPickSensorDescription(
        key=SENSOR_MAINT_PICK_MORNING,
        name="Maintenance Pick — Morning",
        icon="mdi:weather-sunny",
        data_key=DATA_PICK_MORNING,
    ),
    HomeOpsPickSensorDescription(
        key=SENSOR_MAINT_PICK_EVENING,
        name="Maintenance Pick — Evening",
        icon="mdi:weather-night",
        data_key=DATA_PICK_EVENING,
    ),
    HomeOpsPickSensorDescription(
        key=SENSOR_MAINT_PICK_WEEKEND,
        name="Maintenance Pick — Weekend",
        icon="mdi:calendar-weekend",
        data_key=DATA_PICK_WEEKEND,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HomeOps sensor entities."""
    coordinator: HomeOpsCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    base_url = coordinator.client._base_url

    entities: list[SensorEntity] = []

    # Pick sensors (one per surface)
    for desc in PICK_SENSOR_DESCRIPTIONS:
        entities.append(HomeOpsPickSensor(coordinator, desc, base_url))

    # Overdue count sensor
    entities.append(HomeOpsOverdueCountSensor(coordinator, base_url))

    async_add_entities(entities)


def _device_info(base_url: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, DEVICE_ID)},
        name="HomeOps Maintenance",
        manufacturer="HomeOps",
        model="Maintenance",
        configuration_url=base_url,
    )


class HomeOpsPickSensor(CoordinatorEntity[HomeOpsCoordinator], SensorEntity):
    """Sensor reporting the top-pick maintenance item for a given surface.

    state  = item label (string), or "none" when no pick is available.
    attrs  = catalog_id, duration_min, task_type, requires_supply, urgency, category.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HomeOpsCoordinator,
        description: HomeOpsPickSensorDescription,
        base_url: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._data_key = description.data_key
        self._attr_unique_id = f"homeops_{description.key}"
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._attr_device_info = _device_info(base_url)

    @property
    def native_value(self) -> str:
        if self.coordinator.data is None:
            return "none"
        pick: dict | None = self.coordinator.data.get(self._data_key)
        if pick is None:
            return "none"
        return pick.get("label", "none")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        pick: dict | None = self.coordinator.data.get(self._data_key)
        if pick is None:
            return {}
        return {
            "catalog_id": pick.get("catalog_id"),
            "duration_min": pick.get("duration_min"),
            "task_type": pick.get("task_type"),
            "requires_supply": pick.get("requires_supply"),
            "urgency": pick.get("urgency"),
            "category": pick.get("category"),
        }


class HomeOpsOverdueCountSensor(CoordinatorEntity[HomeOpsCoordinator], SensorEntity):
    """Sensor reporting the count of overdue maintenance items."""

    _attr_has_entity_name = True
    _attr_name = "Maintenance Overdue Count"
    _attr_icon = "mdi:alert-circle"
    _attr_native_unit_of_measurement = "items"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: HomeOpsCoordinator, base_url: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"homeops_{SENSOR_MAINT_OVERDUE_COUNT}"
        self._attr_device_info = _device_info(base_url)

    @property
    def native_value(self) -> int:
        if self.coordinator.data is None:
            return 0
        counters: list[dict] = self.coordinator.data.get(DATA_COUNTERS, [])
        return sum(1 for c in counters if c.get("status") == "overdue")

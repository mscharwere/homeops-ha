"""DataUpdateCoordinator for HomeOps."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HomeOpsApiError, HomeOpsClient
from .const import (
    DATA_COUNTERS,
    DATA_PICK_EVENING,
    DATA_PICK_MORNING,
    DATA_PICK_WEEKEND,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class HomeOpsCoordinator(DataUpdateCoordinator[dict]):
    """Fetches maintenance counters and picks from the HomeOps API.

    coordinator.data shape:
    {
        "counters":      [{"id": 1, "catalog_id": 3, "label": "...", "status": "overdue", ...}],
        "pick_morning":  {"catalog_id": 3, "label": "...", "duration_min": 15, ...} | None,
        "pick_evening":  {...} | None,
        "pick_weekend":  {...} | None,
    }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: HomeOpsClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        """Fetch all Maintenance domain data — called by the coordinator on every interval."""
        try:
            counters = await self.client.list_counters()
        except HomeOpsApiError as err:
            raise UpdateFailed(f"Failed to fetch maintenance counters: {err}") from err

        pick_morning: dict | None = None
        pick_evening: dict | None = None
        pick_weekend: dict | None = None

        async def _fetch_morning() -> None:
            nonlocal pick_morning
            try:
                pick_morning = await self.client.get_pick("morning")
            except HomeOpsApiError as err:
                _LOGGER.warning("Failed to fetch morning pick: %s", err)

        async def _fetch_evening() -> None:
            nonlocal pick_evening
            try:
                pick_evening = await self.client.get_pick("evening")
            except HomeOpsApiError as err:
                _LOGGER.warning("Failed to fetch evening pick: %s", err)

        async def _fetch_weekend() -> None:
            nonlocal pick_weekend
            try:
                pick_weekend = await self.client.get_pick("weekend")
            except HomeOpsApiError as err:
                _LOGGER.warning("Failed to fetch weekend pick: %s", err)

        await asyncio.gather(_fetch_morning(), _fetch_evening(), _fetch_weekend())

        return {
            DATA_COUNTERS: counters,
            DATA_PICK_MORNING: pick_morning,
            DATA_PICK_EVENING: pick_evening,
            DATA_PICK_WEEKEND: pick_weekend,
        }

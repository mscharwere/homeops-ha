"""Async API client for HomeOps."""

from __future__ import annotations

import aiohttp

from .const import (
    API_HEALTH,
    API_MAINT_COMPLETIONS,
    API_MAINT_CONDITIONS,
    API_MAINT_COUNTERS,
    API_MAINT_PICK,
    API_MAINT_SNOOZE,
)


class HomeOpsApiError(Exception):
    """Raised when the HomeOps API returns a non-2xx response."""


class HomeOpsAuthError(HomeOpsApiError):
    """Raised on 401/403 responses."""


class HomeOpsClient:
    """Thin async wrapper around the HomeOps REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    # ── Health ─────────────────────────────────────────────────────────────────

    async def get_health(self) -> dict:
        """GET /api/health — validates connectivity and auth."""
        return await self._request("GET", API_HEALTH)  # type: ignore[return-value]

    # ── Maintenance Counters ───────────────────────────────────────────────────

    async def list_counters(self) -> list[dict]:
        """GET /api/maintenance/counters — full catalog with current counter state."""
        resp = await self._request("GET", API_MAINT_COUNTERS)
        return resp.get("data") or []

    async def complete_item(self, catalog_id: int) -> dict:
        """POST /api/maintenance/completions — log a completion."""
        return await self._request(  # type: ignore[return-value]
            "POST", API_MAINT_COMPLETIONS, json={"catalog_id": catalog_id}
        )

    async def snooze_item(self, counter_id: int, days: int = 7) -> dict:
        """POST /api/maintenance/counters/:id/snooze — push due date by N days."""
        path = API_MAINT_SNOOZE.replace("{id}", str(counter_id))
        return await self._request("POST", path, json={"days": days})  # type: ignore[return-value]

    # ── Maintenance Picks ──────────────────────────────────────────────────────

    async def get_pick(self, surface: str) -> dict | None:
        """GET /api/maintenance/pick?surface=<surface> — returns top pick or null."""
        try:
            resp = await self._request(
                "GET", API_MAINT_PICK, params={"surface": surface}
            )
            return resp.get("data")  # type: ignore[union-attr]
        except HomeOpsApiError as err:
            # 404 means no pick available for this surface
            if "404" in str(err):
                return None
            raise

    # ── Condition signals ──────────────────────────────────────────────────────

    async def post_condition_signal(
        self,
        code: str,
        urgency: float,
        source: str,
        reason: str | None = None,
        valid_for_hours: float | None = None,
    ) -> dict:
        """POST /api/maintenance/conditions/:code — upsert a condition signal.

        Args:
            code:            Catalog item code, e.g. 'oliver_feeder_desiccant'.
            urgency:         Float 0.0–1.0. ≥ condition_pick_min_urgency surfaces
                             the item; 1.0 = overdue.
            source:          Identifier for the signal source, e.g. 'ha.feeder_humidity'.
            reason:          Optional human-readable explanation shown in the UI.
            valid_for_hours: TTL override. Defaults to catalog's condition_ttl_hours.
        """
        path = API_MAINT_CONDITIONS.replace("{code}", code)
        payload: dict = {"urgency": round(float(urgency), 4), "source": source}
        if reason is not None:
            payload["reason"] = reason
        if valid_for_hours is not None:
            payload["valid_for_hours"] = valid_for_hours
        return await self._request("POST", path, json=payload)  # type: ignore[return-value]

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method, url, headers=self._headers, **kwargs
            ) as resp:
                if resp.status == 401:
                    raise HomeOpsAuthError("Invalid or missing API key")
                if resp.status == 403:
                    raise HomeOpsAuthError("Insufficient permissions")
                if resp.status == 404:
                    raise HomeOpsApiError(f"404: {path} not found")
                if not resp.ok:
                    try:
                        body = await resp.json()
                        msg = body.get("error", resp.reason)
                    except Exception:
                        msg = resp.reason
                    raise HomeOpsApiError(f"API error {resp.status}: {msg}")
                return await resp.json()
        except aiohttp.ClientError as err:
            raise HomeOpsApiError(f"Connection error: {err}") from err

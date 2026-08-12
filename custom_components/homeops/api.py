"""Async API client for HomeOps."""

from __future__ import annotations

import aiohttp

from .const import (
    API_HEALTH,
    API_MAINT_COMPLETIONS,
    API_MAINT_CONDITIONS,
    API_MAINT_COUNTERS,
    API_MAINT_INCREMENT_BY_CODE,
    API_MAINT_PICK,
    API_MAINT_SNOOZE,
    API_VACUUM_MISSIONS_LOG,
    API_VACUUM_ZONE_SIGNAL,
)


# Envelope keys carry no diagnostic value once `error` has been pulled out.
# Everything else in an error body is context the backend deliberately included.
_ERROR_ENVELOPE_KEYS = frozenset({"ok", "data", "error", "message"})

# Error details land in the HA log and in service-call failures; keep them
# readable rather than dumping an unbounded payload.
_MAX_ERROR_DETAIL = 300


async def _error_detail(resp: aiohttp.ClientResponse) -> str:
    """Render a diagnostic string from a non-2xx response body.

    The HomeOps backend returns a structured body on every error, and that body
    IS the diagnosis. A zone signal for a retired zone, for example, answers
    exactly why it failed::

        {"ok": false, "error": "zone_not_found",
         "unit_name": "Ethan", "zone_label": "Hallway"}

    404 responses used to be raised as a bare ``f"404: {path} not found"``,
    discarding this body entirely — which made a stale ``unit_name`` in an
    automation indistinguishable from a wrong URL, a deleted zone, or a
    misconfigured base URL. Every branch now preserves the server's own reason.

    Falls back to the raw text, then to the HTTP reason, when the body is not
    JSON (empty response, proxy error page, truncated payload).
    """
    try:
        body = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001 — body may be empty, HTML, or malformed
        try:
            text = (await resp.text()).strip()
        except Exception:  # noqa: BLE001 — connection may already be gone
            text = ""
        return text[:_MAX_ERROR_DETAIL] or resp.reason or "unknown error"

    if not isinstance(body, dict):
        return str(body)[:_MAX_ERROR_DETAIL]

    error = body.get("error") or body.get("message") or resp.reason or "unknown error"

    # Scalar extras only — nested structures are noise in a one-line log message.
    context = {
        key: value
        for key, value in body.items()
        if key not in _ERROR_ENVELOPE_KEYS and not isinstance(value, (dict, list))
    }
    if context:
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(context.items()))
        return f"{error} ({rendered})"[:_MAX_ERROR_DETAIL]

    return str(error)[:_MAX_ERROR_DETAIL]


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

    async def increment_counter_by_code(self, code: str) -> dict:
        """POST /api/maintenance/counters/by-code/:code/increment — increment a cycle counter by 1."""
        path = API_MAINT_INCREMENT_BY_CODE.replace("{code}", code)
        return await self._request("POST", path, json={})  # type: ignore[return-value]

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

    # ── Vacuum zone signals ───────────────────────────────────────────────────

    async def post_vacuum_zone_signal(
        self,
        zone_label: str,
        unit_name: str,
        signal_type: str,
        source: str | None = None,
        context: dict | None = None,
        notes: str | None = None,
    ) -> dict:
        """POST /api/vacuum/zones/signal — post a typed dirtiness signal for a vacuum zone.

        HomeOps owns weight computation. This client passes signal_type + context only.

        Args:
            zone_label:   Zone label as configured in HomeOps (e.g. 'Litter Box').
            unit_name:    Unit nickname as stored in the DB (e.g. 'Ethan').
                          The backend resolves by LOWER(nickname) — must match exactly.
            signal_type:  Key into vacuum_signal_config (e.g. 'petivity_visit_oliver').
                          Must match a row exactly (case-sensitive).
            source:       Optional signal source identifier (e.g. 'petivity', 'ha.entry_door').
            context:      Optional dict of context modifiers, e.g.
                          {"weather": "rainy", "season": "fall"}.
            notes:        Optional free-text note logged and emitted but not persisted to the zone row.
        """
        payload: dict = {
            "zone_label": zone_label,
            "unit_name": unit_name,
            "signal_type": signal_type,
        }
        if source is not None:
            payload["source"] = source
        if context is not None:
            payload["context"] = context
        if notes is not None:
            payload["notes"] = notes
        return await self._request("POST", API_VACUUM_ZONE_SIGNAL, json=payload)  # type: ignore[return-value]

    # ── Vacuum mission log ────────────────────────────────────────────────────

    async def log_vacuum_mission(
        self,
        ha_entity_id: str,
        error_code: int = 0,
        duration_min: int | None = None,
        active_duration_min: float | None = None,
        cleaned_area_m2: float | None = None,
        started_at: int | None = None,
        stuck_count: int | None = None,
        panics_count: int | None = None,
        plan_err: str | None = None,
        initiator: str | None = None,
        raw_state_snapshot: dict | None = None,
        clean_status: str | None = None,
        roborock_error: int | None = None,
        roborock_error_desc: str | None = None,
    ) -> dict:
        """POST /api/vacuum/missions/log — log a completed/failed vacuum mission.

        Args:
            ha_entity_id:        HA entity ID for the robot (e.g. 'vacuum.sam').
            error_code:          cleanMissionStatus.error; 0 = success (iRobot only).
            duration_min:        Mission duration in minutes. For iRobot this is
                                 cleanMissionStatus.mssnM (active minutes); for Roborock the
                                 HA automation derives it from last_clean_end - last_clean_begin,
                                 so it is WALL-CLOCK and includes paused/stuck time.
            active_duration_min: Roborock only — sensor.saros_10r_cleaning_time (min). Time spent
                                 ACTUALLY cleaning, excluding paused/stuck. The backend's
                                 duration_short check compares this for Roborock; duration_min is
                                 still sent because the gap between the two is a stall signal.
                                 None when the sensor is unreadable → omitted → check skipped.
            cleaned_area_m2:     Roborock only — sensor.saros_10r_cleaning_area (m²).
            started_at:          Mission start as Unix seconds (cleanMissionStatus.mssnStrtTm).
            stuck_count:         Lifetime stuck counter snapshot — bbrun.nStuck (iRobot only).
            panics_count:        Lifetime panics counter — bbrun.nPanics (iRobot only).
            plan_err:            Nav plan error string — mssnNavStats.plnErr (iRobot only).
            initiator:           Mission initiator — cleanMissionStatus.initiator.
            raw_state_snapshot:  Full iRobot raw_state dict; used for lifetime stuck baseline.
            clean_status:        Terminal status string (Roborock only).
            roborock_error:      0 = no error (Roborock only).
            roborock_error_desc: Human-readable error description (Roborock only).
        """
        payload: dict = {"ha_entity_id": ha_entity_id, "error_code": error_code}
        if duration_min is not None:
            payload["duration_min"] = duration_min
        # None is omitted rather than sent as 0. The backend treats an absent value as
        # "no reading" and skips its duration check; a 0 would read as "cleaned for zero
        # minutes" and flag duration_short on every mission with an unreadable sensor.
        if active_duration_min is not None:
            payload["active_duration_min"] = active_duration_min
        if cleaned_area_m2 is not None:
            payload["cleaned_area_m2"] = cleaned_area_m2
        if started_at is not None:
            payload["started_at"] = started_at
        if stuck_count is not None:
            payload["stuck_count"] = stuck_count
        if panics_count is not None:
            payload["panics_count"] = panics_count
        if plan_err is not None:
            payload["plan_err"] = plan_err
        if initiator is not None:
            payload["initiator"] = initiator
        if raw_state_snapshot is not None:
            payload["raw_state_snapshot"] = raw_state_snapshot
        if clean_status is not None:
            payload["clean_status"] = clean_status
        if roborock_error is not None:
            payload["roborock_error"] = roborock_error
        if roborock_error_desc is not None:
            payload["roborock_error_desc"] = roborock_error_desc
        return await self._request("POST", API_VACUUM_MISSIONS_LOG, json=payload)  # type: ignore[return-value]

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
                if not resp.ok:
                    raise HomeOpsApiError(
                        f"API error {resp.status} on {path}: "
                        f"{await _error_detail(resp)}"
                    )
                return await resp.json()
        except aiohttp.ClientError as err:
            raise HomeOpsApiError(f"Connection error: {err}") from err

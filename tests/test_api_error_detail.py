"""Regression tests — HomeOps API client error reporting.

BUG
───
``_request`` short-circuited every 404 with a bare message that threw the
response body away::

    if resp.status == 404:
        raise HomeOpsApiError(f"404: {path} not found")

The HomeOps backend answers a failed zone signal with the full diagnosis::

    {"ok": false, "error": "zone_not_found",
     "unit_name": "Ethan", "zone_label": "Hallway"}

Discarding it made four very different faults look identical in the HA log —
a stale ``unit_name`` in an automation, a retired zone, a wrong path, and a
misconfigured base URL all surfaced as "404: /api/vacuum/zones/signal not
found". That is precisely what hid the real cause of the live
``post_vacuum_zone_signal`` failures: the automations were still addressing
zones that moved from Ethan to Saros on 2026-07-03.

The 404 branch is gone; every non-2xx now flows through the same
body-preserving path.
"""

from __future__ import annotations

import asyncio
import json


class FakeResponse:
    """Minimal aiohttp.ClientResponse stand-in for the error paths."""

    def __init__(self, *, status=404, reason="Not Found", body=None, text=None,
                 raises_json=False, raises_text=False):
        self.status = status
        self.reason = reason
        self._body = body
        self._text = text if text is not None else (
            json.dumps(body) if body is not None else ""
        )
        self._raises_json = raises_json
        self._raises_text = raises_text

    @property
    def ok(self):
        return self.status < 400

    async def json(self, content_type=None):  # noqa: ARG002 — signature parity
        if self._raises_json or self._body is None:
            raise ValueError("not json")
        return self._body

    async def text(self):
        if self._raises_text:
            raise ValueError("connection gone")
        return self._text


def detail(api_module, resp):
    return asyncio.run(api_module._error_detail(resp))


# ── The live failure this was written for ────────────────────────────────────


def test_zone_not_found_body_is_preserved(api_module):
    """The 404 that broke post_vacuum_zone_signal must name unit AND zone."""
    resp = FakeResponse(body={
        "ok": False,
        "error": "zone_not_found",
        "unit_name": "Ethan",
        "zone_label": "Hallway",
    })

    result = detail(api_module, resp)

    assert "zone_not_found" in result
    assert "unit_name=Ethan" in result
    assert "zone_label=Hallway" in result


def test_unit_not_found_is_distinguishable_from_zone_not_found(api_module):
    """A wrong unit and a wrong zone must not collapse to the same message."""
    unit = detail(api_module, FakeResponse(body={
        "ok": False, "error": "unit_not_found", "unit_name": "Etahn",
    }))
    zone = detail(api_module, FakeResponse(body={
        "ok": False, "error": "zone_not_found",
        "unit_name": "Ethan", "zone_label": "Hallway",
    }))

    assert unit != zone
    assert "unit_not_found" in unit
    assert "zone_label" not in unit


# ── Envelope handling ────────────────────────────────────────────────────────


def test_envelope_keys_are_not_echoed_as_context(api_module):
    result = detail(api_module, FakeResponse(body={
        "ok": False, "data": None, "error": "unknown_signal_type",
        "signal_type": "made_up",
    }))

    assert result == "unknown_signal_type (signal_type=made_up)"


def test_error_without_context_renders_bare(api_module):
    result = detail(api_module, FakeResponse(
        status=500, reason="Internal Server Error",
        body={"data": None, "error": "Failed to log mission"},
    ))

    assert result == "Failed to log mission"


def test_nested_values_are_omitted_from_the_one_line_detail(api_module):
    result = detail(api_module, FakeResponse(body={
        "error": "validation_failed",
        "zone_label": "Hallway",
        "details": {"deep": ["structure"]},
        "candidates": ["a", "b"],
    }))

    assert "zone_label=Hallway" in result
    assert "deep" not in result
    assert "candidates" not in result


def test_message_key_is_used_when_error_is_absent(api_module):
    result = detail(api_module, FakeResponse(body={"message": "rate limited"}))

    assert result == "rate limited"


# ── Fallbacks ────────────────────────────────────────────────────────────────


def test_non_json_body_falls_back_to_text(api_module):
    result = detail(api_module, FakeResponse(
        status=502, reason="Bad Gateway",
        text="<html>nginx upstream unavailable</html>", raises_json=True,
    ))

    assert "nginx upstream unavailable" in result


def test_empty_body_falls_back_to_http_reason(api_module):
    result = detail(api_module, FakeResponse(
        status=404, reason="Not Found", text="", raises_json=True,
    ))

    assert result == "Not Found"


def test_unreadable_body_still_produces_a_message(api_module):
    result = detail(api_module, FakeResponse(
        status=404, reason="Not Found", raises_json=True, raises_text=True,
    ))

    assert result == "Not Found"


def test_json_list_body_is_stringified(api_module):
    result = detail(api_module, FakeResponse(body=["nope"]))

    assert "nope" in result


def test_detail_is_length_capped(api_module):
    result = detail(api_module, FakeResponse(body={
        "error": "boom", "context": "x" * 5000,
    }))

    assert len(result) <= api_module._MAX_ERROR_DETAIL


# ── The 404 special case is gone ─────────────────────────────────────────────


def test_no_404_short_circuit_remains_in_request(api_module):
    """Guard against the bare-404 branch being reintroduced."""
    import inspect

    source = inspect.getsource(api_module.HomeOpsClient._request)

    assert "not found" not in source
    assert "_error_detail" in source
    # 401/403 keep their dedicated auth branches; 404 must not have one.
    assert "resp.status == 404" not in source


def test_auth_statuses_still_raise_auth_errors(api_module):
    """401/403 keep their own branch — they are actionable without a body."""
    import inspect

    source = inspect.getsource(api_module.HomeOpsClient._request)

    assert "resp.status == 401" in source
    assert "resp.status == 403" in source
    assert issubclass(api_module.HomeOpsAuthError, api_module.HomeOpsApiError)

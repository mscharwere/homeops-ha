"""Regression tests — log_vacuum_mission active-cleaning fields.

BUG
───
The Saros mission-log automation was updated to send Roborock's own
active-cleaning telemetry (``active_duration_min`` from
``sensor.saros_10r_cleaning_time`` and ``cleaned_area_m2`` from
``sensor.saros_10r_cleaning_area``), because the existing ``duration_min`` is
WALL-CLOCK for Roborock and therefore includes paused and stuck time — the
backend held a 1062-minute mission stamped ``main_brush_jammed`` against a robot
with roughly three hours of battery.

But ``SCHEMA_LOG_VACUUM_MISSION`` is a bare ``vol.Schema({...})`` with no
``extra=vol.ALLOW_EXTRA``, and voluptuous defaults to ``PREVENT_EXTRA``. Sending
either new key would have made Home Assistant reject the ENTIRE
``homeops.log_vacuum_mission`` service call before it ever reached HomeOps —
breaking Saros mission logging completely, which is strictly worse than the bug
being fixed. Caught in review by ARIIA before it shipped.

A second, subtler trap sits directly behind the first. The automation sends
``none`` (a real JSON null) when a sensor is unreadable, deliberately, so the
backend SKIPS its duration check rather than judging on a missing reading.
``vol.Coerce(float)`` raises on ``None``, so declaring the fields with a bare
coercer would still reject the whole call on exactly that path — same failure
class, but only on the rarer branch and so much harder to spot. Hence
``vol.Any(None, ...)``.

These tests pin both halves, plus the structural rule that the schema must know
every field the handler forwards.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "homeops"

NEW_FIELDS = ("active_duration_min", "cleaned_area_m2")


# ── Schema / handler consistency (static) ────────────────────────────────────
#
# __init__.py imports Home Assistant, which is deliberately not a test
# dependency (see conftest.py), so the schema is inspected via AST rather than
# executed. That is sufficient here: the bug is a missing KEY, which is visible
# in the source without running anything.


def _init_tree() -> ast.Module:
    return ast.parse((PKG_DIR / "__init__.py").read_text(encoding="utf-8"))


def _schema_keys() -> set[str]:
    """String keys declared in SCHEMA_LOG_VACUUM_MISSION."""
    tree = _init_tree()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "SCHEMA_LOG_VACUUM_MISSION" not in targets:
            continue
        keys: set[str] = set()
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Dict):
                for key in sub.keys:
                    # Keys are vol.Required("x") / vol.Optional("x") calls.
                    if isinstance(key, ast.Call) and key.args:
                        arg = key.args[0]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            keys.add(arg.value)
        return keys
    raise AssertionError("SCHEMA_LOG_VACUUM_MISSION not found in __init__.py")


def _handler_node(func_name: str) -> ast.AST:
    """Locate one service handler's function node.

    Scoping matters: __init__.py defines six handlers (complete_item, snooze_item,
    post_condition_signal, post_vacuum_zone_signal, increment_counter and this
    one), all reading their own call.data keys. A module-wide walk mixes every
    service's keys together, and comparing that union against ONE schema is
    meaningless — which is exactly how the earlier version of this test ended up
    filtering the union back down until it could never fail.
    """
    for node in ast.walk(_init_tree()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    raise AssertionError(f"{func_name} not found in __init__.py")


def _handler_data_keys(func_name: str = "handle_log_vacuum_mission") -> set[str]:
    """Keys the named handler reads via call.data.get(...) / call.data[...]."""
    keys: set[str] = set()
    for node in ast.walk(_handler_node(func_name)):
        # call.data.get("x")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "data"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        # call.data["x"]
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "data"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


@pytest.mark.parametrize("field", NEW_FIELDS)
def test_schema_declares_new_field(field):
    """Without this the whole service call is rejected — PREVENT_EXTRA is the default."""
    assert field in _schema_keys()


@pytest.mark.parametrize("field", NEW_FIELDS)
def test_schema_allows_none_for_new_field(field):
    """The automation sends null when a sensor is unreadable; a bare Coerce would raise.

    Asserted on the source text for the field's declaration rather than by
    executing the schema, since __init__.py cannot be imported without HA.
    """
    src = (PKG_DIR / "__init__.py").read_text(encoding="utf-8")
    idx = src.index(f'vol.Optional("{field}")')
    decl = src[idx: idx + 200]
    assert "vol.Any(" in decl and "None" in decl, (
        f"{field} must accept None — the automation sends null for an unreadable sensor"
    )


def test_every_handler_field_is_declared_in_the_schema():
    """Structural guard against this whole bug class recurring.

    Any field the handler forwards must be declared in the schema, or voluptuous
    PREVENT_EXTRA rejects the entire service call.

    ── Why this is a straight set difference ─────────────────────────────────────
    The first version of this test read:

        mission_keys = {k for k in handler_keys if k in schema_keys or k in NEW_FIELDS}
        missing = mission_keys - schema_keys

    which could never fail. `mission_keys` was an INCLUSION filter, so it was a
    subset of `schema_keys | NEW_FIELDS` by construction — and since NEW_FIELDS
    are themselves declared in the schema, `mission_keys - schema_keys` was
    provably always empty. A genuinely undeclared field was filtered out before
    it could ever reach the assertion. ARIIA caught it by mutation-testing:
    adding an undeclared field to the handler left the test passing.

    The filter existed to stop OTHER services' call.data keys leaking in from a
    module-wide AST walk. That is now solved properly by scoping the walk to this
    handler's own body (see _handler_node), so no filtering is needed and the
    comparison can be direct.
    """
    schema_keys = _schema_keys()
    handler_keys = _handler_data_keys()

    # Guard the guard: if the scoping ever silently matched nothing, the set
    # difference below would be trivially empty and pass for the wrong reason.
    assert handler_keys, "no call.data keys found — handler scoping is broken"

    missing = handler_keys - schema_keys
    assert not missing, f"handler reads fields absent from the schema: {sorted(missing)}"


def test_handler_scoping_excludes_other_services_keys():
    """The scoping must be tight, not just non-empty.

    'unit_name' belongs to post_vacuum_zone_signal and must never appear in the
    mission-log handler's key set — if it does, the walk has escaped its function
    and the guard above is comparing the wrong thing against the schema.
    """
    mission_keys = _handler_data_keys()
    other_keys = _handler_data_keys("handle_post_vacuum_zone_signal")

    assert "ha_entity_id" in mission_keys
    assert "unit_name" in other_keys, "fixture assumption: this key identifies the other handler"
    assert "unit_name" not in mission_keys


# ── API client payload behaviour (executed) ──────────────────────────────────


class _CapturingClient:
    """Captures the payload log_vacuum_mission would POST."""

    def __init__(self, api_module):
        self.captured: dict | None = None
        self.client = api_module.HomeOpsClient.__new__(api_module.HomeOpsClient)

        async def _fake_request(method, path, **kwargs):
            self.captured = kwargs.get("json")
            return {"ok": True}

        self.client._request = _fake_request  # noqa: SLF001


def _call(api_module, **kwargs) -> dict:
    cap = _CapturingClient(api_module)
    asyncio.run(cap.client.log_vacuum_mission(ha_entity_id="vacuum.saros_10r", **kwargs))
    return cap.captured


def test_new_fields_are_forwarded_to_the_backend(api_module):
    payload = _call(api_module, active_duration_min=7.43, cleaned_area_m2=5.7)
    assert payload["active_duration_min"] == 7.43
    assert payload["cleaned_area_m2"] == 5.7


def test_none_is_omitted_not_sent_as_zero(api_module):
    """A missing reading must be absent from the payload, never 0.

    The backend skips its duration check on an absent value; a 0 would read as
    "cleaned for zero minutes" and flag duration_short on every mission with an
    unreadable sensor — reintroducing the exact false positive this work removes.
    """
    payload = _call(api_module, active_duration_min=None, cleaned_area_m2=None)
    assert "active_duration_min" not in payload
    assert "cleaned_area_m2" not in payload


def test_wall_clock_duration_still_sent_alongside_active(api_module):
    """Both are kept: the gap between them is itself the stall signal."""
    payload = _call(api_module, duration_min=201, active_duration_min=12.5)
    assert payload["duration_min"] == 201
    assert payload["active_duration_min"] == 12.5


def test_genuine_zero_is_still_sent(api_module):
    """0 from a real reading is meaningful — a mission that cleaned nothing SHOULD flag."""
    payload = _call(api_module, active_duration_min=0.0)
    assert payload["active_duration_min"] == 0.0

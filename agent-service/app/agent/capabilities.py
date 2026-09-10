"""Single capability-to-permission contract for every tool entry point."""
from __future__ import annotations

import json
from typing import Any

CAPABILITY_PERMISSIONS = {
    "sql": frozenset({"solidset_sql", "solidset_schema"}),
    "solidset_sql": frozenset({"solidset_sql"}),
    "solidset_schema": frozenset({"solidset_schema"}),
    "external_web": frozenset({"external_web"}),
    "tool:query_sql_server": frozenset({"solidset_sql"}),
    "tool:get_db_schema": frozenset({"solidset_schema"}),
    "tool:google_web_search": frozenset({"external_web"}),
}


def normalize_capabilities(values: Any) -> set[str]:
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            values = [values]
        if isinstance(values, str):
            values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    return {value.strip().casefold() for value in values if isinstance(value, str) and value.strip()}


def tool_permissions(values: Any) -> set[str]:
    """Reasoning/coding capabilities grant no operational access or writes."""
    permissions: set[str] = set()
    for capability in normalize_capabilities(values):
        permissions.update(CAPABILITY_PERMISSIONS.get(capability, ()))
        if capability.startswith("tool:") and len(capability) > 5:
            permissions.add(capability)
    return permissions

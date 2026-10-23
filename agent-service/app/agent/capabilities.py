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
    "google_web_search": frozenset({"external_web"}),
}

# ``vision`` is the canonical routing capability. ``multimodal`` remains a
# supported alias so existing model assignments do not need to be recreated.
CAPABILITY_ALIASES = {
    "multimodal": "vision",
    "image": "vision",
    "visual": "vision",
    "imagen": "vision",
    "audio_input": "audio",
    "speech": "audio",
}
VISUAL_CAPABILITIES = frozenset({"vision"})
AUDIO_CAPABILITIES = frozenset({"audio"})
AUDIO_INPUT_PROVIDERS = frozenset({
    "openai", "openai_compatible", "local_openai", "azure_openai",
})


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
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        capability = value.strip().casefold()
        normalized.add(CAPABILITY_ALIASES.get(capability, capability))
    return normalized


def has_visual_capability(values: Any) -> bool:
    return bool(VISUAL_CAPABILITIES.intersection(normalize_capabilities(values)))


def has_audio_capability(values: Any) -> bool:
    return bool(AUDIO_CAPABILITIES.intersection(normalize_capabilities(values)))


def supports_audio_provider(provider: Any) -> bool:
    normalized = str(provider or "").strip().casefold().replace("-", "_")
    return normalized in AUDIO_INPUT_PROVIDERS


def tool_permissions(values: Any) -> set[str]:
    """Reasoning/coding capabilities grant no operational access or writes."""
    permissions: set[str] = set()
    for capability in normalize_capabilities(values):
        permissions.update(CAPABILITY_PERMISSIONS.get(capability, ()))
        if capability.startswith("tool:") and len(capability) > 5:
            permissions.add(capability)
    return permissions

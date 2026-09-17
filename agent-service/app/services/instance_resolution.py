from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request

from app.connectors.db_client import (
    get_solidset_instance,
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _request_ip_details(request: Request) -> tuple[str, str]:
    """Obtiene la IP TCP y la cadena informada por proxies, sin confundirlas."""
    direct_ip = request.client.host if request.client else "unknown"
    forwarded_ip = (
        request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        or request.headers.get("x-real-ip", "").strip()
        or "-"
    )
    return direct_ip, forwarded_ip


def _solidset_header_host(value: str) -> str | None:
    """Accept a host or host:port, never a URL, path, or credentials."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 255 or any(char.isspace() for char in raw):
        return None
    if any(char in raw for char in "/\\?#@"):
        return None
    try:
        host = ipaddress.ip_address(raw.strip("[]")).compressed.lower()
        return "localhost" if host in _LOOPBACK_HOSTS else host
    except ValueError:
        pass
    try:
        parsed = urlsplit(f"//{raw}")
        host = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    if not host or parsed.username or parsed.password or parsed.path:
        return None
    host = host.rstrip(".").lower()
    return "localhost" if host in _LOOPBACK_HOSTS else host or None


def _resolve_request_solidset_instance(request: Request) -> dict[str, Any] | None:
    """Match the destination host sent in X-SolidSET-Instance to SourceIP."""
    host = _solidset_header_host(request.headers.get("x-solidset-instance", ""))
    if not host:
        return None
    # Recheck each request: an outbound host may be shared or reassigned.
    return get_solidset_instance(source_ip=host)


def _attach_solidset_instance(candidates: list[dict], instance: dict[str, Any]) -> None:
    for candidate in candidates:
        payload = (
            candidate.get("payload")
            if isinstance(candidate.get("payload"), dict)
            else {}
        )
        regional_sources = []
        for source_name in ("Info", "TimeData", "UserData"):
            source = payload.get(source_name)
            if isinstance(source, dict):
                regional_sources.append(
                    {str(key).lower(): value for key, value in source.items()}
                )

        def regional_value(*keys: str) -> str:
            for source in regional_sources:
                for key in keys:
                    value = source.get(key.lower())
                    if value not in (None, ""):
                        return str(value).strip()
            return ""

        original_fingerprint = str(candidate.get("fingerprint") or "")
        candidate["fingerprint"] = f"{instance['ID']}:{original_fingerprint}"
        candidate["solidset_instance_id"] = str(instance["ID"])
        candidate["solidset_instance_code"] = str(instance["Code"])
        candidate["solidset_base_url"] = str(instance["BaseUrl"]).rstrip("/")
        candidate["solidset_notification_url"] = str(
            instance.get("NotificationUrl") or ""
        ).rstrip("/")
        candidate["country_code"] = (
            regional_value("country_code", "countryCode", "country")
            or str(instance.get("CountryCode") or "PT")
        ).upper()
        candidate["locale"] = regional_value(
            "locale", "culture", "language_tag"
        ) or str(instance.get("Locale") or "pt-PT")
        requested_time_zone = regional_value(
            "time_zone", "timeZone", "timezone", "iana_time_zone", "ianaTimeZone"
        )
        if requested_time_zone:
            try:
                ZoneInfo(requested_time_zone)
            except (ZoneInfoNotFoundError, ValueError):
                requested_time_zone = ""
        candidate["time_zone"] = requested_time_zone or str(
            instance.get("TimeZone") or "Europe/Lisbon"
        )

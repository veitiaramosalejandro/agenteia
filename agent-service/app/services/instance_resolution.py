from __future__ import annotations

import threading
from time import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request

from app.connectors.db_client import (
    get_solidset_instance,
    list_active_solidset_instances,
)


_solidset_instance_cache_lock = threading.Lock()
_solidset_instance_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_instance_cache() -> None:
    with _solidset_instance_cache_lock:
        _solidset_instance_cache.clear()


def _request_ip_details(request: Request) -> tuple[str, str]:
    """Obtiene la IP TCP y la cadena informada por proxies, sin confundirlas."""
    direct_ip = request.client.host if request.client else "unknown"
    forwarded_ip = (
        request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        or request.headers.get("x-real-ip", "").strip()
        or "-"
    )
    return direct_ip, forwarded_ip


def _resolve_request_solidset_instance(request: Request) -> dict[str, Any] | None:
    """Resuelve la instalación aun cuando la petición atraviesa Nginx."""
    direct_ip, forwarded_ip = _request_ip_details(request)
    instance_code = request.headers.get("x-solidset-instance", "").strip()
    request_host = str(getattr(getattr(request, "url", None), "hostname", "") or "")
    cache_key = "|".join(
        (
            instance_code.lower(),
            forwarded_ip.lower(),
            direct_ip.lower(),
            request_host.lower(),
        )
    )
    with _solidset_instance_cache_lock:
        cached = _solidset_instance_cache.get(cache_key)
        if cached and cached[0] > time():
            return dict(cached[1])
    if instance_code:
        instance = get_solidset_instance(code=instance_code, source_ip=None)
        if instance is not None:
            with _solidset_instance_cache_lock:
                _solidset_instance_cache[cache_key] = (time() + 60, dict(instance))
        return instance

    # SourceIP puede contener una IP o el host público registrado para la
    # instalación. Detrás de Nginx, request.client es la IP del contenedor del
    # proxy, por lo que también se prueban X-Forwarded-For/X-Real-IP y Host.
    candidates = [forwarded_ip, direct_ip, request_host]
    seen: set[str] = set()
    for candidate in candidates:
        source = str(candidate or "").strip()
        if not source or source == "-" or source in seen:
            continue
        seen.add(source)
        instance = get_solidset_instance(source_ip=source)
        if instance is not None:
            with _solidset_instance_cache_lock:
                _solidset_instance_cache[cache_key] = (time() + 60, dict(instance))
            return instance

    # En una instalación con un único SolidSET activo no existe ambigüedad y
    # Nginx/Docker puede ocultar tanto el host público como la IP original.
    # Con varias instalaciones no se aplica este fallback: deben identificarse
    # por cabecera, IP o host para impedir respuestas en el sistema equivocado.
    active_instances = list_active_solidset_instances()
    if len(active_instances) == 1:
        instance = active_instances[0]
        with _solidset_instance_cache_lock:
            _solidset_instance_cache[cache_key] = (time() + 60, dict(instance))
        return instance
    return None


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

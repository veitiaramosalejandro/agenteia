from __future__ import annotations

import json
import os
import socket
import ssl
from datetime import datetime
from typing import Any, Optional
from urllib import error as urlerror
from urllib.parse import urlparse
from urllib.request import Request as URLRequest, urlopen

from app.config import settings
from app.connectors.db_client import list_active_solidset_instances
from app.connectors.solidset_sql import test_connection as test_solidset_sql_connection


def _extract_host_port_from_url(
    raw_url: str, default_port: int
) -> tuple[Optional[str], int]:
    parsed = urlparse(raw_url or "")
    host = parsed.hostname
    port = parsed.port or default_port
    return host, int(port)


def _extract_host_port(raw_host: str, default_port: int) -> tuple[Optional[str], int]:
    host = (raw_host or "").strip()
    if not host:
        return None, int(default_port)
    if ":" in host and not host.startswith("["):
        left, right = host.rsplit(":", 1)
        if right.isdigit():
            return left.strip(), int(right)
    return host, int(default_port)


def _probe_tcp(host: Optional[str], port: int, timeout_seconds: float = 2.5) -> dict:
    if not host:
        return {"ok": False, "error": "host_vacio", "host": host, "port": port}

    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {"ok": True, "host": host, "port": port}
    except Exception as exc:
        return {"ok": False, "host": host, "port": port, "error": str(exc)}


def _probe_http(
    base_url: str, path: str = "", timeout_seconds: float = 3.5, verify_tls: bool = True
) -> dict:
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "url_vacia", "url": base}

    target = f"{base}{path}" if path else base
    try:
        request = URLRequest(target)
        if target.lower().startswith("https://") and not verify_tls:
            context = ssl._create_unverified_context()
            with urlopen(request, timeout=timeout_seconds, context=context) as response:
                status_code = int(getattr(response, "status", 200))
        else:
            with urlopen(request, timeout=timeout_seconds) as response:
                status_code = int(getattr(response, "status", 200))

        return {
            "ok": 200 <= status_code < 500,
            "status_code": status_code,
            "url": target,
        }
    except urlerror.HTTPError as exc:
        status_code = int(getattr(exc, "code", 0))
        return {
            "ok": 200 <= status_code < 500,
            "status_code": status_code,
            "url": target,
            "error": str(exc),
        }
    except Exception as exc:
        return {"ok": False, "url": target, "error": str(exc)}


def _probe_http_json(
    base_url: str, path: str, timeout_seconds: float = 4.0, verify_tls: bool = True
) -> dict:
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "url_vacia", "url": base}

    target = f"{base}{path}" if path else base
    try:
        request = URLRequest(target)
        if target.lower().startswith("https://") and not verify_tls:
            context = ssl._create_unverified_context()
            response = urlopen(request, timeout=timeout_seconds, context=context)
        else:
            response = urlopen(request, timeout=timeout_seconds)

        with response as resp:
            status_code = int(getattr(resp, "status", 200))
            body = resp.read().decode("utf-8", errors="ignore")

        data = json.loads(body)
        result = {
            "ok": 200 <= status_code < 500,
            "status_code": status_code,
            "url": target,
            "json": {
                "title": data.get("info", {}).get("title")
                if isinstance(data, dict)
                else None,
                "version": data.get("info", {}).get("version")
                if isinstance(data, dict)
                else None,
                "paths_count": len(data.get("paths", {}))
                if isinstance(data, dict) and isinstance(data.get("paths"), dict)
                else None,
            },
        }
        return result
    except Exception as exc:
        return {"ok": False, "url": target, "error": str(exc)}


def _probe_sql_server_connection(instance: dict[str, Any]) -> dict:
    """Checks the SQL Server connection persisted for one SolidSET instance."""
    try:
        result = test_solidset_sql_connection(instance)
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _run_startup_connectivity_checks() -> dict:
    checks = {}

    ollama_host, ollama_port = _extract_host_port_from_url(
        settings.OLLAMA_BASE_URL, 11434
    )
    checks["ollama"] = {
        "tcp": _probe_tcp(ollama_host, ollama_port),
        "http": _probe_http(settings.OLLAMA_BASE_URL, "/api/tags"),
    }

    qdrant_host, qdrant_port = _extract_host_port_from_url(settings.VECTOR_DB_URL, 6333)
    checks["qdrant"] = {
        "tcp": _probe_tcp(qdrant_host, qdrant_port),
        "http": _probe_http(settings.VECTOR_DB_URL, "/collections"),
    }

    redis_host, redis_port = _extract_host_port_from_url(settings.REDIS_URL, 6379)
    checks["redis"] = {
        "tcp": _probe_tcp(redis_host, redis_port),
    }

    try:
        configured_instances = list_active_solidset_instances()
        instances_error = ""
    except Exception as exc:
        configured_instances = []
        instances_error = str(exc)

    instance_checks = []
    for instance in configured_instances:
        base_url = str(instance.get("BaseUrl") or "").strip()
        notification_url = str(instance.get("NotificationUrl") or "").strip()

        def probe_configured_url(url: str, path: str, default_port: int) -> dict:
            if not url:
                return {"ok": False, "error": "url_nao_configurado", "url": url}
            candidates = [url.rstrip("/")]
            parsed = urlparse(url)
            if (
                os.path.exists("/.dockerenv") or os.getenv("RUNNING_IN_DOCKER") == "1"
            ) and (parsed.hostname or "").lower() in {"localhost", "127.0.0.1"}:
                docker_url = (
                    url.replace("localhost", "host.docker.internal")
                    .replace("127.0.0.1", "host.docker.internal")
                    .rstrip("/")
                )
                if docker_url not in candidates:
                    candidates.insert(0, docker_url)
            last_probe = {"ok": False, "error": "sin_candidatos", "url": url}
            for candidate_url in candidates:
                tcp = _probe_tcp(
                    *_extract_host_port_from_url(candidate_url, default_port)
                )
                http = _probe_http(
                    candidate_url,
                    path,
                    verify_tls=settings.NOTIF_API_VERIFY_TLS,
                )
                last_probe = {
                    "ok": bool(tcp.get("ok") and http.get("ok")),
                    "configured_url": url,
                    "effective_url": candidate_url,
                    "tcp": tcp,
                    "http": http,
                }
                if last_probe["ok"]:
                    break
            return last_probe

        instance_checks.append(
            {
                "id": str(instance.get("ID") or ""),
                "code": str(instance.get("Code") or ""),
                "name": str(instance.get("Name") or ""),
                "source_ip": str(instance.get("SourceIP") or ""),
                "base_url": base_url,
                "notification_url": notification_url,
                "solidset": probe_configured_url(base_url, "/RestApi/Heartbeat", 80),
                "notification": (
                    probe_configured_url(notification_url, "/api/Request", 443)
                    if notification_url
                    else {
                        "ok": True,
                        "skipped": True,
                        "error": "NotificationUrl_nao_configurado",
                    }
                ),
                "database": _probe_sql_server_connection(instance),
            }
        )
    checks["solidset_instances"] = {
        "configured": bool(instance_checks),
        "error": instances_error or None,
        "instances": instance_checks,
    }

    checks["sql_server"] = {
        "configured": any(bool(item.get("DataAPI")) for item in configured_instances),
        "instances": [
            {"code": item["code"], "connection": item["database"]}
            for item in instance_checks
        ],
    }

    db_url = os.getenv("DB_URL", "")
    pg_host, pg_port = _extract_host_port_from_url(db_url, 5432)
    checks["postgres_timescaledb"] = {
        "configured": bool(db_url),
        "tcp": _probe_tcp(pg_host, pg_port)
        if db_url
        else {"ok": False, "error": "DB_URL_nao_configurado"},
    }

    all_ok = True
    for service_name, service_data in checks.items():
        if service_name == "solidset_instances":
            if not service_data.get("configured") or service_data.get("error"):
                all_ok = False
            for instance in service_data.get("instances", []):
                if not instance.get("solidset", {}).get("ok", False):
                    all_ok = False
                notification = instance.get("notification", {})
                if not notification.get("ok", False) and not notification.get(
                    "skipped"
                ):
                    all_ok = False
                if not instance.get("database", {}).get("ok", False):
                    all_ok = False
            continue
        if service_name == "sql_server":
            continue
        for probe_name, probe_result in service_data.items():
            if probe_name in {"enabled", "configured", "database"}:
                continue
            if isinstance(probe_result, dict) and not probe_result.get("ok", False):
                all_ok = False

    return {
        "checked_at": datetime.utcnow().isoformat(),
        "all_ok": all_ok,
        "checks": checks,
    }


def _probe_to_text(probe: dict) -> str:
    if probe.get("skipped"):
        return "SKIPPED"
    if probe.get("ok"):
        if "status_code" in probe:
            return f"OK (HTTP {probe.get('status_code')})"
        host = probe.get("host")
        port = probe.get("port")
        if host and port:
            return f"OK ({host}:{port})"
        return "OK"
    error_text = probe.get("error", "error_desconocido")
    return f"FAIL ({error_text})"


def _log_startup_connectivity(report: dict) -> None:
    checks = report.get("checks", {})
    checked_at = report.get("checked_at")
    print("🔌 Comprobador de conectividad inicial")
    if checked_at:
        print(f"   - Timestamp UTC: {checked_at}")

    ollama = checks.get("ollama", {})
    print(f"   - Ollama URL: {settings.OLLAMA_BASE_URL}")
    print(f"     • TCP: {_probe_to_text(ollama.get('tcp', {}))}")
    print(f"     • HTTP /api/tags: {_probe_to_text(ollama.get('http', {}))}")

    qdrant = checks.get("qdrant", {})
    print(f"   - Qdrant URL: {settings.VECTOR_DB_URL}")
    print(f"     • TCP: {_probe_to_text(qdrant.get('tcp', {}))}")
    print(f"     • HTTP /collections: {_probe_to_text(qdrant.get('http', {}))}")

    redis = checks.get("redis", {})
    print(f"   - Redis URL: {settings.REDIS_URL}")
    print(f"     • TCP: {_probe_to_text(redis.get('tcp', {}))}")

    postgres = checks.get("postgres_timescaledb", {})
    db_url = os.getenv("DB_URL", "")
    print(f"   - PostgreSQL/TimescaleDB URL: {db_url or 'DB_URL_no_configurada'}")
    print(f"     • TCP: {_probe_to_text(postgres.get('tcp', {}))}")

    configured = checks.get("solidset_instances", {})
    print("   - Instancias SolidSET configuradas en PostgreSQL:")
    if configured.get("error"):
        print(f"     • ERROR consultando SysSolidSETInstance: {configured['error']}")
    elif not configured.get("instances"):
        print("     • Ninguna instancia activa configurada")
    for instance in configured.get("instances", []):
        print(
            f"     • [{instance.get('code') or '-'}] {instance.get('name') or '-'} "
            f"SourceIP={instance.get('source_ip') or '-'}"
        )
        solidset = instance.get("solidset", {})
        print(
            f"       SolidSET: {solidset.get('configured_url') or instance.get('base_url') or '-'} "
            f"-> {solidset.get('effective_url') or '-'}: {_probe_to_text(solidset)}"
        )
        notification = instance.get("notification", {})
        print(
            f"       Notification: {notification.get('configured_url') or instance.get('notification_url') or '-'} "
            f"-> {notification.get('effective_url') or '-'}: {_probe_to_text(notification)}"
        )
        print(f"       SQL Server: {_probe_to_text(instance.get('database', {}))}")

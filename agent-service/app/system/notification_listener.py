import hashlib
import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime
from time import perf_counter
from urllib.parse import urlparse
from typing import Any, DefaultDict, Dict, List, Optional

import httpx

from app.config import settings
from app.system.learning import SistemaAprendizaje
from app.system.schema import Actividad


class NotificationApiListener:
    """Escucha NotificationManager + ChatController + RestApiController para aprendizaje contextual."""

    def __init__(self) -> None:
        self.base_url = (settings.NOTIF_API_BASE_URL or "").rstrip("/")
        self.chat_base_url = (settings.SOLIDSET_CHAT_BASE_URL or self.base_url).rstrip("/")
        self.rest_base_url = (settings.SOLIDSET_RESTAPI_BASE_URL or self.base_url).rstrip("/")
        self.base_urls = self._candidate_base_urls(self.base_url)
        self.chat_base_urls = self._candidate_base_urls(self.chat_base_url)
        self.rest_base_urls = self._candidate_base_urls(self.rest_base_url)
        any_api_configured = any([
            bool(self.base_url),
            bool(self.chat_base_url),
            bool(self.rest_base_url),
        ])
        self.enabled = settings.NOTIF_API_ENABLED and any_api_configured
        self.timeout_seconds = max(5, settings.NOTIF_API_TIMEOUT_SECONDS)
        self.verify_tls = settings.NOTIF_API_VERIFY_TLS
        self.audit_log_enabled = settings.NOTIF_AUDIT_LOG_ENABLED
        self.poll_seconds = max(10, settings.NOTIF_API_POLL_SECONDS)
        self.access_key = settings.NOTIF_API_ACCESS_KEY
        self.sistema = SistemaAprendizaje()
        self.seen_fingerprints: List[str] = []
        self.max_seen = 5000
        self.metrics_lock = threading.Lock()
        self.max_cycle_history = 300
        self.cycle_history: List[Dict[str, Any]] = []
        self.max_recent_captured_messages = 200
        self.recent_captured_messages: List[Dict[str, Any]] = []
        self.max_recent_auto_reply_candidates = 200
        self.recent_auto_reply_candidates: List[Dict[str, Any]] = []
        self.api_metrics: Dict[str, Any] = {
            "calls_total": 0,
            "calls_ok": 0,
            "calls_error": 0,
            "http_4xx": 0,
            "http_5xx": 0,
            "rate_limited_429": 0,
            "timeouts": 0,
            "network_errors": 0,
            "latency_ms_total": 0.0,
            "latency_ms_max": 0.0,
            "payload_items_total": 0,
            "last_error": None,
            "last_error_at": None,
            "by_endpoint": {},
        }

        self.notification_endpoints = [
            "/api/Request",
            "/api/Admin/GetActiveSessionsInfo",
            "/api/Admin/GetConnected",
            "/api/Admin/Stats",
            "/api/Admin/Info",
            "/api/Admin/Configurations",
        ]
        self.user_endpoints = [
            "/api/User/GetAllChannels",
            "/api/User/GetAllChannelsRaw",
        ]
        self.chat_meta_endpoints = [
            "/Chat/GetNotifications",
            "/Chat/GetUnreadCount",
            "/Chat/GetUnreadCountGrouped",
        ]
        self.chat_reaction_endpoints = [
            "/chat/get-reaction-users",
            "/chat/get-reactions-user",
        ]
        # Endpoints mutables: se documentan pero no se invocan en modo escucha.
        self.chat_mutating_endpoints = [
            "/chat/update-reaction",
            "/SendMessageAsync",
        ]
        self.rest_endpoints = [
            "/RestApi/Heartbeat",
        ]
        self.channel_message_endpoints = [
            "/Chat/ChatMessagesV2",
            "/Chat/ChatMessagesV2Form",
            "/Chat/ChatMessages",
            "/Chat/ChatMessagesForm",
        ]
        self.uuid_regex = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")

    def _candidate_base_urls(self, base_url: str) -> List[str]:
        if not base_url:
            return []

        urls = [base_url.rstrip("/")]
        parsed = urlparse(base_url)
        hostname = (parsed.hostname or "").lower()

        # Solo agrega host.docker.internal cuando realmente corre dentro de contenedor.
        running_in_container = os.path.exists("/.dockerenv") or os.getenv("RUNNING_IN_DOCKER") == "1"
        if running_in_container and hostname in {"localhost", "127.0.0.1"}:
            alt = base_url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
            alt = alt.rstrip("/")
            if alt not in urls:
                urls.append(alt)

        return urls

    def is_enabled(self) -> bool:
        return self.enabled

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.access_key:
            headers["X-Access-Key"] = self.access_key
            headers["Authorization"] = f"Bearer {self.access_key}"
        return headers

    def _join_url(self, base_url: str, endpoint: str) -> str:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{base_url}{endpoint}"

    def _is_uuid(self, value: Any) -> bool:
        return isinstance(value, str) and bool(self.uuid_regex.match(value.strip()))

    def _extract_channel_ids(self, payload: Any) -> List[str]:
        found: List[str] = []

        def _visit(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    lower_key = str(key).lower()
                    if any(hint in lower_key for hint in ["workroom", "channel", "idworkroom", "idchannel"]):
                        if self._is_uuid(value):
                            found.append(value)
                    # El esquema Channel usa la propiedad ID para el UUID del canal.
                    if lower_key == "id":
                        if self._is_uuid(value):
                            found.append(value)
                    _visit(value)
            elif isinstance(node, list):
                for item in node:
                    _visit(item)

        _visit(payload)
        unique = []
        seen = set()
        for item in found:
            norm = item.lower()
            if norm in seen:
                continue
            seen.add(norm)
            unique.append(item)
        return unique

    def _login_payload(self) -> Optional[Dict[str, Any]]:
        if not settings.SOLIDSET_LOGIN_USERNAME and not settings.SOLIDSET_LOGIN_HASHPASS:
            return None

        payload: Dict[str, Any] = {
            "username": settings.SOLIDSET_LOGIN_USERNAME or None,
            "pass": settings.SOLIDSET_LOGIN_PASSWORD or None,
            "hashPass": settings.SOLIDSET_LOGIN_HASHPASS or None,
            "accessKey": True,
            "generateAccessKey": False,
        }

        if settings.SOLIDSET_LOGIN_RESOURCE_ID and self._is_uuid(settings.SOLIDSET_LOGIN_RESOURCE_ID):
            payload["resource"] = settings.SOLIDSET_LOGIN_RESOURCE_ID

        return payload

    def _legacy_login_headers(self) -> Dict[str, str]:
        headers = self._headers()
        headers["X-Requested-With"] = "XMLHttpRequest"

        cookie_parts = []
        if settings.SOLIDSET_WORKSTATION_ID:
            cookie_parts.append(f"IDWorkstation={settings.SOLIDSET_WORKSTATION_ID}")
        if settings.SOLIDSET_WORKSTATION_NAME:
            cookie_parts.append(f"NameWorkstation={settings.SOLIDSET_WORKSTATION_NAME}")
        if settings.SOLIDSET_CLIENT_VERSION:
            cookie_parts.append(f"ClientVersion={settings.SOLIDSET_CLIENT_VERSION}")
        if settings.SOLIDSET_APPLICATION_ID:
            cookie_parts.append(f"IDApplication={settings.SOLIDSET_APPLICATION_ID}")

        if cookie_parts:
            headers["Cookie"] = ";".join(cookie_parts)

        return headers

    def _legacy_login_form_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "UserName": settings.SOLIDSET_LOGIN_USERNAME or "",
            "Password": settings.SOLIDSET_LOGIN_PASSWORD or "",
        }
        if settings.SOLIDSET_TIMEZONE_ID:
            payload["TimezoneID"] = settings.SOLIDSET_TIMEZONE_ID
        return payload

    async def _ensure_login(self, client: httpx.AsyncClient) -> bool:
        payload = self._login_payload()
        if payload is None:
            return bool(self.access_key)

        login_base_urls: List[str] = []
        for candidate in self.base_urls + self.chat_base_urls + self.rest_base_urls:
            if candidate and candidate not in login_base_urls:
                login_base_urls.append(candidate)

        for base_url in login_base_urls:
            for endpoint in ["/api/User/LoginRaw", "/api/User/Login"]:
                url = self._join_url(base_url, endpoint)
                try:
                    resp = await client.post(url, json=payload, headers=self._headers())
                    if resp.status_code >= 400:
                        continue

                    content_type = (resp.headers.get("content-type") or "").lower()
                    if "json" in content_type:
                        data = resp.json()
                        if isinstance(data, dict):
                            new_key = data.get("accessKey") or data.get("AccessKey")
                            if isinstance(new_key, str) and new_key.strip():
                                self.access_key = new_key.strip()
                    return True
                except Exception:
                    continue

            # Fallback legado para instalaciones que solo exponen /User/LoginJson
            # y requieren body x-www-form-urlencoded con cabeceras tipo navegador.
            legacy_url = self._join_url(base_url, "/User/LoginJson")
            try:
                legacy_resp = await client.post(
                    legacy_url,
                    data=self._legacy_login_form_payload(),
                    headers=self._legacy_login_headers(),
                )
                if legacy_resp.status_code < 400:
                    return True
            except Exception:
                continue

        return bool(self.access_key)

    def _is_message_like_payload(self, node: Any) -> bool:
        if not isinstance(node, dict):
            return False

        has_message = any(
            isinstance(node.get(key), str) and node.get(key).strip()
            for key in ["RawMessage", "Message", "Text"]
        )
        has_context = any(
            node.get(key) is not None
            for key in ["IDChat", "IDChat2", "IDWorkRoom", "IDChannel", "ChannelName", "SenderFullName", "IDSenderResource"]
        )
        return has_message and has_context

    def _extract_message_entries(self, source: str, endpoint: str, payload: Any, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        extracted: List[Dict[str, Any]] = []

        def _visit(node: Any, inherited_channel_id: Optional[str]) -> None:
            if isinstance(node, dict):
                effective_channel_id = (
                    node.get("IDWorkRoom")
                    or node.get("IDChannel")
                    or node.get("BookMarkedIDChannel")
                    or inherited_channel_id
                )
                if self._is_message_like_payload(node):
                    extracted.append({
                        "source": source,
                        "endpoint": endpoint,
                        "channel_id": effective_channel_id,
                        "data": node,
                    })
                for value in node.values():
                    _visit(value, effective_channel_id)
            elif isinstance(node, list):
                for item in node:
                    _visit(item, inherited_channel_id)

        _visit(payload, channel_id)
        return extracted

    def _normalize_entries(self, source: str, endpoint: str, payload: Any, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        message_entries = self._extract_message_entries(source, endpoint, payload, channel_id=channel_id)
        if message_entries:
            return message_entries

        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict):
            entries = [payload]
        else:
            entries = [{"value": payload}]

        normalized: List[Dict[str, Any]] = []
        for item in entries:
            normalized.append({
                "source": source,
                "endpoint": endpoint,
                "channel_id": channel_id,
                "data": item,
            })
        return normalized

    def _fingerprint(self, entry: Dict[str, Any]) -> str:
        raw = json.dumps(entry, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _payload_item_count(self, payload: Any) -> int:
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            return 1
        if payload is None:
            return 0
        return 1

    def _audit_log(self, message: str) -> None:
        if self.audit_log_enabled:
            print(message)

    def _trace_captured_message(self, entry: Dict[str, Any], status: str) -> None:
        if not settings.NOTIF_MESSAGE_TRACE_ENABLED:
            return

        data = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(data, dict):
            return

        raw_message = data.get("RawMessage") or data.get("Message") or data.get("Text")
        if not isinstance(raw_message, str) or not raw_message.strip():
            return

        sender_name = (
            data.get("SenderFullName")
            or data.get("DisplayName")
            or data.get("SenderName")
            or data.get("IDSenderResource")
            or "desconocido"
        )
        channel_name = (
            data.get("ChannelName")
            or data.get("OriginChannelName")
            or entry.get("channel_id")
            or data.get("IDWorkRoom")
            or "sin_canal"
        )
        channel_id = data.get("IDWorkRoom") or data.get("IDChannel") or entry.get("channel_id") or "-"
        chat_id = data.get("IDChat") or data.get("IDChat2") or "-"
        is_public = str(data.get("IsPublic") or "").lower() in {"1", "true"}
        scope = "canal" if is_public else "chat"

        compact = re.sub(r"\s+", " ", raw_message).strip()
        max_len = max(40, settings.NOTIF_MESSAGE_TRACE_MAX_LEN)
        if len(compact) > max_len:
            compact = compact[: max_len - 3] + "..."

        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "status": status,
            "scope": scope,
            "channel": str(channel_name),
            "channel_id": str(channel_id),
            "chat_id": str(chat_id),
            "sender": str(sender_name),
            "message": compact,
        }
        with self.metrics_lock:
            self.recent_captured_messages.append(event)
            if len(self.recent_captured_messages) > self.max_recent_captured_messages:
                self.recent_captured_messages = self.recent_captured_messages[-self.max_recent_captured_messages :]

        print(
            f"📨 NOTIF_CAPTURE [{status}] scope={scope} channel='{channel_name}' "
            f"channel_id={channel_id} chat_id={chat_id} sender='{sender_name}' msg='{compact}'"
        )

    def get_recent_captured_messages(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self.metrics_lock:
            effective_limit = max(1, min(limit, self.max_recent_captured_messages))
            return list(self.recent_captured_messages[-effective_limit:])

    def _build_auto_reply_candidate(self, entry: Dict[str, Any], fingerprint: str) -> Optional[Dict[str, Any]]:
        data = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(data, dict):
            return None

        raw_message = data.get("RawMessage") or data.get("Message") or data.get("Text")
        if not isinstance(raw_message, str) or not raw_message.strip():
            return None

        channel_id = (
            data.get("IDWorkRoom")
            or data.get("IDChannel")
            or data.get("BookMarkedIDChannel")
            or entry.get("channel_id")
        )
        if not isinstance(channel_id, str) or not channel_id.strip():
            return None

        sender_resource = data.get("IDSenderResource")
        sender_name = (
            data.get("SenderFullName")
            or data.get("DisplayName")
            or data.get("SenderName")
            or sender_resource
            or "desconocido"
        )
        is_public = str(data.get("IsPublic") or "").lower() in {"1", "true"}
        chat_id = data.get("IDChat") or data.get("IDChat2")

        candidate = {
            "fingerprint": fingerprint,
            "timestamp": datetime.utcnow().isoformat(),
            "source": entry.get("source", "notification_api"),
            "endpoint": entry.get("endpoint", ""),
            "channel_id": channel_id.strip(),
            "channel_name": data.get("ChannelName") or data.get("OriginChannelName") or channel_id.strip(),
            "sender_resource": str(sender_resource or "").strip(),
            "sender_name": str(sender_name),
            "chat_id": chat_id,
            "is_public": is_public,
            "scope": "canal" if is_public else "chat",
            "message": raw_message.strip(),
            "payload": data,
        }

        with self.metrics_lock:
            self.recent_auto_reply_candidates.append(candidate)
            if len(self.recent_auto_reply_candidates) > self.max_recent_auto_reply_candidates:
                self.recent_auto_reply_candidates = self.recent_auto_reply_candidates[-self.max_recent_auto_reply_candidates :]

        return candidate

    def get_recent_auto_reply_candidates(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self.metrics_lock:
            effective_limit = max(1, min(limit, self.max_recent_auto_reply_candidates))
            return list(self.recent_auto_reply_candidates[-effective_limit:])

    def _record_api_metric(
        self,
        source: str,
        endpoint: str,
        method: str,
        elapsed_ms: Optional[float] = None,
        status_code: Optional[int] = None,
        payload_items: Optional[int] = None,
        error: Optional[Exception] = None,
    ) -> None:
        key = f"{method.upper()} {source}:{endpoint}"
        with self.metrics_lock:
            self.api_metrics["calls_total"] += 1
            if elapsed_ms is not None:
                self.api_metrics["latency_ms_total"] += float(elapsed_ms)
                self.api_metrics["latency_ms_max"] = max(self.api_metrics["latency_ms_max"], float(elapsed_ms))
            if payload_items is not None:
                self.api_metrics["payload_items_total"] += max(0, int(payload_items))

            endpoint_stats = self.api_metrics["by_endpoint"].setdefault(
                key,
                {
                    "calls": 0,
                    "ok": 0,
                    "errors": 0,
                    "http_4xx": 0,
                    "http_5xx": 0,
                    "rate_limited_429": 0,
                    "timeouts": 0,
                    "network_errors": 0,
                    "latency_ms_total": 0.0,
                    "latency_ms_max": 0.0,
                    "payload_items_total": 0,
                    "last_status_code": None,
                    "last_error": None,
                    "last_error_at": None,
                },
            )
            endpoint_stats["calls"] += 1
            if elapsed_ms is not None:
                endpoint_stats["latency_ms_total"] += float(elapsed_ms)
                endpoint_stats["latency_ms_max"] = max(endpoint_stats["latency_ms_max"], float(elapsed_ms))
            if payload_items is not None:
                endpoint_stats["payload_items_total"] += max(0, int(payload_items))

            if status_code is not None and status_code < 400:
                self.api_metrics["calls_ok"] += 1
                endpoint_stats["ok"] += 1
                endpoint_stats["last_status_code"] = int(status_code)
                return

            self.api_metrics["calls_error"] += 1
            endpoint_stats["errors"] += 1

            if status_code is not None:
                status = int(status_code)
                endpoint_stats["last_status_code"] = status
                if 400 <= status < 500:
                    self.api_metrics["http_4xx"] += 1
                    endpoint_stats["http_4xx"] += 1
                if status >= 500:
                    self.api_metrics["http_5xx"] += 1
                    endpoint_stats["http_5xx"] += 1
                if status == 429:
                    self.api_metrics["rate_limited_429"] += 1
                    endpoint_stats["rate_limited_429"] += 1

            if error is not None:
                err_text = str(error)
                self.api_metrics["last_error"] = err_text
                self.api_metrics["last_error_at"] = datetime.utcnow().isoformat()
                endpoint_stats["last_error"] = err_text
                endpoint_stats["last_error_at"] = self.api_metrics["last_error_at"]
                if isinstance(error, httpx.TimeoutException):
                    self.api_metrics["timeouts"] += 1
                    endpoint_stats["timeouts"] += 1
                else:
                    self.api_metrics["network_errors"] += 1
                    endpoint_stats["network_errors"] += 1

    def _record_cycle_summary(self, summary: Dict[str, Any]) -> None:
        with self.metrics_lock:
            self.cycle_history.append(summary)
            if len(self.cycle_history) > self.max_cycle_history:
                self.cycle_history = self.cycle_history[-self.max_cycle_history :]

    def get_api_metrics_snapshot(self) -> Dict[str, Any]:
        with self.metrics_lock:
            raw = dict(self.api_metrics)
            by_endpoint = raw.get("by_endpoint", {})
            calls_total = max(1, int(raw.get("calls_total", 0)))
            latency_total = float(raw.get("latency_ms_total", 0.0))

            endpoint_summary = []
            for key, value in by_endpoint.items():
                calls = max(1, int(value.get("calls", 0)))
                endpoint_summary.append(
                    {
                        "endpoint": key,
                        "calls": int(value.get("calls", 0)),
                        "ok": int(value.get("ok", 0)),
                        "errors": int(value.get("errors", 0)),
                        "rate_limited_429": int(value.get("rate_limited_429", 0)),
                        "timeouts": int(value.get("timeouts", 0)),
                        "avg_latency_ms": round(float(value.get("latency_ms_total", 0.0)) / calls, 2),
                        "max_latency_ms": round(float(value.get("latency_ms_max", 0.0)), 2),
                        "payload_items_total": int(value.get("payload_items_total", 0)),
                        "last_status_code": value.get("last_status_code"),
                        "last_error": value.get("last_error"),
                    }
                )

            endpoint_summary.sort(key=lambda item: item["errors"], reverse=True)
            return {
                "calls_total": int(raw.get("calls_total", 0)),
                "calls_ok": int(raw.get("calls_ok", 0)),
                "calls_error": int(raw.get("calls_error", 0)),
                "http_4xx": int(raw.get("http_4xx", 0)),
                "http_5xx": int(raw.get("http_5xx", 0)),
                "rate_limited_429": int(raw.get("rate_limited_429", 0)),
                "timeouts": int(raw.get("timeouts", 0)),
                "network_errors": int(raw.get("network_errors", 0)),
                "avg_latency_ms": round(latency_total / calls_total, 2),
                "max_latency_ms": round(float(raw.get("latency_ms_max", 0.0)), 2),
                "payload_items_total": int(raw.get("payload_items_total", 0)),
                "last_error": raw.get("last_error"),
                "last_error_at": raw.get("last_error_at"),
                "by_endpoint": endpoint_summary,
            }

    def get_learning_metrics_snapshot(self) -> Dict[str, Any]:
        with self.metrics_lock:
            history = list(self.cycle_history)

        total = len(history)
        if total == 0:
            return {
                "cycles": 0,
                "success_ratio": None,
                "avg_learned_per_cycle": 0.0,
                "avg_errors_per_cycle": 0.0,
                "avg_cycle_ms": 0.0,
                "learning_velocity_per_minute": 0.0,
                "recent_trend": "sin_datos",
            }

        successful = sum(1 for item in history if int(item.get("errors", 0)) == 0)
        learned_sum = sum(int(item.get("learned", 0)) for item in history)
        errors_sum = sum(int(item.get("errors", 0)) for item in history)
        cycle_ms_sum = sum(float(item.get("cycle_elapsed_ms", 0.0)) for item in history)
        minutes = max(1e-6, cycle_ms_sum / 60000.0)

        recent_window = history[-10:]
        previous_window = history[-20:-10]
        recent_avg = sum(int(item.get("learned", 0)) for item in recent_window) / max(1, len(recent_window))
        previous_avg = sum(int(item.get("learned", 0)) for item in previous_window) / max(1, len(previous_window))

        trend = "estable"
        if recent_avg > previous_avg * 1.1:
            trend = "mejorando"
        elif recent_avg < previous_avg * 0.9:
            trend = "degradando"

        return {
            "cycles": total,
            "success_ratio": round(successful / total, 4),
            "avg_learned_per_cycle": round(learned_sum / total, 3),
            "avg_errors_per_cycle": round(errors_sum / total, 3),
            "avg_cycle_ms": round(cycle_ms_sum / total, 2),
            "learning_velocity_per_minute": round(learned_sum / minutes, 3),
            "recent_trend": trend,
            "last_cycle": history[-1],
        }

    def _remember_fingerprint(self, fingerprint: str) -> None:
        self.seen_fingerprints.append(fingerprint)
        if len(self.seen_fingerprints) > self.max_seen:
            self.seen_fingerprints = self.seen_fingerprints[-self.max_seen :]

    def _learn_entry(self, entry: Dict[str, Any], fingerprint: str) -> bool:
        source = entry.get("source", "notification_api")
        endpoint = entry.get("endpoint", "")
        channel_id = entry.get("channel_id")
        data = entry.get("data")

        payload = data if isinstance(data, dict) else {}
        payload_channel_id = _safe_str = None
        if payload:
            payload_channel_id = payload.get("IDWorkRoom") or payload.get("IDChannel") or payload.get("BookMarkedIDChannel")
            if isinstance(payload_channel_id, str) and payload_channel_id.strip():
                channel_id = channel_id or payload_channel_id.strip()

        sender_resource = payload.get("IDSenderResource") if payload else None
        sender_name = payload.get("SenderFullName") if payload else None
        raw_message = payload.get("RawMessage") if payload else None
        channel_name = payload.get("ChannelName") or payload.get("OriginChannelName") if payload else None
        channel_kind = payload.get("ChannelKind") or payload.get("OriginChannelKind") if payload else None
        is_public = payload.get("IsPublic") if payload else None
        resource_table = payload.get("ResourceTable") if payload else None
        destiny = payload.get("Destiny") if payload else None

        if payload and any(key in payload for key in ["IDSenderResource", "RawMessage", "IDWorkRoom", "ChannelName"]):
            source = "solidset_restapi_chat"

        short_data = json.dumps(data, ensure_ascii=False, default=str)
        if len(short_data) > 600:
            short_data = short_data[:600] + "..."

        if source == "solidset_restapi_chat" and raw_message:
            scope = "canal_publico" if str(is_public) in {"1", "True", "true"} else "chat_privado"
            summary = (
                f"Chat REST API ({scope})"
                f" | Canal: {channel_name or channel_id or 'sin_canal'}"
                f" | Remitente: {sender_name or sender_resource or 'desconocido'}"
                f" | Mensaje: {str(raw_message)[:300]}"
            )
        else:
            summary = f"{source} {endpoint}: {short_data}"

        actividad = Actividad(
            id=f"notif_{fingerprint[:28]}",
            recurso_humano_id=str(sender_resource or "sistema"),
            canal_id=channel_id or "solidset_communicator_notifications",
            tipo=source,
            descripcion=summary,
            timestamp=datetime.utcnow(),
            metadatos={
                "source_table": "NotificationAPI",
                "source": source,
                "endpoint": endpoint,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "channel_kind": channel_kind,
                "sender_resource": sender_resource,
                "sender_name": sender_name,
                "is_public": is_public,
                "resource_table": resource_table,
                "destiny": destiny,
                "fingerprint": fingerprint,
                "captured_at": datetime.utcnow().isoformat(),
                "payload": data,
            },
        )
        return self.sistema.aprender_actividad(actividad)

    async def _pull_endpoint(
        self,
        client: httpx.AsyncClient,
        base_urls: List[str],
        endpoint: str,
        source: str,
        method: str = "GET",
        json_body: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, Any]] = None,
        channel_id: Optional[str] = None,
    ) -> Dict[str, int]:
        learned = 0
        skipped = 0
        errors = 0
        auto_reply_candidates: List[Dict[str, Any]] = []
        last_exc: Optional[Exception] = None
        for base_url in base_urls:
            url = self._join_url(base_url, endpoint)
            started_at = perf_counter()
            try:
                if method == "POST":
                    resp = await client.post(
                        url,
                        headers=self._headers(),
                        json=json_body or {},
                        params=query_params,
                    )
                else:
                    resp = await client.get(url, headers=self._headers(), params=query_params)

                elapsed_ms = (perf_counter() - started_at) * 1000
                if resp.status_code >= 400:
                    errors += 1
                    self._record_api_metric(
                        source=source,
                        endpoint=endpoint,
                        method=method,
                        elapsed_ms=elapsed_ms,
                        status_code=resp.status_code,
                    )
                    self._audit_log(
                        f"📡 AUDIT endpoint={source} method={method} status={resp.status_code} "
                        f"elapsed_ms={elapsed_ms:.1f} channel={channel_id or '-'}"
                    )
                    continue

                content_type = (resp.headers.get("content-type") or "").lower()
                if "json" in content_type:
                    payload = resp.json()
                else:
                    payload = resp.text

                payload_items = self._payload_item_count(payload)
                self._record_api_metric(
                    source=source,
                    endpoint=endpoint,
                    method=method,
                    elapsed_ms=elapsed_ms,
                    status_code=resp.status_code,
                    payload_items=payload_items,
                )

                entries = self._normalize_entries(source, endpoint, payload, channel_id=channel_id)
                for entry in entries:
                    fp = self._fingerprint(entry)
                    if fp in self.seen_fingerprints:
                        skipped += 1
                        self._trace_captured_message(entry, status="duplicate")
                        continue
                    if self._learn_entry(entry, fp):
                        learned += 1
                        self._remember_fingerprint(fp)
                        self._trace_captured_message(entry, status="learned")
                        candidate = self._build_auto_reply_candidate(entry, fingerprint=fp)
                        if candidate is not None:
                            auto_reply_candidates.append(candidate)
                    else:
                        errors += 1
                        self._trace_captured_message(entry, status="learn_error")

                self._audit_log(
                    f"📡 AUDIT endpoint={source} method={method} status={resp.status_code} "
                    f"elapsed_ms={elapsed_ms:.1f} payload_items={payload_items} "
                    f"learned={learned} skipped={skipped} errors={errors} channel={channel_id or '-'}"
                )

                return {
                    "learned": learned,
                    "skipped": skipped,
                    "errors": errors,
                    "payload": payload,
                    "auto_reply_candidates": auto_reply_candidates,
                }
            except Exception as exc:
                last_exc = exc
                self._record_api_metric(
                    source=source,
                    endpoint=endpoint,
                    method=method,
                    elapsed_ms=(perf_counter() - started_at) * 1000,
                    error=exc,
                )
                continue

        errors += 1
        if last_exc:
            print(f"⚠️ Error consultando {source} {endpoint}: {last_exc}")

        return {
            "learned": learned,
            "skipped": skipped,
            "errors": errors,
            "auto_reply_candidates": auto_reply_candidates,
        }

    def _to_int_chat_id(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed >= 0 else None
        return None

    def _extract_chat_targets(self, payload: Any) -> List[Dict[str, Any]]:
        """Extrae IDChat y posibles IDUser UUID desde payloads de mensajes."""
        users_by_chat: DefaultDict[int, set] = defaultdict(set)

        def _visit(node: Any) -> None:
            if isinstance(node, dict):
                chat_id = self._to_int_chat_id(
                    node.get("IDChat")
                    or node.get("idChat")
                    or node.get("IdChat")
                    or node.get("IDChat2")
                )

                user_candidates = [
                    node.get("IDUser"),
                    node.get("idUser"),
                    node.get("IdUser"),
                    node.get("IDSenderUser"),
                    node.get("SenderUserId"),
                    node.get("IdUserReaction"),
                    node.get("IDUserReaction"),
                    node.get("QuestionAuthor"),
                    node.get("OpSender"),
                ]
                user_uuids = [
                    candidate.strip()
                    for candidate in user_candidates
                    if isinstance(candidate, str) and self._is_uuid(candidate)
                ]

                if chat_id is not None:
                    if user_uuids:
                        for user_uuid in user_uuids:
                            users_by_chat[chat_id].add(user_uuid)
                    else:
                        users_by_chat[chat_id]  # asegura clave

                for value in node.values():
                    _visit(value)
            elif isinstance(node, list):
                for item in node:
                    _visit(item)

        _visit(payload)

        targets: List[Dict[str, Any]] = []
        for chat_id in sorted(users_by_chat.keys(), reverse=True):
            targets.append(
                {
                    "id_chat": chat_id,
                    "id_users": sorted(users_by_chat[chat_id]),
                }
            )
        return targets

    def _chat_message_payload_variants(self, channel_id: str) -> List[Dict[str, Any]]:
        page_size = max(5, min(settings.SOLIDSET_CHAT_PAGE_SIZE, 100))
        return [
            {"IDWorkRoom": channel_id, "Page": 1, "PageSize": page_size},
            {"idWorkRoom": channel_id, "page": 1, "pageSize": page_size},
            {"IDWorkRoom": channel_id, "Skip": 0, "Take": page_size},
            {"idWorkRoom": channel_id, "skip": 0, "take": page_size},
        ]

    async def _pull_channel_messages(self, client: httpx.AsyncClient, channel_id: str) -> Dict[str, Any]:
        totals: Dict[str, Any] = {
            "learned": 0,
            "skipped": 0,
            "errors": 0,
            "chat_targets": [],
            "auto_reply_candidates": [],
        }
        for endpoint in self.channel_message_endpoints:
            for payload in self._chat_message_payload_variants(channel_id):
                result = await self._pull_endpoint(
                    client=client,
                    base_urls=self.chat_base_urls,
                    endpoint=endpoint,
                    source="chat_controller_messages",
                    method="POST",
                    json_body=payload,
                    channel_id=channel_id,
                )
                totals["learned"] += result["learned"]
                totals["skipped"] += result["skipped"]
                totals["errors"] += result["errors"]
                totals["auto_reply_candidates"].extend(result.get("auto_reply_candidates") or [])
                response_payload = result.get("payload")
                if response_payload is not None:
                    extracted = self._extract_chat_targets(response_payload)
                    if extracted:
                        totals["chat_targets"] = extracted
                        self._audit_log(
                            f"🧪 AUDIT channel={channel_id} endpoint={endpoint} "
                            f"chat_targets_detected={len(extracted)} sample_chat_ids="
                            f"{[x.get('id_chat') for x in extracted[:5]]}"
                        )
                if result["learned"] > 0 or result["skipped"] > 0:
                    return totals
        return totals

    async def _pull_channel_reactions(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
        chat_targets: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        totals = {"learned": 0, "skipped": 0, "errors": 0}
        if not chat_targets:
            return totals

        max_chats = 20
        for target in chat_targets[:max_chats]:
            id_chat = target.get("id_chat")
            if not isinstance(id_chat, int):
                continue

            reaction_users_result = await self._pull_endpoint(
                client=client,
                base_urls=self.chat_base_urls,
                endpoint="/chat/get-reaction-users",
                source="chat_controller_reactions",
                method="GET",
                query_params={"IDChat": id_chat},
                channel_id=channel_id,
            )
            totals["learned"] += reaction_users_result["learned"]
            totals["skipped"] += reaction_users_result["skipped"]
            totals["errors"] += reaction_users_result["errors"]
            self._audit_log(
                f"🎯 AUDIT reactions channel={channel_id} id_chat={id_chat} endpoint=get-reaction-users "
                f"learned={reaction_users_result['learned']} skipped={reaction_users_result['skipped']} "
                f"errors={reaction_users_result['errors']}"
            )

            user_ids = [u for u in (target.get("id_users") or []) if isinstance(u, str) and self._is_uuid(u)]
            for user_id in user_ids[:5]:
                result = await self._pull_endpoint(
                    client=client,
                    base_urls=self.chat_base_urls,
                    endpoint="/chat/get-reactions-user",
                    source="chat_controller_reactions",
                    method="GET",
                    query_params={"IDChat": id_chat, "IDUser": user_id},
                    channel_id=channel_id,
                )
                totals["learned"] += result["learned"]
                totals["skipped"] += result["skipped"]
                totals["errors"] += result["errors"]
                self._audit_log(
                    f"🎯 AUDIT reactions channel={channel_id} id_chat={id_chat} endpoint=get-reactions-user "
                    f"id_user={user_id} learned={result['learned']} skipped={result['skipped']} "
                    f"errors={result['errors']}"
                )

        return totals

    async def pull_once(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "learned": 0, "skipped": 0, "errors": 0}

        cycle_started_at = perf_counter()
        self._audit_log("🔎 AUDIT cycle=start source=notification_listener")

        learned = 0
        skipped = 0
        errors = 0
        channels_detected = 0
        chat_channel_pulls = 0
        reaction_channel_pulls = 0
        auto_reply_candidates: List[Dict[str, Any]] = []

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            verify=self.verify_tls,
            follow_redirects=True,
        ) as client:
            await self._ensure_login(client)

            for endpoint in self.notification_endpoints:
                result = await self._pull_endpoint(
                    client=client,
                    base_urls=self.base_urls,
                    endpoint=endpoint,
                    source="notification_api",
                )
                learned += result["learned"]
                skipped += result["skipped"]
                errors += result["errors"]
                auto_reply_candidates.extend(result.get("auto_reply_candidates") or [])

            channel_ids: List[str] = []
            for endpoint in self.user_endpoints:
                result = await self._pull_endpoint(
                    client=client,
                    base_urls=self.base_urls,
                    endpoint=endpoint,
                    source="notification_user_api",
                )
                learned += result["learned"]
                skipped += result["skipped"]
                errors += result["errors"]
                auto_reply_candidates.extend(result.get("auto_reply_candidates") or [])

                payload = result.get("payload")
                if payload is not None:
                    channel_ids.extend(self._extract_channel_ids(payload))
                else:
                    self._audit_log(f"⚠️ AUDIT sin payload de canales en {endpoint}")

            for endpoint in self.chat_meta_endpoints:
                result = await self._pull_endpoint(
                    client=client,
                    base_urls=self.chat_base_urls,
                    endpoint=endpoint,
                    source="chat_controller_meta",
                )
                learned += result["learned"]
                skipped += result["skipped"]
                errors += result["errors"]
                auto_reply_candidates.extend(result.get("auto_reply_candidates") or [])

            for endpoint in self.rest_endpoints:
                result = await self._pull_endpoint(
                    client=client,
                    base_urls=self.rest_base_urls,
                    endpoint=endpoint,
                    source="restapi_controller",
                )
                learned += result["learned"]
                skipped += result["skipped"]
                errors += result["errors"]
                auto_reply_candidates.extend(result.get("auto_reply_candidates") or [])

            if settings.SOLIDSET_LISTEN_CHAT_MESSAGES:
                unique_channels = []
                seen = set()
                for cid in channel_ids:
                    key = cid.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    unique_channels.append(cid)
                channels_detected = len(unique_channels)
                self._audit_log(
                    f"🔎 AUDIT channels detected={channels_detected} "
                    f"selected={min(channels_detected, max(1, settings.SOLIDSET_CHAT_MAX_CHANNELS))}"
                )

                max_channels = max(1, settings.SOLIDSET_CHAT_MAX_CHANNELS)
                for channel_id in unique_channels[:max_channels]:
                    msg_result = await self._pull_channel_messages(client, channel_id)
                    learned += msg_result["learned"]
                    skipped += msg_result["skipped"]
                    errors += msg_result["errors"]
                    auto_reply_candidates.extend(msg_result.get("auto_reply_candidates") or [])
                    chat_channel_pulls += 1

                    chat_targets = msg_result.get("chat_targets") or []
                    reaction_result = await self._pull_channel_reactions(client, channel_id, chat_targets)
                    learned += reaction_result["learned"]
                    skipped += reaction_result["skipped"]
                    errors += reaction_result["errors"]
                    reaction_channel_pulls += 1

        cycle_elapsed_ms = (perf_counter() - cycle_started_at) * 1000
        self._audit_log(
            f"✅ AUDIT cycle=end elapsed_ms={cycle_elapsed_ms:.1f} learned={learned} "
            f"skipped={skipped} errors={errors} channels_detected={channels_detected} "
            f"chat_channel_pulls={chat_channel_pulls} reaction_channel_pulls={reaction_channel_pulls}"
        )

        cycle_summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "learned": learned,
            "skipped": skipped,
            "errors": errors,
            "channels_detected": channels_detected,
            "chat_channel_pulls": chat_channel_pulls,
            "reaction_channel_pulls": reaction_channel_pulls,
            "cycle_elapsed_ms": round(cycle_elapsed_ms, 2),
        }
        self._record_cycle_summary(cycle_summary)

        return {
            "enabled": True,
            "learned": learned,
            "skipped": skipped,
            "errors": errors,
            "channels_detected": channels_detected,
            "chat_channel_pulls": chat_channel_pulls,
            "reaction_channel_pulls": reaction_channel_pulls,
            "timestamp": cycle_summary["timestamp"],
            "cycle_elapsed_ms": cycle_summary["cycle_elapsed_ms"],
            "auto_reply_candidates": auto_reply_candidates,
        }

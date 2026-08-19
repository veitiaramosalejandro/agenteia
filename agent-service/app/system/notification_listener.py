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
from app.connectors.db_client import get_active_agent_identity_for_resource
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
        # Hard-disable operativo: cuando el background está apagado, la lógica del listener
        # también queda inactiva aunque alguien intente invocarla por código.
        self.runtime_disabled = not settings.NOTIF_API_BACKGROUND_ENABLED
        self.enabled = settings.NOTIF_API_ENABLED and any_api_configured and (not self.runtime_disabled)
        self.timeout_seconds = max(5, settings.NOTIF_API_TIMEOUT_SECONDS)
        self.verify_tls = settings.NOTIF_API_VERIFY_TLS
        self.audit_log_enabled = settings.NOTIF_AUDIT_LOG_ENABLED
        self.poll_seconds = max(10, settings.NOTIF_API_POLL_SECONDS)
        self.access_key = settings.NOTIF_API_ACCESS_KEY
        self.sistema = SistemaAprendizaje()
        self.login_identity = self._resolve_login_identity()
        self.current_login_id = str(self.login_identity.get("login_id") or "").strip()
        self.current_resource_id = str(self.login_identity.get("resource_id") or "").strip()
        self.current_username = str(self.login_identity.get("username") or settings.SOLIDSET_LOGIN_USERNAME or "").strip()
        self.current_session_id = ""
        self.current_login_payload: Dict[str, Any] = {}
        self.current_chat_targets: List[Dict[str, Any]] = []
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

    def _resolve_login_identity(self) -> Dict[str, Optional[str]]:
        username = (settings.SOLIDSET_LOGIN_USERNAME or "").strip()
        if not username:
            return {"username": None, "login_id": None, "resource_id": None}
        try:
            return self.sistema._resolve_user_identity(username)
        except Exception as exc:
            print(f"⚠️ No se pudo resolver identidad SOLIDSET para '{username}': {exc}")
            return {"username": username, "login_id": None, "resource_id": None}

    def _apply_login_response(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        self.current_login_payload = dict(payload)
        login_data = payload.get("LoginData") if isinstance(payload.get("LoginData"), dict) else {}

        login_id = login_data.get("IDLogin") or payload.get("IDUser") or payload.get("IDLogin")
        resource_id = payload.get("IDResource") or login_data.get("IDResource")
        username = payload.get("UserName") or login_data.get("Username") or self.current_username
        session_id = payload.get("IDSession") or self.current_session_id

        if login_id:
            self.current_login_id = str(login_id).strip()
        if resource_id:
            self.current_resource_id = str(resource_id).strip()
        if username:
            self.current_username = str(username).strip()
        if session_id:
            self.current_session_id = str(session_id).strip()

        self.login_identity.update(
            {
                "login_id": self.current_login_id or self.login_identity.get("login_id"),
                "resource_id": self.current_resource_id or self.login_identity.get("resource_id"),
                "username": self.current_username or self.login_identity.get("username"),
                "full_name": str(login_data.get("FullName") or login_data.get("ShowName") or "").strip() or self.login_identity.get("full_name"),
                "display_name": str(login_data.get("ShowName") or login_data.get("Nick") or "").strip() or self.login_identity.get("display_name"),
            }
        )

    def is_enabled(self) -> bool:
        return self.enabled

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }

        active_key = (self.access_key or settings.NOTIF_API_ACCESS_KEY or "").strip()
        if active_key:
            headers["X-Access-Key"] = active_key
            headers["Authorization"] = f"Bearer {active_key}"

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

    def _cache_chat_targets(self, payload: Any) -> List[str]:
        channel_ids = self._extract_channel_ids(payload)
        cached: List[Dict[str, Any]] = []

        def _visit(node: Any) -> None:
            if isinstance(node, dict):
                channel_id = (
                    node.get("IDWorkRoom")
                    or node.get("IDChannel")
                    or node.get("BookMarkedIDChannel")
                    or node.get("ID")
                )
                if isinstance(channel_id, str) and self._is_uuid(channel_id):
                    cached.append(
                        {
                            "channel_id": channel_id.strip(),
                            "channel_name": str(
                                node.get("ChannelName")
                                or node.get("Name")
                                or node.get("DisplayName")
                                or channel_id
                            ).strip(),
                            "kind": str(node.get("ChannelKind") or node.get("Kind") or "").strip(),
                            "raw": node,
                        }
                    )
                for value in node.values():
                    _visit(value)
            elif isinstance(node, list):
                for item in node:
                    _visit(item)

        _visit(payload)

        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in cached:
            key = str(item.get("channel_id") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        self.current_chat_targets = deduped
        return channel_ids

    def _login_payload(self) -> Optional[Dict[str, Any]]:
        if not settings.SOLIDSET_LOGIN_USERNAME and not settings.SOLIDSET_LOGIN_HASHPASS:
            return None

        payload: Dict[str, Any] = {
            "UserName": settings.SOLIDSET_LOGIN_USERNAME or None,
            "Password": settings.SOLIDSET_LOGIN_PASSWORD or None,
            "TimezoneID": settings.SOLIDSET_TIMEZONE_ID or None,
            "Content-Type": "application/json; charset=UTF-8",
        }

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
        headers = self._legacy_login_headers()
        if payload is None:
            return bool(self.access_key)
        
        base_url = settings.SOLIDSET_RESTAPI_BASE_URL        
        for endpoint in ["/User/LoginJson"]:
            url = self._join_url(base_url, endpoint)
            try:
                resp = await client.post(url, data=payload, headers=headers, timeout=10.0)                
                if resp.status_code >= 400:
                    continue

                content_type = (resp.headers.get("content-type") or "").lower()
                if "json" in content_type:
                    data = resp.json()                    
                    if isinstance(data, dict):
                        self._apply_login_response(data)
                        new_key = data.get("accessKey") or data.get("AccessKey")
                        if isinstance(new_key, str) and new_key.strip():
                            self.access_key = new_key.strip()
                return True
            except Exception:
                continue
            

        return bool(self.access_key)

    async def _pull_all_chat_targets(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        result = await self._pull_endpoint(
            client=client,
            base_urls=self.chat_base_urls,
            endpoint="/Chat/GetAllChatTargets",
            source="chat_controller_targets",
            method="GET",
            query_params={
                "mode": 1,
                "includeUserReadPointers": "false",
                "includeTabs": "false",
            },
        )

        payload = result.get("payload")
        if payload is not None:
            channel_ids = self._cache_chat_targets(payload)
        else:
            channel_ids = []

        result["channel_ids"] = channel_ids
        return result

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

    def _normalize_framework_message(self, payload: Any) -> Any:
        """Aplana los campos relevantes del DTO FrameworkMessage sin perder el original."""
        if not isinstance(payload, dict):
            return payload

        normalized = dict(payload)
        top_level_names = [
            "Stamp", "Sender", "Destiny", "Kind", "IDNotification", "RawMessage",
            "RawMessageHtml", "Args", "Chat", "ChatData", "WorkRoomData",
            "Importance", "Priority", "Modifiers", "VisibilityLevel", "MaskMessage", "Info",
        ]
        payload_lower = {str(key).lower(): value for key, value in payload.items()}
        for field_name in top_level_names:
            if field_name not in normalized and field_name.lower() in payload_lower:
                normalized[field_name] = payload_lower[field_name.lower()]

        if "RawMessage" not in normalized:
            return payload

        sender = normalized.get("Sender") if isinstance(normalized.get("Sender"), dict) else {}
        destiny = normalized.get("Destiny") if isinstance(normalized.get("Destiny"), dict) else {}
        chat = normalized.get("Chat") if isinstance(normalized.get("Chat"), dict) else {}
        chat_data = normalized.get("ChatData") if isinstance(normalized.get("ChatData"), dict) else {}
        workroom = normalized.get("WorkRoomData") if isinstance(normalized.get("WorkRoomData"), dict) else {}

        def first(*values: Any) -> Any:
            return next((value for value in values if value not in (None, "")), None)

        def nested(data: Dict[str, Any], *names: str) -> Any:
            lowered = {str(key).lower(): value for key, value in data.items()}
            return first(*(lowered.get(name.lower()) for name in names))

        args = normalized.get("Args") if isinstance(normalized.get("Args"), list) else []
        chat_channels = nested(chat, "channels")
        first_chat_channel = (
            chat_channels[0]
            if isinstance(chat_channels, list) and chat_channels and isinstance(chat_channels[0], dict)
            else {}
        )
        normalized["IDSenderResource"] = first(
            normalized.get("IDSenderResource"),
            nested(sender, "IDResource", "IdResource", "ResourceID", "resource"),
        )
        normalized["IDSenderLogin"] = first(
            normalized.get("IDSenderLogin"),
            nested(sender, "IDLogin", "IdLogin", "LoginID", "login"),
        )
        normalized["SenderFullName"] = first(
            normalized.get("SenderFullName"),
            nested(sender, "FullName", "DisplayName", "Name", "Username", "login"),
        )
        normalized["IDWorkRoom"] = first(
            normalized.get("IDWorkRoom"),
            nested(workroom, "IDWorkRoom", "IdWorkRoom", "workRoom"),
            nested(chat, "IDWorkRoom", "workRoom"), nested(chat_data, "IDWorkRoom", "workRoom"),
            nested(destiny, "IDWorkRoom", "IdWorkRoom", "workRoom", "room"),
            nested(first_chat_channel, "IDChannel", "IdChannel", "idChannel"),
        )
        normalized["ChannelName"] = first(
            normalized.get("ChannelName"),
            nested(workroom, "Name", "ChannelName"),
            nested(chat, "ChannelName"), nested(chat_data, "ChannelName"), nested(destiny, "Name"),
        )
        normalized["IDChat"] = first(
            normalized.get("IDChat"), nested(chat, "IDChat", "IdChat"),
            nested(chat_data, "IDChat", "IdChat"), args[0] if args else None,
        )
        normalized["IsPublic"] = first(
            normalized.get("IsPublic"), nested(chat, "IsPublic"), nested(chat_data, "IsPublic"),
            nested(destiny, "IsPublic"),
        )
        normalized["FrameworkKind"] = normalized.get("Kind")
        normalized["FrameworkStamp"] = normalized.get("Stamp")
        normalized["FrameworkSender"] = sender
        normalized["FrameworkDestiny"] = destiny
        return normalized

    def _destiny_addresses_agent(self, destiny: Dict[str, Any]) -> bool:
        """Comprueba el destino directo y cada entrada de Destiny.dests."""
        if not isinstance(destiny, dict):
            return False

        zero_guid = "00000000-0000-0000-0000-000000000000"

        def clean(value: Any) -> str:
            normalized = str(value or "").strip().lower()
            return "" if normalized == zero_guid else normalized

        own_logins = {
            value for value in (
                clean(settings.SOLIDSET_LOGIN_RESOURCE_ID),
                clean(getattr(self, "current_login_id", "")),
            ) if value
        }
        own_resources = {
            value for value in (
                clean(settings.SOLIDSET_RESOURCE_ID),
                clean(getattr(self, "current_resource_id", "")),
            ) if value
        }
        if not own_logins and not own_resources:
            return False

        lowered = {str(key).lower(): value for key, value in destiny.items()}
        destinations: List[Dict[str, Any]] = [lowered]
        raw_dests = lowered.get("dests")
        if isinstance(raw_dests, list):
            destinations.extend(item for item in raw_dests if isinstance(item, dict))

        for destination in destinations:
            item = {str(key).lower(): value for key, value in destination.items()}
            login = clean(item.get("login") or item.get("idlogin"))
            resource = clean(item.get("resource") or item.get("idresource"))
            if (login and login in own_logins) or (resource and resource in own_resources):
                return True
        return False

    @staticmethod
    def _normalize_visibility_level(value: Any) -> int:
        """Normaliza VisibilityLevel recibido como nombre de enum o entero."""
        visibility_by_name = {
            "public": 0,
            "normal": 1,
            "confidential": 2,
            "private": 3,
        }
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in visibility_by_name:
                return visibility_by_name[normalized]
            if normalized.isdigit():
                value = int(normalized)
        if isinstance(value, int) and not isinstance(value, bool) and value in {0, 1, 2, 3}:
            return value
        return 1

    @staticmethod
    def _normalize_chat_importance(value: Any) -> int:
        """Normaliza ChatImportance recibido como nombre de enum o entero."""
        importance_by_name = {
            "low": 0,
            "normal": 1,
            "high": 2,
            "urgent": 3,
        }
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in importance_by_name:
                return importance_by_name[normalized]
            if normalized.isdigit():
                value = int(normalized)
        if isinstance(value, int) and not isinstance(value, bool) and value in {0, 1, 2, 3}:
            return value
        return 1

    @staticmethod
    def _extract_meeting_context(
        info: Any,
        extra_data: Any = None,
        chat: Any = None,
    ) -> Dict[str, Any]:
        """Extrae un meeting explícito desde Info, ExtraData o Chat."""
        lowered = (
            {str(key).lower(): value for key, value in info.items()}
            if isinstance(info, dict) else {}
        )
        extra: Dict[str, Any] = {}
        if isinstance(extra_data, dict):
            extra = {str(key).lower(): value for key, value in extra_data.items()}
        elif isinstance(extra_data, str) and extra_data.strip():
            try:
                parsed = json.loads(extra_data)
                if isinstance(parsed, dict):
                    extra = {str(key).lower(): value for key, value in parsed.items()}
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        chat_lower = (
            {str(key).lower(): value for key, value in chat.items()}
            if isinstance(chat, dict) else {}
        )
        chat_extra = chat_lower.get("extradata")
        if not extra and isinstance(chat_extra, str) and chat_extra.strip():
            try:
                parsed = json.loads(chat_extra)
                if isinstance(parsed, dict):
                    extra = {str(key).lower(): value for key, value in parsed.items()}
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        meeting_id = str(
            lowered.get("meeting_id")
            or extra.get("meeting_id")
            or chat_lower.get("idmeeting")
            or ""
        ).strip()
        if not meeting_id or meeting_id == "00000000-0000-0000-0000-000000000000":
            return {"active": False, "meeting_id": "", "meeting_code": ""}
        return {
            "active": True,
            "meeting_id": meeting_id,
            "meeting_code": str(
                lowered.get("meeting_code") or extra.get("meeting_code") or ""
            ).strip(),
        }

    @staticmethod
    def _is_generated_by_ia(info: Any) -> bool:
        if not isinstance(info, dict):
            return False
        lowered = {str(key).lower(): value for key, value in info.items()}
        value = str(lowered.get("generated_by_ia") or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def _normalize_message_kind(value: Any) -> Dict[str, Any]:
        """Normaliza KindMessage y aporta una categoría semántica al agente."""
        conversational_names = {
            "chatmessage",
            "chatmessagetaskcomment",
            "chatmessageactivitycomment",
            "chatmessagevideocallcomment",
            "chatmessagevideocallwrcomment",
            "chatmessageagreementitemcomment",
            "chatmessagedocumentcomment",
            "chatmessageleadcomment",
            "chatmessagetimecomment",
            "chatmessageserverfilecomment",
            "chatmessageemailcomment",
            "chatmessagecompanycomment",
            "chatmessagefupcomment",
            "chatmessagemeetingcomment",
            "chatmessagecomment",
        }
        if value is None or value == "":
            # Compatibilidad con fuentes antiguas que no incluían Kind.
            return {"raw": value, "name": "", "value": None, "conversational": True, "category": "chat"}
        if isinstance(value, str):
            text = value.strip()
            if text.lstrip("-").isdigit():
                numeric = int(text)
                return {
                    "raw": value,
                    "name": "ChatMessage" if numeric == 7 else "",
                    "value": numeric,
                    "conversational": numeric == 7,
                    "category": "chat" if numeric == 7 else "event",
                }
            compact = re.sub(r"[^a-z0-9]", "", text.lower())
            if "meeting" in compact:
                category = "meeting"
            elif "task" in compact:
                category = "task"
            elif "activity" in compact:
                category = "activity"
            elif "videocall" in compact or "livekitcall" in compact or "callmessage" in compact:
                category = "call"
            elif "email" in compact:
                category = "email"
            elif "chatmessage" in compact:
                category = "chat"
            else:
                category = "system_event"
            return {
                "raw": value,
                "name": text,
                "value": 7 if compact == "chatmessage" else None,
                "conversational": compact in conversational_names,
                "category": category,
            }
        if isinstance(value, int) and not isinstance(value, bool):
            return {
                "raw": value,
                "name": "ChatMessage" if value == 7 else "",
                "value": value,
                "conversational": value == 7,
                "category": "chat" if value == 7 else "event",
            }
        return {
            "raw": value, "name": "", "value": None,
            "conversational": False, "category": "event",
        }

    def _normalize_entries(self, source: str, endpoint: str, payload: Any, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if endpoint.lower().rstrip("/").endswith("frameworkhub/sendmessage"):
            payload = self._normalize_framework_message(payload)
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
        sender_resource = data.get("IDSenderResource")
        sender_login = data.get("IDSenderLogin")
        sender_name = (
            data.get("SenderFullName")
            or data.get("DisplayName")
            or data.get("SenderName")
            or sender_resource
            or "desconocido"
        )
        is_public = str(data.get("IsPublic") or "").lower() in {"1", "true"}
        chat_id = data.get("IDChat") or data.get("IDChat2")
        destiny = data.get("FrameworkDestiny") if isinstance(data.get("FrameworkDestiny"), dict) else {}
        destiny_lower = {str(key).lower(): value for key, value in destiny.items()}
        destiny_resource = str(destiny_lower.get("resource") or destiny_lower.get("idresource") or "").strip()
        destiny_dests = destiny_lower.get("dests") if isinstance(destiny_lower.get("dests"), list) else []
        addressed_to_agent = self._destiny_addresses_agent(destiny)
        meeting = self._extract_meeting_context(
            data.get("Info"), data.get("ExtraData"), data.get("Chat")
        )
        chat_payload = data.get("Chat") if isinstance(data.get("Chat"), dict) else {}
        chat_lower = {str(key).lower(): value for key, value in chat_payload.items()}
        quoted_payload = (
            chat_lower.get("chatquestion")
            if isinstance(chat_lower.get("chatquestion"), dict)
            else {}
        )
        quoted_lower = {
            str(key).lower(): value for key, value in quoted_payload.items()
        }
        message_kind = self._normalize_message_kind(data.get("FrameworkKind", data.get("Kind")))
        # Un destinatario explícito prevalece sobre workRoom: se responde al Sender.resource.
        is_direct = addressed_to_agent
        channel_id_text = str(channel_id or "").strip()
        if not channel_id_text and not (is_direct and str(sender_resource or "").strip()):
            return None

        candidate = {
            "fingerprint": fingerprint,
            "timestamp": datetime.utcnow().isoformat(),
            "source": entry.get("source", "notification_api"),
            "endpoint": entry.get("endpoint", ""),
            "channel_id": channel_id_text,
            "channel_name": data.get("ChannelName") or data.get("OriginChannelName") or channel_id_text,
            "sender_resource": str(sender_resource or "").strip(),
            "sender_login": str(sender_login or "").strip(),
            "sender_name": str(sender_name),
            "chat_id": chat_id,
            "recipient_count": len([item for item in destiny_dests if isinstance(item, dict)]),
            "importance": self._normalize_chat_importance(data.get("Importance")),
            "is_public": is_public,
            "scope": "directo" if is_direct else ("canal" if is_public else "chat"),
            "visibility_level": self._normalize_visibility_level(data.get("VisibilityLevel")),
            "meeting_active": meeting["active"],
            "meeting_id": meeting["meeting_id"],
            "meeting_code": meeting["meeting_code"],
            "generated_by_ia": self._is_generated_by_ia(data.get("Info")),
            "message_kind": message_kind["name"] or str(message_kind["raw"] or ""),
            "message_kind_value": message_kind["value"],
            "kind_reply_eligible": message_kind["conversational"],
            "message_category": message_kind["category"],
            "message": raw_message.strip(),
            "quoted_chat_id": (
                chat_lower.get("chatquestionmessage")
                or quoted_lower.get("idchat2")
            ),
            "quoted_message": str(quoted_lower.get("rawmessage") or "").strip(),
            "quoted_sender_resource": str(
                quoted_lower.get("idsenderresource") or ""
            ).strip(),
            "destiny_resource": destiny_resource,
            "addressed_to_agent": addressed_to_agent,
            "is_direct": is_direct,
            "reply_resource": str(sender_resource or "").strip(),
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
        meeting = self._extract_meeting_context(
            payload.get("Info") if payload else None,
            payload.get("ExtraData") if payload else None,
            payload.get("Chat") if payload else None,
        )
        framework_stamp = payload.get("FrameworkStamp") or payload.get("Stamp") if payload else None
        event_timestamp = datetime.utcnow()
        if isinstance(framework_stamp, str) and framework_stamp.strip():
            try:
                event_timestamp = datetime.fromisoformat(framework_stamp.strip().replace("Z", "+00:00"))
            except ValueError:
                pass

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
            timestamp=event_timestamp,
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
                "framework_kind": payload.get("FrameworkKind") or payload.get("Kind"),
                "framework_stamp": framework_stamp,
                "id_notification": payload.get("IDNotification"),
                "importance": payload.get("Importance"),
                "priority": payload.get("Priority"),
                "modifiers": payload.get("Modifiers"),
                "visibility_level": payload.get("VisibilityLevel"),
                "meeting_active": meeting["active"],
                "meeting_id": meeting["meeting_id"],
                "meeting_code": meeting["meeting_code"],
                "mask_message": payload.get("MaskMessage"),
                "fingerprint": fingerprint,
                "captured_at": datetime.utcnow().isoformat(),
                "payload": data,
            },
        )
        learned_global = self.sistema.aprender_actividad(actividad)

        # Cada mensaje permanece en el aprendizaje global. Además, cuando el
        # remitente es el recurso humano propietario de un agente activo, se
        # guarda una segunda representación privada etiquetada para ese agente.
        # consultar_documentacion filtra agent_resource_id y evita que otro
        # agente utilice este patrón personal.
        if sender_resource and raw_message:
            try:
                owner_agent = get_active_agent_identity_for_resource(sender_resource)
            except Exception as exc:
                owner_agent = None
                print(f"⚠️ No se pudo resolver aprendizaje privado del agente: {exc}")
            if owner_agent:
                private_metadata = dict(actividad.metadatos or {})
                private_metadata.update({
                    "agent_resource_id": str(owner_agent["IDResource"]),
                    "agent_identity_id": str(owner_agent["ID"]),
                    "scope": "agent_owner_behavior",
                    "learning_origin": "human_resource_message",
                })
                private_activity = Actividad(
                    id=f"agent_owner_{owner_agent['ID']}_{fingerprint[:20]}",
                    recurso_humano_id=str(sender_resource),
                    canal_id=actividad.canal_id,
                    tipo="agent_owner_behavior",
                    descripcion=(
                        "Patrón comunicativo y conocimiento expresado por el "
                        f"recurso humano propietario: {summary}"
                    ),
                    timestamp=event_timestamp,
                    metadatos=private_metadata,
                )
                self.sistema.aprender_actividad(private_activity)

        return learned_global

    def capture_realtime_payload(
        self,
        payload: Any,
        endpoint: str = "/frameworkHub/SendMessage",
    ) -> Dict[str, Any]:

        #print(f"📡 Capturando payload en tiempo real desde {payload}...")

        """Indexa inmediatamente un mensaje recibido por el proxy del hub."""
        entries = self._normalize_entries(
            source="framework_hub_realtime",
            endpoint=endpoint,
            payload=payload,
        )
        learned = 0
        skipped = 0
        errors = 0
        auto_reply_candidates: List[Dict[str, Any]] = []

        for entry in entries:
            fingerprint = self._fingerprint(entry)
            if fingerprint in self.seen_fingerprints:
                skipped += 1
                self._trace_captured_message(entry, status="duplicate")
                continue

            candidate = self._build_auto_reply_candidate(entry, fingerprint=fingerprint)
            if candidate is not None:
                auto_reply_candidates.append(candidate)
            try:
                if self._learn_entry(entry, fingerprint):
                    learned += 1
                    self._trace_captured_message(entry, status="learned_realtime")
                else:
                    skipped += 1
                    self._trace_captured_message(entry, status="learn_error_realtime")
            except Exception as exc:
                errors += 1
                print(f"⚠️ Error indexando mensaje en tiempo real: {exc}")
            finally:
                self._remember_fingerprint(fingerprint)

        return {
            "received": len(entries),
            "learned": learned,
            "skipped": skipped,
            "errors": errors,
            "auto_reply_candidates": auto_reply_candidates,
        }

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
        if self.runtime_disabled:
            return {
                "learned": 0,
                "skipped": 0,
                "errors": 0,
                "auto_reply_candidates": [],
            }

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

                    # Publicar candidatos de auto-reply incluso si el aprendizaje RAG falla.
                    candidate = self._build_auto_reply_candidate(entry, fingerprint=fp)
                    if candidate is not None:
                        auto_reply_candidates.append(candidate)

                    if self._learn_entry(entry, fp):
                        learned += 1
                        self._remember_fingerprint(fp)
                        self._trace_captured_message(entry, status="learned")
                    else:
                        # Evita inflar "errors" cuando solo falla la indexación semántica.
                        skipped += 1
                        self._remember_fingerprint(fp)
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

    def _legacy_chat_message_query_variants(self, channel_id: str) -> List[Dict[str, Any]]:
        login_id = (self.current_login_id or "").strip()
        if not login_id:
            return []

        page_size = max(1, min(settings.SOLIDSET_CHAT_PAGE_SIZE, 100))
        return [
            {
                "Options": 2,
                "Kind": 1,
                "Latest": "true",
                "IDLoginCurrent": login_id,
                "SelectAll": "false",
                "LatestCount": page_size,
                "LatestIDChat": 0,
                "SelectedWorkRooms[0]": channel_id,
            }
        ]

    async def _pull_channel_messages(self, client: httpx.AsyncClient, channel_id: str) -> Dict[str, Any]:
        totals: Dict[str, Any] = {
            "learned": 0,
            "skipped": 0,
            "errors": 0,
            "chat_targets": [],
            "auto_reply_candidates": [],
        }
        legacy_queries = self._legacy_chat_message_query_variants(channel_id)
        for query_params in legacy_queries:
            result = await self._pull_endpoint(
                client=client,
                base_urls=self.chat_base_urls,
                endpoint="/Chat/ChatMessages",
                source="chat_controller_messages_legacy",
                method="POST",
                query_params=query_params,
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
            if result["learned"] > 0 or result["skipped"] > 0:
                return totals

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
        if self.runtime_disabled:
            return {
                "enabled": False,
                "runtime_disabled": True,
                "learned": 0,
                "skipped": 0,
                "errors": 0,
                "auto_reply_candidates": [],
            }
        if not self.enabled:
            return {"enabled": False, "learned": 0, "skipped": 0, "errors": 0, "auto_reply_candidates": []}

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
            logged_in = await self._ensure_login(client)
            if not logged_in:
                return {
                    "enabled": True,
                    "learned": 0,
                    "skipped": 0,
                    "errors": 1,
                    "channels_detected": 0,
                    "chat_channel_pulls": 0,
                    "reaction_channel_pulls": 0,
                    "timestamp": datetime.utcnow().isoformat(),
                    "cycle_elapsed_ms": round((perf_counter() - cycle_started_at) * 1000, 2),
                    "auto_reply_candidates": [],
                }

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
            targets_result = await self._pull_all_chat_targets(client)
            learned += targets_result["learned"]
            skipped += targets_result["skipped"]
            errors += targets_result["errors"]
            auto_reply_candidates.extend(targets_result.get("auto_reply_candidates") or [])
            channel_ids.extend(targets_result.get("channel_ids") or [])

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
                configured_max = settings.SOLIDSET_CHAT_MAX_CHANNELS
                selected_channels = (
                    unique_channels
                    if configured_max == 0
                    else unique_channels[:configured_max]
                )
                self._audit_log(
                    f"🔎 AUDIT channels detected={channels_detected} "
                    f"selected={len(selected_channels)} "
                    f"mode={'all' if configured_max == 0 else 'limited'}"
                )

                for channel_id in selected_channels:
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

    async def warmup_session(self) -> Dict[str, Any]:
        """Autentica al arrancar y precarga los chats/canales donde la cuenta puede operar."""
        if self.runtime_disabled:
            return {
                "enabled": False,
                "runtime_disabled": True,
                "logged_in": False,
                "channel_ids": [],
            }
        if not self.enabled:
            return {"enabled": False, "logged_in": False, "channel_ids": []}

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            verify=self.verify_tls,
            follow_redirects=True,
        ) as client:
            logged_in = await self._ensure_login(client)
            
            if not logged_in:
                return {
                    "enabled": True,
                    "logged_in": False,
                    "login_id": self.current_login_id,
                    "resource_id": self.current_resource_id,
                    "session_id": self.current_session_id,
                    "username": self.current_username,
                    "channel_ids": [],
                }

            targets_result = await self._pull_all_chat_targets(client)
            return {
                "enabled": True,
                "logged_in": True,
                "login_id": self.current_login_id,
                "resource_id": self.current_resource_id,
                "session_id": self.current_session_id,
                "username": self.current_username,
                "login_payload": self.current_login_payload,
                "channel_ids": targets_result.get("channel_ids") or [],
                "targets_cached": len(self.current_chat_targets),
                "errors": targets_result.get("errors", 0),
            }

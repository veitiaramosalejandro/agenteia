import hashlib
import json
import os
import re
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

    def _normalize_entries(self, source: str, endpoint: str, payload: Any, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
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

                entries = self._normalize_entries(source, endpoint, payload, channel_id=channel_id)
                for entry in entries:
                    fp = self._fingerprint(entry)
                    if fp in self.seen_fingerprints:
                        skipped += 1
                        continue
                    if self._learn_entry(entry, fp):
                        learned += 1
                        self._remember_fingerprint(fp)
                    else:
                        errors += 1

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
                }
            except Exception as exc:
                last_exc = exc
                continue

        errors += 1
        if last_exc:
            print(f"⚠️ Error consultando {source} {endpoint}: {last_exc}")

        return {"learned": learned, "skipped": skipped, "errors": errors}

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
        totals: Dict[str, Any] = {"learned": 0, "skipped": 0, "errors": 0, "chat_targets": []}
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

        return {
            "enabled": True,
            "learned": learned,
            "skipped": skipped,
            "errors": errors,
            "channels_detected": channels_detected,
            "chat_channel_pulls": chat_channel_pulls,
            "reaction_channel_pulls": reaction_channel_pulls,
            "timestamp": datetime.utcnow().isoformat(),
        }

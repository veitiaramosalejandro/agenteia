import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

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
        self.enabled = settings.NOTIF_API_ENABLED and bool(self.base_url)
        self.timeout_seconds = max(5, settings.NOTIF_API_TIMEOUT_SECONDS)
        self.verify_tls = settings.NOTIF_API_VERIFY_TLS
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

        # Inside Docker, localhost/127.0.0.1 points to the container itself.
        if hostname in {"localhost", "127.0.0.1"}:
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

    async def _ensure_login(self, client: httpx.AsyncClient) -> bool:
        payload = self._login_payload()
        if payload is None:
            return bool(self.access_key)

        for base_url in self.base_urls:
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

    def _remember_fingerprint(self, fingerprint: str) -> None:
        self.seen_fingerprints.append(fingerprint)
        if len(self.seen_fingerprints) > self.max_seen:
            self.seen_fingerprints = self.seen_fingerprints[-self.max_seen :]

    def _learn_entry(self, entry: Dict[str, Any], fingerprint: str) -> bool:
        source = entry.get("source", "notification_api")
        endpoint = entry.get("endpoint", "")
        channel_id = entry.get("channel_id")
        data = entry.get("data")

        short_data = json.dumps(data, ensure_ascii=False, default=str)
        if len(short_data) > 600:
            short_data = short_data[:600] + "..."

        actividad = Actividad(
            id=f"notif_{fingerprint[:28]}",
            recurso_humano_id="sistema",
            canal_id=channel_id or "solidset_communicator_notifications",
            tipo=source,
            descripcion=f"{source} {endpoint}: {short_data}",
            timestamp=datetime.utcnow(),
            metadatos={
                "source_table": "NotificationAPI",
                "source": source,
                "endpoint": endpoint,
                "channel_id": channel_id,
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
        channel_id: Optional[str] = None,
    ) -> Dict[str, int]:
        learned = 0
        skipped = 0
        errors = 0
        last_exc: Optional[Exception] = None
        for base_url in base_urls:
            url = self._join_url(base_url, endpoint)
            try:
                if method == "POST":
                    resp = await client.post(url, headers=self._headers(), json=json_body or {})
                else:
                    resp = await client.get(url, headers=self._headers())

                if resp.status_code >= 400:
                    errors += 1
                    continue

                content_type = (resp.headers.get("content-type") or "").lower()
                if "json" in content_type:
                    payload = resp.json()
                else:
                    payload = resp.text

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

                return {"learned": learned, "skipped": skipped, "errors": errors}
            except Exception as exc:
                last_exc = exc
                continue

        errors += 1
        if last_exc:
            print(f"⚠️ Error consultando {source} {endpoint}: {last_exc}")

        return {"learned": learned, "skipped": skipped, "errors": errors}

    def _chat_message_payload_variants(self, channel_id: str) -> List[Dict[str, Any]]:
        page_size = max(5, min(settings.SOLIDSET_CHAT_PAGE_SIZE, 100))
        return [
            {"IDWorkRoom": channel_id, "Page": 1, "PageSize": page_size},
            {"idWorkRoom": channel_id, "page": 1, "pageSize": page_size},
            {"IDWorkRoom": channel_id, "Skip": 0, "Take": page_size},
            {"idWorkRoom": channel_id, "skip": 0, "take": page_size},
        ]

    async def _pull_channel_messages(self, client: httpx.AsyncClient, channel_id: str) -> Dict[str, int]:
        totals = {"learned": 0, "skipped": 0, "errors": 0}
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
                if result["learned"] > 0 or result["skipped"] > 0:
                    return totals
        return totals

    async def pull_once(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "learned": 0, "skipped": 0, "errors": 0}

        learned = 0
        skipped = 0
        errors = 0
        channels_detected = 0
        chat_channel_pulls = 0

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
                payload = None
                payload_error = None
                for base_url in self.base_urls:
                    url = self._join_url(base_url, endpoint)
                    try:
                        resp = await client.get(url, headers=self._headers())
                        if resp.status_code >= 400:
                            continue
                        content_type = (resp.headers.get("content-type") or "").lower()
                        payload = resp.json() if "json" in content_type else resp.text
                        break
                    except Exception as exc:
                        payload_error = exc
                        continue

                result = await self._pull_endpoint(
                    client=client,
                    base_urls=self.base_urls,
                    endpoint=endpoint,
                    source="notification_user_api",
                )
                learned += result["learned"]
                skipped += result["skipped"]
                errors += result["errors"]

                if payload is not None:
                    channel_ids.extend(self._extract_channel_ids(payload))
                elif payload_error is not None:
                    print(f"⚠️ Error leyendo canales desde {endpoint}: {payload_error}")

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

                max_channels = max(1, settings.SOLIDSET_CHAT_MAX_CHANNELS)
                for channel_id in unique_channels[:max_channels]:
                    msg_result = await self._pull_channel_messages(client, channel_id)
                    learned += msg_result["learned"]
                    skipped += msg_result["skipped"]
                    errors += msg_result["errors"]
                    chat_channel_pulls += 1

        return {
            "enabled": True,
            "learned": learned,
            "skipped": skipped,
            "errors": errors,
            "channels_detected": channels_detected,
            "chat_channel_pulls": chat_channel_pulls,
            "timestamp": datetime.utcnow().isoformat(),
        }

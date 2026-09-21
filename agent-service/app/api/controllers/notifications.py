from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
import psycopg
import redis
from fastapi import APIRouter, Body, HTTPException, Request, Response, status

from app.api.schemas.common import FrameworkMessageDTO, SendMessageResultDTO
from app.api.schemas.preview import FrameworkMessagePreviewResponse
from app.api.examples import _FRAMEWORK_MESSAGE_EXAMPLES
from app.config import settings
from app.connectors.db_client import save_agent_response_audit
from app.services.auto_reply import (
    _enqueue_auto_replies,
    _process_auto_replies,
    _schedule_auto_replies,
)
from app.services.instance_resolution import (
    _attach_solidset_instance,
    _request_ip_details,
    _resolve_request_solidset_instance,
)
from app.services.response_status import create as _create_response_status
from app.services.response_status import update as _update_response_status


router = APIRouter(tags=["SolidSET Notifications"])
notification_listener = None


def _redact_framework_payload(value: Any) -> Any:
    """Oculta credenciales antes de imprimir el JSON recibido."""
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if re.search(
                    r"password|passwd|token|secret|authorization|api[_-]?key|cookie|salt",
                    str(key),
                    re.I,
                )
                else _redact_framework_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_framework_payload(item) for item in value]
    return value


def _trace_initial_request(request: Request, payload: dict[str, Any]) -> None:
    """Log routing evidence without message text, cookies, or credentials."""
    direct_ip, forwarded_ip = _request_ip_details(request)
    headers = request.headers

    def safe_header(name: str) -> str | None:
        value = headers.get(name)
        if value is None:
            return None
        return " ".join(str(value).split())[:255]

    def field(source: dict[str, Any], name: str) -> str | None:
        value = next(
            (value for key, value in source.items() if str(key).lower() == name.lower()),
            None,
        )
        return str(value)[:255] if value not in (None, "") else None

    def section(name: str) -> dict[str, Any]:
        value = next(
            (value for key, value in payload.items() if str(key).lower() == name.lower()),
            None,
        )
        return value if isinstance(value, dict) else {}

    sender = section("Sender")
    framework_sender = section("FrameworkSender")
    destiny = section("Destiny")
    info = section("Info")
    chat = section("Chat")

    print(
        "📨 INITIAL_REQUEST_IDENTITY "
        + json.dumps(
            {
                "remote_ip": direct_ip,
                "forwarded_ip": forwarded_ip,
                "header_names": sorted(str(name).lower() for name in headers.keys()),
                "headers": {
                    "x-solidset-instance": safe_header("x-solidset-instance"),
                    "host": safe_header("host"),
                    "x-real-ip": safe_header("x-real-ip"),
                    "user-agent": safe_header("user-agent"),
                    "x-request-id": safe_header("x-request-id"),
                },
                "session": {
                    "sender": field(sender, "session"),
                    "framework_sender": field(framework_sender, "session"),
                    "destiny": field(destiny, "session"),
                    "info": field(info, "session_id"),
                },
                "sender": {
                    "login": field(sender, "login"),
                    "resource": field(sender, "resource"),
                    "framework_login": field(framework_sender, "login"),
                    "framework_resource": field(framework_sender, "resource"),
                    "chat_login": field(chat, "idSender"),
                    "chat_resource": field(chat, "idSenderResource"),
                },
                "chat_id": field(chat, "idChat2") or field(chat, "idChat"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def _trace_resolved_instance(instance: dict[str, Any]) -> None:
    def safe_origin(value: Any) -> str:
        try:
            parsed = urlsplit(str(value or ""))
            hostname, port = parsed.hostname, parsed.port
        except ValueError:
            return ""
        if not parsed.scheme or not hostname:
            return ""
        return f"{parsed.scheme}://{hostname}{f':{port}' if port else ''}"

    data_api = instance.get("DataAPI") if isinstance(instance.get("DataAPI"), dict) else {}
    print(
        "📨 INITIAL_REQUEST_DESTINATION "
        + json.dumps(
            {
                "code": str(instance.get("Code") or ""),
                "instance_id": str(instance.get("ID") or ""),
                "source_ip": str(instance.get("SourceIP") or ""),
                "base_url_origin": safe_origin(instance.get("BaseUrl")),
                "notification_url_origin": safe_origin(instance.get("NotificationUrl")),
                "data_api_origin": safe_origin(data_api.get("BaseUrl")),
                "data_api_active": data_api.get("active"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def configure(runtime_notification_listener: Any) -> None:
    global notification_listener
    notification_listener = runtime_notification_listener


def _framework_message_chat_id(
    payload: dict[str, Any], candidates: list[dict[str, Any]]
) -> str:
    candidate_chat_id = next(
        (item.get("chat_id") for item in candidates if item.get("chat_id")), ""
    )
    chat_payload = payload.get("Chat") if isinstance(payload.get("Chat"), dict) else {}
    chat_payload_lower = {
        str(key).lower(): value for key, value in chat_payload.items()
    }
    return str(
        candidate_chat_id
        or chat_payload_lower.get("idchat2")
        or chat_payload_lower.get("idchat")
        or ""
    ).strip()


@router.post("/api/v1/agent/notification/framework-message", response_model=SendMessageResultDTO,  status_code=status.HTTP_202_ACCEPTED)
async def receive_framework_notification(
    message: Annotated[
        FrameworkMessageDTO,
        Body(openapi_examples=_FRAMEWORK_MESSAGE_EXAMPLES),
    ],
    request: Request,
):
    """Recibe desde Notification un FrameworkMessage ya capturado y lo aprende en Qdrant."""
    print(
        "📨 FRAMEWORK_MESSAGE_PAYLOAD "
        + json.dumps(
            _redact_framework_payload(await request.json()),
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )

    payload = (
        message.model_dump(mode="json")
        if hasattr(message, "model_dump")
        else message.dict()
    )
    _trace_initial_request(request, payload)
    try:
        instance = _resolve_request_solidset_instance(request)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível determinar a instância SolidSET."
        ) from exc
    if instance is None:
        raise HTTPException(
            status_code=400,
            detail="Instância SolidSET desconhecida. Envie X-SolidSET-Instance com o host configurado em SourceIP.",
        )
    _trace_resolved_instance(instance)
    payload["_SolidSETInstanceID"] = str(instance["ID"])
    chat_id = _framework_message_chat_id(payload, [])
    # IDChat2 es la referencia compartida con WPF/SolidSET. Solo se genera un
    # UUID defensivo para notificaciones técnicas que no contienen chat.
    request_id = chat_id or str(uuid.uuid4())
    _create_response_status(request_id, chat_id, 0)
    try:
        if settings.AGENT_RESPONSE_QUEUE_ENABLED:
            await asyncio.to_thread(
                _enqueue_auto_replies,
                payload,
                dict(instance),
                request_id,
                chat_id,
            )
        else:
            capture = await asyncio.to_thread(notification_listener.capture_realtime_payload, payload)
            candidates = capture.get("auto_reply_candidates") or []
            _attach_solidset_instance(candidates, instance)
            _schedule_auto_replies(candidates, request_id)
    except redis.RedisError as exc:
        _update_response_status(request_id, "failed", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Não foi possível colocar o pedido na fila nem registá-lo para auditoria.",
                "requestId": request_id,
                "error": str(exc),
            },
        ) from exc
    try:
        await asyncio.to_thread(
            save_agent_response_audit,
            request_id,
            chat_id,
            "queued",
            0,
            None,
            payload,
            None,
        )
    except psycopg.Error as exc:
        # Redis Stream ya aceptó el trabajo. No se devuelve 503 porque el
        # cliente podría duplicarlo; el worker reintentará el UPSERT terminal.
        print(f"⚠️ Solicitud encolada sin auditoría inicial PostgreSQL: {exc}")
    print(f"📥 FrameworkMessage encolado requestId={request_id}")
    return SendMessageResultDTO(
        Result=0,
        Message=message,
        Error=None,
        requestId=request_id,
        status="queued",
        statusUrl=f"/api/v1/agent/responses/{request_id}/status",
    )


def _inflate_solidset_form_payload(form_payload: dict[str, Any]) -> dict[str, Any]:
    """Convierte las claves de SendMessageForm en el JSON lógico de SolidSET."""
    result: dict[str, Any] = {}
    token_pattern = re.compile(r"([^.\[\]]+)|\[([^\]]+)\]")
    for flat_key, raw_value in form_payload.items():
        tokens: list[str | int] = []
        for match in token_pattern.finditer(str(flat_key)):
            token = match.group(1) if match.group(1) is not None else match.group(2)
            tokens.append(int(token) if str(token).isdigit() else str(token))
        if not tokens:
            continue
        value = raw_value
        if flat_key == "ExtraData" and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        elif isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"

        current: Any = result
        for index, token in enumerate(tokens):
            last = index == len(tokens) - 1
            next_token = None if last else tokens[index + 1]
            if isinstance(token, int):
                while len(current) <= token:
                    current.append(None)
                if last:
                    current[token] = value
                elif current[token] is None:
                    current[token] = [] if isinstance(next_token, int) else {}
                current = current[token]
            else:
                if last:
                    current[token] = value
                else:
                    if token not in current:
                        current[token] = [] if isinstance(next_token, int) else {}
                    current = current[token]
    return result


@router.post(
    "/api/v1/agent/notification/framework-message/preview",
    response_model=FrameworkMessagePreviewResponse,
)
async def preview_framework_notification(
    message: Annotated[
        FrameworkMessageDTO,
        Body(openapi_examples=_FRAMEWORK_MESSAGE_EXAMPLES),
    ],
    request: Request,
) -> dict[str, Any]:
    """Devuelve Response y Responses aunque no se pueda preparar un payload.

    No envía mensajes a SolidSET. Response es null cuando no se obtiene una
    respuesta; PayloadCount solo cuenta los payloads preparados. La captura
    conserva su comportamiento de aprendizaje existente.
    """
    payload = message.model_dump(mode="json")
    _trace_initial_request(request, payload)
    try:
        instance = _resolve_request_solidset_instance(request)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível determinar a instância SolidSET."
        ) from exc
    if instance is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Instância SolidSET desconhecida. Envie X-SolidSET-Instance "
                "com o host configurado em SourceIP."
            ),
        )
    _trace_resolved_instance(instance)
    payload["_SolidSETInstanceID"] = str(instance["ID"])
    capture = await asyncio.to_thread(notification_listener.capture_realtime_payload, payload)
    candidates = capture.get("auto_reply_candidates") or []
    _attach_solidset_instance(candidates, instance)
    if capture["errors"]:
        raise HTTPException(
            status_code=503,
            detail=f"Não foi possível processar a mensagem: {capture['errors']} erro(s).",
        )
    preview_responses: list[dict[str, Any]] = []
    flat_payloads = await _process_auto_replies(
        candidates, preview_only=True, _preview_responses=preview_responses,
    )
    logical_payloads = [_inflate_solidset_form_payload(item) for item in flat_payloads]
    return {
        "Result": 0,
        "Learned": capture["learned"],
        "Skipped": capture["skipped"],
        "Response": "\n\n".join(item["Response"] for item in preview_responses) or None,
        "Responses": preview_responses,
        "PayloadCount": len(logical_payloads),
        "Payloads": logical_payloads,
    }


@router.post("/api/v1/agent/notification/frameworkHub/SendMessage")
async def capture_and_forward_framework_message(request: Request):
    """Captura el mensaje en Qdrant antes de reenviarlo al endpoint real de SolidSET."""
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {"RawMessage": raw_body.decode("utf-8", errors="replace")}

    _trace_initial_request(request, payload if isinstance(payload, dict) else {})

    try:
        instance = _resolve_request_solidset_instance(request)
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=503, detail="Não foi possível determinar a instância SolidSET."
        ) from exc
    if instance is None:
        raise HTTPException(
            status_code=400,
            detail="Instância SolidSET desconhecida. Envie X-SolidSET-Instance com o host configurado em SourceIP.",
        )
    _trace_resolved_instance(instance)
    if isinstance(payload, dict):
        payload["_SolidSETInstanceID"] = str(instance["ID"])
    capture = await asyncio.to_thread(notification_listener.capture_realtime_payload, payload)
    candidates = capture.get("auto_reply_candidates") or []
    _attach_solidset_instance(candidates, instance)

    upstream_base = str(instance.get("NotificationUrl") or "").rstrip("/")
    if not upstream_base:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "A instância não tem NotificationUrl configurado para reencaminhar a mensagem.",
                "capture": capture,
            },
        )

    upstream_url = f"{upstream_base}/frameworkHub/SendMessage"
    excluded_headers = {"host", "content-length", "connection", "transfer-encoding"}
    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in excluded_headers
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.NOTIF_API_TIMEOUT_SECONDS,
            verify=settings.NOTIF_API_VERIFY_TLS,
            follow_redirects=False,
        ) as client:
            upstream = await client.post(
                upstream_url,
                content=raw_body,
                headers=forward_headers,
                params=dict(request.query_params),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "A mensagem foi capturada, mas não foi possível reencaminhá-la para o SolidSET.",
                "capture": capture,
            },
        ) from exc

    # Este proxy es con frecuencia la primera entrada del mensaje. Debe programar
    # aquí la respuesta porque la notificación posterior tendrá la misma huella y
    # será correctamente descartada como duplicada. Solo se responde si SolidSET
    # aceptó primero el mensaje original.
    if upstream.status_code < 400 and candidates:
        _schedule_auto_replies(candidates)
        print(
            f"📥 FrameworkHub reenviado status={upstream.status_code} "
            f"respuestas_programadas={len(candidates)}"
        )

    response_headers = {}
    if upstream.headers.get("content-type"):
        response_headers["content-type"] = upstream.headers["content-type"]
    response_headers["X-Agent-Capture-Learned"] = str(capture["learned"])
    response_headers["X-Agent-Replies-Scheduled"] = str(
        len(candidates) if upstream.status_code < 400 else 0
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )

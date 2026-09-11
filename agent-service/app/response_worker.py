from __future__ import annotations

import asyncio
import json
import os
import redis

from app.config import settings
from app.connectors.db_client import (
    ensure_agent_response_audit_schema,
    save_agent_response_audit,
)
from app.container import notification_listener
from app.services.auto_reply import _process_auto_replies
from app.services.instance_resolution import _attach_solidset_instance
from app.services.response_status import update as _update_response_status
from app.services.response_status import load as _load_response_status
from app.response_queue import AgentResponseQueue
from app.interactive_priority import async_interactive_work


async def run_worker() -> None:
    queue = AgentResponseQueue()
    await asyncio.to_thread(ensure_agent_response_audit_schema)
    consumer = (
        os.getenv("AGENT_RESPONSE_CONSUMER_NAME") or queue.default_consumer_name()
    )
    print(f"🛠️ Agent response worker activo consumer={consumer}", flush=True)
    while True:
        try:
            messages = await asyncio.to_thread(queue.read, consumer)
        except redis.RedisError as exc:
            print(f"⚠️ Redis Stream temporalmente no disponible: {exc}", flush=True)
            await asyncio.sleep(2)
            queue = AgentResponseQueue()
            continue
        for message_id, fields in messages:
            request_id = str(fields.get("request_id") or "")
            chat_id = str(fields.get("chat_id") or "")
            attempt = int(fields.get("attempt") or 0)
            payload = {}
            instance = {}
            candidates = []
            try:
                payload = json.loads(fields.get("payload") or "{}")
                instance = json.loads(fields.get("instance") or "{}")
                cached_candidates = fields.get("candidates") or ""
                if cached_candidates:
                    candidates = json.loads(cached_candidates)
                else:
                    async with async_interactive_work("notification-capture"):
                        capture = await asyncio.to_thread(
                            notification_listener.capture_realtime_payload, payload,
                        )
                    if capture.get("errors"):
                        raise RuntimeError(
                            f"Falló la captura: {capture['errors']} error(es)."
                        )
                    candidates = capture.get("auto_reply_candidates") or []
                    _attach_solidset_instance(candidates, instance)
                for candidate in candidates:
                    candidate["response_request_id"] = request_id
                async with async_interactive_work("agent-response-worker"):
                    result = await _process_auto_replies(candidates)
                if candidates and int(result) == 0:
                    raise RuntimeError("Ningún agente pudo completar el envío.")
                try:
                    final_status = _load_response_status(request_id) or {}
                    await asyncio.to_thread(
                        save_agent_response_audit,
                        request_id,
                        chat_id,
                        final_status.get("status") or ("completed" if int(result) > 0 or not candidates else "failed"),
                        int(final_status.get("responseCount", int(result))),
                        final_status.get("error"),
                        None,
                        {"responseCount": int(final_status.get("responseCount", int(result))),
                         "acceptedCount": int(result)},
                    )
                except Exception as audit_exc:
                    # Una respuesta ya enviada nunca se reintenta por un fallo
                    # exclusivo de auditoría, pues duplicaría el chat.
                    print(f"⚠️ Auditoría PostgreSQL pendiente: {audit_exc}", flush=True)
                await asyncio.to_thread(queue.acknowledge, message_id)
            except Exception as exc:
                if attempt < settings.AGENT_RESPONSE_MAX_RETRIES:
                    _update_response_status(
                        request_id,
                        "queued",
                        error=f"Reintento {attempt + 1}: {exc}",
                    )
                    await asyncio.to_thread(
                        queue.enqueue,
                        request_id,
                        chat_id,
                        payload,
                        instance,
                        attempt + 1,
                        candidates,
                    )
                else:
                    _update_response_status(request_id, "failed", error=str(exc))
                    try:
                        await asyncio.to_thread(
                            save_agent_response_audit,
                            request_id,
                            chat_id,
                            "failed",
                            0,
                            str(exc),
                            None,
                            {"attempts": attempt + 1},
                        )
                    except Exception as audit_exc:
                        print(f"⚠️ No se pudo auditar fallo terminal: {audit_exc}")
                await asyncio.to_thread(queue.acknowledge, message_id)


if __name__ == "__main__":
    asyncio.run(run_worker())

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress

import redis
from fastapi import HTTPException

from app.config import settings
from app.container import agent as _configured_agent  # noqa: F401 - configures shared runtime
from app.api.schemas.common import FrameworkMessageDTO
from app.services.response_status import load as _load_response_status
from app.services.response_status import update as _update_response_status
from app.services.suggestions import _process_chat_question_response_suggestion
from app.suggestion_queue import SuggestionQueue


async def _renew_delivery(queue: SuggestionQueue, message_id: str, consumer: str) -> None:
    interval = min(30, settings.SUGGESTION_CLAIM_IDLE_MS / 3000)
    while True:
        await asyncio.sleep(interval)
        try:
            if not await asyncio.to_thread(queue.renew, message_id, consumer):
                print("SUGGESTION_LEASE_LOST", flush=True)
                return
        except redis.RedisError:
            print("SUGGESTION_LEASE_RENEW_FAILED", flush=True)


async def run_worker() -> None:
    queue = SuggestionQueue()
    consumer = os.getenv("SUGGESTION_CONSUMER_NAME") or queue.default_consumer_name()
    print(f"🛠️ Suggestion worker activo consumer={consumer}", flush=True)
    while True:
        try:
            messages = await asyncio.to_thread(queue.read, consumer)
        except redis.RedisError as exc:
            print(
                f"⚠️ Redis de sugerencias temporalmente no disponible: {exc}", flush=True
            )
            await asyncio.sleep(2)
            queue = SuggestionQueue()
            continue
        for message_id, fields in messages:
            request_id = str(fields.get("request_id") or "")
            attempt = int(fields.get("attempt") or 0)
            payload: dict = {}
            instance: dict = {}
            heartbeat = asyncio.create_task(_renew_delivery(queue, message_id, consumer))
            try:
                status = _load_response_status(request_id) or {}
                if status.get("status") == "completed":
                    await asyncio.to_thread(queue.acknowledge, message_id)
                    continue
                payload = json.loads(fields.get("payload") or "{}")
                instance = json.loads(fields.get("instance") or "{}")
                message = FrameworkMessageDTO.model_validate(payload)
                await _process_chat_question_response_suggestion(message, instance)
                await asyncio.to_thread(queue.acknowledge, message_id)

            except (HTTPException, Exception) as exc:
                detail = getattr(exc, "detail", None) or str(exc)
                terminal_output_error = isinstance(exc, HTTPException) and exc.status_code in {400, 404, 422, 502}
                print(
                    f"SUGGESTION_WORKER_ERROR request_id={request_id} attempt={attempt} "
                    f"type={type(exc).__name__} retryable={not terminal_output_error}",
                    flush=True,
                )
                if not terminal_output_error and attempt < settings.SUGGESTION_MAX_RETRIES:
                    _update_response_status(
                        request_id, "queued", error=f"Reintento {attempt + 1}: {detail}"
                    )
                    await asyncio.to_thread(
                        queue.enqueue, request_id, payload, instance, attempt + 1
                    )
                else:
                    _update_response_status(
                        request_id, "failed", error=str(detail),
                        result={"httpStatus": exc.status_code if isinstance(exc, HTTPException) else 503},
                    )
                await asyncio.to_thread(queue.acknowledge, message_id)
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat



if __name__ == "__main__":
    asyncio.run(run_worker())

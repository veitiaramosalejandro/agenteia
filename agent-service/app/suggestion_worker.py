from __future__ import annotations

import asyncio
import json
import os

import redis
from fastapi import HTTPException

from app.config import settings
from app.container import agent as _configured_agent  # noqa: F401 - configures shared runtime
from app.api.schemas.common import FrameworkMessageDTO
from app.services.response_status import load as _load_response_status
from app.services.response_status import update as _update_response_status
from app.services.suggestions import _process_chat_question_response_suggestion
from app.suggestion_queue import SuggestionQueue


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
                if attempt < settings.SUGGESTION_MAX_RETRIES:
                    _update_response_status(
                        request_id, "queued", error=f"Reintento {attempt + 1}: {detail}"
                    )
                    await asyncio.to_thread(
                        queue.enqueue, request_id, payload, instance, attempt + 1
                    )
                else:
                    _update_response_status(request_id, "failed", error=str(detail))
                await asyncio.to_thread(queue.acknowledge, message_id)


if __name__ == "__main__":
    asyncio.run(run_worker())

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import redis

from app.config import settings


class SolidSETRetryQueue:
    """Cola Redis diferida para entregas ya generadas hacia SolidSET."""

    def __init__(self) -> None:
        self.client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=settings.AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        self.key = settings.SOLIDSET_RETRY_QUEUE_KEY
        self.processing_key = f"{self.key}:processing"

    def enqueue(
        self,
        arguments: dict[str, Any],
        *,
        error: str,
        retry_at: float | None = None,
        item_id: str | None = None,
        attempts: int = 0,
    ) -> str:
        identifier = item_id or str(uuid.uuid4())
        item = {
            "id": identifier,
            "arguments": arguments,
            "attempts": attempts,
            "last_error": error[:1000],
            "created_at": int(time.time()),
        }
        # Los trabajos nuevos quedan listos para el próximo ciclo del worker,
        # que se ejecuta cada cinco minutos.
        score = retry_at if retry_at is not None else time.time()
        self.client.zadd(self.key, {json.dumps(item, ensure_ascii=False, default=str): score})
        return identifier

    def claim_due(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Mueve atómicamente trabajos vencidos a processing para evitar dobles envíos."""
        count = limit or settings.SOLIDSET_RETRY_BATCH_SIZE
        now = time.time()
        claimed: list[dict[str, Any]] = []
        for raw in self.client.zrangebyscore(self.key, "-inf", now, start=0, num=count):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(self.key)
                    if pipe.zscore(self.key, raw) is None:
                        pipe.unwatch()
                        continue
                    pipe.multi()
                    pipe.zrem(self.key, raw)
                    pipe.hset(self.processing_key, json.loads(raw)["id"], raw)
                    removed, _ = pipe.execute()
                    if removed:
                        claimed.append(json.loads(raw))
                except redis.WatchError:
                    continue
        return claimed

    def acknowledge(self, item_id: str) -> None:
        self.client.hdel(self.processing_key, item_id)

    def retry(self, item: dict[str, Any], error: str) -> None:
        self.acknowledge(str(item["id"]))
        self.enqueue(
            item["arguments"],
            error=error,
            retry_at=time.time() + settings.SOLIDSET_RETRY_INTERVAL_SECONDS,
            item_id=str(item["id"]),
            attempts=int(item.get("attempts") or 0) + 1,
        )

    def recover_processing(self) -> int:
        """Recupera entregas abandonadas después de un reinicio del worker."""
        recovered = 0
        for item_id, raw in self.client.hgetall(self.processing_key).items():
            item = json.loads(raw)
            self.enqueue(
                item["arguments"], error="Worker reiniciado durante el envío",
                item_id=item_id, attempts=int(item.get("attempts") or 0),
            )
            self.client.hdel(self.processing_key, item_id)
            recovered += 1
        return recovered

    def pending_count(self) -> int:
        return int(self.client.zcard(self.key))

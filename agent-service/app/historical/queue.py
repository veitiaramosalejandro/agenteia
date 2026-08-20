from __future__ import annotations

import json
import os
import socket
from typing import Any
import redis
from app.config import settings


class HistoricalQueue:
    def __init__(self) -> None:
        self.client = redis.Redis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_connect_timeout=5,
            socket_timeout=settings.AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS,
            health_check_interval=30, retry_on_timeout=True,
        )
        self.stream = settings.HISTORICAL_INGESTION_STREAM
        self.group = settings.HISTORICAL_INGESTION_GROUP

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc): raise

    def enqueue(self, batch: dict[str, Any], attempt: int = 0) -> str:
        self.ensure_group()
        return str(self.client.xadd(self.stream, {
            "batch": json.dumps(batch, ensure_ascii=False, default=str),
            "attempt": str(attempt),
        }, maxlen=settings.HISTORICAL_INGESTION_STREAM_MAXLEN, approximate=True))

    def read(self, consumer: str) -> list[tuple[str, dict[str, str]]]:
        try:
            self.ensure_group()
            claimed = self.client.xautoclaim(
                self.stream, self.group, consumer,
                min_idle_time=settings.HISTORICAL_INGESTION_CLAIM_IDLE_MS,
                start_id="0-0", count=1)
            if claimed and claimed[1]: return list(claimed[1])
            response = self.client.xreadgroup(
                self.group, consumer, {self.stream: ">"}, count=1, block=5000)
            return [(mid, fields) for _, messages in response for mid, fields in messages]
        except redis.TimeoutError:
            return []

    def ack(self, message_id: str) -> None:
        self.client.xack(self.stream, self.group, message_id)

    def paused(self) -> bool:
        return self.client.get("machining:historical-ingestion:paused") == "1"

    def set_paused(self, value: bool) -> None:
        self.client.set("machining:historical-ingestion:paused", "1" if value else "0")

    def stats(self) -> dict[str, Any]:
        self.ensure_group(); groups=self.client.xinfo_groups(self.stream)
        group=next((g for g in groups if g.get("name")==self.group), {})
        return {"stream":self.stream,"length":self.client.xlen(self.stream),
                "pending":group.get("pending",0),"consumers":group.get("consumers",0),
                "lag":group.get("lag",0),"paused":self.paused()}

    @staticmethod
    def consumer_name() -> str:
        return f"{socket.gethostname()}-{os.getpid()}"

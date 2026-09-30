from __future__ import annotations

from app.redis_runtime import redis_client

import json
import socket
import threading
from contextlib import contextmanager
from typing import Any

import redis

from app.config import settings
from app.redis_runtime import enqueue_stream, acknowledge_stream


class AgentResponseQueue:
    """Cola durable de respuestas basada en Redis Streams."""

    def __init__(self) -> None:
        self.client = redis_client(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=settings.AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        self.stream = settings.AGENT_RESPONSE_STREAM
        self.group = settings.AGENT_RESPONSE_CONSUMER_GROUP

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue(
        self,
        request_id: str,
        chat_id: str,
        payload: dict[str, Any],
        instance: dict[str, Any],
        attempt: int = 0,
        candidates: list[dict[str, Any]] | None = None,
    ) -> str:
        self.ensure_group()
        return str(enqueue_stream(self.client, 
            self.stream,
            {
                "request_id": request_id,
                "chat_id": chat_id,
                "attempt": str(attempt),
                "payload": json.dumps(payload, ensure_ascii=False, default=str),
                "instance": json.dumps(instance, ensure_ascii=False, default=str),
                "candidates": json.dumps(candidates, ensure_ascii=False, default=str)
                if candidates is not None else "",
            },
            maxlen=settings.AGENT_RESPONSE_STREAM_MAXLEN,
            approximate=True,
        ))

    def read(self, consumer: str, block_ms: int = 5000) -> list[tuple[str, dict[str, str]]]:
        try:
            self.ensure_group()
            try:
                claimed = self.client.xautoclaim(
                    self.stream,
                    self.group,
                    consumer,
                    min_idle_time=settings.AGENT_RESPONSE_CLAIM_IDLE_MS,
                    start_id="0-0",
                    count=1,
                )
                claimed_messages = claimed[1] if claimed and len(claimed) > 1 else []
                if claimed_messages:
                    return [(message_id, fields) for message_id, fields in claimed_messages]
                response = self.client.xreadgroup(
                    self.group, consumer, {self.stream: ">"}, count=1, block=block_ms
                )
            except redis.ResponseError as exc:
                if "NOGROUP" in str(exc):
                    # El grupo o el stream desaparecieron (ej: FLUSHDB). Recrear y reintentar una vez.
                    self.ensure_group()
                    response = self.client.xreadgroup(
                        self.group, consumer, {self.stream: ">"}, count=1, block=block_ms
                    )
                else:
                    raise
        except redis.TimeoutError:

            # XREADGROUP es una espera larga. Un timeout sin mensajes no debe
            # finalizar el worker ni provocar el reinicio del contenedor.
            return []
        if not response:
            return []
        return [(message_id, fields) for _, messages in response for message_id, fields in messages]

    def acknowledge(self, message_id: str) -> None:
        acknowledge_stream(self.client, self.stream, self.group, message_id)

    def claim_request(self, request_id: str) -> bool:
        """Claim one logical request across duplicate stream entries."""
        key = f"agent-response:done:{request_id}"
        return bool(self.client.set(key, "1", nx=True, ex=86400))

    def release_request(self, request_id: str) -> None:
        self.client.delete(f"agent-response:done:{request_id}")

    def refresh_pending(self, consumer: str, message_id: str) -> bool:
        """Refresh only our pending entry, atomically, without stealing it back."""
        return bool(self.client.eval(
            "local p=redis.call('XPENDING',KEYS[1],ARGV[1],ARGV[3],ARGV[3],1) "
            "if #p == 0 or p[1][2] ~= ARGV[2] then return 0 end "
            "local ids=redis.call('XCLAIM',KEYS[1],ARGV[1],ARGV[2],0,ARGV[3],'JUSTID') "
            "return #ids",
            1, self.stream, self.group, consumer, message_id,
        ))

    @contextmanager
    def keep_pending_alive(self, consumer: str, message_id: str):
        """Keep long-running jobs out of XAUTOCLAIM, even during blocking work.

        A dead process stops renewing, so abandoned jobs remain recoverable.
        The guard covers capture, inference, delivery, audit and acknowledgement.
        """
        try:
            owned = self.refresh_pending(consumer, message_id)
        except redis.RedisError as exc:
            print(f"AGENT_RESPONSE_HEARTBEAT_ERROR message={message_id} type={type(exc).__name__}", flush=True)
            owned = False
        if not owned:
            yield False
            return
        stopped = threading.Event()
        interval = max(0.01, min(30.0, settings.AGENT_RESPONSE_CLAIM_IDLE_MS / 3000))

        def renew() -> None:
            while not stopped.wait(interval):
                try:
                    if not self.refresh_pending(consumer, message_id):
                        print(f"AGENT_RESPONSE_OWNERSHIP_LOST message={message_id} consumer={consumer}", flush=True)
                        return
                except redis.RedisError as exc:
                    print(f"AGENT_RESPONSE_HEARTBEAT_ERROR message={message_id} type={type(exc).__name__}", flush=True)

        heartbeat = threading.Thread(target=renew, name="agent-response-heartbeat", daemon=True)
        heartbeat.start()
        try:
            yield True
        finally:
            stopped.set()
            heartbeat.join(timeout=settings.AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS + 2)

    def stats(self) -> dict[str, Any]:
        self.ensure_group()
        groups = self.client.xinfo_groups(self.stream)
        group = next((item for item in groups if item.get("name") == self.group), {})
        return {
            "stream": self.stream,
            "group": self.group,
            "length": int(self.client.xlen(self.stream)),
            "pending": int(group.get("pending") or 0),
            "consumers": int(group.get("consumers") or 0),
            "lag": int(group.get("lag") or 0),
        }

    @staticmethod
    def default_consumer_name() -> str:
        return f"{socket.gethostname()}-{__import__('os').getpid()}"

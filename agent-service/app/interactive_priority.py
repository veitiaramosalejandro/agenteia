"""Prioridad cooperativa para que la ingesta no degrade respuestas interactivas."""

from __future__ import annotations

import socket
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

import redis

from app.config import settings


_KEY = "machining:interactive-work:v1"


def _client() -> redis.Redis:
    return redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=2,
        health_check_interval=30,
    )


def _prune_and_count(client: redis.Redis) -> int:
    now = time.time()
    pipeline = client.pipeline(transaction=True)
    pipeline.zremrangebyscore(_KEY, "-inf", now)
    pipeline.zcard(_KEY)
    return int(pipeline.execute()[-1] or 0)


def interactive_work_active() -> bool:
    """Fail-open: una caída de Redis no debe detener una ingesta indefinidamente."""
    try:
        return _prune_and_count(_client()) > 0
    except redis.RedisError:
        return False


def wait_for_interactive_idle() -> float:
    """Espera entre lotes hasta que no haya solicitudes interactivas activas."""
    if not settings.INGESTION_PAUSE_DURING_INTERACTIVE:
        return 0.0
    started = time.monotonic()
    while interactive_work_active():
        time.sleep(settings.INGESTION_INTERACTIVE_POLL_SECONDS)
    return time.monotonic() - started


@contextmanager
def interactive_work(kind: str = "response") -> Iterator[None]:
    """Publica un lease renovable compartido por API y workers."""
    if not settings.INTERACTIVE_PRIORITY_ENABLED:
        yield
        return
    client = _client()
    lease_id = f"{socket.gethostname()}:{kind}:{uuid.uuid4().hex}"
    stop = threading.Event()

    def renew() -> None:
        while not stop.wait(settings.INTERACTIVE_PRIORITY_HEARTBEAT_SECONDS):
            try:
                client.zadd(
                    _KEY,
                    {lease_id: time.time() + settings.INTERACTIVE_PRIORITY_LEASE_SECONDS},
                )
            except redis.RedisError:
                pass

    registered = False
    try:
        client.zadd(
            _KEY,
            {lease_id: time.time() + settings.INTERACTIVE_PRIORITY_LEASE_SECONDS},
        )
        registered = True
    except redis.RedisError:
        pass
    heartbeat = threading.Thread(target=renew, daemon=True)
    heartbeat.start()
    try:
        yield
    finally:
        stop.set()
        heartbeat.join(timeout=1)
        if registered:
            try:
                client.zrem(_KEY, lease_id)
            except redis.RedisError:
                pass


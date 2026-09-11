"""Prioridad cooperativa para que la ingesta no degrade respuestas interactivas."""

from __future__ import annotations

from app.redis_runtime import redis_client

import asyncio
import os
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import Iterator

import redis

from app.config import settings


_KEY = "machining:interactive-work:v1"
_local_condition = threading.Condition()
_local_active = 0
_local_idle_until = 0.0
_background_stop: ContextVar[threading.Event | None] = ContextVar("background_stop", default=None)


class IngestionCancelled(RuntimeError):
    """The current background run must stop before starting another operation."""


class IngestionBusy(RuntimeError):
    """Another process already owns the background ingestion mutex."""


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    return redis_client(
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
    """Pause background work if activity is present or Redis cannot prove idle."""
    with _local_condition:
        if _local_active or time.monotonic() < _local_idle_until:
            return True
    try:
        return _prune_and_count(_client()) > 0
    except redis.RedisError:
        return True


def wait_for_interactive_idle() -> float:
    """Resume after the last request's idle grace; honor background cancellation."""
    stop = _background_stop.get()
    if stop is not None and stop.is_set():
        raise IngestionCancelled("Ingesta cancelada antes de la siguiente operación.")
    if not settings.INGESTION_PAUSE_DURING_INTERACTIVE or not settings.INTERACTIVE_PRIORITY_ENABLED:
        return 0.0
    started = time.monotonic()
    paused = False
    while interactive_work_active():
        if not paused:
            print(
                "INGESTION_PAUSED reason=interactive_or_priority_store_unavailable "
                f"idle_seconds={settings.INGESTION_INTERACTIVE_IDLE_SECONDS}", flush=True,
            )
            paused = True
        if stop is not None and stop.is_set():
            raise IngestionCancelled("Ingesta cancelada mientras cedía prioridad al chat.")
        with _local_condition:
            _local_condition.wait(settings.INGESTION_INTERACTIVE_POLL_SECONDS)
    if stop is not None and stop.is_set():
        raise IngestionCancelled("Ingesta cancelada antes de reanudar.")
    waited = time.monotonic() - started
    if paused:
        print(f"INGESTION_RESUMED waited_seconds={waited:.2f}", flush=True)
    return waited


def background_checkpoint() -> None:
    """No-op in interactive paths; background callers yield before external I/O."""
    if _background_stop.get() is not None:
        wait_for_interactive_idle()


def background_io_options() -> dict:
    """Bound in-flight background I/O; interactive clients retain their settings."""
    return {"timeout": 10} if _background_stop.get() is not None else {}


def lower_background_thread_priority() -> None:
    """Lower only the dedicated worker thread on Linux, never the API process."""
    if hasattr(os, "setpriority"):
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except OSError:
            pass


@contextmanager
def background_ingestion(stop: threading.Event | None = None) -> Iterator[None]:
    """Serialize DB study across processes; chat never acquires this mutex."""
    if _background_stop.get() is not None:
        background_checkpoint()
        yield
        return
    stop = stop if stop is not None else threading.Event()
    token = _background_stop.set(stop)
    lock = _client().lock("machining:db-study:mutex:v1", timeout=45, thread_local=False)
    heartbeat_stop = threading.Event()
    heartbeat = None
    acquired = False

    def renew() -> None:
        while not heartbeat_stop.wait(10):
            try:
                lock.reacquire()
            except redis.RedisError:
                stop.set()
                return

    try:
        background_checkpoint()
        acquired = lock.acquire(blocking=False)
        if not acquired:
            raise IngestionBusy("Ya existe un ciclo DB_STUDY en ejecución.")
        heartbeat = threading.Thread(target=renew, daemon=True, name="db-study-lease")
        heartbeat.start()
        background_checkpoint()
        yield
    finally:
        heartbeat_stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=3)
        if acquired:
            try:
                lock.release()
            except redis.RedisError:
                pass
        _background_stop.reset(token)


@contextmanager
def interactive_work(kind: str = "response") -> Iterator[None]:
    """Publica un lease renovable compartido por API y workers."""
    if not settings.INTERACTIVE_PRIORITY_ENABLED:
        yield
        return
    global _local_active, _local_idle_until
    with _local_condition:
        _local_active += 1
        _local_condition.notify_all()
    client = _client()
    lease_id = f"{socket.gethostname()}:{kind}:{uuid.uuid4().hex}"
    stop = threading.Event()

    def renew() -> None:
        interval = min(
            settings.INTERACTIVE_PRIORITY_HEARTBEAT_SECONDS,
            settings.INTERACTIVE_PRIORITY_LEASE_SECONDS / 3,
        )
        while not stop.wait(interval):
            try:
                client.zadd(
                    _KEY,
                    {lease_id: time.time() + settings.INTERACTIVE_PRIORITY_LEASE_SECONDS},
                )
            except redis.RedisError:
                pass

    try:
        client.zadd(
            _KEY,
            {lease_id: time.time() + settings.INTERACTIVE_PRIORITY_LEASE_SECONDS},
        )
    except redis.RedisError:
        pass
    heartbeat = threading.Thread(target=renew, daemon=True)
    heartbeat.start()
    try:
        yield
    finally:
        stop.set()
        heartbeat.join(timeout=3)
        # Keep the lease for the quiet period, including when initial registration
        # failed but a subsequent heartbeat succeeded.
        with _local_condition:
            _local_active -= 1
            _local_idle_until = time.monotonic() + settings.INGESTION_INTERACTIVE_IDLE_SECONDS
            _local_condition.notify_all()
        try:
            client.zadd(_KEY, {lease_id: time.time() + settings.INGESTION_INTERACTIVE_IDLE_SECONDS})
        except redis.RedisError:
            pass


@asynccontextmanager
async def async_interactive_work(kind: str = "response"):
    """Keep synchronous Redis registration/cleanup off the event loop."""
    manager = interactive_work(kind)
    entered = asyncio.create_task(asyncio.to_thread(manager.__enter__))
    try:
        await asyncio.shield(entered)
    except asyncio.CancelledError:
        await entered
        await asyncio.to_thread(manager.__exit__, None, None, None)
        raise
    try:
        yield
    finally:
        await asyncio.shield(asyncio.to_thread(manager.__exit__, None, None, None))

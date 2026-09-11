from __future__ import annotations

from app.redis_runtime import redis_client

import hashlib
import threading
from collections import OrderedDict
from time import time
from typing import Optional

import redis
from fastapi import FastAPI

from app.config import settings


_app: FastAPI | None = None
_active = 0
_active_lock = threading.Lock()
slots = threading.BoundedSemaphore(value=max(1, settings.DIALOGUE_MAX_CONCURRENT))
_cache_lock = threading.Lock()
_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_redis = redis_client(settings.REDIS_URL, decode_responses=True)
_metrics_lock = threading.Lock()
_metrics = {
    "count": 0,
    "total_seconds": 0.0,
    "max_seconds": 0.0,
    "last_seconds": None,
    "cache_hits": 0,
}


def configure(app: FastAPI) -> None:
    global _app
    _app = app


def build_cache_key(
    session_id: str, user_id: str, canal_id: Optional[str], message: str
) -> str:
    values = (
        (session_id or "").strip().lower(),
        (user_id or "").strip().lower(),
        (canal_id or "").strip().lower(),
        " ".join((message or "").strip().lower().split()),
    )
    return "|".join(values)


def resolve_canal_id(canal_id: Optional[str], session_id: str) -> Optional[str]:
    if explicit := (canal_id or "").strip():
        return explicit
    candidate = (session_id or "").strip()
    return (
        None if not candidate or candidate.lower().startswith("session_") else candidate
    )


def get_cached(cache_key: str) -> Optional[str]:
    if not settings.DIALOGUE_DUPLICATE_CACHE_ENABLED:
        return None
    redis_key = f"{settings.DIALOGUE_REDIS_CACHE_PREFIX}:{hashlib.sha256(cache_key.encode()).hexdigest()}"
    try:
        if cached := _redis.get(redis_key):
            return cached
    except redis.RedisError as exc:
        print(f"⚠️ Cache Redis no disponible; usando cache local: {exc}")
    now = time()
    ttl = max(1, settings.DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS)
    with _cache_lock:
        item = _cache.get(cache_key)
        if not item:
            return None
        timestamp, response = item
        if now - timestamp > ttl:
            _cache.pop(cache_key, None)
            return None
        _cache.move_to_end(cache_key)
        return response


def store_cached(cache_key: str, response_text: str) -> None:
    if not settings.DIALOGUE_DUPLICATE_CACHE_ENABLED or not response_text:
        return
    redis_key = f"{settings.DIALOGUE_REDIS_CACHE_PREFIX}:{hashlib.sha256(cache_key.encode()).hexdigest()}"
    ttl = max(1, settings.DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS)
    try:
        _redis.setex(redis_key, ttl, response_text)
        return
    except redis.RedisError as exc:
        print(f"⚠️ No se pudo escribir cache Redis; usando cache local: {exc}")
    with _cache_lock:
        _cache[cache_key] = (time(), response_text)
        _cache.move_to_end(cache_key)
        while len(_cache) > max(50, settings.DIALOGUE_DUPLICATE_CACHE_MAX_ITEMS):
            _cache.popitem(last=False)


def record_metrics(duration_seconds: float, cache_hit: bool = False) -> None:
    with _metrics_lock:
        _metrics["count"] += 1
        _metrics["total_seconds"] += duration_seconds
        _metrics["max_seconds"] = max(_metrics["max_seconds"], duration_seconds)
        _metrics["last_seconds"] = duration_seconds
        if cache_hit:
            _metrics["cache_hits"] += 1


def metrics_snapshot() -> dict:
    with _metrics_lock:
        count = int(_metrics["count"])
        total = float(_metrics["total_seconds"])
        return {
            "count": count,
            "avg_seconds": round(total / count, 3) if count else 0.0,
            "max_seconds": round(float(_metrics["max_seconds"]), 3),
            "last_seconds": round(float(_metrics["last_seconds"]), 3)
            if _metrics["last_seconds"] is not None
            else None,
            "cache_hits": int(_metrics["cache_hits"]),
            "cache_size": len(_cache),
            "cache_enabled": settings.DIALOGUE_DUPLICATE_CACHE_ENABLED,
            "cache_ttl_seconds": settings.DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS,
        }


def start() -> None:
    global _active
    with _active_lock:
        _active += 1


def finish() -> None:
    global _active
    with _active_lock:
        _active = max(0, _active - 1)


def active_count() -> int:
    with _active_lock:
        return _active


def release_when_done(worker: threading.Thread) -> None:
    try:
        worker.join(timeout=max(5, settings.DIALOGUE_TIMEOUT_RELEASE_GRACE_SECONDS))
        if worker.is_alive():
            print("⚠️ Worker de diálogo sigue activo tras timeout y periodo de gracia.")
    finally:
        finish()
        if _app is not None:
            _app.state.active_dialogues = active_count()
        try:
            slots.release()
        except ValueError:
            pass

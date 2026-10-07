"""Bounded Redis I/O and retention for transient application data."""

import asyncio

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config import settings


def redis_client(url=None, **kwargs):
    options = dict(
        decode_responses=True, socket_connect_timeout=1, socket_timeout=2,
        health_check_interval=30, max_connections=16,
    )
    options.update(kwargs)
    # Retrying a socket timeout can turn a bounded startup probe into a long wait.
    options.update(retry=Retry(NoBackoff(), 0), retry_on_timeout=False)
    return redis.Redis.from_url(url or settings.REDIS_URL, **options)


def require_redis() -> None:
    client = redis_client(socket_connect_timeout=1, socket_timeout=1)
    try:
        if not client.ping():
            raise RuntimeError("Redis no respondió al PING.")
    except redis.RedisError:
        raise RuntimeError("Redis no está disponible; arranque/health rechazado.") from None
    finally:
        client.close()


def enqueue_stream(client, stream, fields, *, maxlen, approximate=False):
    """Apply backpressure instead of evicting unprocessed entries with MAXLEN."""
    args = [maxlen]
    for key, value in fields.items():
        args.extend((key, value))
    return client.eval(
        "if redis.call('XLEN',KEYS[1]) >= tonumber(ARGV[1]) then "
        "return redis.error_reply('QUEUE_FULL: cola llena; reintente mas tarde') end "
        "local id=redis.call('XADD',KEYS[1],'*',unpack(ARGV,2)) "
        "redis.call('PERSIST',KEYS[1]); return id",
        1, stream, *args,
    )


def acknowledge_stream(client, stream, group, message_id):
    """Delete confirmed payloads only for the application's single-group queues."""
    client.eval(
        "local ack=redis.call('XACK',KEYS[1],ARGV[1],ARGV[2]) "
        "if ack == 1 and #redis.call('XINFO','GROUPS',KEYS[1]) == 1 then "
        "redis.call('XDEL',KEYS[1],ARGV[2]) end "
        "if redis.call('XLEN',KEYS[1]) == 0 then redis.call('EXPIRE',KEYS[1],86400) end "
        "return ack", 1, stream, group, message_id,
    )


def _clean_stream(client, stream):
    """Trim old acknowledged data, preserving every group's pending/unread range."""
    try:
        groups = client.xinfo_groups(stream)
    except redis.ResponseError:
        return
    if not groups:
        return
    watermarks = []
    for group in groups:
        pending = client.xpending_range(stream, group["name"], "-", "+", 1)
        watermarks.append(pending[0]["message_id"] if pending else group["last-delivered-id"])
        # Check and remove idle, empty consumers in one server-side operation.
        client.eval(
            "for _,c in ipairs(redis.call('XINFO','CONSUMERS',KEYS[1],ARGV[1])) do "
            "local d={} for i=1,#c,2 do d[c[i]]=c[i+1] end "
            "if d.pending == 0 and d.idle > 86400000 then "
            "redis.call('XGROUP','DELCONSUMER',KEYS[1],ARGV[1],d.name) end end return 1",
            1, stream, group["name"],
        )
    boundary = min(watermarks, key=lambda value: tuple(int(part) for part in value.split("-")))
    if boundary != "0-0":
        client.xtrim(stream, minid=boundary, approximate=True, limit=1000)


def maintenance_pass(cursors: dict) -> None:
    """Small SCAN pages; never KEYS, FLUSHDB or deletion of unknown namespaces."""
    client = redis_client()
    try:
        for stream in (settings.AGENT_RESPONSE_STREAM, settings.SUGGESTION_STREAM,
                       settings.HISTORICAL_INGESTION_STREAM):
            _clean_stream(client, stream)
        # Legacy conversation lists had neither retention nor a maximum length.
        cursor, keys = client.scan(cursors.get("history", 0), match="message_store:*", count=100)
        cursors["history"] = cursor
        for key in keys:
            client.eval(
                "if redis.call('TYPE',KEYS[1]).ok ~= 'list' then return 0 end "
                "redis.call('LTRIM',KEYS[1],0,tonumber(ARGV[1])-1) "
                "if redis.call('TTL',KEYS[1]) == -1 then redis.call('EXPIRE',KEYS[1],ARGV[2]) end "
                "return 1", 1, key, settings.REDIS_HISTORY_MAX_MESSAGES, settings.REDIS_HISTORY_TTL_SECONDS,
            )
        # Lease members orphaned by stopped processes expire by score.
        import time
        client.zremrangebyscore("machining:interactive-work:v1", "-inf", time.time())
    finally:
        client.close()


async def maintenance_loop():
    cursors = {}
    while True:
        await asyncio.sleep(settings.REDIS_MAINTENANCE_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(maintenance_pass, cursors)
        except redis.RedisError as exc:
            print(f"REDIS_MAINTENANCE deferred type={type(exc).__name__}", flush=True)

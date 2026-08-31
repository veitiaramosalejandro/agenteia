"""Worker durable para materializar conocimiento SQL hasta completar cada run."""

from __future__ import annotations

import socket
import threading
import time
import uuid
from typing import Any

from app.config import settings
from app.connectors.db_client import get_solidset_instance
from app.system.system_knowledge_ingest import (
    claim_next_run,
    heartbeat_run,
    retry_run,
    run_system_knowledge_ingestion,
)


def _heartbeat_loop(run_id: str, worker_id: str, stop: threading.Event) -> None:
    interval = max(10, min(60, settings.SYSTEM_KNOWLEDGE_STALE_SECONDS // 3))
    while not stop.wait(interval):
        try:
            heartbeat_run(run_id, worker_id)
        except Exception as exc:
            print(f"⚠️ Heartbeat de ingesta no disponible run={run_id}: {exc}", flush=True)


def _requested_tables(run: dict[str, Any]) -> list[str] | None:
    value = run.get("RequestedTables")
    return [str(item) for item in value] if isinstance(value, list) else None


def _retry_delay(error: Exception, attempts: int) -> int:
    """Los reinicios de runtimes locales no deben activar un backoff de 15 min."""
    detail = str(error).casefold()
    transient = (
        "server disconnected", "connection refused", "connection reset",
        "timed out", "timeout", "remote protocol error",
    )
    if any(marker in detail for marker in transient):
        return settings.SYSTEM_KNOWLEDGE_RETRY_SECONDS
    return min(
        900,
        settings.SYSTEM_KNOWLEDGE_RETRY_SECONDS * (2 ** min(attempts - 1, 4)),
    )


def run_worker() -> None:
    worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
    print(f"🧠 System knowledge worker activo worker={worker_id}", flush=True)
    while True:
        try:
            run = claim_next_run(worker_id, settings.SYSTEM_KNOWLEDGE_STALE_SECONDS)
        except Exception as exc:
            print(f"⚠️ No se pudo adquirir una ingesta: {exc}", flush=True)
            time.sleep(settings.SYSTEM_KNOWLEDGE_POLL_SECONDS)
            continue
        if not run:
            time.sleep(settings.SYSTEM_KNOWLEDGE_POLL_SECONDS)
            continue

        run_id = str(run["ID"])
        instance_code = str(run.get("InstanceCode") or "")
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop, args=(run_id, worker_id, stop), daemon=True,
        )
        heartbeat.start()
        try:
            instance = get_solidset_instance(code=instance_code, source_ip=None)
            if not instance:
                raise RuntimeError(f"Instancia SolidSET no disponible: {instance_code}")
            print(
                f"▶️ Ejecutando ingesta run={run_id} instance={instance_code} "
                f"attempt={run.get('AttemptCount')}", flush=True,
            )
            result = run_system_knowledge_ingestion(
                run_id, instance, _requested_tables(run),
            )
            print(
                f"✅ Ingesta completada run={run_id} rows={result.get('RowsRead', 0)}",
                flush=True,
            )
        except Exception as exc:
            attempts = max(1, int(run.get("AttemptCount") or 1))
            delay = _retry_delay(exc, attempts)
            try:
                retry_run(run_id, str(exc), delay)
            except Exception as retry_exc:
                print(f"❌ No se pudo programar reintento run={run_id}: {retry_exc}", flush=True)
            print(f"🔁 Ingesta run={run_id} reintentará en {delay}s: {exc}", flush=True)
        finally:
            stop.set()
            heartbeat.join(timeout=2)


if __name__ == "__main__":
    run_worker()

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.config import settings
from app.connectors.db_client import list_active_solidset_instances
from app.historical.extractor import extract_batch
from app.historical.queue import HistoricalQueue
from app.historical.store import ensure_schema, get_cursor, set_cursor, upsert_audit


def enqueue_next_batch(instance: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    ensure_schema()
    instance_id = str(instance["ID"])
    cursor = get_cursor(instance_id)
    if cursor.get("Status") in {"queued", "processing"}:
        return {"status":"in_progress","batchId":None,"messages":0}
    if cursor.get("Status") == "dry_run" and not dry_run:
        return {"status":"requires_cursor_reset","batchId":None,"messages":0}
    if cursor.get("Status") == "dry_run" and dry_run:
        return {"status":"dry_run_completed","batchId":None,"messages":0}
    rows = extract_batch(int(cursor["LastIDChat2"]), settings.HISTORICAL_INGESTION_BATCH_SIZE)
    if not rows:
        set_cursor(instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"), "completed")
        return {"status":"completed","batchId":None,"messages":0}
    first_id, last_id = int(rows[0]["IDChat2"]), int(rows[-1]["IDChat2"])
    batch = {"batchId":f"{instance_id}:{first_id}:{last_id}", "instanceId":instance_id,
             "instanceCode":instance.get("Code"), "firstIdChat2":first_id,
             "lastIdChat2":last_id, "dryRun":dry_run, "messages":rows}
    upsert_audit(batch, "queued")
    HistoricalQueue().enqueue(batch)
    set_cursor(instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"), "queued")
    return {"status":"queued","batchId":batch["batchId"],"messages":len(rows)}


async def run_producer() -> None:
    ensure_schema(); queue=HistoricalQueue()
    while True:
        if settings.HISTORICAL_INGESTION_ENABLED and not queue.paused():
            for instance in list_active_solidset_instances():
                try: await asyncio.to_thread(enqueue_next_batch, instance, settings.HISTORICAL_INGESTION_DRY_RUN)
                except Exception as exc: print(f"⚠️ Productor histórico: {exc}", flush=True)
        await asyncio.sleep(settings.HISTORICAL_INGESTION_POLL_SECONDS)


if __name__ == "__main__": asyncio.run(run_producer())

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.config import settings
from app.connectors.db_client import list_active_solidset_instances
from app.historical.extractor import extract_agent_chat_batch, extract_agent_task_batch
from app.historical.queue import HistoricalQueue
from app.historical.store import (
    ensure_schema, get_cursor, list_active_ingestion_agents,
    recover_stale_cursors, set_cursor, upsert_audit,
)
from app.system.resource_ingest import verify_and_sync_solidset_agent_mapping


def verified_ingestion_agents() -> list[dict[str, Any]]:
    """Reconciles PostgreSQL targets against active SQL Server agent mappings."""
    verified: list[dict[str, Any]] = []
    for target in list_active_ingestion_agents():
        try:
            result = verify_and_sync_solidset_agent_mapping(
                target["IDResource"], target["IDAgentResource"]
            )
        except Exception as exc:
            print(
                f"⚠️ Verificação histórica do agente {target['IDResource']}: {exc}",
                flush=True,
            )
            continue
        if not result.get("verified") or not result.get("matchesExpected"):
            continue
        current = dict(target)
        current["IDAgentResource"] = result["IDAgentResource"]
        verified.append(current)
    return verified


def enqueue_next_batch(
    instance: dict[str, Any],
    dry_run: bool = False,
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_schema()
    if agent is None:
        results = []
        for target in verified_ingestion_agents():
            results.append({
                "resourceId": str(target["IDResource"]),
                "chat": enqueue_next_batch(instance, dry_run, target),
                "tasks": enqueue_next_task_batch(instance, dry_run, target),
            })
        return {
            "status": "reconciled",
            "agents": len(results),
            "queued": sum(
                1 for item in results for source in (item["chat"], item["tasks"])
                if source.get("status") == "queued"
            ),
            "results": results,
        }
    instance_id = str(instance["ID"])
    resource_id = str(agent["IDResource"])
    agent_resource_id = str(agent["IDAgentResource"])
    source = f"solidset_chat_history:{resource_id}"
    cursor = get_cursor(
        instance_id, source, resource_id=resource_id,
        agent_resource_id=agent_resource_id, source_type="chat",
    )
    if cursor.get("Status") in {"queued", "processing"}:
        return {"status":"in_progress","batchId":None,"messages":0}
    if cursor.get("Status") == "dry_run" and not dry_run:
        return {"status":"requires_cursor_reset","batchId":None,"messages":0}
    if cursor.get("Status") == "dry_run" and dry_run:
        return {"status":"dry_run_completed","batchId":None,"messages":0}
    rows = extract_agent_chat_batch(
        int(cursor["LastIDChat2"]), settings.HISTORICAL_INGESTION_BATCH_SIZE,
        resource_id, [str(value) for value in (agent.get("WorkRooms") or [])],
    )
    if not rows:
        set_cursor(
            instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"),
            "completed", source=source,
        )
        return {"status":"completed","batchId":None,"messages":0,"resourceId":resource_id}
    first_id, last_id = int(rows[0]["IDChat2"]), int(rows[-1]["IDChat2"])
    batch = {"batchId":f"{instance_id}:{resource_id}:chat:{first_id}:{last_id}", "instanceId":instance_id,
             "instanceCode":instance.get("Code"), "firstIdChat2":first_id,
             "lastIdChat2":last_id, "dryRun":dry_run, "messages":rows,
             "resourceId":resource_id, "agentResourceId":agent_resource_id,
             "sourceType":"chat", "cursorSource":source}
    upsert_audit(batch, "queued")
    set_cursor(
        instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"),
        "queued", batch_id=batch["batchId"], source=source,
    )
    try:
        HistoricalQueue().enqueue(batch)
    except Exception as exc:
        upsert_audit(batch, "failed", error=str(exc))
        set_cursor(
            instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"),
            "failed", str(exc), batch_id=None, source=source,
        )
        raise
    return {"status":"queued","batchId":batch["batchId"],"messages":len(rows)}


def enqueue_next_task_batch(
    instance: dict[str, Any],
    dry_run: bool,
    agent: dict[str, Any],
) -> dict[str, Any]:
    ensure_schema()
    instance_id = str(instance["ID"])
    resource_id = str(agent["IDResource"])
    agent_resource_id = str(agent["IDAgentResource"])
    source = f"solidset_task_history:{resource_id}"
    cursor = get_cursor(
        instance_id, source, resource_id=resource_id,
        agent_resource_id=agent_resource_id, source_type="task",
    )
    if cursor.get("Status") in {"queued", "processing"}:
        return {"status": "in_progress", "batchId": None, "messages": 0}
    if cursor.get("Status") == "dry_run":
        return {
            "status": "dry_run_completed" if dry_run else "requires_cursor_reset",
            "batchId": None, "messages": 0,
        }
    rows = extract_agent_task_batch(
        int(cursor["LastIDChat2"]), settings.HISTORICAL_INGESTION_BATCH_SIZE,
        resource_id,
    )
    if not rows:
        set_cursor(
            instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"),
            "completed", source=source,
        )
        return {"status": "completed", "batchId": None, "messages": 0, "resourceId": resource_id}
    first_id, last_id = int(rows[0]["IDTask"]), int(rows[-1]["IDTask"])
    batch = {
        "batchId": f"{instance_id}:{resource_id}:task:{first_id}:{last_id}",
        "instanceId": instance_id, "instanceCode": instance.get("Code"),
        "firstIdChat2": first_id, "lastIdChat2": last_id,
        "dryRun": dry_run, "messages": rows,
        "resourceId": resource_id, "agentResourceId": agent_resource_id,
        "sourceType": "task", "cursorSource": source,
    }
    upsert_audit(batch, "queued")
    set_cursor(
        instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"),
        "queued", batch_id=batch["batchId"], source=source,
    )
    try:
        HistoricalQueue().enqueue(batch)
    except Exception as exc:
        upsert_audit(batch, "failed", error=str(exc))
        set_cursor(
            instance_id, int(cursor["LastIDChat2"]), cursor.get("LastStamp"),
            "failed", str(exc), source=source,
        )
        raise
    return {"status": "queued", "batchId": batch["batchId"], "messages": len(rows)}


async def run_producer() -> None:
    ensure_schema(); queue=HistoricalQueue()
    while True:
        if settings.HISTORICAL_INGESTION_ENABLED and not queue.paused():
            try:
                recovered = await asyncio.to_thread(
                    recover_stale_cursors,
                    settings.HISTORICAL_INGESTION_STALE_SECONDS,
                )
                for cursor in recovered:
                    print(
                        "🔄 Cursor histórico recuperado após reinício "
                        f"instance={cursor['IDSolidSETInstance']} "
                        f"last_id={cursor['LastIDChat2']}",
                        flush=True,
                    )
            except Exception as exc:
                print(f"⚠️ Recuperação de cursor histórico: {exc}", flush=True)
            agents = await asyncio.to_thread(verified_ingestion_agents)
            for instance in list_active_solidset_instances():
                for target in agents:
                    try:
                        await asyncio.to_thread(
                            enqueue_next_batch, instance,
                            settings.HISTORICAL_INGESTION_DRY_RUN, target,
                        )
                    except Exception as exc:
                        print(f"⚠️ Productor histórico: {exc}", flush=True)
                    try:
                        await asyncio.to_thread(
                            enqueue_next_task_batch, instance,
                            settings.HISTORICAL_INGESTION_DRY_RUN, target,
                        )
                    except Exception as exc:
                        print(f"⚠️ Productor histórico SysTask: {exc}", flush=True)
        await asyncio.sleep(settings.HISTORICAL_INGESTION_POLL_SECONDS)


if __name__ == "__main__": asyncio.run(run_producer())

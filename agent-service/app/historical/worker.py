from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

import redis
from langchain_ollama import OllamaEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from app.config import settings
from app.historical.normalizer import normalize_historical_message, normalize_historical_task
from app.historical.queue import HistoricalQueue
from app.historical.store import (
    ensure_schema, historical_agent_is_active, save_document, set_cursor, upsert_audit,
)
from app.rag.vector_store import ensure_vector_collection


def _document(row: dict[str, Any], instance_id: str, scope: str, agent: dict[str, Any]) -> dict[str, Any]:
    resource_id = str(agent["IDResource"])
    room_id = str(row.get("IDWorkRoom") or "").strip()
    source_type = str(row.get("SourceType") or "chat")
    source_id = str(row.get("IDTask") or row["IDChat2"])
    key = f"solidset:{instance_id}:{source_type}:{source_id}:{scope}:{resource_id}:{room_id}"
    point_id = uuid.uuid5(uuid.NAMESPACE_URL, key)
    sender_name = str(row.get("FullName") or row.get("ResourceName") or row["IDSenderResource"])
    return {
        "DocumentID": point_id, "QdrantPointID": point_id,
        "IDChat2": int(row["IDChat2"]), "IDSolidSETInstance": uuid.UUID(instance_id),
        "Scope": scope, "IDResource": uuid.UUID(resource_id),
        "IDAgentResource": uuid.UUID(str(agent["IDAgentResource"])) if agent.get("IDAgentResource") else None,
        "IDWorkRoom": uuid.UUID(room_id) if room_id else None, "ContentHash": row["ContentHash"],
        "SourceType": source_type,
        "SourceID": source_id,
        "text": f"{sender_name}: {row['NormalizedText']}",
        "payload": {
            "page_content": f"{sender_name}: {row['NormalizedText']}",
            "document_type": "historical_task" if source_type == "task" else "historical_message",
            "source": "solidset_sql_history", "source_type": source_type,
            "source_id": source_id, "solidset_instance_id": instance_id,
            "id_chat2": int(row["IDChat2"]),
            "id_sender_resource": str(row["IDSenderResource"]), "id_workroom": room_id,
            "agent_resource_id": resource_id, "agent_identity_id": str(agent.get("IDAgentResource") or ""),
            "canal_id": room_id,
            "metadatos": {"agent_resource_id": resource_id, "historical": True},
            "scope": scope, "stamp": str(row.get("Stamp") or ""),
            "content_hash": row["ContentHash"], "generated_by_ia": False,
        },
    }


def process_batch(batch: dict[str, Any]) -> dict[str, int]:
    cursor_source = str(batch.get("cursorSource") or "solidset_sql_history")
    set_cursor(
        batch["instanceId"], batch["firstIdChat2"] - 1, None,
        "processing", batch_id=batch["batchId"], source=cursor_source,
    )
    resource_id = str(batch.get("resourceId") or "")
    agent_resource_id = str(batch.get("agentResourceId") or "")
    if not resource_id or not agent_resource_id or not historical_agent_is_active(
        resource_id, agent_resource_id, str(batch.get("instanceId") or "") or None
    ):
        upsert_audit(batch, "inactive", accepted=0, rejected=len(batch.get("messages") or []), indexed=0)
        set_cursor(
            batch["instanceId"], batch["firstIdChat2"] - 1, None,
            "inactive", source=cursor_source,
        )
        return {"accepted": 0, "rejected": len(batch.get("messages") or []), "indexed": 0}
    accepted = rejected = indexed = 0
    documents: list[dict[str, Any]] = []
    selected_agent = {"IDResource": resource_id, "IDAgentResource": agent_resource_id}
    for raw in batch.get("messages") or []:
        if batch.get("sourceType") == "task":
            row, reason = normalize_historical_task(raw, resource_id)
        else:
            row, reason = normalize_historical_message(raw)
        if reason:
            rejected += 1; continue
        if batch.get("sourceType") == "task":
            scope = "task"
        elif str(row["IDSenderResource"]).lower() == resource_id.lower():
            scope = "owner"
        elif row.get("IDMeeting"):
            scope = "meeting"
        elif int(row.get("WorkRoomKind") or 0) == 1:
            scope = "private"
        else:
            scope = "workroom"
        row["SourceType"] = str(batch.get("sourceType") or "chat")
        documents.append(_document(row, batch["instanceId"], scope, selected_agent))
        accepted += 1
    if batch.get("dryRun"):
        upsert_audit(batch, "dry_run", accepted=accepted, rejected=rejected, indexed=0)
        set_cursor(
            batch["instanceId"], batch["firstIdChat2"] - 1, None,
            "dry_run", source=cursor_source,
        )
        return {"accepted":accepted,"rejected":rejected,"indexed":0}
    if documents:
        embeddings = OllamaEmbeddings(base_url=settings.OLLAMA_BASE_URL, model=settings.EMBEDDING_MODEL_NAME)
        client = QdrantClient(url=settings.VECTOR_DB_URL)
        ensure_vector_collection(client, settings.VECTOR_COLLECTION_NAME, embeddings)
        vectors = embeddings.embed_documents([doc["text"] for doc in documents])
        client.upsert(collection_name=settings.VECTOR_COLLECTION_NAME, points=[
            PointStruct(id=str(doc["QdrantPointID"]), vector=vector, payload=doc["payload"])
            for doc, vector in zip(documents, vectors)
        ], wait=True)
        for doc in documents: save_document(doc)
        indexed = len(documents)
    last = (batch.get("messages") or [])[-1]
    set_cursor(
        batch["instanceId"], int(batch["lastIdChat2"]), last.get("Stamp"),
        "completed", source=cursor_source,
    )
    upsert_audit(batch, "completed", accepted=accepted, rejected=rejected, indexed=indexed)
    return {"accepted":accepted,"rejected":rejected,"indexed":indexed}


async def run_worker() -> None:
    ensure_schema(); queue=HistoricalQueue(); consumer=queue.consumer_name()
    print(f"📚 Historical worker activo consumer={consumer}", flush=True)
    while True:
        if queue.paused(): await asyncio.sleep(2); continue
        try: messages=await asyncio.to_thread(queue.read, consumer)
        except redis.RedisError as exc:
            print(f"⚠️ Redis histórico no disponible: {exc}"); await asyncio.sleep(2); queue=HistoricalQueue(); continue
        for message_id, fields in messages:
            batch=json.loads(fields["batch"]); attempt=int(fields.get("attempt") or 0)
            try:
                result=await asyncio.to_thread(process_batch, batch)
                await asyncio.to_thread(queue.ack, message_id)
                print(f"✅ Lote histórico {batch['batchId']}: {result}", flush=True)
            except Exception as exc:
                if attempt < settings.HISTORICAL_INGESTION_MAX_RETRIES:
                    await asyncio.to_thread(queue.enqueue, batch, attempt+1)
                else:
                    upsert_audit(batch, "failed", error=str(exc))
                    set_cursor(
                        batch["instanceId"], batch["firstIdChat2"]-1, None,
                        "failed", str(exc), source=str(batch.get("cursorSource") or "solidset_sql_history"),
                    )
                await asyncio.to_thread(queue.ack, message_id)


if __name__ == "__main__": asyncio.run(run_worker())

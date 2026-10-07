"""Index SQL record evidence, never model output, in a resource-scoped snapshot."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone

from app.config import settings

_index_slot = threading.BoundedSemaphore(1)


def schedule_record_snapshot(instance_id: str, resource_id: str, table: str,
                             record: dict, row: dict) -> None:
    if not instance_id or not resource_id or not row:
        return
    # Bound background embedding work on this worker. SQL remains authoritative.
    if not _index_slot.acquire(blocking=False):
        print("SUGGESTION_SNAPSHOT deferred=busy", flush=True)
        return
    anchor = str(record.get("gidRecord") or record.get("idRecord") or record.get("recordCode") or "")
    content = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)

    def index() -> None:
        client = None
        try:
            from langchain_ollama import OllamaEmbeddings
            from qdrant_client import QdrantClient
            from qdrant_client.models import PointStruct
            from app.rag.vector_store import ensure_vector_collection

            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                f"suggestion-sql:{instance_id}:{resource_id}:{table}:{anchor}"))
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            client = QdrantClient(url=settings.VECTOR_DB_URL, timeout=15)
            embeddings = OllamaEmbeddings(base_url=settings.SYSTEM_KNOWLEDGE_EMBEDDING_BASE_URL,
                                           model=settings.EMBEDDING_MODEL_NAME)
            ensure_vector_collection(client, settings.VECTOR_COLLECTION_NAME, embeddings)
            existing = client.retrieve(settings.VECTOR_COLLECTION_NAME, [point_id], with_payload=True)
            if existing and (existing[0].payload or {}).get("content_hash") == digest:
                print("SUGGESTION_SNAPSHOT unchanged=True", flush=True)
                return
            text = f"{record.get('recordCode') or anchor}\n{content}"
            vector = embeddings.embed_query(text)
            client.upsert(collection_name=settings.VECTOR_COLLECTION_NAME, wait=True, points=[
                PointStruct(id=point_id, vector=vector, payload={
                    "page_content": text, "source": "solidset_system_snapshot",
                    "document_type": "system_entity", "scope": "system_snapshot",
                    "solidset_instance_id": instance_id, "related_resource_ids": [resource_id],
                    "source_table": table, "source_record_id": anchor,
                    "content_hash": digest, "generated_by_ia": False,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                })])
            print("SUGGESTION_SNAPSHOT indexed=True", flush=True)
        except Exception as exc:
            print(f"SUGGESTION_SNAPSHOT failed={type(exc).__name__}", flush=True)
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                _index_slot.release()

    threading.Thread(target=index, daemon=True, name="suggestion-sql-snapshot").start()

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.config import settings


def connection() -> psycopg.Connection:
    return psycopg.connect(
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB, row_factory=dict_row,
    )


def ensure_schema() -> None:
    migration = (__import__("pathlib").Path(__file__).parents[3] / "database" / "init" / "016_create_historical_ingestion.sql")
    if migration.exists():
        sql = migration.read_text(encoding="utf-8-sig")
    else:
        # La imagen contiene /app pero no necesariamente database/init.
        sql = '''CREATE TABLE IF NOT EXISTS public."SysAgentIAIngestionCursor" (
          "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(), "IDSolidSETInstance" uuid NOT NULL,
          "Source" varchar(100) NOT NULL, "LastIDChat2" bigint NOT NULL DEFAULT 0,
          "LastStamp" timestamptz, "LastRunAt" timestamptz, "Status" varchar(30) NOT NULL DEFAULT 'idle',
          "Error" text, "CurrentBatchID" varchar(200), "IDResource" uuid,
          "IDAgentResource" uuid, "SourceType" varchar(40) NOT NULL DEFAULT 'chat',
          UNIQUE ("IDSolidSETInstance", "Source"));
        CREATE TABLE IF NOT EXISTS public."SysAgentIAIngestionAudit" (
          "BatchID" varchar(200) PRIMARY KEY, "IDSolidSETInstance" uuid NOT NULL,
          "FirstIDChat2" bigint, "LastIDChat2" bigint, "ReadCount" integer DEFAULT 0,
          "AcceptedCount" integer DEFAULT 0, "RejectedCount" integer DEFAULT 0,
          "IndexedCount" integer DEFAULT 0, "Status" varchar(30) NOT NULL, "Error" text,
          "StartedAt" timestamptz DEFAULT CURRENT_TIMESTAMP, "CompletedAt" timestamptz);
        CREATE TABLE IF NOT EXISTS public."SysAgentIAHistoricalDocument" (
          "DocumentID" uuid PRIMARY KEY, "IDChat2" bigint NOT NULL, "IDSolidSETInstance" uuid NOT NULL,
          "Scope" varchar(30) NOT NULL, "IDResource" uuid, "IDAgentResource" uuid,
          "IDWorkRoom" uuid, "QdrantPointID" uuid NOT NULL UNIQUE, "ContentHash" varchar(64) NOT NULL,
          "Status" varchar(30) DEFAULT 'indexed', "RejectReason" varchar(50),
          "IndexedAt" timestamptz DEFAULT CURRENT_TIMESTAMP, "DeletedAt" timestamptz,
          "SourceType" varchar(40) NOT NULL DEFAULT 'chat', "SourceID" varchar(100));
        ALTER TABLE public."SysAgentIAIngestionCursor"
          ADD COLUMN IF NOT EXISTS "CurrentBatchID" varchar(200);'''
    sql += '''
    ALTER TABLE public."SysAgentIAIngestionCursor"
      ADD COLUMN IF NOT EXISTS "CurrentBatchID" varchar(200);
    CREATE INDEX IF NOT EXISTS "IX_IngestionCursor_Recovery"
      ON public."SysAgentIAIngestionCursor" ("Status", "LastRunAt")
      WHERE "Status" IN ('queued', 'processing', 'recovering');
    ALTER TABLE public."SysAgentIAIngestionCursor"
      ADD COLUMN IF NOT EXISTS "IDResource" uuid,
      ADD COLUMN IF NOT EXISTS "IDAgentResource" uuid,
      ADD COLUMN IF NOT EXISTS "SourceType" varchar(40) NOT NULL DEFAULT 'chat';
    ALTER TABLE public."SysAgentIAIngestionAudit"
      ADD COLUMN IF NOT EXISTS "IDResource" uuid,
      ADD COLUMN IF NOT EXISTS "IDAgentResource" uuid,
      ADD COLUMN IF NOT EXISTS "SourceType" varchar(40) NOT NULL DEFAULT 'chat';
    ALTER TABLE public."SysAgentIAHistoricalDocument"
      ADD COLUMN IF NOT EXISTS "SourceType" varchar(40) NOT NULL DEFAULT 'chat',
      ADD COLUMN IF NOT EXISTS "SourceID" varchar(100);
    CREATE INDEX IF NOT EXISTS "IX_IngestionCursor_Agent"
      ON public."SysAgentIAIngestionCursor" ("IDResource", "SourceType", "Status");
    CREATE INDEX IF NOT EXISTS "IX_HistoricalDocument_Source"
      ON public."SysAgentIAHistoricalDocument" ("IDResource", "SourceType", "SourceID")
      WHERE "DeletedAt" IS NULL;
    UPDATE public."SysAgentIAIngestionCursor"
      SET "Status"='superseded', "CurrentBatchID"=NULL,
          "Error"='Substituído por cursores independentes por agente e origem'
      WHERE "Source"='solidset_sql_history' AND "IDResource" IS NULL;
    '''
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)


def get_cursor(
    instance_id: str,
    source: str = "solidset_sql_history",
    *,
    resource_id: str | None = None,
    agent_resource_id: str | None = None,
    source_type: str = "chat",
) -> dict[str, Any]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO public."SysAgentIAIngestionCursor"
          ("IDSolidSETInstance", "Source", "IDResource", "IDAgentResource", "SourceType", "LastIDChat2")
          VALUES (%s, %s, %s, %s, %s, COALESCE((
            SELECT MAX(d."IDChat2") FROM public."SysAgentIAHistoricalDocument" d
            WHERE d."IDSolidSETInstance"=%s AND d."IDResource"=%s
              AND d."SourceType"=%s AND d."DeletedAt" IS NULL
          ), 0))
          ON CONFLICT ("IDSolidSETInstance", "Source") DO UPDATE SET
            "IDResource"=COALESCE(EXCLUDED."IDResource", public."SysAgentIAIngestionCursor"."IDResource"),
            "IDAgentResource"=COALESCE(EXCLUDED."IDAgentResource", public."SysAgentIAIngestionCursor"."IDAgentResource"),
            "SourceType"=EXCLUDED."SourceType"''',
          (UUID(instance_id), source, UUID(resource_id) if resource_id else None,
           UUID(agent_resource_id) if agent_resource_id else None, source_type,
           UUID(instance_id), UUID(resource_id) if resource_id else None, source_type))
        cur.execute('''SELECT * FROM public."SysAgentIAIngestionCursor"
          WHERE "IDSolidSETInstance"=%s AND "Source"=%s''', (UUID(instance_id), source))
        return dict(cur.fetchone())


def set_cursor(
    instance_id: str,
    last_id: int,
    last_stamp: Any,
    status: str,
    error: str | None = None,
    batch_id: str | None = None,
    source: str = "solidset_sql_history",
) -> None:
    """Persists a monotonic checkpoint so an old delivery can never rewind it."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIAIngestionCursor" SET
          "LastIDChat2"=GREATEST("LastIDChat2", %s),
          "LastStamp"=CASE WHEN %s >= "LastIDChat2" THEN COALESCE(%s, "LastStamp") ELSE "LastStamp" END,
          "LastRunAt"=CURRENT_TIMESTAMP,
          "Status"=CASE WHEN %s >= "LastIDChat2" THEN %s ELSE "Status" END,
          "Error"=CASE WHEN %s >= "LastIDChat2" THEN %s ELSE "Error" END,
          "CurrentBatchID"=CASE WHEN %s >= "LastIDChat2" THEN %s ELSE "CurrentBatchID" END
          WHERE "IDSolidSETInstance"=%s AND "Source"=%s ''',
          (
              last_id, last_id, last_stamp, last_id, status,
              last_id, error, last_id, batch_id, UUID(instance_id), source,
          ))


def recover_stale_cursors(stale_seconds: int) -> list[dict[str, Any]]:
    """Releases abandoned batches while retaining the last committed IDChat2."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIAIngestionCursor"
          SET "Status"='recovering',
              "Error"=CONCAT('Recuperado após reinício; lote anterior: ',
                             COALESCE("CurrentBatchID", 'desconhecido')),
              "CurrentBatchID"=NULL,
              "LastRunAt"=CURRENT_TIMESTAMP
          WHERE "Status" IN ('queued','processing')
            AND ("LastRunAt" IS NULL OR "LastRunAt" < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second'))
          RETURNING *''', (max(1, int(stale_seconds)),))
        return [dict(row) for row in cur.fetchall()]


def workroom_agents(workroom_id: str) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT r."IDResource", r."IDAgentResource"
          FROM public."SysResourceIA" r JOIN public."SysChatIAResource" c
            ON c."IDResource"=r."IDResource"
          WHERE r.active=true AND c.active=true AND c."IDWorkRoom"=%s''', (UUID(workroom_id),))
        return [dict(row) for row in cur.fetchall()]


def list_active_ingestion_agents(instance_id: str | None = None) -> list[dict[str, Any]]:
    """Returns only verified local agents and their currently authorized rooms."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT r."IDResource", r."IDAgentResource", r."Name",
          COALESCE(array_agg(c."IDWorkRoom") FILTER (WHERE c.active=true), ARRAY[]::uuid[]) AS "WorkRooms"
          FROM public."SysResourceIA" r
          LEFT JOIN public."SysChatIAResource" c ON c."IDResource"=r."IDResource"
          WHERE r.active=true AND r."IDAgentResource" IS NOT NULL
            AND (%s::uuid IS NULL OR EXISTS (
              SELECT 1 FROM public."SysSolidSETInstanceResource" ir
              WHERE ir."IDSolidSETInstance"=%s::uuid AND ir."IDResource"=r."IDResource" AND ir.active=true
            ))
          GROUP BY r."IDResource", r."IDAgentResource", r."Name"
          ORDER BY r."IDResource"''', (instance_id, instance_id))
        return [dict(row) for row in cur.fetchall()]


def historical_agent_is_active(
    resource_id: str, agent_resource_id: str, instance_id: str | None = None,
) -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT EXISTS(SELECT 1 FROM public."SysResourceIA" r
          WHERE r."IDResource"=%s AND r."IDAgentResource"=%s AND r.active=true
            AND (%s::uuid IS NULL OR EXISTS (
              SELECT 1 FROM public."SysSolidSETInstanceResource" ir
              WHERE ir."IDSolidSETInstance"=%s::uuid AND ir."IDResource"=r."IDResource" AND ir.active=true
            ))) AS active''',
          (UUID(resource_id), UUID(agent_resource_id), instance_id, instance_id))
        row = cur.fetchone()
        return bool(row and row["active"])


def upsert_audit(batch: dict[str, Any], status: str, **counts: Any) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO public."SysAgentIAIngestionAudit"
          ("BatchID","IDSolidSETInstance","FirstIDChat2","LastIDChat2","ReadCount","Status",
           "IDResource","IDAgentResource","SourceType")
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT ("BatchID") DO UPDATE SET
          "AcceptedCount"=%s,"RejectedCount"=%s,"IndexedCount"=%s,"Status"=%s,"Error"=%s,
          "CompletedAt"=CASE WHEN %s IN ('completed','failed','dry_run','inactive') THEN CURRENT_TIMESTAMP ELSE NULL END''',
          (batch["batchId"], UUID(batch["instanceId"]), batch.get("firstIdChat2"), batch.get("lastIdChat2"),
           len(batch.get("messages") or []), status,
           UUID(batch["resourceId"]) if batch.get("resourceId") else None,
           UUID(batch["agentResourceId"]) if batch.get("agentResourceId") else None,
           batch.get("sourceType", "chat"), counts.get("accepted",0), counts.get("rejected",0),
           counts.get("indexed",0), status, counts.get("error"), status))


def save_document(document: dict[str, Any]) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO public."SysAgentIAHistoricalDocument"
          ("DocumentID","IDChat2","IDSolidSETInstance","Scope","IDResource","IDAgentResource",
           "IDWorkRoom","QdrantPointID","ContentHash","Status","SourceType","SourceID")
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'indexed',%s,%s)
          ON CONFLICT ("DocumentID") DO UPDATE SET "ContentHash"=EXCLUDED."ContentHash",
          "IDAgentResource"=EXCLUDED."IDAgentResource",
          "SourceType"=EXCLUDED."SourceType","SourceID"=EXCLUDED."SourceID",
          "Status"='indexed',"DeletedAt"=NULL,"IndexedAt"=CURRENT_TIMESTAMP''',
          tuple(document[key] for key in ("DocumentID","IDChat2","IDSolidSETInstance","Scope",
                "IDResource","IDAgentResource","IDWorkRoom","QdrantPointID","ContentHash")) +
          (document.get("SourceType", "chat"), str(document.get("SourceID") or document["IDChat2"])))


def list_audits(limit: int = 50, resource_id: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT * FROM public."SysAgentIAIngestionAudit"
          WHERE (%s::uuid IS NULL OR "IDResource"=%s::uuid)
          ORDER BY "StartedAt" DESC LIMIT %s''', (resource_id, resource_id, limit))
        return [dict(row) for row in cur.fetchall()]


def list_cursors(resource_id: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT * FROM public."SysAgentIAIngestionCursor"
          WHERE (%s::uuid IS NULL OR "IDResource"=%s::uuid)
          ORDER BY "IDSolidSETInstance", "IDResource", "SourceType", "Source"''',
          (resource_id, resource_id))
        return [dict(row) for row in cur.fetchall()]


def approve_dry_run_cursors(instance_id: str) -> int:
    """Releases every per-agent source after its dry-run has been reviewed."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIAIngestionCursor"
          SET "Status"='idle', "Error"=NULL, "CurrentBatchID"=NULL,
              "LastRunAt"=CURRENT_TIMESTAMP
          WHERE "IDSolidSETInstance"=%s AND "Status"='dry_run' ''',
          (UUID(instance_id),))
        return max(0, cur.rowcount)


def historical_points(instance_id: str, chat_id: int, source_type: str = "chat") -> list[str]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT "QdrantPointID" FROM public."SysAgentIAHistoricalDocument"
          WHERE "IDSolidSETInstance"=%s AND "IDChat2"=%s
            AND "SourceType"=%s AND "DeletedAt" IS NULL''',
          (UUID(instance_id), chat_id, source_type))
        return [str(row["QdrantPointID"]) for row in cur.fetchall()]


def mark_historical_deleted(instance_id: str, chat_id: int, source_type: str = "chat") -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIAHistoricalDocument"
          SET "Status"='deleted', "DeletedAt"=CURRENT_TIMESTAMP
          WHERE "IDSolidSETInstance"=%s AND "IDChat2"=%s
            AND "SourceType"=%s AND "DeletedAt" IS NULL''',
          (UUID(instance_id), chat_id, source_type))
        return max(0, cur.rowcount)

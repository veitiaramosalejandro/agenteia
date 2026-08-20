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
          "Error" text, UNIQUE ("IDSolidSETInstance", "Source"));
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
          "IndexedAt" timestamptz DEFAULT CURRENT_TIMESTAMP, "DeletedAt" timestamptz);'''
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)


def get_cursor(instance_id: str, source: str = "solidset_sql_history") -> dict[str, Any]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO public."SysAgentIAIngestionCursor"
          ("IDSolidSETInstance", "Source") VALUES (%s, %s)
          ON CONFLICT ("IDSolidSETInstance", "Source") DO NOTHING''', (UUID(instance_id), source))
        cur.execute('''SELECT * FROM public."SysAgentIAIngestionCursor"
          WHERE "IDSolidSETInstance"=%s AND "Source"=%s''', (UUID(instance_id), source))
        return dict(cur.fetchone())


def set_cursor(instance_id: str, last_id: int, last_stamp: Any, status: str, error: str | None = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIAIngestionCursor" SET
          "LastIDChat2"=%s, "LastStamp"=%s, "LastRunAt"=CURRENT_TIMESTAMP,
          "Status"=%s, "Error"=%s WHERE "IDSolidSETInstance"=%s AND "Source"='solidset_sql_history' ''',
          (last_id, last_stamp, status, error, UUID(instance_id)))


def workroom_agents(workroom_id: str) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT r."IDResource", r."IDAgentResource"
          FROM public."SysResourceIA" r JOIN public."SysChatIAResource" c
            ON c."IDResource"=r."IDResource"
          WHERE r.active=true AND c.active=true AND c."IDWorkRoom"=%s''', (UUID(workroom_id),))
        return [dict(row) for row in cur.fetchall()]


def upsert_audit(batch: dict[str, Any], status: str, **counts: Any) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO public."SysAgentIAIngestionAudit"
          ("BatchID","IDSolidSETInstance","FirstIDChat2","LastIDChat2","ReadCount","Status")
          VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT ("BatchID") DO UPDATE SET
          "AcceptedCount"=%s,"RejectedCount"=%s,"IndexedCount"=%s,"Status"=%s,"Error"=%s,
          "CompletedAt"=CASE WHEN %s IN ('completed','failed','dry_run') THEN CURRENT_TIMESTAMP ELSE NULL END''',
          (batch["batchId"], UUID(batch["instanceId"]), batch.get("firstIdChat2"), batch.get("lastIdChat2"),
           len(batch.get("messages") or []), status, counts.get("accepted",0), counts.get("rejected",0),
           counts.get("indexed",0), status, counts.get("error"), status))


def save_document(document: dict[str, Any]) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO public."SysAgentIAHistoricalDocument"
          ("DocumentID","IDChat2","IDSolidSETInstance","Scope","IDResource","IDAgentResource",
           "IDWorkRoom","QdrantPointID","ContentHash","Status")
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'indexed')
          ON CONFLICT ("DocumentID") DO UPDATE SET "ContentHash"=EXCLUDED."ContentHash",
          "Status"='indexed',"DeletedAt"=NULL,"IndexedAt"=CURRENT_TIMESTAMP''',
          tuple(document[key] for key in ("DocumentID","IDChat2","IDSolidSETInstance","Scope",
                "IDResource","IDAgentResource","IDWorkRoom","QdrantPointID","ContentHash")))


def list_audits(limit: int = 50) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM public."SysAgentIAIngestionAudit" ORDER BY "StartedAt" DESC LIMIT %s', (limit,))
        return [dict(row) for row in cur.fetchall()]


def list_cursors() -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM public."SysAgentIAIngestionCursor" ORDER BY "IDSolidSETInstance", "Source"')
        return [dict(row) for row in cur.fetchall()]


def historical_points(instance_id: str, chat_id: int) -> list[str]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT "QdrantPointID" FROM public."SysAgentIAHistoricalDocument"
          WHERE "IDSolidSETInstance"=%s AND "IDChat2"=%s AND "DeletedAt" IS NULL''',
          (UUID(instance_id), chat_id))
        return [str(row["QdrantPointID"]) for row in cur.fetchall()]


def mark_historical_deleted(instance_id: str, chat_id: int) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE public."SysAgentIAHistoricalDocument"
          SET "Status"='deleted', "DeletedAt"=CURRENT_TIMESTAMP
          WHERE "IDSolidSETInstance"=%s AND "IDChat2"=%s AND "DeletedAt" IS NULL''',
          (UUID(instance_id), chat_id))
        return max(0, cur.rowcount)

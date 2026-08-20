CREATE TABLE IF NOT EXISTS public."SysAgentIAIngestionCursor" (
    "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    "IDSolidSETInstance" uuid NOT NULL REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
    "Source" varchar(100) NOT NULL,
    "LastIDChat2" bigint NOT NULL DEFAULT 0,
    "LastStamp" timestamptz,
    "LastRunAt" timestamptz,
    "Status" varchar(30) NOT NULL DEFAULT 'idle',
    "Error" text,
    UNIQUE ("IDSolidSETInstance", "Source")
);

CREATE TABLE IF NOT EXISTS public."SysAgentIAIngestionAudit" (
    "BatchID" varchar(200) PRIMARY KEY,
    "IDSolidSETInstance" uuid NOT NULL REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
    "FirstIDChat2" bigint,
    "LastIDChat2" bigint,
    "ReadCount" integer NOT NULL DEFAULT 0,
    "AcceptedCount" integer NOT NULL DEFAULT 0,
    "RejectedCount" integer NOT NULL DEFAULT 0,
    "IndexedCount" integer NOT NULL DEFAULT 0,
    "Status" varchar(30) NOT NULL,
    "Error" text,
    "StartedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "CompletedAt" timestamptz
);

CREATE TABLE IF NOT EXISTS public."SysAgentIAHistoricalDocument" (
    "DocumentID" uuid PRIMARY KEY,
    "IDChat2" bigint NOT NULL,
    "IDSolidSETInstance" uuid NOT NULL REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
    "Scope" varchar(30) NOT NULL,
    "IDResource" uuid,
    "IDAgentResource" uuid,
    "IDWorkRoom" uuid,
    "QdrantPointID" uuid NOT NULL UNIQUE,
    "ContentHash" varchar(64) NOT NULL,
    "Status" varchar(30) NOT NULL DEFAULT 'indexed',
    "RejectReason" varchar(50),
    "IndexedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "DeletedAt" timestamptz,
    UNIQUE ("IDSolidSETInstance", "IDChat2", "Scope", "IDResource", "IDWorkRoom")
);

CREATE INDEX IF NOT EXISTS "IX_HistoricalDocument_Chat"
    ON public."SysAgentIAHistoricalDocument" ("IDSolidSETInstance", "IDChat2");
CREATE INDEX IF NOT EXISTS "IX_HistoricalDocument_AgentScope"
    ON public."SysAgentIAHistoricalDocument" ("IDResource", "Scope", "IDWorkRoom")
    WHERE "DeletedAt" IS NULL;

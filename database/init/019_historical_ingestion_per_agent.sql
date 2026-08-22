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

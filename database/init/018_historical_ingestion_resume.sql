ALTER TABLE public."SysAgentIAIngestionCursor"
    ADD COLUMN IF NOT EXISTS "CurrentBatchID" varchar(200);

CREATE INDEX IF NOT EXISTS "IX_IngestionCursor_Recovery"
    ON public."SysAgentIAIngestionCursor" ("Status", "LastRunAt")
    WHERE "Status" IN ('queued', 'processing', 'recovering');

COMMENT ON COLUMN public."SysAgentIAIngestionCursor"."CurrentBatchID" IS
    'Batch currently queued or processed; cleared only after a terminal checkpoint.';

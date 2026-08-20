ALTER TABLE public."SysResourceIA"
    ADD COLUMN IF NOT EXISTS "IDAgentResource" uuid;

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysResourceIA_IDAgentResource"
    ON public."SysResourceIA" ("IDAgentResource")
    WHERE "IDAgentResource" IS NOT NULL;

COMMENT ON COLUMN public."SysResourceIA"."IDAgentResource" IS
    'Recurso Software de dbo.SysResource2Agent usado como remitente real del chat IA.';

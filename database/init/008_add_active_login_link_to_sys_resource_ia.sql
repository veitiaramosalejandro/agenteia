ALTER TABLE public."SysResourceIA"
    ADD COLUMN IF NOT EXISTS "ActiveIDLogin2Resource" uuid;

CREATE INDEX IF NOT EXISTS "IX_SysResourceIA_ActiveIDLogin2Resource"
    ON public."SysResourceIA" ("ActiveIDLogin2Resource");

COMMENT ON COLUMN public."SysResourceIA"."ActiveIDLogin2Resource" IS
    'Enlace activo de SolidSET usado para resolver sin ambigüedad el SysLogin del recurso agente.';

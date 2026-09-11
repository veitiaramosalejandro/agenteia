ALTER TABLE public."SysSolidSETInstanceResource"
  ADD COLUMN IF NOT EXISTS "IDAgentResource" uuid;

UPDATE public."SysSolidSETInstanceResource" ir
SET "IDAgentResource" = r."IDAgentResource"
FROM public."SysResourceIA" r
WHERE r."IDResource" = ir."IDResource"
  AND ir."IDAgentResource" IS NULL
  AND (r."IDSolidSETInstance" IS NULL
       OR r."IDSolidSETInstance" = ir."IDSolidSETInstance");

CREATE INDEX IF NOT EXISTS "IX_SysSolidSETInstanceResource_AgentResource"
  ON public."SysSolidSETInstanceResource"
  ("IDSolidSETInstance", "IDAgentResource")
  WHERE "IDAgentResource" IS NOT NULL;

COMMENT ON COLUMN public."SysSolidSETInstanceResource"."IDAgentResource" IS
  'Identidad IA autoritativa del recurso dentro de esta instancia SolidSET.';

ALTER TABLE public."SysAgentIAResponseAudit"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;

CREATE INDEX IF NOT EXISTS "IX_ResponseAudit_Instance_CreatedAt"
  ON public."SysAgentIAResponseAudit" ("IDSolidSETInstance", "CreatedAt" DESC);

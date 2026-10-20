ALTER TABLE public."SysAgentIAReaction"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;

UPDATE public."SysAgentIAReaction" reaction
SET "IDSolidSETInstance" = resource."IDSolidSETInstance"
FROM public."SysResourceIA" resource
WHERE resource."IDResource" = reaction."IDAgentResource"
  AND reaction."IDSolidSETInstance" IS NULL;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public."SysAgentIAReaction"
    WHERE "IDSolidSETInstance" IS NULL
  ) THEN
    RAISE EXCEPTION 'No se puede exigir la instancia: existen reacciones sin recurso asociado a una instancia';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'FK_SysAgentIAReaction_Instance'
  ) THEN
    ALTER TABLE public."SysAgentIAReaction"
      ADD CONSTRAINT "FK_SysAgentIAReaction_Instance"
      FOREIGN KEY ("IDSolidSETInstance")
      REFERENCES public."SysSolidSETInstance"("ID")
      ON DELETE CASCADE;
  END IF;
END $$;

ALTER TABLE public."SysAgentIAReaction"
  ALTER COLUMN "IDSolidSETInstance" SET NOT NULL;

ALTER TABLE public."SysAgentIAReaction"
  DROP CONSTRAINT IF EXISTS "UQ_SysAgentIAReaction_ChatUserEmoji";

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'UQ_SysAgentIAReaction_InstanceChatUserEmoji'
  ) THEN
    ALTER TABLE public."SysAgentIAReaction"
      ADD CONSTRAINT "UQ_SysAgentIAReaction_InstanceChatUserEmoji"
      UNIQUE ("IDSolidSETInstance", "IDChat", "IDUser", "IDEmoji");
  END IF;
END $$;

DROP INDEX IF EXISTS public."IX_SysAgentIAReaction_AgentResource";
DROP INDEX IF EXISTS public."IX_SysAgentIAReaction_Reward";

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAReaction_InstanceAgentResource"
  ON public."SysAgentIAReaction"
  ("IDSolidSETInstance", "IDAgentResource", "UpdatedAt" DESC);

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAReaction_InstanceReward"
  ON public."SysAgentIAReaction"
  ("IDSolidSETInstance", "IDAgentResource", "IDChannel", "Reward" DESC);

COMMENT ON COLUMN public."SysAgentIAReaction"."IDSolidSETInstance" IS
  'Instancia SolidSET propietaria de la reacción y de su aprendizaje por refuerzo.';

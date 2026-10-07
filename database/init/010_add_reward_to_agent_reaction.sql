ALTER TABLE public."SysAgentIAReaction"
    ADD COLUMN IF NOT EXISTS "Reward" double precision NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAReaction_Reward"
    ON public."SysAgentIAReaction" ("IDAgentResource", "IDChannel", "Reward" DESC);

COMMENT ON COLUMN public."SysAgentIAReaction"."Reward" IS
    'Recompensa usada por la política de aprendizaje: positiva favorece el patrón y negativa lo penaliza.';

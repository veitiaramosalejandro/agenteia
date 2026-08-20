DROP INDEX IF EXISTS public."UQ_SysAgentIAModel_ActiveResource";

ALTER TABLE public."SysAgentIAModel"
    ADD COLUMN IF NOT EXISTS "Capabilities" jsonb NOT NULL DEFAULT '["general"]'::jsonb,
    ADD COLUMN IF NOT EXISTS "Priority" integer NOT NULL DEFAULT 100,
    ADD COLUMN IF NOT EXISTS "IsDefault" boolean NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysAgentIAModel_ResourceProvider"
    ON public."SysAgentIAModel" ("IDResource", "IDProviderConfiguration")
    WHERE active=true;

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysAgentIAModel_DefaultResource"
    ON public."SysAgentIAModel" ("IDResource")
    WHERE active=true AND "IsDefault"=true;

UPDATE public."SysAgentIAModel"
SET "Capabilities"='["general", "external_web"]'::jsonb,
    "IsDefault"=true
WHERE active=true;

INSERT INTO public."SysAgentIAModel" (
    "IDResource", "IDProviderConfiguration", "Role", "LocalExecution",
    "TrainingMode", "LearnFromOwner", "LearnFromSystem", "LearnFromReactions",
    "Capabilities", "Priority", "IsDefault", active
)
SELECT r."IDResource", p."ID", 'coding_sql', true, 'rag_reinforcement',
       true, true, true, '["coding", "sql", "technical"]'::jsonb, 20, false, true
FROM public."SysResourceIA" r
JOIN public."SysLLMProviderConfiguration" p ON p."Code"='ollama-coder' AND p.active=true
WHERE r.active=true
ON CONFLICT ("IDResource", "IDProviderConfiguration") WHERE active=true DO UPDATE SET
    "Capabilities"=EXCLUDED."Capabilities", "Priority"=EXCLUDED."Priority", active=true;

INSERT INTO public."SysAgentIAModel" (
    "IDResource", "IDProviderConfiguration", "Role", "LocalExecution",
    "TrainingMode", "LearnFromOwner", "LearnFromSystem", "LearnFromReactions",
    "Capabilities", "Priority", "IsDefault", active
)
SELECT r."IDResource", p."ID", 'reasoning', true, 'rag_reinforcement',
       true, true, true, '["reasoning", "planning", "analysis"]'::jsonb, 30, false, true
FROM public."SysResourceIA" r
JOIN public."SysLLMProviderConfiguration" p ON p."Code"='ollama-secondary' AND p.active=true
WHERE r.active=true
ON CONFLICT ("IDResource", "IDProviderConfiguration") WHERE active=true DO UPDATE SET
    "Capabilities"=EXCLUDED."Capabilities", "Priority"=EXCLUDED."Priority", active=true;

COMMENT ON COLUMN public."SysAgentIAModel"."Capabilities" IS
    'Intenciones para las que el router puede seleccionar este modelo.';

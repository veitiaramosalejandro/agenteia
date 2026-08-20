CREATE TABLE IF NOT EXISTS public."SysAgentIAModel" (
    "ID" uuid NOT NULL DEFAULT gen_random_uuid(),
    "IDResource" uuid NOT NULL,
    "IDProviderConfiguration" uuid NOT NULL,
    "Role" varchar(80) NOT NULL DEFAULT 'general',
    "LocalExecution" boolean NOT NULL DEFAULT true,
    "TrainingMode" varchar(40) NOT NULL DEFAULT 'rag_reinforcement',
    "LearnFromOwner" boolean NOT NULL DEFAULT true,
    "LearnFromSystem" boolean NOT NULL DEFAULT true,
    "LearnFromReactions" boolean NOT NULL DEFAULT true,
    active boolean NOT NULL DEFAULT true,
    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "PK_SysAgentIAModel" PRIMARY KEY ("ID"),
    CONSTRAINT "FK_SysAgentIAModel_SysResourceIA" FOREIGN KEY ("IDResource")
        REFERENCES public."SysResourceIA" ("IDResource") ON DELETE CASCADE,
    CONSTRAINT "FK_SysAgentIAModel_Provider" FOREIGN KEY ("IDProviderConfiguration")
        REFERENCES public."SysLLMProviderConfiguration" ("ID") ON DELETE RESTRICT,
    CONSTRAINT "CK_SysAgentIAModel_TrainingMode" CHECK (
        "TrainingMode" IN ('rag_reinforcement', 'rag_only', 'disabled')
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysAgentIAModel_ActiveResource"
    ON public."SysAgentIAModel" ("IDResource") WHERE active = true;

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAModel_Provider"
    ON public."SysAgentIAModel" ("IDProviderConfiguration", active);

INSERT INTO public."SysAgentIAModel" (
    "IDResource", "IDProviderConfiguration", "Role", "LocalExecution",
    "TrainingMode", "LearnFromOwner", "LearnFromSystem", "LearnFromReactions", active
)
SELECT r."IDResource", p."ID", 'general', true, 'rag_reinforcement', true, true, true, true
FROM public."SysResourceIA" r
CROSS JOIN LATERAL (
    SELECT "ID" FROM public."SysLLMProviderConfiguration"
    WHERE active=true AND "IsDefault"=true AND "IDResource" IS NULL
    ORDER BY "UpdatedAt" DESC LIMIT 1
) p
WHERE r.active=true
ON CONFLICT ("IDResource") WHERE active=true DO NOTHING;

COMMENT ON TABLE public."SysAgentIAModel" IS
    'Modelo de ejecución y política de aprendizaje asignados a cada agente SolidSET.';

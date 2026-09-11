ALTER TABLE public."SysAgentIAModel"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;

DROP INDEX IF EXISTS public."UQ_SysAgentIAModel_ResourceProvider";
DROP INDEX IF EXISTS public."UQ_SysAgentIAModel_DefaultResource";

UPDATE public."SysAgentIAModel" m
SET "IDSolidSETInstance" = source."IDSolidSETInstance"
FROM (
  SELECT m2."ID", MIN(ir."IDSolidSETInstance"::text)::uuid AS "IDSolidSETInstance"
  FROM public."SysAgentIAModel" m2
  JOIN public."SysSolidSETInstanceResource" ir
    ON ir."IDResource"=m2."IDResource" AND ir.active=true
  GROUP BY m2."ID"
) source
WHERE source."ID"=m."ID" AND m."IDSolidSETInstance" IS NULL;

INSERT INTO public."SysAgentIAModel" (
  "IDSolidSETInstance", "IDResource", "IDProviderConfiguration", "Role",
  "LocalExecution", "TrainingMode", "LearnFromOwner", "LearnFromSystem",
  "LearnFromReactions", "Capabilities", "Priority", "IsDefault", active
)
SELECT ir."IDSolidSETInstance", m."IDResource", m."IDProviderConfiguration", m."Role",
       m."LocalExecution", m."TrainingMode", m."LearnFromOwner", m."LearnFromSystem",
       m."LearnFromReactions", m."Capabilities", m."Priority", m."IsDefault", m.active
FROM public."SysAgentIAModel" m
JOIN public."SysSolidSETInstanceResource" ir
  ON ir."IDResource"=m."IDResource" AND ir.active=true
WHERE m."IDSolidSETInstance" IS DISTINCT FROM ir."IDSolidSETInstance"
  AND NOT EXISTS (
    SELECT 1 FROM public."SysAgentIAModel" existing
    WHERE existing."IDSolidSETInstance"=ir."IDSolidSETInstance"
      AND existing."IDResource"=m."IDResource"
      AND existing."IDProviderConfiguration"=m."IDProviderConfiguration"
      AND existing.active=true
  );

CREATE UNIQUE INDEX "UQ_SysAgentIAModel_ResourceProvider"
  ON public."SysAgentIAModel"
  ("IDSolidSETInstance", "IDResource", "IDProviderConfiguration") WHERE active=true;
CREATE UNIQUE INDEX "UQ_SysAgentIAModel_DefaultResource"
  ON public."SysAgentIAModel" ("IDSolidSETInstance", "IDResource")
  WHERE active=true AND "IsDefault"=true;
CREATE INDEX IF NOT EXISTS "IX_SysAgentIAModel_InstanceResource"
  ON public."SysAgentIAModel" ("IDSolidSETInstance", "IDResource", active);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='FK_SysAgentIAModel_InstanceResource') THEN
    ALTER TABLE public."SysAgentIAModel"
      ADD CONSTRAINT "FK_SysAgentIAModel_InstanceResource"
      FOREIGN KEY ("IDSolidSETInstance", "IDResource")
      REFERENCES public."SysSolidSETInstanceResource"
        ("IDSolidSETInstance", "IDResource") ON DELETE CASCADE;
  END IF;
END $$;

COMMENT ON COLUMN public."SysAgentIAModel"."IDSolidSETInstance" IS
  'Instancia SolidSET propietaria de esta asignación de modelo.';

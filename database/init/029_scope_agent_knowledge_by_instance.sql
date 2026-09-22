ALTER TABLE public."SysResourceIAKnowledge"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;

UPDATE public."SysResourceIAKnowledge" k
SET "IDSolidSETInstance" = memberships."IDSolidSETInstance"
FROM (
  SELECT "IDResource",
         (array_agg("IDSolidSETInstance" ORDER BY "IDSolidSETInstance"))[1]
           AS "IDSolidSETInstance"
  FROM public."SysSolidSETInstanceResource"
  WHERE active = true
  GROUP BY "IDResource"
  HAVING COUNT(DISTINCT "IDSolidSETInstance") = 1
) memberships
WHERE k."IDSolidSETInstance" IS NULL
  AND memberships."IDResource" = k."IDResource";

CREATE INDEX IF NOT EXISTS "IX_SysResourceIAKnowledge_InstanceResourceRoom"
  ON public."SysResourceIAKnowledge"
  ("IDSolidSETInstance", "IDResource", "IDWorkRoom", active);

DO $$ BEGIN
  ALTER TABLE public."SysResourceIAKnowledge"
    ADD CONSTRAINT "FK_SysResourceIAKnowledge_SolidSETInstance"
    FOREIGN KEY ("IDSolidSETInstance")
    REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN public."SysResourceIAKnowledge"."IDSolidSETInstance" IS
  'Instancia propietaria del conocimiento. Las filas antiguas ambiguas permanecen sin instancia y no se consultan.';

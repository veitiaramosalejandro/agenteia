ALTER TABLE public."SysWorkRoom"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;
ALTER TABLE public."SysResourceIA"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;
ALTER TABLE public."SysLogin"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;
ALTER TABLE public."SysChatIAResource"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;

UPDATE public."SysResourceIA" r
SET "IDSolidSETInstance" = source."IDSolidSETInstance"
FROM (
  SELECT "IDResource", MIN("IDSolidSETInstance"::text)::uuid AS "IDSolidSETInstance"
  FROM public."SysSolidSETInstanceResource"
  GROUP BY "IDResource"
  HAVING COUNT(DISTINCT "IDSolidSETInstance") = 1
) source
WHERE source."IDResource" = r."IDResource"
  AND r."IDSolidSETInstance" IS NULL;

UPDATE public."SysLogin" l
SET "IDSolidSETInstance" = source."IDSolidSETInstance"
FROM (
  SELECT "IDLogin", MIN("IDSolidSETInstance"::text)::uuid AS "IDSolidSETInstance"
  FROM public."SysSolidSETInstanceLogin"
  GROUP BY "IDLogin"
  HAVING COUNT(DISTINCT "IDSolidSETInstance") = 1
) source
WHERE source."IDLogin" = l."IDLogin"
  AND l."IDSolidSETInstance" IS NULL;

UPDATE public."SysWorkRoom" w
SET "IDSolidSETInstance" = source."IDSolidSETInstance"
FROM (
  SELECT "IDWorkRoom", MIN("IDSolidSETInstance"::text)::uuid AS "IDSolidSETInstance"
  FROM public."SysAgentIAScope"
  GROUP BY "IDWorkRoom"
  HAVING COUNT(DISTINCT "IDSolidSETInstance") = 1
) source
WHERE source."IDWorkRoom" = w."IDWorkRoom"
  AND w."IDSolidSETInstance" IS NULL;

UPDATE public."SysChatIAResource" c
SET "IDSolidSETInstance" = r."IDSolidSETInstance"
FROM public."SysResourceIA" r
WHERE r."IDResource" = c."IDResource"
  AND c."IDSolidSETInstance" IS NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='FK_SysWorkRoom_Instance') THEN
    ALTER TABLE public."SysWorkRoom" ADD CONSTRAINT "FK_SysWorkRoom_Instance"
      FOREIGN KEY ("IDSolidSETInstance") REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='FK_SysResourceIA_Instance') THEN
    ALTER TABLE public."SysResourceIA" ADD CONSTRAINT "FK_SysResourceIA_Instance"
      FOREIGN KEY ("IDSolidSETInstance") REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='FK_SysLogin_Instance') THEN
    ALTER TABLE public."SysLogin" ADD CONSTRAINT "FK_SysLogin_Instance"
      FOREIGN KEY ("IDSolidSETInstance") REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='FK_SysChatIAResource_Instance') THEN
    ALTER TABLE public."SysChatIAResource" ADD CONSTRAINT "FK_SysChatIAResource_Instance"
      FOREIGN KEY ("IDSolidSETInstance") REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS "IX_SysWorkRoom_Instance_WorkRoom"
  ON public."SysWorkRoom" ("IDSolidSETInstance", "IDWorkRoom");
CREATE INDEX IF NOT EXISTS "IX_SysResourceIA_Instance_Resource"
  ON public."SysResourceIA" ("IDSolidSETInstance", "IDResource");
CREATE INDEX IF NOT EXISTS "IX_SysLogin_Instance_Login"
  ON public."SysLogin" ("IDSolidSETInstance", "IDLogin");
CREATE INDEX IF NOT EXISTS "IX_SysChatIAResource_Instance_WorkRoom_Resource"
  ON public."SysChatIAResource" ("IDSolidSETInstance", "IDWorkRoom", "IDResource");

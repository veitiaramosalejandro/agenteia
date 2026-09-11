ALTER TABLE public."SysWorkRoom"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;
ALTER TABLE public."SysResourceIA"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;
ALTER TABLE public."SysLogin"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;
ALTER TABLE public."SysChatIAResource"
  ADD COLUMN IF NOT EXISTS "IDSolidSETInstance" uuid;
ALTER TABLE public."SysSolidSETInstanceResource"
  ADD COLUMN IF NOT EXISTS "IDAgentResource" uuid;

UPDATE public."SysSolidSETInstanceResource" ir
SET "IDAgentResource" = r."IDAgentResource"
FROM public."SysResourceIA" r
WHERE r."IDResource" = ir."IDResource"
  AND ir."IDAgentResource" IS NULL
  AND (r."IDSolidSETInstance" IS NULL
       OR r."IDSolidSETInstance" = ir."IDSolidSETInstance");

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

CREATE TABLE IF NOT EXISTS public."SysSolidSETInstanceWorkRoom" (
  "IDSolidSETInstance" uuid NOT NULL REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "IDWorkRoom" uuid NOT NULL REFERENCES public."SysWorkRoom"("IDWorkRoom") ON DELETE CASCADE,
  "Code" varchar(20), "Name" varchar(100), "Description" varchar(200),
  active boolean NOT NULL DEFAULT true,
  "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("IDSolidSETInstance", "IDWorkRoom")
);

CREATE TABLE IF NOT EXISTS public."SysSolidSETInstanceChatIAResource" (
  "IDSolidSETInstance" uuid NOT NULL REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "IDResource" uuid NOT NULL,
  "IDWorkRoom" uuid NOT NULL,
  active boolean NOT NULL DEFAULT true,
  response_order integer NOT NULL DEFAULT 0,
  "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("IDSolidSETInstance", "IDResource", "IDWorkRoom"),
  FOREIGN KEY ("IDSolidSETInstance", "IDWorkRoom")
    REFERENCES public."SysSolidSETInstanceWorkRoom"("IDSolidSETInstance", "IDWorkRoom") ON DELETE CASCADE,
  FOREIGN KEY ("IDSolidSETInstance", "IDResource")
    REFERENCES public."SysSolidSETInstanceResource"("IDSolidSETInstance", "IDResource") ON DELETE CASCADE
);

INSERT INTO public."SysSolidSETInstanceWorkRoom"
  ("IDSolidSETInstance", "IDWorkRoom", "Code", "Name", "Description")
SELECT "IDSolidSETInstance", "IDWorkRoom", "Code", "Name", "Description"
FROM public."SysWorkRoom" WHERE "IDSolidSETInstance" IS NOT NULL
ON CONFLICT ("IDSolidSETInstance", "IDWorkRoom") DO NOTHING;

INSERT INTO public."SysSolidSETInstanceChatIAResource"
  ("IDSolidSETInstance", "IDResource", "IDWorkRoom", active, response_order)
SELECT c."IDSolidSETInstance", c."IDResource", c."IDWorkRoom", c.active, c.response_order
FROM public."SysChatIAResource" c
WHERE c."IDSolidSETInstance" IS NOT NULL
  AND EXISTS (SELECT 1 FROM public."SysSolidSETInstanceWorkRoom" w
              WHERE w."IDSolidSETInstance"=c."IDSolidSETInstance" AND w."IDWorkRoom"=c."IDWorkRoom")
  AND EXISTS (SELECT 1 FROM public."SysSolidSETInstanceResource" r
              WHERE r."IDSolidSETInstance"=c."IDSolidSETInstance" AND r."IDResource"=c."IDResource")
ON CONFLICT ("IDSolidSETInstance", "IDResource", "IDWorkRoom") DO NOTHING;

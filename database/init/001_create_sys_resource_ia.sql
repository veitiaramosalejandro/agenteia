DO $$
BEGIN
    IF to_regclass('public."SysResourceIA"') IS NULL
       AND to_regclass('public."SysChatIA"') IS NOT NULL THEN
        ALTER TABLE public."SysChatIA" RENAME TO "SysResourceIA";
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public."SysResourceIA" (
    "ID" uuid NOT NULL DEFAULT gen_random_uuid(),
    "Name" varchar(255),
    "Stamp" timestamp without time zone,
    "IDResource" uuid NOT NULL,
    active boolean NOT NULL DEFAULT false,
    CONSTRAINT "PK_SysResourceIA" PRIMARY KEY ("ID")
);

ALTER TABLE public."SysResourceIA"
    ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT false,
    ALTER COLUMN "ID" SET DEFAULT gen_random_uuid(),
    ALTER COLUMN "IDResource" SET NOT NULL,
    DROP COLUMN IF EXISTS "Code",
    DROP COLUMN IF EXISTS "Description",
    DROP COLUMN IF EXISTS "SessionResourceId",
    DROP COLUMN IF EXISTS "IDWorkRoom";

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public."SysResourceIA"'::regclass
          AND conname = 'PK_SysChatIA'
    ) THEN
        ALTER TABLE public."SysResourceIA"
            RENAME CONSTRAINT "PK_SysChatIA" TO "PK_SysResourceIA";
    END IF;
END
$$;

COMMENT ON TABLE public."SysResourceIA" IS
    'Configuracion de recursos de IA para la integracion con SolidSET.';

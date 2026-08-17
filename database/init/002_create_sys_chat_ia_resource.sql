ALTER TABLE public."SysResourceIA"
    ALTER COLUMN "IDResource" SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public."SysResourceIA"'::regclass
          AND conname = 'UQ_SysResourceIA_IDResource'
    ) THEN
        ALTER TABLE public."SysResourceIA"
            ADD CONSTRAINT "UQ_SysResourceIA_IDResource" UNIQUE ("IDResource");
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public."SysChatIAResource" (
    "IDResource" uuid NOT NULL,
    "IDWorkRoom" uuid NOT NULL,
    active boolean NOT NULL DEFAULT true,
    response_order integer NOT NULL DEFAULT 0,
    CONSTRAINT "PK_SysChatIAResource"
        PRIMARY KEY ("IDResource", "IDWorkRoom"),
    CONSTRAINT "FK_SysChatIAResource_SysResourceIA_IDResource"
        FOREIGN KEY ("IDResource")
        REFERENCES public."SysResourceIA" ("IDResource")
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

ALTER TABLE public."SysChatIAResource"
    ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS response_order integer NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public."SysChatIAResource"'::regclass
          AND conname = 'PK_SysChatIAResource'
          AND pg_get_constraintdef(oid) LIKE '%"IDSession"%'
    ) THEN
        ALTER TABLE public."SysChatIAResource"
            DROP CONSTRAINT "PK_SysChatIAResource";
        ALTER TABLE public."SysChatIAResource"
            ALTER COLUMN "IDSession" DROP NOT NULL;
        ALTER TABLE public."SysChatIAResource"
            ADD CONSTRAINT "PK_SysChatIAResource"
            PRIMARY KEY ("IDResource", "IDWorkRoom");
    END IF;
END
$$;

ALTER TABLE public."SysChatIAResource"
    DROP COLUMN IF EXISTS "IDSession";

COMMENT ON TABLE public."SysChatIAResource" IS
    'Sesiones y salas asociadas a cada recurso de IA de SolidSET.';

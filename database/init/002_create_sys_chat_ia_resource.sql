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
    "IDSession" uuid NOT NULL,
    CONSTRAINT "PK_SysChatIAResource"
        PRIMARY KEY ("IDResource", "IDWorkRoom", "IDSession"),
    CONSTRAINT "FK_SysChatIAResource_SysResourceIA_IDResource"
        FOREIGN KEY ("IDResource")
        REFERENCES public."SysResourceIA" ("IDResource")
        ON UPDATE CASCADE
        ON DELETE CASCADE
);

COMMENT ON TABLE public."SysChatIAResource" IS
    'Sesiones y salas asociadas a cada recurso de IA de SolidSET.';

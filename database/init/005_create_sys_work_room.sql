CREATE TABLE IF NOT EXISTS public."SysWorkRoom" (
    "IDWorkRoom" uuid NOT NULL,
    "Code" varchar(20),
    "Name" varchar(100),
    "Description" varchar(200),
    CONSTRAINT "PK_SysWorkRoom" PRIMARY KEY ("IDWorkRoom")
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SysChatIAResource_SysWorkRoom') THEN
        ALTER TABLE public."SysChatIAResource"
            ADD CONSTRAINT "FK_SysChatIAResource_SysWorkRoom"
            FOREIGN KEY ("IDWorkRoom") REFERENCES public."SysWorkRoom" ("IDWorkRoom")
            ON UPDATE CASCADE ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SysResourceIAKnowledge_SysWorkRoom') THEN
        ALTER TABLE public."SysResourceIAKnowledge"
            ADD CONSTRAINT "FK_SysResourceIAKnowledge_SysWorkRoom"
            FOREIGN KEY ("IDWorkRoom") REFERENCES public."SysWorkRoom" ("IDWorkRoom")
            ON UPDATE CASCADE ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'FK_SysAgentIASession_SysWorkRoom') THEN
        ALTER TABLE public."SysAgentIASession"
            ADD CONSTRAINT "FK_SysAgentIASession_SysWorkRoom"
            FOREIGN KEY ("IDWorkRoom") REFERENCES public."SysWorkRoom" ("IDWorkRoom")
            ON UPDATE CASCADE ON DELETE CASCADE;
    END IF;
END
$$;

COMMENT ON TABLE public."SysWorkRoom" IS
    'Canales sincronizados desde dbo.SysWorkRoom de SolidSET.';

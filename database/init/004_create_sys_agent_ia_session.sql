CREATE TABLE IF NOT EXISTS public."SysAgentIASession" (
    "IDSession" uuid NOT NULL,
    "IDResource" uuid NOT NULL,
    "IDWorkRoom" uuid NOT NULL,
    "CreatedAt" timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "LastActivityAt" timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "Status" varchar(20) NOT NULL DEFAULT 'active',
    CONSTRAINT "PK_SysAgentIASession"
        PRIMARY KEY ("IDSession", "IDResource"),
    CONSTRAINT "FK_SysAgentIASession_SysChatIAResource"
        FOREIGN KEY ("IDResource", "IDWorkRoom")
        REFERENCES public."SysChatIAResource" ("IDResource", "IDWorkRoom")
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT "CK_SysAgentIASession_Status"
        CHECK ("Status" IN ('active', 'closed', 'error'))
);

CREATE INDEX IF NOT EXISTS "IX_SysAgentIASession_Active"
    ON public."SysAgentIASession" ("IDResource", "IDWorkRoom", "LastActivityAt")
    WHERE "Status" = 'active';

COMMENT ON TABLE public."SysAgentIASession" IS
    'Sesiones de conversación independientes por agente y canal.';

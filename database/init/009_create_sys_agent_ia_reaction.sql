CREATE TABLE IF NOT EXISTS public."SysAgentIAReaction" (
    "ID" uuid NOT NULL DEFAULT gen_random_uuid(),
    "IDChat" bigint NOT NULL,
    "IDUser" uuid NOT NULL,
    "IDChannel" uuid NOT NULL,
    "IDEmoji" varchar(64) NOT NULL,
    "Counter" integer NOT NULL DEFAULT 0,
    "Signal" varchar(32) NOT NULL,
    "IDAgentResource" uuid NOT NULL,
    "AgentResponse" text NOT NULL,
    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "PK_SysAgentIAReaction" PRIMARY KEY ("ID"),
    CONSTRAINT "UQ_SysAgentIAReaction_ChatUserEmoji"
        UNIQUE ("IDChat", "IDUser", "IDEmoji"),
    CONSTRAINT "FK_SysAgentIAReaction_SysResourceIA"
        FOREIGN KEY ("IDAgentResource")
        REFERENCES public."SysResourceIA" ("IDResource")
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAReaction_AgentResource"
    ON public."SysAgentIAReaction" ("IDAgentResource", "UpdatedAt" DESC);

COMMENT ON TABLE public."SysAgentIAReaction" IS
    'Reacciones de usuarios a respuestas emitidas por agentes IA en SolidSET.';

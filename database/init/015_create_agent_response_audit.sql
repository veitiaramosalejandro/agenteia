CREATE TABLE IF NOT EXISTS public."SysAgentIAResponseAudit" (
    "RequestID" varchar(100) PRIMARY KEY,
    "IDChat2" varchar(100),
    "Status" varchar(30) NOT NULL,
    "Code" integer NOT NULL DEFAULT 0,
    "ResponseCount" integer NOT NULL DEFAULT 0,
    "RequestPayload" jsonb,
    "Result" jsonb,
    "Error" text,
    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "CompletedAt" timestamptz
);

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAResponseAudit_IDChat2"
    ON public."SysAgentIAResponseAudit" ("IDChat2");

CREATE TABLE IF NOT EXISTS public."SysAgentIAPrompt" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDSolidSETInstance" uuid NOT NULL,
  "IDResource" uuid NOT NULL,
  "Version" integer NOT NULL,
  "Name" varchar(255) NOT NULL,
  "SystemPrompt" text NOT NULL,
  "BehaviorConfig" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "SourceSnapshot" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "SourceHash" varchar(64),
  "Status" varchar(20) NOT NULL DEFAULT 'draft',
  "CreatedBy" varchar(255),
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "PublishedAt" timestamptz,
  "RetiredAt" timestamptz,
  CONSTRAINT "FK_SysAgentIAPrompt_InstanceResource" FOREIGN KEY
    ("IDSolidSETInstance", "IDResource") REFERENCES
    public."SysSolidSETInstanceResource" ("IDSolidSETInstance", "IDResource")
    ON DELETE CASCADE,
  CONSTRAINT "UQ_SysAgentIAPrompt_Version" UNIQUE
    ("IDSolidSETInstance", "IDResource", "Version"),
  CONSTRAINT "CK_SysAgentIAPrompt_Version" CHECK ("Version" > 0),
  CONSTRAINT "CK_SysAgentIAPrompt_Status" CHECK
    ("Status" IN ('draft', 'active', 'retired')),
  CONSTRAINT "CK_SysAgentIAPrompt_Length" CHECK
    (char_length("SystemPrompt") BETWEEN 1 AND 12000)
);

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysAgentIAPrompt_Active"
  ON public."SysAgentIAPrompt" ("IDSolidSETInstance", "IDResource")
  WHERE "Status" = 'active';

CREATE TABLE IF NOT EXISTS public."SysAgentIAScope" (
  "IDSolidSETInstance" uuid NOT NULL,
  "IDResource" uuid NOT NULL,
  "IDLogin" uuid NOT NULL,
  "IDWorkRoom" uuid NOT NULL,
  "IDCommunity" uuid NOT NULL,
  "OrganizationID" varchar(255),
  "OrganizationNo" varchar(255),
  "OrganizationName" text,
  "DisplayName" text,
  "FullName" text,
  "WorkRoomCode" text,
  "WorkRoomName" text,
  "CommunityCode" text,
  "CommunityName" text,
  "CommunityDescription" text,
  "ResourceAccessType" smallint NOT NULL,
  "SourceHash" varchar(64) NOT NULL,
  "LastSeenAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  active boolean NOT NULL DEFAULT true,
  PRIMARY KEY ("IDSolidSETInstance", "IDResource", "IDLogin", "IDWorkRoom", "IDCommunity"),
  CONSTRAINT "FK_SysAgentIAScope_InstanceResource" FOREIGN KEY
    ("IDSolidSETInstance", "IDResource") REFERENCES
    public."SysSolidSETInstanceResource" ("IDSolidSETInstance", "IDResource")
    ON DELETE CASCADE,
  CONSTRAINT "CK_SysAgentIAScope_Access" CHECK ("ResourceAccessType" BETWEEN 0 AND 3)
);

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAScope_Authorization"
  ON public."SysAgentIAScope"
  ("IDSolidSETInstance", "IDResource", "IDWorkRoom", "ResourceAccessType")
  WHERE active = true;

CREATE TABLE IF NOT EXISTS public."SysAgentIAPromptAudit" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "RequestID" varchar(100),
  "IDSolidSETInstance" uuid NOT NULL,
  "IDResource" uuid NOT NULL,
  "IDWorkRoom" uuid,
  "IDPrompt" uuid NOT NULL REFERENCES public."SysAgentIAPrompt"("ID") ON DELETE RESTRICT,
  "PromptVersion" integer NOT NULL,
  "CompiledPromptHash" varchar(64) NOT NULL,
  "PromptChars" integer NOT NULL,
  "AppliedScope" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "CK_SysAgentIAPromptAudit_Chars" CHECK ("PromptChars" >= 0)
);

CREATE INDEX IF NOT EXISTS "IX_SysAgentIAPromptAudit_Request"
  ON public."SysAgentIAPromptAudit" ("RequestID", "CreatedAt" DESC);

COMMENT ON TABLE public."SysAgentIAPrompt" IS
  'Plantillas manuales, inmutables por version y con una sola version activa por agente e instancia.';
COMMENT ON TABLE public."SysAgentIAScope" IS
  'Instantanea del alcance SolidSET usada para autorizacion; SQL Server permanece como fuente de verdad.';

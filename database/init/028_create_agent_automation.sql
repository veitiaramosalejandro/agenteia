CREATE TABLE IF NOT EXISTS public."SysAgentIAAutomationRule" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDSolidSETInstance" uuid NOT NULL
    REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "IDResource" uuid NOT NULL,
  "IDWorkRoom" uuid NOT NULL,
  "Name" varchar(160) NOT NULL,
  "TriggerType" varchar(30) NOT NULL DEFAULT 'manual'
    CHECK ("TriggerType" IN ('manual', 'selected_message')),
  "Instruction" text NOT NULL DEFAULT '',
  "RequiredCapabilities" jsonb NOT NULL DEFAULT '[]'::jsonb,
  "MaxRunsPerHour" integer NOT NULL DEFAULT 10
    CHECK ("MaxRunsPerHour" BETWEEN 1 AND 1000),
  "RequireApproval" boolean NOT NULL DEFAULT true,
  active boolean NOT NULL DEFAULT true,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY ("IDSolidSETInstance", "IDResource")
    REFERENCES public."SysSolidSETInstanceResource"("IDSolidSETInstance", "IDResource")
    ON DELETE CASCADE,
  FOREIGN KEY ("IDSolidSETInstance", "IDWorkRoom")
    REFERENCES public."SysSolidSETInstanceWorkRoom"("IDSolidSETInstance", "IDWorkRoom")
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS "IX_AutomationRule_Instance_Agent_Room"
  ON public."SysAgentIAAutomationRule"
  ("IDSolidSETInstance", "IDResource", "IDWorkRoom", active);

CREATE TABLE IF NOT EXISTS public."SysAgentIAAutomationRun" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDRule" uuid NOT NULL
    REFERENCES public."SysAgentIAAutomationRule"("ID") ON DELETE CASCADE,
  "Status" varchar(30) NOT NULL,
  "InputText" text NOT NULL,
  "OutputText" text,
  "Approved" boolean NOT NULL DEFAULT false,
  "SentToSolidSET" boolean NOT NULL DEFAULT false,
  "Error" text,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "CompletedAt" timestamptz
);

CREATE INDEX IF NOT EXISTS "IX_AutomationRun_Rule_CreatedAt"
  ON public."SysAgentIAAutomationRun" ("IDRule", "CreatedAt" DESC);

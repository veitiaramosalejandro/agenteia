CREATE TABLE IF NOT EXISTS public."SysAgentIAAdminUser" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "Username" varchar(120) NOT NULL UNIQUE,
  "DisplayName" varchar(180) NOT NULL,
  "PasswordHash" text NOT NULL,
  "PasswordSalt" text NOT NULL,
  "Role" varchar(30) NOT NULL CHECK ("Role" IN ('administrator', 'operator', 'auditor')),
  active boolean NOT NULL DEFAULT true,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "LastLoginAt" timestamptz
);

CREATE TABLE IF NOT EXISTS public."SysAgentIAAdminSession" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDUser" uuid NOT NULL REFERENCES public."SysAgentIAAdminUser"("ID") ON DELETE CASCADE,
  "TokenHash" char(64) NOT NULL UNIQUE,
  "ExpiresAt" timestamptz NOT NULL,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "RevokedAt" timestamptz
);
CREATE INDEX IF NOT EXISTS "IX_AdminSession_Active"
  ON public."SysAgentIAAdminSession" ("TokenHash", "ExpiresAt") WHERE "RevokedAt" IS NULL;

CREATE TABLE IF NOT EXISTS public."SysAgentIAApproval" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDSolidSETInstance" uuid REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "Operation" varchar(80) NOT NULL,
  "ResourceType" varchar(80) NOT NULL,
  "ResourceID" varchar(180) NOT NULL,
  "Reason" varchar(500) NOT NULL,
  "Status" varchar(20) NOT NULL DEFAULT 'pending'
    CHECK ("Status" IN ('pending', 'approved', 'rejected', 'consumed', 'cancelled')),
  "RequestedBy" uuid NOT NULL REFERENCES public."SysAgentIAAdminUser"("ID"),
  "DecidedBy" uuid REFERENCES public."SysAgentIAAdminUser"("ID"),
  "DecisionNote" varchar(500),
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "DecidedAt" timestamptz,
  "ConsumedAt" timestamptz
);
CREATE INDEX IF NOT EXISTS "IX_AdminApproval_Instance_Status"
  ON public."SysAgentIAApproval" ("IDSolidSETInstance", "Status", "CreatedAt" DESC);

CREATE TABLE IF NOT EXISTS public."SysAgentIAChangeHistory" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDSolidSETInstance" uuid REFERENCES public."SysSolidSETInstance"("ID") ON DELETE SET NULL,
  "IDUser" uuid REFERENCES public."SysAgentIAAdminUser"("ID") ON DELETE SET NULL,
  "Action" varchar(80) NOT NULL,
  "ResourceType" varchar(80) NOT NULL,
  "ResourceID" varchar(180),
  "Method" varchar(10) NOT NULL,
  "Path" varchar(500) NOT NULL,
  "StatusCode" integer NOT NULL,
  "BeforeState" jsonb,
  "AfterState" jsonb,
  "RestoredFrom" uuid REFERENCES public."SysAgentIAChangeHistory"("ID") ON DELETE SET NULL,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS "IX_AdminChange_Instance_CreatedAt"
  ON public."SysAgentIAChangeHistory" ("IDSolidSETInstance", "CreatedAt" DESC);


CREATE TABLE IF NOT EXISTS public."SysSolidSETDatabase" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDSolidSETInstance" uuid NOT NULL UNIQUE
    REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "Host" varchar(255) NOT NULL,
  "InstanceName" varchar(255),
  "Port" integer NOT NULL DEFAULT 1433,
  "DatabaseName" varchar(255) NOT NULL,
  "Username" varchar(255) NOT NULL,
  "EncryptedPassword" text NOT NULL,
  "Encrypt" boolean NOT NULL DEFAULT true,
  "TrustServerCertificate" boolean NOT NULL DEFAULT false,
  "ConnectionTimeout" integer NOT NULL DEFAULT 15,
  "SchemaVersion" varchar(80),
  "AdapterCode" varchar(80) NOT NULL DEFAULT 'solidset-v1',
  active boolean NOT NULL DEFAULT true,
  "LastConnectionAt" timestamptz,
  "LastConnectionStatus" varchar(30),
  "LastConnectionError" text,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "CK_SysSolidSETDatabase_Port" CHECK ("Port" BETWEEN 0 AND 65535)
);

CREATE TABLE IF NOT EXISTS public."SysSolidSETDataAPI" (
  "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "IDSolidSETInstance" uuid NOT NULL UNIQUE
    REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "BaseUrl" varchar(500) NOT NULL,
  "EncryptedAPIKey" text NOT NULL,
  "TimeoutSeconds" integer NOT NULL DEFAULT 120,
  "MaxRows" integer NOT NULL DEFAULT 5000,
  "VerifyTLS" boolean NOT NULL DEFAULT true,
  active boolean NOT NULL DEFAULT true,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public."SysSolidSETInstanceResource" (
  "IDSolidSETInstance" uuid NOT NULL
    REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "IDResource" uuid NOT NULL,
  active boolean NOT NULL DEFAULT true,
  "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("IDSolidSETInstance", "IDResource")
);

CREATE TABLE IF NOT EXISTS public."SysSolidSETInstanceLogin" (
  "IDSolidSETInstance" uuid NOT NULL
    REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "IDLogin" uuid NOT NULL,
  "Username" text,
  "FullName" text,
  "Password" text,
  "Salt" text,
  "LastIDResource" uuid,
  "ActiveIDLogin2Resource" uuid,
  PRIMARY KEY ("IDSolidSETInstance", "IDLogin")
);
CREATE INDEX IF NOT EXISTS "IX_SysSolidSETInstanceLogin_Resource"
  ON public."SysSolidSETInstanceLogin" ("IDSolidSETInstance", "LastIDResource");

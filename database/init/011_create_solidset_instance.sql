CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public."SysSolidSETInstance" (
    "ID" uuid NOT NULL DEFAULT gen_random_uuid(),
    "Code" varchar(80) NOT NULL,
    "Name" varchar(255) NOT NULL,
    "BaseUrl" varchar(500) NOT NULL,
    "NotificationUrl" varchar(500) NULL,
    "SourceIP" varchar(255) NULL,
    "CountryCode" varchar(2) NOT NULL DEFAULT 'PT',
    "Locale" varchar(20) NOT NULL DEFAULT 'pt-PT',
    "TimeZone" varchar(80) NOT NULL DEFAULT 'Europe/Lisbon',
    active boolean NOT NULL DEFAULT true,
    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "PK_SysSolidSETInstance" PRIMARY KEY ("ID"),
    CONSTRAINT "UQ_SysSolidSETInstance_Code" UNIQUE ("Code")
);

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysSolidSETInstance_SourceIP"
    ON public."SysSolidSETInstance" ("SourceIP")
    WHERE NULLIF(BTRIM("SourceIP"), '') IS NOT NULL;

CREATE INDEX IF NOT EXISTS "IX_SysSolidSETInstance_Active"
    ON public."SysSolidSETInstance" (active, "Code");

COMMENT ON TABLE public."SysSolidSETInstance" IS
    'Instancias SolidSET atendidas por la API; determina el origen y la URL de respuesta.';

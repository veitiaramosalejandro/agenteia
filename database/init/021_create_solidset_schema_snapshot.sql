CREATE TABLE IF NOT EXISTS public."SysSolidSETSchemaSnapshot" (
  "IDSolidSETInstance" uuid PRIMARY KEY
    REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
  "DatabaseName" varchar(255),
  "SchemaHash" varchar(64) NOT NULL,
  "Catalog" jsonb NOT NULL,
  "CapturedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

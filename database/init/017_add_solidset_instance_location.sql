ALTER TABLE public."SysSolidSETInstance"
    ADD COLUMN IF NOT EXISTS "CountryCode" varchar(2) NOT NULL DEFAULT 'PT',
    ADD COLUMN IF NOT EXISTS "Locale" varchar(20) NOT NULL DEFAULT 'pt-PT',
    ADD COLUMN IF NOT EXISTS "TimeZone" varchar(80) NOT NULL DEFAULT 'Europe/Lisbon';

COMMENT ON COLUMN public."SysSolidSETInstance"."CountryCode" IS
    'ISO 3166-1 alpha-2 country used as regional context for agent responses.';
COMMENT ON COLUMN public."SysSolidSETInstance"."Locale" IS
    'BCP 47 locale used for language variant and formatting, for example pt-PT.';
COMMENT ON COLUMN public."SysSolidSETInstance"."TimeZone" IS
    'IANA time zone used for deterministic local dates and times.';

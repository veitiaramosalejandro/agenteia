ALTER TABLE public."SysLLMProviderConfiguration"
  ADD COLUMN IF NOT EXISTS "OpenAIOrganization" varchar(255),
  ADD COLUMN IF NOT EXISTS "OpenAIProject" varchar(255),
  ADD COLUMN IF NOT EXISTS "UseResponsesAPI" boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS "StoreResponses" boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS "MaxRetries" integer NOT NULL DEFAULT 2,
  ADD COLUMN IF NOT EXISTS "ServiceTier" varchar(20) NOT NULL DEFAULT 'auto';

ALTER TABLE public."SysLLMProviderConfiguration"
  DROP CONSTRAINT IF EXISTS "CK_SysLLMProviderConfiguration_MaxRetries";
ALTER TABLE public."SysLLMProviderConfiguration"
  ADD CONSTRAINT "CK_SysLLMProviderConfiguration_MaxRetries"
    CHECK ("MaxRetries" BETWEEN 0 AND 5);

ALTER TABLE public."SysLLMProviderConfiguration"
  DROP CONSTRAINT IF EXISTS "CK_SysLLMProviderConfiguration_ServiceTier";
ALTER TABLE public."SysLLMProviderConfiguration"
  ADD CONSTRAINT "CK_SysLLMProviderConfiguration_ServiceTier"
    CHECK ("ServiceTier" IN ('auto', 'default', 'flex', 'priority', 'fast'));

COMMENT ON COLUMN public."SysLLMProviderConfiguration"."UseResponsesAPI" IS
  'Usa POST /responses para proveedores OpenAI oficiales.';
COMMENT ON COLUMN public."SysLLMProviderConfiguration"."StoreResponses" IS
  'Control explícito de retención remota; false por defecto.';

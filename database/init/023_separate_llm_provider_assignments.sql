BEGIN;
SELECT pg_advisory_xact_lock(hashtext('llm-provider-model-schema'));

    CREATE TABLE IF NOT EXISTS public."SysLLMProviderConfiguration" (
        "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        "Code" varchar(80) NOT NULL UNIQUE,
        "Name" varchar(255) NOT NULL,
        "Provider" varchar(40) NOT NULL,
        "Model" varchar(255) NOT NULL,
        "BaseUrl" varchar(500), "APIKey" text,
        "Temperature" double precision NOT NULL DEFAULT 0.5,
        "MaxOutputTokens" integer NOT NULL DEFAULT 1024,
        "TimeoutSeconds" integer NOT NULL DEFAULT 60,
        "AzureEndpoint" varchar(500), "AzureApiVersion" varchar(80),
        "AzureDeployment" varchar(255),
        "IsDefault" boolean NOT NULL DEFAULT false,
        active boolean NOT NULL DEFAULT true,
        "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT "CK_SysLLMProviderConfiguration_Temperature"
          CHECK ("Temperature" >= 0 AND "Temperature" <= 2),
        CONSTRAINT "CK_SysLLMProviderConfiguration_MaxOutputTokens"
          CHECK ("MaxOutputTokens" > 0),
        CONSTRAINT "CK_SysLLMProviderConfiguration_TimeoutSeconds"
          CHECK ("TimeoutSeconds" > 0)
    );
    ALTER TABLE public."SysLLMProviderConfiguration"
      ADD COLUMN IF NOT EXISTS "OpenAIOrganization" varchar(255),
      ADD COLUMN IF NOT EXISTS "OpenAIProject" varchar(255),
      ADD COLUMN IF NOT EXISTS "UseResponsesAPI" boolean NOT NULL DEFAULT true,
      ADD COLUMN IF NOT EXISTS "StoreResponses" boolean NOT NULL DEFAULT false,
      ADD COLUMN IF NOT EXISTS "MaxRetries" integer NOT NULL DEFAULT 2,
      ADD COLUMN IF NOT EXISTS "ServiceTier" varchar(20) NOT NULL DEFAULT 'auto';

            CREATE TABLE IF NOT EXISTS public."SysAgentIAModel" (
              "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
              "IDResource" uuid NOT NULL REFERENCES public."SysResourceIA"("IDResource") ON DELETE CASCADE,
              "IDProviderConfiguration" uuid NOT NULL REFERENCES public."SysLLMProviderConfiguration"("ID") ON DELETE RESTRICT,
              "Role" varchar(80) NOT NULL DEFAULT 'general',
              "LocalExecution" boolean NOT NULL DEFAULT true,
              "TrainingMode" varchar(40) NOT NULL DEFAULT 'rag_reinforcement'
                CHECK ("TrainingMode" IN ('rag_reinforcement','rag_only','disabled')),
              "LearnFromOwner" boolean NOT NULL DEFAULT true,
              "LearnFromSystem" boolean NOT NULL DEFAULT true,
              "LearnFromReactions" boolean NOT NULL DEFAULT true,
              active boolean NOT NULL DEFAULT true,
              "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
              "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            DROP INDEX IF EXISTS public."UQ_SysAgentIAModel_ActiveResource";
            ALTER TABLE public."SysAgentIAModel"
              ADD COLUMN IF NOT EXISTS "Capabilities" jsonb NOT NULL DEFAULT '["general"]'::jsonb,
              ADD COLUMN IF NOT EXISTS "Priority" integer NOT NULL DEFAULT 100,
              ADD COLUMN IF NOT EXISTS "IsDefault" boolean NOT NULL DEFAULT false;
            CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysAgentIAModel_ResourceProvider"
              ON public."SysAgentIAModel" ("IDResource", "IDProviderConfiguration") WHERE active=true;
            CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysAgentIAModel_DefaultResource"
              ON public."SysAgentIAModel" ("IDResource") WHERE active=true AND "IsDefault"=true;
            
DO $migration$
BEGIN
 IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public'
            AND table_name='SysLLMProviderConfiguration' AND column_name='IDResource') THEN
   INSERT INTO public."SysAgentIAModel" (
     "IDResource", "IDProviderConfiguration", "Role", "LocalExecution",
     "Capabilities", "IsDefault", active
   )
   SELECT p."IDResource", p."ID", 'general',
          lower(p."Provider") IN ('ollama','local_openai','openai_compatible'),
          '["general"]'::jsonb,
          p.active AND NOT EXISTS (
            SELECT 1 FROM public."SysAgentIAModel" d
            WHERE d."IDResource"=p."IDResource" AND d.active AND d."IsDefault"
          ), p.active
   FROM public."SysLLMProviderConfiguration" p
   WHERE p."IDResource" IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM public."SysAgentIAModel" m
     WHERE m."IDResource"=p."IDResource" AND m."IDProviderConfiguration"=p."ID"
       AND (m.active OR NOT p.active)
   );
   UPDATE public."SysLLMProviderConfiguration" SET "IsDefault"=false
     WHERE "IDResource" IS NOT NULL;
   DROP INDEX IF EXISTS public."UQ_SysLLMProviderConfiguration_Default";
   ALTER TABLE public."SysLLMProviderConfiguration" DROP COLUMN "IDResource";
 END IF;
END $migration$;
CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysLLMProviderConfiguration_Default"
 ON public."SysLLMProviderConfiguration" ("IsDefault") WHERE "IsDefault" AND active;

COMMIT;

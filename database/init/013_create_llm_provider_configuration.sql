CREATE TABLE IF NOT EXISTS public."SysLLMProviderConfiguration" (
    "ID" uuid NOT NULL DEFAULT gen_random_uuid(),
    "Code" varchar(80) NOT NULL,
    "Name" varchar(255) NOT NULL,
    "Provider" varchar(40) NOT NULL,
    "Model" varchar(255) NOT NULL,
    "BaseUrl" varchar(500),
    "APIKey" text,
    "Temperature" double precision NOT NULL DEFAULT 0.5,
    "MaxOutputTokens" integer NOT NULL DEFAULT 1024,
    "TimeoutSeconds" integer NOT NULL DEFAULT 60,
    "AzureEndpoint" varchar(500),
    "AzureApiVersion" varchar(80),
    "AzureDeployment" varchar(255),
    "IDResource" uuid,
    "IsDefault" boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "PK_SysLLMProviderConfiguration" PRIMARY KEY ("ID"),
    CONSTRAINT "UQ_SysLLMProviderConfiguration_Code" UNIQUE ("Code"),
    CONSTRAINT "FK_SysLLMProviderConfiguration_SysResourceIA"
        FOREIGN KEY ("IDResource") REFERENCES public."SysResourceIA" ("IDResource")
        ON DELETE CASCADE,
    CONSTRAINT "CK_SysLLMProviderConfiguration_Temperature"
        CHECK ("Temperature" >= 0 AND "Temperature" <= 2),
    CONSTRAINT "CK_SysLLMProviderConfiguration_MaxOutputTokens"
        CHECK ("MaxOutputTokens" > 0),
    CONSTRAINT "CK_SysLLMProviderConfiguration_TimeoutSeconds"
        CHECK ("TimeoutSeconds" > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysLLMProviderConfiguration_Default"
    ON public."SysLLMProviderConfiguration" ("IsDefault")
    WHERE "IsDefault" = true AND active = true AND "IDResource" IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysLLMProviderConfiguration_ActiveResource"
    ON public."SysLLMProviderConfiguration" ("IDResource")
    WHERE active = true AND "IDResource" IS NOT NULL;

CREATE INDEX IF NOT EXISTS "IX_SysLLMProviderConfiguration_Provider"
    ON public."SysLLMProviderConfiguration" (active, "Provider", "Model");

COMMENT ON TABLE public."SysLLMProviderConfiguration" IS
    'Proveedores y modelos LLM globales o asignados a un agente SolidSET.';

INSERT INTO public."SysLLMProviderConfiguration" (
    "Code", "Name", "Provider", "Model", "BaseUrl", "Temperature",
    "MaxOutputTokens", "TimeoutSeconds", "IsDefault", active
) VALUES (
    'ollama-default', 'Ollama coordinador', 'ollama', 'qwen2.5:3b',
    'http://ollama-llm:11434', 0.5, 1024, 900, true, true
)
ON CONFLICT ("Code") DO NOTHING;

INSERT INTO public."SysLLMProviderConfiguration" (
    "Code", "Name", "Provider", "Model", "BaseUrl", "Temperature",
    "MaxOutputTokens", "TimeoutSeconds", "IsDefault", active
) VALUES
    ('ollama-coder', 'Ollama especialista código y SQL', 'ollama',
     'qwen2.5-coder:3b', 'http://ollama-llm:11434', 0.2, 1024, 900, false, true),
    ('ollama-secondary', 'Ollama agente general alternativo', 'ollama',
     'llama3.2:3b', 'http://ollama-llm:11434', 0.5, 1024, 900, false, true)
ON CONFLICT ("Code") DO NOTHING;

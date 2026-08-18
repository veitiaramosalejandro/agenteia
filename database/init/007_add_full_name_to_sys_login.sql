ALTER TABLE public."SysLogin"
    ADD COLUMN IF NOT EXISTS "FullName" text;

COMMENT ON COLUMN public."SysLogin"."FullName" IS
    'Nombre completo sincronizado desde dbo.SysLogin y usado como identidad visible del agente IA.';

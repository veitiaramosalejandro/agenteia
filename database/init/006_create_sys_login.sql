CREATE TABLE IF NOT EXISTS public."SysLogin" (
    "IDLogin" uuid NOT NULL,
    "Username" text,
    "Password" text,
    "Salt" text,
    "LastIDResource" uuid,
    "ActiveIDLogin2Resource" uuid,
    CONSTRAINT "PK_SysLogin" PRIMARY KEY ("IDLogin")
);

CREATE INDEX IF NOT EXISTS "IX_SysLogin_LastIDResource"
    ON public."SysLogin" ("LastIDResource");

CREATE INDEX IF NOT EXISTS "IX_SysLogin_ActiveIDLogin2Resource"
    ON public."SysLogin" ("ActiveIDLogin2Resource");

COMMENT ON TABLE public."SysLogin" IS
    'Credenciales de login sincronizadas desde dbo.SysLogin para autenticar cada recurso agente en SolidSET.';

COMMENT ON COLUMN public."SysLogin"."Password" IS
    'Dato sensible procedente de SolidSET; nunca debe devolverse desde la API ni escribirse en logs.';

COMMENT ON COLUMN public."SysLogin"."Salt" IS
    'Dato sensible procedente de SolidSET; nunca debe devolverse desde la API ni escribirse en logs.';

-- Run after llm_schema.sql. Resource row locks serialize creation and sync.
SELECT pg_advisory_xact_lock(hashtext('llm-provider-model-schema'));
CREATE OR REPLACE FUNCTION public.ensure_resource_default_model(resource_id uuid)
RETURNS void LANGUAGE plpgsql AS $body$
DECLARE
    provider_id uuid;
BEGIN
    PERFORM 1 FROM public."SysResourceIA" WHERE "IDResource"=resource_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public."SysAgentIAModel"
        WHERE "IDResource"=resource_id AND active AND "IsDefault"
    ) THEN
        RETURN;
    END IF;

    SELECT "ID" INTO provider_id FROM public."SysLLMProviderConfiguration"
    WHERE active AND lower("Provider")='ollama'
    ORDER BY "IsDefault" DESC, ("Code"='ollama-default') DESC, "Code"
    LIMIT 1;
    IF provider_id IS NULL THEN
        RAISE EXCEPTION 'An active Ollama provider is required to assign the resource default model';
    END IF;

    INSERT INTO public."SysAgentIAModel" (
        "IDResource", "IDProviderConfiguration", "Role", "LocalExecution",
        "Capabilities", "Priority", "IsDefault", active
    ) VALUES (resource_id, provider_id, 'general', true, '["general"]'::jsonb, 100, true, true)
    ON CONFLICT ("IDResource", "IDProviderConfiguration") WHERE active=true
    DO UPDATE SET "IsDefault"=true, "UpdatedAt"=CURRENT_TIMESTAMP;
END
$body$;

CREATE OR REPLACE FUNCTION public.assign_resource_default_model()
RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
    PERFORM public.ensure_resource_default_model(NEW."IDResource");
    RETURN NEW;
END
$body$;

CREATE OR REPLACE TRIGGER "TR_SysResourceIA_DefaultModel"
AFTER INSERT OR UPDATE ON public."SysResourceIA"
FOR EACH ROW EXECUTE FUNCTION public.assign_resource_default_model();

-- Idempotent backfill; specialist models and existing defaults are preserved.
SELECT public.ensure_resource_default_model(r."IDResource")
FROM public."SysResourceIA" r
WHERE NOT EXISTS (
    SELECT 1 FROM public."SysAgentIAModel" m
    WHERE m."IDResource"=r."IDResource" AND m.active AND m."IsDefault"
)
ORDER BY r."IDResource";

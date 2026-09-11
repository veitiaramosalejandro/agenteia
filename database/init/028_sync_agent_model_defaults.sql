BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SELECT pg_advisory_xact_lock(hashtext('agent-model-instance-default'));

CREATE OR REPLACE FUNCTION public.ensure_resource_default_model(
  instance_id uuid, resource_id uuid
) RETURNS void LANGUAGE plpgsql AS $body$
DECLARE provider_id uuid;
BEGIN
  PERFORM 1 FROM public."SysSolidSETInstanceResource"
   WHERE "IDSolidSETInstance"=instance_id AND "IDResource"=resource_id
     AND active=true FOR UPDATE;
  IF NOT FOUND OR EXISTS (
    SELECT 1 FROM public."SysAgentIAModel"
     WHERE "IDSolidSETInstance"=instance_id AND "IDResource"=resource_id
       AND active AND "IsDefault"
  ) THEN RETURN; END IF;

  SELECT "ID" INTO provider_id FROM public."SysLLMProviderConfiguration"
   WHERE active AND lower("Provider")='ollama'
   ORDER BY "IsDefault" DESC, ("Code"='ollama-default') DESC, "Code" LIMIT 1;
  IF provider_id IS NULL THEN
    -- Resources can arrive before their provider. Provider synchronization below
    -- fills these assignments when an active Ollama configuration is registered.
    RETURN;
  END IF;

  INSERT INTO public."SysAgentIAModel" (
    "IDSolidSETInstance", "IDResource", "IDProviderConfiguration", "Role",
    "LocalExecution", "Capabilities", "Priority", "IsDefault", active
  ) VALUES (
    instance_id, resource_id, provider_id, 'general', true,
    '["general"]'::jsonb, 100, true, true
  ) ON CONFLICT ("IDSolidSETInstance", "IDResource", "IDProviderConfiguration")
    WHERE active=true DO UPDATE SET "IsDefault"=true,"UpdatedAt"=CURRENT_TIMESTAMP;
END $body$;

CREATE OR REPLACE FUNCTION public.assign_resource_default_model()
RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
  PERFORM public.ensure_resource_default_model(NEW."IDSolidSETInstance", NEW."IDResource");
  RETURN NEW;
END $body$;

DROP TRIGGER IF EXISTS "TR_SysResourceIA_DefaultModel" ON public."SysResourceIA";
DROP TRIGGER IF EXISTS "TR_SysSolidSETInstanceResource_DefaultModel"
  ON public."SysSolidSETInstanceResource";
CREATE TRIGGER "TR_SysSolidSETInstanceResource_DefaultModel"
AFTER INSERT OR UPDATE ON public."SysSolidSETInstanceResource"
FOR EACH ROW EXECUTE FUNCTION public.assign_resource_default_model();

CREATE OR REPLACE FUNCTION public.synchronize_agent_model_defaults(
  requested_instance uuid DEFAULT NULL
) RETURNS TABLE ("sourceRows" integer, inserted integer, promoted integer,
                 existing integer, "skippedNoProvider" integer)
LANGUAGE plpgsql AS $body$
DECLARE
  membership record;
  before_id uuid;
  after_id uuid;
  had_assignment boolean;
BEGIN
  "sourceRows" := 0; inserted := 0; promoted := 0;
  existing := 0; "skippedNoProvider" := 0;
  FOR membership IN
    SELECT ir."IDSolidSETInstance", ir."IDResource"
    FROM public."SysSolidSETInstanceResource" ir
    WHERE ir.active AND (requested_instance IS NULL OR ir."IDSolidSETInstance"=requested_instance)
    ORDER BY ir."IDSolidSETInstance", ir."IDResource"
    FOR UPDATE
  LOOP
    "sourceRows" := "sourceRows" + 1;
    SELECT m."ID" INTO before_id FROM public."SysAgentIAModel" m
      WHERE m."IDSolidSETInstance"=membership."IDSolidSETInstance"
        AND m."IDResource"=membership."IDResource" AND m.active AND m."IsDefault";
    IF before_id IS NOT NULL THEN
      existing := existing + 1;
      CONTINUE;
    END IF;
    SELECT EXISTS (
      SELECT 1 FROM public."SysAgentIAModel" m
      WHERE m."IDSolidSETInstance"=membership."IDSolidSETInstance"
        AND m."IDResource"=membership."IDResource" AND m.active
        AND m."IDProviderConfiguration"=(
          SELECT p."ID" FROM public."SysLLMProviderConfiguration" p
          WHERE p.active AND lower(p."Provider")='ollama'
          ORDER BY p."IsDefault" DESC, (p."Code"='ollama-default') DESC, p."Code" LIMIT 1
        )
    ) INTO had_assignment;
    PERFORM public.ensure_resource_default_model(membership."IDSolidSETInstance", membership."IDResource");
    SELECT m."ID" INTO after_id FROM public."SysAgentIAModel" m
      WHERE m."IDSolidSETInstance"=membership."IDSolidSETInstance"
        AND m."IDResource"=membership."IDResource" AND m.active AND m."IsDefault";
    IF after_id IS NULL THEN
      "skippedNoProvider" := "skippedNoProvider" + 1;
    ELSIF had_assignment THEN
      promoted := promoted + 1;
    ELSE
      inserted := inserted + 1;
    END IF;
  END LOOP;
  RETURN NEXT;
END $body$;

CREATE OR REPLACE FUNCTION public.sync_defaults_after_provider_change()
RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
  IF NEW.active AND lower(NEW."Provider")='ollama' THEN
    PERFORM public.synchronize_agent_model_defaults(NULL);
  END IF;
  RETURN NEW;
END $body$;

DROP TRIGGER IF EXISTS "TR_SysLLMProviderConfiguration_SyncDefaults"
  ON public."SysLLMProviderConfiguration";
CREATE TRIGGER "TR_SysLLMProviderConfiguration_SyncDefaults"
AFTER INSERT OR UPDATE OF active, "Provider", "IsDefault"
ON public."SysLLMProviderConfiguration"
FOR EACH ROW EXECUTE FUNCTION public.sync_defaults_after_provider_change();

SELECT * FROM public.synchronize_agent_model_defaults(NULL);
COMMIT;

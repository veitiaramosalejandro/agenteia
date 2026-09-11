BEGIN;
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
    RAISE EXCEPTION 'An active Ollama provider is required to assign the resource default model';
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

SELECT public.ensure_resource_default_model(ir."IDSolidSETInstance", ir."IDResource")
FROM public."SysSolidSETInstanceResource" ir
WHERE ir.active=true ORDER BY ir."IDSolidSETInstance", ir."IDResource";
COMMIT;

from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID
import hashlib
import json
import threading
import time
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.llm.secrets import decrypt_api_key, encrypt_api_key


_solidset_location_schema_lock = threading.Lock()
_solidset_location_schema_ready = False
_agent_default_model_schema_lock = threading.Lock()
_agent_default_model_schema_ready = False


def _postgres_connection(max_retries: int = 10, retry_delay: float = 1.5) -> psycopg.Connection:
    """Abre una conexión corta a la base PostgreSQL de la aplicación, reintentando durante el arranque."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return psycopg.connect(
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_PORT,
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                dbname=settings.POSTGRES_DB,
                row_factory=dict_row,
            )
        except psycopg.OperationalError as exc:
            last_exc = exc
            msg = str(exc).lower()
            transient = any(
                term in msg
                for term in (
                    "starting up",
                    "connection refused",
                    "could not connect",
                    "connection failed",
                    "timeout",
                    "errno 111",
                )
            )
            if transient and attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise exc
    if last_exc:
        raise last_exc


def ensure_solidset_instance_location_schema() -> None:
    """Adds deterministic regional context to existing PostgreSQL volumes."""
    global _solidset_location_schema_ready
    if _solidset_location_schema_ready:
        return
    with _solidset_location_schema_lock:
        if _solidset_location_schema_ready:
            return
        with _postgres_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute('''
                    ALTER TABLE public."SysSolidSETInstance"
                      ADD COLUMN IF NOT EXISTS "CountryCode" varchar(2) NOT NULL DEFAULT 'PT',
                      ADD COLUMN IF NOT EXISTS "Locale" varchar(20) NOT NULL DEFAULT 'pt-PT',
                      ADD COLUMN IF NOT EXISTS "TimeZone" varchar(80) NOT NULL DEFAULT 'Europe/Lisbon';
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS public."SysSolidSETDatabase" (
                      "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                      "IDSolidSETInstance" uuid NOT NULL UNIQUE
                        REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
                      "Host" varchar(255) NOT NULL,
                      "InstanceName" varchar(255),
                      "Port" integer NOT NULL DEFAULT 1433,
                      "DatabaseName" varchar(255) NOT NULL,
                      "Username" varchar(255) NOT NULL,
                      "EncryptedPassword" text NOT NULL,
                      "Encrypt" boolean NOT NULL DEFAULT true,
                      "TrustServerCertificate" boolean NOT NULL DEFAULT false,
                      "ConnectionTimeout" integer NOT NULL DEFAULT 15,
                      "SchemaVersion" varchar(80),
                      "AdapterCode" varchar(80) NOT NULL DEFAULT 'solidset-v1',
                      active boolean NOT NULL DEFAULT true,
                      "LastConnectionAt" timestamptz,
                      "LastConnectionStatus" varchar(30),
                      "LastConnectionError" text,
                      "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      CONSTRAINT "CK_SysSolidSETDatabase_Port" CHECK ("Port" BETWEEN 0 AND 65535)
                    );
                    CREATE TABLE IF NOT EXISTS public."SysSolidSETDataAPI" (
                      "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                      "IDSolidSETInstance" uuid NOT NULL UNIQUE
                        REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
                      "BaseUrl" varchar(500) NOT NULL,
                      "EncryptedAPIKey" text NOT NULL,
                      "TimeoutSeconds" integer NOT NULL DEFAULT 120,
                      "MaxRows" integer NOT NULL DEFAULT 5000,
                      "VerifyTLS" boolean NOT NULL DEFAULT true,
                      active boolean NOT NULL DEFAULT true,
                      "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS public."SysSolidSETInstanceResource" (
                      "IDSolidSETInstance" uuid NOT NULL
                        REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
                      "IDResource" uuid NOT NULL,
                      "IDAgentResource" uuid,
                      active boolean NOT NULL DEFAULT true,
                      "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      PRIMARY KEY ("IDSolidSETInstance", "IDResource")
                    );
                    ALTER TABLE public."SysSolidSETInstanceResource"
                      ADD COLUMN IF NOT EXISTS "IDAgentResource" uuid;
                    CREATE TABLE IF NOT EXISTS public."SysSolidSETInstanceLogin" (
                      "IDSolidSETInstance" uuid NOT NULL
                        REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
                      "IDLogin" uuid NOT NULL, "Username" text, "FullName" text,
                      "Password" text, "Salt" text, "LastIDResource" uuid,
                      "ActiveIDLogin2Resource" uuid,
                      PRIMARY KEY ("IDSolidSETInstance", "IDLogin")
                    );
                    CREATE INDEX IF NOT EXISTS "IX_SysSolidSETInstanceLogin_Resource"
                      ON public."SysSolidSETInstanceLogin" ("IDSolidSETInstance", "LastIDResource");
                    CREATE TABLE IF NOT EXISTS public."SysSolidSETSchemaSnapshot" (
                      "IDSolidSETInstance" uuid PRIMARY KEY
                        REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
                      "DatabaseName" varchar(255),
                      "SchemaHash" varchar(64) NOT NULL,
                      "Catalog" jsonb NOT NULL,
                      "CapturedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                ''')
            migration = Path(__file__).with_name("sync_instance_schema.sql").read_text(
                encoding="utf-8"
            )
            connection.execute(migration)
        _solidset_location_schema_ready = True


def save_solidset_schema_snapshot(
    instance_id: str | UUID, catalog: dict[str, Any]
) -> dict[str, Any]:
    """Persist the latest structured SQL schema for one SolidSET instance."""
    ensure_solidset_instance_location_schema()
    serialized = json.dumps(catalog, ensure_ascii=False, sort_keys=True, default=str)
    schema_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysSolidSETSchemaSnapshot" (
                    "IDSolidSETInstance", "DatabaseName", "SchemaHash", "Catalog"
                ) VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT ("IDSolidSETInstance") DO UPDATE SET
                    "DatabaseName"=EXCLUDED."DatabaseName",
                    "SchemaHash"=EXCLUDED."SchemaHash",
                    "Catalog"=EXCLUDED."Catalog",
                    "CapturedAt"=CURRENT_TIMESTAMP,
                    "UpdatedAt"=CURRENT_TIMESTAMP
                RETURNING *
                ''',
                (
                    instance_id,
                    str(catalog.get("databaseName") or ""),
                    schema_hash,
                    serialized,
                ),
            )
            row = cursor.fetchone()
    return dict(row or {})


def get_solidset_schema_snapshot(
    instance_id: str | UUID,
) -> dict[str, Any] | None:
    ensure_solidset_instance_location_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT * FROM public."SysSolidSETSchemaSnapshot" '
                'WHERE "IDSolidSETInstance"=%s',
                (instance_id,),
            )
            row = cursor.fetchone()
    return dict(row) if row else None


def ensure_solidset_agent_resource_schema() -> None:
    """Actualiza volúmenes PostgreSQL existentes para el recurso Software IA."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''
                ALTER TABLE public."SysResourceIA"
                  ADD COLUMN IF NOT EXISTS "IDAgentResource" uuid;
                CREATE UNIQUE INDEX IF NOT EXISTS "UQ_SysResourceIA_IDAgentResource"
                  ON public."SysResourceIA" ("IDAgentResource")
                  WHERE "IDAgentResource" IS NOT NULL;
            ''')


def ensure_agent_response_audit_schema() -> None:
    """Crea la auditoría durable de solicitudes y resultados del agente."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS public."SysAgentIAResponseAudit" (
                    "RequestID" varchar(100) PRIMARY KEY,
                    "IDChat2" varchar(100),
                    "Status" varchar(30) NOT NULL,
                    "Code" integer NOT NULL DEFAULT 0,
                    "ResponseCount" integer NOT NULL DEFAULT 0,
                    "RequestPayload" jsonb,
                    "Result" jsonb,
                    "Error" text,
                    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    "CompletedAt" timestamptz
                );
                CREATE INDEX IF NOT EXISTS "IX_SysAgentIAResponseAudit_IDChat2"
                  ON public."SysAgentIAResponseAudit" ("IDChat2");
                ALTER TABLE public."SysAgentIAResponseAudit"
                  ADD COLUMN IF NOT EXISTS "RequestPayload" jsonb,
                  ADD COLUMN IF NOT EXISTS "Result" jsonb;
            ''')


def ensure_agent_tool_audit_schema() -> None:
    """Creates durable audit storage for optional tool execution tracing."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS public."SysAgentIAToolAudit" (
                    "ID" uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    "ToolName" varchar(120) NOT NULL,
                    "Source" varchar(120) NOT NULL,
                    "Success" boolean NOT NULL,
                    "ElapsedSeconds" double precision NOT NULL DEFAULT 0,
                    "IDResource" uuid,
                    "SessionID" varchar(255),
                    "ErrorType" varchar(120),
                    "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS "IX_SysAgentIAToolAudit_Resource"
                  ON public."SysAgentIAToolAudit" ("IDResource", "CreatedAt");
            ''')


def save_agent_tool_audit(event: Any) -> None:
    """Persists a ToolAuditEvent without exposing tool arguments or content."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''INSERT INTO public."SysAgentIAToolAudit"
                   ("ToolName","Source","Success","ElapsedSeconds","IDResource","SessionID","ErrorType")
                   VALUES (%s,%s,%s,%s,%s::uuid,NULLIF(%s,''),%s)''',
                (
                    event.tool_name, event.source, event.success,
                    event.elapsed_seconds, event.agent_resource_id,
                    event.session_id, event.error_type,
                ),
            )


def save_agent_response_audit(
    request_id: str,
    chat_id: str,
    status: str,
    response_count: int = 0,
    error: str | None = None,
    request_payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    codes = {
        "queued": 0, "processing": 1, "searching": 2, "thinking": 3,
        "sending": 4, "completed": 5, "failed": 6, "cancelled": 7,
    }
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysAgentIAResponseAudit" (
                    "RequestID", "IDChat2", "Status", "Code", "ResponseCount",
                    "Error", "RequestPayload", "Result"
                ) VALUES (%s, NULLIF(%s, ''), %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT ("RequestID") DO UPDATE SET
                    "IDChat2" = COALESCE(EXCLUDED."IDChat2", public."SysAgentIAResponseAudit"."IDChat2"),
                    "Status" = EXCLUDED."Status",
                    "Code" = EXCLUDED."Code",
                    "ResponseCount" = EXCLUDED."ResponseCount",
                    "Error" = EXCLUDED."Error",
                    "RequestPayload" = COALESCE(EXCLUDED."RequestPayload", public."SysAgentIAResponseAudit"."RequestPayload"),
                    "Result" = COALESCE(EXCLUDED."Result", public."SysAgentIAResponseAudit"."Result"),
                    "UpdatedAt" = CURRENT_TIMESTAMP,
                    "CompletedAt" = CASE
                        WHEN EXCLUDED."Status" IN ('completed', 'failed', 'cancelled')
                        THEN CURRENT_TIMESTAMP ELSE NULL END
                ''',
                (
                    request_id, chat_id, status, codes.get(status, -1),
                    response_count, error,
                    __import__("json").dumps(request_payload, ensure_ascii=False, default=str)
                    if request_payload is not None else None,
                    __import__("json").dumps(result, ensure_ascii=False, default=str)
                    if result is not None else None,
                ),
            )


def save_solidset_instance(configuration: dict[str, Any]) -> dict[str, Any]:
    """Registra o actualiza por Code, BaseUrl o SourceIP sin crear duplicados."""
    ensure_solidset_instance_location_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "ID"
                FROM public."SysSolidSETInstance"
                WHERE LOWER(BTRIM("Code")) = LOWER(BTRIM(%s))
                   OR LOWER(RTRIM(BTRIM("BaseUrl"), '/')) =
                      LOWER(RTRIM(BTRIM(%s), '/'))
                   OR (NULLIF(%s::text, '') IS NOT NULL AND "SourceIP" = %s)
                ORDER BY
                    CASE WHEN LOWER(BTRIM("Code")) = LOWER(BTRIM(%s)) THEN 0
                         WHEN NULLIF(%s::text, '') IS NOT NULL AND "SourceIP" = %s THEN 1
                         ELSE 2 END
                LIMIT 1
                FOR UPDATE
                ''',
                (
                    configuration["Code"], configuration["BaseUrl"],
                    configuration.get("SourceIP"), configuration.get("SourceIP"),
                    configuration["Code"], configuration.get("SourceIP"),
                    configuration.get("SourceIP"),
                ),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    '''
                    UPDATE public."SysSolidSETInstance"
                    SET "Code" = %s,
                        "Name" = %s,
                        "BaseUrl" = %s,
                        "NotificationUrl" = %s,
                        "SourceIP" = %s,
                        "CountryCode" = %s,
                        "Locale" = %s,
                        "TimeZone" = %s,
                        active = %s,
                        "UpdatedAt" = CURRENT_TIMESTAMP
                    WHERE "ID" = %s
                    RETURNING *
                    ''',
                    (
                        configuration["Code"], configuration["Name"],
                        configuration["BaseUrl"], configuration.get("NotificationUrl"),
                        configuration.get("SourceIP"), configuration.get("CountryCode", "PT"),
                        configuration.get("Locale", "pt-PT"),
                        configuration.get("TimeZone", "Europe/Lisbon"),
                        configuration.get("active", True),
                        existing["ID"],
                    ),
                )
                row = cursor.fetchone()
                operation = "updated"
            else:
                cursor.execute(
                    '''
                    INSERT INTO public."SysSolidSETInstance" (
                        "Code", "Name", "BaseUrl", "NotificationUrl", "SourceIP",
                        "CountryCode", "Locale", "TimeZone", active
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    ''',
                    (
                        configuration["Code"], configuration["Name"],
                        configuration["BaseUrl"], configuration.get("NotificationUrl"),
                        configuration.get("SourceIP"), configuration.get("CountryCode", "PT"),
                        configuration.get("Locale", "pt-PT"),
                        configuration.get("TimeZone", "Europe/Lisbon"),
                        configuration.get("active", True),
                    ),
                )
                row = cursor.fetchone()
                operation = "created"
            data_api = configuration.get("DataAPI")
            if data_api:
                encrypted_key = encrypt_api_key(data_api.get("APIKey"))
                if not encrypted_key:
                    cursor.execute(
                        'SELECT "EncryptedAPIKey" FROM public."SysSolidSETDataAPI" '
                        'WHERE "IDSolidSETInstance"=%s', (row["ID"],),
                    )
                    previous = cursor.fetchone()
                    encrypted_key = previous["EncryptedAPIKey"] if previous else None
                if not encrypted_key:
                    raise ValueError("APIKey é obrigatória ao criar a SolidSET Data API.")
                cursor.execute('''
                  INSERT INTO public."SysSolidSETDataAPI" (
                    "IDSolidSETInstance", "BaseUrl", "EncryptedAPIKey",
                    "TimeoutSeconds", "MaxRows", "VerifyTLS", active
                  ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT ("IDSolidSETInstance") DO UPDATE SET
                    "BaseUrl"=EXCLUDED."BaseUrl",
                    "EncryptedAPIKey"=EXCLUDED."EncryptedAPIKey",
                    "TimeoutSeconds"=EXCLUDED."TimeoutSeconds",
                    "MaxRows"=EXCLUDED."MaxRows",
                    "VerifyTLS"=EXCLUDED."VerifyTLS",
                    active=EXCLUDED.active,
                    "UpdatedAt"=CURRENT_TIMESTAMP
                ''', (
                    row["ID"], data_api["BaseUrl"], encrypted_key,
                    data_api.get("TimeoutSeconds", 120), data_api.get("MaxRows", 5000),
                    data_api.get("VerifyTLS", True), data_api.get("active", True),
                ))
    if row is None:
        raise RuntimeError("PostgreSQL no devolvió la instancia SolidSET guardada.")
    result = dict(row)
    result["_operation"] = operation
    return result


def get_solidset_instance(
    *, code: str | None = None, source_ip: str | None = None,
    active_only: bool = True,
) -> dict[str, Any] | None:
    """Resuelve una instancia activa por código explícito o IP directa."""
    ensure_solidset_instance_location_schema()
    if not code and not source_ip:
        return None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT * FROM public."SysSolidSETInstance"
                WHERE (%s = false OR active = true)
                  AND ((NULLIF(%s::text, '') IS NOT NULL
                        AND LOWER("Code") = LOWER(%s::text))
                    OR (NULLIF(%s::text, '') IS NOT NULL
                        AND "SourceIP" = %s::text))
                ORDER BY CASE WHEN NULLIF(%s::text, '') IS NOT NULL
                                   AND LOWER("Code") = LOWER(%s::text)
                              THEN 0 ELSE 1 END
                LIMIT 1
                ''',
                (active_only, code, code, source_ip, source_ip, code, code),
            )
            row = cursor.fetchone()
            result = dict(row) if row else None
            if result:
                cursor.execute('SELECT * FROM public."SysSolidSETDataAPI" WHERE "IDSolidSETInstance"=%s', (result["ID"],))
                data_api = cursor.fetchone()
                result["DataAPI"] = dict(data_api) if data_api else None
    return result


def list_active_solidset_instances(*, active_only: bool = True) -> list[dict[str, Any]]:
    ensure_solidset_instance_location_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''SELECT * FROM public."SysSolidSETInstance"
              WHERE (%s = false OR active=true) ORDER BY "Code"''', (active_only,))
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                cursor.execute('SELECT * FROM public."SysSolidSETDataAPI" WHERE "IDSolidSETInstance"=%s', (row["ID"],))
                data_api = cursor.fetchone()
                row["DataAPI"] = dict(data_api) if data_api else None
            return rows


def ensure_llm_provider_schema() -> None:
    """Create the LLM schema and migrate legacy resource links atomically."""
    from pathlib import Path
    migration = Path(__file__).with_name("llm_schema.sql").read_text(encoding="utf-8")
    with _postgres_connection() as connection:
        connection.execute(migration)


def ensure_agent_model_schema() -> None:
    ensure_llm_provider_schema()
    global _agent_default_model_schema_ready
    if _agent_default_model_schema_ready:
        return
    from pathlib import Path
    with _agent_default_model_schema_lock:
        if _agent_default_model_schema_ready:
            return
        migration = Path(__file__).with_name("agent_default_model.sql").read_text(encoding="utf-8")
        with _postgres_connection() as connection:
            connection.execute(migration)
        _agent_default_model_schema_ready = True


def save_agent_model_configuration(resource_id: UUID | str, data: dict[str, Any]) -> dict[str, Any]:
    ensure_agent_model_schema()
    resource = UUID(str(resource_id))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT "IDResource" FROM public."SysResourceIA" WHERE "IDResource"=%s FOR UPDATE',
                (resource,),
            )
            requested_instance = data.get("IDSolidSETInstance")
            if requested_instance:
                instance_id = UUID(str(requested_instance))
                cursor.execute(
                    '''SELECT 1 FROM public."SysSolidSETInstanceResource"
                       WHERE "IDSolidSETInstance"=%s AND "IDResource"=%s AND active=true''',
                    (instance_id, resource),
                )
                if cursor.fetchone() is None:
                    raise LookupError("El recurso no pertenece a la instancia indicada.")
            else:
                cursor.execute(
                    '''SELECT "IDSolidSETInstance"
                       FROM public."SysSolidSETInstanceResource"
                       WHERE "IDResource"=%s AND active=true
                       ORDER BY "IDSolidSETInstance"''',
                    (resource,),
                )
                memberships = cursor.fetchall()
                if len(memberships) != 1:
                    raise ValueError(
                        "IDSolidSETInstance es obligatorio cuando el recurso no pertenece exactamente a una instancia."
                    )
                instance_id = memberships[0]["IDSolidSETInstance"]
            cursor.execute(
                'SELECT "ID", "Provider" FROM public."SysLLMProviderConfiguration" '
                'WHERE LOWER("Code")=LOWER(%s) AND active=true', (data["ProviderCode"],),
            )
            provider = cursor.fetchone()
            if not provider:
                raise LookupError("La configuración de proveedor no existe o está inactiva.")
            local_execution = bool(data.get("LocalExecution", True))
            if local_execution and provider["Provider"] not in {
                "ollama", "local_openai", "openai_compatible"
            }:
                raise ValueError(
                    "OpenAI, Azure, Anthropic y Gemini oficiales son servicios remotos; "
                    "usa LocalExecution=false o un servidor local_openai/openai_compatible."
                )
            if data.get("IsDefault") and data.get("active", True):
                cursor.execute(
                    'UPDATE public."SysAgentIAModel" SET "IsDefault"=false, '
                    '"UpdatedAt"=CURRENT_TIMESTAMP WHERE "IDSolidSETInstance"=%s '
                    'AND "IDResource"=%s AND active=true',
                    (instance_id, resource),
                )
            cursor.execute(
                '''INSERT INTO public."SysAgentIAModel" (
                  "IDSolidSETInstance", "IDResource", "IDProviderConfiguration", "Role", "LocalExecution",
                  "TrainingMode", "LearnFromOwner", "LearnFromSystem", "LearnFromReactions",
                  "Capabilities", "Priority", "IsDefault", active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                ON CONFLICT ("IDSolidSETInstance", "IDResource", "IDProviderConfiguration")
                  WHERE active=true DO UPDATE SET
                  "Role"=EXCLUDED."Role", "LocalExecution"=EXCLUDED."LocalExecution",
                  "TrainingMode"=EXCLUDED."TrainingMode", "LearnFromOwner"=EXCLUDED."LearnFromOwner",
                  "LearnFromSystem"=EXCLUDED."LearnFromSystem",
                  "LearnFromReactions"=EXCLUDED."LearnFromReactions",
                  "Capabilities"=EXCLUDED."Capabilities", "Priority"=EXCLUDED."Priority",
                  "IsDefault"=EXCLUDED."IsDefault", active=EXCLUDED.active,
                  "UpdatedAt"=CURRENT_TIMESTAMP RETURNING *''',
                (
                    instance_id, resource, provider["ID"], data.get("Role", "general"), local_execution,
                    data.get("TrainingMode", "rag_reinforcement"),
                    data.get("LearnFromOwner", True), data.get("LearnFromSystem", True),
                    data.get("LearnFromReactions", True),
                    __import__("json").dumps(data.get("Capabilities") or ["general"]),
                    data.get("Priority", 100), data.get("IsDefault", False),
                    data.get("active", True),
                ),
            )
            row = cursor.fetchone()
    return {**dict(row), "ProviderCode": data["ProviderCode"]}


def get_agent_model_configurations(
    resource_id: UUID | str,
    instance_id: UUID | str | None = None,
) -> list[dict[str, Any]]:
    ensure_agent_model_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT m.*, p."Code" AS "ProviderCode", p."Provider", p."Model", p."BaseUrl"
                   FROM public."SysAgentIAModel" m
                   JOIN public."SysLLMProviderConfiguration" p ON p."ID"=m."IDProviderConfiguration"
                   WHERE m."IDResource"=%s
                     AND (%s::uuid IS NULL OR m."IDSolidSETInstance"=%s::uuid)
                     AND m.active=true AND p.active=true
                   ORDER BY m."IsDefault" DESC, m."Priority", p."Code"''',
                (
                    UUID(str(resource_id)), instance_id, instance_id,
                ),
            )
            rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_agent_model_configuration(
    resource_id: UUID | str, instance_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    rows = get_agent_model_configurations(resource_id, instance_id)
    return rows[0] if rows else None


def agent_learning_enabled(
    resource_id: UUID | str,
    source: str,
    instance_id: UUID | str | None = None,
) -> bool:
    """Consulta la política de aprendizaje; sin asignación conserva compatibilidad."""
    config = get_agent_model_configuration(resource_id, instance_id)
    if not config or config.get("TrainingMode") == "disabled":
        return config is None
    field = {
        "owner": "LearnFromOwner",
        "system": "LearnFromSystem",
        "reactions": "LearnFromReactions",
    }.get(source)
    return bool(config.get(field, True)) if field else True


def _public_llm_configuration(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["HasAPIKey"] = bool(result.pop("APIKey", None))
    return result


def save_llm_provider_configuration(configuration: dict[str, Any], *, create_only: bool = False) -> dict[str, Any]:
    """UPSERT por Code y garantiza una sola configuración activa por ámbito."""
    ensure_llm_provider_schema()
    active = bool(configuration.get("active", True))
    is_default = bool(configuration.get("IsDefault", False))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(lower(%s)))", (configuration["Code"],))
            if create_only:
                cursor.execute('SELECT 1 FROM public."SysLLMProviderConfiguration" WHERE lower("Code")=lower(%s)', (configuration["Code"],))
                if cursor.fetchone():
                    raise FileExistsError("Ya existe una conexión con ese código.")
            if active and is_default:
                cursor.execute(
                    'UPDATE public."SysLLMProviderConfiguration" SET "IsDefault"=false, '
                    '"UpdatedAt"=CURRENT_TIMESTAMP WHERE "IsDefault"=true AND active=true '
                    'AND LOWER("Code")<>LOWER(%s)',
                    (configuration["Code"],),
                )
            cursor.execute(
                '''
                INSERT INTO public."SysLLMProviderConfiguration" (
                  "Code", "Name", "Provider", "Model", "BaseUrl", "APIKey",
                  "Temperature", "MaxOutputTokens", "TimeoutSeconds", "AzureEndpoint",
                  "AzureApiVersion", "AzureDeployment", "OpenAIOrganization", "OpenAIProject",
                  "UseResponsesAPI", "StoreResponses", "MaxRetries", "ServiceTier",
                  "IsDefault", active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT ("Code") DO UPDATE SET
                  "Name"=EXCLUDED."Name", "Provider"=EXCLUDED."Provider",
                  "Model"=EXCLUDED."Model", "BaseUrl"=EXCLUDED."BaseUrl",
                  "APIKey"=COALESCE(EXCLUDED."APIKey", public."SysLLMProviderConfiguration"."APIKey"),
                  "Temperature"=EXCLUDED."Temperature",
                  "MaxOutputTokens"=EXCLUDED."MaxOutputTokens",
                  "TimeoutSeconds"=EXCLUDED."TimeoutSeconds",
                  "AzureEndpoint"=EXCLUDED."AzureEndpoint",
                  "AzureApiVersion"=EXCLUDED."AzureApiVersion",
                  "AzureDeployment"=EXCLUDED."AzureDeployment",
                  "OpenAIOrganization"=EXCLUDED."OpenAIOrganization",
                  "OpenAIProject"=EXCLUDED."OpenAIProject",
                  "UseResponsesAPI"=EXCLUDED."UseResponsesAPI",
                  "StoreResponses"=EXCLUDED."StoreResponses",
                  "MaxRetries"=EXCLUDED."MaxRetries",
                  "ServiceTier"=EXCLUDED."ServiceTier",
                  "IsDefault"=EXCLUDED."IsDefault",
                  active=EXCLUDED.active, "UpdatedAt"=CURRENT_TIMESTAMP
                RETURNING *
                ''',
                (
                    configuration["Code"], configuration["Name"], configuration["Provider"],
                    configuration["Model"], configuration.get("BaseUrl"),
                    encrypt_api_key(configuration.get("APIKey")), configuration.get("Temperature", 0.5),
                    configuration.get("MaxOutputTokens", 1024),
                    configuration.get("TimeoutSeconds", 60), configuration.get("AzureEndpoint"),
                    configuration.get("AzureApiVersion"), configuration.get("AzureDeployment"),
                    configuration.get("OpenAIOrganization"), configuration.get("OpenAIProject"),
                    configuration.get("UseResponsesAPI", True),
                    configuration.get("StoreResponses", False),
                    configuration.get("MaxRetries", 2), configuration.get("ServiceTier", "auto"),
                    is_default, active,
                ),
            )
            row = cursor.fetchone()
    return _public_llm_configuration(dict(row))


def list_llm_provider_configurations() -> list[dict[str, Any]]:
    ensure_llm_provider_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM public."SysLLMProviderConfiguration" ORDER BY "Code"')
            return [_public_llm_configuration(dict(row)) for row in cursor.fetchall()]


def get_llm_provider_configuration(
    resource_id: UUID | str | None = None,
    capability: str | None = None,
    provider: str | None = None,
    instance_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    """Resuelve primero la configuración del agente y después la global."""
    ensure_llm_provider_schema()
    normalized = UUID(str(resource_id)) if resource_id else None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            requested_capability = str(capability or "general").strip().lower()
            cursor.execute(
                '''SELECT p.* FROM public."SysLLMProviderConfiguration" p
                   LEFT JOIN public."SysAgentIAModel" m
                     ON m."IDProviderConfiguration"=p."ID" AND m.active=true
                        AND m."IDResource"=%s::uuid
                        AND (%s::uuid IS NULL OR m."IDSolidSETInstance"=%s::uuid)
                   WHERE p.active=true AND (%s::text IS NULL OR lower(p."Provider")=%s)
                     AND ((m."ID" IS NOT NULL AND (m."Capabilities" ? %s OR m."IsDefault"))
                     OR p."IsDefault"=true)
                   ORDER BY CASE WHEN m."ID" IS NOT NULL AND m."Capabilities" ? %s THEN 0
                                 WHEN m."ID" IS NOT NULL AND m."IsDefault" THEN 1
                                 ELSE 2 END,
                            m."Priority" NULLS LAST, p."Code" LIMIT 1''',
                (
                    normalized, instance_id, instance_id, provider, provider,
                    requested_capability, requested_capability,
                ),
            )
            row = cursor.fetchone()
    if not row:
        return None
    result = dict(row)
    result["APIKey"] = decrypt_api_key(result.get("APIKey"))
    return result


def deactivate_llm_provider_configuration(code: str) -> bool:
    ensure_llm_provider_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE public."SysLLMProviderConfiguration" SET active=false, '
                '"IsDefault"=false, "UpdatedAt"=CURRENT_TIMESTAMP '
                'WHERE LOWER("Code")=LOWER(%s)', (code,),
            )
            return cursor.rowcount > 0


def save_sys_resource_ia(configuration: dict[str, Any]) -> dict[str, Any]:
    """Crea o actualiza la configuración canónica de un agente por IDResource."""
    values = (
        configuration.get("Name"),
        configuration.get("Stamp"),
        configuration.get("IDResource"),
        configuration.get("active", False),
        configuration.get("IDAgentResource"),
    )

    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysResourceIA" (
                    "Name", "Stamp", "IDResource", active, "IDAgentResource"
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT ("IDResource") DO UPDATE SET
                    "Name" = EXCLUDED."Name",
                    "Stamp" = EXCLUDED."Stamp",
                    active = EXCLUDED.active,
                    "IDAgentResource" = COALESCE(
                        EXCLUDED."IDAgentResource", public."SysResourceIA"."IDAgentResource"
                    )
                RETURNING *
                ''',
                values,
            )
            saved = cursor.fetchone()

    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió la configuración guardada.")
    return dict(saved)


def get_solidset_login_for_active_agent(
    resource_id: UUID | str,
    preferred_login_id: UUID | str | None = None,
    instance_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    """Obtiene internamente la cuenta de un agente activo; nunca exponer este resultado por API."""
    preferred = UUID(str(preferred_login_id)) if preferred_login_id else None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT l."IDLogin", l."Username", l."Password", l."Salt",
                       l."LastIDResource", l."ActiveIDLogin2Resource",
                       ir."IDAgentResource"
                FROM public."SysResourceIA" r
                INNER JOIN public."SysSolidSETInstanceResource" ir
                    ON ir."IDResource"=r."IDResource"
                   AND ir."IDSolidSETInstance"=%s
                   AND ir.active=true
                   AND ir."IDAgentResource" IS NOT NULL
                INNER JOIN public."SysSolidSETInstanceLogin" l
                    ON l."IDSolidSETInstance"=ir."IDSolidSETInstance"
                   AND (
                       (%s::uuid IS NOT NULL AND l."IDLogin"=%s)
                       OR (
                        r."ActiveIDLogin2Resource" IS NOT NULL
                        AND l."ActiveIDLogin2Resource" = r."ActiveIDLogin2Resource"
                       ) OR (
                        r."ActiveIDLogin2Resource" IS NULL
                        AND l."LastIDResource" = r."IDResource"
                       ) OR l."LastIDResource"=ir."IDAgentResource"
                   )
                WHERE r."IDResource" = %s
                  AND r.active = true
                  AND NULLIF(l."Username", '') IS NOT NULL
                  AND NULLIF(l."Password", '') IS NOT NULL
                ORDER BY CASE WHEN l."IDLogin" = %s THEN 0 ELSE 1 END,
                         l."IDLogin"
                LIMIT 1
                ''',
                (
                    UUID(str(instance_id)), preferred, preferred,
                    UUID(str(resource_id)), preferred,
                ),
            )
            row = cursor.fetchone()
            return dict(row) if row is not None else None


def get_active_agent_identity_for_resource(
    resource_id: UUID | str,
) -> dict[str, Any] | None:
    """Resuelve el agente activo cuyo propietario humano usa IDResource."""
    try:
        normalized = UUID(str(resource_id))
    except (TypeError, ValueError, AttributeError):
        return None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "ID", "IDResource", "IDAgentResource", "Name"
                FROM public."SysResourceIA"
                WHERE "IDResource" = %s AND active = true
                LIMIT 1
                ''',
                (normalized,),
            )
            row = cursor.fetchone()
    return dict(row) if row is not None else None


def get_active_agent_prompt(
    instance_id: UUID | str,
    resource_id: UUID | str,
) -> dict[str, Any] | None:
    """Obtiene la única plantilla publicada para el agente dentro de su instancia."""
    try:
        normalized_instance = UUID(str(instance_id))
        normalized_resource = UUID(str(resource_id))
    except (TypeError, ValueError, AttributeError):
        return None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "ID", "IDSolidSETInstance", "IDResource", "Version", "Name",
                       "SystemPrompt", "BehaviorConfig", "SourceHash", "PublishedAt"
                FROM public."SysAgentIAPrompt"
                WHERE "IDSolidSETInstance" = %s
                  AND "IDResource" = %s
                  AND "Status" = 'active'
                LIMIT 1
                ''',
                (normalized_instance, normalized_resource),
            )
            row = cursor.fetchone()
    return dict(row) if row is not None else None


def create_agent_prompt_draft(
    instance_id: UUID | str,
    resource_id: UUID | str,
    *,
    name: str,
    system_prompt: str,
    behavior_config: dict[str, Any],
    created_by: str,
) -> dict[str, Any]:
    """Crea de forma transaccional la siguiente versión draft del agente."""
    instance = UUID(str(instance_id))
    resource = UUID(str(resource_id))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT 1 FROM public."SysSolidSETInstanceResource"
                   WHERE "IDSolidSETInstance"=%s AND "IDResource"=%s AND active=true
                   FOR UPDATE''',
                (instance, resource),
            )
            if cursor.fetchone() is None:
                raise LookupError("El recurso no pertenece a la instancia SolidSET activa.")
            cursor.execute(
                '''
                SELECT "DisplayName", "FullName", "OrganizationID", "OrganizationNo",
                       "OrganizationName", "IDLogin",
                       jsonb_agg(DISTINCT jsonb_build_object(
                         'idWorkRoom', "IDWorkRoom", 'workRoomCode', "WorkRoomCode",
                         'workRoomName', "WorkRoomName", 'idCommunity', "IDCommunity",
                         'communityCode', "CommunityCode", 'communityName', "CommunityName",
                         'resourceAccessType', "ResourceAccessType"
                       )) AS "Scopes"
                FROM public."SysAgentIAScope"
                WHERE "IDSolidSETInstance"=%s AND "IDResource"=%s AND active=true
                GROUP BY "DisplayName", "FullName", "OrganizationID", "OrganizationNo",
                         "OrganizationName", "IDLogin"
                ORDER BY "IDLogin" LIMIT 1
                ''',
                (instance, resource),
            )
            snapshot = cursor.fetchone()
            if snapshot is None:
                raise LookupError("El recurso no tiene un alcance SolidSET sincronizado.")
            cursor.execute(
                '''SELECT COALESCE(MAX("Version"), 0) + 1 AS version
                   FROM public."SysAgentIAPrompt"
                   WHERE "IDSolidSETInstance"=%s AND "IDResource"=%s''',
                (instance, resource),
            )
            version = int(cursor.fetchone()["version"])
            source_snapshot = dict(snapshot)
            cursor.execute(
                '''
                INSERT INTO public."SysAgentIAPrompt" (
                  "IDSolidSETInstance", "IDResource", "Version", "Name",
                  "SystemPrompt", "BehaviorConfig", "SourceSnapshot", "SourceHash",
                  "Status", "CreatedBy"
                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,'draft',%s)
                RETURNING *
                ''',
                (
                    instance, resource, version, name, system_prompt,
                    json.dumps(behavior_config, ensure_ascii=False, default=str),
                    json.dumps(source_snapshot, ensure_ascii=False, default=str),
                    hashlib.sha256(json.dumps(source_snapshot, sort_keys=True, default=str).encode()).hexdigest(),
                    created_by,
                ),
            )
            return dict(cursor.fetchone())


def get_agent_scope_profile(
    instance_id: UUID | str, resource_id: UUID | str
) -> dict[str, Any] | None:
    """Devuelve un perfil resumido para generar una plantilla manual."""
    instance = UUID(str(instance_id))
    resource = UUID(str(resource_id))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT "DisplayName", "FullName", "OrganizationID", "OrganizationNo",
                          "OrganizationName", "IDLogin", count(*) AS "ScopeCount"
                   FROM public."SysAgentIAScope"
                   WHERE "IDSolidSETInstance"=%s AND "IDResource"=%s AND active=true
                   GROUP BY "DisplayName", "FullName", "OrganizationID", "OrganizationNo",
                            "OrganizationName", "IDLogin"
                   ORDER BY "IDLogin" LIMIT 1''',
                (instance, resource),
            )
            row = cursor.fetchone()
    return dict(row) if row is not None else None


def list_active_agent_resource_ids(instance_id: UUID | str) -> list[UUID]:
    """Lista recursos IA activos con pertenencia y alcance vigentes en la instancia."""
    instance = UUID(str(instance_id))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT DISTINCT r."IDResource"
                   FROM public."SysResourceIA" r
                   INNER JOIN public."SysSolidSETInstanceResource" ir
                     ON ir."IDResource"=r."IDResource"
                    AND ir."IDSolidSETInstance"=%s AND ir.active=true
                   INNER JOIN public."SysAgentIAScope" s
                     ON s."IDResource"=r."IDResource"
                    AND s."IDSolidSETInstance"=ir."IDSolidSETInstance" AND s.active=true
                   WHERE r.active=true AND r."IDAgentResource" IS NOT NULL
                   ORDER BY r."IDResource"''',
                (instance,),
            )
            return [row["IDResource"] for row in cursor.fetchall()]


def get_latest_agent_prompt(
    instance_id: UUID | str, resource_id: UUID | str
) -> dict[str, Any] | None:
    """Devuelve la versión más reciente, independientemente de su estado."""
    instance = UUID(str(instance_id))
    resource = UUID(str(resource_id))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT * FROM public."SysAgentIAPrompt"
                   WHERE "IDSolidSETInstance"=%s AND "IDResource"=%s
                   ORDER BY "Version" DESC LIMIT 1''',
                (instance, resource),
            )
            row = cursor.fetchone()
    return dict(row) if row is not None else None


def publish_agent_prompt(
    instance_id: UUID | str,
    resource_id: UUID | str,
    prompt_id: UUID | str,
) -> dict[str, Any]:
    """Publica un draft y retira la versión activa anterior atómicamente."""
    instance = UUID(str(instance_id))
    resource = UUID(str(resource_id))
    prompt = UUID(str(prompt_id))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT "ID" FROM public."SysAgentIAPrompt"
                   WHERE "ID"=%s AND "IDSolidSETInstance"=%s AND "IDResource"=%s
                     AND "Status"='draft' FOR UPDATE''',
                (prompt, instance, resource),
            )
            if cursor.fetchone() is None:
                raise LookupError("La plantilla draft no existe para este agente e instancia.")
            cursor.execute(
                '''UPDATE public."SysAgentIAPrompt"
                   SET "Status"='retired', "RetiredAt"=CURRENT_TIMESTAMP
                   WHERE "IDSolidSETInstance"=%s AND "IDResource"=%s
                     AND "Status"='active' ''',
                (instance, resource),
            )
            cursor.execute(
                '''UPDATE public."SysAgentIAPrompt"
                   SET "Status"='active', "PublishedAt"=CURRENT_TIMESTAMP,
                       "RetiredAt"=NULL
                   WHERE "ID"=%s RETURNING *''',
                (prompt,),
            )
            return dict(cursor.fetchone())


def get_authorized_learning_agent_ids(
    instance_id: UUID | str,
    workroom_id: UUID | str,
    visibility_level: int,
    private_participant_ids: Iterable[UUID | str] = (),
) -> list[str]:
    """Resuelve en PostgreSQL qué agentes pueden aprender un mensaje no público."""
    try:
        instance = UUID(str(instance_id))
        workroom = UUID(str(workroom_id))
        visibility = int(visibility_level)
        participants = [UUID(str(value)) for value in private_participant_ids]
    except (TypeError, ValueError, AttributeError):
        return []
    if visibility not in {1, 2, 3}:
        return []
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            if visibility == 3:
                if not participants:
                    return []
                cursor.execute(
                    '''
                    SELECT r."IDResource"
                    FROM public."SysResourceIA" r
                    INNER JOIN public."SysSolidSETInstanceResource" ir
                      ON ir."IDResource"=r."IDResource"
                     AND ir."IDSolidSETInstance"=%s AND ir.active=true
                    WHERE r.active=true AND r."IDResource"=ANY(%s)
                    ORDER BY r."IDResource"
                    ''',
                    (instance, participants),
                )
            else:
                minimum_access = 2 if visibility == 2 else 0
                cursor.execute(
                    '''
                    SELECT DISTINCT s."IDResource"
                    FROM public."SysAgentIAScope" s
                    INNER JOIN public."SysResourceIA" r
                      ON r."IDResource"=s."IDResource" AND r.active=true
                    WHERE s."IDSolidSETInstance"=%s
                      AND s."IDWorkRoom"=%s
                      AND s.active=true
                      AND s."ResourceAccessType">=%s
                    ORDER BY s."IDResource"
                    ''',
                    (instance, workroom, minimum_access),
                )
            return [str(row["IDResource"]) for row in cursor.fetchall()]


def resolve_solidset_identity(identifier: str) -> dict[str, Any] | None:
    """Resuelve identidades desde la réplica PostgreSQL, sin consultar SQL Server."""
    raw_identifier = str(identifier or "").strip()
    if not raw_identifier:
        return None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT l."IDLogin", l."Username", l."FullName",
                       l."LastIDResource" AS "IDResource",
                       COALESCE(NULLIF(BTRIM(r."Name"), ''),
                                NULLIF(BTRIM(l."FullName"), ''),
                                NULLIF(BTRIM(l."Username"), '')) AS "DisplayName"
                FROM public."SysLogin" l
                LEFT JOIN public."SysResourceIA" r
                  ON r."IDResource" = l."LastIDResource"
                WHERE LOWER(l."Username") = LOWER(%s)
                   OR l."IDLogin"::text = %s
                   OR l."LastIDResource"::text = %s
                ORDER BY CASE WHEN LOWER(l."Username") = LOWER(%s) THEN 0 ELSE 1 END,
                         l."Username", l."IDLogin"
                LIMIT 1
                ''',
                (raw_identifier, raw_identifier, raw_identifier, raw_identifier),
            )
            row = cursor.fetchone()
    return dict(row) if row is not None else None


def get_active_agents_for_workroom(
    workroom_id: UUID | str,
    selected_resource_ids: Iterable[UUID | str],
    instance_id: UUID | str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve únicamente agentes activos, seleccionados y asignados al canal."""
    selected = list(dict.fromkeys(UUID(str(value)) for value in selected_resource_ids))
    if not selected:
        return []
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            instance = UUID(str(instance_id)) if instance_id else None
            cursor.execute(
                '''
                SELECT r."ID", r."Name", r."IDResource",
                       CASE WHEN %s::uuid IS NULL THEN r."IDAgentResource"
                            ELSE ir."IDAgentResource" END AS "IDAgentResource",
                       r.active,
                       c."IDWorkRoom",
                       CASE WHEN %s::uuid IS NULL THEN c.response_order
                            ELSE ic.response_order END AS response_order,
                       login."FullName"
                FROM public."SysResourceIA" r
                INNER JOIN public."SysChatIAResource" c
                    ON c."IDResource" = r."IDResource"
                LEFT JOIN public."SysSolidSETInstanceResource" ir
                    ON ir."IDResource"=r."IDResource"
                   AND ir."IDSolidSETInstance"=%s::uuid
                   AND ir.active=true
                LEFT JOIN public."SysSolidSETInstanceChatIAResource" ic
                    ON ic."IDResource"=r."IDResource"
                   AND ic."IDWorkRoom"=c."IDWorkRoom"
                   AND ic."IDSolidSETInstance"=%s::uuid
                   AND ic.active=true
                LEFT JOIN LATERAL (
                    SELECT l."FullName"
                    FROM public."SysLogin" l
                    WHERE (
                        r."ActiveIDLogin2Resource" IS NOT NULL
                        AND l."ActiveIDLogin2Resource" = r."ActiveIDLogin2Resource"
                    ) OR (
                        r."ActiveIDLogin2Resource" IS NULL
                        AND l."LastIDResource" = r."IDResource"
                    )
                    ORDER BY
                        CASE WHEN NULLIF(BTRIM(l."FullName"), '') IS NULL THEN 1 ELSE 0 END,
                        l."IDLogin"
                    LIMIT 1
                ) login ON true
                WHERE c."IDWorkRoom" = %s
                  AND r.active = true
                  AND c.active = true
                  AND r."IDResource" = ANY(%s)
                  AND (%s::uuid IS NULL OR ir."IDResource" IS NOT NULL)
                  AND (%s::uuid IS NULL OR ic."IDResource" IS NOT NULL)
                ORDER BY response_order ASC, r."Name" ASC, r."IDResource" ASC
                ''',
                (
                    instance, instance, instance, instance,
                    UUID(str(workroom_id)), selected, instance, instance,
                ),
            )
            return [dict(row) for row in cursor.fetchall()]


def ensure_payload_agent_workroom_assignments(
    workroom_id: UUID | str,
    resource_ids: Iterable[UUID | str],
) -> int:
    """Vincula al canal recursos activos descubiertos en el payload de SolidSET."""
    resources = list(dict.fromkeys(UUID(str(value)) for value in resource_ids))
    if not resources:
        return 0
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysChatIAResource" (
                    "IDResource", "IDWorkRoom", active, response_order
                )
                SELECT r."IDResource", %s, true, 0
                FROM public."SysResourceIA" r
                WHERE r.active = true
                  AND r."IDResource" = ANY(%s)
                ON CONFLICT ("IDResource", "IDWorkRoom") DO NOTHING
                ''',
                (UUID(str(workroom_id)), resources),
            )
            return max(0, cursor.rowcount)


def save_agent_knowledge(knowledge: dict[str, Any]) -> dict[str, Any]:
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            source = str(knowledge.get("Source", "manual") or "manual")
            # Automatic chat learning is idempotent: retries of the same
            # notification must not create dozens of identical facts.
            if source.startswith("chat-question"):
                cursor.execute(
                    '''
                    SELECT * FROM public."SysResourceIAKnowledge"
                    WHERE "IDResource"=%s
                      AND "IDWorkRoom" IS NOT DISTINCT FROM %s
                      AND "KnowledgeText"=%s
                      AND "Source"=%s
                      AND active=true
                    ORDER BY "Stamp" DESC LIMIT 1
                    ''',
                    (
                        knowledge["IDResource"], knowledge.get("IDWorkRoom"),
                        knowledge["KnowledgeText"], source,
                    ),
                )
                existing = cursor.fetchone()
                if existing:
                    return {**dict(existing), "WasExisting": True}
            cursor.execute(
                '''
                INSERT INTO public."SysResourceIAKnowledge" (
                    "IDResource", "IDWorkRoom", "Title", "KnowledgeText", "Source", active
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                ''',
                (
                    knowledge["IDResource"], knowledge.get("IDWorkRoom"),
                    knowledge.get("Title"), knowledge["KnowledgeText"],
                    source, knowledge.get("active", True),
                ),
            )
            saved = cursor.fetchone()
    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió el conocimiento guardado.")
    return {**dict(saved), "WasExisting": False}


def get_agent_knowledge(resource_id: UUID | str, workroom_id: UUID | str) -> str:
    """Obtiene conocimiento privado del agente y el específico del canal actual."""
    from app.knowledge_provenance import usable_agent_knowledge

    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "Title", "KnowledgeText", "Source"
                FROM public."SysResourceIAKnowledge"
                WHERE "IDResource" = %s
                  AND active = true
                  AND ("IDWorkRoom" IS NULL OR "IDWorkRoom" = %s)
                ORDER BY "IDWorkRoom" NULLS FIRST, "Stamp" DESC
                LIMIT 30
                ''',
                (UUID(str(resource_id)), UUID(str(workroom_id))),
            )
            rows = cursor.fetchall()
    return "\n\n".join(
        f"[{row.get('Title') or row.get('Source') or 'Conocimiento'}]\n{row['KnowledgeText']}"
        for row in rows
        if usable_agent_knowledge(row.get("KnowledgeText"), row.get("Source"))
    )[:20000]


def quarantine_legacy_generated_knowledge() -> int:
    """Deactivates legacy AI drafts misclassified as user assertions.

    Rows remain in PostgreSQL for audit/recovery. Only the old ambiguous source
    is inspected; manual knowledge and versioned user assertions are untouched.
    """
    from app.knowledge_provenance import (
        LEGACY_SUGGESTION_SOURCE,
        looks_like_generated_suggestion,
    )

    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "ID", "KnowledgeText"
                FROM public."SysResourceIAKnowledge"
                WHERE "Source"=%s AND active=true
                ''',
                (LEGACY_SUGGESTION_SOURCE,),
            )
            unsafe_ids = [
                row["ID"] for row in cursor.fetchall()
                if looks_like_generated_suggestion(row.get("KnowledgeText"))
            ]
            if not unsafe_ids:
                return 0
            cursor.execute(
                '''
                UPDATE public."SysResourceIAKnowledge"
                SET active=false
                WHERE "ID" = ANY(%s)
                ''',
                (unsafe_ids,),
            )
            return max(0, cursor.rowcount)


def configure_agent_workroom(
    resource_id: UUID | str,
    workroom_id: UUID | str,
    *,
    active: bool,
    response_order: int,
) -> dict[str, Any]:
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysChatIAResource" (
                    "IDResource", "IDWorkRoom", active, response_order
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT ("IDResource", "IDWorkRoom") DO UPDATE SET
                    active = EXCLUDED.active,
                    response_order = EXCLUDED.response_order
                RETURNING *
                ''',
                (UUID(str(resource_id)), UUID(str(workroom_id)), active, response_order),
            )
            saved = cursor.fetchone()
    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió la asignación guardada.")
    return dict(saved)


def touch_agent_session(
    session_id: UUID | str,
    resource_id: UUID | str,
    workroom_id: UUID | str,
    *,
    status: str = "active",
) -> dict[str, Any]:
    """Crea la sesión lógica del agente o actualiza su última actividad."""
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO public."SysAgentIASession" (
                    "IDSession", "IDResource", "IDWorkRoom", "Status"
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT ("IDSession", "IDResource") DO UPDATE SET
                    "IDWorkRoom" = EXCLUDED."IDWorkRoom",
                    "LastActivityAt" = CURRENT_TIMESTAMP,
                    "Status" = EXCLUDED."Status"
                RETURNING *
                ''',
                (
                    UUID(str(session_id)), UUID(str(resource_id)),
                    UUID(str(workroom_id)), status,
                ),
            )
            saved = cursor.fetchone()
    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió la sesión guardada.")
    return dict(saved)

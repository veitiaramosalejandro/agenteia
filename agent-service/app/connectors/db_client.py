from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID
import threading
import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.llm.secrets import decrypt_api_key, encrypt_api_key
from app.connectors.solidset_sql import encrypt_sql_password


_solidset_location_schema_lock = threading.Lock()
_solidset_location_schema_ready = False


def _postgres_connection() -> psycopg.Connection:
    """Abre una conexión corta a la base PostgreSQL de la aplicación."""
    return psycopg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        dbname=settings.POSTGRES_DB,
        row_factory=dict_row,
    )


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
                    CREATE TABLE IF NOT EXISTS public."SysSolidSETInstanceResource" (
                      "IDSolidSETInstance" uuid NOT NULL
                        REFERENCES public."SysSolidSETInstance"("ID") ON DELETE CASCADE,
                      "IDResource" uuid NOT NULL,
                      active boolean NOT NULL DEFAULT true,
                      "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      PRIMARY KEY ("IDSolidSETInstance", "IDResource")
                    );
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
                ''')
        _solidset_location_schema_ready = True


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
            database = configuration.get("Database")
            if database:
                encrypted = encrypt_sql_password(database.get("Password"))
                if not encrypted:
                    cursor.execute(
                        'SELECT "EncryptedPassword" FROM public."SysSolidSETDatabase" '
                        'WHERE "IDSolidSETInstance"=%s', (row["ID"],),
                    )
                    previous = cursor.fetchone()
                    encrypted = previous["EncryptedPassword"] if previous else None
                if not encrypted:
                    raise ValueError("Password é obrigatória ao criar a ligação SQL Server.")
                cursor.execute('''
                  INSERT INTO public."SysSolidSETDatabase" (
                    "IDSolidSETInstance", "Host", "InstanceName", "Port", "DatabaseName",
                    "Username", "EncryptedPassword", "Encrypt", "TrustServerCertificate",
                    "ConnectionTimeout", "SchemaVersion", "AdapterCode", active
                  ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT ("IDSolidSETInstance") DO UPDATE SET
                    "Host"=EXCLUDED."Host", "InstanceName"=EXCLUDED."InstanceName",
                    "Port"=EXCLUDED."Port", "DatabaseName"=EXCLUDED."DatabaseName",
                    "Username"=EXCLUDED."Username", "EncryptedPassword"=EXCLUDED."EncryptedPassword",
                    "Encrypt"=EXCLUDED."Encrypt",
                    "TrustServerCertificate"=EXCLUDED."TrustServerCertificate",
                    "ConnectionTimeout"=EXCLUDED."ConnectionTimeout",
                    "SchemaVersion"=EXCLUDED."SchemaVersion", "AdapterCode"=EXCLUDED."AdapterCode",
                    active=EXCLUDED.active, "UpdatedAt"=CURRENT_TIMESTAMP
                ''', (
                    row["ID"], database["Host"], database.get("InstanceName"),
                    database.get("Port", 1433), database["DatabaseName"], database["Username"],
                    encrypted, database.get("Encrypt", True),
                    database.get("TrustServerCertificate", False),
                    database.get("ConnectionTimeout", 15), database.get("SchemaVersion"),
                    database.get("AdapterCode", "solidset-v1"), database.get("active", True),
                ))
    if row is None:
        raise RuntimeError("PostgreSQL no devolvió la instancia SolidSET guardada.")
    result = dict(row)
    result["_operation"] = operation
    return result


def get_solidset_instance(
    *, code: str | None = None, source_ip: str | None = None
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
                WHERE active = true
                  AND ((NULLIF(%s::text, '') IS NOT NULL
                        AND LOWER("Code") = LOWER(%s::text))
                    OR (NULLIF(%s::text, '') IS NOT NULL
                        AND "SourceIP" = %s::text))
                ORDER BY CASE WHEN NULLIF(%s::text, '') IS NOT NULL
                                   AND LOWER("Code") = LOWER(%s::text)
                              THEN 0 ELSE 1 END
                LIMIT 1
                ''',
                (code, code, source_ip, source_ip, code, code),
            )
            row = cursor.fetchone()
            result = dict(row) if row else None
            if result:
                cursor.execute('SELECT * FROM public."SysSolidSETDatabase" WHERE "IDSolidSETInstance"=%s', (result["ID"],))
                database = cursor.fetchone()
                result["Database"] = dict(database) if database else None
    return result


def list_active_solidset_instances() -> list[dict[str, Any]]:
    ensure_solidset_instance_location_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT * FROM public."SysSolidSETInstance" WHERE active=true ORDER BY "Code"'
            )
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                cursor.execute('SELECT * FROM public."SysSolidSETDatabase" WHERE "IDSolidSETInstance"=%s', (row["ID"],))
                database = cursor.fetchone()
                row["Database"] = dict(database) if database else None
            return rows


def update_solidset_database_connection_status(
    instance_id: str | UUID, status: str, error: str | None = None,
) -> None:
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''UPDATE public."SysSolidSETDatabase"
              SET "LastConnectionAt"=CURRENT_TIMESTAMP,
                  "LastConnectionStatus"=%s, "LastConnectionError"=%s,
                  "UpdatedAt"=CURRENT_TIMESTAMP
              WHERE "IDSolidSETInstance"=%s''',
              (status, error, UUID(str(instance_id))))


def ensure_llm_provider_schema() -> None:
    """Crea el esquema LLM también en volúmenes PostgreSQL ya existentes."""
    migration = '''
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
        "AzureDeployment" varchar(255), "IDResource" uuid,
        "IsDefault" boolean NOT NULL DEFAULT false,
        active boolean NOT NULL DEFAULT true,
        "CreatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
        "UpdatedAt" timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    '''
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(migration)


def ensure_agent_model_schema() -> None:
    """Crea la asignación agente-modelo en bases persistentes existentes."""
    ensure_llm_provider_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('''
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
            ''')


def save_agent_model_configuration(resource_id: UUID | str, data: dict[str, Any]) -> dict[str, Any]:
    ensure_agent_model_schema()
    resource = UUID(str(resource_id))
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
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
                    '"UpdatedAt"=CURRENT_TIMESTAMP WHERE "IDResource"=%s AND active=true',
                    (resource,),
                )
            cursor.execute(
                '''INSERT INTO public."SysAgentIAModel" (
                  "IDResource", "IDProviderConfiguration", "Role", "LocalExecution",
                  "TrainingMode", "LearnFromOwner", "LearnFromSystem", "LearnFromReactions",
                  "Capabilities", "Priority", "IsDefault", active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                ON CONFLICT ("IDResource", "IDProviderConfiguration") WHERE active=true DO UPDATE SET
                  "Role"=EXCLUDED."Role", "LocalExecution"=EXCLUDED."LocalExecution",
                  "TrainingMode"=EXCLUDED."TrainingMode", "LearnFromOwner"=EXCLUDED."LearnFromOwner",
                  "LearnFromSystem"=EXCLUDED."LearnFromSystem",
                  "LearnFromReactions"=EXCLUDED."LearnFromReactions",
                  "Capabilities"=EXCLUDED."Capabilities", "Priority"=EXCLUDED."Priority",
                  "IsDefault"=EXCLUDED."IsDefault", active=EXCLUDED.active,
                  "UpdatedAt"=CURRENT_TIMESTAMP RETURNING *''',
                (
                    resource, provider["ID"], data.get("Role", "general"), local_execution,
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


def get_agent_model_configurations(resource_id: UUID | str) -> list[dict[str, Any]]:
    ensure_agent_model_schema()
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''SELECT m.*, p."Code" AS "ProviderCode", p."Provider", p."Model", p."BaseUrl"
                   FROM public."SysAgentIAModel" m
                   JOIN public."SysLLMProviderConfiguration" p ON p."ID"=m."IDProviderConfiguration"
                   WHERE m."IDResource"=%s AND m.active=true AND p.active=true
                   ORDER BY m."IsDefault" DESC, m."Priority", p."Code"''',
                (UUID(str(resource_id)),),
            )
            rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_agent_model_configuration(resource_id: UUID | str) -> dict[str, Any] | None:
    rows = get_agent_model_configurations(resource_id)
    return rows[0] if rows else None


def agent_learning_enabled(resource_id: UUID | str, source: str) -> bool:
    """Consulta la política de aprendizaje; sin asignación conserva compatibilidad."""
    config = get_agent_model_configuration(resource_id)
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


def save_llm_provider_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """UPSERT por Code y garantiza una sola configuración activa por ámbito."""
    ensure_llm_provider_schema()
    resource_id = configuration.get("IDResource")
    active = bool(configuration.get("active", True))
    is_default = bool(configuration.get("IsDefault", False)) and resource_id is None
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            if active and resource_id is not None:
                cursor.execute(
                    'UPDATE public."SysLLMProviderConfiguration" SET active=false, '
                    '"UpdatedAt"=CURRENT_TIMESTAMP WHERE "IDResource"=%s AND active=true '
                    'AND LOWER("Code")<>LOWER(%s)',
                    (resource_id, configuration["Code"]),
                )
            if active and is_default:
                cursor.execute(
                    'UPDATE public."SysLLMProviderConfiguration" SET "IsDefault"=false, '
                    '"UpdatedAt"=CURRENT_TIMESTAMP WHERE "IsDefault"=true AND active=true '
                    'AND "IDResource" IS NULL AND LOWER("Code")<>LOWER(%s)',
                    (configuration["Code"],),
                )
            cursor.execute(
                '''
                INSERT INTO public."SysLLMProviderConfiguration" (
                  "Code", "Name", "Provider", "Model", "BaseUrl", "APIKey",
                  "Temperature", "MaxOutputTokens", "TimeoutSeconds", "AzureEndpoint",
                  "AzureApiVersion", "AzureDeployment", "IDResource", "IsDefault", active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                  "IDResource"=EXCLUDED."IDResource", "IsDefault"=EXCLUDED."IsDefault",
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
                    resource_id, is_default, active,
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
                   WHERE p.active=true AND (m."ID" IS NOT NULL
                     OR (p."IDResource"=%s::uuid)
                     OR (p."IDResource" IS NULL AND p."IsDefault"=true))
                   ORDER BY CASE WHEN m."ID" IS NOT NULL AND m."Capabilities" ? %s THEN 0
                                 WHEN m."ID" IS NOT NULL AND m."IsDefault" THEN 1
                                 WHEN p."IDResource" IS NOT NULL THEN 2 ELSE 3 END,
                            m."Priority" NULLS LAST LIMIT 1''',
                (normalized, normalized, requested_capability),
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
                       l."LastIDResource", l."ActiveIDLogin2Resource"
                FROM public."SysSolidSETInstanceLogin" l
                INNER JOIN public."SysResourceIA" r
                    ON (
                        r."ActiveIDLogin2Resource" IS NOT NULL
                        AND l."ActiveIDLogin2Resource" = r."ActiveIDLogin2Resource"
                    ) OR (
                        r."ActiveIDLogin2Resource" IS NULL
                        AND l."LastIDResource" = r."IDResource"
                    )
                WHERE r."IDResource" = %s
                  AND l."IDSolidSETInstance" = %s
                  AND EXISTS (SELECT 1 FROM public."SysSolidSETInstanceResource" ir
                    WHERE ir."IDSolidSETInstance"=l."IDSolidSETInstance"
                      AND ir."IDResource"=r."IDResource" AND ir.active=true)
                  AND r.active = true
                  AND NULLIF(l."Username", '') IS NOT NULL
                  AND NULLIF(l."Password", '') IS NOT NULL
                ORDER BY CASE WHEN l."IDLogin" = %s THEN 0 ELSE 1 END,
                         l."IDLogin"
                LIMIT 1
                ''',
                (UUID(str(resource_id)), UUID(str(instance_id)), preferred),
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
) -> list[dict[str, Any]]:
    """Devuelve únicamente agentes activos, seleccionados y asignados al canal."""
    selected = list(dict.fromkeys(UUID(str(value)) for value in selected_resource_ids))
    if not selected:
        return []
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                '''
                SELECT r."ID", r."Name", r."IDResource", r."IDAgentResource", r.active,
                       c."IDWorkRoom", c.response_order, login."FullName"
                FROM public."SysResourceIA" r
                INNER JOIN public."SysChatIAResource" c
                    ON c."IDResource" = r."IDResource"
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
                ORDER BY c.response_order ASC, r."Name" ASC, r."IDResource" ASC
                ''',
                (UUID(str(workroom_id)), selected),
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
                    knowledge.get("Source", "manual"), knowledge.get("active", True),
                ),
            )
            saved = cursor.fetchone()
    if saved is None:
        raise RuntimeError("PostgreSQL no devolvió el conocimiento guardado.")
    return dict(saved)


def get_agent_knowledge(resource_id: UUID | str, workroom_id: UUID | str) -> str:
    """Obtiene conocimiento privado del agente y el específico del canal actual."""
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
    )[:20000]


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

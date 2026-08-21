import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

class Settings(BaseSettings):
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Ollama Local Configuration
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://ollama-llm:11434")
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_CREDENTIAL_ENCRYPTION_KEY: str = os.getenv("LLM_CREDENTIAL_ENCRYPTION_KEY", "")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.5"))
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "qwen2.5:7b")
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "nomic-embed-text")
    EMBEDDING_VECTOR_SIZE: int = max(0, int(os.getenv("EMBEDDING_VECTOR_SIZE", "0")))
    LLM_MAX_OUTPUT_TOKENS: int = max(128, int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024")))
    LLM_REQUEST_TIMEOUT_SECONDS: int = max(10, int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "60")))
    
    # Vector Database (Qdrant)
    VECTOR_DB_URL: str = os.getenv("VECTOR_DB_URL", "http://vector-db:6333")
    VECTOR_COLLECTION_NAME: str = os.getenv("VECTOR_COLLECTION_NAME", "machining_docs")

    # Web search and persistent learning
    WEB_SEARCH_ENABLED: bool = _env_bool("WEB_SEARCH_ENABLED", "true")
    WEB_SEARCH_MAX_RESULTS: int = max(1, min(int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")), 10))
    WEB_SEARCH_TIMEOUT_SECONDS: int = max(3, int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "15")))
    WEB_SEARCH_REGION: str = os.getenv("WEB_SEARCH_REGION", "wt-wt")
    WEB_SEARCH_SAFESEARCH: str = os.getenv("WEB_SEARCH_SAFESEARCH", "moderate")
    WEB_SEARCH_AUTO_LEARN: bool = _env_bool("WEB_SEARCH_AUTO_LEARN", "true")
    WEB_MEMORY_MAX_AGE_HOURS: int = max(1, int(os.getenv("WEB_MEMORY_MAX_AGE_HOURS", "24")))
    WEB_MEMORY_MIN_SCORE: float = max(0.0, min(float(os.getenv("WEB_MEMORY_MIN_SCORE", "0.55")), 1.0))

    # Redis Cache / Memory
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis-cache:6379")
    AGENT_RESPONSE_STATUS_TTL_SECONDS: int = max(
        600, int(os.getenv("AGENT_RESPONSE_STATUS_TTL_SECONDS", "86400"))
    )
    AGENT_RESPONSE_QUEUE_ENABLED: bool = _env_bool("AGENT_RESPONSE_QUEUE_ENABLED", "true")
    AGENT_RESPONSE_STREAM: str = os.getenv(
        "AGENT_RESPONSE_STREAM", "machining:agent-responses:v1"
    )
    AGENT_RESPONSE_CONSUMER_GROUP: str = os.getenv(
        "AGENT_RESPONSE_CONSUMER_GROUP", "agent-response-workers-v1"
    )
    AGENT_RESPONSE_STREAM_MAXLEN: int = max(
        10000, int(os.getenv("AGENT_RESPONSE_STREAM_MAXLEN", "100000"))
    )
    AGENT_RESPONSE_MAX_RETRIES: int = max(
        0, min(10, int(os.getenv("AGENT_RESPONSE_MAX_RETRIES", "3")))
    )
    AGENT_RESPONSE_CLAIM_IDLE_MS: int = max(
        30000, int(os.getenv("AGENT_RESPONSE_CLAIM_IDLE_MS", "300000"))
    )
    AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS: int = max(
        10, int(os.getenv("AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS", "15"))
    )
    HISTORICAL_INGESTION_ENABLED: bool = _env_bool("HISTORICAL_INGESTION_ENABLED", "false")
    HISTORICAL_INGESTION_DRY_RUN: bool = _env_bool("HISTORICAL_INGESTION_DRY_RUN", "true")
    HISTORICAL_INGESTION_BATCH_SIZE: int = max(
        10, min(2000, int(os.getenv("HISTORICAL_INGESTION_BATCH_SIZE", "500")))
    )
    HISTORICAL_INGESTION_STREAM: str = os.getenv(
        "HISTORICAL_INGESTION_STREAM", "machining:historical-ingestion:v1"
    )
    HISTORICAL_INGESTION_GROUP: str = os.getenv(
        "HISTORICAL_INGESTION_GROUP", "historical-workers-v1"
    )
    HISTORICAL_INGESTION_STREAM_MAXLEN: int = max(
        1000, int(os.getenv("HISTORICAL_INGESTION_STREAM_MAXLEN", "10000"))
    )
    HISTORICAL_INGESTION_MAX_RETRIES: int = max(
        0, min(10, int(os.getenv("HISTORICAL_INGESTION_MAX_RETRIES", "3")))
    )
    HISTORICAL_INGESTION_CLAIM_IDLE_MS: int = max(
        30000, int(os.getenv("HISTORICAL_INGESTION_CLAIM_IDLE_MS", "60000"))
    )
    HISTORICAL_INGESTION_STALE_SECONDS: int = max(
        60, int(os.getenv("HISTORICAL_INGESTION_STALE_SECONDS", "300"))
    )
    HISTORICAL_INGESTION_POLL_SECONDS: int = max(
        10, int(os.getenv("HISTORICAL_INGESTION_POLL_SECONDS", "60"))
    )
    HISTORICAL_INGESTION_ADMIN_KEY: str = os.getenv("HISTORICAL_INGESTION_ADMIN_KEY", "")
    AGENT_TEMPORAL_STATE_TTL_SECONDS: int = max(
        300, int(os.getenv("AGENT_TEMPORAL_STATE_TTL_SECONDS", "3600"))
    )
    AGENT_USER_MEMORY_MAX_ITEMS: int = max(
        5, int(os.getenv("AGENT_USER_MEMORY_MAX_ITEMS", "30"))
    )
    
    # PostgreSQL / TimescaleDB
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "timescaledb")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "user")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "pass")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "machining_db")
    
    # Compatibilidad transitoria para módulos de diagnóstico antiguos. Las
    # conexiones operativas se resuelven exclusivamente desde PostgreSQL.
    SQL_SERVER_HOST: str = ""
    SQL_SERVER_INSTANCE: str = ""
    SQL_SERVER_PORT: int = 0
    SQL_SERVER_USER: str = ""
    SQL_SERVER_PASSWORD: str = ""
    SQL_SERVER_DB: str = ""
    DB_STUDY_INTERVAL_SECONDS: int = int(os.getenv("DB_STUDY_INTERVAL_SECONDS", "3600"))
    DB_STUDY_IDLE_CHECK_SECONDS: int = int(os.getenv("DB_STUDY_IDLE_CHECK_SECONDS", "10"))
    DB_STUDY_MAX_RUN_SECONDS: int = int(os.getenv("DB_STUDY_MAX_RUN_SECONDS", "900"))
    DB_INGEST_CONNECT_TIMEOUT_SECONDS: int = max(
        1, int(os.getenv("DB_INGEST_CONNECT_TIMEOUT_SECONDS", "15"))
    )
    DB_INGEST_QUERY_TIMEOUT_SECONDS: int = max(
        1, int(os.getenv("DB_INGEST_QUERY_TIMEOUT_SECONDS", "120"))
    )
    DB_INGEST_CONNECT_RETRIES: int = int(os.getenv("DB_INGEST_CONNECT_RETRIES", "2"))
    DIALOGUE_MAX_CONCURRENT: int = int(os.getenv("DIALOGUE_MAX_CONCURRENT", "1"))
    DIALOGUE_ADMISSION_TIMEOUT_SECONDS: int = int(os.getenv("DIALOGUE_ADMISSION_TIMEOUT_SECONDS", "2"))
    DIALOGUE_PROCESSING_TIMEOUT_SECONDS: int = int(os.getenv("DIALOGUE_PROCESSING_TIMEOUT_SECONDS", "0"))
    DIALOGUE_HARD_TIMEOUT_SECONDS: int = int(os.getenv("DIALOGUE_HARD_TIMEOUT_SECONDS", "0"))
    DIALOGUE_TIMEOUT_RELEASE_GRACE_SECONDS: int = int(os.getenv("DIALOGUE_TIMEOUT_RELEASE_GRACE_SECONDS", "45"))
    DIALOGUE_DUPLICATE_CACHE_ENABLED: bool = _env_bool("DIALOGUE_DUPLICATE_CACHE_ENABLED", "true")
    DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS: int = int(os.getenv("DIALOGUE_DUPLICATE_CACHE_TTL_SECONDS", "15"))
    DIALOGUE_DUPLICATE_CACHE_MAX_ITEMS: int = int(os.getenv("DIALOGUE_DUPLICATE_CACHE_MAX_ITEMS", "400"))
    DIALOGUE_REDIS_CACHE_PREFIX: str = os.getenv("DIALOGUE_REDIS_CACHE_PREFIX", "machining:dialogue:v1")
    EMBEDDING_CACHE_ENABLED: bool = _env_bool("EMBEDDING_CACHE_ENABLED", "true")
    EMBEDDING_CACHE_TTL_SECONDS: int = int(os.getenv("EMBEDDING_CACHE_TTL_SECONDS", "86400"))
    EMBEDDING_REDIS_CACHE_PREFIX: str = os.getenv("EMBEDDING_REDIS_CACHE_PREFIX", "machining:embedding:v1")
    DIALOGUE_SLOW_LOG_SECONDS: float = float(os.getenv("DIALOGUE_SLOW_LOG_SECONDS", "8"))

    # Notification API listener (SolidSET Communicator)
    NOTIF_API_ENABLED: bool = _env_bool("NOTIF_API_ENABLED", "true")
    NOTIF_API_BACKGROUND_ENABLED: bool = _env_bool("NOTIF_API_BACKGROUND_ENABLED", "false")
    NOTIF_API_BASE_URL: str = os.getenv("NOTIF_API_BASE_URL", "")
    NOTIF_API_ACCESS_KEY: str = os.getenv("NOTIF_API_ACCESS_KEY", "")
    NOTIF_API_POLL_SECONDS: int = int(os.getenv("NOTIF_API_POLL_SECONDS", "30"))
    NOTIF_API_START_DELAY_SECONDS: int = int(os.getenv("NOTIF_API_START_DELAY_SECONDS", "30"))
    NOTIF_API_TIMEOUT_SECONDS: int = int(os.getenv("NOTIF_API_TIMEOUT_SECONDS", "15"))
    NOTIF_API_VERIFY_TLS: bool = _env_bool("NOTIF_API_VERIFY_TLS", "false")
    NOTIF_AUDIT_LOG_ENABLED: bool = _env_bool("NOTIF_AUDIT_LOG_ENABLED", "true")
    NOTIF_MESSAGE_TRACE_ENABLED: bool = _env_bool("NOTIF_MESSAGE_TRACE_ENABLED", "true")
    NOTIF_MESSAGE_TRACE_MAX_LEN: int = int(os.getenv("NOTIF_MESSAGE_TRACE_MAX_LEN", "220"))
    NOTIF_PAUSE_DURING_DIALOGUE: bool = _env_bool("NOTIF_PAUSE_DURING_DIALOGUE", "true")

    # SolidSET controllers integration (ChatController + RestApiController)
    SOLIDSET_CHAT_BASE_URL: str = os.getenv("SOLIDSET_CHAT_BASE_URL", "")
    SOLIDSET_RESTAPI_BASE_URL: str = os.getenv("SOLIDSET_RESTAPI_BASE_URL", "")
    SOLIDSET_LOGIN_USERNAME: str = os.getenv("SOLIDSET_LOGIN_USERNAME", "")
    SOLIDSET_LOGIN_PASSWORD: str = os.getenv("SOLIDSET_LOGIN_PASSWORD", "")
    SOLIDSET_LOGIN_HASHPASS: str = os.getenv("SOLIDSET_LOGIN_HASHPASS", "")
    # Identidad del agente en el DTO FrameworkMessage: login y recurso son GUID distintos.
    SOLIDSET_LOGIN_RESOURCE_ID: str = os.getenv("SOLIDSET_LOGIN_RESOURCE_ID", "")
    SOLIDSET_RESOURCE_ID: str = os.getenv("SOLIDSET_RESOURCE_ID", "")
    SOLIDSET_TIMEZONE_ID: str = os.getenv("SOLIDSET_TIMEZONE_ID", "GMT Standard Time")
    SOLIDSET_WORKSTATION_ID: str = os.getenv("SOLIDSET_WORKSTATION_ID", "1")
    SOLIDSET_WORKSTATION_NAME: str = os.getenv("SOLIDSET_WORKSTATION_NAME", "Android.1")
    SOLIDSET_CLIENT_VERSION: str = os.getenv("SOLIDSET_CLIENT_VERSION", "Android.20.0.0")
    SOLIDSET_APPLICATION_ID: str = os.getenv("SOLIDSET_APPLICATION_ID", "")
    SOLIDSET_USER_ACTIONS_ENABLED: bool = _env_bool("SOLIDSET_USER_ACTIONS_ENABLED", "false")
    SOLIDSET_LISTEN_CHAT_MESSAGES: bool = _env_bool("SOLIDSET_LISTEN_CHAT_MESSAGES", "true")
    # 0 = todos los canales detectados; un valor positivo aplica un límite operativo.
    SOLIDSET_CHAT_MAX_CHANNELS: int = max(0, int(os.getenv("SOLIDSET_CHAT_MAX_CHANNELS", "0")))
    SOLIDSET_CHAT_PAGE_SIZE: int = int(os.getenv("SOLIDSET_CHAT_PAGE_SIZE", "20"))
    SOLIDSET_AUTO_REPLY_ENABLED: bool = _env_bool("SOLIDSET_AUTO_REPLY_ENABLED", "false")
    SOLIDSET_AUTO_REPLY_REQUIRE_MENTION: bool = _env_bool("SOLIDSET_AUTO_REPLY_REQUIRE_MENTION", "true")
    SOLIDSET_AUTO_REPLY_MENTION_TOKEN: str = os.getenv("SOLIDSET_AUTO_REPLY_MENTION_TOKEN", "@agente")
    SOLIDSET_AUTO_REPLY_ALLOW_SELF: bool = _env_bool("SOLIDSET_AUTO_REPLY_ALLOW_SELF", "false")
    SOLIDSET_AUTO_REPLY_MAX_PER_CYCLE: int = int(os.getenv("SOLIDSET_AUTO_REPLY_MAX_PER_CYCLE", "1"))
    SOLIDSET_AUTO_REPLY_MAX_INPUT_CHARS: int = int(os.getenv("SOLIDSET_AUTO_REPLY_MAX_INPUT_CHARS", "700"))
    SOLIDSET_AUTO_REPLY_FOLLOWUP_TTL_SECONDS: int = int(os.getenv("SOLIDSET_AUTO_REPLY_FOLLOWUP_TTL_SECONDS", "300"))
    CHANNEL_SUMMARY_MAX_MESSAGE_LIMIT: int = max(
        30, int(os.getenv("CHANNEL_SUMMARY_MAX_MESSAGE_LIMIT", "500"))
    )
    CHANNEL_SUMMARY_DEFAULT_MESSAGE_LIMIT: int = max(
        30,
        min(
            int(os.getenv("CHANNEL_SUMMARY_DEFAULT_MESSAGE_LIMIT", "30")),
            CHANNEL_SUMMARY_MAX_MESSAGE_LIMIT,
        ),
    )
    
    # Audio
    AUDIO_DIR: str = os.getenv("AUDIO_DIR", "./audio_samples")
    GENERATED_DOCS_DIR: str = os.getenv("GENERATED_DOCS_DIR", "./data/generated_docs")
    INGEST_INTERVAL_SECONDS: int = int(os.getenv("INGEST_INTERVAL_SECONDS", "3600"))

    def sql_server_connection_options(self) -> dict:
        """Devuelve el destino pymssql sin forzar puerto en instancias nombradas."""
        server = self.SQL_SERVER_HOST.strip().rstrip("\\")
        instance = self.SQL_SERVER_INSTANCE.strip().strip("\\")
        if instance:
            server = f"{server}\\{instance}"

        options = {"server": server}
        if "\\" not in server and self.SQL_SERVER_PORT > 0:
            options["port"] = self.SQL_SERVER_PORT
        return options

    def sql_server_endpoint_label(self) -> str:
        options = self.sql_server_connection_options()
        port = options.get("port")
        return f"{options['server']}:{port}" if port else str(options["server"])
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()

# Imprimir configuración para debug
if settings.ENVIRONMENT == "development":
    print("🔧 Configuración de desarrollo:")
    print(f"  - Ollama: {settings.OLLAMA_BASE_URL}")
    print(f"  - Qdrant: {settings.VECTOR_DB_URL}")
    print(f"  - Redis: {settings.REDIS_URL}")
    print(f"  - PostgreSQL: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}")
    print(f"  - SQL Server: {settings.sql_server_endpoint_label()}")
    print(f"  - SolidSET RestApi: {settings.SOLIDSET_RESTAPI_BASE_URL}")
    print(f"  - Notifications Api: {settings.NOTIF_API_BASE_URL}")
    

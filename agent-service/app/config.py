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
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://ollama-llm:11435")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "qwen2.5:7b")
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "nomic-embed-text")
    
    # Vector Database (Qdrant)
    VECTOR_DB_URL: str = os.getenv("VECTOR_DB_URL", "http://vector-db:6333")
    VECTOR_COLLECTION_NAME: str = os.getenv("VECTOR_COLLECTION_NAME", "machining_docs")

    # Redis Cache / Memory
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis-cache:6379")
    
    # PostgreSQL / TimescaleDB
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "timescaledb")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "user")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "pass")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "machining_db")
    
    # SQL Server (para compatibilidad)
    SQL_SERVER_HOST: str = os.getenv("SQL_SERVER_HOST", "172.16.10.167")
    SQL_SERVER_USER: str = os.getenv("SQL_SERVER_USER", "sa")
    SQL_SERVER_PASSWORD: str = os.getenv("SQL_SERVER_PASSWORD", "Abcd*1234")
    SQL_SERVER_DB: str = os.getenv("SQL_SERVER_DB", "ISIFrameIsicom")
    DB_STUDY_INTERVAL_SECONDS: int = int(os.getenv("DB_STUDY_INTERVAL_SECONDS", "3600"))
    DB_STUDY_IDLE_CHECK_SECONDS: int = int(os.getenv("DB_STUDY_IDLE_CHECK_SECONDS", "10"))
    DB_STUDY_MAX_RUN_SECONDS: int = int(os.getenv("DB_STUDY_MAX_RUN_SECONDS", "900"))
    DB_INGEST_CONNECT_TIMEOUT_SECONDS: int = int(os.getenv("DB_INGEST_CONNECT_TIMEOUT_SECONDS", "5"))
    DB_INGEST_CONNECT_RETRIES: int = int(os.getenv("DB_INGEST_CONNECT_RETRIES", "2"))
    DIALOGUE_MAX_CONCURRENT: int = int(os.getenv("DIALOGUE_MAX_CONCURRENT", "1"))
    DIALOGUE_ADMISSION_TIMEOUT_SECONDS: int = int(os.getenv("DIALOGUE_ADMISSION_TIMEOUT_SECONDS", "2"))
    DIALOGUE_PROCESSING_TIMEOUT_SECONDS: int = int(os.getenv("DIALOGUE_PROCESSING_TIMEOUT_SECONDS", "0"))

    # Notification API listener (SolidSET Communicator)
    NOTIF_API_ENABLED: bool = _env_bool("NOTIF_API_ENABLED", "true")
    NOTIF_API_BASE_URL: str = os.getenv("NOTIF_API_BASE_URL", "")
    NOTIF_API_ACCESS_KEY: str = os.getenv("NOTIF_API_ACCESS_KEY", "")
    NOTIF_API_POLL_SECONDS: int = int(os.getenv("NOTIF_API_POLL_SECONDS", "30"))
    NOTIF_API_TIMEOUT_SECONDS: int = int(os.getenv("NOTIF_API_TIMEOUT_SECONDS", "15"))
    NOTIF_API_VERIFY_TLS: bool = _env_bool("NOTIF_API_VERIFY_TLS", "false")

    # SolidSET controllers integration (ChatController + RestApiController)
    SOLIDSET_CHAT_BASE_URL: str = os.getenv("SOLIDSET_CHAT_BASE_URL", "")
    SOLIDSET_RESTAPI_BASE_URL: str = os.getenv("SOLIDSET_RESTAPI_BASE_URL", "")
    SOLIDSET_LOGIN_USERNAME: str = os.getenv("SOLIDSET_LOGIN_USERNAME", "")
    SOLIDSET_LOGIN_PASSWORD: str = os.getenv("SOLIDSET_LOGIN_PASSWORD", "")
    SOLIDSET_LOGIN_HASHPASS: str = os.getenv("SOLIDSET_LOGIN_HASHPASS", "")
    SOLIDSET_LOGIN_RESOURCE_ID: str = os.getenv("SOLIDSET_LOGIN_RESOURCE_ID", "")
    SOLIDSET_LISTEN_CHAT_MESSAGES: bool = _env_bool("SOLIDSET_LISTEN_CHAT_MESSAGES", "true")
    SOLIDSET_CHAT_MAX_CHANNELS: int = int(os.getenv("SOLIDSET_CHAT_MAX_CHANNELS", "15"))
    SOLIDSET_CHAT_PAGE_SIZE: int = int(os.getenv("SOLIDSET_CHAT_PAGE_SIZE", "20"))
    
    # Audio
    AUDIO_DIR: str = os.getenv("AUDIO_DIR", "./audio_samples")
    INGEST_INTERVAL_SECONDS: int = int(os.getenv("INGEST_INTERVAL_SECONDS", "3600"))
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

# Imprimir configuración para debug
if settings.ENVIRONMENT == "development":
    print("🔧 Configuración de desarrollo:")
    print(f"  - Ollama: {settings.OLLAMA_BASE_URL}")
    print(f"  - Qdrant: {settings.VECTOR_DB_URL}")
    print(f"  - Redis: {settings.REDIS_URL}")
    print(f"  - PostgreSQL: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}")